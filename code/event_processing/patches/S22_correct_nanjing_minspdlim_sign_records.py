from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

from S15_correct_maxspdlim_mapinfo import write_csv_atomic


DATA_ROOT = Path(r"Z:\HongqiData\Nanjing")
EVENT_ROOT_NAME = "zEvent_MinSpdlim_sign_1_to_0"

ARTICLE_COLUMNS = {
    "78_2": ("trigger_IMR_78_2", "com_IMR_78_2"),
    "78_4": ("trigger_IMR_78_4", "com_IMR_78_4"),
    "78_5": ("trigger_IMR_78_5", "com_IMR_78_5"),
    "78_6": ("trigger_IMR_78_6", "com_IMR_78_6"),
    "78_7": ("trigger_IMR_78_7", "com_IMR_78_7"),
}


def write_csv_resilient(df: pd.DataFrame, path: Path) -> None:
    try:
        write_csv_atomic(df, path)
    except PermissionError:
        df.to_csv(path, index=False, encoding="utf-8-sig")


def numeric_value(value, default: float = 0) -> float:
    result = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(result):
        return default
    return float(result)


def fix_map_columns(map_path: Path) -> bool:
    df = pd.read_csv(map_path)
    if "LanePos" not in df.columns:
        return False

    lane_pos = df["LanePos"].copy()
    df = df.drop(columns=[column for column in ("LanePos", "EgoLaneIndex") if column in df.columns])
    original_columns = list(pd.read_csv(map_path, nrows=0).columns)
    lane_pos_index = original_columns.index("LanePos")
    lane_pos_index = min(lane_pos_index, len(df.columns))
    df.insert(lane_pos_index, "EgoLaneIndex", lane_pos.astype(int))
    write_csv_resilient(df, map_path)
    return True


def ensure_evidence_columns(evidence: pd.DataFrame) -> pd.DataFrame:
    if "Congestion" not in evidence.columns:
        evidence["Congestion"] = 0
    if "Special_case" not in evidence.columns:
        evidence["Special_case"] = 0
    for lane in range(1, 6):
        column = f"LaneMinSpdlim_{lane}"
        if column not in evidence.columns:
            evidence[column] = 0
    for trigger_col, com_col in ARTICLE_COLUMNS.values():
        if trigger_col not in evidence.columns:
            evidence[trigger_col] = 0
        if com_col not in evidence.columns:
            evidence[com_col] = 0
    return evidence


def reset_article_columns(df: pd.DataFrame) -> None:
    for trigger_col, com_col in ARTICLE_COLUMNS.values():
        df[trigger_col] = 0
        df[com_col] = 0


def sign_active_start(event_row: pd.Series) -> int:
    start_idx = int(numeric_value(event_row.get("start_idx", 0), 0))
    key_idx = int(numeric_value(event_row.get("key_idx_s", start_idx), start_idx))
    return max(0, key_idx - start_idx)


def ego_lane_limit(row: pd.Series) -> int:
    lane_index = int(numeric_value(row.get("EgoLaneIndex", 0), 0))
    if 1 <= lane_index <= 5:
        return int(round(numeric_value(row.get(f"LaneMinSpdlim_{lane_index}", 0), 0)))
    return 0


def row_special_case(evidence: pd.DataFrame, index: int, event_row: pd.Series) -> int:
    event_congestion = int(numeric_value(event_row.get("Congestion", 0), 0))
    event_special = int(numeric_value(event_row.get("Special_case", 0), 0))
    row_congestion = int(numeric_value(evidence.at[index, "Congestion"], 0))
    row_special = int(numeric_value(evidence.at[index, "Special_case"], 0))
    return max(event_congestion, event_special, row_congestion, row_special)


def compliance_value(speed_mps: float, limit_kph: float, special_case: int) -> int:
    if special_case:
        return 1
    if limit_kph < 0 or not np.isfinite(limit_kph):
        return 0
    if limit_kph == 0:
        return 1
    return -1 if speed_mps * 3.6 < limit_kph else 1


def correct_evidence(evidence_path: Path, map_path: Path, event_row: pd.Series) -> tuple[int, int]:
    evidence = ensure_evidence_columns(pd.read_csv(evidence_path))
    map_info = pd.read_csv(map_path)
    if len(evidence) != len(map_info):
        raise ValueError(f"row count mismatch: {evidence_path} evidence={len(evidence)} map={len(map_info)}")

    compare_cols = [
        "Road_type",
        "Lane_type",
        "IsMinSpdsignArea",
        "LaneNumSameDirection",
        "EgoLaneIndex",
        "Congestion",
        "Special_case",
        "Thres_MinSpdlim",
    ]
    compare_cols += [f"LaneMinSpdlim_{lane}" for lane in range(1, 6)]
    compare_cols += [ARTICLE_COLUMNS[key][0] for key in ARTICLE_COLUMNS]
    compare_cols += [ARTICLE_COLUMNS[key][1] for key in ARTICLE_COLUMNS]
    for column in compare_cols:
        if column not in evidence.columns:
            evidence[column] = 0
    old = evidence[compare_cols].copy()

    reset_article_columns(evidence)
    active_start = sign_active_start(event_row)
    event_special = max(
        int(numeric_value(event_row.get("Congestion", 0), 0)),
        int(numeric_value(event_row.get("Special_case", 0), 0)),
    )
    if event_special:
        evidence["Congestion"] = event_special if event_special == 1 else 0
        evidence["Special_case"] = event_special

    for index, map_row in map_info.iterrows():
        evidence.at[index, "Road_type"] = int(numeric_value(map_row.get("Road_type", 0), 0))
        evidence.at[index, "Lane_type"] = int(numeric_value(map_row.get("Lane_type_CurrentLane", 0), 0))
        evidence.at[index, "LaneNumSameDirection"] = int(numeric_value(map_row.get("LaneNumSameDirection", 0), 0))
        evidence.at[index, "EgoLaneIndex"] = int(numeric_value(map_row.get("EgoLaneIndex", 0), 0))
        for lane in range(1, 6):
            evidence.at[index, f"LaneMinSpdlim_{lane}"] = int(numeric_value(map_row.get(f"LaneMinSpdlim_{lane}", 0), 0))

        active = index >= active_start
        limit = ego_lane_limit(map_row)
        speed = numeric_value(evidence.at[index, "Ego_velocity"], 0)
        special_case = row_special_case(evidence, index, event_row)

        evidence.at[index, "IsMinSpdsignArea"] = int(active)
        evidence.at[index, "Thres_MinSpdlim"] = limit if active else -1
        if active:
            trigger_col, com_col = ARTICLE_COLUMNS["78_4"]
            evidence.at[index, trigger_col] = 1
            evidence.at[index, com_col] = compliance_value(speed, limit, special_case)

    changed_rows = int((old != evidence[compare_cols]).any(axis=1).sum())
    write_csv_resilient(evidence, evidence_path)
    violation_rows = int((evidence[[com for _, com in ARTICLE_COLUMNS.values()]] < 0).any(axis=1).sum())
    return changed_rows, violation_rows


def summarize_com(values: pd.Series) -> int:
    numeric = pd.to_numeric(values, errors="coerce").fillna(0)
    if (numeric < 0).any():
        return -1
    if (numeric > 0).any():
        return 1
    return 0


def mode_ignore_missing(values: pd.Series, missing_value: int) -> int:
    numeric = pd.to_numeric(values, errors="coerce")
    numeric = numeric[np.isfinite(numeric) & (numeric != missing_value)]
    if numeric.empty:
        return missing_value
    return int(round(numeric.astype(int).mode().iloc[0]))


def update_events_summary(events_path: Path) -> tuple[int, int]:
    events = pd.read_csv(events_path)
    if "Special_case" not in events.columns:
        events["Special_case"] = 0
    for trigger_col, com_col in ARTICLE_COLUMNS.values():
        if trigger_col not in events.columns:
            events[trigger_col] = 0
        if com_col not in events.columns:
            events[com_col] = 0
    for column in ("LanePos", "LaneNumSameDirection", "Thres_MinSpdlim", "Ego_speed_min_kph", "violated_article"):
        if column not in events.columns:
            events[column] = 0

    compare_cols = [
        "violated_article",
        "Thres_MinSpdlim",
        "LaneNumSameDirection",
        "LanePos",
        "Congestion",
        "Special_case",
        "Ego_speed_min_kph",
    ]
    compare_cols += [ARTICLE_COLUMNS[key][0] for key in ARTICLE_COLUMNS]
    compare_cols += [ARTICLE_COLUMNS[key][1] for key in ARTICLE_COLUMNS]
    for column in compare_cols:
        if column not in events.columns:
            events[column] = 0
    old = events[compare_cols].copy()

    for index, event_row in events.iterrows():
        event_num = int(event_row["event_num"])
        evidence_path = events_path.parent / f"MinSpdlim_event_{event_num}_EvidenceChain.csv"
        if not evidence_path.exists():
            continue
        evidence = ensure_evidence_columns(pd.read_csv(evidence_path))
        for key, (trigger_col, com_col) in ARTICLE_COLUMNS.items():
            events.at[index, trigger_col] = int((pd.to_numeric(evidence[trigger_col], errors="coerce").fillna(0) != 0).any())
            events.at[index, com_col] = summarize_com(evidence[com_col])

        events.at[index, "violated_article"] = "78.4" if events.at[index, "com_IMR_78_4"] == -1 else "Com-78.4"
        events.at[index, "Thres_MinSpdlim"] = mode_ignore_missing(evidence["Thres_MinSpdlim"], -1)
        events.at[index, "LaneNumSameDirection"] = mode_ignore_missing(evidence["LaneNumSameDirection"], 0)
        events.at[index, "LanePos"] = mode_ignore_missing(evidence["EgoLaneIndex"], 0)
        events.at[index, "Congestion"] = mode_ignore_missing(evidence["Congestion"], 0)
        events.at[index, "Special_case"] = mode_ignore_missing(evidence["Special_case"], 0)
        events.at[index, "Ego_speed_min_kph"] = float(pd.to_numeric(evidence["Ego_velocity"], errors="coerce").min() * 3.6)

    changed_rows = int((old != events[compare_cols]).any(axis=1).sum())
    write_csv_resilient(events, events_path)
    violation_events = int((events[[com for _, com in ARTICLE_COLUMNS.values()]] < 0).any(axis=1).sum())
    return changed_rows, violation_events


def regenerate_json(data_root: Path, dates: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from s7_event_json import generate_category

    written = generate_category(data_root, "min_speed", dates=dates, location="", event_root=EVENT_ROOT_NAME)
    return len(written)


def main() -> None:
    rows = []
    dates: set[str] = set()
    for events_path in sorted(DATA_ROOT.glob(f"*\\{EVENT_ROOT_NAME}\\*_CSV\\MinSpdlim_events.csv")):
        date = events_path.parts[-4]
        dates.add(date)
        events = pd.read_csv(events_path).set_index("event_num")
        map_fixed = 0
        changed_rows = 0
        violation_rows = 0
        failures = []
        for event_num, event_row in events.iterrows():
            event_num = int(event_num)
            map_path = events_path.parent / f"MinSpdlim_event_{event_num}_MapInfo_correct_scheme1.csv"
            evidence_path = events_path.parent / f"MinSpdlim_event_{event_num}_EvidenceChain.csv"
            try:
                if fix_map_columns(map_path):
                    map_fixed += 1
                changed, violations = correct_evidence(evidence_path, map_path, event_row)
                changed_rows += changed
                violation_rows += violations
            except Exception as exc:
                failures.append(f"{event_num}: {type(exc).__name__}: {exc}")
        summary_changed, violation_events = update_events_summary(events_path)
        rows.append(
            {
                "date": date,
                "segment": events_path.parent.name,
                "events": len(events),
                "map_fixed": map_fixed,
                "changed_rows": changed_rows,
                "violation_rows": violation_rows,
                "summary_changed": summary_changed,
                "violation_events": violation_events,
                "failures": " || ".join(failures),
            }
        )

    if rows:
        summary = pd.DataFrame(rows)
        summary_path = Path(__file__).with_name("S22_nanjing_minspdlim_sign_summary.csv")
        summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
        print(summary.to_string(index=False))
        print(f"SUMMARY {summary_path}")
        failed = summary[summary["failures"].astype(str) != ""]
        if not failed.empty:
            raise SystemExit(f"failed segments: {len(failed)}")

    json_count = regenerate_json(DATA_ROOT, sorted(dates)) if dates else 0
    print(f"json_written={json_count}")


if __name__ == "__main__":
    main()
