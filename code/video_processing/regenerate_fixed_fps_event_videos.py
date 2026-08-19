"""Regenerate one released event's seven camera videos at a fixed frame rate.

Source frames are selected on a uniform event-time grid using nearest-neighbor
matching. By default, the encoded source-frame sequence is normalized across
the event window recorded in ``*_record.json``; original ROS bag timestamps
remain available as an optional diagnostic mode. Missing grid samples repeat
the nearest real frame, and no interpolated frames are synthesized. Outputs are
written to a staging directory and never overwrite the released H.265 videos.
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import shutil
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
from rosbags.rosbag1 import Reader


TOPICS = {
    "120": "J5_1_H265_120",
    "30": "J5_1_H265_30",
    "rear": "VH_2_H265_Rear",
    "Side_B": "VH_2_H265_Side_B",
    "Side_F": "VH_2_H265_Side_F",
    "Side_L": "VH_2_H265_Side_L",
    "Side_R": "VH_2_H265_Side_R",
}


@dataclass(frozen=True)
class EventWindow:
    start_ns: int
    end_ns: int

    @property
    def duration_s(self) -> float:
        return (self.end_ns - self.start_ns) / 1_000_000_000


def read_event_window(event_dir: Path) -> EventWindow:
    records = list(event_dir.glob("*_record.json"))
    if len(records) != 1:
        raise RuntimeError(f"Expected one *_record.json in {event_dir}, found {len(records)}")
    record = json.loads(records[0].read_text(encoding="utf-8-sig"))
    timestamp = record.get("Timestamp")
    if isinstance(timestamp, str):
        parts = [part.strip() for part in timestamp.split("--")]
        if len(parts) != 2:
            raise ValueError(f"Invalid Timestamp string in {records[0]}")
        start_ms, end_ms = map(int, parts)
    elif isinstance(timestamp, dict):
        start_ms = int(timestamp["t_start"])
        end_ms = int(timestamp["t_end"])
    else:
        raise ValueError(f"Unsupported Timestamp value in {records[0]}")
    if end_ms <= start_ms:
        raise ValueError(f"Event end must be later than start in {records[0]}")
    return EventWindow(start_ms * 1_000_000, end_ms * 1_000_000)


def event_number(event_dir: Path) -> int:
    try:
        return int(event_dir.name.split("_")[-1])
    except ValueError as error:
        raise ValueError(f"Unexpected event directory name: {event_dir.name}") from error


def source_video_paths(event_dir: Path) -> dict[str, Path]:
    number = event_number(event_dir)
    video_dir = event_dir / "video"
    paths = {
        key: video_dir / f"video_{key}_event_{number:03d}.mp4"
        for key in TOPICS
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing released video(s):\n" + "\n".join(missing))
    return paths


@contextmanager
def local_bag_copy(bag_path: Path, cache_dir: Path | None):
    if cache_dir is None:
        yield bag_path
        return
    cache_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tlcd-bag-", dir=cache_dir) as temp_dir:
        local_path = Path(temp_dir) / bag_path.name
        started = time.perf_counter()
        shutil.copy2(bag_path, local_path)
        print(
            f"[BAG-CACHED] size_gib={local_path.stat().st_size / 1024**3:.2f} "
            f"elapsed_s={time.perf_counter() - started:.1f}",
            flush=True,
        )
        yield local_path


def camera_timestamps(
    bag_path: Path,
    window: EventWindow,
) -> dict[str, list[int]]:
    timestamps: dict[str, list[int]] = {}
    with Reader(bag_path) as reader:
        connections_by_topic = {
            connection.topic: connection for connection in reader.connections
        }
        for key, topic in TOPICS.items():
            if topic not in connections_by_topic:
                raise RuntimeError(f"Topic {topic!r} is absent from {bag_path}")
            connection = connections_by_topic[topic]
            all_times = [entry.time for entry in reader.indexes[connection.id]]
            first = bisect.bisect_left(all_times, window.start_ns)
            last = bisect.bisect_right(all_times, window.end_ns)
            timestamps[key] = all_times[first:last]
            if not timestamps[key]:
                raise RuntimeError(f"No {topic!r} messages in the event interval")
    return timestamps


def normalized_camera_timestamps(
    sources: dict[str, Path],
    window: EventWindow,
) -> dict[str, list[int]]:
    """Place each decodable source frame uniformly over the event window."""
    timestamps = {}
    for key, path in sources.items():
        count = count_decodable_frames(path)
        if count <= 0:
            raise RuntimeError(f"No decodable video frame in {path}")
        timestamps[key] = normalized_timestamps_for_count(count, window)
    return timestamps


def count_decodable_frames(path: Path) -> int:
    """Count frames using the same packet-level error handling as regeneration."""
    count = 0
    with av.open(str(path), mode="r") as container:
        stream = container.streams.video[0]
        for packet in container.demux(stream):
            if not packet.size:
                continue
            try:
                count += len(packet.decode())
            except av.error.FFmpegError:
                continue
    return count


def normalized_timestamps_for_count(
    count: int,
    window: EventWindow,
) -> list[int]:
    if count <= 0:
        raise ValueError("Source-frame count must be positive")
    if count == 1:
        return [window.start_ns]
    duration_ns = window.end_ns - window.start_ns
    return [
        window.start_ns + round(index * duration_ns / (count - 1))
        for index in range(count)
    ]


def prepare_frame(frame: av.VideoFrame) -> av.VideoFrame:
    if frame.format.name != "yuv420p":
        frame = frame.reformat(format="yuv420p")
    frame.pict_type = 0
    return frame


def decodable_frame_records(
    container: av.InputContainer,
    stream: av.VideoStream,
    timestamps_ns: list[int] | None,
    stats: dict[str, int],
    sequential_mapping: bool = False,
):
    """Yield decodable frames mapped back to their original message index."""
    nominal_rate = float(stream.base_rate or stream.average_rate)
    for packet in container.demux(stream):
        if not packet.size:
            continue
        packet_index = stats["source_packets"]
        stats["source_packets"] += 1
        if not sequential_mapping and packet_index >= len(timestamps_ns):
            raise RuntimeError("Source video contains more packets than bag timestamps")
        try:
            decoded = packet.decode()
        except av.error.FFmpegError:
            stats["source_decode_error_packets"] += 1
            continue
        for frame in decoded:
            if sequential_mapping:
                source_index = stats["decodable_source_frames"]
            elif frame.pts is None or frame.time_base is None:
                source_index = packet_index
            else:
                source_index = round(float(frame.pts * frame.time_base) * nominal_rate)
            if not 0 <= source_index < len(timestamps_ns):
                raise RuntimeError(
                    f"Decoded frame maps outside the bag timestamp sequence: {source_index}"
                )
            if source_index <= stats["last_source_index"]:
                raise RuntimeError(
                    f"Decoded source-frame indices are not strictly increasing: "
                    f"{stats['last_source_index']} then {source_index}"
                )
            stats["last_source_index"] = source_index
            stats["decodable_source_frames"] += 1
            yield prepare_frame(frame), timestamps_ns[source_index], source_index


def validate_output(path: Path, expected_frames: int, fps: int) -> dict:
    with av.open(str(path), mode="r") as container:
        stream = container.streams.video[0]
        decoded_frames = 0
        previous_time = None
        for frame in container.decode(stream):
            frame_time = float(frame.pts * frame.time_base)
            if previous_time is not None and frame_time <= previous_time:
                raise RuntimeError(f"Non-monotonic frame timestamps in {path}")
            previous_time = frame_time
            decoded_frames += 1
        duration_s = float(stream.duration * stream.time_base)
        average_rate = float(stream.average_rate)
        codec = stream.codec_context.name
        width = stream.codec_context.width
        height = stream.codec_context.height
    if decoded_frames != expected_frames:
        raise RuntimeError(
            f"Decoded {decoded_frames} frames from {path}, expected {expected_frames}"
        )
    if not math.isclose(average_rate, fps, rel_tol=0, abs_tol=1e-6):
        raise RuntimeError(f"Unexpected average frame rate in {path}: {average_rate}")
    expected_duration = expected_frames / fps
    if not math.isclose(duration_s, expected_duration, rel_tol=0, abs_tol=1e-6):
        raise RuntimeError(
            f"Unexpected duration in {path}: {duration_s} versus {expected_duration}"
        )
    return {
        "decoded_frames": decoded_frames,
        "average_frame_rate_fps": average_rate,
        "duration_s": duration_s,
        "codec": codec,
        "width": width,
        "height": height,
    }


def regenerate_video(
    source_path: Path,
    output_path: Path,
    timestamps_ns: list[int],
    window: EventWindow,
    fps: int,
    encoder: str,
    bitrate_mbps: float,
    crf: int,
    preset: str,
) -> dict:
    normalized_timing = timestamps_ns is None
    if normalized_timing:
        source_frame_count = count_decodable_frames(source_path)
        if source_frame_count <= 0:
            raise RuntimeError(f"No decodable frame in {source_path}")
        timestamps_ns = normalized_timestamps_for_count(
            source_frame_count,
            window,
        )

    output_frames = max(1, int(math.floor(window.duration_s * fps + 0.5)))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    partial_path = output_path.with_name(output_path.stem + ".partial.mp4")
    partial_path.unlink(missing_ok=True)

    selected_indices: set[int] = set()
    maximum_match_error_ns = 0
    source_stats = {
        "source_packets": 0,
        "source_decode_error_packets": 0,
        "decodable_source_frames": 0,
        "last_source_index": -1,
    }
    started = time.perf_counter()

    try:
        with av.open(str(source_path), mode="r") as source:
            source_stream = source.streams.video[0]
            width = source_stream.codec_context.width
            height = source_stream.codec_context.height
            source_codec = source_stream.codec_context.name
            frames = iter(
                decodable_frame_records(
                    source,
                    source_stream,
                    timestamps_ns,
                    source_stats,
                    sequential_mapping=normalized_timing,
                )
            )

            try:
                current, current_timestamp_ns, current_index = next(frames)
            except StopIteration as error:
                raise RuntimeError(f"No decodable frame in {source_path}") from error
            try:
                following, following_timestamp_ns, following_index = next(frames)
            except StopIteration:
                following = None
                following_timestamp_ns = None
                following_index = None

            with av.open(
                str(partial_path),
                mode="w",
                options={"movflags": "+faststart"},
            ) as target:
                target_stream = target.add_stream(encoder, rate=fps)
                target_stream.width = width
                target_stream.height = height
                target_stream.pix_fmt = "yuv420p"
                target_stream.time_base = Fraction(1, fps)
                target_stream.codec_context.time_base = Fraction(1, fps)
                target_stream.codec_context.gop_size = fps
                target_stream.codec_context.max_b_frames = 0
                if encoder == "libx265":
                    target_stream.options = {
                        "preset": preset,
                        "crf": str(crf),
                        "x265-params": (
                            f"bframes=0:keyint={fps}:min-keyint={fps}:"
                            "scenecut=0:log-level=error"
                        ),
                    }
                else:
                    target_stream.bit_rate = round(bitrate_mbps * 1_000_000)
                target.metadata["tlcd_timing"] = "nearest_source_frame_on_fixed_grid"
                target.metadata["tlcd_source_codec"] = source_codec

                for output_index in range(output_frames):
                    grid_ns = window.start_ns + round(output_index * 1_000_000_000 / fps)
                    while (
                        following is not None
                        and following_timestamp_ns is not None
                        and following_index is not None
                        and following_timestamp_ns <= grid_ns
                    ):
                        current = following
                        current_timestamp_ns = following_timestamp_ns
                        current_index = following_index
                        try:
                            following, following_timestamp_ns, following_index = next(frames)
                        except StopIteration:
                            following = None
                            following_timestamp_ns = None
                            following_index = None

                    selected = current
                    selected_index = current_index
                    selected_timestamp_ns = current_timestamp_ns
                    if (
                        following is not None
                        and following_timestamp_ns is not None
                        and following_index is not None
                    ):
                        current_error = abs(current_timestamp_ns - grid_ns)
                        following_error = abs(following_timestamp_ns - grid_ns)
                        if following_error < current_error:
                            selected = following
                            selected_index = following_index
                            selected_timestamp_ns = following_timestamp_ns

                    match_error_ns = abs(selected_timestamp_ns - grid_ns)
                    maximum_match_error_ns = max(maximum_match_error_ns, match_error_ns)
                    selected_indices.add(selected_index)
                    selected.pts = output_index
                    selected.time_base = Fraction(1, fps)
                    selected.pict_type = 0
                    for packet in target_stream.encode(selected):
                        target.mux(packet)

                for packet in target_stream.encode():
                    target.mux(packet)

            for _ in frames:
                pass

        if normalized_timing and source_stats["decodable_source_frames"] != len(timestamps_ns):
            raise RuntimeError(
                f"Source decoded-frame count changed while reading {source_path}: "
                f"first_pass={len(timestamps_ns)}, "
                f"second_pass={source_stats['decodable_source_frames']}"
            )
        if not normalized_timing and source_stats["source_packets"] != len(timestamps_ns):
            raise RuntimeError(
                f"Source packet/message mismatch for {source_path}: "
                f"packets={source_stats['source_packets']}, timestamps={len(timestamps_ns)}"
            )

        validation = validate_output(partial_path, output_frames, fps)
        partial_path.replace(output_path)
    except Exception:
        partial_path.unlink(missing_ok=True)
        raise

    return {
        "source_video": str(source_path),
        "output_video": str(output_path),
        "source_packets": source_stats["source_packets"],
        "decodable_source_frames": source_stats["decodable_source_frames"],
        "source_decode_error_packets": source_stats["source_decode_error_packets"],
        "source_message_timestamps": len(timestamps_ns),
        "source_first_offset_s": (timestamps_ns[0] - window.start_ns) / 1_000_000_000,
        "source_last_offset_s": (timestamps_ns[-1] - window.start_ns) / 1_000_000_000,
        "output_frames": output_frames,
        "duplicated_grid_frames": output_frames - len(selected_indices),
        "unused_source_frames": source_stats["decodable_source_frames"] - len(selected_indices),
        "maximum_nearest_timestamp_error_s": maximum_match_error_ns / 1_000_000_000,
        "event_duration_s": window.duration_s,
        "output_duration_error_s": abs(validation["duration_s"] - window.duration_s),
        "encoder": encoder,
        "target_bitrate_mbps": bitrate_mbps if encoder != "libx265" else None,
        "encoding_crf": crf,
        "encoding_preset": preset,
        "elapsed_s": time.perf_counter() - started,
        **validation,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-dir", type=Path, required=True)
    parser.add_argument("--bag", type=Path)
    parser.add_argument(
        "--timing-source",
        choices=("event-window", "bag"),
        default="event-window",
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument(
        "--encoder",
        choices=("hevc_videotoolbox", "libx265"),
        default="hevc_videotoolbox",
    )
    parser.add_argument("--bitrate-mbps", type=float, default=6.0)
    parser.add_argument("--crf", type=int, default=23)
    parser.add_argument("--preset", default="fast")
    parser.add_argument("--bag-cache-dir", type=Path)
    parser.add_argument("--camera", action="append", choices=tuple(TOPICS))
    parser.add_argument("--workers", type=int, default=4)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.fps <= 0:
        raise ValueError("--fps must be positive")
    if args.workers <= 0:
        raise ValueError("--workers must be positive")
    event_dir = args.event_dir.resolve()
    output_dir = args.output_dir or event_dir / f"video_cfr{args.fps}_staging"
    output_dir.mkdir(parents=True, exist_ok=True)
    window = read_event_window(event_dir)
    sources = source_video_paths(event_dir)
    timestamp_cache = output_dir / "source_camera_timestamps_ns.json"
    if timestamp_cache.is_file():
        cached = json.loads(timestamp_cache.read_text(encoding="utf-8"))
        cache_valid = (
            cached["event_start_ns"] == window.start_ns
            and cached["event_end_ns"] == window.end_ns
            and cached.get("timestamp_source", "bag") == args.timing_source
        )
    else:
        cache_valid = False
    if cache_valid:
        timestamps = {key: cached["timestamps_ns"][key] for key in TOPICS}
        print(f"[TIMESTAMPS-CACHED] {timestamp_cache}", flush=True)
    else:
        if args.timing_source == "bag":
            if args.bag is None:
                raise ValueError("--bag is required when --timing-source=bag")
            with local_bag_copy(args.bag.resolve(), args.bag_cache_dir) as bag_path:
                timestamps = camera_timestamps(bag_path, window)
        else:
            timestamps = normalized_camera_timestamps(sources, window)
        timestamp_cache.write_text(
            json.dumps(
                {
                    "event_start_ns": window.start_ns,
                    "event_end_ns": window.end_ns,
                    "timestamp_source": args.timing_source,
                    "timestamps_ns": timestamps,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

    results = {}
    number = event_number(event_dir)
    selected_cameras = args.camera or list(TOPICS)

    def process_camera(key: str):
        output_path = output_dir / f"video_{key}_event_{number:03d}.mp4"
        print(
            f"[VIDEO-START] camera={key} source_frames={len(timestamps[key])}",
            flush=True,
        )
        result = regenerate_video(
            source_path=sources[key],
            output_path=output_path,
            timestamps_ns=timestamps[key],
            window=window,
            fps=args.fps,
            encoder=args.encoder,
            bitrate_mbps=args.bitrate_mbps,
            crf=args.crf,
            preset=args.preset,
        )
        print(
            f"[VIDEO-DONE] camera={key} output_frames={result['output_frames']} "
            f"duration_error_s={result['output_duration_error_s']:.6f} "
            f"elapsed_s={result['elapsed_s']:.1f}",
            flush=True,
        )
        return key, result

    with ThreadPoolExecutor(max_workers=min(args.workers, len(selected_cameras))) as executor:
        futures = [executor.submit(process_camera, key) for key in selected_cameras]
        for future in as_completed(futures):
            key, result = future.result()
            results[key] = result

    qa = {
        "event_dir": str(event_dir),
        "bag": str(args.bag.resolve()) if args.bag else None,
        "fixed_frame_rate_fps": args.fps,
        "timestamp_source": args.timing_source,
        "sampling_method": "nearest source frame on event-time grid; no interpolation",
        "event_duration_s": window.duration_s,
        "videos": results,
    }
    qa_path = output_dir / "fixed_fps_qa.json"
    qa_path.write_text(json.dumps(qa, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(qa, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
