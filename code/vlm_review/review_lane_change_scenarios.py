#!/usr/bin/env python
"""用前后双视角视频和辅助CSV审查换道事件场景。"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from lane_change_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
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
CROSS_TIME_EDGE_RATIO = 0.1
VALID_LANE_CHANGE_SITUATIONS = {
    "向左完成换道",
    "向右完成换道",
    "向左换道后放弃",
    "向右换道后放弃",
    "未发生换道",
    "无法确认",
}
VALID_CONSISTENCY = {"一致", "部分一致", "明显不一致", "视觉不足"}
UNRELATED_SCENE_TERMS = (
    "车牌号",
    "限速牌",
    "限速标志",
    "指路牌",
    "指路标志",
    "隔音屏",
    "隔音墙",
    "声屏障",
)


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compact_numeric_sequence(
    rows: list[dict[str, str]], field: str, positive_only: bool = False
) -> list[int | float]:
    values: list[int | float] = []
    for row in rows:
        number = to_float(row.get(field))
        if number is None or (positive_only and number <= 0):
            continue
        value: int | float = int(number) if number.is_integer() else round(number, 6)
        if not values or values[-1] != value:
            values.append(value)
    return values


def field_transitions(
    rows: list[dict[str, str]], field: str, positive_only: bool = False
) -> list[dict[str, int | float]]:
    transitions: list[dict[str, int | float]] = []
    last_value: int | float | None = None
    for row in rows:
        event_time = to_float(row.get("event_time"))
        number = to_float(row.get(field))
        if (
            event_time is None
            or number is None
            or (positive_only and number <= 0)
        ):
            continue
        value: int | float = int(number) if number.is_integer() else round(number, 6)
        if value == last_value:
            continue
        transitions.append({"event_time": round(event_time, 3), "value": value})
        last_value = value
    return transitions


def build_slice_plan(record: dict[str, Any]) -> dict[str, Any]:
    evidence = record.get("Evidence") or record.get("evidence") or {}
    start = to_float(evidence.get("Lane_line_overlap_start_time_s"))
    cross_raw = to_float(evidence.get("Cross_line_time_s"))
    end = to_float(evidence.get("Lane_change_end_time_s"))
    if start is None or cross_raw is None or end is None:
        raise ReviewError("record Evidence缺少换道开始、越线或结束时间")
    if start < 0 or end <= start:
        raise ReviewError(f"无效换道时间区间: start={start}, end={end}")

    interval = end - start
    cross_ratio = (cross_raw - start) / interval
    sampling_fallback_applied = (
        cross_raw <= start
        or cross_raw >= end
        or cross_ratio <= CROSS_TIME_EDGE_RATIO
        or cross_ratio >= 1 - CROSS_TIME_EDGE_RATIO
    )
    slice_3_time = (
        (start + end) / 2 if sampling_fallback_applied else cross_raw
    )
    return {
        "recorded_times": {
            "Lane_line_overlap_start_time_s": round(start, 6),
            "Cross_line_time_s": round(cross_raw, 6),
            "Lane_change_end_time_s": round(end, 6),
        },
        "cross_time_sampling_fallback_applied": sampling_fallback_applied,
        "sampling_fallback_reason": (
            "原越线时刻明显贴近换道区间边缘，仅第三切片选用开始与结束的中点"
            if sampling_fallback_applied
            else ""
        ),
        "slice_3_sampling_time_s": round(slice_3_time, 6),
        "slices": [
            {"slice_index": 1, "role": "事件初始", "time_s": 0.01},
            {"slice_index": 2, "role": "车道线重叠开始", "time_s": round(start, 6)},
            {
                "slice_index": 3,
                "role": "越线时刻或切片用换道区间中点",
                "time_s": round(slice_3_time, 6),
            },
            {"slice_index": 4, "role": "换道结束", "time_s": round(end, 6)},
            {"slice_index": 5, "role": "视频末帧", "time_s": None},
        ],
    }


def extract_view_frames(
    video_path: Path,
    frame_dir: Path,
    view: str,
    slice_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    video_info = probe_video(video_path)
    last_time = float(video_info["last_frame_time"])
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    view_label = "前向30度" if view == "front" else "后向"

    for item in slice_plan["slices"]:
        index = int(item["slice_index"])
        is_last = index == 5
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
                "slice_index": index,
                "view": view,
                "label": f"切片{index}-{view_label}视角",
                "time": round(sample_time, 6),
                "is_last_frame": is_last,
                "path": frame_path,
            }
        )
    return frames


def extract_dual_view_frames(
    front_video: Path,
    rear_video: Path,
    frame_dir: Path,
    slice_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    front_frames = extract_view_frames(
        front_video, frame_dir / "front", "front", slice_plan
    )
    rear_frames = extract_view_frames(
        rear_video, frame_dir / "rear", "rear", slice_plan
    )
    paired: list[dict[str, Any]] = []
    for front, rear in zip(front_frames, rear_frames):
        paired.extend([front, rear])
    for index, frame in enumerate(paired, start=1):
        frame["index"] = index
    return paired


def overlap_transitions(
    rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    last_state: str | None = None
    for row in rows:
        event_time = to_float(row.get("event_time"))
        left = (to_float(row.get("overlap_LeftLine")) or 0) > 0
        right = (to_float(row.get("overlap_RightLine")) or 0) > 0
        state = "both" if left and right else "left" if left else "right" if right else "none"
        if event_time is None or state == last_state:
            continue
        transitions.append({"event_time": round(event_time, 3), "state": state})
        last_state = state
    return transitions


def infer_lane_change_situation(
    rows: list[dict[str, str]],
) -> dict[str, Any]:
    transitions = overlap_transitions(rows)
    active = [
        item for item in transitions if item["state"] in {"left", "right"}
    ]
    start_boundary = active[0]["state"] if active else None
    end_boundary = active[-1]["state"] if active else None
    mapping = {
        ("left", "right"): "向左完成换道",
        ("right", "left"): "向右完成换道",
        ("left", "left"): "向左换道后放弃",
        ("right", "right"): "向右换道后放弃",
    }
    return {
        "overlap_transitions": transitions,
        "start_boundary": start_boundary,
        "end_boundary": end_boundary,
        "inferred_situation": mapping.get((start_boundary, end_boundary)),
    }


def nearest_row(
    rows: list[dict[str, str]], target_time: float
) -> dict[str, str] | None:
    timed_rows = [
        (abs(float(row["event_time"]) - target_time), row)
        for row in rows
        if to_float(row.get("event_time")) is not None
    ]
    return min(timed_rows, key=lambda item: item[0])[1] if timed_rows else None


def measurement_summary(
    rows: list[dict[str, str]],
    start: float,
    end: float,
    record_evidence: dict[str, Any],
) -> dict[str, Any]:
    start_row = nearest_row(rows, start)
    ttc = to_float(start_row.get("TTC_FV")) if start_row else None
    ttc_threshold = to_float(start_row.get("Thres_TTC_FV")) if start_row else None
    front_measurement_exists = ttc is not None and ttc >= 0
    ttc_insufficient = (
        ttc < ttc_threshold
        if front_measurement_exists
        and ttc_threshold is not None
        and ttc_threshold >= 0
        else None
    )

    window_rows = [
        row
        for row in rows
        if (event_time := to_float(row.get("event_time"))) is not None
        and start <= event_time <= end
    ]
    rear_measurements = [
        (
            float(row["event_time"]),
            float(row["dis_RVTL"]),
            to_float(row.get("Thres_dis_RVTL")),
        )
        for row in window_rows
        if to_float(row.get("dis_RVTL")) is not None
        and float(row["dis_RVTL"]) >= 0
    ]
    comparison_rows = [
        item
        for item in rear_measurements
        if item[2] is not None and item[2] >= 0
    ]
    rear_exists = bool(rear_measurements)
    rear_gap_too_close = (
        any(distance < threshold for _, distance, threshold in comparison_rows)
        if comparison_rows
        else False if not rear_exists else None
    )
    closest_rear = (
        min(rear_measurements, key=lambda item: item[1])
        if rear_measurements
        else None
    )
    return {
        "front_vehicle_at_lane_change_start": {
            "measurement_exists": front_measurement_exists,
            "TTC_FV_s": round(ttc, 3) if front_measurement_exists else None,
            "Thres_TTC_FV_s": (
                round(ttc_threshold, 3)
                if ttc_threshold is not None and ttc_threshold >= 0
                else None
            ),
            "programmatic_result": {
                "ttc_insufficient": ttc_insufficient,
                "rule": "TTC_FV < Thres_TTC_FV",
            },
        },
        "target_lane_rear_vehicle_during_change": {
            "measurement_exists": rear_exists,
            "closest_dis_RVTL_m": (
                round(closest_rear[1], 3) if closest_rear else None
            ),
            "threshold_at_closest_m": (
                round(closest_rear[2], 3)
                if closest_rear and closest_rear[2] is not None
                else None
            ),
            "valid_measurement_count": len(rear_measurements),
            "programmatic_result": {
                "gap_too_close": rear_gap_too_close,
                "rule": "dis_RVTL < Thres_dis_RVTL（仅比较非负有效值）",
            },
        },
        "record_numeric_cross_check": {
            "Minimum_front_vehicle_TTC_s": record_evidence.get(
                "Minimum_front_vehicle_TTC_s"
            ),
            "Required_front_vehicle_TTC_s": record_evidence.get(
                "Required_front_vehicle_TTC_s"
            ),
            "Minimum_target_lane_rear_gap_m": record_evidence.get(
                "Minimum_target_lane_rear_gap_m"
            ),
            "Required_target_lane_rear_gap_at_minimum_m": record_evidence.get(
                "Required_target_lane_rear_gap_at_minimum_m"
            ),
        },
    }


def decoded_sequence(
    rows: list[dict[str, str]], field: str, mapping: dict[str, str]
) -> list[str]:
    result: list[str] = []
    for value in compact_numeric_sequence(rows, field):
        name = mapping.get(str(value), f"unknown_{value}")
        if not result or result[-1] != name:
            result.append(name)
    return result


def lane_index_validity_issues(
    rows: list[dict[str, str]],
) -> list[dict[str, int | float]]:
    issues: list[dict[str, int | float]] = []
    seen: set[tuple[int, int]] = set()
    for row in rows:
        event_time = to_float(row.get("event_time"))
        lane_count = to_float(row.get("LaneNumSameDirection"))
        lane_index = to_float(row.get("EgoLaneIndex"))
        if (
            event_time is None
            or lane_count is None
            or lane_index is None
            or lane_count <= 0
            or lane_index <= 0
            or lane_index <= lane_count
        ):
            continue
        pair = (int(lane_count), int(lane_index))
        if pair in seen:
            continue
        seen.add(pair)
        issues.append(
            {
                "first_event_time": round(event_time, 3),
                "main_lane_count": pair[0],
                "ego_lane_index": pair[1],
            }
        )
    return issues


def build_lane_change_auxiliary_summary(
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

    line_evidence = infer_lane_change_situation(all_evidence_rows)
    lane_transitions = field_transitions(
        all_map_rows, "EgoLaneIndex", positive_only=True
    )
    lane_values = [item["value"] for item in lane_transitions]
    initial_lane = lane_values[0] if lane_values else None
    final_lane = lane_values[-1] if lane_values else None
    inferred = line_evidence["inferred_situation"]
    map_supports = (
        inferred == "向左完成换道"
        and initial_lane is not None
        and final_lane is not None
        and final_lane < initial_lane
    ) or (
        inferred == "向右完成换道"
        and initial_lane is not None
        and final_lane is not None
        and final_lane > initial_lane
    ) or (
        inferred in {"向左换道后放弃", "向右换道后放弃"}
        and initial_lane is not None
        and final_lane == initial_lane
    )
    record_direction = str(record_evidence.get("Lane_change_direction", "")).lower()
    record_supports = (
        inferred in {"向左完成换道", "向左换道后放弃"}
        and record_direction == "left"
    ) or (
        inferred in {"向右完成换道", "向右换道后放弃"}
        and record_direction == "right"
    )
    if inferred and map_supports:
        confidence = "高"
    elif inferred and record_supports:
        confidence = "中"
    else:
        confidence = "低"

    start = float(slice_plan["recorded_times"]["Lane_line_overlap_start_time_s"])
    end = float(slice_plan["recorded_times"]["Lane_change_end_time_s"])
    speed_values = [
        value
        for row in all_evidence_rows
        if (value := to_float(row.get("Ego_velocity"))) is not None
    ]
    evidence_columns = [
        "event_time",
        "Ego_velocity",
        "RVTL_Velocity",
        "overlap_LeftLine",
        "overlap_RightLine",
        "TTC_FV",
        "dis_RVTL",
        "Thres_TTC_FV",
        "Thres_dis_RVTL",
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
    return {
        "warning": (
            "未读取ObjInfo，也未纳入trigger/com或Article_status合规字段。"
            "车道线、地图和原始描述用于交叉核验；TTC/距离比较结果由程序计算。"
        ),
        "slice_plan": slice_plan,
        "record": {
            "Location": record.get("Location"),
            "Date": record.get("Date"),
            "Time": record.get("Time"),
            "Article_ID": (record.get("Article") or {}).get("ID"),
            "EventAnchor": record.get("EventAnchor"),
            "Lane_change_direction": record_evidence.get("Lane_change_direction"),
            "original_Scenario_description": result.get("Scenario_description"),
            "original_Driving_suggestion": result.get("Driving_suggestion"),
        },
        "lane_change_evidence": {
            **line_evidence,
            "EgoLaneIndex_transitions": lane_transitions,
            "main_lane_count_sequence": compact_numeric_sequence(
                all_map_rows, "LaneNumSameDirection", positive_only=True
            ),
            "lane_index_validity_issues": lane_index_validity_issues(
                all_map_rows
            ),
            "map_supports_overlap_inference": map_supports,
            "record_direction_supports_overlap_inference": record_supports,
            "inference_confidence": confidence,
            "instruction": (
                "这是高置信换道候选，除非双视角连续图像有清晰直接反证，否则采用"
                if confidence == "高"
                else "必须结合双视角图像继续核验"
            ),
        },
        "obstacle_evidence": measurement_summary(
            all_evidence_rows, start, end, record_evidence
        ),
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
        },
        "EvidenceChain_samples": [
            select_columns(row, evidence_columns) for row in evidence_samples_raw
        ],
        "EgoInfo_samples": [
            select_columns(row, ["event_time", "Ego_velocity"])
            for row in ego_samples_raw
        ],
        "MapInfo_samples": [
            select_columns(row, map_columns) for row in map_samples_raw
        ],
    }


LICENSE_PLATE_PATTERN = re.compile(
    r"(?<![A-Z0-9])"
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领]"
    r"[A-Z](?:\s*[·•]?\s*[A-Z0-9]){5,6}"
    r"(?![A-Z0-9])",
    re.IGNORECASE,
)
FRAME_REFERENCE_PATTERN = re.compile(
    r"(?:帧\s*\d+|第\s*\d+\s*帧|切片\s*\d+)"
    r"(?:\s*[-—~～至到]\s*(?:帧\s*\d+|\d+\s*帧?|切片\s*\d+))?"
    r"(?:\s*[（(]\s*\d+(?:\.\d+)?\s*(?:s|秒)\s*[）)])?"
    r"\s*[：:]?"
)
TIMESTAMP_PAREN_PATTERN = re.compile(
    r"[（(]\s*(?:约|大约)?\s*\d+(?:\.\d+)?"
    r"(?:\s*[-—~～至到]\s*\d+(?:\.\d+)?)?"
    r"\s*(?:s|秒)(?:起|时|后|处|左右|附近)?\s*[）)]"
)


def remove_unrelated_clauses(text: str) -> str:
    sentences = re.findall(r"[^。！？]+[。！？]?", text)
    cleaned: list[str] = []
    for sentence in sentences:
        punctuation = sentence[-1] if sentence[-1:] in "。！？" else ""
        body = sentence[:-1] if punctuation else sentence
        clauses = [
            clause.strip()
            for clause in re.split(r"[，；]", body)
            if clause.strip()
            and not any(term in clause for term in UNRELATED_SCENE_TERMS)
        ]
        if clauses:
            cleaned.append("，".join(clauses) + punctuation)
    return "".join(cleaned)


def sanitize_text(text: str, remove_unrelated: bool = False) -> str:
    text = LICENSE_PLATE_PATTERN.sub("", text)
    text = FRAME_REFERENCE_PATTERN.sub("", text)
    text = TIMESTAMP_PAREN_PATTERN.sub("", text)
    text = text.replace("程序判断距离未过近", "车距未见过近")
    text = re.sub(r"[，,；]\s*未对后方车辆造成干扰", "", text)
    if remove_unrelated:
        text = remove_unrelated_clauses(text)
    text = re.sub(r"[（(]\s*[）)]", "", text)
    text = re.sub(r"\s+([，。；：])", r"\1", text)
    text = re.sub(r"[，。；]{2,}", lambda match: match.group(0)[-1], text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ，,；;：:")


def integrate_lane_change_situation(description: str, situation: str) -> str:
    description = re.sub(
        r"^换道(?:情况|状态)：[^。]+。", "", description.strip()
    ).strip()
    markers = {
        "向左完成换道": ("向左换", "左侧车道"),
        "向右完成换道": ("向右换", "右侧车道"),
        "向左换道后放弃": ("向左", "回到原车道", "未完成换道"),
        "向右换道后放弃": ("向右", "回到原车道", "未完成换道"),
        "未发生换道": ("未换道", "保持原车道"),
        "无法确认": ("无法确认", "难以确认"),
    }
    if situation in {"向左换道后放弃", "向右换道后放弃"}:
        present = all(marker in description for marker in markers[situation])
    else:
        present = any(marker in description for marker in markers[situation])
    if present:
        return description
    clauses = {
        "向左完成换道": "事件中自车向左完成换道",
        "向右完成换道": "事件中自车向右完成换道",
        "向左换道后放弃": "自车向左侧车道偏移后回到原车道，未完成换道",
        "向右换道后放弃": "自车向右侧车道偏移后回到原车道，未完成换道",
        "未发生换道": "事件中自车保持原车道行驶，未发生换道",
        "无法确认": "受画面限制，自车换道状态无法确认",
    }
    return description.rstrip("。") + "。" + clauses[situation] + "。"


def normalize_lane_change_model_result(
    value: dict[str, Any], auxiliary: dict[str, Any]
) -> dict[str, Any]:
    situation = str(value.get("lane_change_situation", "")).strip()
    if situation not in VALID_LANE_CHANGE_SITUATIONS:
        raise ReviewError(f"模型返回无效换道情况: {situation!r}")
    description = str(value.get("Scenario_description_VLM", "")).strip()
    suggestion = str(value.get("Driving_suggestion_VLM", "")).strip()
    if not description or not suggestion:
        raise ReviewError("模型未返回完整的场景描述或驾驶建议")

    lane_review = value.get("lane_change_review")
    obstacle_review = value.get("obstacle_review")
    if not isinstance(lane_review, dict):
        raise ReviewError("模型未返回lane_change_review")
    if not isinstance(obstacle_review, dict):
        raise ReviewError("模型未返回obstacle_review")
    front_review = obstacle_review.get("front_vehicle_at_lane_change_start")
    rear_review = obstacle_review.get("target_lane_rear_vehicle_during_change")
    if not isinstance(front_review, dict) or not isinstance(rear_review, dict):
        raise ReviewError("模型未返回完整的前后车核验结果")

    obstacle_facts = auxiliary["obstacle_evidence"]
    front_facts = obstacle_facts["front_vehicle_at_lane_change_start"]
    rear_facts = obstacle_facts["target_lane_rear_vehicle_during_change"]
    front_review["ttc_insufficient_by_evidence"] = front_facts[
        "programmatic_result"
    ]["ttc_insufficient"]
    rear_review["gap_too_close_by_evidence"] = rear_facts[
        "programmatic_result"
    ]["gap_too_close"]
    obstacle_review["programmatic_evidence"] = obstacle_facts

    description = sanitize_text(description, remove_unrelated=True)
    suggestion = sanitize_text(suggestion)
    visual_observations = value.get("visual_observations")
    weather_light = (
        str(visual_observations.get("weather_light", "")).strip()
        if isinstance(visual_observations, dict)
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
    value["Scenario_description_VLM"] = integrate_lane_change_situation(
        description, situation
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

    lane_evidence = auxiliary["lane_change_evidence"]
    inferred = lane_evidence.get("inferred_situation")
    confidence = lane_evidence.get("inference_confidence")
    inference_conflict = (
        inferred in VALID_LANE_CHANGE_SITUATIONS
        and situation != inferred
        and confidence in {"高", "中"}
    )
    if inference_conflict:
        consistency["status"] = "明显不一致"
        conflict_point = {
            "auxiliary_claim": (
                f"车道线序列与车道编号支持“{inferred}”"
            ),
            "visual_finding": f"模型视觉结论为“{situation}”",
            "reason": "换道完成状态或方向与结构化换道证据不一致",
        }
        if conflict_point not in doubtful_points:
            doubtful_points.append(conflict_point)
        lane_review["auxiliary_inference_consistent"] = False

    lane_index_issues = lane_evidence.get("lane_index_validity_issues", [])
    if lane_index_issues:
        consistency["status"] = "明显不一致"
        for issue in lane_index_issues:
            issue_point = {
                "auxiliary_claim": (
                    f"同方向主车道数为{issue['main_lane_count']}，"
                    f"但EgoLaneIndex出现{issue['ego_lane_index']}"
                ),
                "visual_finding": (
                    f"视觉车道结论为初始{lane_review.get('initial_lane')}、"
                    f"最终{lane_review.get('final_lane')}"
                ),
                "reason": "车道编号超过同方向主车道总数，辅助字段内部矛盾",
            }
            if issue_point not in doubtful_points:
                doubtful_points.append(issue_point)

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
        value["requires_manual_review"] = situation == "无法确认"
    if situation == "无法确认":
        value["requires_manual_review"] = True
    if value["requires_manual_review"]:
        current_reason = str(value.get("manual_review_reason", "")).strip()
        if lane_index_issues:
            value["manual_review_reason"] = (
                "EgoLaneIndex超过同方向主车道总数，需核验地图车道编号"
            )
        elif not current_reason:
            value["manual_review_reason"] = (
                "换道方向、完成状态或关键前后车关系需要人工复核"
            )
    if not value["requires_manual_review"]:
        value["manual_review_reason"] = ""

    unsafe_gap = (
        front_review["ttc_insufficient_by_evidence"] is True
        or rear_review["gap_too_close_by_evidence"] is True
    )
    if unsafe_gap and not any(
        marker in suggestion
        for marker in ("暂缓", "放弃换道", "保持原车道", "扩大", "拉开")
    ):
        value["Driving_suggestion_VLM"] = (
            suggestion.rstrip("。")
            + "。应暂缓换道并保持原车道，先扩大与相关车辆的安全余量。"
        )
    elif situation in {"向左换道后放弃", "向右换道后放弃"} and "保持原车道" not in suggestion:
        value["Driving_suggestion_VLM"] = (
            suggestion.rstrip("。")
            + "。当前宜保持原车道，待目标车道前后间距充足后再择机换道。"
        )
    return value


def collect_lane_change_doubtful_reviews(
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
                "lane_change_situation": model_result.get(
                    "lane_change_situation"
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


def review_lane_change_event(
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
    video_dir = event_dir / "video" / "mp4"
    front_video = find_one(video_dir, "video_30_event_*.mp4")
    rear_video = find_one(video_dir, "video_rear_event_*.mp4")
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

    slice_plan = build_slice_plan(record)
    frames = extract_dual_view_frames(
        front_video,
        rear_video,
        output_root / "frames" / relative_dir,
        slice_plan,
    )
    front_frames = [frame for frame in frames if frame["view"] == "front"]
    rear_frames = [frame for frame in frames if frame["view"] == "rear"]
    slice_plan["slices"][-1]["front_time_s"] = front_frames[-1]["time"]
    slice_plan["slices"][-1]["rear_time_s"] = rear_frames[-1]["time"]
    sample_times = [frame["time"] for frame in front_frames]
    auxiliary = build_lane_change_auxiliary_summary(
        event_dir, record, sample_times, slice_plan
    )
    if args.dry_run:
        return {
            "segment": segment.name,
            "event": event_dir.name,
            "status": "dry_run",
            "record_path": str(record_path),
            "slice_plan": slice_plan,
            "lane_change_evidence": auxiliary["lane_change_evidence"],
            "obstacle_evidence": auxiliary["obstacle_evidence"],
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
        max_tokens=3200,
    )
    model_result = normalize_lane_change_model_result(model_result, auxiliary)

    review_path = output_root / "reviews" / relative_dir.with_suffix(".json")
    atomic_write_json(
        review_path,
        {
            "segment": segment.name,
            "event": event_dir.name,
            "record_path": str(record_path),
            "front_video_path": str(front_video),
            "rear_video_path": str(rear_video),
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
    result["Scenario_description_VLM"] = model_result["Scenario_description_VLM"]
    result["Driving_suggestion_VLM"] = model_result["Driving_suggestion_VLM"]
    atomic_write_json(record_path, record)
    return {
        "segment": segment.name,
        "event": event_dir.name,
        "status": "updated",
        "record_path": str(record_path),
        "lane_change_situation": model_result["lane_change_situation"],
        "requires_manual_review": model_result["requires_manual_review"],
        "manual_review_reason": model_result.get("manual_review_reason", ""),
        "auxiliary_consistency": model_result.get("auxiliary_consistency", {}),
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
        default=Path(__file__).resolve().parent / "outputs_lane_change",
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
        f"发现 {len(events)} 个换道事件；模型={args.model}；"
        f"模式={'dry-run' if args.dry_run else 'API审查并写入'}；"
        f"workers={args.workers}；resume={args.resume}"
    )

    def process_event(segment: Path, event_dir: Path) -> dict[str, Any]:
        try:
            return review_lane_change_event(
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
    doubtful = collect_lane_change_doubtful_reviews(args.output_dir)
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
            item["status"] == "skipped_existing" for item in completed_results
        ),
        "skipped_completed": sum(
            item["status"] == "skipped_completed" for item in completed_results
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
