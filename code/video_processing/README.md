# Video processing

This directory contains only the code needed for the video version described in the current manuscript: seven camera views encoded as fixed-30-Hz HEVC streams in MP4 containers, with duration aligned to the event interval in `record.json`.

## Pipeline

1. `extract_valid_videos_from_record_timestamps.py` selects the seven camera streams from the original ROS bags using each released event's timestamp interval.
2. `regenerate_fixed_fps_event_videos.py` maps decodable source frames to a fixed 30-Hz event-time grid for one event. The nearest real frame is repeated when required; interpolated imagery is not synthesized.
3. `regenerate_all_fixed_fps_videos.py` runs the fixed-rate conversion across the dataset.
4. `audit_fixed_fps_dataset.py` independently verifies codec, frame rate, decoded frame count, duration, and preservation of source videos.
5. `promote_fixed_fps_dataset.py` promotes an audited staging directory while retaining the original H.265 videos for rollback.

Legacy category-specific extraction scripts and intermediate logs are intentionally excluded.

## Environment

```bash
python -m pip install -r requirements-fixed-fps.txt
```

Use Python 3.10 or newer (the release code was smoke-tested with Python 3.12). The scripts require FFmpeg/FFprobe on `PATH`. For example:

```bash
python extract_valid_videos_from_record_timestamps.py \
  --dataset-root /path/to/Dataset \
  --source-root /path/to/original-data

python regenerate_all_fixed_fps_videos.py \
  --dataset-root /path/to/Dataset \
  --fps 30 \
  --timing-source event-window
```

Run the audit before promotion. `promote_fixed_fps_dataset.py --mode apply` changes directory names and should only be used after reviewing a passing audit report.
