#!/usr/bin/env python
"""用前向视频和辅助CSV审查跟车距离事件场景。"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from following_distance_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from review_scenarios import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    LANE_TYPES,
    ROAD_TYPES,
    ReviewError,
    atomic_write_json,
    call_model,
    discover_events,
    extract_frames,
    find_one,
    load_api_key,
    load_completed_review,
    nearest_csv_rows,
    read_json,
    result_container,
    select_columns,
)


DEFAULT_DATASET_ROOT = Path(os.environ.get("TLCD_DATASET_ROOT", "."))
LOW_SPEED_CONGESTION_THRESHOLD_MPS = 16.5
DISTANCE_JUMP_MIN_ABS_M = 8.0
DISTANCE_JUMP_MIN_RELATIVE = 0.2
DISTANCE_JUMP_STRONG_ABS_M = 15.0
DISTANCE_JUMP_MAX_TIME_GAP_S = 0.2
DISTANCE_JUMP_LANE_CONTEXT_WINDOW_S = 0.75
MAX_DISTANCE_JUMP_CANDIDATES = 12
VALID_FOLLOWING_SITUATIONS = {
    "不存在同车道前车",
    "始终跟随同车道前车",
    "同车道前车驶离或变道（cut-out）",
    "相邻车道车辆切入自车道（cut-in）",
    "自车变道后跟随新的同车道前车",
    "自车变道后原跟车关系结束",
    "跟车关系发生复合变化",
    "无法确认",
}
VALID_APPLICABILITY = {"适用", "部分适用", "不适用", "无法确认"}
VALID_CONGESTION_ASSESSMENTS = {"拥堵", "不拥堵", "无法确认"}
VALID_DISTANCE_JUMP_INTERPRETATIONS = {
    "自车换道",
    "前车cut-out",
    "邻车cut-in",
    "跟车目标切换但类型无法确认",
    "测距异常或视觉不足",
    "无显著跳变",
    "多种变化组合",
}


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def numeric_sequence(
    rows: list[dict[str, str]], field: str
) -> list[int | float]:
    values: list[int | float] = []
    for row in rows:
        try:
            number = float(row.get(field, ""))
        except (TypeError, ValueError):
            continue
        value: int | float = int(number) if number.is_integer() else round(number, 6)
        if value not in values:
            values.append(value)
    return values


def field_transitions(
    rows: list[dict[str, str]], field: str
) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    last_value: int | float | None = None
    for row in rows:
        try:
            event_time = round(float(row["event_time"]), 3)
            number = float(row[field])
        except (KeyError, TypeError, ValueError):
            continue
        value: int | float = int(number) if number.is_integer() else round(number, 6)
        if value == last_value:
            continue
        transitions.append({"event_time": event_time, "value": value})
        last_value = value
    return transitions


def distance_availability_transitions(
    rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    last_state: str | None = None
    for row in rows:
        try:
            event_time = round(float(row["event_time"]), 3)
            distance = float(row["Dis_FV"])
        except (KeyError, TypeError, ValueError):
            continue
        state = "有效测距" if distance >= 0 else "无有效测距"
        if state == last_state:
            continue
        transitions.append({"event_time": event_time, "state": state})
        last_state = state
    return transitions


def nearest_positive_lane_index(
    rows: list[dict[str, str]], event_time: float
) -> int | None:
    best: tuple[float, int] | None = None
    for row in rows:
        try:
            row_time = float(row["event_time"])
            lane_index = int(float(row["EgoLaneIndex"]))
        except (KeyError, TypeError, ValueError):
            continue
        if lane_index <= 0:
            continue
        difference = abs(row_time - event_time)
        if best is None or difference < best[0]:
            best = (difference, lane_index)
    return best[1] if best is not None else None


def lane_index_sequence_near(
    rows: list[dict[str, str]], event_time: float
) -> list[int]:
    values: list[int] = []
    for row in rows:
        try:
            row_time = float(row["event_time"])
            lane_index = int(float(row["EgoLaneIndex"]))
        except (KeyError, TypeError, ValueError):
            continue
        if (
            lane_index <= 0
            or abs(row_time - event_time) > DISTANCE_JUMP_LANE_CONTEXT_WINDOW_S
        ):
            continue
        if not values or values[-1] != lane_index:
            values.append(lane_index)
    return values


def detect_distance_jumps(
    rows: list[dict[str, str]],
    map_rows: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for before_row, after_row in zip(rows, rows[1:]):
        try:
            before_time = float(before_row["event_time"])
            after_time = float(after_row["event_time"])
            before_distance = float(before_row["Dis_FV"])
            after_distance = float(after_row["Dis_FV"])
        except (KeyError, TypeError, ValueError):
            continue
        time_gap = after_time - before_time
        if (
            before_distance < 0
            or after_distance < 0
            or time_gap <= 0
            or time_gap > DISTANCE_JUMP_MAX_TIME_GAP_S
        ):
            continue
        delta = after_distance - before_distance
        absolute_delta = abs(delta)
        relative_delta = absolute_delta / max(abs(before_distance), 1.0)
        if absolute_delta < DISTANCE_JUMP_MIN_ABS_M:
            continue
        if (
            absolute_delta < DISTANCE_JUMP_STRONG_ABS_M
            and relative_delta < DISTANCE_JUMP_MIN_RELATIVE
        ):
            continue
        lane_before = (
            nearest_positive_lane_index(map_rows, before_time)
            if map_rows
            else None
        )
        lane_after = (
            nearest_positive_lane_index(map_rows, after_time)
            if map_rows
            else None
        )
        lane_sequence_near_jump = (
            lane_index_sequence_near(
                map_rows, (before_time + after_time) / 2
            )
            if map_rows
            else []
        )
        lane_changed = len(set(lane_sequence_near_jump)) > 1
        if lane_changed:
            priority_hypothesis = "EgoLaneIndex发生变化，优先核查自车换道"
        elif lane_before is not None and lane_after is not None:
            priority_hypothesis = (
                "EgoLaneIndex未变化，距离突增时优先核查原前车cut-out"
                if delta > 0
                else "EgoLaneIndex未变化，距离突减时优先核查邻车cut-in"
            )
        else:
            priority_hypothesis = "车道编号不足，必须完全依赖视觉时序核查"
        candidates.append(
            {
                "from_event_time": round(before_time, 3),
                "to_event_time": round(after_time, 3),
                "time_gap_s": round(time_gap, 3),
                "before_m": round(before_distance, 3),
                "after_m": round(after_distance, 3),
                "delta_m": round(delta, 3),
                "relative_change": round(delta / max(abs(before_distance), 1.0), 3),
                "direction": "突然增大" if delta > 0 else "突然减小",
                "ego_lane_index_before": lane_before,
                "ego_lane_index_after": lane_after,
                "ego_lane_index_sequence_near_jump": lane_sequence_near_jump,
                "ego_lane_index_changed": lane_changed,
                "priority_hypothesis": priority_hypothesis,
                "interpretation_hint": (
                    "可能是自车换道或原前车cut-out，必须用视觉时序区分"
                    if delta > 0
                    else "可能是自车换道或邻车cut-in，必须用视觉时序区分"
                ),
            }
        )
    strongest = sorted(
        candidates, key=lambda item: abs(item["delta_m"]), reverse=True
    )[:MAX_DISTANCE_JUMP_CANDIDATES]
    return sorted(strongest, key=lambda item: item["from_event_time"])


def decoded_names(values: list[int | float], mapping: dict[str, str]) -> list[str]:
    return [mapping.get(str(value), f"unknown_{value}") for value in values]


def build_applicability_hint(
    evidence_rows: list[dict[str, str]],
) -> dict[str, Any]:
    valid_rows: list[tuple[int, int]] = []
    for row in evidence_rows:
        try:
            road_type = int(float(row["Road_type"]))
            lane_type = int(float(row["Lane_type"]))
        except (KeyError, TypeError, ValueError):
            continue
        valid_rows.append((road_type, lane_type))
    if not valid_rows:
        status = "辅助信息不足"
    else:
        mainline_flags = [
            road_type in {1, 34} and lane_type == 1
            for road_type, lane_type in valid_rows
        ]
        if all(mainline_flags):
            status = "可能处于高速公路或城市快速路主路"
        elif any(mainline_flags):
            status = "可能在主路与非主路车道之间转换"
        else:
            status = "可能不处于法规所指主路"
    return {
        "status": status,
        "is_auxiliary_hint": True,
        "instruction": "必须结合图像道路结构核验，不得直接作为最终适用性结论",
    }


def build_follow_auxiliary_summary(
    event_dir: Path, record: dict[str, Any], sample_times: list[float]
) -> dict[str, Any]:
    evidence_path = find_one(event_dir, "*_EvidenceChain.csv")
    ego_path = find_one(event_dir, "*_EgoInfo.csv")
    map_path = find_one(event_dir, "*_MapInfo.csv")

    all_evidence_rows = read_csv_rows(evidence_path)
    all_map_rows = read_csv_rows(map_path)
    evidence_rows_raw = nearest_csv_rows(evidence_path, sample_times)
    ego_rows_raw = nearest_csv_rows(ego_path, sample_times)
    map_rows_raw = nearest_csv_rows(map_path, sample_times)

    evidence_columns = [
        "event_time",
        "Ego_velocity",
        "Road_type",
        "Lane_type",
        "Congestion",
        "Dis_FV",
        "Thres_Dis_FV",
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
    evidence_rows = [
        select_columns(row, evidence_columns) for row in evidence_rows_raw
    ]
    map_rows = [select_columns(row, map_columns) for row in map_rows_raw]
    ego_rows = [
        select_columns(row, ["event_time", "Ego_velocity"])
        for row in ego_rows_raw
    ]

    speed_values = [
        float(row["Ego_velocity"])
        for row in all_evidence_rows
        if row.get("Ego_velocity", "").strip()
    ]
    all_distance_values = [
        float(row["Dis_FV"])
        for row in all_evidence_rows
        if row.get("Dis_FV", "").strip()
    ]
    valid_distance_values = [
        distance for distance in all_distance_values if distance >= 0
    ]
    road_type_codes = numeric_sequence(all_evidence_rows, "Road_type")
    lane_type_codes = numeric_sequence(all_evidence_rows, "Lane_type")
    congestion_values = numeric_sequence(all_evidence_rows, "Congestion")
    lane_counts = [
        value
        for value in numeric_sequence(all_map_rows, "LaneNumSameDirection")
        if value > 0
    ]
    ego_lane_indexes = [
        value
        for value in numeric_sequence(all_map_rows, "EgoLaneIndex")
        if value > 0
    ]
    low_speed_screen = bool(speed_values) and max(speed_values) < (
        LOW_SPEED_CONGESTION_THRESHOLD_MPS
    )
    result = record.get("Result") or record.get("result") or {}
    evidence = record.get("Evidence") or record.get("evidence") or {}

    return {
        "warning": (
            "以下信息均为待核验辅助证据，不是视觉事实；未提供或读取ObjInfo，"
            "trigger/com合规字段也未纳入摘要。"
        ),
        "record": {
            "Location": record.get("Location"),
            "Date": record.get("Date"),
            "Time": record.get("Time"),
            "Article_ID": (record.get("Article") or {}).get("ID"),
            "EventAnchor": record.get("EventAnchor"),
            "Evidence_scene_fields": {
                "Minimum_front_vehicle_distance_m": evidence.get(
                    "Minimum_front_vehicle_distance_m"
                ),
                "Required_following_distance_at_minimum_m": evidence.get(
                    "Required_following_distance_at_minimum_m"
                ),
                "Ego_speed_at_minimum_distance_kph": evidence.get(
                    "Ego_speed_at_minimum_distance_kph"
                ),
                "Time_of_minimum_distance_s": evidence.get(
                    "Time_of_minimum_distance_s"
                ),
                "Required_following_distance_m": evidence.get(
                    "Required_following_distance_m"
                ),
                "Ego_speed_kph": evidence.get("Ego_speed_kph"),
                "Road_types": evidence.get("Road_types"),
                "Lane_types": evidence.get("Lane_types"),
                "Congestion": evidence.get("Congestion"),
            },
            "original_Scenario_description": result.get("Scenario_description"),
            "original_Driving_suggestion": result.get("Driving_suggestion"),
        },
        "csv_aggregate": {
            "full_event_speed_range_mps": [
                round(min(speed_values), 3),
                round(max(speed_values), 3),
            ]
            if speed_values
            else None,
            "full_event_speed_range_kph": [
                round(min(speed_values) * 3.6, 1),
                round(max(speed_values) * 3.6, 1),
            ]
            if speed_values
            else None,
            "all_event_speed_below_16_5_mps": low_speed_screen,
            "Road_type_codes": road_type_codes,
            "Road_type_sequence": decoded_names(road_type_codes, ROAD_TYPES),
            "Lane_type_codes": lane_type_codes,
            "Lane_type_sequence": decoded_names(lane_type_codes, LANE_TYPES),
            "rule_applicability_hint": build_applicability_hint(
                all_evidence_rows
            ),
            "Congestion_sequence": congestion_values,
            "csv_claims_congestion": any(value != 0 for value in congestion_values),
            "valid_Dis_FV_range_m": [
                round(min(valid_distance_values), 3),
                round(max(valid_distance_values), 3),
            ]
            if valid_distance_values
            else None,
            "first_valid_Dis_FV_m": (
                round(valid_distance_values[0], 3)
                if valid_distance_values
                else None
            ),
            "last_valid_Dis_FV_m": (
                round(valid_distance_values[-1], 3)
                if valid_distance_values
                else None
            ),
            "valid_distance_sample_ratio": (
                round(len(valid_distance_values) / len(all_distance_values), 3)
                if all_distance_values
                else None
            ),
            "Dis_FV_availability_transitions": distance_availability_transitions(
                all_evidence_rows
            ),
            "Dis_FV_jump_candidates": detect_distance_jumps(
                all_evidence_rows, all_map_rows
            ),
            "Thres_Dis_FV_sequence_m": numeric_sequence(
                all_evidence_rows, "Thres_Dis_FV"
            ),
            "main_lane_count_sequence": lane_counts,
            "main_lane_count_transitions": field_transitions(
                all_map_rows, "LaneNumSameDirection"
            ),
            "ego_lane_index_sequence": ego_lane_indexes,
            "ego_lane_index_transitions": field_transitions(
                all_map_rows, "EgoLaneIndex"
            ),
        },
        "EvidenceChain_samples": evidence_rows,
        "EgoInfo_samples": ego_rows,
        "MapInfo_samples": map_rows,
    }


def integrate_following_situation(description: str, situation: str) -> str:
    description = re.sub(
        r"^跟车(?:情况|状态)：[^。]+。", "", description.strip()
    ).strip()
    clauses = {
        "不存在同车道前车": (
            ("不存在同车道前车", "未见明确的同车道前车"),
            "事件期间未见明确的同车道前车",
        ),
        "始终跟随同车道前车": (
            ("始终跟随", "持续跟随"),
            "事件期间自车持续跟随同车道前车行驶",
        ),
        "同车道前车驶离或变道（cut-out）": (
            (
                "cut-out",
                "前车驶离",
                "前车变道离开",
                "原跟车关系结束",
                "驶离后",
            ),
            "原同车道前车随后驶离自车车道，跟车目标发生cut-out",
        ),
        "相邻车道车辆切入自车道（cut-in）": (
            (
                "cut-in",
                "车辆切入自车道",
                "车辆切入本车道",
                "切入自车所在",
                "切入自车车道",
                "成为自车新的同车道前车",
            ),
            "相邻车道车辆随后切入自车道，自车形成新的跟车关系",
        ),
        "自车变道后跟随新的同车道前车": (
            ("变道后跟随", "变道后在目标车道"),
            "自车变道后在目标车道跟随新的同车道前车",
        ),
        "自车变道后原跟车关系结束": (
            ("变道后原跟车关系结束", "变道后暂无明确前车"),
            "自车变道后原跟车关系结束，目标车道暂无明确同车道前车",
        ),
        "跟车关系发生复合变化": (
            ("跟车关系发生", "多次变化", "复合变化"),
            "事件期间同车道前车及跟车关系发生多次变化",
        ),
        "无法确认": (
            ("跟车关系无法", "无法确认同车道前车"),
            "受画面距离或遮挡影响，事件中的同车道前车关系无法稳定确认",
        ),
    }
    markers, clause = clauses[situation]
    if any(marker in description for marker in markers):
        return description
    return description.rstrip("。") + "。" + clause + "。"


LICENSE_PLATE_PATTERN = re.compile(
    r"(?<![A-Z0-9\u4e00-\u9fff])"
    r"[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领]"
    r"[A-Z](?:\s*[·•]?\s*[A-Z0-9]){5,6}"
    r"(?![A-Z0-9])",
    re.IGNORECASE,
)
FRAME_REFERENCE_PATTERN = re.compile(
    r"(?:帧\s*\d+|第\s*\d+\s*帧)"
    r"(?:\s*[-—~～至到]\s*(?:帧\s*\d+|\d+\s*帧?))?"
    r"(?:\s*[（(]\s*\d+(?:\.\d+)?\s*(?:s|秒)\s*[）)])?"
    r"\s*[：:]?"
)
TIMESTAMP_PAREN_PATTERN = re.compile(
    r"[（(]\s*(?:约|大约)?\s*\d+(?:\.\d+)?"
    r"(?:\s*[-—~～至到]\s*\d+(?:\.\d+)?)?"
    r"\s*(?:s|秒)(?:起|时|后|处|左右|附近)?\s*[）)]"
)


def remove_license_plates(text: str) -> str:
    return LICENSE_PLATE_PATTERN.sub("", text)


def sanitize_scenario_description(text: str) -> str:
    text = remove_license_plates(text)
    text = FRAME_REFERENCE_PATTERN.sub("", text)
    text = TIMESTAMP_PAREN_PATTERN.sub("", text)
    text = re.sub(r"[（(]\s*[）)]", "", text)
    text = re.sub(r"\s+([，。；：])", r"\1", text)
    text = re.sub(r"，{2,}", "，", text)
    text = re.sub(r"。{2,}", "。", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" ，,；;：:")


def normalize_follow_model_result(
    value: dict[str, Any], auxiliary: dict[str, Any]
) -> dict[str, Any]:
    situation = str(value.get("following_situation", "")).strip()
    if situation not in VALID_FOLLOWING_SITUATIONS:
        raise ReviewError(f"模型返回无效跟车情况: {situation!r}")
    description = str(value.get("Scenario_description_VLM", "")).strip()
    suggestion = str(value.get("Driving_suggestion_VLM", "")).strip()
    if not description or not suggestion:
        raise ReviewError("模型未返回完整的场景描述或驾驶建议")
    aggregate = auxiliary["csv_aggregate"]

    applicability = value.get("road_applicability_review")
    if not isinstance(applicability, dict):
        raise ReviewError("模型未返回road_applicability_review")
    applicability_status = str(applicability.get("status", "")).strip()
    if applicability_status not in VALID_APPLICABILITY:
        raise ReviewError(f"模型返回无效道路适用性: {applicability_status!r}")

    lane_review = value.get("lane_review")
    if not isinstance(lane_review, dict):
        raise ReviewError("模型未返回lane_review")
    following_review = value.get("following_review")
    if not isinstance(following_review, dict):
        raise ReviewError("模型未返回following_review")
    initial_front = following_review.get("initial_same_lane_front_vehicle")
    final_front = following_review.get("final_same_lane_front_vehicle")
    if not isinstance(initial_front, dict) or not isinstance(final_front, dict):
        raise ReviewError("模型未返回完整的前车时序")
    if situation == "不存在同车道前车" and (
        initial_front.get("exists") is True or final_front.get("exists") is True
    ):
        raise ReviewError("跟车情况与前车时序矛盾")
    if situation == "始终跟随同车道前车" and (
        initial_front.get("exists") is not True
        or final_front.get("exists") is not True
    ):
        raise ReviewError("持续跟车结论缺少首末同车道前车")
    if (
        situation == "同车道前车驶离或变道（cut-out）"
        and following_review.get("cut_out_seen") is not True
    ):
        raise ReviewError("cut-out结论缺少视觉时序确认")
    if (
        situation == "相邻车道车辆切入自车道（cut-in）"
        and following_review.get("cut_in_seen") is not True
    ):
        raise ReviewError("cut-in结论缺少视觉时序确认")
    if (
        situation
        in {
            "自车变道后跟随新的同车道前车",
            "自车变道后原跟车关系结束",
        }
        and following_review.get("ego_lane_change_affects_following") is not True
    ):
        raise ReviewError("自车变道后的跟车结论缺少车道变化确认")
    if (
        situation == "自车变道后跟随新的同车道前车"
        and final_front.get("exists") is not True
    ):
        raise ReviewError("自车变道后新跟车结论缺少目标车道前车")
    if situation == "自车变道后原跟车关系结束" and (
        initial_front.get("exists") is not True
        or final_front.get("exists") is True
    ):
        raise ReviewError("自车变道结束原跟车关系的首末前车状态矛盾")

    jump_review = value.get("distance_jump_review")
    if not isinstance(jump_review, dict):
        raise ReviewError("模型未返回distance_jump_review")
    jump_candidates = aggregate.get("Dis_FV_jump_candidates", [])
    jump_review["has_significant_jump"] = bool(jump_candidates)
    jump_review["csv_jump_candidates"] = jump_candidates
    jump_interpretation = str(jump_review.get("interpretation", "")).strip()
    if jump_interpretation not in VALID_DISTANCE_JUMP_INTERPRETATIONS:
        raise ReviewError(f"模型返回无效Dis_FV跳变解释: {jump_interpretation!r}")
    if jump_candidates and jump_interpretation == "无显著跳变":
        raise ReviewError("辅助信息存在显著Dis_FV跳变，但模型未审查")
    if jump_candidates and situation == "始终跟随同车道前车":
        raise ReviewError(
            "存在显著Dis_FV跳变，不能判定自车始终跟随同一前车"
        )
    if jump_candidates and (
        not str(jump_review.get("before_target_lane_basis", "")).strip()
        or not str(jump_review.get("after_target_lane_basis", "")).strip()
    ):
        raise ReviewError("显著Dis_FV跳变缺少跳变前后同车道目标依据")
    if jump_interpretation == "邻车cut-in" and (
        following_review.get("cut_in_seen") is not True
        or situation
        not in {
            "相邻车道车辆切入自车道（cut-in）",
            "跟车关系发生复合变化",
        }
    ):
        raise ReviewError("Dis_FV跳变解释为邻车cut-in，但跟车时序结论不一致")
    if jump_interpretation == "前车cut-out" and (
        following_review.get("cut_out_seen") is not True
        or situation
        not in {
            "同车道前车驶离或变道（cut-out）",
            "跟车关系发生复合变化",
        }
    ):
        raise ReviewError("Dis_FV跳变解释为前车cut-out，但跟车时序结论不一致")
    if jump_interpretation == "自车换道" and (
        following_review.get("ego_lane_change_affects_following") is not True
        or situation
        not in {
            "自车变道后跟随新的同车道前车",
            "自车变道后原跟车关系结束",
            "跟车关系发生复合变化",
        }
    ):
        raise ReviewError("Dis_FV跳变解释为自车换道，但跟车时序结论不一致")

    description = sanitize_scenario_description(description)
    value["Scenario_description_VLM"] = integrate_following_situation(
        description, situation
    )
    suggestion = remove_license_plates(suggestion)

    special_review = value.get("special_case_review")
    if not isinstance(special_review, dict):
        raise ReviewError("模型未返回special_case_review")
    low_speed_screen = aggregate["all_event_speed_below_16_5_mps"]
    special_review["low_speed_congestion_screen_triggered"] = low_speed_screen
    special_review["csv_congestion_hint"] = aggregate["Congestion_sequence"]
    congestion_assessment = str(
        special_review.get("congestion_assessment", "")
    ).strip()
    if congestion_assessment not in VALID_CONGESTION_ASSESSMENTS:
        raise ReviewError(f"模型返回无效拥堵判断: {congestion_assessment!r}")
    other_cases = special_review.get("visually_confirmed_other_cases")
    if not isinstance(other_cases, list):
        other_cases = []
        special_review["visually_confirmed_other_cases"] = other_cases
    if congestion_assessment == "拥堵":
        special_review["following_distance_exemption_applies"] = True
        special_review["exemption_reason"] = "视觉确认车流拥堵"
    elif (
        special_review.get("following_distance_exemption_applies") is True
        and not other_cases
    ):
        special_review["following_distance_exemption_applies"] = False
        special_review["exemption_reason"] = "未确认需要豁免的特殊路况"

    exemption_applies = (
        special_review.get("following_distance_exemption_applies") is True
    )
    fixed_distance_phrases = ("50米", "100米", "50 米", "100 米")
    if congestion_assessment == "拥堵" and any(
        phrase in suggestion for phrase in fixed_distance_phrases
    ):
        suggestion = (
            "当前车流拥堵，应低速平顺跟随前车，避免频繁加减速并保留足够制动余量，"
            "待拥堵缓解后再逐步恢复正常跟车间距。"
        )
    elif applicability_status == "不适用" and any(
        phrase in suggestion for phrase in fixed_distance_phrases
    ):
        suggestion = (
            "当前道路或车道不属于固定跟车距离条款所针对的主路范围，"
            "仍应根据前车动态保持足以安全制动的距离。"
        )
    elif situation == "不存在同车道前车" and any(
        phrase in suggestion for phrase in ("减速拉开", "拉开与前车", "增大车距")
    ):
        suggestion = (
            "当前未见明确同车道前车，可保持平稳行驶并持续观察远端及相邻车道车辆，"
            "为可能的车辆切入预留反应空间。"
        )
    if exemption_applies and not suggestion:
        raise ReviewError("豁免场景缺少驾驶建议")
    value["Driving_suggestion_VLM"] = suggestion

    consistency = value.get("auxiliary_consistency")
    if not isinstance(consistency, dict):
        consistency = {}
        value["auxiliary_consistency"] = consistency
    doubtful_points = consistency.get("doubtful_points")
    if not isinstance(doubtful_points, list):
        doubtful_points = []
    if str(consistency.get("status", "")).strip() != "明显不一致":
        unconfirmed = consistency.get("unconfirmed_points")
        if not isinstance(unconfirmed, list):
            unconfirmed = []
        unconfirmed.extend(doubtful_points)
        consistency["unconfirmed_points"] = unconfirmed
        consistency["doubtful_points"] = []
        value["requires_manual_review"] = False
        value["manual_review_reason"] = ""
    else:
        value["requires_manual_review"] = bool(doubtful_points)
        if value["requires_manual_review"] and not str(
            value.get("manual_review_reason", "")
        ).strip():
            value["manual_review_reason"] = "视觉结果与辅助跟车证据存在明显冲突"
    if jump_candidates and jump_interpretation in {
        "跟车目标切换但类型无法确认",
        "测距异常或视觉不足",
    }:
        value["requires_manual_review"] = True
        if not str(value.get("manual_review_reason", "")).strip():
            value["manual_review_reason"] = (
                "存在显著Dis_FV跳变，但视觉不足以确认跟车目标切换类型"
            )
    return value


def collect_follow_doubtful_reviews(output_root: Path) -> list[dict[str, Any]]:
    doubtful: list[dict[str, Any]] = []
    reviews_root = output_root / "reviews"
    if not reviews_root.exists():
        return doubtful
    for review_path in sorted(reviews_root.rglob("event_*.json")):
        try:
            review = read_json(review_path)
        except (OSError, json.JSONDecodeError):
            continue
        record_path = review.get("record_path")
        if not record_path or not Path(record_path).is_file():
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
                "record_path": record_path,
                "following_situation": model_result.get("following_situation"),
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


def review_follow_event(
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
    video_path = find_one(event_dir / "video" / "mp4", "video_30_event_*.mp4")
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

    frames = extract_frames(video_path, output_root / "frames" / relative_dir)
    sample_times = [frame["time"] for frame in frames]
    auxiliary = build_follow_auxiliary_summary(event_dir, record, sample_times)
    if args.dry_run:
        aggregate = auxiliary["csv_aggregate"]
        return {
            "segment": segment.name,
            "event": event_dir.name,
            "status": "dry_run",
            "record_path": str(record_path),
            "frame_times": sample_times,
            "speed_range_mps": aggregate["full_event_speed_range_mps"],
            "low_speed_congestion_screen": aggregate[
                "all_event_speed_below_16_5_mps"
            ],
            "csv_congestion_sequence": aggregate["Congestion_sequence"],
            "road_type_sequence": aggregate["Road_type_sequence"],
            "lane_type_sequence": aggregate["Lane_type_sequence"],
            "ego_lane_index_sequence": aggregate["ego_lane_index_sequence"],
            "distance_availability_transitions": aggregate[
                "Dis_FV_availability_transitions"
            ],
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
    )
    model_result = normalize_follow_model_result(model_result, auxiliary)

    review_path = output_root / "reviews" / relative_dir.with_suffix(".json")
    atomic_write_json(
        review_path,
        {
            "segment": segment.name,
            "event": event_dir.name,
            "record_path": str(record_path),
            "video_path": str(video_path),
            "model": args.model,
            "frame_times": sample_times,
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
        "following_situation": model_result["following_situation"],
        "requires_manual_review": model_result["requires_manual_review"],
        "manual_review_reason": model_result.get("manual_review_reason", ""),
        "road_applicability_review": model_result.get(
            "road_applicability_review", {}
        ),
        "special_case_review": model_result.get("special_case_review", {}),
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
        default=Path(__file__).resolve().parent / "outputs_follow_distance",
    )
    parser.add_argument("--api-key-source", type=Path)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--request-timeout", type=int, default=180)
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
            requested = {value.replace("\\", "/").strip("/") for value in args.only}
            events = [
                (segment, event_dir)
                for segment, event_dir in events
                if f"{segment.name}/{event_dir.name}" in requested
            ]
            found = {f"{segment.name}/{event_dir.name}" for segment, event_dir in events}
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
        f"发现 {len(events)} 个跟车距离事件；模型={args.model}；"
        f"模式={'dry-run' if args.dry_run else 'API审查并写入'}；"
        f"workers={args.workers}；resume={args.resume}"
    )

    def process_event(segment: Path, event_dir: Path) -> dict[str, Any]:
        try:
            return review_follow_event(
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
            stream = sys.stderr if event_result["status"] == "failed" else sys.stdout
            detail = event_result.get("error", event_result["status"])
            print(f"  {detail}", file=stream)
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
            for completed, future in enumerate(as_completed(future_events), start=1):
                index, segment, event_dir = future_events[future]
                event_result = future.result()
                results[index] = event_result
                stream = (
                    sys.stderr if event_result["status"] == "failed" else sys.stdout
                )
                detail = event_result.get("error", event_result["status"])
                print(
                    f"[{completed}/{len(events)}] "
                    f"{segment.name}/{event_dir.name}: {detail}",
                    file=stream,
                )

    completed_results = [result for result in results if result is not None]
    doubtful = collect_follow_doubtful_reviews(args.output_dir)
    summary = {
        "dataset_root": str(args.dataset_root),
        "model": args.model,
        "dry_run": args.dry_run,
        "workers": args.workers,
        "resume": args.resume,
        "total": len(completed_results),
        "updated": sum(
            result["status"] == "updated" for result in completed_results
        ),
        "skipped_existing": sum(
            result["status"] == "skipped_existing"
            for result in completed_results
        ),
        "skipped_completed": sum(
            result["status"] == "skipped_completed"
            for result in completed_results
        ),
        "dry_run_ok": sum(
            result["status"] == "dry_run" for result in completed_results
        ),
        "failed": sum(
            result["status"] == "failed" for result in completed_results
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
