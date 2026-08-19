# Processing and validation code

This directory contains the custom code used after event-triggered acquisition to construct and validate TLCD 1.0.

| Directory | Purpose |
| --- | --- |
| [`event_processing/`](event_processing/) | Generate the three structured CSV inputs, build `EvidenceChain.csv`, create `record.json`, and document historical correction scripts. |
| [`video_processing/`](video_processing/) | Extract the seven camera streams and generate the released fixed-30-Hz HEVC/MP4 videos. |
| [`vlm_review/`](vlm_review/) | Generate auxiliary scene descriptions and driving suggestions and flag questionable cases for manual review. |
| [`validation/`](validation/) | Reproduce dataset statistics and run structural, temporal, and video-integrity checks. |

The repository intentionally starts at S5. S1–S4 are not distributed because the acquisition platform stored synchronized data as event-level records at collection time; the public processing chain begins with construction of the released event files.

Local paths are removed from the primary pipeline; set roots with the documented command-line arguments or environment variables. The MATLAB S5–S6 scripts use `TLCD_DATA_ROOT`; the VLM programs read the API credential from `QWEN_API_KEY` or an explicitly supplied external key file. Historical patch scripts retain some project-specific defaults for provenance and must be reviewed before use.

The historical patch scripts are provided for provenance. They are not required for a clean run of the S5–S7 pipeline on newly acquired events.
