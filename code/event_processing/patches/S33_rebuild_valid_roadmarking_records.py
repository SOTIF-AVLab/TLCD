"""Rebuild canonical road-marking record.json files from EvidenceChain.csv files."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s7_record_json"))
import s7_event_json as s7


ROOTS = (
    Path(r"Z:\HongqiData\Nanjing_valid\07_RoadMarking"),
    Path(r"Z:\HongqiData\Changchun_valid\07_RoadMarking"),
)
ARTICLES = {
    "TSM_4.3.1": ("trigger_TSM_4_3_1", "com_TSM_4_3_1", "A dashed lane line may be crossed briefly only when safety is ensured."),
    "TSM_4.5.2": ("trigger_TSM_4_5_2", "com_TSM_4_5_2", "Vehicles shall not cross a solid lane boundary."),
    "TSM_4.5.3": ("trigger_TSM_4_5_3", "com_TSM_4_5_3", "Vehicles shall follow channelizing lines and shall not drive on or cross them."),
}
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


def status(values: list[str]) -> str:
    changes = compressed(values)
    if changes[0] == "Compliance" and len(changes) == 1:
        return "Compliance"
    if changes[0] == "Violation" and len(changes) == 1:
        return "Violation"
    return "Compliance→Violation" if changes[0] == "Compliance" else "Violation→Compliance"


def article_status(rows: list[dict[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for article, (trigger_column, compliance_column, _) in ARTICLES.items():
        states: list[str] = []
        for row in rows:
            if number(row.get(trigger_column)) == 0:
                continue
            value = int(number(row.get(compliance_column)))
            if value == 1:
                states.append("Compliance")
            elif value == -1:
                states.append("Violation")
        if states:
            result[article] = status(states)
    if not result:
        raise ValueError("no road-marking compliance decision")
    return result


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
    trigger_mask = [any(number(row.get(columns[0])) != 0 for columns in ARTICLES.values()) for row in rows]
    indices = [index for index, active in enumerate(trigger_mask) if active]
    if not indices:
        raise ValueError("no road-marking trigger")
    statuses = article_status(rows)
    evidence, _ = s7._road_marking_evidence(rows, trigger_mask)
    for interaction in evidence["Line_interactions"]:
        interaction.pop("Initial_line_overlap_detected", None)
    evidence["Article_status"] = statuses
    evidence["Road_types"] = labels(rows, trigger_mask, "Road_type", ROAD_TYPES)
    evidence["Lane_types"] = labels(rows, trigger_mask, "Lane_type", LANE_TYPES)
    evidence = {"Article_status": evidence.pop("Article_status"), **evidence}
    label = "Violation" if any("Violation" in value for value in statuses.values()) else "Compliance" if all(value == "Compliance" for value in statuses.values()) else "Unknown"
    record = {
        "Location": current.get("Location", ""),
        "Date": current.get("Date", ""),
        "Time": current.get("Time", ""),
        "Timestamp": timestamp(current.get("Date", ""), current.get("Time", ""), current.get("Timestamp")),
        "DrivingMode": current.get("DrivingMode", "Manual Driving"),
        "Article": {"ID": " & ".join(statuses), "Text": [ARTICLES[article][2] for article in statuses]},
        "EventAnchor": {
            "Anchor_type": "trigger_interval",
            "anchor_time": [format_seconds(number(rows[indices[0]].get("event_time"))), format_seconds(number(rows[indices[-1]].get("event_time")))],
        },
        "Evidence": evidence,
        "Result": {
            "Compliance_label": label,
            "Violation_reason": "The ego vehicle violated the applicable road-marking rule." if label == "Violation" else "None",
            "Driving_suggestion": "Follow the applicable road markings and avoid prohibited line crossings." if label == "Violation" else "Maintain the observed compliant road-marking behavior.",
            "Scenario_description": "The road-marking evidence was summarized from the recorded trigger interval.",
        },
    }
    return record


def records(root: Path, segment_glob: str) -> list[Path]:
    return sorted(root.glob(f"{segment_glob}/event_*/RoadMarking_event_*_record.json"))


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
