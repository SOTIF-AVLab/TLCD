# Video validation

The `samples-v1.1` check covers **16 events and all 112 sample videos**, not the complete dataset. See [`samples/validation_summary.json`](../../samples/validation_summary.json) and the per-video [`samples/video_validation.csv`](../../samples/video_validation.csv).

All 112 videos were completely decoded and checked as H.264/AVC at 30 Hz, with dimensions matching their camera parameter entries and decoded-frame counts matching `floor(30T + 0.5)`. The maximum absolute video/event duration difference was 0.0166667 s. These checks were also reproduced using [`audit_final_videos.py`](../../code/video_processing/audit_final_videos.py) on the public sample contents. They do not establish absence of missed redactions, annotation correctness, or original frame-level sensor synchronization.

To run these checks on a dataset directory, see the checker's [usage and environment](../../code/video_processing/README.md#current-h264-release-checker).
