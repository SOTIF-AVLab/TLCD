from __future__ import annotations

import argparse
import csv
import shutil
from collections import defaultdict
from pathlib import Path

import S13_copy_valid_events_to_nanjing_valid as copy_valid


REPORT_PATH = Path("01Event_Extraction") / "missing_mp4_events_removed.csv"


def read_events_with_encoding(path: Path) -> tuple[list[dict[str, str]], str, list[str]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                reader = csv.DictReader(f)
                return list(reader), encoding, list(reader.fieldnames or [])
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, str(path))


def find_missing_mp4_events():
    valid_events = list(copy_valid.iter_valid_events())
    duplicate_keys = copy_valid.duplicate_segment_keys(valid_events)
    missing = []
    for cfg, events_path, valid_index, event_num in valid_events:
        mp4_files = copy_valid.source_mp4_files(events_path, event_num)
        if len(mp4_files) == copy_valid.EXPECTED_MP4_VIEW_COUNT:
            continue
        segment_dir = events_path.parent
        dest_dir = copy_valid.destination_dir(
            cfg,
            segment_dir.name,
            valid_index,
            copy_valid.source_date_dir(events_path),
            duplicate_keys,
        )
        missing.append(
            {
                "dest_category": cfg["dest_dir"],
                "event_type": cfg["source_dir"],
                "events_csv": str(events_path),
                "segment": segment_dir.name,
                "event_num": event_num,
                "valid_index": valid_index,
                "source_mp4_count": len(mp4_files),
                "dest_dir": str(dest_dir),
            }
        )
    return missing


def remove_target_dirs(missing_events: list[dict[str, object]]) -> int:
    removed = 0
    dest_root = copy_valid.DEST_ROOT.resolve()
    for item in missing_events:
        dest_dir = Path(str(item["dest_dir"]))
        if not dest_dir.exists():
            continue
        resolved = dest_dir.resolve()
        if not resolved.is_relative_to(dest_root):
            raise ValueError(f"Refusing to delete outside target root: {dest_dir}")
        shutil.rmtree(dest_dir)
        removed += 1
    return removed


def update_source_csvs(missing_events: list[dict[str, object]]) -> int:
    by_csv: dict[Path, set[int]] = defaultdict(set)
    for item in missing_events:
        by_csv[Path(str(item["events_csv"]))].add(int(item["event_num"]))

    updated_rows = 0
    for events_path, event_nums in by_csv.items():
        rows, encoding, fieldnames = read_events_with_encoding(events_path)
        if not fieldnames or "Event_Validity" not in fieldnames:
            raise ValueError(f"Missing Event_Validity in {events_path}")
        event_num_col = "event_num"
        if event_num_col not in fieldnames:
            event_num_col = fieldnames[0]

        changed = False
        for row in rows:
            try:
                event_num = int(row[event_num_col])
            except (TypeError, ValueError):
                continue
            if event_num in event_nums and (row.get("Event_Validity") or "").strip() != "0":
                row["Event_Validity"] = "0"
                updated_rows += 1
                changed = True

        if changed:
            with events_path.open("w", encoding=encoding, newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)
    return updated_rows


def write_report(missing_events: list[dict[str, object]]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "dest_category",
        "event_type",
        "events_csv",
        "segment",
        "event_num",
        "valid_index",
        "source_mp4_count",
        "dest_dir",
    ]
    with REPORT_PATH.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(missing_events)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    missing_events = find_missing_mp4_events()
    write_report(missing_events)
    print(f"Missing mp4 events: {len(missing_events)}")
    print(f"Report: {REPORT_PATH}")
    if not missing_events:
        return
    if args.dry_run:
        return

    removed_dirs = remove_target_dirs(missing_events)
    updated_rows = update_source_csvs(missing_events)
    print(f"Removed target dirs: {removed_dirs}")
    print(f"Updated Event_Validity rows: {updated_rows}")


if __name__ == "__main__":
    main()
