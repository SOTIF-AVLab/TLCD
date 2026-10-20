# Technical-validation reports and their scope

## Current public sample videos

The current `samples-v1.1` check covers **16 events and all 112 sample videos**, not the complete dataset. See [`samples/validation_summary.json`](../../samples/validation_summary.json) and the per-video [`samples/video_validation.csv`](../../samples/video_validation.csv).

All 112 videos were completely decoded and checked as H.264/AVC at 30 Hz, with dimensions matching their camera parameter entries and decoded-frame counts matching `floor(30T + 0.5)`. The maximum absolute video/event duration difference was 0.0166667 s. These checks were also reproduced using [`audit_final_videos.py`](../../code/video_processing/audit_final_videos.py) on the public sample contents. They do not establish absence of missed redactions, annotation correctness, or original frame-level sensor synchronization.

## Historical metadata-only report

[`video_metadata_summary.json`](video_metadata_summary.json) is retained as a **historical metadata-probe report**. Its `report_context` identifies that scope; the original numeric measurements have not been changed.

- `mode: probe` means metadata inspection, not complete decoding.
- Its maximum duration difference of 16.480115 s and zero events satisfying the all-views one-frame duration criterion describe the historical input snapshot, not the current sample videos.
- In probe mode, `decoded_frames: 0` and zero decode-error counters cannot be interpreted as evidence that all videos decoded successfully.
- The exact source-build identifier was not recorded in the original report. Do not assign it a new processing version retrospectively.

A new full-decoding audit of all 36,211 final dataset videos was not performed during the 2026-10-20 repository update. A sample check must not be cited as a dataset-wide validation. To generate a new report for the complete final dataset, use the current H.264 checker on that dataset root; see its [usage and environment](../../code/video_processing/README.md#current-h264-release-checker).

The other reports in this directory retain their original checks and scope. This documentation update does not rerun or replace them.
