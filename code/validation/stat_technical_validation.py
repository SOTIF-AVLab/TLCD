#!/usr/bin/env python3
"""Summarize TLCD candidate, released, excluded and repaired event counts.

Candidate events are counted from non-empty ``event_num`` records in the
selected ``zEvent_*/*_events.csv`` files. Released-event counts are read from
``statistics/01_city_category.csv``. The repair rates are author-supplied
manual-review summaries and are converted to integer counts by conventional
rounding to the nearest event.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INVENTORY = REPOSITORY_ROOT / "statistics" / "01_city_category.csv"
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "statistics" / "technical_validation"

CATEGORY_ORDER = [
    "MaxSpdlim",
    "MinSpdlim",
    "FollowDis",
    "LateralDis",
    "LaneChange",
    "ContinueLaneChange",
    "RoadMarking",
    "Overtake",
]

SELECTED_EVENT_SOURCES = {
    "Changchun": {
        "MaxSpdlim": ["zEvent_MaxSpdlim", "zEvent_MaxSpdlim_sign_1"],
        "MinSpdlim": ["zEvent_MinSpdlim", "zEvent_MinSpdlim_invalid"],
        "FollowDis": ["zEvent_FollowDis"],
        "LateralDis": ["zEvent_LateralDis"],
        "LaneChange": ["zEvent_LaneChange"],
        "ContinueLaneChange": ["zEvent_ContinueLaneChange"],
        "RoadMarking": ["zEvent_RoadMarking"],
        "Overtake": ["zEvent_Overtake"],
    },
    "Nanjing": {
        "MaxSpdlim": [
            "zEvent_MaxSpdlim",
            "zEvent_MaxSpdlim_sign_1",
            "zEvent_MaxSpdlim_sign_2",
        ],
        "MinSpdlim": [
            "zEvent_MinSpdlim_sign_1_to_0",
            "zEvent_MinSpdlim_expressway_lane_min_speed",
            "zEvent_MinSpdlim_invalid",
        ],
        "FollowDis": ["zEvent_FollowDis"],
        "LateralDis": ["zEvent_LateralDis"],
        "LaneChange": ["zEvent_LaneChange"],
        "ContinueLaneChange": ["zEvent_ContinueLaneChange"],
        "RoadMarking": ["zEvent_RoadMarking"],
        "Overtake": ["zEvent_Overtake"],
    },
}

REPAIR_RATES_PERCENT = {
    "Nanjing": {
        "MaxSpdlim": Decimal("5.23"),
        "MinSpdlim": Decimal("11.48"),
        "FollowDis": Decimal("9.73"),
        "LateralDis": Decimal("2.11"),
        "LaneChange": Decimal("4.26"),
        "ContinueLaneChange": Decimal("1.32"),
        "RoadMarking": Decimal("8.65"),
        "Overtake": Decimal("6.00"),
    },
    "Changchun": {
        "MaxSpdlim": Decimal("9.45"),
        "MinSpdlim": Decimal("14.13"),
        "FollowDis": Decimal("9.12"),
        "LateralDis": Decimal("1.31"),
        "LaneChange": Decimal("2.26"),
        "ContinueLaneChange": Decimal("3.32"),
        "RoadMarking": Decimal("7.48"),
        "Overtake": Decimal("9.00"),
    },
}


def round_half_up(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def read_event_nums(path: Path) -> tuple[list[str], str]:
    """Return non-empty event_num values and an optional read error."""
    lines = None
    decode_errors = []
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            lines = path.read_text(encoding=encoding, errors="strict").splitlines()
            break
        except UnicodeDecodeError as exc:
            decode_errors.append(f"{encoding}: {exc}")
        except Exception as exc:
            return [], f"{type(exc).__name__}: {exc}"
    if lines is None:
        return [], " | ".join(decode_errors)

    header_index = None
    header_fields = None
    for index, line in enumerate(lines):
        fields = next(csv.reader([line])) if line.strip() else []
        if "event_num" in fields or (
            "start_idx" in fields and fields and not fields[0].strip()
        ):
            header_index = index
            header_fields = fields
            if not header_fields[0].strip():
                header_fields[0] = "event_num"
            break

    if header_index is None:
        if not any(line.strip().lstrip("\ufeff") for line in lines):
            return [], ""
        return [], "event_num header not found"

    reader = csv.DictReader(lines[header_index + 1 :], fieldnames=header_fields)
    event_nums = []
    for row in reader:
        value = str(row.get("event_num", "")).strip()
        if value:
            event_nums.append(value)
    return event_nums, ""


def iter_selected_files(others_root: Path):
    for city, category_sources in SELECTED_EVENT_SOURCES.items():
        city_root = others_root / city
        for date_dir in sorted(path for path in city_root.iterdir() if path.is_dir()):
            for category in CATEGORY_ORDER:
                for source_name in category_sources[category]:
                    source_dir = date_dir / source_name
                    if not source_dir.is_dir():
                        continue
                    for segment_dir in sorted(path for path in source_dir.iterdir() if path.is_dir()):
                        for events_file in sorted(segment_dir.glob("*_events.csv")):
                            yield city, category, source_name, date_dir.name, events_file


def load_released_counts(path: Path) -> dict[tuple[str, str], int]:
    output = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            output[(row["city"], row["category"])] = int(row["total_events"])
    return output


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--others-root",
        type=Path,
        default=Path(os.environ.get("TLCD_OTHERS_ROOT", "")),
        help="Root containing Changchun and Nanjing pre-release zEvent results.",
    )
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not str(args.others_root) or not args.others_root.is_dir():
        raise SystemExit("Provide --others-root or set TLCD_OTHERS_ROOT.")

    candidate_counts = Counter()
    source_counts = Counter()
    files_scanned = Counter()
    empty_files = []
    read_errors = []
    duplicate_event_num_files = []

    selected_files = list(iter_selected_files(args.others_root))

    def load_selected(item):
        return item, read_event_nums(item[-1])

    with ThreadPoolExecutor(max_workers=48) as executor:
        loaded_files = list(executor.map(load_selected, selected_files))

    for (city, category, source, date, path), (event_nums, error) in loaded_files:
        files_scanned[(city, category)] += 1
        if error:
            read_errors.append(
                {
                    "city": city,
                    "category": category,
                    "source": source,
                    "date": date,
                    "file": str(path.relative_to(args.others_root)),
                    "error": error,
                }
            )
            continue
        if not event_nums:
            empty_files.append(str(path.relative_to(args.others_root)))
        if len(event_nums) != len(set(event_nums)):
            duplicate_event_num_files.append(str(path.relative_to(args.others_root)))
        candidate_counts[(city, category)] += len(event_nums)
        source_counts[(city, category, source)] += len(event_nums)

    if read_errors:
        examples = "\n".join(
            f"- {item['file']}: {item['error']}" for item in read_errors[:20]
        )
        raise RuntimeError(
            f"Failed to read {len(read_errors)} events files. Examples:\n{examples}"
        )

    released_counts = load_released_counts(args.inventory)
    summary_rows = []
    for city in ["Changchun", "Nanjing"]:
        for category in CATEGORY_ORDER:
            candidate = candidate_counts[(city, category)]
            released = released_counts[(city, category)]
            excluded = candidate - released
            if excluded < 0:
                raise RuntimeError(
                    f"Released count exceeds candidates for {city}/{category}: "
                    f"{released} > {candidate}"
                )
            repair_rate = REPAIR_RATES_PERCENT[city][category]
            repaired = round_half_up(Decimal(released) * repair_rate / Decimal("100"))
            summary_rows.append(
                {
                    "city": city,
                    "category": category,
                    "candidate_events": candidate,
                    "released_events": released,
                    "excluded_events": excluded,
                    "retention_percent": f"{Decimal(released) * 100 / Decimal(candidate):.2f}",
                    "repair_rate_percent": f"{repair_rate:.2f}",
                    "repaired_events": repaired,
                    "events_files_scanned": files_scanned[(city, category)],
                }
            )

    source_rows = [
        {
            "city": city,
            "category": category,
            "zEvent_source": source,
            "candidate_events": count,
        }
        for (city, category, source), count in sorted(source_counts.items())
    ]

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(
        args.output_dir / "candidate_release_repair_counts.csv",
        summary_rows,
        [
            "city",
            "category",
            "candidate_events",
            "released_events",
            "excluded_events",
            "retention_percent",
            "repair_rate_percent",
            "repaired_events",
            "events_files_scanned",
        ],
    )
    write_csv(
        args.output_dir / "candidate_source_counts.csv",
        source_rows,
        ["city", "category", "zEvent_source", "candidate_events"],
    )

    result = {
        "counting_grain": "city/category/zEvent source/source segment/event_num",
        "candidate_events": sum(row["candidate_events"] for row in summary_rows),
        "released_events": sum(row["released_events"] for row in summary_rows),
        "excluded_events": sum(row["excluded_events"] for row in summary_rows),
        "repaired_events": sum(row["repaired_events"] for row in summary_rows),
        "events_files_scanned": sum(row["events_files_scanned"] for row in summary_rows),
        "empty_events_files": len(empty_files),
        "duplicate_event_num_files": duplicate_event_num_files,
        "read_errors": read_errors,
        "selected_sources": SELECTED_EVENT_SOURCES,
    }
    (args.output_dir / "candidate_count_qa.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
