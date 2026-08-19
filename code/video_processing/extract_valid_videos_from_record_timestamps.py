"""Re-extract valid-event MP4 clips from each record.json Timestamp interval.

Running this script archives an existing event/video/mp4 folder as mp4_old,
then writes the newly extracted seven-camera clips to event/video/mp4.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from rosbags.highlevel import AnyReader


DEFAULT_VALID_ROOTS = {
    "Nanjing": (
        Path(os.environ.get("TLCD_NANJING_VALID_ROOT", "Nanjing_valid")),
        Path(os.environ.get("TLCD_NANJING_SOURCE_ROOT", "Nanjing")),
    ),
    "Changchun": (
        Path(os.environ.get("TLCD_CHANGCHUN_VALID_ROOT", "Changchun_valid")),
        Path(os.environ.get("TLCD_CHANGCHUN_SOURCE_ROOT", "Changchun")),
    ),
}
DEFAULT_FFMPEG_BIN = "ffmpeg"
TOPICS = {
    "120": "J5_1_H265_120",
    "30": "J5_1_H265_30",
    "rear": "VH_2_H265_Rear",
    "Side_B": "VH_2_H265_Side_B",
    "Side_F": "VH_2_H265_Side_F",
    "Side_L": "VH_2_H265_Side_L",
    "Side_R": "VH_2_H265_Side_R",
}
TOPIC_TO_KEY = {topic: key for key, topic in TOPICS.items()}
EVENT_NUMBER_PATTERN = re.compile(r"^event_(\d+)$")
SEGMENT_DATE_PATTERN = re.compile(r"\d{8}")


@dataclass(order=True)
class EventWindow:
    start_ns: int
    end_ns: int
    event_dir: Path = field(compare=False)
    event_number: int = field(compare=False)
    record_path: Path = field(compare=False)
    h265_paths: dict[str, Path] = field(default_factory=dict, compare=False)
    mp4_paths: dict[str, Path] = field(default_factory=dict, compare=False)
    handles: dict[str, object] = field(default_factory=dict, compare=False)
    message_counts: dict[str, int] = field(default_factory=dict, compare=False)
    opened: bool = field(default=False, compare=False)
    closed: bool = field(default=False, compare=False)

    def open_files(self) -> None:
        if self.opened:
            return
        for path in self.h265_paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        self.handles = {key: path.open("wb") for key, path in self.h265_paths.items()}
        self.opened = True

    def close_files(self) -> None:
        if self.closed:
            return
        for handle in self.handles.values():
            handle.close()
        self.closed = True


def format_elapsed(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, remain = divmod(seconds, 60)
    return f"{int(minutes)}m{remain:05.2f}s"


def ensure_ffmpeg(ffmpeg_bin: str) -> None:
    if shutil.which(ffmpeg_bin) is None:
        raise RuntimeError(f"ffmpeg not found in PATH: {ffmpeg_bin}")


def wrap_h265_to_mp4(ffmpeg_bin: str, h265_path: Path, mp4_path: Path) -> None:
    result = subprocess.run(
        [ffmpeg_bin, "-y", "-f", "hevc", "-i", str(h265_path), "-c", "copy", str(mp4_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed for {h265_path}:\n{result.stderr}")


def event_number(event_dir: Path) -> int:
    match = EVENT_NUMBER_PATTERN.fullmatch(event_dir.name)
    if match is None:
        raise ValueError(f"unexpected event directory name: {event_dir}")
    return int(match.group(1))


def read_window(record_path: Path) -> tuple[int, int]:
    record = json.loads(record_path.read_text(encoding="utf-8-sig"))
    timestamp = record.get("Timestamp")
    if not isinstance(timestamp, dict):
        raise ValueError("Timestamp is missing")

    start_ms = timestamp.get("t_start")
    end_ms = timestamp.get("t_end")
    if not isinstance(start_ms, int) or not isinstance(end_ms, int):
        raise ValueError("Timestamp.t_start and Timestamp.t_end must be integers")
    if len(str(start_ms)) != 13 or len(str(end_ms)) != 13:
        raise ValueError("Timestamp values must be 13-digit Unix milliseconds")
    if end_ms <= start_ms:
        raise ValueError("Timestamp.t_end must be later than Timestamp.t_start")
    return start_ms * 1_000_000, end_ms * 1_000_000


def discover_events(valid_root: Path) -> dict[str, list[EventWindow]]:
    groups: dict[str, list[EventWindow]] = defaultdict(list)
    for record_path in sorted(valid_root.rglob("*_record.json")):
        event_dir = record_path.parent
        start_ns, end_ns = read_window(record_path)
        groups[event_dir.parent.name].append(
            EventWindow(
                start_ns=start_ns,
                end_ns=end_ns,
                event_dir=event_dir,
                event_number=event_number(event_dir),
                record_path=record_path,
            )
        )
    for events in groups.values():
        events.sort()
    return groups


def segment_name_candidates(segment_name: str) -> list[str]:
    names = [segment_name]
    match = re.fullmatch(r"(.+)__\d{8}", segment_name)
    if match:
        names.append(match.group(1))
    return names


def source_csv_dir(source_root: Path, segment_name: str) -> Path:
    names = segment_name_candidates(segment_name)
    date_names = SEGMENT_DATE_PATTERN.findall(segment_name)
    date_dirs = [source_root / date_name for date_name in date_names]
    date_dirs.extend(sorted(path for path in source_root.iterdir() if path.is_dir()))

    seen: set[Path] = set()
    for date_dir in date_dirs:
        if date_dir in seen:
            continue
        seen.add(date_dir)
        selected_root = date_dir / "csv_selected"
        if not selected_root.is_dir():
            continue
        for name in names:
            candidate = selected_root / name / "CSV"
            if candidate.is_dir():
                return candidate
        for candidate in selected_root.glob(f"{segment_name}__*/CSV"):
            if candidate.is_dir():
                return candidate
    raise FileNotFoundError(f"csv_selected segment is unavailable: {segment_name}")


def find_bag(source_date_dir: Path, source_segment_name: str) -> Path:
    bag_root = source_date_dir / "bag"
    if not bag_root.is_dir():
        raise FileNotFoundError(f"bag directory not found: {bag_root}")

    segment_key = re.sub(r"_CSV$", "", source_segment_name)
    direct_candidates = [
        bag_root / f"{segment_key}.bag",
        bag_root / f"{segment_key}_bag",
    ]
    for candidate in direct_candidates:
        if candidate.exists():
            return candidate

    matches = [
        path
        for path in bag_root.rglob("*")
        if (path.is_dir() or path.suffix == ".bag") and segment_key in path.name
    ]
    if not matches:
        raise FileNotFoundError(f"no bag matched segment '{segment_key}' under {bag_root}")
    matches.sort(key=lambda path: (path.name == segment_key, path.suffix == ".bag", str(path)), reverse=True)
    return matches[0]


def output_paths(event: EventWindow) -> tuple[Path, Path]:
    video_dir = event.event_dir / "video"
    mp4_dir = video_dir / "mp4"
    old_mp4_dir = video_dir / "mp4_old"
    if mp4_dir.is_dir() and not old_mp4_dir.exists():
        mp4_dir.rename(old_mp4_dir)
    elif mp4_dir.exists() and not mp4_dir.is_dir():
        raise ValueError(f"mp4 path is not a directory: {mp4_dir}")

    mp4_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = video_dir / "h265_tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return mp4_dir, temp_dir


def event_outputs_complete(event: EventWindow) -> bool:
    return all(path.is_file() and path.stat().st_size > 0 for path in event.mp4_paths.values())


def prepare_event(event: EventWindow) -> bool:
    mp4_dir, temp_dir = output_paths(event)
    event.mp4_paths = {
        key: mp4_dir / f"video_{key}_event_{event.event_number:03d}.mp4"
        for key in TOPICS
    }
    event.h265_paths = {
        key: temp_dir / f"video_{key}_event_{event.event_number:03d}.h265"
        for key in TOPICS
    }
    event.message_counts = {key: 0 for key in TOPICS}
    if event_outputs_complete(event):
        return False

    for path in event.mp4_paths.values():
        path.unlink(missing_ok=True)
    for path in event.h265_paths.values():
        path.unlink(missing_ok=True)
    return True


def finalize_event(event: EventWindow, ffmpeg_bin: str, keep_temp_h265: bool) -> None:
    event.close_files()
    for key in TOPICS:
        h265_path = event.h265_paths[key]
        mp4_path = event.mp4_paths[key]
        if event.message_counts[key] == 0 or not h265_path.is_file() or h265_path.stat().st_size == 0:
            h265_path.unlink(missing_ok=True)
            continue
        wrap_h265_to_mp4(ffmpeg_bin, h265_path, mp4_path)
        if not keep_temp_h265:
            h265_path.unlink(missing_ok=True)


def extract_segment(
    city: str,
    source_root: Path,
    segment_name: str,
    events: list[EventWindow],
    ffmpeg_bin: str,
    keep_temp_h265: bool,
) -> None:
    started_at = time.perf_counter()
    source_dir = source_csv_dir(source_root, segment_name)
    bag_path = find_bag(source_dir.parents[2], source_dir.parent.name)
    pending_events = [event for event in events if prepare_event(event)]
    if not pending_events:
        print(f"[SEGMENT-SKIP] city={city} segment={segment_name} reason=outputs_complete", flush=True)
        return

    print(
        f"[SEGMENT-START] city={city} segment={segment_name} "
        f"events={len(pending_events)} bag={bag_path}",
        flush=True,
    )
    start_ns = min(event.start_ns for event in pending_events)
    stop_ns = max(event.end_ns for event in pending_events) + 1
    next_index = 0
    active: list[EventWindow] = []
    scanned = 0

    with AnyReader([bag_path]) as reader:
        connections = [connection for connection in reader.connections if connection.topic in TOPIC_TO_KEY]
        if not connections:
            raise RuntimeError(f"no configured video topics in bag: {bag_path}")
        for connection, timestamp, rawdata in reader.messages(
            connections=connections,
            start=start_ns,
            stop=stop_ns,
        ):
            scanned += 1
            while next_index < len(pending_events) and pending_events[next_index].start_ns <= timestamp:
                event = pending_events[next_index]
                event.open_files()
                active.append(event)
                next_index += 1

            still_active = []
            for event in active:
                if timestamp > event.end_ns:
                    finalize_event(event, ffmpeg_bin, keep_temp_h265)
                else:
                    still_active.append(event)
            active = still_active

            key = TOPIC_TO_KEY[connection.topic]
            for event in active:
                if event.start_ns <= timestamp <= event.end_ns:
                    event.handles[key].write(rawdata)
                    event.message_counts[key] += 1

    for event in active:
        finalize_event(event, ffmpeg_bin, keep_temp_h265)
    for event in pending_events[next_index:]:
        print(f"[EVENT-WARN] no video message reached event: {event.record_path}", flush=True)

    counts = " ".join(
        f"{key}={sum(event.message_counts[key] for event in pending_events)}" for key in TOPICS
    )
    print(
        f"[SEGMENT-DONE] city={city} segment={segment_name} scanned={scanned} "
        f"elapsed={format_elapsed(time.perf_counter() - started_at)}\n[COUNTS] {counts}",
        flush=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Re-extract valid-event MP4 clips from record.json Timestamp intervals."
    )
    parser.add_argument("--ffmpeg", default=DEFAULT_FFMPEG_BIN, help="ffmpeg binary name or full path")
    parser.add_argument(
        "--keep-temp-h265",
        action="store_true",
        help="Keep intermediate H265 streams under each event/video/h265_tmp directory.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    ensure_ffmpeg(args.ffmpeg)
    failures: list[tuple[str, str, str]] = []
    total_segments = 0
    started_at = time.perf_counter()

    for city, (valid_root, source_root) in DEFAULT_VALID_ROOTS.items():
        groups = discover_events(valid_root)
        print(f"[INFO] city={city} events={sum(map(len, groups.values()))} segments={len(groups)}", flush=True)
        for segment_name, events in groups.items():
            total_segments += 1
            try:
                extract_segment(city, source_root, segment_name, events, args.ffmpeg, args.keep_temp_h265)
            except Exception as error:
                failures.append((city, segment_name, str(error)))
                print(f"[SEGMENT-ERROR] city={city} segment={segment_name}\n  {error}", flush=True)

    print(
        f"[DONE] segments={total_segments} failed={len(failures)} "
        f"elapsed={format_elapsed(time.perf_counter() - started_at)}",
        flush=True,
    )
    for city, segment_name, reason in failures:
        print(f"[FAILED] city={city} segment={segment_name}\n  {reason}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
