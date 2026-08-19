"""Rebuild canonical MaxSpdlim record.json files from their EvidenceChain.csv files."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path


ROOTS = (
    Path(r"Z:\HongqiData\Nanjing_valid\01_MaxSpdlim"),
    Path(r"Z:\HongqiData\Changchun_valid\01_MaxSpdlim"),
)
ARTICLES = {
    "IMR_45.1": (
        "trigger_IMR_45_1",
        "com_IMR_45_1",
        "Vehicles shall not exceed speed limits indicated by traffic signs or markings.",
    ),
    "IMR_46.3": ("trigger_IMR_46_3", "com_IMR_46_3", "A motor vehicle shall not exceed 30 km/h on a sharp turn."),
    "IMR_46.4": ("trigger_IMR_46_4", "com_IMR_46_4", "A motor vehicle shall not exceed 30 km/h on a narrow road or bridge."),
    "IMR_46.5": ("trigger_IMR_46_5", "com_IMR_46_5", "A motor vehicle shall not exceed 30 km/h while descending a steep slope."),
    "IMR_78.1": ("trigger_IMR_78_1", "com_IMR_78_1", "A small passenger vehicle on an expressway shall not exceed 120 km/h."),
    "IMR_78.3": ("trigger_IMR_78_3", "com_IMR_78_3", "A vehicle shall follow the maximum speed indicated by the road speed-limit sign."),
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
EXPRESS_ROAD_TYPES = {1, 2, 34}
EXPRESS_LANE_TYPES = {1, 8}


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


def format_range(value_range: dict[str, int | float | None]) -> str:
    low, high = value_range["min"], value_range["max"]
    return str(low) if low == high else f"{low}-{high}"


def compressed(values: list[str]) -> list[str]:
    return [value for index, value in enumerate(values) if not index or value != values[index - 1]]


def status_from_states(states: list[str]) -> str:
    changes = compressed(states)
    if changes[0] == "Compliance" and len(changes) == 1:
        return "Compliance"
    if changes[0] == "Violation" and len(changes) == 1:
        return "Violation"
    if changes[0] == "Compliance":
        return "Compliance→Violation"
    return "Violation→Compliance"


def labels(frames: list[dict], key: str, mapping: dict[int, str]) -> list[str]:
    result: list[str] = []
    for frame in frames:
        value = frame[key]
        if not math.isfinite(value):
            continue
        label = mapping.get(int(round(value)), f"unknown({int(round(value))})")
        if label not in result:
            result.append(label)
    return result


def limit_changes(frames: list[dict]) -> list[int | float]:
    values: list[int | float] = []
    for frame in frames:
        value = frame["limit"]
        if not math.isfinite(value) or value <= 0:
            continue
        value = rounded(value)
        if not values or value != values[-1]:
            values.append(value)
    return values


def speed_range(rows: list[dict[str, str]]) -> dict[str, int | float | None]:
    values = [number(row.get("Ego_velocity")) * 3.6 for row in rows]
    values = [value for value in values if math.isfinite(value)]
    return {"min": rounded(min(values)), "max": rounded(max(values))} if values else {"min": None, "max": None}


def derived_frames(rows: list[dict[str, str]], map_rows: list[dict[str, str]]) -> list[dict]:
    if len(rows) != len(map_rows):
        raise ValueError(f"evidence/map row count mismatch: {len(rows)} != {len(map_rows)}")
    frames: list[dict] = []
    for row, map_row in zip(rows, map_rows):
        lane_count = int(number(map_row.get("LaneNumSameDirection")) or 0)
        limits = [
            number(map_row.get(f"LaneMaxSpdlim_{lane}"))
            for lane in range(1, min(max(lane_count, 0), 5) + 1)
        ]
        limits = [value for value in limits if math.isfinite(value) and value > 0]
        ego_lane = int(number(map_row.get("EgoLaneIndex")) or 0)
        limit = number(map_row.get(f"LaneMaxSpdlim_{ego_lane}")) if ego_lane else math.nan
        if not math.isfinite(limit) or limit <= 0:
            limit = min(limits) if limits else math.nan
        road = number(map_row.get("Road_type"))
        lane = number(map_row.get("Lane_type_CurrentLane"))
        speed = number(row.get("Ego_velocity")) * 3.6
        compliance = "Violation" if math.isfinite(limit) and speed > limit else "Compliance"
        road_code = int(road) if math.isfinite(road) else 0
        lane_code = int(lane) if math.isfinite(lane) else 0
        if road_code in EXPRESS_ROAD_TYPES and lane_code in EXPRESS_LANE_TYPES:
            articles = ["IMR_78.1" if limits and all(value == 120 for value in limits) else "IMR_78.3"]
        else:
            articles = [
                article for article, (trigger, _, _) in ARTICLES.items()
                if article in {"IMR_46.3", "IMR_46.4", "IMR_46.5"} and number(row.get(trigger)) != 0
            ] or ["IMR_45.1"]
        frames.append({"articles": articles, "status": compliance, "time": number(row.get("event_time")), "limit": limit, "road": road, "lane": lane})
    return frames


def event_states(frames: list[dict]) -> tuple[dict[str, str], list[str], float]:
    article_status: dict[str, str] = {}
    timeline: list[str] = []
    timeline_times: list[float] = []
    for article in ARTICLES:
        states = [frame["status"] for frame in frames if article in frame["articles"]]
        if states:
            article_status[article] = status_from_states(states)

    for frame in frames:
        timeline.append(frame["status"])
        timeline_times.append(frame["time"])

    if not article_status or not timeline:
        raise ValueError("no triggered compliance decision")
    overall = status_from_states(timeline)
    if "→" not in overall:
        return article_status, timeline, 3.01
    first_state = timeline[0]
    switch_index = next(index for index, state in enumerate(timeline) if state != first_state)
    return article_status, timeline, timeline_times[switch_index]


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
        if isinstance(current, str):
            return current
        return ""


def read_rows(evidence_path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with evidence_path.open(encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"cannot decode {evidence_path}")


def build_record(record_path: Path, evidence_path: Path, location: str) -> dict:
    current = json.loads(record_path.read_text(encoding="utf-8-sig"))
    rows = read_rows(evidence_path)
    map_path = evidence_path.with_name(evidence_path.name.replace("_EvidenceChain.csv", "_MapInfo.csv"))
    map_rows = read_rows(map_path)
    if not rows:
        raise ValueError("empty evidence chain")

    frames = derived_frames(rows, map_rows)
    article_status, timeline, anchor_time = event_states(frames)
    article_ids = list(article_status)
    has_violation = any("Violation" in status for status in article_status.values())
    all_compliance = all(status == "Compliance" for status in article_status.values())
    label = "Violation" if has_violation else "Compliance" if all_compliance else "Unknown"
    anchor_type = {
        "Compliance": "all_compliance",
        "Violation": "all_violation",
        "Compliance→Violation": "compliance_to_violation",
        "Violation→Compliance": "violation_to_compliance",
    }[status_from_states(timeline)]
    reasons = (
        "The ego vehicle exceeded the default maximum speed limit of 120 km/h"
        if "IMR_78.1" in article_ids
        else "The ego vehicle exceeded the maximum speed indicated by a traffic sign or map speed limit"
    )
    date_text = current.get("Date", "")
    time_text = current.get("Time", "")
    evidence = {
        "Article_status": article_status,
        "Ego_speed_kph": speed_range(rows),
        "Applicable_max_speed_limit_kph": limit_changes(frames),
        "Road_types": labels(frames, "road", ROAD_TYPES),
        "Lane_types": labels(frames, "lane", LANE_TYPES),
        "Inside_speed_limit_sign_area": "IMR_78.1" not in article_ids,
    }
    road_text = ", ".join(evidence["Road_types"]) or "unknown"
    lane_text = ", ".join(evidence["Lane_types"]) or "unknown"
    limit_text = ", ".join(str(value) for value in evidence["Applicable_max_speed_limit_kph"]) or "unknown"
    scenario = (
        f"The ego vehicle traveled on {lane_text} of {road_text} at "
        f"{format_range(evidence['Ego_speed_kph'])} km/h. The applicable maximum speed limit "
        f"value sequence was {limit_text} km/h."
    )
    return {
        "Location": current.get("Location") or location,
        "Date": date_text,
        "Time": time_text,
        "Timestamp": timestamp(date_text, time_text, current.get("Timestamp")),
        "Article": {
            "ID": " & ".join(article_ids),
            "Text": [ARTICLES[article][2] for article in article_ids],
        },
        "EventAnchor": {"Anchor_type": anchor_type, "anchor_time": format_seconds(anchor_time)},
        "Evidence": evidence,
        "Result": {
            "Compliance_label": label,
            "Violation_reason": reasons if label == "Violation" else "None",
            "Driving_suggestion": "Reduce speed to the applicable maximum speed limit." if label == "Violation" else "Maintain speed within the applicable maximum speed limit.",
            "Scenario_description": scenario,
        },
    }


def canonical_records(root: Path, segment_glob: str) -> list[Path]:
    return sorted(root.glob(f"{segment_glob}/event_*/MaxSpdlim_event_*_record.json"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write rebuilt canonical record.json files.")
    parser.add_argument("--root", type=Path, action="append", help="Override a dataset root; may be repeated.")
    parser.add_argument("--segment-glob", default="*", help="Limit processing to matching segment directories.")
    args = parser.parse_args()
    roots = tuple(args.root) if args.root else ROOTS
    report: Counter[str] = Counter()
    updates: list[tuple[Path, dict]] = []
    for root in roots:
        location = "China, Changchun" if "changchun" in root.name.lower() else "China, Nanjing"
        for record_path in canonical_records(root, args.segment_glob):
            evidence_path = record_path.with_name(record_path.name.replace("_record.json", "_EvidenceChain.csv"))
            if not evidence_path.exists():
                raise FileNotFoundError(evidence_path)
            record = build_record(record_path, evidence_path, location)
            report[record["EventAnchor"]["Anchor_type"]] += 1
            updates.append((record_path, record))
    if args.apply:
        for record_path, record in updates:
            temporary = record_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, record_path)
    print(json.dumps({"records": len(updates), "written": bool(args.apply), "anchor_types": report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
