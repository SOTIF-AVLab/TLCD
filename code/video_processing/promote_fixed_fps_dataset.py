"""Promote audited fixed-30-Hz videos while preserving original H.265 files.

For each event, the existing ``video`` directory is atomically renamed to
``video_original_h265`` and ``video_cfr30_staging`` is atomically renamed to
``video``. Generated QA JSON is kept separately in ``video_cfr30_metadata``.
The operation is resumable and supports a non-destructive rollback.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

import regenerate_all_fixed_fps_videos as batch
import regenerate_fixed_fps_event_videos as fixed


METADATA_FILES = (
    "fixed_fps_qa.json",
    "source_camera_timestamps_ns.json",
)


def expected_video_names(event_dir: Path) -> set[str]:
    number = fixed.event_number(event_dir)
    return {
        f"video_{key}_event_{number:03d}.mp4"
        for key in fixed.TOPICS
    }


def validate_video_dir(path: Path, event_dir: Path) -> None:
    if not path.is_dir():
        raise RuntimeError(f"Video directory is missing: {path}")
    expected = expected_video_names(event_dir)
    current = {item.name for item in path.glob("*.mp4") if item.is_file()}
    if current != expected:
        raise RuntimeError(
            f"Unexpected MP4 set in {path}: missing={sorted(expected - current)}, "
            f"extra={sorted(current - expected)}"
        )
    empty = [name for name in expected if (path / name).stat().st_size <= 0]
    if empty:
        raise RuntimeError(f"Empty MP4 files in {path}: {empty}")
    partial = list(path.glob("*.partial.mp4"))
    if partial:
        raise RuntimeError(f"Partial MP4 files remain in {path}")


def filesystem_state(event_dir: Path, fps: int) -> str:
    active = (event_dir / "video").is_dir()
    original = (event_dir / "video_original_h265").is_dir()
    staging = batch.staging_dir(event_dir, fps).is_dir()
    if active and staging and not original:
        return "staged"
    if not active and staging and original:
        return "promotion_interrupted"
    if active and original and not staging:
        return "promoted"
    return f"invalid(video={active},original={original},staging={staging})"


def move_metadata(source_dir: Path, metadata_dir: Path) -> None:
    metadata_dir.mkdir(parents=True, exist_ok=True)
    for name in METADATA_FILES:
        source = source_dir / name
        destination = metadata_dir / name
        if not source.exists():
            continue
        if destination.exists():
            raise RuntimeError(f"Both metadata source and destination exist: {name}")
        source.rename(destination)


def update_qa_paths(event_dir: Path, fps: int, promoted: bool) -> None:
    metadata_dir = event_dir / f"video_cfr{fps}_metadata"
    qa_path = metadata_dir / "fixed_fps_qa.json"
    if not qa_path.is_file():
        raise RuntimeError(f"QA metadata is missing: {qa_path}")
    qa = json.loads(qa_path.read_text(encoding="utf-8"))
    number = fixed.event_number(event_dir)
    source_dir = event_dir / ("video_original_h265" if promoted else "video")
    output_dir = event_dir / ("video" if promoted else f"video_cfr{fps}_staging")
    for key in fixed.TOPICS:
        name = f"video_{key}_event_{number:03d}.mp4"
        qa["videos"][key]["source_video"] = str(source_dir / name)
        qa["videos"][key]["output_video"] = str(output_dir / name)
    partial = qa_path.with_suffix(".partial.json")
    partial.write_text(json.dumps(qa, indent=2) + "\n", encoding="utf-8")
    partial.replace(qa_path)


def promote_event(event_dir: Path, fps: int) -> None:
    active_dir = event_dir / "video"
    original_dir = event_dir / "video_original_h265"
    staging_dir = batch.staging_dir(event_dir, fps)
    metadata_dir = event_dir / f"video_cfr{fps}_metadata"
    state = filesystem_state(event_dir, fps)
    if state == "staged":
        validate_video_dir(active_dir, event_dir)
        validate_video_dir(staging_dir, event_dir)
        move_metadata(staging_dir, metadata_dir)
        active_dir.rename(original_dir)
        staging_dir.rename(active_dir)
    elif state == "promotion_interrupted":
        validate_video_dir(original_dir, event_dir)
        validate_video_dir(staging_dir, event_dir)
        move_metadata(staging_dir, metadata_dir)
        staging_dir.rename(active_dir)
    elif state == "promoted":
        move_metadata(active_dir, metadata_dir)
    else:
        raise RuntimeError(f"Cannot promote event in state {state}: {event_dir}")

    validate_video_dir(active_dir, event_dir)
    validate_video_dir(original_dir, event_dir)
    update_qa_paths(event_dir, fps, promoted=True)


def rollback_event(event_dir: Path, fps: int) -> None:
    active_dir = event_dir / "video"
    original_dir = event_dir / "video_original_h265"
    staging_dir = batch.staging_dir(event_dir, fps)
    metadata_dir = event_dir / f"video_cfr{fps}_metadata"
    state = filesystem_state(event_dir, fps)
    if state == "promoted":
        validate_video_dir(active_dir, event_dir)
        validate_video_dir(original_dir, event_dir)
        active_dir.rename(staging_dir)
        original_dir.rename(active_dir)
    elif state == "promotion_interrupted":
        original_dir.rename(active_dir)
    elif state != "staged":
        raise RuntimeError(f"Cannot roll back event in state {state}: {event_dir}")

    for name in METADATA_FILES:
        source = metadata_dir / name
        destination = staging_dir / name
        if not source.exists():
            continue
        if destination.exists():
            raise RuntimeError(f"Both metadata source and destination exist: {name}")
        source.rename(destination)
    qa_in_staging = staging_dir / "fixed_fps_qa.json"
    if qa_in_staging.is_file():
        metadata_dir.mkdir(parents=True, exist_ok=True)
        qa_in_staging.rename(metadata_dir / "fixed_fps_qa.json")
        update_qa_paths(event_dir, fps, promoted=False)
        (metadata_dir / "fixed_fps_qa.json").rename(qa_in_staging)
    validate_video_dir(active_dir, event_dir)
    validate_video_dir(staging_dir, event_dir)


def require_passing_audit(path: Path, expected_events: int) -> None:
    if not path.is_file():
        raise RuntimeError(f"Audit report is missing: {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if report.get("failures"):
        raise RuntimeError("Audit report contains failures")
    if report.get("expected_events") != expected_events:
        raise RuntimeError("Audit report event total does not match the manifest")
    if report.get("audited_events") != expected_events:
        raise RuntimeError("Audit report does not cover every event")
    if report.get("audited_camera_videos") != expected_events * len(fixed.TOPICS):
        raise RuntimeError("Audit report does not cover every camera video")
    if not report.get("original_h265_videos_preserved"):
        raise RuntimeError("Audit did not confirm preservation of original H.265 videos")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path(os.environ.get("TLCD_DATASET_ROOT", "Dataset")),
    )
    parser.add_argument(
        "--event-manifest",
        type=Path,
        default=Path(__file__).resolve().parent / "fixed_fps_dataset_events.json",
    )
    parser.add_argument(
        "--audit-report",
        type=Path,
        default=Path(__file__).resolve().parent / "fixed_fps_dataset_audit.json",
    )
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--mode", choices=("check", "apply", "rollback"), default="check")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    events = batch.discover_events(args.dataset_root, args.event_manifest)
    if args.mode == "apply":
        require_passing_audit(args.audit_report, len(events))

    failures = []
    counts = Counter()
    for index, event_dir in enumerate(events, start=1):
        try:
            if args.mode == "apply":
                promote_event(event_dir, args.fps)
            elif args.mode == "rollback":
                rollback_event(event_dir, args.fps)
            counts[filesystem_state(event_dir, args.fps)] += 1
        except Exception as error:
            failures.append({"event": str(event_dir), "error": str(error)})
        if index % 100 == 0 or index == len(events):
            print(
                f"[{args.mode.upper()}] events={index}/{len(events)} "
                f"failures={len(failures)} states={dict(counts)}",
                flush=True,
            )

    summary = {
        "mode": args.mode,
        "events": len(events),
        "states": dict(counts),
        "failures": failures,
    }
    print(json.dumps(summary, indent=2), flush=True)
    if failures:
        return 1
    if args.mode == "apply" and counts != Counter({"promoted": len(events)}):
        return 1
    if args.mode == "rollback" and counts != Counter({"staged": len(events)}):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
