# TLCD sample events

The [`samples-v1.1`](https://github.com/SOTIF-AVLab/TLCD/releases/tag/samples-v1.1) GitHub release contains one complete event for each city–category combination: two cities × eight categories = 16 sample events. Refreshed on 2026-10-20, it contains the current dataset files for the 16 selected events.

Each ZIP archive preserves the dataset-relative path and contains:

- `*_EgoInfo.csv`, `*_ObjInfo.csv`, and `*_MapInfo.csv`;
- `*_EvidenceChain.csv` and `*_record.json`;
- seven current fixed-30-Hz H.264/AVC MP4 videos under `video/`;
- root-level `camera_parameters.json`, `DatasetInfo.md` and `CAMERA_PARAMETERS.md`;
- `FILES.sha256`, covering every data and documentation file in that ZIP.

Each record now includes `camera_set_id`; use it with the included parameter JSON and the video's tag to select the appropriate intrinsics, distortion coefficients and extrinsics. Current `Result` objects contain `Compliance_label`, `Violation_reason`, `Scenario_description_VLM` and `Driving_suggestion_VLM`. The two VLM text fields are final English auxiliary annotations, including review and revision.

Historical H.265 backups and `video_cfr30_metadata/fixed_fps_qa.json` are not copied into the refreshed archives: they describe an earlier processing stage. Record both the release tag and SHA-256 checksum when using a sample.

Machine-readable provenance is provided in [`manifest.csv`](manifest.csv), including the city, category, dataset-relative event path, duration, size, checksum, parameter-set reference and release tag. Archive checksums are also in [`SHA256SUMS`](SHA256SUMS).

## Validation

[`validation_summary.json`](validation_summary.json) and [`video_validation.csv`](video_validation.csv) report checks on these 16 samples only:

- four CSV files per event with matching `event_time` values and 0.01-s steps;
- a parseable record JSON with valid camera references and current auxiliary fields;
- complete decoding of all 112 H.264 videos at 30 frames/s, with the expected frame count `floor(30T + 0.5)` and no more than half a frame of duration difference from the event interval;
- video dimensions matching the selected camera entries;
- numerical consistency of camera transforms and byte-for-byte verification of archived files against the source files read during packaging.

These checks do not constitute a new full-dataset validation, a new assessment of annotation correctness, or an independent evaluation of calibration accuracy or video de-identification.

CSV endpoint coverage is preserved as supplied. For example, the Changchun lateral-distance sample has a 5.20-s JSON interval, while its matching CSV time axes span 0.01–5.18 s. The validation summary reports endpoint gaps exceeding one structured sampling interval; no samples are invented to fill those gaps.

## Download

Download an individual asset from the release page, or use a URL of the form:

```text
https://github.com/SOTIF-AVLab/TLCD/releases/download/samples-v1.1/TLCD_sample_Nanjing_01_MaxSpdlim.zip
```

Verify a download on macOS or Linux with:

```bash
shasum -a 256 -c SHA256SUMS
```

Run this in the directory containing all 16 downloaded ZIPs, or select the relevant checksum line for an individual download. After extracting a single ZIP, run `shasum -a 256 -c FILES.sha256` from its extraction root to verify its contents. Extract archives into separate folders, or into one shared dataset root; their three shared documentation/parameter files are identical.

## Full dataset

To request the full dataset, email `hong_wang@mail.tsinghua.edu.cn` or `zhao_cx25@mails.tsinghua.edu.cn` with the subject **[Apply for TLCD] name_country(region)_organization**. Include your affiliation, research purpose, intended use, and requested data scope.
