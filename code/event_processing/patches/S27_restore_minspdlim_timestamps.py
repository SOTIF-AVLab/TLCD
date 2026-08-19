"""Restore Timestamp for MinSpdlim event records from unique velocity matches."""

import argparse
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from fractions import Fraction
from pathlib import Path

from S26_update_valid_record_times import (
    CITY_ROOTS,
    atomic_write_json,
    build_segment_model,
    item_from_record_path,
    kmp_matches,
    milliseconds,
    read_event_velocities,
)


TARGET_PATTERN = "MinSpdlim_event_*_record.json"


def process_segment(items: list[dict], apply: bool) -> list[dict]:
    try:
        sub_min, time_diff, grid = build_segment_model(items[0])
    except Exception as error:
        return [
            {
                "record": str(item["record_path"]),
                "status": "source_error",
                "detail": str(error),
            }
            for item in items
        ]

    results = []
    for item in items:
        try:
            ego = read_event_velocities(item["ego_path"])
            candidates = kmp_matches(grid, ego)
            if len(candidates) != 1:
                results.append(
                    {
                        "record": str(item["record_path"]),
                        "status": "velocity_not_found" if not candidates else "velocity_not_unique",
                        "candidate_count": len(candidates),
                    }
                )
                continue

            start = Fraction(sub_min + candidates[0] * 10_000) - time_diff
            end = Fraction(sub_min + (candidates[0] + len(ego) - 1) * 10_000) - time_diff
            timestamp = {
                "t_start": milliseconds(start),
                "t_end": milliseconds(end),
            }
            record = item["record"]
            if record.get("Timestamp") == timestamp:
                status = "unchanged"
            elif apply:
                record["Timestamp"] = timestamp
                atomic_write_json(record, item["record_path"])
                status = "updated"
            else:
                status = "would_update"
            results.append(
                {
                    "record": str(item["record_path"]),
                    "status": status,
                    "timestamp": timestamp,
                }
            )
        except Exception as error:
            results.append(
                {
                    "record": str(item["record_path"]),
                    "status": "event_error",
                    "detail": str(error),
                }
            )
    return results


def collect_items() -> tuple[list[dict], list[dict]]:
    items: list[dict] = []
    errors: list[dict] = []
    for city, (valid_root, _) in CITY_ROOTS.items():
        for record_path in sorted((valid_root / "02_MinSpdlim").rglob(TARGET_PATTERN)):
            try:
                items.append(item_from_record_path(record_path))
            except Exception as error:
                errors.append(
                    {
                        "record": str(record_path),
                        "status": "event_error",
                        "detail": str(error),
                        "city": city,
                    }
                )
    return items, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Restore Timestamp only for all MinSpdlim event record files."
    )
    parser.add_argument("--apply", action="store_true", help="Write reconstructed Timestamp values.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(__file__).with_name("S27_minspdlim_timestamp_restore_summary.json"),
    )
    args = parser.parse_args()

    items, results = collect_items()
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for item in items:
        groups[(item["city"], item["date"], item["segment"])].append(item)

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [executor.submit(process_segment, group, args.apply) for group in groups.values()]
        for completed, future in enumerate(as_completed(futures), 1):
            results.extend(future.result())
            if completed % 25 == 0 or completed == len(futures):
                print(f"processed segments: {completed}/{len(futures)}", flush=True)

    counts = Counter(result["status"] for result in results)
    report = {
        "apply": args.apply,
        "target": TARGET_PATTERN,
        "records": len(results),
        "status_counts": dict(counts),
        "results": results,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.report), **report["status_counts"]}, ensure_ascii=False))
    return 1 if any(status.endswith("error") for status in counts) else 0


if __name__ == "__main__":
    raise SystemExit(main())
