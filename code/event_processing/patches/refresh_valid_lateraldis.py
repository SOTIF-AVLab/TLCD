from __future__ import annotations

import argparse
import csv
import io
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROAD_TYPES = {
    0: "unknown", 1: "multiple_carriageway", 2: "single_carriageway",
    4: "service_road", 6: "ramp_entry", 7: "ramp_exit", 9: "jct",
    18: "service_area_approach", 19: "service_area_jct",
    20: "service_area_approach_jct", 27: "toll_booth",
    31: "motorway_entry_ramp", 32: "motorway_exit_ramp", 34: "tunnel",
    37: "toll_area", 38: "rest_area",
}
LANE_TYPES = {
    0: "unknown", 1: "regular_lane", 2: "deceleration_lane",
    3: "acceleration_lane", 4: "compound_lane", 5: "drivable_parking_lane",
    8: "slow_lane", 9: "drivable_shoulder_lane", 10: "shoulder_lane",
    12: "regulated_access_lane", 13: "variable_driving_lane",
    14: "emergency_strip", 15: "other_lane",
}
ARTICLE_TEXT = "A vehicle should maintain more than 1.5 m lateral clearance from vehicles on either side."


def number(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], fields: list[str]) -> None:
    buffer = io.StringIO(newline="")
    with buffer:
        writer = csv.DictWriter(buffer, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        write_text(path, buffer.getvalue())


def write_text(path: Path, text: str) -> None:
    try:
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        return
    except PermissionError:
        pass
    with path.open("r+", encoding="utf-8", newline="") as handle:
        handle.seek(0)
        handle.write(text)
        handle.truncate()


def labels(rows: list[dict[str, str]], column: str, mapping: dict[int, str]) -> list[str]:
    result: list[str] = []
    for row in rows:
        code = int(round(number(row.get(column, "0"))))
        label = mapping.get(code, f"code_{code}")
        if label not in result:
            result.append(label)
    return result


def range_kph(rows: list[dict[str, str]]) -> dict[str, float] | None:
    values = [number(row.get("Ego_velocity", "nan"), float("nan")) * 3.6 for row in rows]
    values = [value for value in values if value == value]
    if not values:
        return None
    return {"min": round(min(values), 3), "max": round(max(values), 3)}


def format_time(value: str) -> str:
    return f"{number(value):.2f}".rstrip("0").rstrip(".") + "s"


def state(rows: list[dict[str, str]]) -> tuple[str, list[str], str]:
    trigger = [number(row.get("trigger_OSP_8_2_1")) != 0 for row in rows]
    compliance = [int(number(row.get("com_OSP_8_2_1"))) for row in rows]
    active = [
        is_triggered or value != 0 for is_triggered, value in zip(trigger, compliance)
    ]
    active_indices = [index for index, value in enumerate(active) if value]
    if not active_indices:
        return "all_compliance", ["3.01s"], "Compliance"

    if active_indices[0] > 0 or active_indices[-1] < len(rows) - 1:
        return "interval", [format_time(rows[active_indices[0]]["event_time"]), format_time(rows[active_indices[-1]]["event_time"])], status(compliance)

    decisions = [(index, compliance[index]) for index in active_indices if compliance[index] != 0]
    decision_values = [value for _, value in decisions]
    if all(value < 0 for value in decision_values):
        return "all_violation", ["3.01s"], "Violation"
    if all(value > 0 for value in decision_values):
        return "all_compliance", ["3.01s"], "Compliance"
    changes = [index for index in range(1, len(decisions)) if decisions[index - 1][1] != decisions[index][1]]
    if not changes:
        return "all_compliance", ["3.01s"], "Compliance"

    change = changes[0]
    anchor_type = "compliance_to_violation" if decisions[change - 1][1] > 0 else "violation_to_compliance"
    decision_index = decisions[change][0]
    return anchor_type, [format_time(rows[decision_index]["event_time"])], status(compliance)


def status(compliance: list[int]) -> str:
    nonzero = [value for value in compliance if value != 0]
    if any(value < 0 for value in nonzero) and any(value > 0 for value in nonzero):
        first_negative = next(index for index, value in enumerate(nonzero) if value < 0)
        first_positive = next(index for index, value in enumerate(nonzero) if value > 0)
        return "compliance_to_violation" if first_positive < first_negative else "violation_to_compliance"
    return "Violation" if any(value < 0 for value in nonzero) else "Compliance"


def evidence(rows: list[dict[str, str]]) -> dict[str, object]:
    left = [number(row["Dis_LV"]) for row in rows if number(row["Dis_LV"]) != -1]
    right = [number(row["Dis_RV"]) for row in rows if number(row["Dis_RV"]) != -1]
    offsets = [abs(number(row["Dis_centerline"])) for row in rows]
    compliance = [int(number(row["com_OSP_8_2_1"])) for row in rows]
    return {
        "Article_status": {"OSP_8.2.1": status(compliance)},
        "Minimum_left_vehicle_distance_m": round(min(left), 3) if left else None,
        "Minimum_right_vehicle_distance_m": round(min(right), 3) if right else None,
        "Required_lateral_distance_m": number(rows[0].get("Thres_min_LatDis", "1.5")),
        "Maximum_abs_centerline_offset_m": round(max(offsets), 3) if offsets else None,
        "Centerline_offset_threshold_m": number(rows[0].get("Thres_Offset_centerline", "0.375")),
        "Left_lane_line_present": any(number(row["Exist_LeftLine"]) != 0 for row in rows),
        "Right_lane_line_present": any(number(row["Exist_RightLine"]) != 0 for row in rows),
        "Lateral_avoidance_observed": any(number(row["Is_Lat_avoidance"]) != 0 for row in rows),
        "Ego_speed_kph": range_kph(rows),
        "Road_types": labels(rows, "Road_type", ROAD_TYPES),
        "Lane_types": labels(rows, "Lane_type", LANE_TYPES),
    }


def update_evidence(path: Path, write: bool) -> tuple[list[dict[str, str]], int, int]:
    rows = read_csv(path)
    ego_rows = read_csv(path.with_name(path.name.replace("_EvidenceChain.csv", "_EgoInfo.csv")))
    map_rows = read_csv(path.with_name(path.name.replace("_EvidenceChain.csv", "_MapInfo.csv")))
    if not (len(rows) == len(ego_rows) == len(map_rows)):
        raise ValueError(f"row-count mismatch: {path}")

    avoidance_changes = 0
    compliance_changes = 0
    for row, ego_row, map_row in zip(rows, ego_rows, map_rows):
        left_vehicle = number(row["Dis_LV"]) != -1
        right_vehicle = number(row["Dis_RV"]) != -1
        lines_exist = number(row["Exist_LeftLine"]) != 0 and number(row["Exist_RightLine"]) != 0
        offset = number(row["Dis_centerline"])
        threshold = number(row.get("Thres_Offset_centerline", "0.375"))
        avoidance = lines_exist and ((left_vehicle and offset > threshold) or (right_vehicle and offset < -threshold))
        if int(number(row["Is_Lat_avoidance"])) != int(avoidance):
            avoidance_changes += 1
        row["Is_Lat_avoidance"] = str(int(avoidance))
        if avoidance and int(number(row["com_OSP_8_2_1"])) != 1:
            row["com_OSP_8_2_1"] = "1"
            compliance_changes += 1
        row["Ego_velocity"] = ego_row["Ego_velocity"]
        row["Road_type"] = map_row["Road_type"]
        row["Lane_type"] = map_row["Lane_type_CurrentLane"]

    fields = list(rows[0])
    for field in ("Ego_velocity", "Road_type", "Lane_type"):
        if field not in fields:
            fields.append(field)
    if write:
        write_csv(path, rows, fields)
    return rows, avoidance_changes, compliance_changes


def update_record(path: Path, rows: list[dict[str, str]], location: str) -> str:
    record_path = path.with_name(path.name.replace("_EvidenceChain.csv", "_record.json"))
    prior = json.loads(record_path.read_text(encoding="utf-8")) if record_path.exists() else {}
    backup_path = record_path.with_name(record_path.name.replace("_record.json", "_record_before_model_result.json"))
    backup = json.loads(backup_path.read_text(encoding="utf-8")) if backup_path.exists() else {}
    old_time = prior.get("Time", "")
    if isinstance(old_time, dict):
        old_time = old_time.get("Range", "")
    timestamp = prior.get("Timestamp", prior.get("Time", {}).get("Timestamp", "") if isinstance(prior.get("Time"), dict) else "")
    if isinstance(timestamp, dict):
        timestamp_text = f"{timestamp.get('t_start', '')} -- {timestamp.get('t_end', '')}"
    else:
        timestamp_text = str(timestamp)
    if timestamp_text.strip(" -") == "":
        start_text, end_text = (part.strip() for part in old_time.split("--", 1))
        tz = timezone(timedelta(hours=8))
        start = datetime.strptime(f"{prior.get('Date', '')} {start_text}", "%Y-%m-%d %H:%M:%S.%f")
        end = datetime.strptime(f"{prior.get('Date', '')} {end_text}", "%Y-%m-%d %H:%M:%S.%f")
        timestamp_text = f"{int(start.replace(tzinfo=tz).timestamp() * 1000)} -- {int(end.replace(tzinfo=tz).timestamp() * 1000)}"
    driving_mode = prior.get("DrivingMode") or backup.get("DrivingMode") or "Manual Driving"

    anchor_type, anchor_time, article_status = state(rows)
    summary = evidence(rows)
    compliance_label = "Violation" if article_status in {"Violation", "compliance_to_violation", "violation_to_compliance"} else "Compliance"
    record = {
        "Location": location,
        "Date": prior.get("Date", ""),
        "Time": old_time,
        "Timestamp": timestamp_text,
        "DrivingMode": driving_mode,
        "Article": {"ID": "OSP_8.2.1", "Text": [ARTICLE_TEXT]},
        "EventAnchor": {"Anchor_type": anchor_type, "anchor_time": anchor_time},
        "Evidence": summary,
        "Result": {
            "Compliance_label": compliance_label,
            "Violation_reason": "The ego vehicle did not maintain the required lateral safety distance from an adjacent vehicle." if compliance_label == "Violation" else "None",
            "Driving_suggestion": "Maintain a lateral distance of at least 1.5 meters from adjacent vehicles." if compliance_label == "Violation" else "Maintain the observed compliant driving behavior.",
        },
    }
    write_text(record_path, json.dumps(record, ensure_ascii=False, indent=2))
    return anchor_type


def refresh(root: Path, dry_run: bool, records_only: bool) -> Counter[str]:
    location = "China, Nanjing" if "nanjing" in str(root).lower() else "China, Changchun"
    counts: Counter[str] = Counter()
    print(f"{root}: scanning", flush=True)
    paths = list(root.rglob("LateralDis_event_*_EvidenceChain.csv"))
    print(f"{root}: found {len(paths)} evidence chains", flush=True)
    for index, path in enumerate(paths, start=1):
        if records_only:
            rows = read_csv(path)
            avoidance_changes = 0
            compliance_changes = 0
        else:
            rows, avoidance_changes, compliance_changes = update_evidence(path, write=not dry_run)
        counts["evidence"] += 1
        counts["avoidance_frames_changed"] += avoidance_changes
        counts["compliance_frames_changed"] += compliance_changes
        if dry_run:
            continue
        counts[update_record(path, rows, location)] += 1
        counts["records"] += 1
        if index % 100 == 0:
            print(f"{root}: refreshed {index}/{len(paths)}", flush=True)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh valid lateral-distance evidence chains and records.")
    parser.add_argument("roots", nargs="+", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--records-only", action="store_true")
    args = parser.parse_args()
    total: Counter[str] = Counter()
    for root in args.roots:
        counts = refresh(root, args.dry_run, args.records_only)
        total.update(counts)
        print(f"{root}: {dict(sorted(counts.items()))}")
    print(f"total: {dict(sorted(total.items()))}")


if __name__ == "__main__":
    main()
