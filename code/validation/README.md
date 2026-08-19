# Validation and statistics

- `stat_dataset_events.py`: reproduce city, category, provision, driving-mode, and compliance counts.
- `stat_scene_diversity.py`: reproduce the scene-diversity tables and classification QA outputs.
- `stat_technical_validation.py`: summarize candidate/release counts and technical-validation evidence.
- `validate_dataset_integrity.py`: validate the event directory structure, required files, JSON fields, and temporal consistency.
- `validate_video_integrity.py`: inspect MP4 metadata and decoding integrity.

Install the Python dependencies and point the tools to the dataset root:

```bash
python -m pip install -r requirements.txt
export TLCD_DATASET_ROOT=/path/to/Dataset
python stat_dataset_events.py
python validate_dataset_integrity.py --dataset-root "$TLCD_DATASET_ROOT"
```

Published aggregate outputs are stored in [`../../statistics/`](../../statistics/). Figure-rendering programs are intentionally not included in the public code package.
