# Video processing

This directory contains the historical extraction/fixed-frame-rate stage, the subsequent masked-video release-layout utility, and a read-only checker for the current H.264 release.

The refreshed `samples-v1.1` videos are masked, fixed-30-Hz H.264/AVC MP4 files. Each current event has seven videos directly under `video/`. The earlier HEVC-stage scripts depend on staging, backup and QA files that are not distributed with the current samples. Do not run their promotion/audit commands against the reduced current layout. Current sample validation is documented in [`samples/`](../../samples/).

## Historical extraction and fixed-rate stage

1. `extract_valid_videos_from_record_timestamps.py` selects the seven camera streams from the original ROS bags using each released event's timestamp interval.
2. `regenerate_fixed_fps_event_videos.py` maps decodable source frames to a fixed 30-Hz event-time grid for one event. The nearest real frame is repeated when required; interpolated imagery is not synthesized.
3. `regenerate_all_fixed_fps_videos.py` runs the fixed-rate conversion across the dataset.
4. `audit_fixed_fps_dataset.py` independently verifies codec, frame rate, decoded frame count, duration, and preservation of source videos.
5. `promote_fixed_fps_dataset.py` promotes an audited staging directory while retaining the original H.265 videos for rollback.

Legacy category-specific extraction scripts and intermediate logs are intentionally excluded.

## Masking and release-layout stage

The recorded processing order is: source extraction → fixed-30-Hz event-window conversion → masking/H.264 output in `video/video_masked/` → promotion into the released `video/` directory. Original videos and intermediate assets were retained in a separate backup, not included in the public ZIPs. Masking does not establish frame-level synchronization with the 100-Hz structured signals.

`dataset_masked_restructure.py` is the original layout-maintenance utility, published unchanged. It checks the seven source/masked file pairs and their MP4 movie-header durations, backs up the pre-promotion assets, promotes already-masked videos and verifies the reduced layout. **It does not detect or mask faces/plates and does not encode H.264.** The detector/encoder that produced `video_masked/` has not yet been located in the supplied processing source tree and is not included here; this remains an explicit gap in end-to-end reproducibility.

The maintenance utility's `audit` and `verify` commands are read-only for dataset contents. `backup` writes a backup; `promote` and `relocate` change the source layout and can remove backed-up intermediate copies. Use them only on the historical pre-promotion layout after checking the audit and backup. The `relocate` command is a recovery path requiring matching saved backup-selection metadata, not the normal starting command. Public sample users do not need any of these maintenance commands.

For a restored historical working copy, inspect the input layout without promoting it:

```bash
python dataset_masked_restructure.py audit \
  --source /path/to/historical-working-copy \
  --report /path/to/new-masked-layout-audit.json
```

The historical input must contain four CSVs, one record JSON, `clip/`, `video_original_h265/`, `video_cfr30_metadata/`, seven source MP4s in `video/`, and their seven masked counterparts in `video/video_masked/`. Its duration checks read MP4 headers only; backup verification uses file paths and sizes, not historical content hashes. They are not full-decoding or missed-redaction tests.

## Current H.264 release checker

`audit_final_videos.py` is a standalone checker derived from the current sample-release validation. It works directly with the reduced released layout and does not need the historical backups. It verifies seven expected views, H.264, 30 Hz, dimensions against the referenced camera parameter set, complete decoding, decoded-frame count `floor(30T + 0.5)`, and a duration difference of no more than half a 30-Hz frame. It never changes input data and refuses to overwrite an existing report.

Use a separate environment for the current checker:

```bash
python -m pip install -r requirements-final-video.txt
python audit_final_videos.py \
  --dataset-root /path/to/extracted-samples \
  --output /path/to/new-final-video-report.json
```

The root must include `camera_parameters.json` and the city/category/segment/event folders. Repeated `--event CITY/CATEGORY/SEGMENT/event_XX` options restrict the check to selected events. Without them, all event directories under that root are checked. The report states its selection and counts; checking a sample folder is not a full-dataset audit. This checker does not assess missed redactions, annotation correctness or original sensor synchronization.

In the current checker's environment, run its regression tests from this directory with:

```bash
python -m unittest discover -s tests -v
```

## Historical-stage environment

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
