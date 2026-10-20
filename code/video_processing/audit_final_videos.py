#!/usr/bin/env python3
"""Read-only, full-decoding audit of the current TLCD H.264 event videos.

Reports describe only the selected events. This does not detect faces/plates,
apply redaction, or establish frame-level sensor synchronization.
"""

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import av

TAGS = ("120", "30", "Side_B", "Side_F", "Side_L", "Side_R", "rear")


def audit_video(path, duration_ms, image_size):
    result = {"file": path.name, "errors": []}
    expected = (30 * duration_ms + 500) // 1000
    result["expected_frames"] = expected
    try:
        with av.open(str(path)) as container:
            if len(container.streams.video) != 1:
                raise ValueError("expected exactly one video stream")
            stream = container.streams.video[0]
            stream.codec_context.thread_count = 2
            codec = stream.codec_context.name
            rate = float(stream.average_rate) if stream.average_rate is not None else None
            dimensions = [stream.codec_context.width, stream.codec_context.height]
            duration = float(stream.duration * stream.time_base) if stream.duration is not None else None
            count = 0
            corrupt = 0
            for frame in container.decode(stream):
                count += 1
                corrupt += bool(frame.is_corrupt)
            result.update(codec=codec, fps=rate, image_size=dimensions,
                          duration_s=duration, decoded_frames=count, corrupt_frames=corrupt)
            if codec != "h264":
                result["errors"].append("codec is not H.264")
            if rate != 30:
                result["errors"].append("frame rate is not 30 Hz")
            if dimensions != image_size:
                result["errors"].append("dimensions do not match camera parameters")
            if count != expected:
                result["errors"].append("decoded frame count does not match round(30T)")
            if corrupt:
                result["errors"].append("decoder marked corrupt frames")
            if duration is None:
                result["errors"].append("missing video-stream duration")
            else:
                error = abs(duration - duration_ms / 1000)
                result["duration_error_s"] = error
                if error > 1 / 60 + 1e-6:
                    result["errors"].append("duration differs by more than half a 30-Hz frame")
    except Exception as error:
        result["errors"].append(f"cannot fully decode: {type(error).__name__}: {error}")
    result["passed"] = not result["errors"]
    return result


def audit_event(event, camera_sets):
    result = {"event": str(event), "errors": [], "videos": []}
    try:
        match = re.fullmatch(r"event_(\d+)", event.name)
        if not match:
            raise ValueError("invalid event directory name")
        records = list(event.glob("*_record.json"))
        if len(records) != 1:
            raise ValueError("expected exactly one record JSON")
        record = json.loads(records[0].read_text(encoding="utf-8-sig"))
        bounds = re.findall(r"\d{13}", record["Timestamp"])
        if len(bounds) != 2:
            raise ValueError("expected two millisecond epoch timestamps")
        duration_ms = int(bounds[1]) - int(bounds[0])
        if duration_ms <= 0:
            raise ValueError("event duration must be positive")
        cameras = {c["video_tag"]: c for c in camera_sets[record["camera_set_id"]]["cameras"]}
        if set(cameras) != set(TAGS):
            raise ValueError("camera set must contain the seven expected views")
        expected = {f"video_{tag}_event_{int(match[1]):03d}.mp4" for tag in TAGS}
        actual = {p.name for p in (event / "video").glob("*.mp4")}
        if actual != expected:
            result["errors"].append(f"video file set mismatch: missing={sorted(expected-actual)}, extra={sorted(actual-expected)}")
        result["event_duration_s"] = duration_ms / 1000
        for tag in TAGS:
            path = event / "video" / f"video_{tag}_event_{int(match[1]):03d}.mp4"
            result["videos"].append(audit_video(path, duration_ms, cameras[tag]["image_size"]))
    except Exception as error:
        result["errors"].append(f"invalid event: {type(error).__name__}: {error}")
    result["passed"] = not result["errors"] and len(result["videos"]) == 7 and all(v["passed"] for v in result["videos"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", required=True, type=Path)
    parser.add_argument("--event", action="append", help="Dataset-relative event directory; repeat to select a subset. Default: all event directories.")
    parser.add_argument("--output", required=True, type=Path, help="New report path; existing files are never overwritten.")
    args = parser.parse_args()
    root = args.dataset_root.resolve()
    if args.output.exists():
        parser.error("output already exists; choose a new report path")
    parameters = json.loads((root / "camera_parameters.json").read_text(encoding="utf-8-sig"))
    camera_sets = {s["camera_set_id"]: s for s in parameters["camera_sets"]}
    events = sorted({(root / p).resolve() for p in args.event}) if args.event else sorted(p.resolve() for p in root.glob("*_valid/*/*/event_*") if p.is_dir())
    if not events or any(not p.is_relative_to(root) for p in events):
        parser.error("no events selected, or an event is outside dataset-root")
    results = []
    for event in events:
        result = audit_event(event, camera_sets)
        result["event"] = event.relative_to(root).as_posix()
        results.append(result)
        print(f"{len(results)}/{len(events)} {result['event']}: {'PASS' if result['passed'] else 'FAIL'}", flush=True)
    report = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Selected event directories only; full video decoding, not a de-identification or sensor-synchronization audit.",
        "selection": "explicit event subset" if args.event else "all discovered event directories",
        "events_checked": len(results),
        "videos_checked": sum(len(r["videos"]) for r in results),
        "passed": all(r["passed"] for r in results),
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
