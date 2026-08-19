# TLCD 1.0 dataset information

## Scope

TLCD 1.0 is organized around traffic-law-related driving events rather than complete trips. Each released event is a synchronized temporal window containing camera streams, structured vehicle/environment signals, a machine-readable evidence chain, and a reviewed event-level result.

## Collection boundary

- Locations: highways and urban expressways in Nanjing and Changchun, China.
- Collection period: 27 August–22 October 2024 in Changchun and 10 September–23 October 2024 in Nanjing.
- Source road tests: 2,034.94 km and 35.18 h.
- Vehicle platform: FAW Hongqi EH7 engineering prototype equipped with an L3 autonomous-driving system.
- Included observations: seven camera streams covering six directions, structured ego-vehicle states, up to 30 fused surrounding objects, and lane-level map information.
- Excluded raw sensors: raw LiDAR, raw millimeter-wave radar, and raw GNSS/INS measurements.

## Directory layout

```text
Dataset/
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
| `EgoInfo.csv` | Ego-vehicle state time series resampled to a 0.01-s grid. |
| `ObjInfo.csv` | Time-aligned fused-object states for surrounding traffic participants. |
| `MapInfo.csv` | Time-aligned road, lane, and map attributes used by the monitor. |
| `EvidenceChain.csv` | Category-specific trigger variables, thresholds, intermediate states, and compliance evidence. |
| `*_record.json` | Human-readable event metadata, applicable provisions, temporal anchors, key evidence, reviewed compliance label, and auxiliary text. |
| `video/*.mp4` | Seven fixed-30-Hz HEVC event videos covering 30° and 120° front views, rear, left-front, left-rear, right-front, and right-rear views. |
| `video_cfr30_metadata/fixed_fps_qa.json` | Per-event generation and decoding QA for the fixed-rate videos. |

The seven filenames are `video_30_event_NNN.mp4`, `video_120_event_NNN.mp4`, `video_rear_event_NNN.mp4`, and `video_Side_{B,F,L,R}_event_NNN.mp4`, where `NNN` is the zero-padded event number.

## Event labels

`Result.Compliance_label` is the final event-level binary label:

- `Compliance`: the reviewed event is compliant with the applicable provision(s).
- `Violation`: the reviewed event violates the applicable provision(s).

Frame-level states in the evidence chain preserve temporal transitions such as compliance-to-violation or violation-to-compliance. These process states should not be collapsed into additional event labels without an explicit analytical reason.

`Scenario_description_VLM` and `Driving_suggestion_VLM` are auxiliary model-generated text fields. They were used to improve interpretability and assist quality review, but they did not determine the final compliance label.

## Time alignment

Structured topics are mapped to a shared `event_time` axis with a 0.01-s interval. At each target timestamp, the latest source sample not later than that timestamp is retained (zero-order hold). For each camera view, decodable source frames are ordered as acquired and mapped uniformly to the event interval. The nearest source frame is selected on a fixed 30-Hz grid; the nearest real frame is repeated where necessary, and no interpolated imagery is synthesized. `event_time` and the timestamp interval in `record.json` remain the authoritative time base for the 100-Hz structured signals.

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

One complete event for each city–category combination is distributed through the repository's GitHub sample release. See [`samples/README.md`](samples/README.md) for the 16-asset manifest and checksums. Sample videos are an interim release and may later be replaced by de-identified versions.

The full dataset is available through an application process while video de-identification and release review are ongoing. Contact `hong_wang@mail.tsinghua.edu.cn` or `zhao_cx25@mails.tsinghua.edu.cn` using the subject `[Apply for TLCD] name_country(region)_organization`; include affiliation, research purpose, intended use, and requested data scope.
