"""Rebuild canonical lane-change record.json files from their EvidenceChain.csv files."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s7_record_json"))
import s7_event_json as s7


ROOTS = (
    Path(r"Z:\HongqiData\Nanjing_valid\05_LaneChange"),
    Path(r"Z:\HongqiData\Changchun_valid\05_LaneChange"),
)
ROAD_TYPES = {
    0: "unknown", 1: "multiple_carriageway", 2: "single_carriageway", 4: "service_road",
    6: "ramp_entry", 7: "ramp_exit", 9: "jct", 18: "service_area_approach",
    19: "service_area_jct", 20: "service_area_approach_jct", 27: "toll_booth",
    31: "motorway_entry_ramp", 32: "motorway_exit_ramp", 34: "tunnel",
    37: "toll_area", 38: "rest_area",
}
LANE_TYPES = {
    0: "unknown", 1: "regular_lane", 2: "deceleration_lane", 3: "acceleration_lane",
    4: "compound_lane", 5: "drivable_parking_lane", 8: "slow_lane",
    9: "drivable_shoulder_lane", 10: "shoulder_lane", 12: "regulated_access_lane",
    13: "variable_driving_lane", 14: "emergency_strip", 15: "other_lane",
}
ARTICLE_TEXT = "A vehicle changing lanes shall not interfere with vehicles traveling normally in the relevant lane."


def number(value: object) -> float:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError):
        return math.nan
    return result if math.isfinite(result) else math.nan


def rounded(value: float, digits: int = 3) -> int | float | None:
    if not math.isfinite(value):
        return None
    result = round(value, digits)
    return int(result) if result.is_integer() else result


def format_seconds(value: float) -> str:
    return f"{rounded(value, 2)} s"


def compressed(values: list[str]) -> list[str]:
    return [value for index, value in enumerate(values) if not index or value != values[index - 1]]


def article_status(rows: list[dict[str, str]]) -> str:
    states: list[str] = []
    for row in rows:
        if number(row.get("trigger_IMR_44_1")) == 0:
            continue
        value = int(number(row.get("com_IMR_44_1")))
        if value == 1:
            states.append("Compliance")
        elif value == -1:
            states.append("Violation")
    if not states:
        raise ValueError("no triggered compliance decision")
    changes = compressed(states)
    if changes[0] == "Compliance" and len(changes) == 1:
        return "Compliance"
    if changes[0] == "Violation" and len(changes) == 1:
        return "Violation"
    return "Compliance→Violation" if changes[0] == "Compliance" else "Violation→Compliance"


def labels(rows: list[dict[str, str]], mask: list[bool], column: str, mapping: dict[int, str]) -> list[str]:
    result: list[str] = []
    for row, active in zip(rows, mask):
        if not active:
            continue
        value = number(row.get(column))
        if not math.isfinite(value):
            continue
        label = mapping.get(int(round(value)), f"unknown({int(round(value))})")
        if label not in result:
            result.append(label)
    return result


def timestamp(date_text: str, time_text: str, current: object) -> str:
    try:
        start_text, end_text = (part.strip() for part in time_text.split("--", 1))
        start = datetime.strptime(f"{date_text} {start_text}", "%Y-%m-%d %H:%M:%S.%f")
        end = datetime.strptime(f"{date_text} {end_text}", "%Y-%m-%d %H:%M:%S.%f")
        tz = timezone(timedelta(hours=8))
        return f"{int(start.replace(tzinfo=tz).timestamp() * 1000)} -- {int(end.replace(tzinfo=tz).timestamp() * 1000)}"
    except (TypeError, ValueError):
        if isinstance(current, dict) and "t_start" in current and "t_end" in current:
            return f"{current['t_start']} -- {current['t_end']}"
        return current if isinstance(current, str) else ""


def build_record(record_path: Path) -> dict:
    current = json.loads(record_path.read_text(encoding="utf-8-sig"))
    evidence_path = record_path.with_name(record_path.name.replace("_record.json", "_EvidenceChain.csv"))
    rows = s7._enrich_lane_change_context(evidence_path, s7._read_csv(evidence_path))
    trigger_mask = [number(row.get("trigger_IMR_44_1")) != 0 for row in rows]
    indices = [index for index, active in enumerate(trigger_mask) if active]
    if not indices:
        raise ValueError("no lane-change trigger")
    status = article_status(rows)
    evidence, _ = s7._lane_change_evidence(rows, trigger_mask)
    evidence["Article_status"] = {"IMR_44.1": status}
    evidence["Road_types"] = labels(rows, trigger_mask, "Road_type", ROAD_TYPES)
    evidence["Lane_types"] = labels(rows, trigger_mask, "Lane_type", LANE_TYPES)
    evidence = {"Article_status": evidence.pop("Article_status"), **evidence}
    label = "Violation" if "Violation" in status else "Compliance" if status == "Compliance" else "Unknown"
    speed = evidence.get("Ego_speed_kph", {})
    speed_text = str(speed.get("min")) if speed.get("min") == speed.get("max") else f"{speed.get('min')}-{speed.get('max')}"
    scenario = f"The ego vehicle changed lanes at {speed_text} km/h during the recorded trigger interval."
    record = {
        "Location": current.get("Location", ""),
        "Date": current.get("Date", ""),
        "Time": current.get("Time", ""),
        "Timestamp": timestamp(current.get("Date", ""), current.get("Time", ""), current.get("Timestamp")),
    }
    if "DrivingMode" in current:
        record["DrivingMode"] = current["DrivingMode"]
    record.update({
        "Article": {"ID": "IMR_44.1", "Text": [ARTICLE_TEXT]},
        "EventAnchor": {
            "Anchor_type": "trigger_interval",
            "anchor_time": [
                format_seconds(number(rows[indices[0]].get("event_time"))),
                format_seconds(number(rows[indices[-1]].get("event_time"))),
            ],
        },
        "Evidence": evidence,
        "Result": {
            "Compliance_label": label,
            "Violation_reason": "The lane change interfered with the normal travel of another vehicle." if label == "Violation" else "None",
            "Driving_suggestion": "Change lanes only after confirming sufficient safety margins." if label == "Violation" else "Maintain the observed compliant lane-change behavior.",
            "Scenario_description": scenario,
        },
    })
    return record


def records(root: Path, segment_glob: str) -> list[Path]:
    return sorted(root.glob(f"{segment_glob}/event_*/lane_change_event_*_record.json"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write rebuilt canonical record.json files.")
    parser.add_argument("--root", type=Path, action="append", help="Override a dataset root; may be repeated.")
    parser.add_argument("--segment-glob", default="*", help="Limit processing to matching segment directories.")
    args = parser.parse_args()
    roots = tuple(args.root) if args.root else ROOTS
    updates: list[tuple[Path, dict]] = []
    for root in roots:
        for record_path in records(root, args.segment_glob):
            updates.append((record_path, build_record(record_path)))
    if args.apply:
        for record_path, record in updates:
            temporary = record_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, record_path)
    print(json.dumps({"records": len(updates), "written": bool(args.apply)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
