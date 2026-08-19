#!/usr/bin/env python
"""用前向视频和辅助CSV审查最高限速事件场景。"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from scenario_prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE


DEFAULT_DATASET_ROOT = Path(os.environ.get("TLCD_DATASET_ROOT", "."))
DEFAULT_BASE_URL = "https://llmapi.paratera.com/v1"
DEFAULT_MODEL = "Qwen3.6-Plus"
FRAME_TIMES = (0.01, 1.0, 2.0, 3.0, 4.5)
VALID_SPEED_LIMIT_SOURCES = {
    "经过龙门架车道级限速标志",
    "经过路侧路段级限速标志",
    "位于地图限速管理区域",
    "位于无明确限速区域",
}
ROAD_TYPES = {
    "0": "unknown",
    "1": "multiple_carriageway",
    "2": "single_carriageway",
    "4": "service_road",
    "6": "ramp_entry",
    "7": "ramp_exit",
    "9": "jct",
    "18": "service_area_approach",
    "19": "service_area_jct",
    "20": "service_area_approach_jct",
    "27": "toll_booth",
    "31": "motorway_entry_ramp",
    "32": "motorway_exit_ramp",
    "34": "tunnel",
    "37": "toll_area",
    "38": "rest_area",
}
LANE_TYPES = {
    "0": "unknown",
    "1": "regular_lane",
    "2": "deceleration_lane",
    "3": "acceleration_lane",
    "4": "compound_lane",
    "5": "drivable_parking_lane",
    "8": "slow_lane",
    "9": "drivable_shoulder_lane",
    "10": "shoulder_lane",
    "12": "regulated_access_lane",
    "13": "variable_driving_lane",
    "14": "emergency_strip",
    "15": "other_lane",
}


class ReviewError(RuntimeError):
    """单事件审查失败。"""


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def parse_ratio(value: str) -> float:
    numerator, denominator = value.split("/", 1)
    return float(numerator) / float(denominator)


def probe_video(video_path: Path) -> dict[str, Any]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=avg_frame_rate,r_frame_rate,nb_frames,duration",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(video_path),
    ]
    try:
        completed = subprocess.run(
            command, check=True, capture_output=True, text=True, encoding="utf-8"
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as error:
        raise ReviewError(f"无法探测视频 {video_path}: {error}") from error

    metadata = json.loads(completed.stdout)
    stream = metadata["streams"][0]
    duration = float(stream.get("duration") or metadata["format"]["duration"])
    frame_rate_text = stream.get("avg_frame_rate") or stream.get("r_frame_rate")
    frame_rate = parse_ratio(frame_rate_text)
    last_frame_time = max(0.0, duration - 1.0 / frame_rate)
    return {
        "duration": duration,
        "frame_rate": frame_rate,
        "last_frame_time": last_frame_time,
    }


def extract_frames(video_path: Path, frame_dir: Path) -> list[dict[str, Any]]:
    video_info = probe_video(video_path)
    sample_times = [*FRAME_TIMES, video_info["last_frame_time"]]
    if sample_times[-1] <= FRAME_TIMES[-1]:
        raise ReviewError(f"视频不足以覆盖4.5秒采样点: {video_path}")

    frame_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, sample_time in enumerate(sample_times, start=1):
        suffix = "last" if index == len(sample_times) else f"{sample_time:g}s"
        frame_path = frame_dir / f"frame_{index:02d}_{suffix}.jpg"
        if index == len(sample_times):
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
            raise ReviewError(f"切帧失败 {video_path} @ {sample_time:.3f}s") from error
        if not frame_path.exists() or frame_path.stat().st_size == 0:
            raise ReviewError(f"未生成图像: {frame_path}")
        frames.append(
            {
                "index": index,
                "time": round(sample_time, 6),
                "is_last_frame": index == len(sample_times),
                "path": frame_path,
            }
        )
    return frames


def find_one(directory: Path, pattern: str) -> Path:
    matches = sorted(directory.glob(pattern))
    if len(matches) != 1:
        raise ReviewError(
            f"{directory} 中应有且仅有一个 {pattern}，实际找到 {len(matches)} 个"
        )
    return matches[0]


def nearest_csv_rows(path: Path, target_times: Iterable[float]) -> list[dict[str, str]]:
    targets = list(target_times)
    best_rows: list[dict[str, str] | None] = [None] * len(targets)
    best_diffs = [float("inf")] * len(targets)
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            try:
                row_time = float(row["event_time"])
            except (KeyError, TypeError, ValueError):
                continue
            for index, target in enumerate(targets):
                difference = abs(row_time - target)
                if difference < best_diffs[index]:
                    best_diffs[index] = difference
                    best_rows[index] = row
    if any(row is None for row in best_rows):
        raise ReviewError(f"CSV无有效event_time: {path}")
    return [row for row in best_rows if row is not None]


def select_columns(row: dict[str, str], columns: Iterable[str]) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    for column in columns:
        if column not in row:
            continue
        raw_value = row[column].strip()
        if raw_value == "":
            selected[column] = None
            continue
        try:
            number = float(raw_value)
            selected[column] = int(number) if number.is_integer() else round(number, 6)
        except ValueError:
            selected[column] = raw_value
    road_type = str(selected.get("Road_type", ""))
    lane_type = str(
        selected.get("Lane_type", selected.get("Lane_type_CurrentLane", ""))
    )
    if road_type in ROAD_TYPES:
        selected["Road_type_name"] = ROAD_TYPES[road_type]
    if lane_type in LANE_TYPES:
        selected["Lane_type_name"] = LANE_TYPES[lane_type]
    return selected


def unique_values(rows: list[dict[str, Any]], field: str) -> list[Any]:
    values = []
    for row in rows:
        value = row.get(field)
        if value is not None and value not in values:
            values.append(value)
    return values


def build_auxiliary_summary(
    event_dir: Path, record: dict[str, Any], sample_times: list[float]
) -> dict[str, Any]:
    evidence_path = find_one(event_dir, "*_EvidenceChain.csv")
    ego_path = find_one(event_dir, "*_EgoInfo.csv")
    map_path = find_one(event_dir, "*_MapInfo.csv")

    evidence_rows_raw = nearest_csv_rows(evidence_path, sample_times)
    ego_rows_raw = nearest_csv_rows(ego_path, sample_times)
    map_rows_raw = nearest_csv_rows(map_path, sample_times)

    evidence_columns = [
        "event_time",
        "Ego_velocity",
        "Road_type",
        "Lane_type",
        "IsMaxSpdsignArea",
        "Thres_MaxSpdlim",
        "LaneNumSameDirection",
        "EgoLaneIndex",
        "LaneMaxSpdlim_1",
        "LaneMaxSpdlim_2",
        "LaneMaxSpdlim_3",
        "LaneMaxSpdlim_4",
        "LaneMaxSpdlim_5",
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
    ]
    evidence_rows = [
        select_columns(row, evidence_columns) for row in evidence_rows_raw
    ]
    map_rows = [select_columns(row, map_columns) for row in map_rows_raw]
    ego_rows = [
        select_columns(row, ["event_time", "Ego_velocity"]) for row in ego_rows_raw
    ]

    speeds_kph = [
        round(float(row["Ego_velocity"]) * 3.6, 1)
        for row in evidence_rows_raw
        if row.get("Ego_velocity", "").strip()
    ]
    result = record.get("Result") or record.get("result") or {}
    evidence = record.get("Evidence") or record.get("evidence") or {}
    return {
        "warning": "以下信息均为待核验辅助证据，不是视觉事实。",
        "record": {
            "Location": record.get("Location"),
            "Date": record.get("Date"),
            "Time": record.get("Time"),
            "Article_ID": (record.get("Article") or {}).get("ID"),
            "EventAnchor": record.get("EventAnchor"),
            "Evidence_scene_fields": {
                "Ego_speed_kph": evidence.get("Ego_speed_kph"),
                "Applicable_max_speed_limit_kph": evidence.get(
                    "Applicable_max_speed_limit_kph"
                ),
                "Road_types": evidence.get("Road_types"),
                "Lane_types": evidence.get("Lane_types"),
                "Inside_speed_limit_sign_area": evidence.get(
                    "Inside_speed_limit_sign_area"
                ),
            },
            "original_Scenario_description": result.get("Scenario_description"),
            "original_Driving_suggestion": result.get("Driving_suggestion"),
        },
        "csv_aggregate": {
            "sampled_ego_speed_kph_range": [
                min(speeds_kph),
                max(speeds_kph),
            ]
            if speeds_kph
            else None,
            "Road_type_sequence": unique_values(evidence_rows, "Road_type_name"),
            "Lane_type_sequence": unique_values(evidence_rows, "Lane_type_name"),
            "IsMaxSpdsignArea_sequence": unique_values(
                evidence_rows, "IsMaxSpdsignArea"
            ),
            "Thres_MaxSpdlim_sequence_kph": unique_values(
                evidence_rows, "Thres_MaxSpdlim"
            ),
        },
        "EvidenceChain_samples": evidence_rows,
        "EgoInfo_samples": ego_rows,
        "MapInfo_samples": map_rows,
    }


def encode_image(path: Path) -> str:
    with path.open("rb") as file:
        return base64.b64encode(file.read()).decode("ascii")


def load_api_key(source_path: Path | None) -> str:
    environment_key = os.environ.get("QWEN_API_KEY", "").strip()
    if environment_key:
        return environment_key
    if source_path is None:
        raise ReviewError(
            "未设置QWEN_API_KEY，也未提供--api-key-source；不会把key写入项目"
        )
    source_text = source_path.read_text(encoding="utf-8")
    match = re.search(
        r"(?m)^\s*API_KEY\s*=\s*([\"'])(?P<key>[^\"']+)\1\s*$", source_text
    )
    if not match:
        raise ReviewError(f"无法从 {source_path} 读取API_KEY赋值")
    return match.group("key")


def parse_model_json(content: str) -> dict[str, Any]:
    stripped = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ReviewError("模型响应中没有JSON对象")
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as error:
            raise ReviewError(f"模型JSON解析失败: {error}") from error
    if not isinstance(value, dict):
        raise ReviewError("模型响应JSON不是对象")
    return value


def call_model(
    api_key: str,
    base_url: str,
    model: str,
    frames: list[dict[str, Any]],
    auxiliary: dict[str, Any],
    timeout: int,
    max_retries: int,
    system_prompt: str = SYSTEM_PROMPT,
    user_prompt_template: str = USER_PROMPT_TEMPLATE,
    max_tokens: int = 2200,
) -> tuple[dict[str, Any], str]:
    timeline = "\n".join(
        f"- {frame.get('label') or '帧' + str(frame['index'])}: "
        + ("视频末帧" if frame["is_last_frame"] else f"event_time={frame['time']:g}s")
        for frame in frames
    )
    user_prompt = user_prompt_template.format(
        frame_timeline=timeline,
        auxiliary_json=json.dumps(auxiliary, ensure_ascii=False, indent=2),
    )
    content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
    for frame in frames:
        frame_label = frame.get("label", f"帧{frame['index']}")
        content.extend(
            [
                {
                    "type": "text",
                    "text": f"{frame_label}（{frame['time']:g}s）",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/jpeg;base64," + encode_image(frame["path"])
                    },
                },
            ]
        )
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    direct_opener = (
        urllib.request.build_opener(urllib.request.ProxyHandler({}))
        if request.host == "llmapi.paratera.com"
        else None
    )

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            if direct_opener is None:
                response_context = urllib.request.urlopen(request, timeout=timeout)
            else:
                response_context = direct_opener.open(request, timeout=timeout)
            with response_context as response:
                response_value = json.loads(response.read().decode("utf-8"))
            raw_content = response_value["choices"][0]["message"]["content"]
            if isinstance(raw_content, list):
                raw_content = "".join(
                    item.get("text", "")
                    for item in raw_content
                    if isinstance(item, dict)
                )
            if not isinstance(raw_content, str):
                raise ReviewError("模型响应content不是字符串")
            return parse_model_json(raw_content), raw_content
        except urllib.error.HTTPError as error:
            error_body = error.read().decode("utf-8", errors="replace")[:1000]
            last_error = ReviewError(f"HTTP {error.code}: {error_body}")
            if error.code not in {408, 429, 500, 502, 503, 504}:
                break
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ReviewError) as error:
            last_error = error
        if attempt < max_retries:
            time.sleep(min(2**attempt, 8))
    raise ReviewError(f"模型调用失败（已尝试{max_retries}次）: {last_error}")


def normalize_model_result(value: dict[str, Any]) -> dict[str, Any]:
    source = str(value.get("speed_limit_source", "")).strip()
    if source not in VALID_SPEED_LIMIT_SOURCES:
        raise ReviewError(f"模型返回无效限速来源: {source!r}")

    description = str(value.get("Scenario_description_VLM", "")).strip()
    suggestion = str(value.get("Driving_suggestion_VLM", "")).strip()
    if not description or not suggestion:
        raise ReviewError("模型未返回完整的Scenario_description_VLM/Driving_suggestion_VLM")

    description = re.sub(r"^限速来源：[^。]+。", "", description).strip()
    source_clauses = {
        "经过龙门架车道级限速标志": (
            "龙门架车道级限速标志",
            "自车经过适用于当前车道的龙门架车道级限速标志",
        ),
        "经过路侧路段级限速标志": (
            "路侧路段级限速标志",
            "自车经过适用于当前道路的路侧路段级限速标志",
        ),
        "位于地图限速管理区域": (
            "地图限速管理区域",
            "该路段为地图限速管理区域",
        ),
        "位于无明确限速区域": (
            "无明确限速",
            "该路段无明确限速",
        ),
    }
    marker, clause = source_clauses[source]
    if marker not in description:
        first_sentence, separator, remainder = description.partition("。")
        description = first_sentence.rstrip("，,") + "，" + clause + "。"
        if separator and remainder:
            description += remainder
    value["Scenario_description_VLM"] = description
    value["Driving_suggestion_VLM"] = suggestion
    value["speed_limit_source"] = source
    sign_review = value.get("speed_limit_sign_review")
    if not isinstance(sign_review, dict):
        raise ReviewError("模型未返回speed_limit_sign_review")
    visible_signs = sign_review.get("visible_signs")
    if not isinstance(visible_signs, list):
        raise ReviewError("模型未返回speed_limit_sign_review.visible_signs")
    applicable_sign_seen = sign_review.get("applicable_sign_seen")
    sign_source = source in {
        "经过龙门架车道级限速标志",
        "经过路侧路段级限速标志",
    }
    if sign_source and applicable_sign_seen is not True:
        raise ReviewError("模型选择了实体标志来源，但未确认该标志适用于自车")
    consistency = value.get("auxiliary_consistency")
    if not isinstance(consistency, dict):
        consistency = {}
        value["auxiliary_consistency"] = consistency
    status = str(consistency.get("status", "")).strip()
    doubtful_points = consistency.get("doubtful_points")
    if not isinstance(doubtful_points, list):
        doubtful_points = []
    if status != "明显不一致":
        unconfirmed = consistency.get("unconfirmed_points")
        if not isinstance(unconfirmed, list):
            unconfirmed = []
        unconfirmed.extend(doubtful_points)
        consistency["unconfirmed_points"] = unconfirmed
        consistency["doubtful_points"] = []
        value["requires_manual_review"] = False
        return value

    value["requires_manual_review"] = bool(doubtful_points)
    return value


def discover_events(dataset_root: Path, segment_limit: int) -> list[tuple[Path, Path]]:
    segments = sorted(path for path in dataset_root.iterdir() if path.is_dir())
    if segment_limit > 0:
        segments = segments[:segment_limit]
    events = []
    for segment in segments:
        event_dirs = sorted(
            path
            for path in segment.iterdir()
            if path.is_dir() and re.fullmatch(r"event_\d+", path.name)
        )
        events.extend((segment, event_dir) for event_dir in event_dirs)
    return events


def result_container(record: dict[str, Any]) -> dict[str, Any]:
    if isinstance(record.get("Result"), dict):
        return record["Result"]
    if isinstance(record.get("result"), dict):
        return record["result"]
    raise ReviewError("record.json中没有Result/result对象")


def load_completed_review(
    output_root: Path, segment_name: str, event_name: str, model: str
) -> dict[str, Any] | None:
    review_path = output_root / "reviews" / segment_name / f"{event_name}.json"
    try:
        review = read_json(review_path)
    except (OSError, json.JSONDecodeError):
        return None
    model_result = review.get("model_result")
    if review.get("model") != model or not isinstance(model_result, dict):
        return None
    if not model_result.get("Scenario_description_VLM") or not model_result.get(
        "Driving_suggestion_VLM"
    ):
        return None
    return review


def review_event(
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

    frame_dir = output_root / "frames" / relative_dir
    frames = extract_frames(video_path, frame_dir)
    sample_times = [frame["time"] for frame in frames]
    auxiliary = build_auxiliary_summary(event_dir, record, sample_times)
    if args.dry_run:
        return {
            "segment": segment.name,
            "event": event_dir.name,
            "status": "dry_run",
            "record_path": str(record_path),
            "frame_times": sample_times,
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
    )
    model_result = normalize_model_result(model_result)

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
        "auxiliary_consistency": model_result.get("auxiliary_consistency", {}),
    }


def collect_doubtful_reviews(output_root: Path) -> list[dict[str, Any]]:
    doubtful = []
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
                "record_path": review.get("record_path"),
                "speed_limit_source": model_result.get("speed_limit_source"),
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
        default=Path(__file__).resolve().parent / "outputs",
    )
    parser.add_argument("--api-key-source", type=Path)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument(
        "--workers", type=int, default=1, help="并行处理事件数；默认1（串行）"
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
        f"发现 {len(events)} 个事件；模型={args.model}；"
        f"模式={'dry-run' if args.dry_run else 'API审查并写入'}；"
        f"workers={args.workers}；resume={args.resume}"
    )

    def process_event(segment: Path, event_dir: Path) -> dict[str, Any]:
        try:
            return review_event(
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
        "resume": args.resume,
        "total": len(completed_results),
        "updated": sum(
            result["status"] == "updated" for result in completed_results
        ),
        "skipped_existing": sum(
            result["status"] == "skipped_existing" for result in completed_results
        ),
        "skipped_completed": sum(
            result["status"] == "skipped_completed" for result in completed_results
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
