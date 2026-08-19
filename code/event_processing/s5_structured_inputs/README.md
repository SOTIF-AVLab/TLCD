# Event processing

TLCD events are processed in three release-building stages:

1. [`s5_structured_inputs/`](s5_structured_inputs/) produces the three synchronized upstream tables for every event: `EgoInfo.csv`, `ObjInfo.csv`, and `MapInfo.csv`.
2. [`s6_evidence_chain/`](s6_evidence_chain/) combines these inputs with category-specific monitoring variables to create `EvidenceChain.csv`.
3. [`s7_record_json/`](s7_record_json/) converts the evidence chain and event metadata into the released `*_record.json` file.

The eight numbered category implementations correspond to maximum speed, minimum speed, following distance, lateral distance, lane change, continuous lane change, road marking, and overtaking. Shared MATLAB helpers are in [`helpers/`](helpers/).

Before running S5 or S6, point `TLCD_DATA_ROOT` to one city-level source directory:

```bash
export TLCD_DATA_ROOT=/path/to/Nanjing
```

Run the MATLAB category scripts individually or use `S5_run_selected_Event_input` and `S6_run_selected_Evidence_chain`. For S7:

```bash
export TLCD_DATA_ROOT=/path/to/Nanjing
python s7_record_json/S7_run_all.py
```

S1–S4 are intentionally excluded. The paper treats acquisition output as already organized by event; publishing those earlier local conversion and monitoring-development utilities would not represent the released data-construction boundary.

Scripts S8 and later are isolated in [`patches/`](patches/) because they are historical repairs and normalization passes rather than the primary release pipeline.
