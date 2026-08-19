# VLM-assisted scene review

These programs generate the auxiliary `Scenario_description_VLM` and `Driving_suggestion_VLM` fields and write potential evidence/video inconsistencies to sidecar review results. Model output does not determine `Result.Compliance_label`; final labels remain manually reviewed.

`review_scenarios.py` contains the shared API, frame extraction, evidence sampling, and maximum-speed workflow. The other `review_*` files add category-specific logic for minimum speed, following distance, lane change, continuous lane change, and road marking. Matching `*_prompts.py` files contain the prompts used by those workflows. `lateraldis_vlm_batch.py` contains the lateral-distance description and consistency pass found in the project source tree.

This directory reflects the category-specific source that was present in the authors' working tree. The maximum-speed, minimum-speed, following-distance, lane-change, continuous-lane-change, and road-marking entry points call the Qwen-compatible API. The lateral-distance program is a separate data-derived description and consistency pass. No standalone Qwen entry point for the overtaking category was present, so none is claimed or reconstructed here; overtaking records can be rebuilt from their evidence chains with `../event_processing/patches/S34_rebuild_valid_overtake_records.py`.

No API credential is stored in this repository. Set it at runtime:

```bash
export QWEN_API_KEY='your-key'
python review_scenarios.py \
  --dataset-root /path/to/Dataset/Nanjing_valid/01_MaxSpdlim \
  --dry-run
```

Remove `--dry-run` only after inspecting the planned events and extracted frames. The default endpoint and model identifiers reproduce the authors' environment and can be overridden with `--base-url` and `--model`.

Generated frames, API responses, backups, and run logs are deliberately excluded from version control.
