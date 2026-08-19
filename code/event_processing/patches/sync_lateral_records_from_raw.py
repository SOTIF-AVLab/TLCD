"""Sync valid LateralDis record timing from the matching raw event record.

Matching deliberately uses only: segment name, event category, and the first/
last EgoInfo speed values.  It does not use event sequence numbers or GNSS.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path


SOURCES = (
    ("Nanjing", Path(r"Z:\HongqiData\Nanjing_valid\04_LateralDis"), Path(r"Z:\HongqiData\Nanjing")),
    ("Changchun", Path(r"Z:\HongqiData\Changchun_valid\04_LateralDis"), Path(r"Z:\HongqiData\Changchun")),
)
CHINA_TZ = timezone(timedelta(hours=8))


def category(path: Path) -> str:
    return path.name.split("_event_", 1)[0]


def normalized_speed(value: str) -> str:
    return str(Decimal(value).normalize())


def speed_ends(path: Path) -> tuple[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    return normalized_speed(rows[0]["Ego_velocity"]), normalized_speed(rows[-1]["Ego_velocity"])


def segment_date(segment: str) -> str:
    match = re.search(r"20\d{6}", segment)
    if not match:
        raise ValueError(f"No YYYYMMDD date in segment: {segment}")
    return match.group()


def time_to_timestamp(date: str, time_text: str) -> str:
    start_text, end_text = (part.strip() for part in time_text.split("--", 1))

    def convert(value: str) -> int:
        for pattern in ("%H:%M:%S.%f", "%H:%M:%S"):
            try:
                return int(datetime.strptime(f"{date} {value}", f"%Y-%m-%d {pattern}")
                           .replace(tzinfo=CHINA_TZ).timestamp() * 1000)
            except ValueError:
                continue
        raise ValueError(f"Unsupported time: {value}")

    return f"{convert(start_text)} -- {convert(end_text)}"


def normalized_timestamp(value: object, date: str, time_text: str) -> str:
    if isinstance(value, dict):
        return f"{value.get('t_start', value.get('start', ''))} -- {value.get('t_end', value.get('end', ''))}"
    if value:
        return str(value)
    return time_to_timestamp(date, time_text)


def raw_candidates(raw_root: Path, segment: str,
                   cache: dict[tuple[Path, str], dict[tuple[str, str, str], list[Path]]]) -> dict[tuple[str, str, str], list[Path]]:
    key = raw_root, segment
    if key not in cache:
        date = segment_date(segment)
        folder = raw_root / date / "zEvent_LateralDis" / segment
        index: dict[tuple[str, str, str], list[Path]] = defaultdict(list)
        if folder.is_dir():
            for candidate in folder.glob("LateralDis_event_*_EgoInfo.csv"):
                index[(category(candidate), *speed_ends(candidate))].append(candidate)
        cache[key] = index
    return cache[key]


def corresponding_record(ego_info: Path) -> Path:
    return Path(str(ego_info).replace("_EgoInfo.csv", "_record.json"))


def run(apply: bool) -> int:
    outcomes = Counter()
    missing_by_city = defaultdict(list)
    cache: dict[tuple[Path, str], dict[tuple[str, str, str], list[Path]]] = {}

    for city, valid_root, raw_root in SOURCES:
        for target_ego in sorted(valid_root.rglob("LateralDis_event_*_EgoInfo.csv")):
            segment = target_ego.parent.parent.name
            key = (segment, category(target_ego), *speed_ends(target_ego))
            matches = raw_candidates(raw_root, segment, cache).get(key[1:], [])
            label = f"{city}:{segment}/{target_ego.parent.name}"
            if len(matches) != 1:
                outcomes[(city, "unmatched" if not matches else "ambiguous")] += 1
                missing_by_city[city].append((label, len(matches)))
                continue

            raw_record_path = corresponding_record(matches[0])
            target_record_path = next(target_ego.parent.glob("*_record.json"), None)
            if not raw_record_path.exists() or target_record_path is None:
                outcomes[(city, "record_missing")] += 1
                missing_by_city[city].append((label, -1))
                continue

            raw = json.loads(raw_record_path.read_text(encoding="utf-8"))
            target = json.loads(target_record_path.read_text(encoding="utf-8"))
            raw_time = raw.get("Time")
            raw_date = raw.get("Date", target.get("Date", ""))
            if not isinstance(raw_time, str) or not raw_date:
                outcomes[(city, "raw_time_missing")] += 1
                missing_by_city[city].append((label, -2))
                continue
            raw_timestamp = normalized_timestamp(raw.get("Timestamp"), raw_date, raw_time)
            raw_mode = raw.get("DrivingMode") or target.get("DrivingMode") or "Manual Driving"
            changed = (target.get("Time") != raw_time or
                       target.get("Timestamp") != raw_timestamp or
                       target.get("DrivingMode") != raw_mode)
            outcomes[(city, "changed" if changed else "already_equal")] += 1
            if apply and changed:
                target["Time"] = raw_time
                target["Timestamp"] = raw_timestamp
                target["DrivingMode"] = raw_mode
                target_record_path.write_text(json.dumps(target, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("mode=apply" if apply else "mode=dry-run")
    for (city, outcome), count in sorted(outcomes.items()):
        print(f"{city}\t{outcome}\t{count}")
    for city, entries in missing_by_city.items():
        print(f"{city} unresolved examples ({len(entries)} total):")
        for label, count in entries[:10]:
            print(f"  {label}\tmatches={count}")
    return sum(count for (_, outcome), count in outcomes.items() if outcome in {"unmatched", "ambiguous", "record_missing", "raw_time_missing"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write matching raw Time/Timestamp/DrivingMode into valid records")
    args = parser.parse_args()
    raise SystemExit(0 if run(args.apply) == 0 else 1)
