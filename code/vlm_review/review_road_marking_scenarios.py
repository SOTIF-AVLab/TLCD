#!/usr/bin/env python
"""用前向视频和辅助CSV审查道路标线事件场景。"""

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

from road_marking_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
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
LINE_TYPES = {
    0: "unknown",
    1: "solid_line",
    2: "dashed_line",
    3: "double_solid_line",
    4: "double_dashed_line",
    5: "left_solid_right_dashed",
    6: "right_solid_left_dashed",
    7: "channelizing_line",
    10: "other_line",
}
VALID_INTERACTION_SITUATIONS = {
    "未观察到压线或越线",
    "向左偏移并压线但未越线",
    "向右偏移并压线但未越线",
    "向左越过车道线",
    "向右越过车道线",
    "与导流线发生交互",
    "多次或复合标线交互",
    "交互结果无法确认",
    "无法确认",
}
VALID_ACTIONS = {"未压线", "压线未越线", "越线", "结果无法确认"}
VALID_CONSISTENCY = {"一致", "部分一致", "明显不一致", "视觉不足"}
ABSOLUTE_TIME_PATTERN = re.compile(
    r"(?:在|于)?\s*\d+(?:\.\d+)?\s*(?:s|秒)"
    r"(?:时|后|前|左右|附近|开始|结束)?"
)
FORBIDDEN_CONCLUSION_TERMS = ("合规", "违规", "违反", "符合法规")


def record_line_interactions(record: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = record.get("Evidence") or record.get("evidence") or {}
    interactions = evidence.get("Line_interactions")
    if not isinstance(interactions, list):
        return []
    return [item for item in interactions if isinstance(item, dict)]


def evidence_event_end(rows: list[dict[str, str]]) -> float:
    times = [
        value
        for row in rows
        if (value := to_float(row.get("event_time"))) is not None
    ]
    if not times:
        raise ReviewError("EvidenceChain缺少有效event_time")
    return max(times)


def build_road_marking_slice_plan(
    record: dict[str, Any], event_end: float
) -> dict[str, Any]:
    """生成五个切片时间；只改变取帧时间，不修改record。"""
    if event_end <= 0:
        raise ReviewError(f"无效事件结束时间: {event_end}")
    interactions = record_line_interactions(record)
    starts = [
        value
        for item in interactions
        if (value := to_float(item.get("Overlap_start_time_s")))
        is not None
        and value >= 0
    ]
    ends = [
        value
        for item in interactions
        if (value := to_float(item.get("Interaction_end_time_s")))
        is not None
        and value >= 0
    ]
    overlap_start_fallback = not starts
    slice_3_time = min(starts) if starts else 3.0
    recorded_last_end = max(ends) if ends else None
    end_fallback = (
        recorded_last_end is None
        or abs(recorded_last_end - event_end) <= 0.05
    )
    slice_4_time = (
        max(0.01, event_end - 1.0)
        if end_fallback
        else float(recorded_last_end)
    )
    return {
        "source": (
            "record Line_interactions与EvidenceChain事件结束时间，"
            "仅用于取帧，不修改record"
        ),
        "event_end_time_s": round(event_end, 6),
        "recorded_interaction_times": {
            "earliest_Overlap_start_time_s": (
                round(min(starts), 6) if starts else None
            ),
            "latest_Interaction_end_time_s": (
                round(max(ends), 6) if ends else None
            ),
        },
        "overlap_start_fallback_applied": overlap_start_fallback,
        "interaction_end_fallback_applied": end_fallback,
        "interaction_end_fallback_reason": (
            "未记录Interaction_end_time_s，第四切片使用事件结束前1秒"
            if recorded_last_end is None
            else (
                "Interaction_end_time_s等于事件结束时间，"
                "第四切片使用事件结束前1秒"
                if end_fallback
                else ""
            )
        ),
        "slices": [
            {"slice_index": 1, "role": "事件初始", "time_s": 0.01},
            {"slice_index": 2, "role": "早期场景", "time_s": 1.5},
            {
                "slice_index": 3,
                "role": "最早标线重叠开始或默认时刻",
                "time_s": round(slice_3_time, 6),
            },
            {
                "slice_index": 4,
                "role": "最晚标线交互结束或事件结束前1秒",
                "time_s": round(slice_4_time, 6),
            },
            {"slice_index": 5, "role": "视频末帧", "time_s": None},
        ],
    }


def extract_road_marking_frames(
    video_path: Path,
    frame_dir: Path,
    slice_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    video_info = probe_video(video_path)
    last_time = float(video_info["last_frame_time"])
    fit_slice_plan_to_video(slice_plan, last_time)
    frame_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
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
                "label": f"切片{index}-前向30度视角",
                "time": round(sample_time, 6),
                "is_last_frame": is_last,
                "path": frame_path,
            }
        )
    return frames


def fit_slice_plan_to_video(
    slice_plan: dict[str, Any], last_time: float
) -> None:
    """将无法取到的切片回退到视频范围内，并保留原请求时间。"""
    if last_time <= 0.01:
        raise ReviewError(f"视频可取时长过短: {last_time}")
    fallbacks: list[dict[str, Any]] = []
    for item in slice_plan["slices"]:
        requested = to_float(item.get("time_s"))
        if requested is None or requested <= last_time:
            continue
        index = int(item["slice_index"])
        fallback = (
            max(0.01, last_time - 1.0)
            if index == 4
            else max(0.01, last_time - 0.05)
        )
        item["requested_time_s"] = round(requested, 6)
        item["time_s"] = round(fallback, 6)
        fallbacks.append(
            {
                "slice_index": index,
                "requested_time_s": round(requested, 6),
                "fallback_time_s": round(fallback, 6),
                "reason": (
                    "原交互结束时间超出前向视频范围，"
                    "仅第四切片回退到视频末端前1秒"
                    if index == 4
                    else "原切片时间超出前向视频范围，回退到可取末端"
                ),
            }
        )
    slice_plan["video_range_fallbacks"] = fallbacks


def row_overlap_state(row: dict[str, str]) -> str:
    left = (to_float(row.get("overlap_LeftLine")) or 0) > 0
    right = (to_float(row.get("overlap_RightLine")) or 0) > 0
    if left and right:
        return "both"
    if left:
        return "left"
    if right:
        return "right"
    return "none"


def decode_line_type(code: int) -> str:
    return LINE_TYPES.get(code, f"unknown_{code}")


def _line_type_candidates(
    rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for row in rows:
        state = row_overlap_state(row)
        sides = (
            ("left", "right") if state == "both" else (state,)
        )
        for side in sides:
            if side not in {"left", "right"}:
                continue
            field = (
                "MAP_Type_Left1" if side == "left" else "MAP_Type_Right1"
            )
            number = to_float(row.get(field))
            if number is None:
                continue
            code = int(number)
            key = (side, code)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                {
                    "boundary": side,
                    "code": code,
                    "type": decode_line_type(code),
                }
            )
    return candidates


def _finalize_overlap_run(
    rows: list[dict[str, str]],
    start_index: int,
    end_index: int,
    total_rows: int,
    order: int,
) -> dict[str, Any]:
    run_rows = rows[start_index : end_index + 1]
    transitions: list[dict[str, Any]] = []
    last_state: str | None = None
    for row in run_rows:
        state = row_overlap_state(row)
        if state == last_state:
            continue
        transitions.append(
            {
                "event_time": round(
                    to_float(row.get("event_time")) or 0.0, 3
                ),
                "state": state,
            }
        )
        last_state = state
    directional = [
        item["state"]
        for item in transitions
        if item["state"] in {"left", "right"}
    ]
    start_boundary = directional[0] if directional else None
    end_boundary = directional[-1] if directional else None
    truncated_start = start_index == 0
    truncated_end = end_index == total_rows - 1
    pair = (start_boundary, end_boundary)
    mapping = {
        ("left", "right"): ("向左越过车道线", "crossed", "left"),
        ("right", "left"): ("向右越过车道线", "crossed", "right"),
        ("left", "left"): (
            "向左偏移并压线但未越线",
            "overlapped",
            "left",
        ),
        ("right", "right"): (
            "向右偏移并压线但未越线",
            "overlapped",
            "right",
        ),
    }
    situation, action, direction = mapping.get(
        pair, ("交互结果无法确认", "unknown", None)
    )
    if truncated_end and start_boundary == end_boundary:
        situation, action = "交互结果无法确认", "unknown"
    contains_both = any(
        item["state"] == "both" for item in transitions
    )
    if action == "unknown":
        confidence = "低"
    elif contains_both or truncated_start:
        confidence = "中"
    elif action == "crossed":
        confidence = "高"
    else:
        confidence = "中"
    type_candidates = _line_type_candidates(run_rows)
    channelizing_candidate = bool(type_candidates) and {
        item["type"] for item in type_candidates
    } == {"channelizing_line"}
    return {
        "order": order,
        "start_time_s": round(
            to_float(run_rows[0].get("event_time")) or 0.0, 3
        ),
        "end_time_s": round(
            to_float(run_rows[-1].get("event_time")) or 0.0, 3
        ),
        "overlap_transitions": transitions,
        "active_boundary_sequence": directional,
        "start_boundary": start_boundary,
        "end_boundary": end_boundary,
        "inferred_direction": direction,
        "inferred_action": action,
        "inferred_situation": situation,
        "line_type_candidates": type_candidates,
        "channelizing_line_candidate": channelizing_candidate,
        "truncated_at_event_start": truncated_start,
        "truncated_at_event_end": truncated_end,
        "inference_confidence": confidence,
    }


def detect_overlap_runs(
    rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """按连续的非零overlap区间拆分并推断每段交互。"""
    runs: list[dict[str, Any]] = []
    start_index: int | None = None
    for index, row in enumerate(rows):
        active = row_overlap_state(row) != "none"
        if active and start_index is None:
            start_index = index
        if not active and start_index is not None:
            runs.append(
                _finalize_overlap_run(
                    rows,
                    start_index,
                    index - 1,
                    len(rows),
                    len(runs) + 1,
                )
            )
            start_index = None
    if start_index is not None:
        runs.append(
            _finalize_overlap_run(
                rows,
                start_index,
                len(rows) - 1,
                len(rows),
                len(runs) + 1,
            )
        )
    return runs


def overall_interaction_situation(
    runs: list[dict[str, Any]],
) -> str:
    if not runs:
        return "未观察到压线或越线"
    if len(runs) > 1:
        return "多次或复合标线交互"
    return str(runs[0]["inferred_situation"])


def record_interaction_consistency_issues(
    record_interactions: list[dict[str, Any]],
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    if len(record_interactions) != len(runs):
        issues.append(
            {
                "kind": "interaction_count_mismatch",
                "record_count": len(record_interactions),
                "overlap_run_count": len(runs),
            }
        )
    for index, (record_item, run) in enumerate(
        zip(record_interactions, runs), start=1
    ):
        expected_action = run.get("inferred_action")
        expected_side = run.get("inferred_direction")
        record_action = str(record_item.get("Action", "")).lower()
        record_side = str(record_item.get("Side", "")).lower()
        if (
            expected_action in {"crossed", "overlapped"}
            and record_action != expected_action
        ):
            issues.append(
                {
                    "kind": "action_mismatch",
                    "order": index,
                    "record_action": record_action,
                    "overlap_inference": expected_action,
                }
            )
        if (
            expected_side in {"left", "right"}
            and record_side != expected_side
        ):
            issues.append(
                {
                    "kind": "side_mismatch",
                    "order": index,
                    "record_side": record_side,
                    "overlap_inference": expected_side,
                }
            )
    return issues


def build_road_marking_auxiliary_summary(
    event_dir: Path,
    record: dict[str, Any],
    sample_times: list[float],
    slice_plan: dict[str, Any],
) -> dict[str, Any]:
    evidence_path = find_one(event_dir, "*_EvidenceChain.csv")
    ego_path = find_one(event_dir, "*_EgoInfo.csv")
    map_path = find_one(event_dir, "*_MapInfo.csv")
    all_evidence_rows = read_csv_rows(evidence_path)
    all_ego_rows = read_csv_rows(ego_path)
    all_map_rows = read_csv_rows(map_path)
    evidence_samples = nearest_csv_rows(evidence_path, sample_times)
    ego_samples = nearest_csv_rows(ego_path, sample_times)
    map_samples = nearest_csv_rows(map_path, sample_times)
    record_evidence = record.get("Evidence") or record.get("evidence") or {}
    result = record.get("Result") or record.get("result") or {}
    record_interactions = record_line_interactions(record)
    runs = detect_overlap_runs(all_evidence_rows)
    speed_values = [
        value
        for row in all_ego_rows
        if (value := to_float(row.get("Ego_velocity"))) is not None
    ]
    interaction_fields = (
        "Side",
        "Action",
        "Line_types",
        "Overlap_start_time_s",
        "Cross_line_time_s",
        "Interaction_end_time_s",
    )
    return {
        "warning": (
            "未读取ObjInfo，也未纳入trigger/com/Article_status字段。"
            "overlap、地图标线类型和record交互只用于辅助核验；"
            "地图标线类型不含颜色。"
        ),
        "slice_plan": copy.deepcopy(slice_plan),
        "record": {
            "Location": record.get("Location"),
            "Date": record.get("Date"),
            "Time": record.get("Time"),
            "Article_ID": (record.get("Article") or {}).get("ID"),
            "EventAnchor": record.get("EventAnchor"),
            "Line_interactions": [
                {
                    field: item.get(field)
                    for field in interaction_fields
                    if field in item
                }
                for item in record_interactions
            ],
            "Maximum_continuous_line_overlap_s": record_evidence.get(
                "Maximum_continuous_line_overlap_s"
            ),
            "Maximum_allowed_line_overlap_s": record_evidence.get(
                "Maximum_allowed_line_overlap_s"
            ),
            "Road_types": record_evidence.get("Road_types"),
            "Lane_types": record_evidence.get("Lane_types"),
            "original_Scenario_description": result.get(
                "Scenario_description"
            ),
            "original_Driving_suggestion": result.get(
                "Driving_suggestion"
            ),
        },
        "overlap_analysis": {
            "runs": runs,
            "expected_interaction_situation": (
                overall_interaction_situation(runs)
            ),
            "record_interaction_consistency_issues": (
                record_interaction_consistency_issues(
                    record_interactions, runs
                )
            ),
            "instruction": (
                "逐段用图像复核压线首尾、是否真正越线以及标线类型；"
                "事件末端被截断时不得仅凭同侧首尾断言未越线。"
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
            select_columns(
                row,
                [
                    "event_time",
                    "overlap_LeftLine",
                    "overlap_RightLine",
                    "lanechange_stage",
                    "MAP_Type_Left1",
                    "MAP_Type_Right1",
                    "Time_ContinuousLineOverlap",
                    "Thres_MaxContinuousLineOverlap",
                ],
            )
            for row in evidence_samples
        ],
        "EgoInfo_samples": [
            select_columns(row, ["event_time", "Ego_velocity"])
            for row in ego_samples
        ],
        "MapInfo_samples": [
            select_columns(
                row,
                [
                    "event_time",
                    "Road_type",
                    "Road_Curve",
                    "Road_Slope",
                    "Lane_type_CurrentLane",
                    "LaneNumSameDirection",
                    "EgoLaneIndex",
                    "MAP_Type_Left1",
                    "MAP_Type_Right1",
                ],
            )
            for row in map_samples
        ],
    }


def remove_forbidden_clauses(text: str) -> str:
    sentences = re.findall(r"[^。！？]+[。！？]?", text)
    cleaned: list[str] = []
    for sentence in sentences:
        punctuation = sentence[-1] if sentence[-1:] in "。！？" else ""
        body = sentence[:-1] if punctuation else sentence
        clauses = [
            clause.strip()
            for clause in re.split(r"[，；]", body)
            if clause.strip()
            and not any(
                term in clause for term in FORBIDDEN_CONCLUSION_TERMS
            )
        ]
        if clauses:
            cleaned.append("，".join(clauses) + punctuation)
    return "".join(cleaned)


def sanitize_road_marking_text(
    text: str, remove_unrelated: bool = False
) -> str:
    text = sanitize_text(text, remove_unrelated=remove_unrelated)
    text = ABSOLUTE_TIME_PATTERN.sub("", text)
    text = remove_forbidden_clauses(text)
    text = re.sub(
        r"^标线交互(?:情况|状态)：[^。]+。", "", text.strip()
    ).strip()
    text = re.sub(r"\s+([，。；：])", r"\1", text)
    text = re.sub(r"[，；]{2,}", "，", text)
    text = re.sub(r"。{2,}", "。", text)
    return text.strip(" ，；：")


def interaction_line_phrase(interactions: list[dict[str, Any]]) -> str:
    if not interactions:
        return "车道线"
    first = interactions[0]
    color = str(first.get("line_color", "")).strip()
    line_type = str(first.get("line_type", "")).strip()
    parts = [
        value
        for value in (color, line_type)
        if value and value not in {"无法确认", "其他"}
    ]
    return "".join(parts) if parts else "车道线"


def integrate_interaction_description(
    description: str,
    situation: str,
    interactions: list[dict[str, Any]],
) -> str:
    markers = {
        "未观察到压线或越线": ("未压线", "未越线", "保持在车道内"),
        "向左偏移并压线但未越线": ("向左偏移", "左侧车道线"),
        "向右偏移并压线但未越线": ("向右偏移", "右侧车道线"),
        "向左越过车道线": ("向左越", "向左换"),
        "向右越过车道线": ("向右越", "向右换"),
        "与导流线发生交互": ("导流线", "导流区"),
        "多次或复合标线交互": ("多次", "先后", "再次"),
        "交互结果无法确认": ("结果无法确认", "难以确认是否越线"),
        "无法确认": ("无法确认", "难以确认"),
    }
    if any(marker in description for marker in markers[situation]):
        return description
    line_phrase = interaction_line_phrase(interactions)
    clauses = {
        "未观察到压线或越线": (
            "事件过程中自车保持在当前车道内行驶，"
            "未观察到压线或越线"
        ),
        "向左偏移并压线但未越线": (
            f"随后自车向左偏移并与{line_phrase}发生重叠，"
            "但未完全越过该标线，之后回到车道内"
        ),
        "向右偏移并压线但未越线": (
            f"随后自车向右偏移并与{line_phrase}发生重叠，"
            "但未完全越过该标线，之后回到车道内"
        ),
        "向左越过车道线": (
            f"随后自车向左越过{line_phrase}，完成车道转换"
        ),
        "向右越过车道线": (
            f"随后自车向右越过{line_phrase}，完成车道转换"
        ),
        "与导流线发生交互": (
            "事件过程中自车与导流线或导流区边界发生交互"
        ),
        "多次或复合标线交互": (
            "事件过程中自车先后与道路标线发生多段交互"
        ),
        "交互结果无法确认": (
            "受事件末段或画面范围限制，标线交互结果无法确认"
        ),
        "无法确认": "受画面限制，自车与道路标线的交互无法确认",
    }
    return description.rstrip("。") + "。" + clauses[situation] + "。"


def add_doubtful_point(
    points: list[dict[str, Any]], point: dict[str, Any]
) -> None:
    if point not in points:
        points.append(point)


def model_interaction_matches_run(
    model_item: dict[str, Any], run: dict[str, Any]
) -> bool:
    action_map = {
        "crossed": "越线",
        "overlapped": "压线未越线",
    }
    expected_action = action_map.get(run.get("inferred_action"))
    if expected_action and model_item.get("action") != expected_action:
        return False
    expected_side = run.get("inferred_direction")
    if (
        expected_side in {"left", "right"}
        and model_item.get("side") != expected_side
    ):
        return False
    return True


def normalize_road_marking_model_result(
    value: dict[str, Any], auxiliary: dict[str, Any]
) -> dict[str, Any]:
    situation = str(value.get("interaction_situation", "")).strip()
    if situation not in VALID_INTERACTION_SITUATIONS:
        raise ReviewError(f"模型返回无效标线交互情况: {situation!r}")
    description = str(value.get("Scenario_description_VLM", "")).strip()
    suggestion = str(value.get("Driving_suggestion_VLM", "")).strip()
    if not description or not suggestion:
        raise ReviewError("模型未返回完整的场景描述或驾驶建议")
    interaction_review = value.get("interaction_review")
    if not isinstance(interaction_review, dict):
        raise ReviewError("模型未返回interaction_review")
    interactions = interaction_review.get("interactions")
    if not isinstance(interactions, list):
        raise ReviewError("模型未返回interaction_review.interactions")
    for item in interactions:
        if not isinstance(item, dict) or item.get("action") not in VALID_ACTIONS:
            raise ReviewError("模型返回无效的逐段标线交互结果")

    description = sanitize_road_marking_text(
        description, remove_unrelated=True
    )
    suggestion = sanitize_road_marking_text(suggestion)
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
        description = (
            sanitize_road_marking_text(weather_light).rstrip("。")
            + "。"
            + description
        )
    value["Scenario_description_VLM"] = (
        integrate_interaction_description(
            description, situation, interactions
        )
    )
    value["Driving_suggestion_VLM"] = suggestion

    consistency = value.get("auxiliary_consistency")
    if not isinstance(consistency, dict):
        consistency = {}
        value["auxiliary_consistency"] = consistency
    status = str(consistency.get("status", "")).strip()
    if status not in VALID_CONSISTENCY:
        consistency["status"] = "视觉不足"
    doubtful_points = consistency.get("doubtful_points")
    if not isinstance(doubtful_points, list):
        doubtful_points = []

    analysis = auxiliary["overlap_analysis"]
    runs = analysis.get("runs", [])
    expected = analysis.get("expected_interaction_situation")
    informative_runs = [
        run
        for run in runs
        if run.get("inference_confidence") in {"高", "中"}
        and run.get("inferred_action") in {"crossed", "overlapped"}
    ]
    if (
        expected in VALID_INTERACTION_SITUATIONS
        and expected not in {"交互结果无法确认", "无法确认"}
        and informative_runs
        and situation != expected
    ):
        consistency["status"] = "明显不一致"
        add_doubtful_point(
            doubtful_points,
            {
                "auxiliary_claim": f"连续压线序列支持“{expected}”",
                "visual_finding": f"模型视觉结论为“{situation}”",
                "reason": "交互方向、次数或是否越线与结构化压线序列不一致",
            },
        )

    for index, run in enumerate(runs):
        if index >= len(interactions):
            break
        if (
            run.get("inference_confidence") in {"高", "中"}
            and not model_interaction_matches_run(
                interactions[index], run
            )
        ):
            consistency["status"] = "明显不一致"
            add_doubtful_point(
                doubtful_points,
                {
                    "auxiliary_claim": (
                        f"第{index + 1}段压线首尾支持"
                        f"“{run.get('inferred_situation')}”"
                    ),
                    "visual_finding": (
                        "模型逐段结论为"
                        f"“{interactions[index].get('action')}、"
                        f"{interactions[index].get('side')}”"
                    ),
                    "reason": "逐段交互方向或越线状态不一致",
                },
            )

    record_issues = analysis.get(
        "record_interaction_consistency_issues", []
    )
    for issue in record_issues:
        consistency["status"] = "明显不一致"
        add_doubtful_point(
            doubtful_points,
            {
                "auxiliary_claim": f"record交互记录：{issue}",
                "visual_finding": "EvidenceChain连续压线序列给出不同结果",
                "reason": "两类辅助信息的交互数量、方向或动作互相矛盾",
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
                "visual_finding": "视觉车道编号需要人工复核",
                "reason": "辅助车道编号超过同方向主车道总数",
            },
        )

    truncated_uncertain = any(
        run.get("truncated_at_event_end")
        and run.get("inferred_action") == "unknown"
        for run in runs
    ) and situation in {"交互结果无法确认", "无法确认"}
    if consistency.get("status") == "明显不一致":
        consistency["doubtful_points"] = doubtful_points
        value["requires_manual_review"] = True
    else:
        unconfirmed = consistency.get("unconfirmed_points")
        if not isinstance(unconfirmed, list):
            unconfirmed = []
        unconfirmed.extend(doubtful_points)
        consistency["unconfirmed_points"] = unconfirmed
        consistency["doubtful_points"] = []
        value["requires_manual_review"] = (
            situation in {"交互结果无法确认", "无法确认"}
            or truncated_uncertain
        )
    if value["requires_manual_review"]:
        if record_issues:
            value["manual_review_reason"] = (
                "record与EvidenceChain的标线交互记录存在矛盾"
            )
        elif lane_issues:
            value["manual_review_reason"] = (
                "EgoLaneIndex超过同方向主车道总数，需核验地图车道编号"
            )
        elif not str(value.get("manual_review_reason", "")).strip():
            value["manual_review_reason"] = (
                "标线交互方向或是否越线无法由当前画面确认"
            )
    else:
        value["manual_review_reason"] = ""

    line_types = {
        str(item.get("line_type", "")).strip() for item in interactions
    }
    if (
        line_types
        & {"单实线", "双实线", "实虚组合线", "道路边缘线"}
        and not any(
            marker in value["Driving_suggestion_VLM"]
            for marker in ("车道边界内", "避免压", "避免越", "提前规划")
        )
    ):
        value["Driving_suggestion_VLM"] = (
            value["Driving_suggestion_VLM"].rstrip("。")
            + "。建议保持在车道边界内行驶并提前规划路线，避免压越实线或道路边缘线。"
        )
    elif (
        "导流线" in line_types
        or situation == "与导流线发生交互"
    ) and not any(
        marker in value["Driving_suggestion_VLM"]
        for marker in ("规定路线", "导流区")
    ):
        value["Driving_suggestion_VLM"] = (
            value["Driving_suggestion_VLM"].rstrip("。")
            + "。建议沿规定路线行驶，避免继续驶入导流区。"
        )
    elif situation in {
        "向左偏移并压线但未越线",
        "向右偏移并压线但未越线",
    } and "车道中央" not in value["Driving_suggestion_VLM"]:
        value["Driving_suggestion_VLM"] = (
            value["Driving_suggestion_VLM"].rstrip("。")
            + "。建议平稳回到车道中央，待道路条件适合后再进行车道转换。"
        )
    elif (
        situation in {"向左越过车道线", "向右越过车道线"}
        and "虚线" in line_types
        and not any(
            marker in value["Driving_suggestion_VLM"]
            for marker in ("平顺", "短时", "车道中央")
        )
    ):
        value["Driving_suggestion_VLM"] = (
            value["Driving_suggestion_VLM"].rstrip("。")
            + "。确认道路条件后应平顺、短时完成车道转换，并回到车道中央。"
        )
    return value


def collect_road_marking_doubtful_reviews(
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
                "interaction_situation": model_result.get(
                    "interaction_situation"
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


def review_road_marking_event(
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
    evidence_path = find_one(event_dir, "*_EvidenceChain.csv")
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

    event_end = evidence_event_end(read_csv_rows(evidence_path))
    slice_plan = build_road_marking_slice_plan(record, event_end)
    frames = extract_road_marking_frames(
        video_path,
        output_root / "frames" / relative_dir,
        slice_plan,
    )
    slice_plan["slices"][-1]["actual_time_s"] = frames[-1]["time"]
    sample_times = [frame["time"] for frame in frames]
    auxiliary = build_road_marking_auxiliary_summary(
        event_dir, record, sample_times, slice_plan
    )
    if args.dry_run:
        return {
            "segment": segment.name,
            "event": event_dir.name,
            "status": "dry_run",
            "record_path": str(record_path),
            "slice_plan": slice_plan,
            "overlap_analysis": auxiliary["overlap_analysis"],
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
        max_tokens=3200,
    )
    model_result = normalize_road_marking_model_result(
        model_result, auxiliary
    )
    review_path = output_root / "reviews" / relative_dir.with_suffix(
        ".json"
    )
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
        "interaction_situation": model_result["interaction_situation"],
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
    parser.add_argument(
        "--dataset-root", type=Path, default=DEFAULT_DATASET_ROOT
    )
    parser.add_argument(
        "--segment-limit",
        type=int,
        default=2,
        help="按名称排序处理前N个segment；0表示全部，默认2",
    )
    parser.add_argument(
        "--event-limit",
        type=int,
        default=0,
        help="最多处理N个事件；0表示不限",
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
        / "outputs_road_marking",
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
        "--dry-run",
        action="store_true",
        help="只检查数据和切帧，不调用API、不改JSON",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.dataset_root.is_dir():
        print(
            f"错误：数据目录不存在: {args.dataset_root}",
            file=sys.stderr,
        )
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
                raise ReviewError(
                    "未找到指定事件: " + ", ".join(missing)
                )
        if args.event_limit > 0:
            events = events[: args.event_limit]
        if not events:
            raise ReviewError("未发现event_*目录")
        api_key = None if args.dry_run else load_api_key(
            args.api_key_source
        )
    except (OSError, ReviewError) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2

    print(
        f"发现 {len(events)} 个道路标线事件；模型={args.model}；"
        f"模式={'dry-run' if args.dry_run else 'API审查并写入'}；"
        f"workers={args.workers}；resume={args.resume}"
    )

    def process_event(
        segment: Path, event_dir: Path
    ) -> dict[str, Any]:
        try:
            return review_road_marking_event(
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
    doubtful = collect_road_marking_doubtful_reviews(args.output_dir)
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
    atomic_write_json(
        args.output_dir / "doubtful_events.json", doubtful
    )
    atomic_write_json(args.output_dir / "run_summary.json", summary)
    print(
        f"完成：updated={summary['updated']} "
        f"dry_run={summary['dry_run_ok']} "
        f"skipped_completed={summary['skipped_completed']} "
        f"failed={summary['failed']} "
        f"manual_review={summary['manual_review_count']}"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
