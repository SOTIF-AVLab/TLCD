from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


DATA_ROOT = Path(r"Z:\HongqiData\Nanjing")
DATE = "20240910"
EVENT_ROOT_NAME = "zEvent_MaxSpdlim"

EXPRESS_ROAD_TYPES = {1, 2, 34}
EXPRESS_LANE_TYPES = {1, 8}
ARTICLE_COLUMNS = {
    "45_1": ("trigger_IMR_45_1", "com_IMR_45_1"),
    "46_3": ("trigger_IMR_46_3", "com_IMR_46_3"),
    "46_4": ("trigger_IMR_46_4", "com_IMR_46_4"),
    "46_5": ("trigger_IMR_46_5", "com_IMR_46_5"),
    "78_1": ("trigger_IMR_78_1", "com_IMR_78_1"),
    "78_3": ("trigger_IMR_78_3", "com_IMR_78_3"),
}


def positive_limits(row: pd.Series) -> list[float]:
    lane_num = int(row["LaneNumSameDirection"])
    values = []
    for lane in range(1, min(lane_num, 5) + 1):
        value = float(row[f"LaneMaxSpdlim_{lane}"])
        if value > 0:
            values.append(value)
    return values


def ego_lane_limit(row: pd.Series) -> float:
    ego_lane = int(row["EgoLaneIndex"])
    if 1 <= ego_lane <= 5:
        value = float(row[f"LaneMaxSpdlim_{ego_lane}"])
        if value > 0:
            return value
    limits = positive_limits(row)
    return min(limits) if limits else 0.0


def compliance_value(speed_mps: float, limit_kph: float) -> int:
    if limit_kph <= 0 or not np.isfinite(limit_kph):
        return 0
    return -1 if speed_mps * 3.6 > limit_kph else 1


def reset_article_columns(evidence: pd.DataFrame) -> None:
    for trigger_col, com_col in ARTICLE_COLUMNS.values():
        evidence[trigger_col] = 0
        evidence[com_col] = 0


def update_row(evidence: pd.DataFrame, index: int, article_key: str, com_value: int) -> None:
    trigger_col, com_col = ARTICLE_COLUMNS[article_key]
    evidence.at[index, trigger_col] = 1
    evidence.at[index, com_col] = com_value


def correct_evidence(evidence_path: Path, map_path: Path) -> tuple[int, int]:
    evidence = pd.read_csv(evidence_path)
    map_info = pd.read_csv(map_path)
    if len(evidence) != len(map_info):
        raise ValueError(f"row count mismatch: {evidence_path} evidence={len(evidence)} map={len(map_info)}")

    original_46 = {
        key: pd.to_numeric(evidence[ARTICLE_COLUMNS[key][0]], errors="coerce").fillna(0).to_numpy() != 0
        for key in ("46_3", "46_4", "46_5")
    }
    reset_article_columns(evidence)
    changed_rows = 0

    for i, map_row in map_info.iterrows():
        road_type = int(map_row["Road_type"])
        lane_type = int(map_row["Lane_type_CurrentLane"])
        limits = positive_limits(map_row)
        all_valid_limits_are_120 = bool(limits) and all(limit == 120 for limit in limits)
        limit = ego_lane_limit(map_row)
        com = compliance_value(float(evidence.at[i, "Ego_velocity"]), limit)

        evidence.at[i, "Road_type"] = road_type
        evidence.at[i, "Lane_type"] = lane_type
        evidence.at[i, "Thres_MaxSpdlim"] = int(round(limit)) if limit > 0 else 0

        if road_type in EXPRESS_ROAD_TYPES and lane_type in EXPRESS_LANE_TYPES:
            if all_valid_limits_are_120:
                evidence.at[i, "IsMaxSpdsignArea"] = 0
                update_row(evidence, i, "78_1", com)
            else:
                evidence.at[i, "IsMaxSpdsignArea"] = 1
                update_row(evidence, i, "78_3", com)
        else:
            kept_46 = False
            for article_key in ("46_3", "46_4", "46_5"):
                if original_46[article_key][i]:
                    update_row(evidence, i, article_key, com)
                    kept_46 = True
            if kept_46:
                evidence.at[i, "IsMaxSpdsignArea"] = 0
            else:
                evidence.at[i, "IsMaxSpdsignArea"] = 1
                update_row(evidence, i, "45_1", com)

    old = pd.read_csv(evidence_path)
    compare_cols = list(ARTICLE_COLUMNS[col][0] for col in ARTICLE_COLUMNS)
    compare_cols += list(ARTICLE_COLUMNS[col][1] for col in ARTICLE_COLUMNS)
    compare_cols += ["Road_type", "Lane_type", "IsMaxSpdsignArea", "Thres_MaxSpdlim"]
    changed_rows = int((old[compare_cols] != evidence[compare_cols]).any(axis=1).sum())
    write_csv_atomic(evidence, evidence_path)
    violation_rows = int((evidence[[ARTICLE_COLUMNS[key][1] for key in ARTICLE_COLUMNS]] < 0).any(axis=1).sum())
    return changed_rows, violation_rows


def write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8-sig",
        newline="",
        suffix=".tmp",
        delete=False,
        dir=path.parent,
    ) as handle:
        temp_path = Path(handle.name)
        df.to_csv(handle, index=False)
    try:
        temp_path.replace(path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def event_number_from_evidence(path: Path) -> int:
    return int(path.name.split("_")[2])


def regenerate_json(data_root: Path, date: str) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from s7_event_json import generate_category

    written = generate_category(data_root, "max_speed", dates=[date], location="")
    return len(written)


def main() -> None:
    parser = argparse.ArgumentParser(description="Correct MaxSpdlim EvidenceChain and JSON from scheme1 MapInfo.")
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--date", default=DATE)
    parser.add_argument("--skip-json", action="store_true")
    args = parser.parse_args()

    event_root = args.data_root / args.date / EVENT_ROOT_NAME
    total_events = 0
    total_changed_rows = 0
    total_violation_rows = 0
    failures = []
    for evidence_path in sorted(event_root.glob("*_CSV/MaxSpdlim_event_*_EvidenceChain.csv")):
        event_num = event_number_from_evidence(evidence_path)
        map_path = evidence_path.with_name(f"MaxSpdlim_event_{event_num}_MapInfo_correct_scheme1.csv")
        if not map_path.exists():
            raise FileNotFoundError(map_path)
        try:
            changed_rows, violation_rows = correct_evidence(evidence_path, map_path)
        except PermissionError as exc:
            failures.append((evidence_path, str(exc)))
            print(f"{evidence_path.parent.name} event {event_num}: FAILED PermissionError")
            continue
        total_events += 1
        total_changed_rows += changed_rows
        total_violation_rows += violation_rows
        print(f"{evidence_path.parent.name} event {event_num}: changed_rows={changed_rows}, violation_rows={violation_rows}")


    if failures:
        for path, error in failures:
            print(f"FAILED {path}: {error}")
        raise SystemExit(f"failed to update {len(failures)} EvidenceChain files")
    json_count = 0 if args.skip_json else regenerate_json(args.data_root, args.date)
    print(f"events={total_events}")
    print(f"changed_rows={total_changed_rows}")
    print(f"violation_rows={total_violation_rows}")
    if not args.skip_json:
        print(f"json_written={json_count}")


if __name__ == "__main__":
    main()
