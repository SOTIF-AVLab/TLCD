from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from S15_correct_maxspdlim_mapinfo import detect_lane_changes, write_csv_atomic


DATA_ROOT = Path(r"Z:\HongqiData\Nanjing")
EVENT_ROOT_NAME = "zEvent_MaxSpdlim_sign_2"
EXPRESS_ROAD_TYPES = {1, 2, 34}
EXPRESS_LANE_TYPES = {1, 8}
ARTICLE_COLUMNS = {
    "45_1": ("trigger_IMR_45_1", "com_IMR_45_1"),
    "78_1": ("trigger_IMR_78_1", "com_IMR_78_1"),
    "78_3": ("trigger_IMR_78_3", "com_IMR_78_3"),
}
ALL_ARTICLE_COLUMNS = {
    "45_1": ("trigger_IMR_45_1", "com_IMR_45_1"),
    "46_3": ("trigger_IMR_46_3", "com_IMR_46_3"),
    "46_4": ("trigger_IMR_46_4", "com_IMR_46_4"),
    "46_5": ("trigger_IMR_46_5", "com_IMR_46_5"),
    "78_1": ("trigger_IMR_78_1", "com_IMR_78_1"),
    "78_3": ("trigger_IMR_78_3", "com_IMR_78_3"),
}


def read_events(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeDecodeError:
            pass
    raise UnicodeDecodeError("csv", b"", 0, 1, f"cannot decode {path}")


def write_csv_safe(df: pd.DataFrame, path: Path) -> Path:
    try:
        write_csv_atomic(df, path)
        return path
    except PermissionError:
        try:
            df.to_csv(path, index=False, encoding="utf-8-sig")
            return path
        except PermissionError:
            pending = path.with_name(f"{path.stem}_pending_overwrite{path.suffix}")
            df.to_csv(pending, index=False, encoding="utf-8-sig")
            return pending


def clean_text(value: object) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def lane_values_from_description(description: str) -> list[int]:
    match = re.search(r"\d{2,3}(?:\s*/\s*\d{2,3})+", description)
    if match:
        return [int(value) for value in re.findall(r"\d{2,3}", match.group(0))]
    match = re.search(r"\d{2,3}(?:\s+\d{2,3})+", description)
    if match:
        return [int(value) for value in re.findall(r"\d{2,3}", match.group(0))]
    return []


def speed_values_from_description(description: str) -> list[int]:
    return [int(value) for value in re.findall(r"(?<!\d)(\d{2,3})(?!\d)", description)]


def classify_speed_sign(event_row: pd.Series) -> tuple[str, list[int], int | None]:
    description = clean_text(event_row.get("Event_description", ""))
    data_issue = clean_text(event_row.get("Data_issue", ""))
    lane_values = lane_values_from_description(description)
    lane_values = [value for value in lane_values if value >= 80]
    if len(lane_values) > 1:
        return "lane_values", lane_values, None

    values = speed_values_from_description(description)
    high_values = [value for value in values if value >= 80]
    if high_values:
        return "single_value", [high_values[0]], None

    low_values = [value for value in values if value < 80]
    text = f"{description} {data_issue}"
    if low_values and "匝道" in text:
        return "ramp_low_value", [low_values[0]], None
    if low_values:
        return "low_value_unchanged", low_values, low_values[0]
    return "no_value_unchanged", [], None


def ego_lane_from_initial(map_info: pd.DataFrame, initial_lane: int) -> np.ndarray:
    lane = int(np.clip(initial_lane, 1, 5))
    result = np.full(len(map_info), lane, dtype=float)
    for index, direction in detect_lane_changes(map_info):
        if direction == "left":
            lane -= 1
        else:
            lane += 1
        lane = int(np.clip(lane, 1, 5))
        result[index:] = lane
    return result


def apply_speed_rule(map_info: pd.DataFrame, event_row: pd.Series) -> tuple[pd.DataFrame, str, bool]:
    corrected = map_info.copy()
    lane_count = int(np.clip(int(event_row["mainLaneNum"]), 1, 5))
    corrected["LaneNumSameDirection"] = lane_count
    corrected["EgoLaneIndex"] = np.rint(
        ego_lane_from_initial(corrected, int(event_row["iniEgoLaneindex"]))
    ).astype(int)

    rule, values, _ = classify_speed_sign(event_row)
    sign_effective = rule not in {"low_value_unchanged", "no_value_unchanged"}
    if rule == "lane_values":
        for lane in range(1, min(len(values), 5) + 1):
            corrected[f"LaneMaxSpdlim_{lane}"] = values[lane - 1]
    elif rule == "single_value":
        value = values[0]
        for lane in range(1, 6):
            col = f"LaneMaxSpdlim_{lane}"
            mask = pd.to_numeric(corrected[col], errors="coerce").fillna(0) > 0
            corrected.loc[mask, col] = value
    elif rule == "ramp_low_value":
        value = values[0]
        corrected["LaneMaxSpdlim_1"] = value
        corrected["LaneMaxSpdlim_2"] = value
        for lane in range(3, 6):
            corrected[f"LaneMaxSpdlim_{lane}"] = 0
    return corrected, rule, sign_effective


def positive_limits(row: pd.Series) -> list[float]:
    values = []
    for lane in range(1, 6):
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


def article_for_row(map_row: pd.Series, sign_effective: bool, rule: str) -> tuple[str, int]:
    limits = positive_limits(map_row)
    all_valid_limits_are_120 = bool(limits) and all(int(round(limit)) == 120 for limit in limits)
    if not sign_effective and rule in {"low_value_unchanged", "no_value_unchanged"} and all_valid_limits_are_120:
        return "78_1", 0
    road_type = int(map_row["Road_type"])
    lane_type = int(map_row["Lane_type_CurrentLane"])
    if road_type in EXPRESS_ROAD_TYPES and lane_type in EXPRESS_LANE_TYPES:
        return "78_3", 1
    return "45_1", 1


def build_evidence(event_dir: Path, event_row: pd.Series, map_info: pd.DataFrame, rule: str, sign_effective: bool) -> Path:
    event_num = int(event_row["event_num"])
    ego_path = event_dir / f"MaxSpdlim_event_{event_num}_EgoInfo.csv"
    if not ego_path.exists():
        raise FileNotFoundError(ego_path)
    ego_info = pd.read_csv(ego_path)
    if len(ego_info) != len(map_info):
        raise ValueError(f"row count mismatch: {ego_path} ego={len(ego_info)} map={len(map_info)}")

    evidence = pd.DataFrame()
    evidence["event_time"] = ego_info["event_time"]
    for trigger_col, com_col in ALL_ARTICLE_COLUMNS.values():
        evidence[trigger_col] = 0
        evidence[com_col] = 0
    evidence["Ego_velocity"] = ego_info["Ego_velocity"]
    evidence["Road_type"] = pd.to_numeric(map_info["Road_type"], errors="coerce").fillna(0).astype(int)
    evidence["Lane_type"] = pd.to_numeric(map_info["Lane_type_CurrentLane"], errors="coerce").fillna(0).astype(int)
    evidence["IsMaxSpdsignArea"] = 0
    evidence["Thres_MaxSpdlim"] = 0
    evidence["LaneNumSameDirection"] = pd.to_numeric(map_info["LaneNumSameDirection"], errors="coerce").fillna(0).astype(int)
    evidence["EgoLaneIndex"] = pd.to_numeric(map_info["EgoLaneIndex"], errors="coerce").fillna(0).astype(int)
    for lane in range(1, 6):
        evidence[f"LaneMaxSpdlim_{lane}"] = pd.to_numeric(map_info[f"LaneMaxSpdlim_{lane}"], errors="coerce").fillna(0).astype(int)
    evidence["Event_description"] = clean_text(event_row.get("Event_description", ""))
    evidence["Speed_limit_sign_effective"] = int(sign_effective)
    evidence["Speed_limit_rule"] = rule

    for index, map_row in map_info.iterrows():
        article_key, is_sign_area = article_for_row(map_row, sign_effective, rule)
        limit = ego_lane_limit(map_row)
        speed_kph = float(evidence.at[index, "Ego_velocity"]) * 3.6
        com_value = -1 if limit > 0 and speed_kph > limit else 1 if limit > 0 else 0
        trigger_col, com_col = ARTICLE_COLUMNS[article_key]
        evidence.at[index, "IsMaxSpdsignArea"] = is_sign_area
        evidence.at[index, "Thres_MaxSpdlim"] = int(round(limit)) if limit > 0 else 0
        evidence.at[index, trigger_col] = 1
        evidence.at[index, com_col] = com_value

    output = event_dir / f"MaxSpdlim_event_{event_num}_EvidenceChain.csv"
    return write_csv_safe(evidence, output)


def correct_event(event_dir: Path, event_row: pd.Series) -> tuple[Path, Path]:
    event_num = int(event_row["event_num"])
    map_path = event_dir / f"MaxSpdlim_event_{event_num}_MapInfo_correct_scheme1.csv"
    if not map_path.exists():
        raise FileNotFoundError(map_path)
    map_info = pd.read_csv(map_path)
    corrected_map, rule, sign_effective = apply_speed_rule(map_info, event_row)
    map_output = write_csv_safe(corrected_map, map_path)
    evidence_path = build_evidence(event_dir, event_row, corrected_map, rule, sign_effective)
    return map_output, evidence_path


def regenerate_json(data_root: Path, dates: list[str]) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from s7_event_json import generate_category

    written = generate_category(
        data_root,
        "max_speed",
        dates=dates,
        location="",
        event_root=EVENT_ROOT_NAME,
    )
    return len(written)


def main() -> None:
    rows = []
    failures = []
    dates = []
    for date_dir in sorted(path for path in DATA_ROOT.iterdir() if path.is_dir()):
        event_root = date_dir / EVENT_ROOT_NAME
        events_path = event_root / "MaxSpdlim_events.csv"
        if not events_path.exists():
            continue
        dates.append(date_dir.name)
        events = read_events(events_path)
        valid = pd.to_numeric(events["Event_Validity"], errors="coerce").fillna(0) == 1
        corrected = 0
        pending_maps = []
        for _, event_row in events.loc[valid].iterrows():
            try:
                map_output, _ = correct_event(event_root, event_row)
            except Exception as exc:
                failures.append(f"{date_dir.name} event {int(event_row['event_num'])}: {type(exc).__name__}: {exc}")
                continue
            if map_output.name.endswith("_pending_overwrite.csv"):
                pending_maps.append(str(map_output))
            corrected += 1
        rows.append({
            "date": date_dir.name,
            "valid_events": int(valid.sum()),
            "corrected_events": corrected,
            "pending_map_overwrites": " || ".join(pending_maps),
        })
        print(f"{date_dir.name}: valid={int(valid.sum())}, corrected={corrected}, pending_maps={len(pending_maps)}")

    if failures:
        for failure in failures:
            print(f"FAILED {failure}")
        raise SystemExit(f"failed to correct {len(failures)} events")

    json_written = regenerate_json(DATA_ROOT, dates)
    summary = pd.DataFrame(rows)
    summary["json_written_total"] = json_written
    summary_path = Path(__file__).resolve().parent / "S21_maxspdlim_sign2_summary.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(f"json_written={json_written}")
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
