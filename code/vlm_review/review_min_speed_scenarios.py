#!/usr/bin/env python
"""用前向视频和辅助CSV审查最低限速事件场景。"""

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

from min_speed_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from review_scenarios import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    ReviewError,
    atomic_write_json,
    call_model,
    collect_doubtful_reviews,
    discover_events,
    extract_frames,
    find_one,
    load_api_key,
    nearest_csv_rows,
    read_json,
    result_container,
    select_columns,
    unique_values,
)


DEFAULT_DATASET_ROOT = Path(os.environ.get("TLCD_DATASET_ROOT", "."))
VALID_MIN_SPEED_SOURCES = {
    "经过龙门架车道级最低限速标志",
    "经过路侧路段级最低限速标志",
    "位于地图限速管理区域",
    "位于无明确最低限速区域",
}
SPECIAL_CASES = {
    0: "未记录特殊路况",
    1: "拥堵",
    2: "施工",
    3: "上坡低速行驶",
    4: "下坡低速行驶",
    5: "弯道",
}
LOW_SPEED_CONGESTION_THRESHOLD_MPS = 16.5


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def numeric_sequence(rows: list[dict[str, str]], field: str) -> list[int | float]:
    values: list[int | float] = []
    for row in rows:
        raw_value = row.get(field, "").strip()
        if not raw_value:
            continue
        try:
            number = float(raw_value)
        except ValueError:
            continue
        value: int | float = int(number) if number.is_integer() else round(number, 6)
        if value not in values:
            values.append(value)
    return values


def field_transitions(rows: list[dict[str, str]], field: str) -> list[dict[str, Any]]:
    transitions = []
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


def build_min_auxiliary_summary(
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
        "Road_type",
        "Lane_type",
        "Ego_velocity",
        "IsMinSpdsignArea",
        "LaneNumSameDirection",
        "mainLaneNum",
        "EgoLaneIndex",
        "Special_case",
        "Thres_MinSpdlim",
        "LaneMinSpdlim_1",
        "LaneMinSpdlim_2",
        "LaneMinSpdlim_3",
        "LaneMinSpdlim_4",
        "LaneMinSpdlim_5",
    ]
    map_columns = [
        "event_time",
        "Road_type",
        "Road_Curve",
        "Road_Slope",
        "Lane_type_CurrentLane",
        "LaneNumSameDirection",
        "EgoLaneIndex",
        "LaneMaxSpdlim_1",
        "LaneMaxSpdlim_2",
        "LaneMaxSpdlim_3",
        "LaneMaxSpdlim_4",
        "LaneMaxSpdlim_5",
        "LaneMinSpdlim_1",
        "LaneMinSpdlim_2",
        "LaneMinSpdlim_3",
        "LaneMinSpdlim_4",
        "LaneMinSpdlim_5",
    ]
    evidence_rows = [
        select_columns(row, evidence_columns) for row in evidence_rows_raw
    ]
    map_rows = [select_columns(row, map_columns) for row in map_rows_raw]
    ego_rows = [
        select_columns(row, ["event_time", "Ego_velocity"]) for row in ego_rows_raw
    ]

    speed_values = [
        float(row["Ego_velocity"])
        for row in all_evidence_rows
        if row.get("Ego_velocity", "").strip()
    ]
    special_codes = [
        int(value)
        for value in numeric_sequence(all_evidence_rows, "Special_case")
    ]
    result = record.get("Result") or record.get("result") or {}
    evidence = record.get("Evidence") or record.get("evidence") or {}
    low_speed_screen = bool(speed_values) and max(speed_values) < (
        LOW_SPEED_CONGESTION_THRESHOLD_MPS
    )
    valid_lane_max_values: list[int | float] = []
    for row in all_map_rows:
        try:
            lane_count = int(float(row.get("LaneNumSameDirection", "0") or 0))
        except ValueError:
            lane_count = 0
        lane_indexes = range(1, lane_count + 1) if lane_count > 0 else range(1, 6)
        for lane_index in lane_indexes:
            try:
                value = float(row.get(f"LaneMaxSpdlim_{lane_index}", "0") or 0)
            except ValueError:
                continue
            if value <= 0:
                continue
            normalized: int | float = (
                int(value) if value.is_integer() else round(value, 6)
            )
            if normalized not in valid_lane_max_values:
                valid_lane_max_values.append(normalized)
    all_valid_max_are_120 = bool(valid_lane_max_values) and all(
        value == 120 for value in valid_lane_max_values
    )
    fallback_source = (
        "位于无明确最低限速区域"
        if all_valid_max_are_120
        else "位于地图限速管理区域"
    )

    return {
        "warning": "除标明可信的字段外，以下信息均为待核验辅助证据，不是视觉事实。",
        "trusted_field_rules": {
            "Special_case_nonzero": "真值，不得被视觉否定",
            "mainLaneNum": "默认真值，除非存在清晰直接反证",
            "EgoLaneIndex": "默认真值，除非存在清晰直接反证",
        },
        "special_case_enum": {
            str(code): description for code, description in SPECIAL_CASES.items()
        },
        "record": {
            "Location": record.get("Location"),
            "Date": record.get("Date"),
            "Time": record.get("Time"),
            "Article_ID": (record.get("Article") or {}).get("ID"),
            "EventAnchor": record.get("EventAnchor"),
            "Evidence_scene_fields": {
                "Ego_speed_kph": evidence.get("Ego_speed_kph"),
                "Applicable_min_speed_limit_kph": evidence.get(
                    "Applicable_min_speed_limit_kph"
                ),
                "Road_types": evidence.get("Road_types"),
                "Lane_types": evidence.get("Lane_types"),
                "Same_direction_lane_count": evidence.get(
                    "Same_direction_lane_count"
                ),
                "Ego_lane_index_from_left": evidence.get(
                    "Ego_lane_index_from_left"
                ),
                "Special_case": evidence.get("Special_case"),
                "Inside_speed_limit_sign_area": evidence.get(
                    "Inside_speed_limit_sign_area"
                ),
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
            "Road_type_sequence": unique_values(evidence_rows, "Road_type_name"),
            "Lane_type_sequence": unique_values(evidence_rows, "Lane_type_name"),
            "Special_case_sequence": special_codes,
            "Special_case_meanings": [
                SPECIAL_CASES.get(code, f"未知代码{code}") for code in special_codes
            ],
            "mainLaneNum_sequence": numeric_sequence(
                all_evidence_rows, "mainLaneNum"
            ),
            "mainLaneNum_transitions": field_transitions(
                all_evidence_rows, "mainLaneNum"
            ),
            "EgoLaneIndex_sequence": numeric_sequence(
                all_evidence_rows, "EgoLaneIndex"
            ),
            "EgoLaneIndex_transitions": field_transitions(
                all_evidence_rows, "EgoLaneIndex"
            ),
            "IsMinSpdsignArea_transitions": field_transitions(
                all_evidence_rows, "IsMinSpdsignArea"
            ),
            "Thres_MinSpdlim_sequence_kph": numeric_sequence(
                all_evidence_rows, "Thres_MinSpdlim"
            ),
            "Valid_LaneMaxSpdlim_values_kph": valid_lane_max_values,
            "all_valid_lane_max_speed_limits_are_120": all_valid_max_are_120,
            "fallback_source_if_no_applicable_sign": {
                "source": fallback_source,
                "condition": "仅在视觉时序未确认自车实际驶过适用实体最低限速标志时使用",
                "is_auxiliary_claim": False,
            },
        },
        "EvidenceChain_samples": evidence_rows,
        "EgoInfo_samples": ego_rows,
        "MapInfo_samples": map_rows,
    }


def integrate_min_speed_source(description: str, source: str) -> str:
    description = re.sub(
        r"^(?:最低)?限速来源：[^。]+。", "", description.strip()
    ).strip()
    source_clauses = {
        "经过龙门架车道级最低限速标志": (
            "龙门架车道级最低限速标志",
            "自车经过适用于当前车道的龙门架车道级最低限速标志",
        ),
        "经过路侧路段级最低限速标志": (
            "路侧路段级最低限速标志",
            "自车经过适用于当前道路的路侧路段级最低限速标志",
        ),
        "位于地图限速管理区域": (
            "地图限速管理区域",
            "该路段为地图限速管理区域",
        ),
        "位于无明确最低限速区域": (
            "无明确最低限速",
            "该路段无明确最低限速",
        ),
    }
    marker, clause = source_clauses[source]
    if marker in description:
        return description
    first_sentence, separator, remainder = description.partition("。")
    integrated = first_sentence.rstrip("，,") + "，" + clause + "。"
    if separator and remainder:
        integrated += remainder
    return integrated


def fallback_min_speed_source(auxiliary: dict[str, Any]) -> str:
    all_valid_max_are_120 = auxiliary["csv_aggregate"][
        "all_valid_lane_max_speed_limits_are_120"
    ]
    return (
        "位于无明确最低限速区域"
        if all_valid_max_are_120
        else "位于地图限速管理区域"
    )


def normalize_min_model_result(
    value: dict[str, Any], auxiliary: dict[str, Any]
) -> dict[str, Any]:
    source = str(value.get("speed_limit_source", "")).strip()
    if source not in VALID_MIN_SPEED_SOURCES:
        raise ReviewError(f"模型返回无效最低限速来源: {source!r}")
    description = str(value.get("Scenario_description_VLM", "")).strip()
    suggestion = str(value.get("Driving_suggestion_VLM", "")).strip()
    if not description or not suggestion:
        raise ReviewError("模型未返回完整的场景描述或驾驶建议")
    lane_review = value.get("lane_review")
    if not isinstance(lane_review, dict):
        raise ReviewError("模型未返回lane_review")
    sign_review = value.get("min_speed_sign_review")
    if not isinstance(sign_review, dict) or not isinstance(
        sign_review.get("visible_signs"), list
    ):
        raise ReviewError("模型未返回完整的min_speed_sign_review")
    ego_passed_sign = sign_review.get("ego_passed_sign") is True
    sign_source = source in {
        "经过龙门架车道级最低限速标志",
        "经过路侧路段级最低限速标志",
    }
    if sign_source and (
        sign_review.get("applicable_sign_seen") is not True or not ego_passed_sign
    ):
        raise ReviewError("模型选择了实体最低限速标志来源，但未确认自车实际驶过")
    if (
        sign_review.get("applicable_sign_seen") is True
        and ego_passed_sign
        and not sign_source
    ):
        raise ReviewError("模型确认自车驶过适用最低限速标志，但未选择实体标志来源")
    if sign_review.get("applicable_sign_seen") is not True or not ego_passed_sign:
        source = fallback_min_speed_source(auxiliary)
    value["speed_limit_source"] = source
    value["Scenario_description_VLM"] = integrate_min_speed_source(
        description, source
    )

    special_review = value.get("special_case_review")
    if not isinstance(special_review, dict):
        raise ReviewError("模型未返回special_case_review")
    aggregate = auxiliary["csv_aggregate"]
    special_codes = aggregate["Special_case_sequence"]
    trusted_special = [code for code in special_codes if code != 0]
    low_speed_screen = aggregate["all_event_speed_below_16_5_mps"]
    special_review["low_speed_congestion_screen_triggered"] = low_speed_screen
    if trusted_special:
        special_review["minimum_speed_exemption_applies"] = True
        trusted_names = [SPECIAL_CASES.get(code, str(code)) for code in trusted_special]
        special_review["exemption_reason"] = (
            "证据链已确认特殊路况：" + "、".join(trusted_names)
        )
    additional_cases = special_review.get("visually_confirmed_additional_cases")
    if not isinstance(additional_cases, list):
        additional_cases = []
        special_review["visually_confirmed_additional_cases"] = additional_cases
    additional_exempt_cases = [
        str(case).strip()
        for case in additional_cases
        if str(case).strip() and "隧道" not in str(case)
    ]
    tunnel_present = special_review.get("tunnel_present") is True
    if not trusted_special and additional_exempt_cases:
        special_review["minimum_speed_exemption_applies"] = True
    elif not trusted_special:
        special_review["minimum_speed_exemption_applies"] = False
        special_review["exemption_reason"] = (
            "仅存在隧道，不构成最低限速豁免"
            if tunnel_present
            else "未确认可豁免最低限速的特殊路况"
        )

    exemption_applies = (
        special_review.get("minimum_speed_exemption_applies") is True
    )
    if exemption_applies and any(
        phrase in suggestion
        for phrase in ("立即提速", "不得低于", "至少达到", "保持至少")
    ):
        reason = special_review.get("exemption_reason") or "特殊路况"
        suggestion = (
            f"当前存在{reason}，应优先根据实际交通和道路条件保持安全车速，"
            "待特殊情况解除后再平稳恢复至适用速度范围。"
        )
    value["Driving_suggestion_VLM"] = suggestion

    consistency = value.get("auxiliary_consistency")
    if not isinstance(consistency, dict):
        consistency = {}
        value["auxiliary_consistency"] = consistency
    doubtful_points = consistency.get("doubtful_points")
    if not isinstance(doubtful_points, list):
        doubtful_points = []
    if sign_source and ego_passed_sign:
        fallback_source = fallback_min_speed_source(auxiliary)
        doubtful_points = [
            point
            for point in doubtful_points
            if not (
                isinstance(point, dict)
                and (
                    fallback_source
                    in str(point.get("auxiliary_claim", "")).strip()
                    or "fallback"
                    in str(point.get("auxiliary_claim", "")).strip().lower()
                    or "no_min_speed_sign_source_by_rule"
                    in str(point.get("auxiliary_claim", "")).strip()
                )
            )
        ]
        consistency["doubtful_points"] = doubtful_points
        if (
            str(consistency.get("status", "")).strip() == "明显不一致"
            and not doubtful_points
        ):
            consistency["status"] = "一致"
    if str(consistency.get("status", "")).strip() != "明显不一致":
        unconfirmed = consistency.get("unconfirmed_points")
        if not isinstance(unconfirmed, list):
            unconfirmed = []
        unconfirmed.extend(doubtful_points)
        consistency["unconfirmed_points"] = unconfirmed
        consistency["doubtful_points"] = []
        value["requires_manual_review"] = False
    else:
        value["requires_manual_review"] = bool(doubtful_points)
    return value


def review_min_event(
    segment: Path,
    event_dir: Path,
    output_root: Path,
    api_key: str | None,
    args: argparse.Namespace,
) -> dict[str, Any]:
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

    relative_dir = Path(segment.name) / event_dir.name
    frames = extract_frames(video_path, output_root / "frames" / relative_dir)
    sample_times = [frame["time"] for frame in frames]
    auxiliary = build_min_auxiliary_summary(event_dir, record, sample_times)
    if args.dry_run:
        aggregate = auxiliary["csv_aggregate"]
        return {
            "segment": segment.name,
            "event": event_dir.name,
            "status": "dry_run",
            "record_path": str(record_path),
            "frame_times": sample_times,
            "valid_lane_max_speed_limits_kph": aggregate[
                "Valid_LaneMaxSpdlim_values_kph"
            ],
            "no_min_speed_sign_source_by_rule": aggregate[
                "no_min_speed_sign_source_by_rule"
            ],
            "special_case_sequence": aggregate["Special_case_sequence"],
            "low_speed_congestion_screen": aggregate[
                "all_event_speed_below_16_5_mps"
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
    model_result = normalize_min_model_result(model_result, auxiliary)

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
        "speed_limit_source": model_result["speed_limit_source"],
        "requires_manual_review": model_result["requires_manual_review"],
        "manual_review_reason": model_result.get("manual_review_reason", ""),
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
        default=Path(__file__).resolve().parent / "outputs_min_speed",
    )
    parser.add_argument("--api-key-source", type=Path)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--workers", type=int, default=8, help="并行处理事件数；默认8"
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
        f"发现 {len(events)} 个最低限速事件；模型={args.model}；"
        f"模式={'dry-run' if args.dry_run else 'API审查并写入'}；"
        f"workers={args.workers}"
    )

    def process_event(segment: Path, event_dir: Path) -> dict[str, Any]:
        try:
            return review_min_event(
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

    doubtful = collect_doubtful_reviews(args.output_dir)
    summary = {
        "dataset_root": str(args.dataset_root),
        "model": args.model,
        "dry_run": args.dry_run,
        "workers": args.workers,
        "total": len(completed_results),
        "updated": sum(
            result["status"] == "updated" for result in completed_results
        ),
        "skipped_existing": sum(
            result["status"] == "skipped_existing" for result in completed_results
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
        f"failed={summary['failed']} manual_review={summary['manual_review_count']}"
    )
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
