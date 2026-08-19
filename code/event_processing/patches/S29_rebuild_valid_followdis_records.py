"""Rebuild canonical FollowDis record.json files from their EvidenceChain.csv files."""

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
    Path(r"Z:\HongqiData\Nanjing_valid\03_FollowDis"),
    Path(r"Z:\HongqiData\Changchun_valid\03_FollowDis"),
)
ARTICLES = {
    "IMR_80.1": (
        "trigger_IMR_80_1",
        "com_IMR_80_1",
        "At 100 km/h or more on an expressway, a vehicle shall keep at least 100 m from the vehicle ahead.",
    ),
    "IMR_80.2": (
        "trigger_IMR_80_2",
        "com_IMR_80_2",
        "Below 100 km/h on an expressway, a vehicle shall keep at least 50 m from the vehicle ahead.",
    ),
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


def transition_status(states: list[str]) -> str:
    changes = compressed(states)
    if changes[0] == "Compliance" and len(changes) == 1:
        return "Compliance"
    if changes[0] == "Violation" and len(changes) == 1:
        return "Violation"
    return "Compliance→Violation" if changes[0] == "Compliance" else "Violation→Compliance"


def read_rows(path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open(encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    raise UnicodeError(f"cannot decode {path}")


def labels(rows: list[dict[str, str]], column: str, mapping: dict[int, str]) -> list[str]:
    result: list[str] = []
    for row in rows:
        value = number(row.get(column))
        if not math.isfinite(value):
            continue
        label = mapping.get(int(round(value)), f"unknown({int(round(value))})")
        if label not in result:
            result.append(label)
    return result


def speed_range(rows: list[dict[str, str]]) -> dict[str, int | float | None]:
    values = [number(row.get("Ego_velocity")) * 3.6 for row in rows]
    values = [value for value in values if math.isfinite(value)]
    return {"min": rounded(min(values)), "max": rounded(max(values))} if values else {"min": None, "max": None}


def threshold_changes(rows: list[dict[str, str]]) -> list[int | float]:
    result: list[int | float] = []
    for row in rows:
        value = number(row.get("Thres_Dis_FV"))
        if not math.isfinite(value) or value <= 0:
            continue
        value = rounded(value)
        if not result or value != result[-1]:
            result.append(value)
    return result


def frame_state(row: dict[str, str]) -> tuple[bool, str | None]:
    triggered = False
    states: list[str] = []
    for trigger_column, compliance_column, _ in ARTICLES.values():
        if number(row.get(trigger_column)) == 0:
            continue
        triggered = True
        compliance = int(number(row.get(compliance_column)))
        if compliance == -1:
            states.append("Violation")
        elif compliance == 1:
            states.append("Compliance")
    if not states:
        return triggered, None
    return triggered, "Violation" if "Violation" in states else "Compliance"


def summarize_status(rows: list[dict[str, str]]) -> tuple[dict[str, str], list[bool], list[str | None]]:
    article_status: dict[str, str] = {}
    trigger_mask: list[bool] = []
    states: list[str | None] = []
    for article, (trigger_column, compliance_column, _) in ARTICLES.items():
        article_states = []
        for row in rows:
            if number(row.get(trigger_column)) == 0:
                continue
            compliance = int(number(row.get(compliance_column)))
            if compliance == 1:
                article_states.append("Compliance")
            elif compliance == -1:
                article_states.append("Violation")
        if article_states:
            article_status[article] = transition_status(article_states)
    for row in rows:
        triggered, state = frame_state(row)
        trigger_mask.append(triggered)
        states.append(state)
    if not article_status:
        raise ValueError("no triggered compliance decision")
    return article_status, trigger_mask, states


def anchor(rows: list[dict[str, str]], trigger_mask: list[bool], states: list[str | None]) -> tuple[str, list[str]]:
    active = [index for index, triggered in enumerate(trigger_mask) if triggered]
    if not active:
        raise ValueError("no triggered frame")
    if active[0] > 0 and active[-1] < len(rows) - 1:
        return "interval", [format_seconds(number(rows[active[0]].get("event_time"))), format_seconds(number(rows[active[-1]].get("event_time")))]
    valid = [(index, state) for index, state in enumerate(states) if state is not None]
    timeline = [state for _, state in valid]
    status = transition_status(timeline)
    if status == "Compliance":
        return "all_compliance", ["3.01 s"]
    if status == "Violation":
        return "all_violation", ["3.01 s"]
    first_state = timeline[0]
    switch_index = next(index for index, state in valid if state != first_state)
    kind = "compliance_to_violation" if first_state == "Compliance" else "violation_to_compliance"
    return kind, [format_seconds(number(rows[switch_index].get("event_time")))]


def minimum_distance_evidence(rows: list[dict[str, str]]) -> dict[str, int | float | None]:
    candidates = [
        (index, number(row.get("Dis_FV"))) for index, row in enumerate(rows)
        if math.isfinite(number(row.get("Dis_FV"))) and number(row.get("Dis_FV")) >= 0
    ]
    if not candidates:
        return {"Minimum_front_vehicle_distance_m": None, "Required_following_distance_at_minimum_m": None, "Ego_speed_at_minimum_distance_kph": None, "Time_of_minimum_distance_s": None}
    index, distance = min(candidates, key=lambda item: item[1])
    row = rows[index]
    return {
        "Minimum_front_vehicle_distance_m": rounded(distance),
        "Required_following_distance_at_minimum_m": rounded(number(row.get("Thres_Dis_FV"))),
        "Ego_speed_at_minimum_distance_kph": rounded(number(row.get("Ego_velocity")) * 3.6),
        "Time_of_minimum_distance_s": rounded(number(row.get("event_time"))),
    }


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
    rows = read_rows(evidence_path)
    if not rows:
        raise ValueError("empty evidence chain")
    article_status, trigger_mask, states = summarize_status(rows)
    anchor_type, anchor_time = anchor(rows, trigger_mask, states)
    article_ids = list(article_status)
    has_violation = any("Violation" in status for status in article_status.values())
    all_compliance = all(status == "Compliance" for status in article_status.values())
    label = "Violation" if has_violation else "Compliance" if all_compliance else "Unknown"
    evidence = {
        "Article_status": article_status,
        **minimum_distance_evidence(rows),
        "Required_following_distance_m": threshold_changes(rows),
        "Ego_speed_kph": speed_range(rows),
        "Road_types": labels(rows, "Road_type", ROAD_TYPES),
        "Lane_types": labels(rows, "Lane_type", LANE_TYPES),
        "Congestion": any(number(row.get("Congestion")) != 0 for row in rows),
    }
    low, high = evidence["Ego_speed_kph"]["min"], evidence["Ego_speed_kph"]["max"]
    speed_text = str(low) if low == high else f"{low}-{high}"
    threshold_text = ", ".join(str(value) for value in evidence["Required_following_distance_m"]) or "unknown"
    scenario = f"The ego vehicle traveled at {speed_text} km/h. The required following-distance value sequence was {threshold_text} m."
    record = {
        "Location": current.get("Location", ""),
        "Date": current.get("Date", ""),
        "Time": current.get("Time", ""),
        "Timestamp": timestamp(current.get("Date", ""), current.get("Time", ""), current.get("Timestamp")),
    }
    if "DrivingMode" in current:
        record["DrivingMode"] = current["DrivingMode"]
    record.update({
        "Article": {"ID": " & ".join(article_ids), "Text": [ARTICLES[article][2] for article in article_ids]},
        "EventAnchor": {"Anchor_type": anchor_type, "anchor_time": anchor_time},
        "Evidence": evidence,
        "Result": {
            "Compliance_label": label,
            "Violation_reason": "The ego vehicle did not maintain the required following distance." if label == "Violation" else "None",
            "Driving_suggestion": "Increase the following distance to the applicable requirement." if label == "Violation" else "Maintain the observed following distance.",
            "Scenario_description": scenario,
        },
    })
    return record


def records(root: Path, segment_glob: str) -> list[Path]:
    return sorted(root.glob(f"{segment_glob}/event_*/FollowDis_event_*_record.json"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write rebuilt canonical record.json files.")
    parser.add_argument("--root", type=Path, action="append", help="Override a dataset root; may be repeated.")
    parser.add_argument("--segment-glob", default="*", help="Limit processing to matching segment directories.")
    args = parser.parse_args()
    roots = tuple(args.root) if args.root else ROOTS
    counts: Counter[str] = Counter()
    updates: list[tuple[Path, dict]] = []
    for root in roots:
        for record_path in records(root, args.segment_glob):
            record = build_record(record_path)
            counts[record["EventAnchor"]["Anchor_type"]] += 1
            updates.append((record_path, record))
    if args.apply:
        for record_path, record in updates:
            temporary = record_path.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            os.replace(temporary, record_path)
    print(json.dumps({"records": len(updates), "written": bool(args.apply), "anchor_types": counts}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
