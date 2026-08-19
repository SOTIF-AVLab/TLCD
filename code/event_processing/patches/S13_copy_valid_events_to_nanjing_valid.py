from __future__ import annotations

import argparse
import csv
import shutil
from collections import defaultdict
from pathlib import Path


SOURCE_ROOT = Path(r"Z:\HongqiData\Nanjing")
DEST_ROOT = Path(r"Z:\HongqiData\Nanjing_valid")
SKIP_SOURCE_DATE_PREFIXES = ("20241025",)


EVENT_TYPES = [
    {
        "source_dir": "zEvent_MaxSpdlim",
        "events_csv": "MaxSpdlim_events.csv",
        "prefix": "MaxSpdlim",
        "dest_dir": "01_MaxSpdlim",
    },
    {
        "source_dir": "zEvent_MinSpdlim",
        "events_csv": "MinSpdlim_events.csv",
        "prefix": "MinSpdlim",
        "dest_dir": "02_MinSpdlim",
    },
    {
        "source_dir": "zEvent_FollowDis",
        "events_csv": "FollowDis_events.csv",
        "prefix": "FollowDis",
        "dest_dir": "03_FollowDis",
    },
    {
        "source_dir": "zEvent_LateralDis",
        "events_csv": "LateralDis_events.csv",
        "prefix": "LateralDis",
        "dest_dir": "04_LateralDis",
    },
    {
        "source_dir": "zEvent_LaneChange",
        "events_csv": "lane_change_events.csv",
        "prefix": "lane_change",
        "dest_dir": "05_LaneChange",
    },
    {
        "source_dir": "zEvent_ContinueLaneChange",
        "events_csv": "ContinueLC_events.csv",
        "prefix": "ContinueLC",
        "dest_dir": "06_ContinueLaneChange",
    },
    {
        "source_dir": "zEvent_RoadMarking",
        "events_csv": "RoadMarking_events.csv",
        "prefix": "RoadMarking",
        "dest_dir": "07_RoadMarking",
    },
    {
        "source_dir": "zEvent_Overtake",
        "events_csv": "Overtake_events.csv",
        "prefix": "Overtake",
        "dest_dir": "08_Overtake",
    },
]

ARTIFACT_SUFFIXES = [
    "EgoInfo.csv",
    "ObjInfo.csv",
    "MapInfo.csv",
    "EvidenceChain.csv",
    "record.json",
]
EXPECTED_MP4_VIEW_COUNT = 7


def read_events(path: Path) -> tuple[list[dict[str, str]], str]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as f:
                return list(csv.DictReader(f)), encoding
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, str(path))


def source_date_dir(events_path: Path) -> str:
    return events_path.parent.parent.parent.name


def iter_valid_events():
    for cfg in EVENT_TYPES:
        for events_path in sorted(SOURCE_ROOT.glob(f"*/{cfg['source_dir']}/*/{cfg['events_csv']}")):
            if source_date_dir(events_path).startswith(SKIP_SOURCE_DATE_PREFIXES):
                continue
            rows, _ = read_events(events_path)
            if not rows or "Event_Validity" not in rows[0]:
                continue
            event_num_col = "event_num"
            if event_num_col not in rows[0]:
                event_num_col = next(iter(rows[0].keys()))
            valid_rows = [row for row in rows if (row.get("Event_Validity") or "").strip() == "1"]
            for valid_index, row in enumerate(valid_rows, start=1):
                yield cfg, events_path, valid_index, int(row[event_num_col])


def source_files(segment_dir: Path, prefix: str, event_num: int) -> list[Path]:
    return [
        segment_dir / f"{prefix}_event_{event_num}_{suffix}"
        for suffix in ARTIFACT_SUFFIXES[:-1]
    ] + [
        segment_dir / f"{prefix}_event_{event_num}_{ARTIFACT_SUFFIXES[-1]}"
    ]


def source_mp4_files(events_path: Path, event_num: int) -> list[Path]:
    segment_dir = events_path.parent
    own_files = sorted(segment_dir.glob(f"video_*/mp4/event_{event_num:03d}.mp4"))
    if len(own_files) == EXPECTED_MP4_VIEW_COUNT:
        return own_files

    date_dir = events_path.parent.parent.parent
    sibling_pattern = f"zEvent_*/{segment_dir.name}/video_*/mp4/event_{event_num:03d}.mp4"
    by_event_dir: dict[Path, list[Path]] = defaultdict(list)
    for path in sorted(date_dir.glob(sibling_pattern)):
        by_event_dir[path.parents[2]].append(path)

    for files in by_event_dir.values():
        if len(files) == EXPECTED_MP4_VIEW_COUNT:
            return sorted(files)
    return own_files


def mp4_destination_path(dest_dir: Path, src: Path) -> Path:
    view_name = src.parent.parent.name
    return dest_dir / "video" / "mp4" / f"{view_name}_{src.name}"


def duplicate_segment_keys(valid_events: list[tuple[dict[str, str], Path, int, int]]) -> set[tuple[str, str]]:
    dates_by_segment: dict[tuple[str, str], set[str]] = defaultdict(set)
    for cfg, events_path, _, _ in valid_events:
        dates_by_segment[(cfg["dest_dir"], events_path.parent.name)].add(source_date_dir(events_path))
    return {key for key, dates in dates_by_segment.items() if len(dates) > 1}


def destination_dir(
    cfg: dict[str, str],
    segment_name: str,
    valid_index: int,
    source_date: str | None = None,
    duplicate_keys: set[tuple[str, str]] | None = None,
) -> Path:
    if source_date and duplicate_keys and (cfg["dest_dir"], segment_name) in duplicate_keys:
        segment_name = f"{segment_name}__{source_date}"
    return DEST_ROOT / cfg["dest_dir"] / segment_name / f"event_{valid_index:02d}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    totals: dict[str, int] = {cfg["dest_dir"]: 0 for cfg in EVENT_TYPES}
    missing: list[Path] = []
    missing_videos: list[str] = []
    copied_files = 0
    copied_bytes = 0
    copied_mp4_files = 0
    copied_mp4_bytes = 0

    valid_events = list(iter_valid_events())
    duplicate_keys = duplicate_segment_keys(valid_events)

    for cfg, events_path, _, event_num in valid_events:
        segment_dir = events_path.parent
        files = source_files(segment_dir, cfg["prefix"], event_num)
        missing.extend(path for path in files if not path.exists())
        mp4_files = source_mp4_files(events_path, event_num)
        if len(mp4_files) != EXPECTED_MP4_VIEW_COUNT:
            missing_videos.append(f"{segment_dir} event_{event_num:03d}: {len(mp4_files)} mp4 files")
        totals[cfg["dest_dir"]] += 1

    print(f"Valid events: {len(valid_events)}")
    for dest_dir, count in totals.items():
        print(f"{dest_dir}: {count}")
    print(f"Missing standard files: {len(missing)}")
    print(f"Events without {EXPECTED_MP4_VIEW_COUNT} mp4 views: {len(missing_videos)}")
    if missing:
        for path in missing[:50]:
            print(f"MISSING {path}")
        raise SystemExit(1)
    if missing_videos:
        for item in missing_videos[:50]:
            print(f"MISSING_MP4 {item}")

    if args.dry_run:
        return

    processed_events = 0
    for cfg, events_path, valid_index, event_num in valid_events:
        processed_events += 1
        if processed_events % 100 == 0:
            print(f"Processed events: {processed_events}/{len(valid_events)}", flush=True)
        segment_dir = events_path.parent
        dest_dir = destination_dir(
            cfg,
            segment_dir.name,
            valid_index,
            source_date_dir(events_path),
            duplicate_keys,
        )
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "video" / "h265").mkdir(parents=True, exist_ok=True)
        (dest_dir / "video" / "mp4").mkdir(parents=True, exist_ok=True)

        for src in source_files(segment_dir, cfg["prefix"], event_num):
            dst = dest_dir / src.name
            src_size = src.stat().st_size
            if dst.exists() and dst.stat().st_size == src_size:
                continue
            shutil.copy2(src, dst)
            copied_files += 1
            copied_bytes += src_size

        for src in source_mp4_files(events_path, event_num):
            dst = mp4_destination_path(dest_dir, src)
            src_size = src.stat().st_size
            if dst.exists() and dst.stat().st_size == src_size:
                continue
            shutil.copy2(src, dst)
            copied_mp4_files += 1
            copied_mp4_bytes += src_size

    print(f"Copied files: {copied_files}")
    print(f"Copied MiB: {copied_bytes / 1024 / 1024:.1f}")
    print(f"Copied mp4 files: {copied_mp4_files}")
    print(f"Copied mp4 GiB: {copied_mp4_bytes / 1024 / 1024 / 1024:.1f}")


if __name__ == "__main__":
    main()
