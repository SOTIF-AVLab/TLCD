from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from S15_correct_maxspdlim_mapinfo import (
    LANE_SPEED_SUFFIXES,
    LINE_FILE,
    RECOMMENDED_FILE,
    RECOMMENDED_LANE_NUM_SUFFIX,
    RECOMMENDED_LANE_SUFFIX,
    TRAVEL_SPEED_FILE,
    apply_lane_change_consistency,
    clamp_lane_index,
    event_send_time,
    find_column,
    nearest_by_send_time,
    parse_enum_series,
    read_csv_columns,
    resize_to_length,
    update_map_info,
    write_csv_atomic,
)


EVENT_ROOT = Path(r"Z:\HongqiData\Changchun\20240827\zEvent_MinSpdlim")
CSV_ALL_ROOT = Path(r"Z:\HongqiData\Changchun\20240827\csv_all")
CSV_SELECTED_ROOT = Path(r"Z:\HongqiData\Changchun\20240827\csv_selected")

MIN_SIGN_POSITIVE = (
    "视频中存在最低限速标志",
    "有最低限速标志牌",
    "右侧可见同时标示最高限速和最低限速的组合标志牌",
    "同时标示最高限速和最低限速",
    "牌面含最高限速与最低限速",
    "车型分行的组合限速信息牌",
    "组合限速信息牌，后续牌面消失",
    "组合限速信息牌通过",
    "组合限速牌",
)
MIN_SIGN_NEGATIVE = (
    "未见",
    "无法清晰确认",
    "未能清晰确认",
    "未见清晰",
    "不合理",
)


def sign_min_speed(lane_index: int, max_speed: float) -> int:
    max_speed = int(round(max_speed)) if np.isfinite(max_speed) else -1
    if max_speed < 60:
        return 0
    if lane_index == 1:
        return {80: 60, 100: 80, 120: 110}.get(max_speed, 0)
    if lane_index == 2:
        return {80: 40, 100: 60, 120: 90}.get(max_speed, 0)
    if lane_index == 3:
        return {60: 0, 80: 40, 100: 60, 120: 80}.get(max_speed, 0)
    if lane_index in (4, 5):
        return {60: 0, 80: 40, 100: 60, 120: 60}.get(max_speed, 0)
    return 0


def lane_rule_min_speed(main_lane_num: int, lane_index: int) -> int:
    if main_lane_num == 2:
        return 100 if lane_index == 1 else 60 if lane_index == 2 else 0
    if main_lane_num >= 3:
        if lane_index == 1:
            return 110
        if lane_index == main_lane_num:
            return 60
        if 1 < lane_index < main_lane_num:
            return 90
    return 0


def event_text(event_row: pd.Series) -> str:
    return " ".join(
        str(event_row.get(column, ""))
        for column in ("Data_issue", "Event_description")
        if pd.notna(event_row.get(column, ""))
    )


def has_min_speed_sign(event_row: pd.Series) -> bool:
    text = event_text(event_row)
    if any(token in text for token in MIN_SIGN_NEGATIVE):
        return False
    return any(token in text for token in MIN_SIGN_POSITIVE)


def min_speed_mode(event_row: pd.Series) -> str:
    text = event_text(event_row)
    if "JCT" in text:
        return "jct"
    if has_min_speed_sign(event_row):
        return "sign"
    return "lane_rule"


def detect_lane_changes_from_map(map_info: pd.DataFrame) -> list[tuple[int, str]]:
    from S15_correct_maxspdlim_mapinfo import detect_lane_changes

    return detect_lane_changes(map_info)


def ego_lane_from_initial(map_info: pd.DataFrame, lane_count: np.ndarray, initial_lane: int) -> np.ndarray:
    max_initial_lane = max(1, int(np.nanmax(lane_count))) if len(lane_count) else 1
    lane = int(np.clip(initial_lane, 1, max_initial_lane))
    result = np.full(len(map_info), lane, dtype=float)
    for index, direction in detect_lane_changes_from_map(map_info):
        lane = lane - 1 if direction == "left" else lane + 1
        max_lane = int(np.clip(round(float(lane_count[index])), 1, 5))
        lane = int(np.clip(lane, 1, max_lane))
        result[index:] = lane
    return clamp_lane_index(result, lane_count)


def update_min_speed_columns(map_info: pd.DataFrame, mode: str, main_lane_num: int) -> pd.DataFrame:
    corrected = map_info.copy()
    main_lane_num = int(np.clip(main_lane_num, 1, 5))
    corrected["mainLaneNum"] = main_lane_num
    for lane in range(1, 6):
        corrected[f"LaneMinSpdlim_{lane}"] = 0

    for lane in range(1, main_lane_num + 1):
        if mode == "jct":
            value = 0
        elif mode == "sign":
            value = 60
        else:
            value = lane_rule_min_speed(main_lane_num, lane)
        corrected[f"LaneMinSpdlim_{lane}"] = value
        if value > 0:
            corrected[f"LaneMaxSpdlim_{lane}"] = 120
    return corrected


def put_ego_lane_at_lane_pos(corrected: pd.DataFrame) -> pd.DataFrame:
    columns = list(corrected.columns)
    ego_values = corrected["EgoLaneIndex"].copy()
    if "LanePos" not in columns:
        return corrected

    lane_pos_index = columns.index("LanePos")
    corrected = corrected.drop(columns=[column for column in ("LanePos", "EgoLaneIndex") if column in corrected.columns])
    lane_pos_index = min(lane_pos_index, len(corrected.columns))
    corrected.insert(lane_pos_index, "EgoLaneIndex", ego_values.astype(int))
    return corrected


def corrected_lane_count(raw_lane_count: np.ndarray, main_lane_num: int) -> np.ndarray:
    lane_count = np.minimum(np.maximum(raw_lane_count, main_lane_num), 5)
    if np.isnan(lane_count).any():
        lane_count = np.where(np.isnan(lane_count), main_lane_num, lane_count)
    return lane_count


def correct_event_map_info(
    event_dir: Path,
    event_row: pd.Series,
    line_df: pd.DataFrame,
    recommended: pd.DataFrame,
    travel_speed: pd.DataFrame,
) -> Path:
    event_num = int(event_row["event_num"])
    map_path = event_dir / f"MinSpdlim_event_{event_num}_MapInfo.csv"
    if not map_path.exists():
        raise FileNotFoundError(map_path)

    map_info = pd.read_csv(map_path)
    send_time = event_send_time(line_df, event_row)
    rec_window = nearest_by_send_time(send_time, recommended)
    speed_window = nearest_by_send_time(send_time, travel_speed)

    length = len(map_info)
    rec_ego_col = find_column(rec_window.columns, RECOMMENDED_LANE_SUFFIX)
    rec_lane_num_col = find_column(rec_window.columns, RECOMMENDED_LANE_NUM_SUFFIX)
    raw_lane_count = resize_to_length(pd.to_numeric(rec_window[rec_lane_num_col], errors="coerce").to_numpy(dtype=float), length)
    main_lane_num = int(np.clip(int(event_row["mainLaneNum"]), 1, 5))
    lane_count = corrected_lane_count(raw_lane_count, main_lane_num)

    speeds = {}
    for name, suffix in LANE_SPEED_SUFFIXES.items():
        column = find_column(speed_window.columns, suffix)
        speeds[name] = resize_to_length(pd.to_numeric(speed_window[column], errors="coerce").to_numpy(dtype=float), length)

    recommended_ego = resize_to_length(parse_enum_series(rec_window[rec_ego_col]) + 1, length)
    recommended_ego = clamp_lane_index(recommended_ego, lane_count)
    recommended_ego = apply_lane_change_consistency(recommended_ego, lane_count, map_info)
    corrected = update_map_info(map_info, lane_count, recommended_ego, speeds)

    initial_ego = int(event_row["iniEgoLaneindex"])
    corrected["EgoLaneIndex"] = np.rint(ego_lane_from_initial(corrected, lane_count, initial_ego)).astype(int)
    corrected["LaneNumSameDirection"] = np.rint(lane_count).astype(int)
    corrected = update_min_speed_columns(corrected, min_speed_mode(event_row), main_lane_num)
    corrected = put_ego_lane_at_lane_pos(corrected)

    output = map_path.with_name(f"MinSpdlim_event_{event_num}_MapInfo_correct_scheme1.csv")
    write_csv_atomic(corrected, output)
    return output


def correct_segment(event_dir: Path, csv_all_root: Path, csv_selected_root: Path) -> list[Path]:
    segment = event_dir.name
    events_path = event_dir / "MinSpdlim_events.csv"
    line_path = csv_selected_root / segment / "CSV" / LINE_FILE
    recommended_path = csv_all_root / segment / RECOMMENDED_FILE
    travel_speed_path = csv_all_root / segment / TRAVEL_SPEED_FILE

    events = pd.read_csv(events_path)
    line_df = pd.read_csv(line_path, usecols=["CommomPackage.sendTime", "CommomPackage.receiveTime"])
    recommended = read_csv_columns(recommended_path, [RECOMMENDED_LANE_SUFFIX, RECOMMENDED_LANE_NUM_SUFFIX])
    travel_speed = read_csv_columns(travel_speed_path, list(LANE_SPEED_SUFFIXES.values()))

    outputs = []
    for _, event_row in events.iterrows():
        outputs.append(correct_event_map_info(event_dir, event_row, line_df, recommended, travel_speed))
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Correct Changchun MinSpdlim MapInfo with scheme1 map raw signals.")
    parser.add_argument("--event-root", type=Path, default=EVENT_ROOT)
    parser.add_argument("--csv-all-root", type=Path, default=CSV_ALL_ROOT)
    parser.add_argument("--csv-selected-root", type=Path, default=CSV_SELECTED_ROOT)
    args = parser.parse_args()

    all_outputs = []
    for events_path in sorted(args.event_root.glob("*_CSV/MinSpdlim_events.csv")):
        outputs = correct_segment(events_path.parent, args.csv_all_root, args.csv_selected_root)
        all_outputs.extend(outputs)
        print(f"{events_path.parent.name}: wrote {len(outputs)} files")
    print(f"Done. Corrected outputs: {len(all_outputs)}")


if __name__ == "__main__":
    main()
