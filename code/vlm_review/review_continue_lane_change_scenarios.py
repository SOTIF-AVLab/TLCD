#!/usr/bin/env python
"""用前向视频和辅助CSV审查连续换道事件场景。"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from continue_lane_change_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from review_lane_change_scenarios import (
    compact_numeric_sequence,
    decoded_sequence,
    field_transitions,
    lane_index_validity_issues,
    read_csv_rows,
    sanitize_text,
    to_float,
)
from review_scenarios import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LANE_TYPES,
    ROAD_TYPES,
    ReviewError,
    atomic_write_json,
    call_model,
    discover_events,
    find_one,
    load_api_key,
    load_completed_review,
    nearest_csv_rows,
    probe_video,
    read_json,
    result_container,
    select_columns,
)


DEFAULT_DATASET_ROOT = Path(os.environ.get("TLCD_DATASET_ROOT", "."))
VALID_MANEUVER_SITUATIONS = {
    "向左完成换道",
    "向右完成换道",
    "向左换道后放弃",
    "向右换道后放弃",
    "无法确认",
}
VALID_SEQUENCE_SITUATIONS = {
    "先后两次向左完成换道",
    "先后两次向右完成换道",
    "先向左后向右完成换道",
    "先向右后向左完成换道",
    "两次换道中包含未完成过程",
    "仅确认一次换道",
    "未发生换道",
    "无法确认",
}
VALID_CONSISTENCY = {"一致", "部分一致", "明显不一致", "视觉不足"}
OBSTACLE_TERMS = (
    "前车",
    "后车",
    "TTC",
    "车距",
    "轿车",
    "SUV",
    "面包车",
    "货车",
    "卡车",
    "小客车",
    "目标车辆",
)
ABSOLUTE_TIME_PATTERN = re.compile(
    r"(?:在|于)?\s*\d+(?:\.\d+)?\s*(?:s|秒)"
    r"(?:时|后|处|左右|附近|开始|结束)?"
)


def maneuver_record(
    record: dict[str, Any], key: str
) -> dict[str, Any]:
    evidence = record.get("Evidence") or record.get("evidence") or {}
    value = evidence.get(key)
    if not isinstance(value, dict):
        raise ReviewError(f"record Evidence缺少{key}")
    return value


def required_time(maneuver: dict[str, Any], field: str, label: str) -> float:
    value = to_float(maneuver.get(field))
    if value is None or value < 0:
        raise ReviewError(f"{label}.{field}无效")
    return value


def build_continue_slice_plan(record: dict[str, Any]) -> dict[str, Any]:
    first = maneuver_record(record, "First_lane_change")
    second = maneuver_record(record, "Second_lane_change")
    first_start = required_time(first, "Start_time_s", "First_lane_change")
    first_end = required_time(first, "End_time_s", "First_lane_change")
    second_start = required_time(second, "Start_time_s", "Second_lane_change")
    second_end = required_time(second, "End_time_s", "Second_lane_change")
    if first_end < first_start or second_end < second_start:
        raise ReviewError("record中的换道结束时间早于开始时间")
    return {
        "source": "record Evidence原始时间，仅用于取帧，不修改record",
        "slices": [
            {"slice_index": 1, "role": "事件初始", "time_s": 0.01},
            {
                "slice_index": 2,
                "role": "第一次换道开始",
                "time_s": round(first_start, 6),
            },
            {
                "slice_index": 3,
                "role": "第一次换道结束",
                "time_s": round(first_end, 6),
            },
            {
                "slice_index": 4,
                "role": "第二次换道开始",
                "time_s": round(second_start, 6),
            },
            {
                "slice_index": 5,
                "role": "第二次换道结束",
                "time_s": round(second_end, 6),
            },
            {"slice_index": 6, "role": "视频末帧", "time_s": None},
        ],
    }


def extract_continue_frames(
    video_path: Path,
    frame_dir: Path,
    slice_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    video_info = probe_video(video_path)
    last_time = float(video_info["last_frame_time"])
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    for item in slice_plan["slices"]:
        index = int(item["slice_index"])
        is_last = index == 6
        sample_time = last_time if is_last else float(item["time_s"])
        if not is_last and sample_time > last_time + 0.05:
            raise ReviewError(
                f"切片时间{sample_time:.3f}s超出视频时长: {video_path}"
            )
        suffix = "last" if is_last else f"{sample_time:g}s"
        frame_path = frame_dir / f"slice_{index:02d}_{suffix}.jpg"
        if is_last:
            command = [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-sseof",
                "-1",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "reverse,scale=960:-2:force_original_aspect_ratio=decrease",
                "-q:v",
                "3",
                str(frame_path),
            ]
        else:
            command = [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-i",
                str(video_path),
                "-ss",
                f"{sample_time:.6f}",
                "-frames:v",
                "1",
                "-vf",
                "scale=960:-2:force_original_aspect_ratio=decrease",
                "-q:v",
                "3",
                str(frame_path),
            ]
        try:
            subprocess.run(command, check=True, capture_output=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            raise ReviewError(
                f"切帧失败 {video_path} @ {sample_time:.3f}s"
            ) from error
        if not frame_path.exists() or frame_path.stat().st_size == 0:
            raise ReviewError(f"未生成图像: {frame_path}")
        frames.append(
            {
                "index": index,
                "label": f"切片{index}-前向30度视角",
                "time": round(sample_time, 6),
                "is_last_frame": is_last,
                "path": frame_path,
            }
        )
    return frames


def infer_maneuver_from_overlap(
    rows: list[dict[str, str]], start: float, end: float
) -> dict[str, Any]:
    transitions: list[dict[str, Any]] = []
    last_state: str | None = None
    for row in rows:
        event_time = to_float(row.get("event_time"))
        if event_time is None or not start <= event_time <= end:
            continue
        left = (to_float(row.get("overlap_LeftLine")) or 0) > 0
        right = (to_float(row.get("overlap_RightLine")) or 0) > 0
        state = (
            "both"
            if left and right
            else "left"
            if left
            else "right"
            if right
            else "none"
        )
        if state == last_state:
            continue
        transitions.append(
            {"event_time": round(event_time, 3), "state": state}
        )
        last_state = state
    active = [
        item["state"]
        for item in transitions
        if item["state"] in {"left", "right"}
    ]
    start_boundary = active[0] if active else None
    end_boundary = active[-1] if active else None
    situation = {
        ("left", "right"): "向左完成换道",
        ("right", "left"): "向右完成换道",
        ("left", "left"): "向左换道后放弃",
        ("right", "right"): "向右换道后放弃",
    }.get((start_boundary, end_boundary))
    return {
        "window_start_s": round(start, 6),
        "window_end_s": round(end, 6),
        "overlap_transitions": transitions,
        "active_boundary_sequence": active,
        "start_boundary": start_boundary,
        "end_boundary": end_boundary,
        "inferred_situation": situation,
    }


def nearest_lane_snapshot(
    rows: list[dict[str, str]], event_time: float
) -> dict[str, int | float | None]:
    timed = [
        row
        for row in rows
        if to_float(row.get("event_time")) is not None
    ]
    if not timed:
        return {
            "event_time": round(event_time, 3),
            "main_lane_count": None,
            "ego_lane_index": None,
        }
    row = min(
        timed,
        key=lambda item: abs(float(item["event_time"]) - event_time),
    )
    lane_count = to_float(row.get("LaneNumSameDirection"))
    lane_index = to_float(row.get("EgoLaneIndex"))
    return {
        "event_time": round(float(row["event_time"]), 3),
        "main_lane_count": (
            int(lane_count)
            if lane_count is not None and lane_count.is_integer()
            else lane_count
        ),
        "ego_lane_index": (
            int(lane_index)
            if lane_index is not None and lane_index.is_integer()
            else lane_index
        ),
    }


def direction_of_situation(situation: str | None) -> str | None:
    if situation and "向左" in situation:
        return "left"
    if situation and "向右" in situation:
        return "right"
    return None


def completed_situation(situation: str | None) -> bool:
    return situation in {"向左完成换道", "向右完成换道"}


def map_supports_situation(
    situation: str | None,
    start_snapshot: dict[str, Any],
    end_snapshot: dict[str, Any],
) -> bool:
    start_lane = start_snapshot.get("ego_lane_index")
    end_lane = end_snapshot.get("ego_lane_index")
    if not isinstance(start_lane, (int, float)) or not isinstance(
        end_lane, (int, float)
    ):
        return False
    if situation == "向左完成换道":
        return end_lane < start_lane
    if situation == "向右完成换道":
        return end_lane > start_lane
    if situation in {"向左换道后放弃", "向右换道后放弃"}:
        return end_lane == start_lane
    return False


def summarize_sequence(
    first: str | None, second: str | None
) -> str:
    if first is None and second is None:
        return "未发生换道"
    known = [value for value in (first, second) if value in VALID_MANEUVER_SITUATIONS]
    if len(known) < 2:
        return "仅确认一次换道" if known else "无法确认"
    if "无法确认" in known:
        return "无法确认"
    if not all(completed_situation(value) for value in known):
        return "两次换道中包含未完成过程"
    pair = (direction_of_situation(first), direction_of_situation(second))
    return {
        ("left", "left"): "先后两次向左完成换道",
        ("right", "right"): "先后两次向右完成换道",
        ("left", "right"): "先向左后向右完成换道",
        ("right", "left"): "先向右后向左完成换道",
    }.get(pair, "无法确认")


def build_maneuver_evidence(
    rows: list[dict[str, str]],
    map_rows: list[dict[str, str]],
    record_maneuver: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    start = required_time(record_maneuver, "Start_time_s", label)
    end = required_time(record_maneuver, "End_time_s", label)
    inferred = infer_maneuver_from_overlap(rows, start, end)
    start_snapshot = nearest_lane_snapshot(map_rows, start)
    end_snapshot = nearest_lane_snapshot(map_rows, end)
    situation = inferred["inferred_situation"]
    record_direction = str(record_maneuver.get("Direction", "")).lower()
    record_direction_consistent = (
        direction_of_situation(situation) == record_direction
        if situation
        else None
    )
    map_supports = map_supports_situation(
        situation, start_snapshot, end_snapshot
    )
    if situation and map_supports:
        confidence = "高"
    elif situation and record_direction_consistent:
        confidence = "中"
    else:
        confidence = "低"
    return {
        **inferred,
        "record_direction": record_maneuver.get("Direction"),
        "record_direction_consistent": record_direction_consistent,
        "start_lane_snapshot": start_snapshot,
        "end_lane_snapshot": end_snapshot,
        "map_supports_overlap_inference": map_supports,
        "inference_confidence": confidence,
    }


def build_continue_auxiliary_summary(
    event_dir: Path,
    record: dict[str, Any],
    sample_times: list[float],
    slice_plan: dict[str, Any],
) -> dict[str, Any]:
    evidence_path = find_one(event_dir, "*_EvidenceChain.csv")
    ego_path = find_one(event_dir, "*_EgoInfo.csv")
    map_path = find_one(event_dir, "*_MapInfo.csv")
    all_evidence_rows = read_csv_rows(evidence_path)
    all_map_rows = read_csv_rows(map_path)
    evidence_samples_raw = nearest_csv_rows(evidence_path, sample_times)
    ego_samples_raw = nearest_csv_rows(ego_path, sample_times)
    map_samples_raw = nearest_csv_rows(map_path, sample_times)
    record_evidence = record.get("Evidence") or record.get("evidence") or {}
    result = record.get("Result") or record.get("result") or {}
    first_record = maneuver_record(record, "First_lane_change")
    second_record = maneuver_record(record, "Second_lane_change")
    first = build_maneuver_evidence(
        all_evidence_rows,
        all_map_rows,
        first_record,
        "First_lane_change",
    )
    second = build_maneuver_evidence(
        all_evidence_rows,
        all_map_rows,
        second_record,
        "Second_lane_change",
    )
    expected_sequence = summarize_sequence(
        first["inferred_situation"], second["inferred_situation"]
    )
    speed_values = [
        value
        for row in all_evidence_rows
        if (value := to_float(row.get("Ego_velocity"))) is not None
    ]
    evidence_columns = [
        "event_time",
        "Ego_velocity",
        "overlap_LeftLine",
        "overlap_RightLine",
        "last_cross_time",
        "last_cross_dir",
        "current_cross_dir",
        "Thres_LaneChangeCross_gap",
    ]
    map_columns = [
        "event_time",
        "Road_type",
        "Road_Curve",
        "Road_Slope",
        "Lane_type_CurrentLane",
        "LaneNumSameDirection",
        "EgoLaneIndex",
    ]
    behavior_flag_conflict = (
        record_evidence.get("Continuous_lane_change_behavior_confirmed")
        is True
        and expected_sequence
        in {
            "两次换道中包含未完成过程",
            "仅确认一次换道",
            "未发生换道",
        }
    )
    return {
        "warning": (
            "未读取ObjInfo，也未纳入trigger/com或Article_status字段。"
            "两次换道按各自时间窗口内的压线首尾推断，并由前向图像复核。"
        ),
        "slice_plan": copy.deepcopy(slice_plan),
        "record": {
            "Location": record.get("Location"),
            "Date": record.get("Date"),
            "Time": record.get("Time"),
            "Article_ID": (record.get("Article") or {}).get("ID"),
            "EventAnchor": record.get("EventAnchor"),
            "First_lane_change": first_record,
            "Second_lane_change": second_record,
            "Same_direction_lane_changes": record_evidence.get(
                "Same_direction_lane_changes"
            ),
            "Continuous_lane_change_behavior_confirmed": record_evidence.get(
                "Continuous_lane_change_behavior_confirmed"
            ),
            "Cross_line_time_gap_s": record_evidence.get(
                "Cross_line_time_gap_s"
            ),
            "Maximum_allowed_cross_line_gap_s": record_evidence.get(
                "Maximum_allowed_cross_line_gap_s"
            ),
            "original_Scenario_description": result.get(
                "Scenario_description"
            ),
            "original_Driving_suggestion": result.get("Driving_suggestion"),
        },
        "maneuver_evidence": {
            "first": first,
            "second": second,
            "expected_sequence_situation": expected_sequence,
            "record_behavior_flag_conflict": behavior_flag_conflict,
            "instruction": (
                "分别核验两次换道；Direction不证明完成，任一次压线首尾同侧时重点核查放弃换道"
            ),
        },
        "scene_aggregate": {
            "full_event_speed_range_kph": (
                [
                    round(min(speed_values) * 3.6, 1),
                    round(max(speed_values) * 3.6, 1),
                ]
                if speed_values
                else None
            ),
            "Road_type_sequence": decoded_sequence(
                all_map_rows, "Road_type", ROAD_TYPES
            ),
            "Lane_type_sequence": decoded_sequence(
                all_map_rows, "Lane_type_CurrentLane", LANE_TYPES
            ),
            "main_lane_count_sequence": compact_numeric_sequence(
                all_map_rows,
                "LaneNumSameDirection",
                positive_only=True,
            ),
            "EgoLaneIndex_transitions": field_transitions(
                all_map_rows, "EgoLaneIndex", positive_only=True
            ),
            "lane_index_validity_issues": lane_index_validity_issues(
                all_map_rows
            ),
        },
        "EvidenceChain_samples": [
            select_columns(row, evidence_columns)
            for row in evidence_samples_raw
        ],
        "EgoInfo_samples": [
            select_columns(row, ["event_time", "Ego_velocity"])
            for row in ego_samples_raw
        ],
        "MapInfo_samples": [
            select_columns(row, map_columns) for row in map_samples_raw
        ],
    }


def remove_obstacle_clauses(text: str) -> str:
    sentences = re.findall(r"[^。！？]+[。！？]?", text)
    cleaned: list[str] = []
    for sentence in sentences:
        punctuation = sentence[-1] if sentence[-1:] in "。！？" else ""
        body = sentence[:-1] if punctuation else sentence
        clauses = []
        for clause in re.split(r"[，；]", body):
            clause = clause.strip()
            if not clause:
                continue
            contains_obstacle = any(term in clause for term in OBSTACLE_TERMS)
            if contains_obstacle and not any(
                term in clause for term in ("拥堵", "车流")
            ):
                continue
            clauses.append(clause)
        if clauses:
            cleaned.append("，".join(clauses) + punctuation)
    return "".join(cleaned)


def sanitize_continue_text(
    text: str, remove_obstacles: bool = False
) -> str:
    text = sanitize_text(text, remove_unrelated=True)
    text = ABSOLUTE_TIME_PATTERN.sub("", text)
    if remove_obstacles:
        text = remove_obstacle_clauses(text)
    text = re.sub(r"\s+([，。；：])", r"\1", text)
    text = re.sub(r"[，；]{2,}", "，", text)
    text = re.sub(r"。{2,}", "。", text)
    return text.strip(" ，,；;：:")


def expected_sequence_clause(sequence: str) -> str:
    return {
        "先后两次向左完成换道": "自车先后完成两次向左换道",
        "先后两次向右完成换道": "自车先后完成两次向右换道",
        "先向左后向右完成换道": "自车先向左完成换道，随后向右完成第二次换道",
        "先向右后向左完成换道": "自车先向右完成换道，随后向左完成第二次换道",
        "两次换道中包含未完成过程": "两次横向操作中至少有一次未完成换道并回到原车道",
        "仅确认一次换道": "事件中仅能确认一次完整换道",
        "未发生换道": "事件中未确认自车发生换道",
        "无法确认": "受画面限制，两次换道过程无法完整确认",
    }[sequence]


def integrate_sequence_description(
    description: str, sequence: str
) -> str:
    description = re.sub(
        r"^连续换道(?:情况|状态)：[^。]+。", "", description.strip()
    ).strip()
    markers = {
        "先后两次向左完成换道": ("两次向左", "再次向左"),
        "先后两次向右完成换道": ("两次向右", "再次向右"),
        "先向左后向右完成换道": ("先向左", "随后向右"),
        "先向右后向左完成换道": ("先向右", "随后向左"),
        "两次换道中包含未完成过程": ("未完成换道", "回到原车道"),
        "仅确认一次换道": ("仅确认一次", "一次完整换道"),
        "未发生换道": ("未发生换道", "保持原车道"),
        "无法确认": ("无法确认", "难以确认"),
    }[sequence]
    if any(marker in description for marker in markers):
        return description
    return (
        description.rstrip("。")
        + "。"
        + expected_sequence_clause(sequence)
        + "。"
    )


def add_doubtful_point(
    doubtful_points: list[dict[str, str]], point: dict[str, str]
) -> None:
    if point not in doubtful_points:
        doubtful_points.append(point)


def normalize_continue_model_result(
    value: dict[str, Any], auxiliary: dict[str, Any]
) -> dict[str, Any]:
    description = str(value.get("Scenario_description_VLM", "")).strip()
    suggestion = str(value.get("Driving_suggestion_VLM", "")).strip()
    if not description or not suggestion:
        raise ReviewError("模型未返回完整的场景描述或驾驶建议")
    maneuver_review = value.get("maneuver_review")
    if not isinstance(maneuver_review, dict):
        raise ReviewError("模型未返回maneuver_review")
    first_review = maneuver_review.get("first")
    second_review = maneuver_review.get("second")
    if not isinstance(first_review, dict) or not isinstance(
        second_review, dict
    ):
        raise ReviewError("模型未返回两次换道的独立核验结果")
    first_situation = str(first_review.get("situation", "")).strip()
    second_situation = str(second_review.get("situation", "")).strip()
    if first_situation not in VALID_MANEUVER_SITUATIONS:
        raise ReviewError(f"模型返回无效第一次换道情况: {first_situation!r}")
    if second_situation not in VALID_MANEUVER_SITUATIONS:
        raise ReviewError(f"模型返回无效第二次换道情况: {second_situation!r}")

    sequence = summarize_sequence(first_situation, second_situation)
    if sequence not in VALID_SEQUENCE_SITUATIONS:
        raise ReviewError(f"无法汇总连续换道情况: {sequence!r}")
    value["sequence_situation"] = sequence
    first_review["completed"] = completed_situation(first_situation)
    second_review["completed"] = completed_situation(second_situation)
    first_review["returned_to_original_lane"] = (
        "放弃" in first_situation
    )
    second_review["returned_to_original_lane"] = (
        "放弃" in second_situation
    )
    maneuver_review["both_completed"] = (
        first_review["completed"] and second_review["completed"]
    )
    maneuver_review["same_direction"] = (
        direction_of_situation(first_situation)
        == direction_of_situation(second_situation)
        and direction_of_situation(first_situation) is not None
    )

    description = sanitize_continue_text(
        description, remove_obstacles=True
    )
    suggestion = sanitize_continue_text(
        suggestion, remove_obstacles=True
    )
    observations = value.get("visual_observations")
    weather_light = (
        str(observations.get("weather_light", "")).strip()
        if isinstance(observations, dict)
        else ""
    )
    if weather_light and not any(
        marker in description
        for marker in (
            "白天",
            "夜间",
            "夜晚",
            "清晨",
            "黄昏",
            "天气",
            "晴",
            "雨",
            "雪",
            "雾",
            "能见度",
        )
    ):
        description = weather_light.rstrip("。") + "。" + description
    value["Scenario_description_VLM"] = integrate_sequence_description(
        description, sequence
    )
    value["Driving_suggestion_VLM"] = suggestion

    consistency = value.get("auxiliary_consistency")
    if not isinstance(consistency, dict):
        consistency = {}
        value["auxiliary_consistency"] = consistency
    status = str(consistency.get("status", "")).strip()
    if status not in VALID_CONSISTENCY:
        status = "视觉不足"
        consistency["status"] = status
    doubtful_points = consistency.get("doubtful_points")
    if not isinstance(doubtful_points, list):
        doubtful_points = []

    evidence = auxiliary["maneuver_evidence"]
    model_situations = {
        "first": first_situation,
        "second": second_situation,
    }
    for key, label in (("first", "第一次"), ("second", "第二次")):
        expected = evidence[key].get("inferred_situation")
        confidence = evidence[key].get("inference_confidence")
        if (
            expected in VALID_MANEUVER_SITUATIONS
            and model_situations[key] != expected
            and confidence in {"高", "中"}
        ):
            consistency["status"] = "明显不一致"
            add_doubtful_point(
                doubtful_points,
                {
                    "auxiliary_claim": f"{label}压线首尾支持“{expected}”",
                    "visual_finding": (
                        f"模型视觉结论为“{model_situations[key]}”"
                    ),
                    "reason": "换道方向或完成状态与结构化压线证据不一致",
                },
            )
            maneuver_review["auxiliary_inference_consistent"] = False

    expected_sequence = evidence.get("expected_sequence_situation")
    if (
        expected_sequence in VALID_SEQUENCE_SITUATIONS
        and sequence != expected_sequence
        and all(
            evidence[key].get("inference_confidence") in {"高", "中"}
            for key in ("first", "second")
        )
    ):
        consistency["status"] = "明显不一致"
        add_doubtful_point(
            doubtful_points,
            {
                "auxiliary_claim": f"两段压线组合支持“{expected_sequence}”",
                "visual_finding": f"模型整体结论为“{sequence}”",
                "reason": "整体换道时序与两段结构化证据不一致",
            },
        )

    if evidence.get("record_behavior_flag_conflict"):
        consistency["status"] = "明显不一致"
        add_doubtful_point(
            doubtful_points,
            {
                "auxiliary_claim": (
                    "record声称已确认连续换道行为"
                ),
                "visual_finding": (
                    f"压线首尾组合推断为“{expected_sequence}”"
                ),
                "reason": "至少一段压线首尾同侧，可能为放弃换道",
            },
        )

    lane_issues = auxiliary["scene_aggregate"].get(
        "lane_index_validity_issues", []
    )
    for issue in lane_issues:
        consistency["status"] = "明显不一致"
        add_doubtful_point(
            doubtful_points,
            {
                "auxiliary_claim": (
                    f"同方向主车道数为{issue['main_lane_count']}，"
                    f"但EgoLaneIndex出现{issue['ego_lane_index']}"
                ),
                "visual_finding": "视觉车道编号需人工复核",
                "reason": "车道编号超过同方向主车道总数，辅助字段内部矛盾",
            },
        )

    if consistency["status"] == "明显不一致":
        consistency["doubtful_points"] = doubtful_points
        value["requires_manual_review"] = bool(doubtful_points)
    else:
        unconfirmed = consistency.get("unconfirmed_points")
        if not isinstance(unconfirmed, list):
            unconfirmed = []
        unconfirmed.extend(doubtful_points)
        consistency["unconfirmed_points"] = unconfirmed
        consistency["doubtful_points"] = []
        value["requires_manual_review"] = sequence == "无法确认"
    if sequence == "无法确认":
        value["requires_manual_review"] = True
    if value["requires_manual_review"]:
        if lane_issues:
            value["manual_review_reason"] = (
                "EgoLaneIndex超过同方向主车道总数，需核验地图车道编号"
            )
        elif evidence.get("record_behavior_flag_conflict"):
            value["manual_review_reason"] = (
                "record连续换道标记与压线首尾推断不一致"
            )
        elif not str(value.get("manual_review_reason", "")).strip():
            value["manual_review_reason"] = (
                "两次换道的方向或完成状态需要人工复核"
            )
    else:
        value["manual_review_reason"] = ""

    if sequence in {
        "先后两次向左完成换道",
        "先后两次向右完成换道",
    } and not any(
        marker in value["Driving_suggestion_VLM"]
        for marker in ("每次只", "稳定", "重新观察", "再进行第二次")
    ):
        value["Driving_suggestion_VLM"] = (
            value["Driving_suggestion_VLM"].rstrip("。")
            + "。建议每次只变更一条车道，第一次换道完成并稳定行驶后，"
            "重新观察道路条件再决定是否进行第二次换道。"
        )
    elif sequence in {
        "先向左后向右完成换道",
        "先向右后向左完成换道",
    } and "稳定车道" not in value["Driving_suggestion_VLM"]:
        value["Driving_suggestion_VLM"] = (
            value["Driving_suggestion_VLM"].rstrip("。")
            + "。应提前规划行驶路线并保持稳定车道，减少不必要的反复变道。"
        )
    elif sequence == "两次换道中包含未完成过程" and not any(
        marker in value["Driving_suggestion_VLM"]
        for marker in ("保持原车道", "待条件", "重新")
    ):
        value["Driving_suggestion_VLM"] = (
            value["Driving_suggestion_VLM"].rstrip("。")
            + "。对于未完成的换道应先保持原车道，待条件适合后再重新操作。"
        )
    return value


def collect_continue_doubtful_reviews(
    output_root: Path,
) -> list[dict[str, Any]]:
    doubtful: list[dict[str, Any]] = []
    reviews_root = output_root / "reviews"
    if not reviews_root.exists():
        return doubtful
    for review_path in sorted(reviews_root.rglob("event_*.json")):
        try:
            review = read_json(review_path)
        except (OSError, json.JSONDecodeError):
            continue
        model_result = review.get("model_result")
        if not isinstance(model_result, dict) or not model_result.get(
            "requires_manual_review"
        ):
            continue
        doubtful.append(
            {
                "segment": review.get("segment"),
                "event": review.get("event"),
                "record_path": review.get("record_path"),
                "sequence_situation": model_result.get(
                    "sequence_situation"
                ),
                "manual_review_reason": model_result.get(
                    "manual_review_reason", ""
                ),
                "auxiliary_consistency": model_result.get(
                    "auxiliary_consistency", {}
                ),
                "review_path": str(review_path),
            }
        )
    return doubtful


def review_continue_event(
    segment: Path,
    event_dir: Path,
    output_root: Path,
    api_key: str | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
    relative_dir = Path(segment.name) / event_dir.name
    if args.resume:
        completed_review = load_completed_review(
            output_root, segment.name, event_dir.name, args.model
        )
        if completed_review is not None:
            return {
                "segment": segment.name,
                "event": event_dir.name,
                "status": "skipped_completed",
                "record_path": completed_review.get("record_path", ""),
            }
    record_path = find_one(event_dir, "*_record.json")
    video_path = find_one(
        event_dir / "video" / "mp4", "video_30_event_*.mp4"
    )
    record = read_json(record_path)
    result = result_container(record)
    if (
        not args.overwrite
        and result.get("Scenario_description_VLM")
        and result.get("Driving_suggestion_VLM")
    ):
        return {
            "segment": segment.name,
            "event": event_dir.name,
            "status": "skipped_existing",
            "record_path": str(record_path),
        }

    slice_plan = build_continue_slice_plan(record)
    frames = extract_continue_frames(
        video_path,
        output_root / "frames" / relative_dir,
        slice_plan,
    )
    slice_plan["slices"][-1]["actual_time_s"] = frames[-1]["time"]
    sample_times = [frame["time"] for frame in frames]
    auxiliary = build_continue_auxiliary_summary(
        event_dir, record, sample_times, slice_plan
    )
    if args.dry_run:
        return {
            "segment": segment.name,
            "event": event_dir.name,
            "status": "dry_run",
            "record_path": str(record_path),
            "slice_plan": slice_plan,
            "maneuver_evidence": auxiliary["maneuver_evidence"],
            "scene_aggregate": auxiliary["scene_aggregate"],
        }
    if api_key is None:
        raise ReviewError("内部错误：未加载API key")

    model_result, raw_content = call_model(
        api_key=api_key,
        base_url=args.base_url,
        model=args.model,
        frames=frames,
        auxiliary=auxiliary,
        timeout=args.request_timeout,
        max_retries=args.max_retries,
        system_prompt=SYSTEM_PROMPT,
        user_prompt_template=USER_PROMPT_TEMPLATE,
        max_tokens=3000,
    )
    model_result = normalize_continue_model_result(
        model_result, auxiliary
    )
    review_path = output_root / "reviews" / relative_dir.with_suffix(".json")
    atomic_write_json(
        review_path,
        {
            "segment": segment.name,
            "event": event_dir.name,
            "record_path": str(record_path),
            "video_path": str(video_path),
            "model": args.model,
            "slice_plan": slice_plan,
            "auxiliary": auxiliary,
            "model_result": model_result,
            "raw_model_content": raw_content,
        },
    )
    backup_path = output_root / "backups" / relative_dir / record_path.name
    if not backup_path.exists():
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(record_path, backup_path)
    result["Scenario_description_VLM"] = model_result[
        "Scenario_description_VLM"
    ]
    result["Driving_suggestion_VLM"] = model_result[
        "Driving_suggestion_VLM"
    ]
    atomic_write_json(record_path, record)
    return {
        "segment": segment.name,
        "event": event_dir.name,
        "status": "updated",
        "record_path": str(record_path),
        "sequence_situation": model_result["sequence_situation"],
        "requires_manual_review": model_result["requires_manual_review"],
        "manual_review_reason": model_result.get(
            "manual_review_reason", ""
        ),
        "auxiliary_consistency": model_result.get(
            "auxiliary_consistency", {}
        ),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT)
    parser.add_argument(
        "--segment-limit",
        type=int,
        default=2,
        help="按名称排序处理前N个segment；0表示全部，默认2",
    )
    parser.add_argument(
        "--event-limit", type=int, default=0, help="最多处理N个事件；0表示不限"
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="SEGMENT/EVENT",
        help="只处理指定相对事件路径，可重复提供",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent
        / "outputs_continue_lane_change",
    )
    parser.add_argument("--api-key-source", type=Path)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--request-timeout", type=int, default=240)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--workers", type=int, default=8, help="并行处理事件数；默认8"
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="跳过输出目录中已有同模型完整review的事件",
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--dry-run", action="store_true", help="只检查数据和切帧，不调用API、不改JSON"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.dataset_root.is_dir():
        print(f"错误：数据目录不存在: {args.dataset_root}", file=sys.stderr)
        return 2
    if args.workers < 1:
        print("错误：--workers必须大于等于1", file=sys.stderr)
        return 2
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("错误：PATH中找不到ffmpeg/ffprobe", file=sys.stderr)
        return 2
    try:
        events = discover_events(args.dataset_root, args.segment_limit)
        if args.only:
            requested = {
                value.replace("\\", "/").strip("/") for value in args.only
            }
            events = [
                (segment, event_dir)
                for segment, event_dir in events
                if f"{segment.name}/{event_dir.name}" in requested
            ]
            found = {
                f"{segment.name}/{event_dir.name}"
                for segment, event_dir in events
            }
            missing = sorted(requested - found)
            if missing:
                raise ReviewError("未找到指定事件: " + ", ".join(missing))
        if args.event_limit > 0:
            events = events[: args.event_limit]
        if not events:
            raise ReviewError("未发现event_*目录")
        api_key = None if args.dry_run else load_api_key(args.api_key_source)
    except (OSError, ReviewError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2

    print(
        f"发现 {len(events)} 个连续换道事件；模型={args.model}；"
        f"模式={'dry-run' if args.dry_run else 'API审查并写入'}；"
        f"workers={args.workers}；resume={args.resume}"
    )

    def process_event(segment: Path, event_dir: Path) -> dict[str, Any]:
        try:
            return review_continue_event(
                segment, event_dir, args.output_dir, api_key, args
            )
        except Exception as error:
            return {
                "segment": segment.name,
                "event": event_dir.name,
                "status": "failed",
                "error": str(error),
            }

    results: list[dict[str, Any] | None] = [None] * len(events)
    if args.workers == 1:
        for index, (segment, event_dir) in enumerate(events, start=1):
            print(f"[{index}/{len(events)}] {segment.name}/{event_dir.name}")
            event_result = process_event(segment, event_dir)
            results[index - 1] = event_result
            stream = (
                sys.stderr
                if event_result["status"] == "failed"
                else sys.stdout
            )
            print(
                f"  {event_result.get('error', event_result['status'])}",
                file=stream,
            )
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_events = {
                executor.submit(process_event, segment, event_dir): (
                    index,
                    segment,
                    event_dir,
                )
                for index, (segment, event_dir) in enumerate(events)
            }
            for completed, future in enumerate(
                as_completed(future_events), start=1
            ):
                index, segment, event_dir = future_events[future]
                event_result = future.result()
                results[index] = event_result
                stream = (
                    sys.stderr
                    if event_result["status"] == "failed"
                    else sys.stdout
                )
                print(
                    f"[{completed}/{len(events)}] "
                    f"{segment.name}/{event_dir.name}: "
                    f"{event_result.get('error', event_result['status'])}",
                    file=stream,
                )

    completed_results = [item for item in results if item is not None]
    doubtful = collect_continue_doubtful_reviews(args.output_dir)
    summary = {
        "dataset_root": str(args.dataset_root),
        "model": args.model,
        "dry_run": args.dry_run,
        "workers": args.workers,
        "resume": args.resume,
        "total": len(completed_results),
        "updated": sum(
            item["status"] == "updated" for item in completed_results
        ),
        "skipped_existing": sum(
            item["status"] == "skipped_existing"
            for item in completed_results
        ),
        "skipped_completed": sum(
            item["status"] == "skipped_completed"
            for item in completed_results
        ),
        "dry_run_ok": sum(
            item["status"] == "dry_run" for item in completed_results
        ),
        "failed": sum(
            item["status"] == "failed" for item in completed_results
        ),
        "manual_review_count": len(doubtful),
        "events": completed_results,
    }
    atomic_write_json(args.output_dir / "doubtful_events.json", doubtful)
    atomic_write_json(args.output_dir / "run_summary.json", summary)
    print(
        f"完成：updated={summary['updated']} dry_run={summary['dry_run_ok']} "
        f"skipped_completed={summary['skipped_completed']} "
        f"failed={summary['failed']} manual_review={summary['manual_review_count']}"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
