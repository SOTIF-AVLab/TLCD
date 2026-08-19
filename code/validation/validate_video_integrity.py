#!/usr/bin/env python3
"""Validate video metadata or full decoding using a completed CSV event audit."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from validate_dataset_integrity import probe_video


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader()
        writer.writerows(rows)


def audit_video(path: Path, event_id: str, structured_duration: float, mode: str) -> dict:
    metadata, error = probe_video(path, full_decode=mode == "decode")
    row = {
        "event_id": event_id,
        "video": path.name,
        "structured_duration_s": structured_duration,
        "video_duration_s": "",
        "frame_rate_fps": "",
        "duration_difference_s": "",
        "duration_difference_frames": "",
        "within_one_frame": False,
        "decoded_frames": "",
        "decode_error_packets": "",
        "error": error,
    }
    if not metadata:
        return row
    difference = abs(float(metadata["duration_s"]) - structured_duration)
    difference_frames = difference * float(metadata["frame_rate_fps"])
    row.update(
        {
            "video_duration_s": metadata["duration_s"],
            "frame_rate_fps": metadata["frame_rate_fps"],
            "duration_difference_s": difference,
            "duration_difference_frames": difference_frames,
            "within_one_frame": difference_frames <= 1.0 + 1e-6,
            "decoded_frames": metadata.get("decoded_frames", ""),
            "decode_error_packets": metadata.get("decode_error_packets", ""),
        }
    )
    return row


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--event-audit", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--mode", choices=("probe", "decode"), default="probe")
    parser.add_argument("--workers", type=int, default=24)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    event_rows = read_csv(args.event_audit)
    tasks = []
    omitted_events = []
    for row in event_rows:
        if row.get("seven_videos_present", "").lower() != "true" or not row.get(
            "structured_duration_s"
        ):
            omitted_events.append(row["event_id"])
            continue
        event_dir = args.dataset_root / row["event_id"]
        videos = sorted((event_dir / "video").glob("*.mp4"))
        if len(videos) != 7:
            omitted_events.append(row["event_id"])
            continue
        for video in videos:
            tasks.append((video, row["event_id"], float(row["structured_duration_s"])))

    output_rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(audit_video, path, event_id, duration, args.mode): path
            for path, event_id, duration in tasks
        }
        for index, future in enumerate(as_completed(futures), start=1):
            output_rows.append(future.result())
            if index % 1000 == 0:
                print(f"Validated {index}/{len(tasks)} video files", flush=True)
    output_rows.sort(key=lambda row: (row["event_id"], row["video"]))

    by_event: dict[str, list[dict]] = defaultdict(list)
    for row in output_rows:
        by_event[row["event_id"]].append(row)
    complete_event_count = sum(
        len(rows) == 7 and all(not row["error"] for row in rows)
        for rows in by_event.values()
    )
    within_one_frame_event_count = sum(
        len(rows) == 7 and all(row["within_one_frame"] for row in rows)
        for rows in by_event.values()
    )
    rows_with_duration = [row for row in output_rows if row["duration_difference_s"] != ""]
    maximum_row = max(
        rows_with_duration,
        key=lambda row: float(row["duration_difference_frames"]),
        default=None,
    )
    summary = {
        "mode": args.mode,
        "events_in_event_audit": len(event_rows),
        "events_omitted_without_complete_video_set": omitted_events,
        "events_audited": len(by_event),
        "videos_expected": len(tasks),
        "videos_with_readable_metadata": len(rows_with_duration),
        "videos_without_reported_errors": sum(not row["error"] for row in output_rows),
        "videos_with_duration_within_one_frame": sum(
            bool(row["within_one_frame"]) for row in output_rows
        ),
        "events_with_all_videos_without_reported_errors": complete_event_count,
        "events_with_all_video_durations_within_one_frame": within_one_frame_event_count,
        "total_decode_error_packets": sum(
            int(row["decode_error_packets"] or 0) for row in output_rows
        ),
        "maximum_duration_difference": maximum_row,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stem = "video_decode" if args.mode == "decode" else "video_metadata"
    write_csv(args.output_dir / f"{stem}_file_audit.csv", output_rows)
    (args.output_dir / f"{stem}_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
