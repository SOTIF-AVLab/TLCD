"""Batch-regenerate all released TLCD event videos at a fixed frame rate.

The default workflow operates only on released events under ``Dataset``. It
normalizes each existing H.265 source-video sequence across the event window in
``*_record.json`` and writes validated H.265 videos to
``video_cfr30_staging``. Original videos are never overwritten. Reading ROS bag
timestamps remains available only as an optional diagnostic mode.
"""

from __future__ import annotations

import argparse
import bisect
import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from rosbags.rosbag1 import Reader

import regenerate_fixed_fps_event_videos as fixed


def discover_events(dataset_root: Path, manifest_path: Path | None = None) -> list[Path]:
    dataset_root = dataset_root.resolve()
    if manifest_path and manifest_path.is_file():
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        if data.get("dataset_root") == str(dataset_root):
            return [Path(path) for path in data["event_dirs"]]

    event_dirs = sorted(
        path
        for path in dataset_root.glob("*_valid/*/*/event_*")
        if path.is_dir()
    )
    if manifest_path:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        partial = manifest_path.with_suffix(".partial.json")
        partial.write_text(
            json.dumps(
                {
                    "dataset_root": str(dataset_root),
                    "event_dirs": [str(path) for path in event_dirs],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        partial.replace(manifest_path)
    return event_dirs


def city_and_segment(event_dir: Path) -> tuple[str, str]:
    city = event_dir.parents[2].name.removesuffix("_valid")
    segment = event_dir.parent.name.removesuffix("_CSV")
    segment = re.sub(r"__20\d{6}$", "", segment)
    return city, segment


def segment_date(segment: str, event_dir: Path) -> str:
    match = re.search(r"20\d{6}", segment)
    if match:
        return match.group(0)
    record = next(event_dir.glob("*_record.json"))
    data = json.loads(record.read_text(encoding="utf-8-sig"))
    return str(data["Date"]).replace("-", "")


def find_bag(source_root: Path, event_dir: Path) -> Path:
    city, segment = city_and_segment(event_dir)
    bag_root = source_root / city / segment_date(segment, event_dir) / "bag"
    direct = bag_root / f"{segment}.bag"
    if direct.is_file():
        return direct
    candidates = sorted(bag_root.glob(f"*{segment}*.bag"))
    if len(candidates) != 1:
        raise FileNotFoundError(
            f"Expected one bag for {event_dir}, found {len(candidates)} under {bag_root}"
        )
    return candidates[0]


def staging_dir(event_dir: Path, fps: int) -> Path:
    return event_dir / f"video_cfr{fps}_staging"


def timestamp_cache_path(event_dir: Path, fps: int) -> Path:
    return staging_dir(event_dir, fps) / "source_camera_timestamps_ns.json"


def cache_matches(
    path: Path,
    window: fixed.EventWindow,
    timestamp_source: str,
) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return (
            data["event_start_ns"] == window.start_ns
            and data["event_end_ns"] == window.end_ns
            and data.get("timestamp_source", "bag") == timestamp_source
            and all(data["timestamps_ns"][key] for key in fixed.TOPICS)
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def write_timestamp_cache(
    event_dir: Path,
    fps: int,
    window: fixed.EventWindow,
    timestamps: dict[str, list[int]],
    timestamp_source: str,
) -> None:
    path = timestamp_cache_path(event_dir, fps)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".partial.json")
    partial.write_text(
        json.dumps(
            {
                "event_start_ns": window.start_ns,
                "event_end_ns": window.end_ns,
                "timestamp_source": timestamp_source,
                "timestamps_ns": timestamps,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    partial.replace(path)


def cache_group_timestamps(
    bag_path: Path,
    event_dirs: list[Path],
    fps: int,
) -> int:
    events = [(event_dir, fixed.read_event_window(event_dir)) for event_dir in event_dirs]
    pending = [
        item
        for item in events
        if not cache_matches(timestamp_cache_path(item[0], fps), item[1], "bag")
    ]
    if not pending:
        return 0

    minimum = min(window.start_ns for _, window in pending)
    maximum = max(window.end_ns for _, window in pending)
    topic_times: dict[str, list[int]] = {}
    with Reader(bag_path) as reader:
        by_topic = {connection.topic: connection for connection in reader.connections}
        for key, topic in fixed.TOPICS.items():
            connection = by_topic.get(topic)
            if connection is None:
                raise RuntimeError(f"Topic {topic!r} is absent from {bag_path}")
            times = [entry.time for entry in reader.indexes[connection.id]]
            first = bisect.bisect_left(times, minimum)
            last = bisect.bisect_right(times, maximum)
            topic_times[key] = times[first:last]

    for event_dir, window in pending:
        timestamps = {}
        for key, times in topic_times.items():
            first = bisect.bisect_left(times, window.start_ns)
            last = bisect.bisect_right(times, window.end_ns)
            timestamps[key] = times[first:last]
            if not timestamps[key]:
                raise RuntimeError(f"No {fixed.TOPICS[key]!r} messages for {event_dir}")
        write_timestamp_cache(event_dir, fps, window, timestamps, "bag")
    return len(pending)


def cache_event_window_timestamps(event_dir: Path, fps: int) -> bool:
    window = fixed.read_event_window(event_dir)
    cache_path = timestamp_cache_path(event_dir, fps)
    if cache_matches(cache_path, window, "event-window"):
        return False
    timestamps = fixed.normalized_camera_timestamps(
        fixed.source_video_paths(event_dir),
        window,
    )
    write_timestamp_cache(
        event_dir,
        fps,
        window,
        timestamps,
        "event-window",
    )
    return True


def event_complete(
    event_dir: Path,
    fps: int,
    encoder: str,
    timestamp_source: str,
) -> bool:
    qa_path = staging_dir(event_dir, fps) / "fixed_fps_qa.json"
    if not qa_path.is_file():
        return False
    try:
        qa = json.loads(qa_path.read_text(encoding="utf-8"))
        if (
            qa["fixed_frame_rate_fps"] != fps
            or qa.get("timestamp_source", "bag") != timestamp_source
            or len(qa["videos"]) != 7
        ):
            return False
        number = fixed.event_number(event_dir)
        for key in fixed.TOPICS:
            result = qa["videos"][key]
            path = staging_dir(event_dir, fps) / f"video_{key}_event_{number:03d}.mp4"
            if result["encoder"] != encoder or not path.is_file() or path.stat().st_size == 0:
                return False
        return True
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def regenerate_event(
    event_dir: Path,
    bag_path: Path | None,
    timestamp_source: str,
    fps: int,
    encoder: str,
    bitrate_mbps: float,
    crf: int,
    preset: str,
    workers: int,
) -> dict:
    output_dir = staging_dir(event_dir, fps)
    window = fixed.read_event_window(event_dir)
    cache_path = timestamp_cache_path(event_dir, fps)
    if cache_matches(cache_path, window, timestamp_source):
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        timestamps: dict[str, list[int] | None] = cache["timestamps_ns"]
    elif timestamp_source == "event-window":
        timestamps = {key: None for key in fixed.TOPICS}
    else:
        raise RuntimeError("ROS bag timestamp cache is missing")
    sources = fixed.source_video_paths(event_dir)
    number = fixed.event_number(event_dir)
    results = {}

    def process(key: str):
        result = fixed.regenerate_video(
            source_path=sources[key],
            output_path=output_dir / f"video_{key}_event_{number:03d}.mp4",
            timestamps_ns=timestamps[key],
            window=window,
            fps=fps,
            encoder=encoder,
            bitrate_mbps=bitrate_mbps,
            crf=crf,
            preset=preset,
        )
        return key, result

    with ThreadPoolExecutor(max_workers=min(workers, 7)) as executor:
        futures = [executor.submit(process, key) for key in fixed.TOPICS]
        for future in as_completed(futures):
            key, result = future.result()
            results[key] = result

    qa = {
        "event_dir": str(event_dir),
        "bag": str(bag_path) if bag_path else None,
        "fixed_frame_rate_fps": fps,
        "timestamp_source": timestamp_source,
        "sampling_method": "nearest source frame on event-time grid; no interpolation",
        "event_duration_s": window.duration_s,
        "videos": results,
    }
    qa_path = output_dir / "fixed_fps_qa.json"
    partial = qa_path.with_suffix(".partial.json")
    partial.write_text(json.dumps(qa, indent=2) + "\n", encoding="utf-8")
    partial.replace(qa_path)
    return qa


def append_status(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(data, ensure_ascii=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, default=Path(os.environ.get("TLCD_DATASET_ROOT", "Dataset")))
    parser.add_argument("--source-root", type=Path, default=Path(os.environ.get("TLCD_SOURCE_ROOT", "source_data")))
    parser.add_argument("--phase", choices=("timestamps", "videos", "all"), default="all")
    parser.add_argument(
        "--timing-source",
        choices=("event-window", "bag"),
        default="event-window",
    )
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--encoder", choices=("hevc_videotoolbox", "libx265"), default="hevc_videotoolbox")
    parser.add_argument("--bitrate-mbps", type=float, default=6.0)
    parser.add_argument("--crf", type=int, default=23)
    parser.add_argument("--preset", default="fast")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--event-workers", type=int, default=2)
    parser.add_argument(
        "--status-log",
        type=Path,
        default=Path(__file__).resolve().parent / "fixed_fps_batch_status.jsonl",
    )
    parser.add_argument(
        "--event-manifest",
        type=Path,
        default=Path(__file__).resolve().parent / "fixed_fps_dataset_events.json",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.workers <= 0 or args.event_workers <= 0:
        raise ValueError("--workers and --event-workers must be positive")
    event_dirs = discover_events(args.dataset_root, args.event_manifest)
    if not event_dirs:
        raise RuntimeError(f"No event directories found under {args.dataset_root}")
    groups: dict[Path, list[Path]] = {}
    failures = []
    if args.timing_source == "bag":
        for event_dir in event_dirs:
            try:
                groups.setdefault(find_bag(args.source_root, event_dir), []).append(event_dir)
            except Exception as error:
                failures.append((str(event_dir), str(error)))

    print(
        f"[DISCOVERED] events={len(event_dirs)} timing_source={args.timing_source} "
        f"bags={len(groups)}",
        flush=True,
    )
    if args.phase in {"timestamps", "all"}:
        if args.timing_source == "event-window":
            print(
                "[TIMESTAMPS] event-window mappings will be created on demand "
                "during each video's first read",
                flush=True,
            )
        else:
            for index, (bag_path, grouped_events) in enumerate(sorted(groups.items()), start=1):
                started = time.perf_counter()
                try:
                    written = cache_group_timestamps(bag_path, grouped_events, args.fps)
                    print(
                        f"[TIMESTAMPS] bag={index}/{len(groups)} events={len(grouped_events)} "
                        f"written={written} elapsed_s={time.perf_counter() - started:.1f}",
                        flush=True,
                    )
                except Exception as error:
                    failures.append((str(bag_path), str(error)))
                    append_status(
                        args.status_log,
                        {"stage": "timestamps", "bag": str(bag_path), "error": str(error)},
                    )

    if args.phase in {"videos", "all"}:
        bag_by_event = {
            event_dir: bag_path
            for bag_path, grouped_events in groups.items()
            for event_dir in grouped_events
        }
        pending = [
            event_dir
            for event_dir in event_dirs
            if not event_complete(
                event_dir,
                args.fps,
                args.encoder,
                args.timing_source,
            )
        ]
        print(f"[VIDEO-PHASE] pending_events={len(pending)}", flush=True)
        def process_event(event_dir: Path):
            started = time.perf_counter()
            qa = regenerate_event(
                event_dir,
                bag_by_event.get(event_dir),
                args.timing_source,
                args.fps,
                args.encoder,
                args.bitrate_mbps,
                args.crf,
                args.preset,
                args.workers,
            )
            return event_dir, qa, time.perf_counter() - started

        pending_iter = iter(pending)
        completed_count = 0
        with ThreadPoolExecutor(max_workers=args.event_workers) as executor:
            future_to_event = {}
            for _ in range(min(args.event_workers, len(pending))):
                event_dir = next(pending_iter)
                future_to_event[executor.submit(process_event, event_dir)] = event_dir

            while future_to_event:
                future = next(as_completed(future_to_event))
                event_dir = future_to_event.pop(future)
                completed_count += 1
                try:
                    _, qa, elapsed_s = future.result()
                    append_status(
                        args.status_log,
                        {
                            "stage": "videos",
                            "event": str(event_dir),
                            "status": "complete",
                            "maximum_duration_error_s": max(
                                value["output_duration_error_s"] for value in qa["videos"].values()
                            ),
                        },
                    )
                    print(
                        f"[EVENT-DONE] {completed_count}/{len(pending)} "
                        f"elapsed_s={elapsed_s:.1f} "
                        f"event={event_dir}",
                        flush=True,
                    )
                except Exception as error:
                    failures.append((str(event_dir), str(error)))
                    append_status(
                        args.status_log,
                        {"stage": "videos", "event": str(event_dir), "error": str(error)},
                    )
                    print(f"[EVENT-ERROR] event={event_dir} error={error}", flush=True)

                try:
                    next_event = next(pending_iter)
                except StopIteration:
                    continue
                future_to_event[executor.submit(process_event, next_event)] = next_event

    print(f"[DONE] failures={len(failures)}", flush=True)
    for item, error in failures:
        print(f"[FAILED] {item}\n  {error}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
