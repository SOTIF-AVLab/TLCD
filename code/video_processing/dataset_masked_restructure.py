#!/usr/bin/env python3
"""Audit, back up, and promote masked TLCD event videos safely.

The workflow is intentionally split into four commands:

1. ``audit`` checks every event without modifying the dataset.
2. ``backup`` copies the requested pre-promotion assets to a mirror tree.
3. ``promote`` atomically replaces each event's ``video`` directory with its
   existing ``video/video_masked`` directory, after verifying the backup.
4. ``verify`` checks the backup and the final, reduced source tree.

Only ISO-BMFF/MP4 movie-header metadata is read for duration comparison; video
payloads are not decoded during the audit.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import os
import re
import shutil
import struct
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable


CAMERAS = ("120", "30", "rear", "Side_B", "Side_F", "Side_L", "Side_R")
CSV_SUFFIXES = ("_EgoInfo.csv", "_ObjInfo.csv", "_MapInfo.csv", "_EvidenceChain.csv")
EVENT_RE = re.compile(r"^event_(\d+)$")
AUDIT_VERSION = 1
COPY_BUFFER_BYTES = 8 * 1024 * 1024


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    partial.replace(path)


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def scan_dirs(path: Path) -> list[Path]:
    with os.scandir(path) as entries:
        return sorted(
            (Path(entry.path) for entry in entries if entry.is_dir(follow_symlinks=False)),
            key=lambda item: item.name,
        )


def discover_events(root: Path) -> list[Path]:
    events: list[Path] = []
    for city in scan_dirs(root):
        if not city.name.endswith("_valid"):
            continue
        for category in scan_dirs(city):
            for segment in scan_dirs(category):
                for event in scan_dirs(segment):
                    if EVENT_RE.fullmatch(event.name):
                        events.append(event)
    return events


def expected_video_names(event: Path) -> set[str]:
    match = EVENT_RE.fullmatch(event.name)
    if not match:
        raise ValueError(f"Invalid event directory name: {event}")
    number = int(match.group(1))
    return {f"video_{camera}_event_{number:03d}.mp4" for camera in CAMERAS}


def mp4_duration(path: Path) -> tuple[float, int, int]:
    """Return movie duration in seconds plus mvhd timescale and tick count."""

    file_size = path.stat().st_size
    if file_size <= 0:
        raise ValueError("empty file")

    with path.open("rb") as handle:
        def box_header(position: int, boundary: int) -> tuple[int, bytes, int]:
            handle.seek(position)
            raw = handle.read(8)
            if len(raw) != 8:
                raise ValueError(f"short MP4 box header at byte {position}")
            size32, kind = struct.unpack(">I4s", raw)
            header_size = 8
            if size32 == 1:
                extended = handle.read(8)
                if len(extended) != 8:
                    raise ValueError(f"short extended MP4 box header at byte {position}")
                size = struct.unpack(">Q", extended)[0]
                header_size = 16
            elif size32 == 0:
                size = boundary - position
            else:
                size = size32
            if size < header_size or position + size > boundary:
                raise ValueError(f"invalid MP4 box size at byte {position}: {size}")
            return size, kind, header_size

        position = 0
        while position + 8 <= file_size:
            size, kind, header_size = box_header(position, file_size)
            if kind == b"moov":
                child = position + header_size
                boundary = position + size
                while child + 8 <= boundary:
                    child_size, child_kind, child_header = box_header(child, boundary)
                    if child_kind == b"mvhd":
                        handle.seek(child + child_header)
                        payload = handle.read(32)
                        if len(payload) < 20:
                            raise ValueError("short mvhd payload")
                        version = payload[0]
                        if version == 0:
                            timescale = struct.unpack(">I", payload[12:16])[0]
                            ticks = struct.unpack(">I", payload[16:20])[0]
                        elif version == 1:
                            if len(payload) < 32:
                                raise ValueError("short version-1 mvhd payload")
                            timescale = struct.unpack(">I", payload[20:24])[0]
                            ticks = struct.unpack(">Q", payload[24:32])[0]
                        else:
                            raise ValueError(f"unsupported mvhd version: {version}")
                        if timescale <= 0:
                            raise ValueError("invalid zero mvhd timescale")
                        return ticks / timescale, timescale, ticks
                    child += child_size
            position += size
    raise ValueError("mvhd box not found")


def visible_names(path: Path) -> set[str]:
    with os.scandir(path) as entries:
        return {entry.name for entry in entries if entry.name != ".DS_Store"}


def matching_files(event: Path, suffix: str) -> list[Path]:
    with os.scandir(event) as entries:
        return sorted(
            (
                Path(entry.path)
                for entry in entries
                if entry.is_file(follow_symlinks=False) and entry.name.endswith(suffix)
            ),
            key=lambda item: item.name,
        )


def audit_one_event(event: Path, root: Path, duration_tolerance_s: float) -> dict:
    relative = event.relative_to(root).as_posix()
    failures: list[str] = []
    expected_names = expected_video_names(event)

    csv_paths: list[Path] = []
    for suffix in CSV_SUFFIXES:
        matches = matching_files(event, suffix)
        if len(matches) != 1:
            failures.append(f"expected exactly one {suffix}, found {len(matches)}")
        else:
            csv_paths.extend(matches)
    json_paths = matching_files(event, "_record.json")
    if len(json_paths) != 1:
        failures.append(f"expected exactly one _record.json, found {len(json_paths)}")

    required_dirs = {
        "video": event / "video",
        "clip": event / "clip",
        "video_cfr30_metadata": event / "video_cfr30_metadata",
        "video_original_h265": event / "video_original_h265",
    }
    for name, path in required_dirs.items():
        if not path.is_dir():
            failures.append(f"missing required directory: {name}")

    allowed_root = {path.name for path in csv_paths + json_paths}
    allowed_root.update(required_dirs)
    current_root = visible_names(event)
    unexpected_root = sorted(current_root - allowed_root)
    if unexpected_root:
        failures.append(f"unexpected event-root entries: {unexpected_root}")

    videos: dict[str, dict] = {}
    video_dir = required_dirs["video"]
    masked_dir = video_dir / "video_masked"
    if video_dir.is_dir():
        root_mp4 = {
            item.name: item
            for item in video_dir.iterdir()
            if item.is_file() and item.suffix.lower() == ".mp4"
        }
        if set(root_mp4) != expected_names:
            failures.append(
                "video MP4 set mismatch: "
                f"missing={sorted(expected_names - set(root_mp4))}, "
                f"extra={sorted(set(root_mp4) - expected_names)}"
            )
        allowed_video = expected_names | {"video_masked"}
        unexpected_video = sorted(visible_names(video_dir) - allowed_video)
        if unexpected_video:
            failures.append(f"unexpected video entries: {unexpected_video}")
    else:
        root_mp4 = {}

    if not masked_dir.is_dir():
        failures.append("missing video/video_masked directory")
        masked_mp4: dict[str, Path] = {}
    else:
        masked_mp4 = {
            item.name: item
            for item in masked_dir.iterdir()
            if item.is_file() and item.suffix.lower() == ".mp4"
        }
        if set(masked_mp4) != expected_names:
            failures.append(
                "masked MP4 set mismatch: "
                f"missing={sorted(expected_names - set(masked_mp4))}, "
                f"extra={sorted(set(masked_mp4) - expected_names)}"
            )
        unexpected_masked = sorted(visible_names(masked_dir) - expected_names)
        if unexpected_masked:
            failures.append(f"unexpected video_masked entries: {unexpected_masked}")

    original_dir = required_dirs["video_original_h265"]
    if original_dir.is_dir():
        original_mp4 = {
            item.name
            for item in original_dir.iterdir()
            if item.is_file() and item.suffix.lower() == ".mp4"
        }
        if original_mp4 != expected_names:
            failures.append(
                "video_original_h265 MP4 set mismatch: "
                f"missing={sorted(expected_names - original_mp4)}, "
                f"extra={sorted(original_mp4 - expected_names)}"
            )

    if set(root_mp4) == expected_names and set(masked_mp4) == expected_names:
        for name in sorted(expected_names):
            source = root_mp4[name]
            masked = masked_mp4[name]
            try:
                source_duration, source_scale, source_ticks = mp4_duration(source)
                masked_duration, masked_scale, masked_ticks = mp4_duration(masked)
                difference = abs(source_duration - masked_duration)
                if difference > duration_tolerance_s:
                    failures.append(
                        f"duration mismatch {name}: source={source_duration:.9f}s "
                        f"masked={masked_duration:.9f}s delta={difference:.9f}s"
                    )
                videos[name] = {
                    "source_size": source.stat().st_size,
                    "masked_size": masked.stat().st_size,
                    "source_duration_s": source_duration,
                    "masked_duration_s": masked_duration,
                    "source_mvhd_timescale": source_scale,
                    "source_mvhd_ticks": source_ticks,
                    "masked_mvhd_timescale": masked_scale,
                    "masked_mvhd_ticks": masked_ticks,
                    "duration_delta_s": difference,
                }
            except Exception as error:
                failures.append(f"cannot read duration for {name}: {error}")

    return {
        "event": relative,
        "passed": not failures,
        "failures": failures,
        "csv_names": sorted(path.name for path in csv_paths),
        "json_name": json_paths[0].name if len(json_paths) == 1 else None,
        "videos": videos,
    }


def run_audit(args: argparse.Namespace) -> int:
    root = args.source.resolve()
    events = discover_events(root)
    if not events:
        raise RuntimeError(f"No event directories found under {root}")
    print(f"[AUDIT] discovered_events={len(events)}", flush=True)

    results: dict[str, dict] = {}
    failures: list[dict] = []
    counts = Counter()
    max_delta = 0.0
    last_update = time.monotonic()

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(audit_one_event, event, root, args.duration_tolerance_s): event
            for event in events
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_map):
            event = future_map[future]
            try:
                result = future.result()
            except Exception as error:
                result = {
                    "event": event.relative_to(root).as_posix(),
                    "passed": False,
                    "failures": [f"unhandled audit error: {error}"],
                    "csv_names": [],
                    "json_name": None,
                    "videos": {},
                }
            results[result["event"]] = result
            city, category = result["event"].split("/", 2)[:2]
            counts[(city, category)] += 1
            if not result["passed"]:
                failures.append({"event": result["event"], "failures": result["failures"]})
            for video in result["videos"].values():
                max_delta = max(max_delta, video["duration_delta_s"])
            completed += 1
            now = time.monotonic()
            if completed == len(events) or now - last_update >= 30:
                print(
                    f"[AUDIT] events={completed}/{len(events)} failures={len(failures)}",
                    flush=True,
                )
                last_update = now

    ordered_results = {key: results[key] for key in sorted(results)}
    report = {
        "audit_version": AUDIT_VERSION,
        "created_at": now_iso(),
        "source_root": str(root),
        "duration_tolerance_s": args.duration_tolerance_s,
        "passed": not failures and len(results) == len(events),
        "event_count": len(events),
        "passed_events": sum(1 for value in results.values() if value["passed"]),
        "video_pairs_checked": sum(len(value["videos"]) for value in results.values()),
        "maximum_duration_delta_s": max_delta,
        "counts_by_city_category": {
            f"{city}/{category}": count
            for (city, category), count in sorted(counts.items())
        },
        "failures": failures,
        "events": ordered_results,
    }
    save_json(args.report.resolve(), report)
    print(
        f"[AUDIT-DONE] passed={report['passed']} events={report['event_count']} "
        f"video_pairs={report['video_pairs_checked']} failures={len(failures)} "
        f"max_duration_delta_s={max_delta:.9f} report={args.report.resolve()}",
        flush=True,
    )
    return 0 if report["passed"] else 2


def walk_tree(root: Path) -> tuple[list[Path], list[Path]]:
    directories: list[Path] = [root]
    files: list[Path] = []
    stack = [root]
    while stack:
        current = stack.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_symlink():
                    raise RuntimeError(f"Symlink is not allowed in backup selection: {path}")
                if entry.is_dir(follow_symlinks=False):
                    directories.append(path)
                    stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    files.append(path)
                else:
                    raise RuntimeError(f"Unsupported filesystem entry: {path}")
    return directories, files


def selected_backup_entries(source: Path, audit: dict) -> tuple[list[dict], list[str]]:
    file_entries: list[dict] = []
    directory_entries: set[str] = set()

    for event_relative, result in audit["events"].items():
        event = source / event_relative
        directory_entries.add(event_relative)

        for name in result["csv_names"]:
            path = event / name
            relative = path.relative_to(source).as_posix()
            file_entries.append({"path": relative, "size": path.stat().st_size})

        for directory_name in ("clip", "video_cfr30_metadata", "video_original_h265"):
            directory = event / directory_name
            directories, files = walk_tree(directory)
            directory_entries.update(path.relative_to(source).as_posix() for path in directories)
            for path in files:
                file_entries.append(
                    {"path": path.relative_to(source).as_posix(), "size": path.stat().st_size}
                )

        video_destination = f"{event_relative}/video"
        directory_entries.add(video_destination)
        for name in sorted(expected_video_names(event)):
            path = event / "video" / name
            file_entries.append(
                {"path": path.relative_to(source).as_posix(), "size": path.stat().st_size}
            )

    file_entries.sort(key=lambda value: value["path"])
    duplicates = [path for path, count in Counter(value["path"] for value in file_entries).items() if count > 1]
    if duplicates:
        raise RuntimeError(f"Duplicate backup manifest paths: {duplicates[:10]}")
    return file_entries, sorted(directory_entries)


def copy_file_atomic(source: Path, destination: Path, expected_size: int) -> tuple[int, bool]:
    if destination.is_file() and destination.stat().st_size == expected_size:
        return expected_size, True
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    partial.unlink(missing_ok=True)
    with source.open("rb") as reader, partial.open("wb") as writer:
        shutil.copyfileobj(reader, writer, length=COPY_BUFFER_BYTES)
        writer.flush()
        os.fsync(writer.fileno())
    if partial.stat().st_size != expected_size:
        raise RuntimeError(
            f"Copied size mismatch for {source}: {partial.stat().st_size} != {expected_size}"
        )
    shutil.copystat(source, partial, follow_symlinks=False)
    partial.replace(destination)
    return expected_size, False


def verify_manifest_files(root: Path, entries: list[dict], label: str) -> list[str]:
    failures: list[str] = []
    last_update = time.monotonic()
    for index, entry in enumerate(entries, start=1):
        path = root / entry["path"]
        try:
            if not path.is_file():
                failures.append(f"missing {entry['path']}")
            elif path.stat().st_size != entry["size"]:
                failures.append(
                    f"size mismatch {entry['path']}: {path.stat().st_size} != {entry['size']}"
                )
        except Exception as error:
            failures.append(f"cannot stat {entry['path']}: {error}")
        now = time.monotonic()
        if index == len(entries) or now - last_update >= 30:
            print(f"[{label}] files={index}/{len(entries)} failures={len(failures)}", flush=True)
            last_update = now
    return failures


def validate_audit(audit: dict, source: Path) -> None:
    if audit.get("audit_version") != AUDIT_VERSION:
        raise RuntimeError("Unsupported or missing audit version")
    if Path(audit.get("source_root", "")) != source:
        raise RuntimeError("Audit source path does not match requested source")
    if not audit.get("passed"):
        raise RuntimeError("Audit did not pass; refusing to continue")


def run_backup(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    backup = args.backup.resolve()
    audit = load_json(args.audit_report.resolve())
    validate_audit(audit, source)
    if backup == source or source in backup.parents or backup in source.parents:
        raise RuntimeError("Source and backup must be separate sibling trees")

    marker = backup / ".dataset_backup_in_progress.json"
    manifest_path = backup / "backup_manifest.json"
    if backup.exists():
        if manifest_path.is_file():
            manifest = load_json(manifest_path)
            if manifest.get("source_root") != str(source):
                raise RuntimeError(f"Existing backup belongs to another source: {backup}")
            failures = verify_manifest_files(backup, manifest["files"], "BACKUP-VERIFY")
            if failures:
                raise RuntimeError(f"Existing completed backup failed verification: {failures[:10]}")
            print(f"[BACKUP-DONE] existing verified backup={backup}", flush=True)
            return 0
        if not marker.is_file():
            raise RuntimeError(f"Backup directory already exists without a resumable marker: {backup}")
        marker_data = load_json(marker)
        if marker_data.get("source_root") != str(source):
            raise RuntimeError(f"Backup resume marker belongs to another source: {backup}")
    else:
        backup.mkdir(parents=True)
        save_json(marker, {"source_root": str(source), "created_at": now_iso()})

    selection_path = backup / ".dataset_backup_selection.json"
    if selection_path.is_file():
        selection = load_json(selection_path)
        if selection.get("source_root") != str(source):
            raise RuntimeError("Saved backup selection belongs to another source")
        files = selection["files"]
        directories = selection["directories"]
        print("[BACKUP] reusing saved exact file manifest", flush=True)
    else:
        print("[BACKUP] building exact file manifest", flush=True)
        files, directories = selected_backup_entries(source, audit)
        save_json(
            selection_path,
            {
                "source_root": str(source),
                "created_at": now_iso(),
                "directories": directories,
                "files": files,
            },
        )
    total_bytes = sum(entry["size"] for entry in files)
    print(
        f"[BACKUP] directories={len(directories)} files={len(files)} "
        f"bytes={total_bytes}",
        flush=True,
    )
    marker_data = load_json(marker)
    if marker_data.get("directories_initialized"):
        print("[BACKUP] reusing previously initialized directory tree", flush=True)
    else:
        for relative in directories:
            (backup / relative).mkdir(parents=True, exist_ok=True)
        marker_data["directories_initialized"] = True
        marker_data["directory_count"] = len(directories)
        marker_data["directories_initialized_at"] = now_iso()
        save_json(marker, marker_data)

    entries_by_event: dict[str, list[dict]] = defaultdict(list)
    for entry in files:
        event_relative = "/".join(entry["path"].split("/")[:4])
        entries_by_event[event_relative].append(entry)

    progress_lock = threading.Lock()
    copied_bytes = 0
    skipped_bytes = 0
    copied_files = 0
    skipped_files = 0
    started = time.monotonic()
    last_print = started

    def copy_event(event_relative: str, event_entries: list[dict]) -> tuple[int, int, int, int]:
        event_copied_bytes = 0
        event_skipped_bytes = 0
        event_copied_files = 0
        event_skipped_files = 0
        for entry in event_entries:
            size, skipped = copy_file_atomic(
                source / entry["path"], backup / entry["path"], entry["size"]
            )
            if skipped:
                event_skipped_bytes += size
                event_skipped_files += 1
            else:
                event_copied_bytes += size
                event_copied_files += 1
        return event_copied_bytes, event_skipped_bytes, event_copied_files, event_skipped_files

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(copy_event, event, event_entries): event
            for event, event_entries in entries_by_event.items()
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_map):
            event = future_map[future]
            try:
                cb, sb, cf, sf = future.result()
            except Exception as error:
                raise RuntimeError(f"Backup failed for {event}: {error}") from error
            with progress_lock:
                copied_bytes += cb
                skipped_bytes += sb
                copied_files += cf
                skipped_files += sf
            completed += 1
            now = time.monotonic()
            if completed == len(future_map) or now - last_print >= 30:
                done_bytes = copied_bytes + skipped_bytes
                percent = 100 * done_bytes / total_bytes if total_bytes else 100.0
                elapsed = now - started
                rate = copied_bytes / elapsed if elapsed else 0.0
                print(
                    f"[BACKUP] events={completed}/{len(future_map)} files_copied={copied_files} "
                    f"files_skipped={skipped_files} progress={percent:.2f}% "
                    f"write_rate_MiB_s={rate / 1048576:.2f}",
                    flush=True,
                )
                last_print = now

    failures = verify_manifest_files(backup, files, "BACKUP-VERIFY")
    if failures:
        raise RuntimeError(f"Backup verification failed: {failures[:20]}")

    manifest = {
        "manifest_version": 1,
        "created_at": now_iso(),
        "source_root": str(source),
        "backup_root": str(backup),
        "source_audit_report": str(args.audit_report.resolve()),
        "event_count": audit["event_count"],
        "directory_count": len(directories),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "directories": directories,
        "files": files,
        "verification": "all destination relative paths exist with the recorded byte size",
    }
    save_json(manifest_path, manifest)
    marker.unlink(missing_ok=True)
    selection_path.unlink(missing_ok=True)
    print(
        f"[BACKUP-DONE] events={audit['event_count']} files={len(files)} "
        f"bytes={total_bytes} backup={backup}",
        flush=True,
    )
    return 0


def backup_entries_by_event(manifest: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for entry in manifest["files"]:
        event_relative = "/".join(entry["path"].split("/")[:4])
        grouped[event_relative].append(entry)
    return grouped


def verify_event_backup(backup: Path, entries: list[dict]) -> None:
    for entry in entries:
        path = backup / entry["path"]
        if not path.is_file() or path.stat().st_size != entry["size"]:
            raise RuntimeError(f"Backup is missing or has changed: {entry['path']}")


def active_video_matches(video_dir: Path, video_info: dict, masked: bool) -> bool:
    if not video_dir.is_dir():
        return False
    expected = set(video_info)
    current = {
        item.name
        for item in video_dir.iterdir()
        if item.is_file() and item.suffix.lower() == ".mp4"
    }
    if current != expected:
        return False
    size_key = "masked_size" if masked else "source_size"
    return all((video_dir / name).stat().st_size == info[size_key] for name, info in video_info.items())


def promote_one_event(event: Path, result: dict, backup: Path, backup_entries: list[dict]) -> None:
    verify_event_backup(backup, backup_entries)
    video = event / "video"
    ready = event / ".video_masked_ready"
    old = event / ".video_unmasked_old"
    video_info = result["videos"]

    # Already promoted, possibly awaiting only event-root cleanup.
    if not ready.exists() and not old.exists() and active_video_matches(video, video_info, masked=True):
        pass
    else:
        if not ready.exists() and not old.exists():
            masked = video / "video_masked"
            if not active_video_matches(video, video_info, masked=False):
                raise RuntimeError("current unmasked video directory no longer matches the audit")
            if not active_video_matches(masked, video_info, masked=True):
                raise RuntimeError("video_masked directory no longer matches the audit")
            masked.replace(ready)

        if ready.exists() and video.exists() and not old.exists():
            video.replace(old)

        if ready.exists() and old.exists() and not video.exists():
            ready.replace(video)

        if ready.exists():
            raise RuntimeError("unexpected residual .video_masked_ready state")
        if not old.exists() or not active_video_matches(video, video_info, masked=True):
            raise RuntimeError("masked video promotion did not produce the audited video set")
        shutil.rmtree(old)

    keep = set(result["csv_names"])
    keep.add(result["json_name"])
    keep.add("video")
    with os.scandir(event) as entries:
        removals = [Path(entry.path) for entry in entries if entry.name not in keep]
    for path in removals:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    current = visible_names(event)
    if current != keep:
        raise RuntimeError(f"final event-root set mismatch: {sorted(current)} != {sorted(keep)}")
    if not active_video_matches(video, video_info, masked=True):
        raise RuntimeError("final active video directory does not match audited masked videos")
    if visible_names(video) != set(video_info):
        raise RuntimeError("final video directory contains unexpected entries")


def run_promote(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    backup = args.backup.resolve()
    audit = load_json(args.audit_report.resolve())
    validate_audit(audit, source)
    manifest_path = backup / "backup_manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("Completed backup manifest is missing; refusing to modify source")
    manifest = load_json(manifest_path)
    if manifest.get("source_root") != str(source) or manifest.get("event_count") != audit["event_count"]:
        raise RuntimeError("Backup manifest does not match the source audit")
    print("[PROMOTE] verifying complete backup before any source changes", flush=True)
    failures = verify_manifest_files(backup, manifest["files"], "BACKUP-PRE-PROMOTE")
    if failures:
        raise RuntimeError(f"Backup failed pre-promotion verification: {failures[:20]}")

    grouped = backup_entries_by_event(manifest)
    if set(grouped) != set(audit["events"]):
        raise RuntimeError("Backup event set does not match audit event set")

    last_update = time.monotonic()
    for index, (relative, result) in enumerate(audit["events"].items(), start=1):
        event = source / relative
        try:
            promote_one_event(event, result, backup, grouped[relative])
        except Exception as error:
            raise RuntimeError(f"Promotion failed for {relative}: {error}") from error
        now = time.monotonic()
        if index == audit["event_count"] or now - last_update >= 30:
            print(f"[PROMOTE] events={index}/{audit['event_count']}", flush=True)
            last_update = now
    print(f"[PROMOTE-DONE] events={audit['event_count']} source={source}", flush=True)
    return 0


def move_directory_to_backup(source: Path, destination: Path) -> None:
    """Move a directory to its backup location, safely resuming if already moved."""

    if source.is_dir():
        if destination.exists():
            if not destination.is_dir():
                raise RuntimeError(f"Backup destination is not a directory: {destination}")
            shutil.rmtree(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source.replace(destination)
    elif not destination.is_dir():
        raise RuntimeError(f"Directory is absent from both source and backup: {source}")


def move_file_to_backup(source: Path, destination: Path, expected_size: int) -> None:
    """Move a file to its backup location, safely resuming if already moved."""

    partial = destination.with_name(destination.name + ".partial")
    partial.unlink(missing_ok=True)
    if source.is_file():
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        source.replace(destination)
    elif not destination.is_file():
        raise RuntimeError(f"File is absent from both source and backup: {source}")
    if destination.stat().st_size != expected_size:
        raise RuntimeError(
            f"Relocated file size mismatch: {destination} "
            f"{destination.stat().st_size} != {expected_size}"
        )


def promote_masked_after_relocation(event: Path, result: dict) -> None:
    """Promote video_masked after the seven unmasked MP4s have been relocated."""

    video = event / "video"
    ready = event / ".video_masked_ready"
    video_info = result["videos"]

    if active_video_matches(video, video_info, masked=True) and not ready.exists():
        return

    masked = video / "video_masked"
    if masked.is_dir() and not ready.exists():
        if not active_video_matches(masked, video_info, masked=True):
            raise RuntimeError("video_masked no longer matches the audited masked videos")
        masked.replace(ready)

    if ready.is_dir():
        if video.exists():
            remaining = visible_names(video)
            if remaining:
                raise RuntimeError(
                    f"unmasked video directory still contains entries before promotion: {sorted(remaining)}"
                )
            (video / ".DS_Store").unlink(missing_ok=True)
            video.rmdir()
        ready.replace(video)

    if not active_video_matches(video, video_info, masked=True):
        raise RuntimeError("masked video promotion did not produce the audited video set")
    if visible_names(video) != set(video_info):
        raise RuntimeError("promoted video directory contains unexpected entries")


def run_relocate(args: argparse.Namespace) -> int:
    """Build the requested backup by relocating soon-to-be-removed assets."""

    source = args.source.resolve()
    backup = args.backup.resolve()
    audit = load_json(args.audit_report.resolve())
    validate_audit(audit, source)
    marker = backup / ".dataset_backup_in_progress.json"
    selection_path = backup / ".dataset_backup_selection.json"
    manifest_path = backup / "backup_manifest.json"
    if manifest_path.is_file():
        raise RuntimeError("Completed backup manifest already exists; use verify instead")
    if not marker.is_file() or not selection_path.is_file():
        raise RuntimeError("Resumable backup marker or exact selection manifest is missing")
    if source.stat().st_dev != backup.stat().st_dev:
        raise RuntimeError("Source and backup are not on the same filesystem; atomic relocation is unsafe")

    marker_data = load_json(marker)
    selection = load_json(selection_path)
    if marker_data.get("source_root") != str(source) or selection.get("source_root") != str(source):
        raise RuntimeError("Backup continuation metadata does not match the source")
    files = selection["files"]
    directories = selection["directories"]
    file_sizes = {entry["path"]: entry["size"] for entry in files}
    grouped = backup_entries_by_event({"files": files})
    if set(grouped) != set(audit["events"]):
        raise RuntimeError("Saved backup selection event set does not match audit event set")

    print(
        f"[RELOCATE] events={audit['event_count']} files={len(files)} "
        f"bytes={sum(entry['size'] for entry in files)} workers={args.workers}",
        flush=True,
    )

    def relocate_event(relative: str, result: dict) -> int:
        event = source / relative
        backup_event = backup / relative
        backup_event.mkdir(parents=True, exist_ok=True)
        already_promoted = (
            active_video_matches(event / "video", result["videos"], masked=True)
            and not (event / "video" / "video_masked").exists()
            and not (event / ".video_masked_ready").exists()
        )

        # CSVs remain in Dataset, so these four files are copied rather than moved.
        for name in result["csv_names"]:
            relpath = f"{relative}/{name}"
            copy_file_atomic(event / name, backup / relpath, file_sizes[relpath])

        # These three directories are destined to disappear from Dataset and can
        # be moved atomically within the same SMB filesystem.
        for directory_name in ("clip", "video_cfr30_metadata", "video_original_h265"):
            move_directory_to_backup(
                event / directory_name,
                backup_event / directory_name,
            )

        # Move the seven current unmasked MP4s out while retaining video_masked.
        backup_video = backup_event / "video"
        backup_video.mkdir(parents=True, exist_ok=True)
        if not already_promoted:
            for name in sorted(result["videos"]):
                relpath = f"{relative}/video/{name}"
                move_file_to_backup(event / "video" / name, backup / relpath, file_sizes[relpath])

        promote_masked_after_relocation(event, result)

        keep = set(result["csv_names"])
        keep.add(result["json_name"])
        keep.add("video")
        with os.scandir(event) as entries:
            removals = [Path(entry.path) for entry in entries if entry.name not in keep]
        for path in removals:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

        if visible_names(event) != keep:
            raise RuntimeError("final event-root set does not match requested reduced structure")
        if visible_names(event / "video") != set(result["videos"]):
            raise RuntimeError("final video set does not contain exactly seven masked videos")
        verify_event_backup(backup, grouped[relative])
        return sum(entry["size"] for entry in grouped[relative])

    completed = 0
    completed_bytes = 0
    total_bytes = sum(entry["size"] for entry in files)
    last_update = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(relocate_event, relative, result): relative
            for relative, result in audit["events"].items()
        }
        for future in concurrent.futures.as_completed(future_map):
            relative = future_map[future]
            try:
                completed_bytes += future.result()
            except Exception as error:
                raise RuntimeError(f"Relocation failed for {relative}: {error}") from error
            completed += 1
            now = time.monotonic()
            if completed == len(future_map) or now - last_update >= 30:
                print(
                    f"[RELOCATE] events={completed}/{len(future_map)} "
                    f"logical_bytes={100 * completed_bytes / total_bytes:.2f}%",
                    flush=True,
                )
                last_update = now

    backup_failures = verify_manifest_files(backup, files, "RELOCATE-BACKUP-VERIFY")
    if backup_failures:
        raise RuntimeError(f"Relocated backup verification failed: {backup_failures[:20]}")
    missing_directories = [relative for relative in directories if not (backup / relative).is_dir()]
    if missing_directories:
        raise RuntimeError(f"Relocated backup directories are missing: {missing_directories[:20]}")

    manifest = {
        "manifest_version": 1,
        "created_at": now_iso(),
        "source_root": str(source),
        "backup_root": str(backup),
        "source_audit_report": str(args.audit_report.resolve()),
        "event_count": audit["event_count"],
        "directory_count": len(directories),
        "file_count": len(files),
        "total_bytes": total_bytes,
        "directories": directories,
        "files": files,
        "backup_method": (
            "four CSV files copied per event; clip, video_cfr30_metadata, "
            "video_original_h265, and seven unmasked videos atomically relocated "
            "within the same filesystem"
        ),
        "verification": "all backup relative paths exist with the recorded byte size",
    }
    save_json(manifest_path, manifest)
    marker.unlink(missing_ok=True)
    selection_path.unlink(missing_ok=True)
    print(
        f"[RELOCATE-DONE] events={audit['event_count']} files={len(files)} "
        f"bytes={total_bytes} backup={backup}",
        flush=True,
    )
    return 0


def verify_final_event(event: Path, root: Path, result: dict, tolerance: float) -> list[str]:
    failures: list[str] = []
    keep = set(result["csv_names"])
    keep.add(result["json_name"])
    keep.add("video")
    try:
        current = visible_names(event)
        if current != keep:
            failures.append(f"event-root set mismatch: {sorted(current)} != {sorted(keep)}")
        video = event / "video"
        if visible_names(video) != set(result["videos"]):
            failures.append("final video filename set mismatch")
        else:
            for name, info in result["videos"].items():
                path = video / name
                if path.stat().st_size != info["masked_size"]:
                    failures.append(f"masked size mismatch after promotion: {name}")
                    continue
                duration, _, _ = mp4_duration(path)
                if abs(duration - info["masked_duration_s"]) > tolerance:
                    failures.append(
                        f"masked duration changed after promotion: {name} "
                        f"{duration:.9f} != {info['masked_duration_s']:.9f}"
                    )
    except Exception as error:
        failures.append(f"unhandled final verification error: {error}")
    return failures


def run_verify(args: argparse.Namespace) -> int:
    source = args.source.resolve()
    backup = args.backup.resolve()
    audit = load_json(args.audit_report.resolve())
    validate_audit(audit, source)
    manifest = load_json(backup / "backup_manifest.json")
    backup_failures = verify_manifest_files(backup, manifest["files"], "FINAL-BACKUP")

    current_events = discover_events(source)
    current_relatives = {path.relative_to(source).as_posix() for path in current_events}
    expected_relatives = set(audit["events"])
    event_set_failures = []
    if current_relatives != expected_relatives:
        event_set_failures.append(
            f"event set mismatch: missing={sorted(expected_relatives-current_relatives)[:20]} "
            f"extra={sorted(current_relatives-expected_relatives)[:20]}"
        )

    source_failures: list[dict] = []
    last_update = time.monotonic()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_map = {
            executor.submit(
                verify_final_event,
                source / relative,
                source,
                result,
                audit["duration_tolerance_s"],
            ): relative
            for relative, result in audit["events"].items()
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_map):
            relative = future_map[future]
            try:
                failures = future.result()
            except Exception as error:
                failures = [f"unhandled future error: {error}"]
            if failures:
                source_failures.append({"event": relative, "failures": failures})
            completed += 1
            now = time.monotonic()
            if completed == len(future_map) or now - last_update >= 30:
                print(
                    f"[FINAL-SOURCE] events={completed}/{len(future_map)} "
                    f"failures={len(source_failures)}",
                    flush=True,
                )
                last_update = now

    report = {
        "created_at": now_iso(),
        "passed": not backup_failures and not event_set_failures and not source_failures,
        "source_root": str(source),
        "backup_root": str(backup),
        "event_count": audit["event_count"],
        "backup_file_count": manifest["file_count"],
        "backup_total_bytes": manifest["total_bytes"],
        "backup_failures": backup_failures,
        "event_set_failures": event_set_failures,
        "source_failures": source_failures,
    }
    save_json(args.report.resolve(), report)
    print(
        f"[VERIFY-DONE] passed={report['passed']} events={report['event_count']} "
        f"backup_failures={len(backup_failures)} source_failures={len(source_failures)} "
        f"report={args.report.resolve()}",
        flush=True,
    )
    return 0 if report["passed"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--source", type=Path, required=True)
    common.add_argument("--workers", type=int, default=12)

    audit_parser = subparsers.add_parser("audit", parents=[common])
    audit_parser.add_argument("--report", type=Path, required=True)
    audit_parser.add_argument("--duration-tolerance-s", type=float, default=0.001)
    audit_parser.set_defaults(function=run_audit)

    backup_parser = subparsers.add_parser("backup", parents=[common])
    backup_parser.add_argument("--backup", type=Path, required=True)
    backup_parser.add_argument("--audit-report", type=Path, required=True)
    backup_parser.set_defaults(function=run_backup)

    promote_parser = subparsers.add_parser("promote", parents=[common])
    promote_parser.add_argument("--backup", type=Path, required=True)
    promote_parser.add_argument("--audit-report", type=Path, required=True)
    promote_parser.set_defaults(function=run_promote)

    relocate_parser = subparsers.add_parser("relocate", parents=[common])
    relocate_parser.add_argument("--backup", type=Path, required=True)
    relocate_parser.add_argument("--audit-report", type=Path, required=True)
    relocate_parser.set_defaults(function=run_relocate)

    verify_parser = subparsers.add_parser("verify", parents=[common])
    verify_parser.add_argument("--backup", type=Path, required=True)
    verify_parser.add_argument("--audit-report", type=Path, required=True)
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.set_defaults(function=run_verify)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.workers <= 0:
        raise ValueError("--workers must be positive")
    try:
        return args.function(args)
    except KeyboardInterrupt:
        print("[INTERRUPTED] safe to rerun the same command", file=sys.stderr, flush=True)
        return 130
    except Exception as error:
        print(f"[ERROR] {error}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
