# TLCD sample events

The [`samples-v1.0`](https://github.com/SOTIF-AVLab/TLCD/releases/tag/samples-v1.0) GitHub release contains one complete event for each city–category combination: two cities × eight categories = 16 sample events.

Each ZIP archive preserves the dataset-relative path and contains:

- `*_EgoInfo.csv`, `*_ObjInfo.csv`, and `*_MapInfo.csv`;
- `*_EvidenceChain.csv` and `*_record.json`;
- seven final fixed-30-Hz HEVC/MP4 videos under `video/`;
- `video_cfr30_metadata/fixed_fps_qa.json`.

Original pre-conversion H.265 backups are not duplicated in the sample archives. The sample videos are an interim release and may later be replaced with de-identified versions. Record the release tag and SHA-256 checksum when using a sample.

Machine-readable provenance is provided in [`manifest.csv`](manifest.csv), and checksums are duplicated in [`SHA256SUMS`](SHA256SUMS).

## Download

Download an individual asset from the release page, or use a URL of the form:

```text
https://github.com/SOTIF-AVLab/TLCD/releases/download/samples-v1.0/TLCD_sample_Nanjing_01_MaxSpdlim.zip
```

Verify a download on macOS or Linux with:

```bash
shasum -a 256 -c SHA256SUMS
```

## Full dataset

To request the full dataset, email `hong_wang@mail.tsinghua.edu.cn` or `zhao_cx25@mails.tsinghua.edu.cn` with the subject **[Apply for TLCD] name_country(region)_organization**. Include your affiliation, research purpose, intended use, and requested data scope.
