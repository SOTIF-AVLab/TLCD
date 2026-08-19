from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from S15_correct_maxspdlim_mapinfo import write_csv_atomic
from S18_correct_minspdlim_mapinfo import min_speed_mode


DATA_ROOT = Path(r"Z:\HongqiData\Changchun")
DATE = "20240827"
EVENT_ROOT_NAME = "zEvent_MinSpdlim"

ARTICLE_COLUMNS = {
    "78_2": ("trigger_IMR_78_2", "com_IMR_78_2"),
    "78_4": ("trigger_IMR_78_4", "com_IMR_78_4"),
    "78_5": ("trigger_IMR_78_5", "com_IMR_78_5"),
    "78_6": ("trigger_IMR_78_6", "com_IMR_78_6"),
    "78_7": ("trigger_IMR_78_7", "com_IMR_78_7"),
}

ARTICLE_TEXT = {
    "78_2": "78.2",
    "78_4": "78.4",
    "78_5": "78.5",
    "78_6": "78.6",
    "78_7": "78.7",
}


def event_text(event_row: pd.Series) -> str:
    return " ".join(
        str(event_row.get(column, ""))
        for column in ("Data_issue", "Event_description")
        if pd.notna(event_row.get(column, ""))
    )


def event_special_case(event_row: pd.Series) -> int:
    congestion = pd.to_numeric(pd.Series([event_row.get("Congestion", 0)]), errors="coerce").fillna(0).iloc[0]
    text = event_text(event_row)
    if int(congestion) == 1 or "拥堵" in text:
        return 1
    if "施工" in text:
        return 2
    if "上坡减速慢行" in text or "上坡" in text:
        return 3
    if "下坡减速慢行" in text or "下坡" in text:
        return 4
    if "弯道" in text:
        return 5
    return 0


def reset_article_columns(df: pd.DataFrame) -> None:
    for trigger_col, com_col in ARTICLE_COLUMNS.values():
        df[trigger_col] = 0
        df[com_col] = 0


def compliance_value(speed_mps: float, limit_kph: float, special_case: float) -> int:
    if special_case != 0:
        return 1
    if limit_kph < 0 or not np.isfinite(limit_kph):
        return 0
    if limit_kph == 0:
        return 1
    return -1 if speed_mps * 3.6 < limit_kph else 1


def ego_lane_limit(row: pd.Series) -> int:
    lane_index = int(row["EgoLaneIndex"])
    if 1 <= lane_index <= 5:
        return int(round(float(row[f"LaneMinSpdlim_{lane_index}"])))
    return 0


def lane_rule_article(main_lane_num: int, lane_index: int) -> str:
    if main_lane_num == 2 and lane_index == 1:
        return "78_5"
    if main_lane_num == 2 and lane_index == 2:
        return "78_2"
    if main_lane_num >= 3 and lane_index == 1:
        return "78_6"
    if main_lane_num >= 3 and lane_index == main_lane_num:
        return "78_2"
    if main_lane_num >= 3 and 1 < lane_index < main_lane_num:
        return "78_7"
    return ""


def update_row(df: pd.DataFrame, index: int, article_key: str, com_value: int) -> None:
    trigger_col, com_col = ARTICLE_COLUMNS[article_key]
    df.at[index, trigger_col] = 1
    df.at[index, com_col] = com_value


def sync_map_fields(evidence: pd.DataFrame, map_info: pd.DataFrame, index: int) -> None:
    evidence.at[index, "Road_type"] = int(map_info.at[index, "Road_type"])
    evidence.at[index, "Lane_type"] = int(map_info.at[index, "Lane_type_CurrentLane"])
    evidence.at[index, "LaneNumSameDirection"] = int(map_info.at[index, "LaneNumSameDirection"])
    evidence.at[index, "mainLaneNum"] = int(map_info.at[index, "mainLaneNum"])
    evidence.at[index, "EgoLaneIndex"] = int(map_info.at[index, "EgoLaneIndex"])
    for lane in range(1, 6):
        evidence.at[index, f"LaneMinSpdlim_{lane}"] = int(map_info.at[index, f"LaneMinSpdlim_{lane}"])


def ensure_evidence_columns(evidence: pd.DataFrame) -> pd.DataFrame:
    if "Congestion" in evidence.columns:
        evidence = evidence.rename(columns={"Congestion": "Special_case"})
    if "Special_case" not in evidence.columns:
        evidence["Special_case"] = 0
    if "mainLaneNum" not in evidence.columns:
        evidence["mainLaneNum"] = 0
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


def sign_active_start(event_row: pd.Series) -> int:
    start_idx = int(event_row.get("start_idx", 0))
    key_idx = int(event_row.get("key_idx_s", start_idx))
    return max(0, key_idx - start_idx)


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
        "mainLaneNum",
        "EgoLaneIndex",
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
    special_case = event_special_case(event_row)
    evidence["Special_case"] = special_case
    mode = min_speed_mode(event_row)
    active_start = sign_active_start(event_row)

    for index, map_row in map_info.iterrows():
        sync_map_fields(evidence, map_info, index)
        main_lane_num = int(map_row["mainLaneNum"])
        lane_index = int(map_row["EgoLaneIndex"])
        speed = float(pd.to_numeric(pd.Series([evidence.at[index, "Ego_velocity"]]), errors="coerce").fillna(0).iloc[0])
        row_special_case = float(evidence.at[index, "Special_case"])
        limit = ego_lane_limit(map_row)

        if mode == "sign":
            active = index >= active_start
            evidence.at[index, "IsMinSpdsignArea"] = int(active)
            evidence.at[index, "Thres_MinSpdlim"] = limit if active else -1
            if active:
                update_row(evidence, index, "78_4", compliance_value(speed, limit, row_special_case))
            continue

        evidence.at[index, "IsMinSpdsignArea"] = 0
        evidence.at[index, "Thres_MinSpdlim"] = limit
        article_key = lane_rule_article(main_lane_num, lane_index)
        if article_key:
            update_row(evidence, index, article_key, compliance_value(speed, limit, row_special_case))

    changed_rows = int((old != evidence[compare_cols]).any(axis=1).sum())
    write_csv_atomic(evidence, evidence_path)
    violation_rows = int((evidence[[ARTICLE_COLUMNS[key][1] for key in ARTICLE_COLUMNS]] < 0).any(axis=1).sum())
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


def summarize_articles(event_summary: dict[str, int]) -> str:
    violated = [ARTICLE_TEXT[key] for key in ARTICLE_COLUMNS if event_summary[f"com_IMR_{key}"] == -1]
    if violated:
        return ";".join(violated)
    triggered = [ARTICLE_TEXT[key] for key in ARTICLE_COLUMNS if event_summary[f"trigger_IMR_{key}"] != 0]
    return "" if not triggered else "Com-" + ";".join(triggered)


def ensure_event_columns(events: pd.DataFrame) -> pd.DataFrame:
    if "Congestion" in events.columns:
        events = events.rename(columns={"Congestion": "Special_case"})
    if "Special_case" not in events.columns:
        events["Special_case"] = 0
    if "LanePos" not in events.columns:
        events["LanePos"] = 0
    if "LaneNumSameDirection" not in events.columns:
        events["LaneNumSameDirection"] = 0
    if "mainLaneNum" not in events.columns:
        events["mainLaneNum"] = 0
    for trigger_col, com_col in ARTICLE_COLUMNS.values():
        if trigger_col not in events.columns:
            events[trigger_col] = 0
        if com_col not in events.columns:
            events[com_col] = 0
    return events


def update_events_summary(events_path: Path) -> tuple[int, int]:
    events = ensure_event_columns(pd.read_csv(events_path))
    compare_cols = [
        "violated_article",
        "Thres_MinSpdlim",
        "LaneNumSameDirection",
        "mainLaneNum",
        "LanePos",
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
        event_summary: dict[str, int] = {}
        for key, (trigger_col, com_col) in ARTICLE_COLUMNS.items():
            event_summary[trigger_col] = int((pd.to_numeric(evidence[trigger_col], errors="coerce").fillna(0) != 0).any())
            event_summary[com_col] = summarize_com(evidence[com_col])
            events.at[index, trigger_col] = event_summary[trigger_col]
            events.at[index, com_col] = event_summary[com_col]
        events.at[index, "violated_article"] = summarize_articles(event_summary)
        events.at[index, "Thres_MinSpdlim"] = mode_ignore_missing(evidence["Thres_MinSpdlim"], -1)
        events.at[index, "LaneNumSameDirection"] = mode_ignore_missing(evidence["LaneNumSameDirection"], 0)
        events.at[index, "mainLaneNum"] = mode_ignore_missing(evidence["mainLaneNum"], 0)
        events.at[index, "LanePos"] = mode_ignore_missing(evidence["EgoLaneIndex"], 0)
        events.at[index, "Special_case"] = mode_ignore_missing(evidence["Special_case"], 0)
        events.at[index, "Ego_speed_min_kph"] = float(pd.to_numeric(evidence["Ego_velocity"], errors="coerce").min() * 3.6)

    changed_rows = int((old != events[compare_cols]).any(axis=1).sum())
    write_csv_atomic(events, events_path)
    violation_events = int((events[[ARTICLE_COLUMNS[key][1] for key in ARTICLE_COLUMNS]] < 0).any(axis=1).sum())
    return changed_rows, violation_events


def event_number_from_evidence(path: Path) -> int:
    return int(path.name.split("_")[2])


def regenerate_json(data_root: Path, date: str, event_root_name: str = EVENT_ROOT_NAME) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from s7_event_json import generate_category

    written = generate_category(data_root, "min_speed", dates=[date], location="", event_root=event_root_name)
    return len(written)


def main() -> None:
    parser = argparse.ArgumentParser(description="Correct Changchun MinSpdlim EvidenceChain and JSON from scheme1 MapInfo.")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--date", default=DATE)
    parser.add_argument("--event-root-name", default=EVENT_ROOT_NAME)
    parser.add_argument("--skip-json", action="store_true")
    args = parser.parse_args()

    event_root = args.data_root / args.date / args.event_root_name
    total_events = 0
    total_changed_rows = 0
    total_violation_rows = 0
    total_event_summary_rows = 0
    total_violation_events = 0
    failures = []
    for events_path in sorted(event_root.glob("*_CSV/MinSpdlim_events.csv")):
        events = pd.read_csv(events_path).set_index("event_num")
        for evidence_path in sorted(events_path.parent.glob("MinSpdlim_event_*_EvidenceChain.csv")):
            event_num = event_number_from_evidence(evidence_path)
            map_path = evidence_path.with_name(f"MinSpdlim_event_{event_num}_MapInfo_correct_scheme1.csv")
            if not map_path.exists():
                failures.append(f"{evidence_path}: missing scheme1 MapInfo")
                continue
            try:
                changed_rows, violation_rows = correct_evidence(evidence_path, map_path, events.loc[event_num])
            except Exception as exc:
                failures.append(f"{evidence_path}: {type(exc).__name__}: {exc}")
                continue
            total_events += 1
            total_changed_rows += changed_rows
            total_violation_rows += violation_rows
            print(f"{evidence_path.parent.name} event {event_num}: changed_rows={changed_rows}, violation_rows={violation_rows}")
        summary_changed, summary_violations = update_events_summary(events_path)
        total_event_summary_rows += summary_changed
        total_violation_events += summary_violations

    if failures:
        for failure in failures:
            print(f"FAILED {failure}")
        raise SystemExit(f"failed to update {len(failures)} EvidenceChain files")

    json_count = 0 if args.skip_json else regenerate_json(args.data_root, args.date, args.event_root_name)
    print(f"events={total_events}")
    print(f"changed_rows={total_changed_rows}")
    print(f"violation_rows={total_violation_rows}")
    print(f"event_summary_changed_rows={total_event_summary_rows}")
    print(f"violation_events={total_violation_events}")
    if not args.skip_json:
        print(f"json_written={json_count}")


if __name__ == "__main__":
    main()
