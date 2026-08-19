"""Audit every staged fixed-frame-rate TLCD event video.

The generation pipeline fully decodes each output before writing its per-event
QA record. This audit independently checks those QA records against the event
JSON and current MP4 stream metadata, and confirms that all original H.265
videos remain present as separate files.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import av

import regenerate_all_fixed_fps_videos as batch
import regenerate_fixed_fps_event_videos as fixed


def video_stream_metadata(path: Path) -> dict:
    with av.open(str(path), mode="r") as container:
        stream = container.streams.video[0]
        return {
            "codec": stream.codec_context.name,
            "frames": stream.frames,
            "average_rate_fps": float(stream.average_rate),
            "duration_s": float(stream.duration * stream.time_base),
            "width": stream.codec_context.width,
            "height": stream.codec_context.height,
        }


def camera_paths(video_dir: Path, event_dir: Path) -> dict[str, Path]:
    number = fixed.event_number(event_dir)
    paths = {
        key: video_dir / f"video_{key}_event_{number:03d}.mp4"
        for key in fixed.TOPICS
    }
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing camera video(s):\n" + "\n".join(missing))
    return paths


def event_video_state(event_dir: Path, fps: int) -> dict:
    active_dir = event_dir / "video"
    original_dir = event_dir / "video_original_h265"
    staging_dir = batch.staging_dir(event_dir, fps)
    metadata_dir = event_dir / f"video_cfr{fps}_metadata"
    if active_dir.is_dir() and staging_dir.is_dir() and not original_dir.exists():
        return {
            "state": "staged",
            "original_dir": active_dir,
            "output_dir": staging_dir,
            "qa_path": staging_dir / "fixed_fps_qa.json",
        }
    if active_dir.is_dir() and original_dir.is_dir() and not staging_dir.exists():
        return {
            "state": "promoted",
            "original_dir": original_dir,
            "output_dir": active_dir,
            "qa_path": metadata_dir / "fixed_fps_qa.json",
        }
    raise RuntimeError(
        "Event is neither in the staged state nor the promoted state: "
        f"video={active_dir.exists()}, original={original_dir.exists()}, "
        f"staging={staging_dir.exists()}"
    )


def audit_event(event_dir: Path, fps: int, encoder: str) -> dict:
    window = fixed.read_event_window(event_dir)
    expected_frames = max(1, int(math.floor(window.duration_s * fps + 0.5)))
    expected_duration_s = expected_frames / fps
    duration_tolerance_s = 1 / (2 * fps) + 1e-6
    state = event_video_state(event_dir, fps)
    output_dir = state["output_dir"]
    qa_path = state["qa_path"]
    if not qa_path.is_file():
        raise RuntimeError("fixed_fps_qa.json is missing")
    qa = json.loads(qa_path.read_text(encoding="utf-8"))

    if qa.get("event_dir") != str(event_dir):
        raise RuntimeError("QA event path does not match")
    if qa.get("fixed_frame_rate_fps") != fps:
        raise RuntimeError("QA frame rate does not match")
    if qa.get("timestamp_source") != "event-window":
        raise RuntimeError("QA timestamp source is not event-window")
    if not math.isclose(
        qa.get("event_duration_s", -1),
        window.duration_s,
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise RuntimeError("QA event duration does not match the record JSON")
    if set(qa.get("videos", {})) != set(fixed.TOPICS):
        raise RuntimeError("QA does not contain exactly seven camera videos")

    originals = camera_paths(state["original_dir"], event_dir)
    number = fixed.event_number(event_dir)
    checked = {}
    for key in fixed.TOPICS:
        original_path = originals[key]
        output_path = output_dir / f"video_{key}_event_{number:03d}.mp4"
        if not output_path.is_file() or output_path.stat().st_size <= 0:
            raise RuntimeError(f"Staged output is absent or empty for camera {key}")
        if original_path.samefile(output_path):
            raise RuntimeError(f"Original and staged output are the same file for camera {key}")

        result = qa["videos"][key]
        if Path(result.get("source_video", "")) != original_path:
            raise RuntimeError(f"QA source path mismatch for camera {key}")
        if Path(result.get("output_video", "")) != output_path:
            raise RuntimeError(f"QA output path mismatch for camera {key}")
        if result.get("encoder") != encoder:
            raise RuntimeError(f"Unexpected encoder for camera {key}")
        if result.get("codec") != "hevc":
            raise RuntimeError(f"QA output codec is not HEVC for camera {key}")
        if result.get("output_frames") != expected_frames:
            raise RuntimeError(f"QA output-frame count mismatch for camera {key}")
        if result.get("decoded_frames") != expected_frames:
            raise RuntimeError(f"QA full-decode count mismatch for camera {key}")
        if not math.isclose(
            result.get("average_frame_rate_fps", -1),
            fps,
            rel_tol=0,
            abs_tol=1e-6,
        ):
            raise RuntimeError(f"QA frame rate mismatch for camera {key}")
        if result.get("output_duration_error_s", math.inf) > duration_tolerance_s:
            raise RuntimeError(f"QA duration error exceeds half a frame for camera {key}")

        original_meta = video_stream_metadata(original_path)
        output_meta = video_stream_metadata(output_path)
        if original_meta["codec"] != "hevc":
            raise RuntimeError(f"Original video is not HEVC for camera {key}")
        if output_meta["codec"] != "hevc":
            raise RuntimeError(f"Current staged video is not HEVC for camera {key}")
        if not math.isclose(
            output_meta["average_rate_fps"],
            fps,
            rel_tol=0,
            abs_tol=1e-6,
        ):
            raise RuntimeError(f"Current staged frame rate mismatch for camera {key}")
        if output_meta["frames"] not in (0, expected_frames):
            raise RuntimeError(f"Current staged frame count mismatch for camera {key}")
        if not math.isclose(
            output_meta["duration_s"],
            expected_duration_s,
            rel_tol=0,
            abs_tol=1e-6,
        ):
            raise RuntimeError(f"Current staged duration mismatch for camera {key}")
        if output_meta["width"] != result.get("width") or output_meta["height"] != result.get("height"):
            raise RuntimeError(f"Current staged resolution mismatch for camera {key}")

        checked[key] = {
            "original_path": str(original_path),
            "original_size_bytes": original_path.stat().st_size,
            "output_path": str(output_path),
            "output_size_bytes": output_path.stat().st_size,
            "frames": expected_frames,
            "duration_s": expected_duration_s,
        }

    partial_files = list(output_dir.glob("*.partial.mp4"))
    if partial_files:
        raise RuntimeError(f"Partial MP4 files remain: {len(partial_files)}")
    return {
        "state": state["state"],
        "event_duration_s": window.duration_s,
        "expected_output_frames_per_camera": expected_frames,
        "videos": checked,
    }


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
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--encoder", default="hevc_videotoolbox")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(__file__).resolve().parent / "fixed_fps_dataset_audit.json",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    events = batch.discover_events(args.dataset_root, args.event_manifest)
    started = time.perf_counter()
    failures = []
    audited = {}
    for index, event_dir in enumerate(events, start=1):
        try:
            audited[str(event_dir)] = audit_event(event_dir, args.fps, args.encoder)
        except Exception as error:
            failures.append({"event": str(event_dir), "error": str(error)})
        if index % 100 == 0 or index == len(events):
            print(
                f"[AUDIT] events={index}/{len(events)} failures={len(failures)}",
                flush=True,
            )

    report = {
        "dataset_root": str(args.dataset_root.resolve()),
        "expected_events": len(events),
        "audited_events": len(audited),
        "expected_camera_videos": len(events) * len(fixed.TOPICS),
        "audited_camera_videos": len(audited) * len(fixed.TOPICS),
        "fixed_frame_rate_fps": args.fps,
        "output_codec": "hevc",
        "original_h265_videos_preserved": not failures and len(audited) == len(events),
        "failures": failures,
        "event_states": {
            state: sum(1 for value in audited.values() if value["state"] == state)
            for state in ("staged", "promoted")
        },
        "events": audited,
        "elapsed_s": time.perf_counter() - started,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    partial = args.report.with_suffix(".partial.json")
    partial.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    partial.replace(args.report)
    print(
        f"[DONE] audited_events={len(audited)} failures={len(failures)} "
        f"report={args.report}",
        flush=True,
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
