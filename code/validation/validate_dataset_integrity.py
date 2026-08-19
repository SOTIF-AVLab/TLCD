#!/usr/bin/env python3
"""Validate TLCD event-file completeness, CSV timing and video integrity."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import shutil
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "statistics" / "technical_validation"
CITY_DIRS = ("Changchun_valid", "Nanjing_valid")
CSV_SUFFIXES = ("EgoInfo.csv", "ObjInfo.csv", "MapInfo.csv", "EvidenceChain.csv")
VIDEO_TOKENS = ("30", "120", "rear", "Side_B", "Side_F", "Side_L", "Side_R")


def discover_event_dirs(root: Path) -> list[Path]:
    output = []
    for city_name in CITY_DIRS:
        city_dir = root / city_name
        if not city_dir.is_dir():
            continue
        for category_dir in sorted(path for path in city_dir.iterdir() if path.is_dir()):
            for segment_dir in sorted(path for path in category_dir.iterdir() if path.is_dir()):
                output.extend(
                    path
                    for path in sorted(segment_dir.iterdir())
                    if path.is_dir() and path.name.startswith("event_")
                )
    return output


def find_suffix(event_dir: Path, suffix: str) -> list[Path]:
    return sorted(event_dir.glob(f"*_{suffix}"))


def read_event_time(path: Path) -> tuple[list[Decimal], str]:
    try:
        with path.open("r", encoding="utf-8-sig", errors="strict") as handle:
            header = handle.readline().rstrip("\r\n").split(",", 1)[0].lstrip("\ufeff")
            if header != "event_time":
                return [], f"first column is {header!r}, expected 'event_time'"
            values = []
            for line_number, line in enumerate(handle, start=2):
                if not line.strip():
                    continue
                raw = line.split(",", 1)[0].strip()
                try:
                    values.append(Decimal(raw))
                except InvalidOperation:
                    return [], f"invalid event_time {raw!r} at line {line_number}"
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"
    if not values:
        return [], "no data rows"
    return values, ""


def parse_rate(value: str) -> float:
    try:
        return float(Fraction(value))
    except Exception:
        return 0.0


def probe_video(path: Path, full_decode: bool) -> tuple[dict, str]:
    if importlib.util.find_spec("av") is not None:
        import av

        try:
            with av.open(str(path)) as container:
                if not container.streams.video:
                    return {}, "no video stream"
                stream = container.streams.video[0]
                frame_rate = float(stream.average_rate or stream.base_rate or 0)
                if stream.duration is not None and stream.time_base is not None:
                    duration = float(stream.duration * stream.time_base)
                elif container.duration is not None:
                    duration = float(container.duration / av.time_base)
                else:
                    duration = 0.0
                decoded_frames = 0
                decode_error_packets = 0
                if full_decode:
                    for packet in container.demux(stream):
                        try:
                            decoded_frames += len(packet.decode())
                        except Exception:
                            decode_error_packets += 1
                    if decoded_frames == 0:
                        return {}, "video stream yielded no decoded frames"
        except Exception as exc:
            return {}, f"PyAV {type(exc).__name__}: {exc}"
        if duration <= 0 or frame_rate <= 0:
            return {}, f"invalid duration/frame rate: duration={duration}, fps={frame_rate}"
        return {
            "duration_s": duration,
            "frame_rate_fps": frame_rate,
            "decoded_frames": decoded_frames,
            "decode_error_packets": decode_error_packets,
            "backend": "PyAV",
        }, (
            f"decode_error_packets={decode_error_packets}"
            if decode_error_packets
            else ""
        )

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=avg_frame_rate,duration,nb_frames:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if probe.returncode != 0:
        return {}, probe.stderr.strip() or f"ffprobe exit code {probe.returncode}"
    try:
        metadata = json.loads(probe.stdout)
        stream = metadata["streams"][0]
        duration = float(stream.get("duration") or metadata["format"]["duration"])
        frame_rate = parse_rate(stream.get("avg_frame_rate", "0/0"))
    except Exception as exc:
        return {}, f"invalid ffprobe output: {type(exc).__name__}: {exc}"
    if duration <= 0 or frame_rate <= 0:
        return {}, f"invalid duration/frame rate: duration={duration}, fps={frame_rate}"

    if full_decode:
        decode = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if decode.returncode != 0 or decode.stderr.strip():
            return {}, decode.stderr.strip() or f"ffmpeg exit code {decode.returncode}"
    return {
        "duration_s": duration,
        "frame_rate_fps": frame_rate,
        "decoded_frames": None,
        "decode_error_packets": 0,
        "backend": "FFmpeg",
    }, ""


def validate_event(event_dir: Path, dataset_root: Path, video_mode: str) -> dict:
    relative = str(event_dir.relative_to(dataset_root))
    parts = event_dir.relative_to(dataset_root).parts
    row = {
        "event_id": relative,
        "city": parts[0] if len(parts) > 0 else "",
        "category": parts[1] if len(parts) > 1 else "",
        "has_record_json": False,
        "four_csv_present": False,
        "identical_event_time": False,
        "event_time_100hz": False,
        "csv_rows": "",
        "structured_duration_s": "",
        "seven_videos_present": False,
        "videos_probed": 0,
        "videos_fully_decoded": 0,
        "video_decode_error_packets": 0,
        "max_video_duration_difference_s": "",
        "max_video_duration_difference_frames": "",
        "video_duration_within_one_frame": False,
        "issues": [],
    }

    record_files = find_suffix(event_dir, "record.json")
    row["has_record_json"] = len(record_files) == 1
    if not row["has_record_json"]:
        row["issues"].append(f"record_json_count={len(record_files)}")
        return row

    csv_files = {}
    for suffix in CSV_SUFFIXES:
        matches = find_suffix(event_dir, suffix)
        if len(matches) != 1:
            row["issues"].append(f"{suffix}_count={len(matches)}")
        else:
            csv_files[suffix] = matches[0]
    row["four_csv_present"] = len(csv_files) == 4
    if not row["four_csv_present"]:
        return row

    time_axes = {}
    for suffix, path in csv_files.items():
        values, error = read_event_time(path)
        if error:
            row["issues"].append(f"{suffix}: {error}")
        else:
            time_axes[suffix] = values
    if len(time_axes) != 4:
        return row

    reference = time_axes[CSV_SUFFIXES[0]]
    row["csv_rows"] = len(reference)
    row["identical_event_time"] = all(values == reference for values in time_axes.values())
    if not row["identical_event_time"]:
        row["issues"].append("event_time_axes_differ")
    increments = [reference[index + 1] - reference[index] for index in range(len(reference) - 1)]
    row["event_time_100hz"] = all(value == Decimal("0.01") for value in increments)
    if not row["event_time_100hz"]:
        row["issues"].append("event_time_increment_not_0.01_s")
    structured_duration = float(reference[-1] - reference[0])
    row["structured_duration_s"] = structured_duration

    video_dir = event_dir / "video"
    videos = sorted(video_dir.glob("*.mp4")) if video_dir.is_dir() else []
    names = [path.name for path in videos]
    expected_tokens_present = all(sum(token in name for name in names) == 1 for token in VIDEO_TOKENS)
    row["seven_videos_present"] = len(videos) == 7 and expected_tokens_present
    if not row["seven_videos_present"]:
        row["issues"].append(f"video_count_or_names_invalid={len(videos)}")
        return row
    zero_byte = [path.name for path in videos if path.stat().st_size == 0]
    if zero_byte:
        row["issues"].append("zero_byte_videos=" + ";".join(zero_byte))
        return row

    if video_mode == "skip":
        return row

    duration_differences = []
    duration_differences_frames = []
    full_decode = video_mode == "decode"
    for path in videos:
        metadata, error = probe_video(path, full_decode=full_decode)
        if error:
            row["issues"].append(f"{path.name}: {error}")
        if not metadata:
            continue
        row["videos_probed"] += 1
        decode_error_packets = int(metadata.get("decode_error_packets", 0))
        row["video_decode_error_packets"] += decode_error_packets
        row["videos_fully_decoded"] += int(full_decode and decode_error_packets == 0)
        difference = abs(metadata["duration_s"] - structured_duration)
        duration_differences.append(difference)
        duration_differences_frames.append(difference * metadata["frame_rate_fps"])
    if duration_differences:
        row["max_video_duration_difference_s"] = max(duration_differences)
        row["max_video_duration_difference_frames"] = max(duration_differences_frames)
    row["video_duration_within_one_frame"] = (
        row["videos_probed"] == 7
        and max(duration_differences_frames, default=float("inf")) <= 1.0 + 1e-6
    )
    if row["videos_probed"] != 7:
        row["issues"].append(f"successfully_probed_videos={row['videos_probed']}")
    if row["videos_probed"] == 7 and not row["video_duration_within_one_frame"]:
        row["issues"].append("video_duration_difference_exceeds_one_frame")
    return row


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path(os.environ.get("TLCD_DATASET_ROOT", "")),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument(
        "--video-mode",
        choices=("skip", "probe", "decode"),
        default="probe",
        help="skip video metadata, probe stream metadata, or fully decode every video",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not str(args.dataset_root) or not args.dataset_root.is_dir():
        raise SystemExit("Provide --dataset-root or set TLCD_DATASET_ROOT.")
    pyav_available = importlib.util.find_spec("av") is not None
    if (
        args.video_mode in {"probe", "decode"}
        and not pyav_available
        and shutil.which("ffprobe") is None
    ):
        raise SystemExit("PyAV or ffprobe is required for --video-mode probe/decode.")
    if args.video_mode == "decode" and not pyav_available and shutil.which("ffmpeg") is None:
        raise SystemExit("PyAV or ffmpeg is required for --video-mode decode.")

    event_dirs = discover_event_dirs(args.dataset_root)
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(validate_event, event_dir, args.dataset_root, args.video_mode): event_dir
            for event_dir in event_dirs
        }
        for index, future in enumerate(as_completed(futures), start=1):
            rows.append(future.result())
            if index % 250 == 0:
                print(f"Validated {index}/{len(event_dirs)} event directories", flush=True)
    rows.sort(key=lambda row: row["event_id"])

    summary = Counter()
    summary["event_directories"] = len(rows)
    for row in rows:
        for key in (
            "has_record_json",
            "four_csv_present",
            "identical_event_time",
            "event_time_100hz",
            "seven_videos_present",
            "video_duration_within_one_frame",
        ):
            summary[key] += int(bool(row[key]))
        summary["videos_probed"] += int(row["videos_probed"])
        summary["videos_fully_decoded"] += int(row["videos_fully_decoded"])
        summary["video_decode_error_packets"] += int(row["video_decode_error_packets"])
    valid_rows = [row for row in rows if row["has_record_json"]]
    max_seconds = max(
        (float(row["max_video_duration_difference_s"]) for row in valid_rows if row["max_video_duration_difference_s"] != ""),
        default=None,
    )
    max_frames = max(
        (float(row["max_video_duration_difference_frames"]) for row in valid_rows if row["max_video_duration_difference_frames"] != ""),
        default=None,
    )
    output = {
        **dict(summary),
        "released_records": len(valid_rows),
        "records_with_issues": sum(bool(row["issues"]) for row in valid_rows),
        "max_video_duration_difference_s": max_seconds,
        "max_video_duration_difference_frames": max_frames,
        "video_mode": args.video_mode,
        "video_backend": "PyAV" if pyav_available else "FFmpeg",
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    event_rows = []
    for row in rows:
        flat = dict(row)
        flat["issues"] = " | ".join(flat["issues"])
        event_rows.append(flat)
    write_csv(
        args.output_dir / "dataset_integrity_event_audit.csv",
        event_rows,
        list(event_rows[0]) if event_rows else [],
    )
    (args.output_dir / "dataset_integrity_summary.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
