# TLCD 1.0 dataset information

## Scope

TLCD 1.0 is organized around traffic-law-related driving events rather than complete trips. Each released event is a synchronized temporal window containing camera streams, structured vehicle/environment signals, a machine-readable evidence chain, and a reviewed event-level result.

The complete author-supplied Chinese field dictionary is mirrored in [`DatasetInfo.md`](DatasetInfo.md). Camera parameters are provided in [`camera_parameters.json`](camera_parameters.json), with an English usage guide in [`CAMERA_PARAMETERS.md`](CAMERA_PARAMETERS.md). The dictionary and parameter JSON are copied unchanged from the current dataset root.

## Collection boundary

- Locations: highways and urban expressways in Nanjing and Changchun, China.
- Collection period: 27 August–22 October 2024 in Changchun and 10 September–23 October 2024 in Nanjing.
- Source road tests: 2,034.94 km and 29.41 h, following the current manuscript.
- Vehicle platform: FAW Hongqi EH7 engineering prototype equipped with an L3 autonomous-driving system.
- Included observations: seven camera streams covering six directions, structured ego-vehicle states, up to 30 fused surrounding objects, and lane-level map information.
- Excluded raw sensors: raw LiDAR, raw millimeter-wave radar, and raw GNSS/INS measurements.

The mileage and duration below reproduce the manuscript's road-test table. They describe source road tests, not sums over overlapping event windows.

| Collection site | Manual (km) | Autonomous (km) | Total (km) | Manual (h) | Autonomous (h) | Total (h) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Nanjing | 776.33 | 573.81 | 1,350.14 | 11.34 | 8.36 | 19.70 |
| Changchun | 51.01 | 633.79 | 684.80 | 1.73 | 7.98 | 9.71 |
| Total | 827.34 | 1,207.60 | 2,034.94 | 13.07 | 16.34 | 29.41 |

## Directory layout

```text
Dataset/
├── DatasetInfo.md
├── camera_parameters.json
├── Changchun_valid/
│   ├── 01_MaxSpdlim/
│   ├── 02_MinSpdlim/
│   ├── 03_FollowDis/
│   ├── 04_LateralDis/
│   ├── 05_LaneChange/
│   ├── 06_ContinueLaneChange/
│   ├── 07_RoadMarking/
│   └── 08_Overtake/
└── Nanjing_valid/
    └── ... same eight categories ...
```

Within each category, source segments contain one or more `event_*` directories. A typical event directory is expected to contain:

| File | Purpose |
| --- | --- |
| `*_EgoInfo.csv` | Ego-vehicle state time series resampled to a 0.01-s grid. |
| `*_ObjInfo.csv` | Time-aligned fused-object states for surrounding traffic participants. |
| `*_MapInfo.csv` | Time-aligned road, lane, and map attributes used by the monitor. |
| `*_EvidenceChain.csv` | Category-specific trigger variables, thresholds, intermediate states, and compliance evidence. |
| `*_record.json` | Human-readable event metadata, applicable provisions, temporal anchors, key evidence, reviewed compliance label, and auxiliary text. |
| `video/*.mp4` | Seven fixed-30-Hz H.264/AVC event videos covering 30° and 120° front views, rear, left-front, left-rear, right-front, and right-rear views. |

The seven filenames are `video_30_event_NNN.mp4`, `video_120_event_NNN.mp4`, `video_rear_event_NNN.mp4`, and `video_Side_{B,F,L,R}_event_NNN.mp4`, where `NNN` is the zero-padded event number.

The refreshed events contain four CSV files, one record JSON and seven videos. Historical `video_cfr30_metadata` and original-video backup directories are not required parts of the current release. Validation of the refreshed public samples is recorded separately under [`samples/`](samples/).

## Camera parameters

Each record's top-level `camera_set_id` selects one of seven entries in `camera_parameters.json` → `camera_sets`. Within that set, match the video's tag to `cameras[].video_tag`. Every set contains seven views; the two front views have a video resolution of 1408 × 792, and the remaining five views have a resolution of 1920 × 1200.

The file includes K, eight distortion coefficients, camera-to-ego rotation and translation, an xyzw quaternion, and both directions of the 4 × 4 transform. Intrinsics correspond to the distributed video resolution. Front 30° uses effective rectified intrinsics and zero distortion under the dataset's adopted convention; front 120° intrinsics are conditional on full-frame proportional resizing without extra cropping or reprojection. See the [usage guide](CAMERA_PARAMETERS.md) for coordinate axes, units, filename mappings and an executable lookup example.

## Event labels

`Result.Compliance_label` is the final event-level binary label:

- `Compliance`: the reviewed event is compliant with the applicable provision(s).
- `Violation`: the reviewed event violates the applicable provision(s).

Frame-level states in the evidence chain preserve temporal transitions such as compliance-to-violation or violation-to-compliance. These process states should not be collapsed into additional event labels without an explicit analytical reason.

`Result.Scenario_description_VLM` and `Result.Driving_suggestion_VLM` contain the final English auxiliary scene description and driving suggestion, including subsequent review and revision. They were used to improve interpretability and assist quality review, but they did not determine the final compliance label. Current records do not retain the old `Scenario_description` and `Driving_suggestion` fields.

## Time alignment

Structured topics are mapped to a shared `event_time` axis with a 0.01-s interval. At each target timestamp, the latest source sample not later than that timestamp is retained (zero-order hold). For each camera view, decodable source frames are ordered as acquired and mapped uniformly to the event interval. The nearest source frame is selected on a fixed 30-Hz grid; the nearest real frame is repeated where necessary, and no interpolated imagery is synthesized. `event_time` and the timestamp interval in `record.json` remain the authoritative time base for the 100-Hz structured signals.

This establishes event-window and playback-duration alignment, not independently verified frame-by-frame synchronization of original camera acquisition timestamps with structured signals. The 30-Hz video frames and 100-Hz structured samples are not in one-to-one correspondence.

## Field dictionary notes

The mirrored `DatasetInfo.md` documents variable units, missing-value codes, object and lane fields, category-specific evidence, JSON summaries and camera geometry. Exact column names should be read from the released CSV headers: minimum-speed columns use underscores (for example, `trigger_IMR_78_2`), although the supplied dictionary still spells some of them with a dot. Regulation identifiers such as `IMR_78.2` remain distinct from CSV column names. The dictionary's monitor definitions also include reserved provisions not present in the released records' `Article.ID` values; these should not be counted as additional observed provisions.

## Event-window construction

The eight categories use different temporal semantics. Continuous-state constraints are anchored around trigger/compliance transitions; process events retain their effective interval; lane-change and overtaking events retain the complete state-transition process. Approximately 3 s of context is generally retained before and after the defining interval, subject to category-specific rules.

## Recommended use

- Split data by source segment or collection session—not by individual event alone—to reduce temporal leakage.
- Preserve city, driving-mode, category, and compliance distributions when constructing benchmark subsets.
- Account for pronounced class imbalance and long-tailed contextual attributes.
- Treat VLM text as auxiliary annotation and verify it against the evidence chain/video for safety-critical analyses.
- Cite the exact dataset version or commit used.

## Known packaging note

One of 5,174 discovered candidate event directories did not contain a valid `record.json` and is excluded. The validated dataset count is therefore 5,173 events.

## Samples and access

One complete event for each city–category combination is distributed through the [`samples-v1.1`](https://github.com/SOTIF-AVLab/TLCD/releases/tag/samples-v1.1) release. The 16 events retain the previous sample identities and use the current dataset files. Each ZIP also includes the root-level parameter JSON, Chinese field dictionary, English camera guide and per-file checksums. See [`samples/README.md`](samples/README.md) for the manifest and validation results.

The full dataset remains available through an application process. Contact `hong_wang@mail.tsinghua.edu.cn` or `zhao_cx25@mails.tsinghua.edu.cn` using the subject `[Apply for TLCD] name_country(region)_organization`; include affiliation, research purpose, intended use, and requested data scope.
