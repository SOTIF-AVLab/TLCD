# Historical patch scripts

These files record one-off repairs, audits, and canonical rebuilds applied during dataset curation. They are retained for provenance and are not part of the clean S5–S7 processing path. Several scripts contain project-specific path defaults or expect review lists that are not distributed; inspect the target paths and use dry-run/check modes where available before applying them.

## S8–S14: early event-level corrections

- `S8_B1_3_Fix_Selected_MinSpdlim_60.m`: correct selected minimum-speed events with a 60-km/h rule context.
- `S8_B1_4_Fix_FollowDis_Congestion.m`: repair following-distance congestion/special-case fields.
- `S8_B1_6_Fix_LC_RVTL_ObjInfo.m`: correct rear-vehicle target-lane object data for lane-change events.
- `S8_B1_7_Fix_OvertakeDirection_RM.m`: correct inferred crossing/overtaking direction in road-marking events.
- `S8_B1_8_Fix_OvertakeDirection_OT.m`: correct overtaking direction in overtaking events.
- `S9_B1_6_Fix_LC_FirstFrame_Judgment.m`: repair first-frame lane-change state judgements.
- `S10_fill_speed_event_validity.py`: fill missing validity fields for speed-limit events.
- `S11_update_speed_event_descriptions.py`: refresh speed-event descriptions after validity corrections.
- `S12_update_speed_record_scenarios.py`: update speed-limit `record.json` scenario fields.
- `S13_copy_valid_events_to_nanjing_valid.py`: copy reviewed events into the validation/release tree.
- `S14_remove_missing_mp4_events.py`: remove or report events whose required MP4 files are missing.

## S15–S25: speed-limit map and sign corrections

- `S15_correct_maxspdlim_mapinfo.py`: calibrate maximum-speed map information.
- `S15_summarize_maxspdlim_corrections.py`: summarize S15 corrections.
- `S16_correct_maxspdlim_evidence_json.py`: propagate corrected maximum-speed map values into evidence and JSON.
- `S17_run_all_maxspdlim_corrections.py`: batch orchestrator for S15–S16.
- `S18_correct_minspdlim_mapinfo.py`: correct minimum-speed map and lane-rule fields.
- `S19_correct_minspdlim_evidence_json.py`: propagate minimum-speed corrections into evidence and JSON.
- `S20_run_all_minspdlim_corrections.py`: batch orchestrator for S18–S19.
- `S21_correct_maxspdlim_sign2.py`: correct the second maximum-speed-sign handling case.
- `S22_correct_maxspdlim_sign1.py`: correct the first maximum-speed-sign handling case.
- `S22_correct_nanjing_minspdlim_sign_records.py`: repair selected Nanjing minimum-speed sign records.
- `S23_move_invalid_maxspdlim_sign_events.py`: isolate events invalidated by sign review.
- `S24_audit_nanjing_valid_maxspdlim.py`: audit the reviewed Nanjing maximum-speed release set.
- `S24_build_maxspdlim_audit_workbook.mjs`: render the S24 audit into a review workbook.
- `S25_apply_nanjing_valid_maxspdlim_fixes.py`: apply approved fixes from the Nanjing audit.

## S26–S35: timestamp restoration and canonical record rebuilds

- `S26_update_valid_record_times.py`: reconcile released event timestamps with source records.
- `S26_build_skipped_record_list.mjs`: build a review list for records skipped by S26.
- `S27_restore_minspdlim_timestamps.py`: restore minimum-speed timestamps from unique velocity matches.
- `S28_format_minspdlim_timestamp_strings.py`: normalize minimum-speed timestamp serialization.
- `S28_rebuild_valid_maxspdlim_records.py`: rebuild canonical maximum-speed records from evidence chains.
- `S29_rebuild_valid_followdis_records.py`: rebuild canonical following-distance records.
- `S30_rebuild_valid_lanechange_records.py`: rebuild canonical lane-change records.
- `S31_rebuild_valid_continuelc_records.py`: rebuild canonical continuous-lane-change records.
- `S32_add_valid_driving_mode.py`: add missing driving-mode fields to rebuilt records.
- `S33_rebuild_valid_roadmarking_records.py`: rebuild canonical road-marking records.
- `S34_rebuild_valid_overtake_records.py`: rebuild canonical overtaking records.
- `S35_standardize_valid_maxspdlim.py`: standardize final maximum-speed evidence and descriptions.

## Additional named repairs

- `Patch_Ego_velocity.py`: standardize the ego-velocity column name.
- `refresh_valid_lateraldis.py`: refresh validated lateral-distance evidence and records.
- `sync_lateral_records_from_raw.py`: synchronize lateral-distance record timing from matched raw events.
