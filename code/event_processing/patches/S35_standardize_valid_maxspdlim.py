#!/usr/bin/env python3
"""Standardize final MaxSpdlim evidence chains and record descriptions."""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOTS = (
    Path(r"Z:\HongqiData\Nanjing_valid\01_MaxSpdlim"),
    Path(r"Z:\HongqiData\Changchun_valid\01_MaxSpdlim"),
)

BASE_COLUMNS = [
    "event_time",
    "trigger_IMR_45_1",
    "com_IMR_45_1",
    "trigger_IMR_46_3",
    "com_IMR_46_3",
    "trigger_IMR_46_4",
    "com_IMR_46_4",
    "trigger_IMR_46_5",
    "com_IMR_46_5",
    "trigger_IMR_78_1",
    "com_IMR_78_1",
    "trigger_IMR_78_3",
    "com_IMR_78_3",
    "Ego_velocity",
    "Road_type",
    "Lane_type",
    "IsMaxSpdsignArea",
    "Thres_MaxSpdlim",
]
MAP_COLUMNS = [
    "LaneNumSameDirection",
    "EgoLaneIndex",
    "LaneMaxSpdlim_1",
    "LaneMaxSpdlim_2",
    "LaneMaxSpdlim_3",
    "LaneMaxSpdlim_4",
    "LaneMaxSpdlim_5",
]
REMOVED_COLUMNS = [
    "Event_description",
    "Speed_limit_sign_effective",
    "Speed_limit_rule",
]
TARGET_COLUMNS = BASE_COLUMNS + MAP_COLUMNS

NO_CONTROL_DESCRIPTION = "This is a road section without maximum-speed-limit control."
MAP_CONTROL_DESCRIPTION = "This is a map-based speed-limit control area."
VEHICLE_TYPE_DESCRIPTION = "依车型区分限速 120"
ARTICLE_78_3_TEXT = (
    "A vehicle shall follow the maximum speed indicated by the road speed-limit sign."
)

DESCRIPTION_EN = {
    "依车型区分限速 120": (
        "A right-side speed-limit sign specifies vehicle-type-dependent limits, "
        "including 120 km/h."
    ),
    "右侧有圆形100": "A circular 100 km/h speed-limit sign is present on the right.",
    "龙门架上有120/120/100/100": (
        "The overhead gantry displays lane-specific speed limits of "
        "120/120/100/100 km/h."
    ),
    "龙门架120/120/100/100": (
        "The gantry displays lane-specific speed limits of 120/120/100/100 km/h."
    ),
    "右侧有圆形40": "A circular 40 km/h speed-limit sign is present on the right.",
    "右侧有圆形80": "A circular 80 km/h speed-limit sign is present on the right.",
    "隧道内上方有圆形80": (
        "A circular 80 km/h speed-limit sign is mounted overhead in the tunnel."
    ),
    "匝道限速 40": "A 40 km/h speed limit applies on the ramp.",
    "右侧有圆形120": "A circular 120 km/h speed-limit sign is present on the right.",
    "龙门架上120/120/100/100": (
        "The overhead gantry displays lane-specific speed limits of "
        "120/120/100/100 km/h."
    ),
    "右侧圆形100": "A circular 100 km/h speed-limit sign is present on the right.",
    "右侧圆形 100": "A circular 100 km/h speed-limit sign is present on the right.",
    "龙门架 120 120 100 100": (
        "The gantry displays lane-specific speed limits of 120/120/100/100 km/h."
    ),
    "龙门架上有80/80/80": (
        "The overhead gantry displays lane-specific speed limits of 80/80/80 km/h."
    ),
    "龙门架80/80/80": (
        "The gantry displays lane-specific speed limits of 80/80/80 km/h."
    ),
    "右侧有圆形80和电子显示80": (
        "A circular 80 km/h speed-limit sign and an electronic 80 km/h display "
        "are present on the right."
    ),
    "出口限速 60": "A 60 km/h speed limit applies at the exit.",
    "龙门架上有圆形80和电子显示80": (
        "The overhead gantry carries a circular 80 km/h speed-limit sign and "
        "an electronic 80 km/h display."
    ),
    "龙门加上有120/120/100/100": (
        "The overhead gantry displays lane-specific speed limits of "
        "120/120/100/100 km/h."
    ),
    "左侧上方有电子显示120": (
        "An overhead electronic 120 km/h speed-limit display is present on the left."
    ),
    "龙门架120/120/120": (
        "The gantry displays lane-specific speed limits of 120/120/120 km/h."
    ),
    "龙门架上有120/120/120": (
        "The overhead gantry displays lane-specific speed limits of 120/120/120 km/h."
    ),
    "龙门架上120/120/120": (
        "The overhead gantry displays lane-specific speed limits of 120/120/120 km/h."
    ),
    "右侧加速车道有限速100": (
        "A 100 km/h speed limit applies to the acceleration lane on the right."
    ),
    "右侧匝道内有限速40": "A 40 km/h speed limit applies on the ramp to the right.",
    "右侧有临时限速80，左侧有圆形80": (
        "A temporary 80 km/h speed-limit sign is present on the right, and a "
        "circular 80 km/h speed-limit sign is present on the left."
    ),
    "右侧有临时限速80": (
        "A temporary 80 km/h speed-limit sign is present on the right."
    ),
    "龙门架上方电子显示120": (
        "An overhead electronic 120 km/h speed-limit display is mounted on the gantry."
    ),
    "龙门架 120 120 120": (
        "The gantry displays lane-specific speed limits of 120/120/120 km/h."
    ),
    "临时限速牌 80": "A temporary 80 km/h speed-limit sign is present.",
    "匝道限速40": "A 40 km/h speed limit applies on the ramp.",
    "顶部LED 80": "An overhead LED display shows an 80 km/h speed limit.",
    "龙门架上有圆形80": (
        "A circular 80 km/h speed-limit sign is mounted on the overhead gantry."
    ),
    "右侧有临时限速100，左侧有圆形100": (
        "A temporary 100 km/h speed-limit sign is present on the right, and a "
        "circular 100 km/h speed-limit sign is present on the left."
    ),
    "右侧临时限速80": (
        "A temporary 80 km/h speed-limit sign is present on the right."
    ),
    "右侧圆形80": "A circular 80 km/h speed-limit sign is present on the right.",
    "右侧有临时限速标志80": (
        "A temporary 80 km/h speed-limit sign is present on the right."
    ),
    "右侧有匝道内限速60": "A 60 km/h speed limit applies on the ramp to the right.",
    "右侧圆形 80": "A circular 80 km/h speed-limit sign is present on the right.",
    "临时限速牌 100": "A temporary 100 km/h speed-limit sign is present.",
    "龙门架 80 80 80": (
        "The gantry displays lane-specific speed limits of 80/80/80 km/h."
    ),
    "龙门架上方圆形80和电子显示80": (
        "The overhead gantry carries a circular 80 km/h speed-limit sign and "
        "an electronic 80 km/h display."
    ),
    "隧道内上方圆形80": (
        "A circular 80 km/h speed-limit sign is mounted overhead in the tunnel."
    ),
    "龙门架上方存在圆形80和电子显示80": (
        "The overhead gantry carries a circular 80 km/h speed-limit sign and "
        "an electronic 80 km/h display."
    ),
    "龙门架上有120/120/100/100，右侧有圆形80": (
        "The overhead gantry displays lane-specific speed limits of "
        "120/120/100/100 km/h, and a circular 80 km/h speed-limit sign is "
        "present on the right."
    ),
    "龙门加上120/120/100/100": (
        "The overhead gantry displays lane-specific speed limits of "
        "120/120/100/100 km/h."
    ),
    "龙门架上80/80/80": (
        "The overhead gantry displays lane-specific speed limits of 80/80/80 km/h."
    ),
    "龙门架100/100/80/80": (
        "The gantry displays lane-specific speed limits of 100/100/80/80 km/h."
    ),
    "自车处于减速车道，最右侧有限速40": (
        "The ego vehicle is in a deceleration lane, and a 40 km/h speed limit "
        "applies to the rightmost lane."
    ),
    "龙门加上有120/120/120": (
        "The overhead gantry displays lane-specific speed limits of 120/120/120 km/h."
    ),
    "龙门架上有100/100/80/80": (
        "The overhead gantry displays lane-specific speed limits of "
        "100/100/80/80 km/h."
    ),
    "右侧有限速100": "A 100 km/h speed limit is posted on the right.",
    "自车在减速车道，最右侧有圆形40": (
        "The ego vehicle is in a deceleration lane, and a circular 40 km/h "
        "speed-limit sign is present at the far right."
    ),
    "龙门架上方有圆形80和电子显示80": (
        "The overhead gantry carries a circular 80 km/h speed-limit sign and "
        "an electronic 80 km/h display."
    ),
    "右侧临时限速100": (
        "A temporary 100 km/h speed-limit sign is present on the right."
    ),
    "右侧有圆形60": "A circular 60 km/h speed-limit sign is present on the right.",
    "右侧有临时限速100": (
        "A temporary 100 km/h speed-limit sign is present on the right."
    ),
    "左侧有临时限速标志120": (
        "A temporary 120 km/h speed-limit sign is present on the left."
    ),
    "左侧有临时限速80": (
        "A temporary 80 km/h speed-limit sign is present on the left."
    ),
    "左侧有临时限速120": (
        "A temporary 120 km/h speed-limit sign is present on the left."
    ),
    "右侧有临时限速60": (
        "A temporary 60 km/h speed-limit sign is present on the right."
    ),
    "左侧上方有圆形120": (
        "A circular 120 km/h speed-limit sign is mounted overhead on the left."
    ),
    "左侧存在圆形80，圆形60": (
        "Circular 80 km/h and 60 km/h speed-limit signs are present on the left."
    ),
    "右侧圆形120": "A circular 120 km/h speed-limit sign is present on the right.",
    "右侧有电子显示80": (
        "An electronic 80 km/h speed-limit display is present on the right."
    ),
}


def detect_text_format(path: Path) -> tuple[str, str, bytes, str]:
    raw = path.read_bytes()
    bom = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    payload = raw[len(bom) :]
    try:
        text = payload.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = payload.decode("gb18030")
        encoding = "gb18030"
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return text, encoding, bom, newline


def read_csv_file(path: Path) -> tuple[list[str], list[dict[str, str]], tuple[str, bytes, str]]:
    text, encoding, bom, newline = detect_text_format(path)
    reader = csv.DictReader(io.StringIO(text, newline=""))
    rows = list(reader)
    return list(reader.fieldnames or []), rows, (encoding, bom, newline)


def render_csv(
    rows: list[dict[str, str]],
    fieldnames: list[str],
    text_format: tuple[str, bytes, str],
) -> bytes:
    encoding, bom, newline = text_format
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=fieldnames,
        extrasaction="ignore",
        lineterminator=newline,
    )
    writer.writeheader()
    writer.writerows(rows)
    return bom + output.getvalue().encode(encoding)


def time_key(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid event_time {value!r}") from exc


def exactly_one(directory: Path, pattern: str) -> Path:
    paths = list(directory.glob(pattern))
    if len(paths) != 1:
        raise ValueError(f"{directory}: expected one {pattern}, found {len(paths)}")
    return paths[0]


def classify_map_limits(rows: list[dict[str, str]]) -> str:
    valid_limits: list[float] = []
    for row in rows:
        try:
            lane_count = int(float(row["LaneNumSameDirection"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid LaneNumSameDirection") from exc
        for lane_index in range(1, min(max(lane_count, 0), 5) + 1):
            try:
                value = float(row[f"LaneMaxSpdlim_{lane_index}"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"invalid LaneMaxSpdlim_{lane_index}") from exc
            if math.isfinite(value) and value > 0:
                valid_limits.append(value)
    if not valid_limits:
        raise ValueError("no valid positive lane maximum speed limit")
    if all(value == 120 for value in valid_limits):
        return "no_control"
    return "map_controlled"


def load_record(path: Path) -> dict:
    text, _, _, _ = detect_text_format(path)
    return json.loads(text)


def update_record(record: dict, description: str, vehicle_type_sign: bool) -> dict:
    result = record.setdefault("Result", {})
    scenario = result.get("Scenario_description")
    if not isinstance(scenario, str) or not scenario.strip():
        raise ValueError("missing Result.Scenario_description")
    scenario = re.sub(r"\s+Speed-limit context:.*$", "", scenario, flags=re.DOTALL)
    result["Scenario_description"] = (
        f"{scenario.rstrip()} Speed-limit context: {description}"
    )

    if vehicle_type_sign:
        article = record.setdefault("Article", {})
        article["ID"] = "IMR_78.3"
        article["Text"] = [ARTICLE_78_3_TEXT]
        evidence = record.setdefault("Evidence", {})
        statuses = evidence.setdefault("Article_status", {})
        old_status = statuses.pop("IMR_78.1", None)
        if old_status is None:
            old_status = statuses.get("IMR_78.3")
        if old_status is None:
            raise ValueError("missing IMR_78.1/IMR_78.3 Article_status")
        statuses["IMR_78.3"] = old_status
        evidence["Inside_speed_limit_sign_area"] = True
    return record


def json_bytes(record: dict) -> bytes:
    return (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def preflight_event(evidence_path: Path) -> dict:
    event_dir = evidence_path.parent
    map_path = exactly_one(event_dir, "*_MapInfo.csv")
    record_path = exactly_one(event_dir, "*_record.json")
    columns, evidence_rows, evidence_format = read_csv_file(evidence_path)
    if len(columns) == len(BASE_COLUMNS) and set(columns) == set(BASE_COLUMNS):
        source_schema = 18
        map_columns, map_rows, _ = read_csv_file(map_path)
        missing_map_columns = [
            column for column in ["event_time"] + MAP_COLUMNS if column not in map_columns
        ]
        if missing_map_columns:
            raise ValueError(f"MapInfo missing columns {missing_map_columns}")
        by_time: dict[Decimal, dict[str, str]] = {}
        for row in map_rows:
            key = time_key(row["event_time"])
            if key in by_time:
                raise ValueError(f"duplicate MapInfo event_time {row['event_time']}")
            by_time[key] = row
        enriched_rows = []
        for row in evidence_rows:
            key = time_key(row["event_time"])
            if key not in by_time:
                raise ValueError(f"MapInfo missing event_time {row['event_time']}")
            enriched = dict(row)
            enriched.update({column: by_time[key][column] for column in MAP_COLUMNS})
            enriched_rows.append(enriched)
        map_class = classify_map_limits(enriched_rows)
        description = (
            NO_CONTROL_DESCRIPTION
            if map_class == "no_control"
            else MAP_CONTROL_DESCRIPTION
        )
        output_rows = enriched_rows
        original_description = None
    elif columns == TARGET_COLUMNS + REMOVED_COLUMNS:
        source_schema = 28
        descriptions = {
            row.get("Event_description", "").strip() for row in evidence_rows
        }
        if len(descriptions) != 1 or not next(iter(descriptions), ""):
            raise ValueError(
                f"expected one non-empty Event_description, found {descriptions}"
            )
        original_description = next(iter(descriptions))
        if original_description not in DESCRIPTION_EN:
            raise ValueError(
                f"unmapped Event_description {original_description!r}"
            )
        description = DESCRIPTION_EN[original_description]
        map_class = "existing_description"
        output_rows = evidence_rows
    else:
        raise ValueError(f"unexpected EvidenceChain columns ({len(columns)}): {columns}")

    record = load_record(record_path)
    vehicle_type_sign = original_description == VEHICLE_TYPE_DESCRIPTION
    updated_record = update_record(record, description, vehicle_type_sign)
    evidence_output = render_csv(output_rows, TARGET_COLUMNS, evidence_format)
    record_output = json_bytes(updated_record)
    if re.search(r"[\u3400-\u9fff]", record_output.decode("utf-8")):
        raise ValueError("updated record.json still contains Chinese text")

    return {
        "evidence_path": evidence_path,
        "record_path": record_path,
        "evidence_output": evidence_output,
        "record_output": record_output,
        "source_schema": source_schema,
        "map_class": map_class,
        "vehicle_type_sign": vehicle_type_sign,
        "description": original_description,
        "encoding": evidence_format[0],
        "bom": bool(evidence_format[1]),
        "newline": evidence_format[2],
    }


def backup_relative_path(path: Path) -> Path:
    for dataset_name in ("Nanjing_valid", "Changchun_valid"):
        if dataset_name in path.parts:
            index = path.parts.index(dataset_name)
            return Path(*path.parts[index:])
    raise ValueError(f"path is outside expected datasets: {path}")


def apply_outputs(items: list[dict]) -> Path:
    stage_dir = Path(tempfile.mkdtemp(prefix="tlcd_maxspdlim_stage_"))
    backup_dir = Path(tempfile.mkdtemp(prefix="tlcd_maxspdlim_backup_"))
    staged: list[tuple[Path, Path, Path]] = []
    try:
        for index, item in enumerate(items):
            for kind, original, content in (
                ("evidence", item["evidence_path"], item["evidence_output"]),
                ("record", item["record_path"], item["record_output"]),
            ):
                stage_path = stage_dir / f"{index:04d}_{kind}{original.suffix}"
                stage_path.write_bytes(content)
                backup_path = backup_dir / backup_relative_path(original)
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(original, backup_path)
                staged.append((stage_path, original, backup_path))

        applied: list[tuple[Path, Path]] = []
        try:
            for stage_path, original, backup_path in staged:
                handle, sibling_name = tempfile.mkstemp(
                    prefix=f".{original.name}.",
                    suffix=".s35.tmp",
                    dir=original.parent,
                )
                os.close(handle)
                sibling_path = Path(sibling_name)
                try:
                    shutil.copyfile(stage_path, sibling_path)
                    os.replace(sibling_path, original)
                finally:
                    sibling_path.unlink(missing_ok=True)
                applied.append((original, backup_path))
        except Exception:
            for original, backup_path in reversed(applied):
                shutil.copy2(backup_path, original)
            raise
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
    return backup_dir


def verify_written(items: list[dict]) -> None:
    for item in items:
        columns, rows, _ = read_csv_file(item["evidence_path"])
        if columns != TARGET_COLUMNS:
            raise ValueError(f"{item['evidence_path']}: post-write schema mismatch")
        if any(any(column not in row for column in TARGET_COLUMNS) for row in rows):
            raise ValueError(f"{item['evidence_path']}: post-write row mismatch")
        record = load_record(item["record_path"])
        scenario = record.get("Result", {}).get("Scenario_description", "")
        if scenario.count("Speed-limit context:") != 1:
            raise ValueError(
                f"{item['record_path']}: expected one speed-limit context"
            )
        if re.search(r"[\u3400-\u9fff]", json.dumps(record, ensure_ascii=False)):
            raise ValueError(f"{item['record_path']}: contains Chinese text")
        if item["vehicle_type_sign"]:
            if record.get("Article", {}).get("ID") != "IMR_78.3":
                raise ValueError(f"{item['record_path']}: Article.ID mismatch")
            statuses = record.get("Evidence", {}).get("Article_status", {})
            if "IMR_78.3" not in statuses or "IMR_78.1" in statuses:
                raise ValueError(
                    f"{item['record_path']}: Article_status mismatch"
                )


def verify_final_event(evidence_path: Path, backup_dir: Path) -> dict:
    columns, evidence_rows, (_, bom, newline) = read_csv_file(evidence_path)
    if columns != TARGET_COLUMNS:
        raise ValueError(f"schema mismatch: {columns}")
    if any(column in columns for column in REMOVED_COLUMNS):
        raise ValueError("removed field is still present")

    source_path = backup_dir / backup_relative_path(evidence_path)
    source_columns, source_rows, _ = read_csv_file(source_path)
    if len(source_rows) != len(evidence_rows):
        raise ValueError(
            f"row count changed: {len(source_rows)} != {len(evidence_rows)}"
        )
    for source_row, evidence_row in zip(source_rows, evidence_rows):
        if source_row["event_time"] != evidence_row["event_time"]:
            raise ValueError(
                "event_time order changed: "
                f"{source_row['event_time']} != {evidence_row['event_time']}"
            )
    if len(source_columns) == 18 and set(source_columns) == set(BASE_COLUMNS):
        source_schema = 18
        preserved_columns = BASE_COLUMNS
    elif source_columns == TARGET_COLUMNS + REMOVED_COLUMNS:
        source_schema = 28
        preserved_columns = TARGET_COLUMNS
    else:
        raise ValueError(f"unexpected backup schema: {source_columns}")
    for source_row, evidence_row in zip(source_rows, evidence_rows):
        for column in preserved_columns:
            if source_row[column] != evidence_row[column]:
                raise ValueError(
                    f"{column} changed at event_time {source_row['event_time']}: "
                    f"{source_row[column]} != {evidence_row[column]}"
                )

    map_path = exactly_one(evidence_path.parent, "*_MapInfo.csv")
    map_columns, map_rows, _ = read_csv_file(map_path)
    missing_map_columns = [
        column for column in ["event_time"] + MAP_COLUMNS if column not in map_columns
    ]
    if missing_map_columns:
        raise ValueError(f"MapInfo missing columns {missing_map_columns}")
    map_by_time = {time_key(row["event_time"]): row for row in map_rows}
    if len(map_by_time) != len(map_rows):
        raise ValueError("duplicate MapInfo event_time")
    if source_schema == 18:
        for row in evidence_rows:
            key = time_key(row["event_time"])
            if key not in map_by_time:
                raise ValueError(f"MapInfo missing event_time {row['event_time']}")
            map_row = map_by_time[key]
            for column in MAP_COLUMNS:
                if row[column] != map_row[column]:
                    raise ValueError(
                        f"{column} mismatch at event_time {row['event_time']}: "
                        f"{row[column]} != {map_row[column]}"
                    )

    record_path = exactly_one(evidence_path.parent, "*_record.json")
    record = load_record(record_path)
    record_text = json.dumps(record, ensure_ascii=False)
    if re.search(r"[\u3400-\u9fff]", record_text):
        raise ValueError("record.json contains Chinese text")
    scenario = record.get("Result", {}).get("Scenario_description", "")
    if not isinstance(scenario, str) or scenario.count("Speed-limit context:") != 1:
        raise ValueError("record.json does not contain exactly one speed-limit context")
    context = scenario.split("Speed-limit context:", 1)[1].strip()
    vehicle_type_sign = context == DESCRIPTION_EN[VEHICLE_TYPE_DESCRIPTION]
    if vehicle_type_sign:
        if record.get("Article", {}).get("ID") != "IMR_78.3":
            raise ValueError("vehicle-type sign event has incorrect Article.ID")
        if record.get("Article", {}).get("Text") != [ARTICLE_78_3_TEXT]:
            raise ValueError("vehicle-type sign event has incorrect Article.Text")
        evidence = record.get("Evidence", {})
        statuses = evidence.get("Article_status", {})
        if "IMR_78.3" not in statuses or "IMR_78.1" in statuses:
            raise ValueError("vehicle-type sign event has incorrect Article_status")
        if evidence.get("Inside_speed_limit_sign_area") is not True:
            raise ValueError(
                "vehicle-type sign event is not marked inside the sign area"
            )
    return {
        "context": context,
        "vehicle_type_sign": vehicle_type_sign,
        "source_schema": source_schema,
        "bom": bool(bom),
        "newline": newline,
    }


def verify_final_dataset(evidence_paths: list[Path], backup_dir: Path) -> None:
    results = []
    errors = []
    for index, evidence_path in enumerate(evidence_paths, start=1):
        try:
            results.append(verify_final_event(evidence_path, backup_dir))
        except Exception as exc:
            errors.append(f"{evidence_path}: {exc}")
        if index % 100 == 0 or index == len(evidence_paths):
            print(
                f"Final audit {index}/{len(evidence_paths)}; errors={len(errors)}",
                flush=True,
            )
    if errors:
        print("\n".join(errors[:50]))
        raise ValueError(f"final audit failed for {len(errors)} event(s)")
    context_counts = Counter(result["context"] for result in results)
    summary = {
        "events": len(results),
        "schema_columns": len(TARGET_COLUMNS),
        "source_schemas": dict(
            Counter(result["source_schema"] for result in results)
        ),
        "no_control_contexts": context_counts[NO_CONTROL_DESCRIPTION],
        "map_controlled_contexts": context_counts[MAP_CONTROL_DESCRIPTION],
        "translated_contexts": (
            len(results)
            - context_counts[NO_CONTROL_DESCRIPTION]
            - context_counts[MAP_CONTROL_DESCRIPTION]
        ),
        "unique_contexts": len(context_counts),
        "vehicle_type_sign_events": sum(
            result["vehicle_type_sign"] for result in results
        ),
        "bom_files": sum(result["bom"] for result in results),
        "no_bom_files": sum(not result["bom"] for result in results),
        "newlines": dict(Counter(repr(result["newline"]) for result in results)),
    }
    print("FINAL_AUDIT_JSON=" + json.dumps(summary, ensure_ascii=False), flush=True)


def normalize_utf8(evidence_paths: list[Path]) -> Path:
    targets = []
    for path in evidence_paths:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raw.decode("utf-8-sig")
            continue
        text = raw.decode("gb18030")
        targets.append((path, raw, b"\xef\xbb\xbf" + text.encode("utf-8")))
    if len(targets) != 4:
        raise ValueError(f"expected 4 non-BOM files, found {len(targets)}")

    backup_dir = Path(tempfile.mkdtemp(prefix="tlcd_maxspdlim_utf8_backup_"))
    for path, _, _ in targets:
        backup_path = backup_dir / backup_relative_path(path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, backup_path)

    converted: list[Path] = []
    try:
        for path, original, updated in targets:
            if updated[3:].decode("utf-8") != original.decode("gb18030"):
                raise ValueError(f"{path}: transcoded content mismatch")
            handle, sibling_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".utf8.tmp",
                dir=path.parent,
            )
            os.close(handle)
            sibling_path = Path(sibling_name)
            try:
                sibling_path.write_bytes(updated)
                os.replace(sibling_path, path)
            finally:
                sibling_path.unlink(missing_ok=True)
            converted.append(path)
    except Exception:
        for path in reversed(converted):
            shutil.copy2(backup_dir / backup_relative_path(path), path)
        raise

    for path in evidence_paths:
        raw = path.read_bytes()
        if not raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError(f"{path}: UTF-8 BOM is still missing")
        raw.decode("utf-8-sig")
        columns, _, (_, bom, newline) = read_csv_file(path)
        if columns != TARGET_COLUMNS or not bom or newline != "\r\n":
            raise ValueError(f"{path}: encoding/schema/newline verification failed")

    print(f"Converted {len(targets)} files to UTF-8 with BOM.", flush=True)
    print(f"Backup directory: {backup_dir}", flush=True)
    return backup_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--apply",
        action="store_true",
        help="write the validated changes; otherwise perform a dry run",
    )
    mode.add_argument(
        "--verify-final",
        type=Path,
        metavar="BACKUP_DIR",
        help="audit the final 25-column dataset without modifying files",
    )
    mode.add_argument(
        "--normalize-utf8",
        action="store_true",
        help="convert the four non-BOM MaxSpdlim evidence chains to UTF-8 with BOM",
    )
    args = parser.parse_args()

    evidence_paths = sorted(
        path
        for root in ROOTS
        for path in root.rglob("*_EvidenceChain.csv")
    )
    print(f"Discovered {len(evidence_paths)} MaxSpdlim evidence chains.", flush=True)
    if len(evidence_paths) != 1335:
        raise ValueError(f"expected 1335 events, found {len(evidence_paths)}")

    if args.verify_final:
        verify_final_dataset(evidence_paths, args.verify_final)
        return 0
    if args.normalize_utf8:
        normalize_utf8(evidence_paths)
        return 0

    items = []
    errors = []
    for index, evidence_path in enumerate(evidence_paths, start=1):
        try:
            items.append(preflight_event(evidence_path))
        except Exception as exc:
            errors.append(f"{evidence_path}: {exc}")
        if index % 100 == 0 or index == len(evidence_paths):
            print(
                f"Preflight {index}/{len(evidence_paths)}; errors={len(errors)}",
                flush=True,
            )
    if errors:
        print("\n".join(errors[:50]))
        raise ValueError(f"preflight failed for {len(errors)} event(s)")

    counts = {
        "events": len(items),
        "source_schemas": dict(Counter(item["source_schema"] for item in items)),
        "map_classes": dict(Counter(item["map_class"] for item in items)),
        "vehicle_type_sign_events": sum(item["vehicle_type_sign"] for item in items),
        "description_values": len(
            {item["description"] for item in items if item["description"]}
        ),
        "encodings": dict(Counter(item["encoding"] for item in items)),
        "bom_files": sum(item["bom"] for item in items),
        "newlines": dict(Counter(repr(item["newline"]) for item in items)),
    }
    print("SUMMARY_JSON=" + json.dumps(counts, ensure_ascii=False), flush=True)

    if not args.apply:
        print("Dry run complete; no files changed.", flush=True)
        return 0

    backup_dir = apply_outputs(items)
    print(f"Applied {len(items)} evidence and {len(items)} JSON updates.", flush=True)
    print(f"Backup directory: {backup_dir}", flush=True)
    print("Verifying written files...", flush=True)
    verify_written(items)
    print("Post-write verification passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
