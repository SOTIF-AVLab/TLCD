from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd


DATA_ROOT = Path(r"Z:\HongqiData\Nanjing_valid\01_MaxSpdlim")
OUTPUT = Path(__file__).resolve().parent / "audit_outputs" / "MaxSpdlim_principle_audit_20260709.json"
EXPRESS_ROAD_TYPES = {1, 2, 34}
EXPRESS_LANE_TYPES = {1, 8}
ARTICLE_COLUMNS = {
    "45.1": ("trigger_IMR_45_1", "com_IMR_45_1"),
    "46.3": ("trigger_IMR_46_3", "com_IMR_46_3"),
    "46.4": ("trigger_IMR_46_4", "com_IMR_46_4"),
    "46.5": ("trigger_IMR_46_5", "com_IMR_46_5"),
    "78.1": ("trigger_IMR_78_1", "com_IMR_78_1"),
    "78.3": ("trigger_IMR_78_3", "com_IMR_78_3"),
}
COMPARE_COLUMNS = [
    column
    for pair in ARTICLE_COLUMNS.values()
    for column in pair
] + ["Road_type", "Lane_type", "IsMaxSpdsignArea", "Thres_MaxSpdlim"]


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def article_values(row: pd.Series, column_index: int) -> str:
    values = []
    for article, columns in ARTICLE_COLUMNS.items():
        value = pd.to_numeric(pd.Series([row.get(columns[column_index], 0)]), errors="coerce").iloc[0]
        if pd.notna(value) and value != 0:
            values.append(f"{article}:{int(value)}" if column_index else article)
    return "|".join(values)


def expected_evidence(map_info: pd.DataFrame, evidence: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    expected = evidence.copy()
    for trigger_col, com_col in ARTICLE_COLUMNS.values():
        expected[trigger_col] = 0
        expected[com_col] = 0

    diagnostics = pd.DataFrame(index=map_info.index)
    diagnostics["no_valid_limit"] = False
    diagnostics["invalid_ego_limit"] = False

    for i, map_row in map_info.iterrows():
        lane_num = int(pd.to_numeric(pd.Series([map_row.get("LaneNumSameDirection")]), errors="coerce").fillna(0).iloc[0])
        limits = []
        for lane in range(1, min(max(lane_num, 0), 5) + 1):
            value = pd.to_numeric(pd.Series([map_row.get(f"LaneMaxSpdlim_{lane}")]), errors="coerce").iloc[0]
            if pd.notna(value) and value > 0:
                limits.append(float(value))

        diagnostics.at[i, "no_valid_limit"] = not limits
        all_120 = bool(limits) and all(value == 120 for value in limits)
        ego_lane = int(pd.to_numeric(pd.Series([map_row.get("EgoLaneIndex")]), errors="coerce").fillna(0).iloc[0])
        ego_limit = np.nan
        if 1 <= ego_lane <= 5:
            ego_limit = pd.to_numeric(
                pd.Series([map_row.get(f"LaneMaxSpdlim_{ego_lane}")]), errors="coerce"
            ).iloc[0]
        if pd.isna(ego_limit) or ego_limit <= 0:
            diagnostics.at[i, "invalid_ego_limit"] = True
            ego_limit = min(limits) if limits else 0.0

        road_type = int(pd.to_numeric(pd.Series([map_row.get("Road_type")]), errors="coerce").fillna(0).iloc[0])
        lane_type = int(
            pd.to_numeric(pd.Series([map_row.get("Lane_type_CurrentLane")]), errors="coerce").fillna(0).iloc[0]
        )
        speed_mps = pd.to_numeric(pd.Series([evidence.at[i, "Ego_velocity"]]), errors="coerce").iloc[0]
        compliance = 0
        if ego_limit > 0 and pd.notna(speed_mps):
            compliance = -1 if float(speed_mps) * 3.6 > float(ego_limit) else 1

        expected.at[i, "Road_type"] = road_type
        expected.at[i, "Lane_type"] = lane_type
        expected.at[i, "Thres_MaxSpdlim"] = int(round(ego_limit)) if ego_limit > 0 else 0

        if road_type in EXPRESS_ROAD_TYPES and lane_type in EXPRESS_LANE_TYPES:
            article = "78.1" if all_120 else "78.3"
            expected.at[i, "IsMaxSpdsignArea"] = 0 if all_120 else 1
            trigger_col, com_col = ARTICLE_COLUMNS[article]
            expected.at[i, trigger_col] = 1
            expected.at[i, com_col] = compliance
            continue

        kept_46 = False
        for article in ("46.3", "46.4", "46.5"):
            trigger_col, com_col = ARTICLE_COLUMNS[article]
            original = pd.to_numeric(pd.Series([evidence.at[i, trigger_col]]), errors="coerce").fillna(0).iloc[0]
            if original != 0:
                expected.at[i, trigger_col] = 1
                expected.at[i, com_col] = compliance
                kept_46 = True
        if kept_46:
            expected.at[i, "IsMaxSpdsignArea"] = 0
        else:
            trigger_col, com_col = ARTICLE_COLUMNS["45.1"]
            expected.at[i, "IsMaxSpdsignArea"] = 1
            expected.at[i, trigger_col] = 1
            expected.at[i, com_col] = compliance

    return expected, diagnostics


def json_matches_current_evidence(evidence_path: Path, record_path: Path, event_number: int) -> bool:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import s7_event_json as s7

    expected = s7._record(
        evidence_path,
        event_number,
        s7.CATEGORIES["max_speed"],
        "China, Nanjing",
        {},
    )
    current = json.loads(record_path.read_text(encoding="utf-8-sig"))

    evidence_keys = (
        "Article_status",
        "Ego_speed_kph",
        "Applicable_max_speed_limit_kph",
        "Applicable_max_speed_limit_at_peak_speed_kph",
        "Time_of_peak_speed_s",
        "Road_types",
        "Lane_types",
        "Inside_speed_limit_sign_area",
        "Speed_limit_sign_context",
    )
    result_keys = ("Compliance_label", "Violation_reason", "Driving_suggestion")
    current_scenario = current.get("Result", {}).get("Scenario_description", "")
    expected_scenario = expected.get("Result", {}).get("Scenario_description", "")
    expected_scenario = expected_scenario.split(" The recorded sign description", 1)[0]
    return (
        current.get("Article") == expected.get("Article")
        and current.get("EventAnchor") == expected.get("EventAnchor")
        and all(
            current.get("Evidence", {}).get(key) == expected.get("Evidence", {}).get(key)
            for key in evidence_keys
        )
        and all(
            current.get("Result", {}).get(key) == expected.get("Result", {}).get(key)
            for key in result_keys
        )
        and current_scenario == expected_scenario
    )


def audit_event(event_dir: Path) -> dict:
    match = re.fullmatch(r"event_(\d+)", event_dir.name)
    event_number = int(match.group(1))
    prefix = f"MaxSpdlim_event_{event_number}"
    paths = {
        "map": event_dir / f"{prefix}_MapInfo.csv",
        "evidence": event_dir / f"{prefix}_EvidenceChain.csv",
        "json": event_dir / f"{prefix}_record.json",
    }
    missing = [name for name, path in paths.items() if not path.exists()]
    base = {
        "segment": event_dir.parent.name,
        "event": event_dir.name,
        "event_number": event_number,
        "event_path": str(event_dir),
    }
    if missing:
        return {**base, "status": "DATA_ISSUE", "issue": "missing_" + "|missing_".join(missing)}

    map_info = pd.read_csv(paths["map"], encoding="utf-8-sig")
    evidence = pd.read_csv(paths["evidence"], encoding="utf-8-sig")
    if len(map_info) != len(evidence):
        return {
            **base,
            "status": "DATA_ISSUE",
            "issue": "row_count_mismatch",
            "map_frames": len(map_info),
            "evidence_frames": len(evidence),
        }

    expected, diagnostics = expected_evidence(map_info, evidence)
    actual_numeric = evidence[COMPARE_COLUMNS].apply(numeric)
    expected_numeric = expected[COMPARE_COLUMNS].apply(numeric)
    differences = ~(actual_numeric.eq(expected_numeric) | (actual_numeric.isna() & expected_numeric.isna()))
    mismatch_mask = differences.any(axis=1)
    trigger_cols = [pair[0] for pair in ARTICLE_COLUMNS.values()]
    compliance_cols = [pair[1] for pair in ARTICLE_COLUMNS.values()]
    json_match = json_matches_current_evidence(paths["evidence"], paths["json"], event_number)
    no_limit_frames = int(diagnostics["no_valid_limit"].sum())
    invalid_ego_limit_frames = int(diagnostics["invalid_ego_limit"].sum())
    data_issue = no_limit_frames > 0
    does_not_satisfy = bool(mismatch_mask.any() or not json_match or data_issue)

    result = {
        **base,
        "status": "REVIEW" if does_not_satisfy else "PASS",
        "issue": "|".join(
            name
            for condition, name in (
                (differences[trigger_cols].any(axis=1).any(), "trigger"),
                (differences[compliance_cols].any(axis=1).any(), "compliance"),
                (differences[["Road_type", "Lane_type"]].any(axis=1).any(), "road_or_lane_type"),
                (differences["IsMaxSpdsignArea"].any(), "sign_area"),
                (differences["Thres_MaxSpdlim"].any(), "threshold"),
                (not json_match, "json"),
                (data_issue, "no_valid_limit"),
            )
            if condition
        ),
        "total_frames": len(evidence),
        "mismatch_frames": int(mismatch_mask.sum()),
        "trigger_mismatch_frames": int(differences[trigger_cols].any(axis=1).sum()),
        "compliance_mismatch_frames": int(differences[compliance_cols].any(axis=1).sum()),
        "road_lane_mismatch_frames": int(differences[["Road_type", "Lane_type"]].any(axis=1).sum()),
        "sign_area_mismatch_frames": int(differences["IsMaxSpdsignArea"].sum()),
        "threshold_mismatch_frames": int(differences["Thres_MaxSpdlim"].sum()),
        "json_mismatch": int(not json_match),
        "no_valid_limit_frames": no_limit_frames,
        "invalid_ego_limit_frames": invalid_ego_limit_frames,
        "first_mismatch_time_s": "",
        "current_articles": "",
        "expected_articles": "",
        "current_compliance": "",
        "expected_compliance": "",
    }
    if mismatch_mask.any():
        first = mismatch_mask[mismatch_mask].index[0]
        result.update(
            {
                "first_mismatch_time_s": evidence.at[first, "event_time"],
                "current_articles": article_values(evidence.loc[first], 0),
                "expected_articles": article_values(expected.loc[first], 0),
                "current_compliance": article_values(evidence.loc[first], 1),
                "expected_compliance": article_values(expected.loc[first], 1),
            }
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit Nanjing_valid maximum-speed evidence and JSON files.")
    parser.add_argument("--root", type=Path, default=DATA_ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    event_dirs = sorted(
        event_dir
        for segment in args.root.iterdir()
        if segment.is_dir()
        for event_dir in segment.glob("event_*")
        if event_dir.is_dir() and re.fullmatch(r"event_\d+", event_dir.name)
    )
    results = [audit_event(event_dir) for event_dir in event_dirs]
    review = [row for row in results if row["status"] != "PASS"]
    issue_counts = Counter(
        issue
        for row in review
        for issue in str(row.get("issue", "")).split("|")
        if issue
    )
    payload = {
        "scope": str(args.root),
        "audit_date": "2026-07-09",
        "assumptions": [
            "The rule is evaluated frame by frame.",
            "Valid maximum limits are positive LaneMaxSpdlim values within LaneNumSameDirection, capped at lane 5.",
            "The ego-lane limit is selected by EgoLaneIndex; if invalid, the minimum valid limit is used, matching S16.",
            "Ego_velocity in EvidenceChain is interpreted as m/s and converted to km/h for comparison.",
            "Existing 46.3/46.4/46.5 triggers are preserved only outside road types 1/2/34 with lane types 1/8.",
        ],
        "summary": {
            "total_events": len(results),
            "pass_events": len(results) - len(review),
            "review_events": len(review),
            "evidence_mismatch_events": sum(row.get("mismatch_frames", 0) > 0 for row in results),
            "json_mismatch_events": sum(row.get("json_mismatch", 0) > 0 for row in results),
            "data_issue_events": sum(row.get("status") == "DATA_ISSUE" or row.get("no_valid_limit_frames", 0) > 0 for row in results),
            "issue_event_counts": dict(sorted(issue_counts.items())),
        },
        "review_events": review,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    print(f"output={args.output}")


if __name__ == "__main__":
    main()
