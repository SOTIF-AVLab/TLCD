from __future__ import annotations

import argparse
import re
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


EVENT_ROOT = Path(r"Z:\HongqiData\Nanjing\20240910\zEvent_MaxSpdlim")
CSV_ALL_ROOT = Path(r"Z:\HongqiData\Nanjing\20240910\csv_all")
CSV_SELECTED_ROOT = Path(r"Z:\HongqiData\Nanjing\20240910\csv_selected")

LINE_FILE = "select_VH_1_IDT_Sf_MapLocSrv_Line_struct.csv"
RECOMMENDED_FILE = "VH_1_IDT_Sf_MapLocSrv_RecommendedLane_struct.csv"
TRAVEL_SPEED_FILE = "VH_1_IDT_Sf_MapLocSrv_TravelSpeed_struct.csv"

LANE_SPEED_SUFFIXES = {
    "left2": "Sf_EHRTravelSpeedLeft2CurValue_uint32",
    "left1": "Sf_EHRTravelSpeedLeft1CurValue_uint32",
    "current": "Sf_EHRTravelSpeedCurrentCurValue_uint32",
    "right1": "Sf_EHRTravelSpeedRight1CurValue_uint32",
    "right2": "Sf_EHRTravelSpeedRight2CurValue_uint32",
}
RECOMMENDED_LANE_SUFFIX = "Sf_EHRRecommendedLaneCurrentLaneSequence_enum"
RECOMMENDED_LANE_NUM_SUFFIX = "Sf_EHRRecommendedLaneCurrentLaneSequenceNum_uint32"
TARGET_COLUMNS = [
    "EgoLaneIndex",
    "LaneNumSameDirection",
    "LaneMaxSpdlim_1",
    "LaneMaxSpdlim_2",
    "LaneMaxSpdlim_3",
    "LaneMaxSpdlim_4",
    "LaneMaxSpdlim_5",
]


def find_column(columns: pd.Index, suffix: str) -> str:
    matches = [column for column in columns if column.endswith(suffix)]
    if not matches:
        raise KeyError(f"Missing required column suffix: {suffix}")
    return matches[0]


def read_csv_columns(path: Path, suffixes: list[str]) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0).columns
    usecols = ["CommomPackage.sendTime"]
    usecols.extend(find_column(header, suffix) for suffix in suffixes)
    return pd.read_csv(path, usecols=usecols)


def parse_enum_value(value) -> float:
    if pd.isna(value):
        return np.nan
    match = re.search(r"\((-?\d+)\)", str(value))
    if match:
        return float(match.group(1))
    try:
        return float(value)
    except ValueError:
        return np.nan


def nearest_by_send_time(target_send_time: pd.Series, source: pd.DataFrame) -> pd.DataFrame:
    target = pd.DataFrame({"CommomPackage.sendTime": target_send_time.astype(np.int64)})
    target = target.sort_values("CommomPackage.sendTime").reset_index()
    source = source.sort_values("CommomPackage.sendTime")
    merged = pd.merge_asof(target, source, on="CommomPackage.sendTime", direction="nearest")
    return merged.sort_values("index").drop(columns=["index"]).reset_index(drop=True)


def resize_to_length(values: np.ndarray, length: int) -> np.ndarray:
    if len(values) == length:
        return values
    if len(values) == 0:
        return np.full(length, np.nan)
    indices = np.rint(np.linspace(0, len(values) - 1, length)).astype(int)
    return values[indices]


def clamp_lane_index(values: np.ndarray, lane_count: np.ndarray) -> np.ndarray:
    result = np.rint(values).astype(float)
    for i in range(len(result)):
        if np.isnan(result[i]) or np.isnan(lane_count[i]) or lane_count[i] < 1:
            continue
        result[i] = min(max(result[i], 1), min(lane_count[i], 5))
    return result


def infer_scheme2_ego(left2: np.ndarray, left1: np.ndarray, right1: np.ndarray, right2: np.ndarray, lane_count: np.ndarray) -> np.ndarray:
    ego = np.full(len(lane_count), np.nan)
    for i in range(len(lane_count)):
        n = lane_count[i]
        if np.isnan(n) or n < 1:
            continue
        left_count = int(left2[i] > 0) + int(left1[i] > 0)
        right_count = int(right1[i] > 0) + int(right2[i] > 0)
        if left_count == 0:
            ego[i] = 1
        elif left_count == 1:
            ego[i] = 2
        elif right_count < 2:
            ego[i] = n - right_count
        else:
            ego[i] = 3
    return clamp_lane_index(ego, lane_count)


def mode_int(values: np.ndarray) -> float:
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return np.nan
    counts = pd.Series(np.rint(values).astype(int)).value_counts()
    return float(counts.index[0])


def detect_lane_changes(map_info: pd.DataFrame) -> list[tuple[int, str]]:
    if "MAP_C0_Left1" not in map_info.columns or "MAP_C0_Right1" not in map_info.columns:
        return []
    left_c0 = pd.to_numeric(map_info["MAP_C0_Left1"], errors="coerce").to_numpy()
    right_c0 = pd.to_numeric(map_info["MAP_C0_Right1"], errors="coerce").to_numpy()
    changes: list[tuple[int, str]] = []
    for i in range(1, len(map_info)):
        left_change = abs(left_c0[i - 1]) < 0.75 and abs(right_c0[i]) < 0.75 and left_c0[i] > 2
        right_change = abs(right_c0[i - 1]) < 0.75 and abs(left_c0[i]) < 0.75 and right_c0[i] < -2
        if left_change:
            changes.append((i, "left"))
        elif right_change:
            changes.append((i, "right"))
    return changes


def apply_lane_change_consistency(ego: np.ndarray, lane_count: np.ndarray, map_info: pd.DataFrame) -> np.ndarray:
    result = ego.copy()
    changes = detect_lane_changes(map_info)
    if not changes:
        return result

    boundaries = [0] + [idx for idx, _ in changes] + [len(result)]
    for change_number, (idx, direction) in enumerate(changes, start=1):
        before_slice = slice(boundaries[change_number - 1], idx)
        after_slice = slice(idx, boundaries[change_number + 1])
        before = mode_int(result[before_slice])
        after = mode_int(result[after_slice])
        if np.isnan(before) or np.isnan(after):
            continue
        if direction == "left":
            corrected_before = min(mode_int(lane_count[before_slice]), after + 1)
            if before <= after and not np.isnan(corrected_before):
                result[before_slice] = corrected_before
            elif after != before - 1:
                result[after_slice] = before - 1
        else:
            corrected_after = min(mode_int(lane_count[after_slice]), before + 1)
            if before >= after and not np.isnan(corrected_after):
                result[after_slice] = corrected_after
            elif after != before + 1:
                result[after_slice] = before + 1
    return clamp_lane_index(result, lane_count)


def map_speed_limits(ego: np.ndarray, lane_count: np.ndarray, speeds: dict[str, np.ndarray]) -> np.ndarray:
    result = np.zeros((len(ego), 5), dtype=float)
    offsets = {
        "left2": -2,
        "left1": -1,
        "current": 0,
        "right1": 1,
        "right2": 2,
    }
    for i in range(len(ego)):
        if np.isnan(ego[i]) or np.isnan(lane_count[i]):
            continue
        k = int(round(ego[i]))
        n = min(int(round(lane_count[i])), 5)
        for name, offset in offsets.items():
            lane_index = k + offset
            value = speeds[name][i]
            if 1 <= lane_index <= n and value > 0:
                result[i, lane_index - 1] = value
        if k in (4, 5) and speeds["left2"][i] > 0:
            leftmost_covered = k - 2
            for lane_index in range(1, min(leftmost_covered, n + 1)):
                result[i, lane_index - 1] = speeds["left2"][i]
        if n < 5:
            result[i, n:5] = 0
    return result


def update_map_info(map_info: pd.DataFrame, lane_count: np.ndarray, ego: np.ndarray, speeds: dict[str, np.ndarray]) -> pd.DataFrame:
    corrected = map_info.copy()
    lane_count = np.minimum(np.maximum(lane_count, 1), 5)
    lane_speed = map_speed_limits(ego, lane_count, speeds)
    corrected["EgoLaneIndex"] = np.rint(ego).astype(int)
    corrected["LaneNumSameDirection"] = np.rint(lane_count).astype(int)
    for lane in range(5):
        corrected[f"LaneMaxSpdlim_{lane + 1}"] = np.rint(lane_speed[:, lane]).astype(int)
    return corrected


def event_send_time(line_df: pd.DataFrame, event_row: pd.Series) -> pd.Series:
    receive_time = pd.to_numeric(line_df["CommomPackage.receiveTime"], errors="coerce")
    mask = receive_time.between(int(event_row["t_start"]), int(event_row["t_end"]), inclusive="both")
    window = line_df.loc[mask, "CommomPackage.sendTime"]
    if window.empty:
        valid = receive_time.dropna()
        if valid.empty:
            raise ValueError("Line receiveTime is empty")
        start_pos = int((valid - int(event_row["t_start"])).abs().idxmin())
        end_pos = int((valid - int(event_row["t_end"])).abs().idxmin())
        if start_pos > end_pos:
            start_pos, end_pos = end_pos, start_pos
        window = line_df.loc[start_pos:end_pos, "CommomPackage.sendTime"]
    return pd.to_numeric(window, errors="coerce").dropna().astype(np.int64).reset_index(drop=True)


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


def calibrate_event(event_dir: Path, event_row: pd.Series, line_df: pd.DataFrame, recommended: pd.DataFrame, travel_speed: pd.DataFrame) -> tuple[Path, Path]:
    event_num = int(event_row["event_num"])
    map_path = event_dir / f"MaxSpdlim_event_{event_num}_MapInfo.csv"
    if not map_path.exists():
        raise FileNotFoundError(map_path)

    map_info = pd.read_csv(map_path)
    send_time = event_send_time(line_df, event_row)
    rec_window = nearest_by_send_time(send_time, recommended)
    speed_window = nearest_by_send_time(send_time, travel_speed)

    length = len(map_info)
    rec_ego_col = find_column(rec_window.columns, RECOMMENDED_LANE_SUFFIX)
    rec_lane_num_col = find_column(rec_window.columns, RECOMMENDED_LANE_NUM_SUFFIX)
    lane_count = resize_to_length(pd.to_numeric(rec_window[rec_lane_num_col], errors="coerce").to_numpy(dtype=float), length)
    lane_count = np.minimum(np.maximum(lane_count, 1), 5)

    speeds = {}
    for name, suffix in LANE_SPEED_SUFFIXES.items():
        column = find_column(speed_window.columns, suffix)
        speeds[name] = resize_to_length(pd.to_numeric(speed_window[column], errors="coerce").to_numpy(dtype=float), length)

    scheme1_ego = parse_enum_series(rec_window[rec_ego_col])
    scheme1_ego = resize_to_length(scheme1_ego + 1, length)
    scheme1_ego = clamp_lane_index(scheme1_ego, lane_count)
    scheme1_ego = apply_lane_change_consistency(scheme1_ego, lane_count, map_info)

    scheme2_ego = infer_scheme2_ego(speeds["left2"], speeds["left1"], speeds["right1"], speeds["right2"], lane_count)
    scheme2_ego = apply_lane_change_consistency(scheme2_ego, lane_count, map_info)

    output1 = map_path.with_name(f"MaxSpdlim_event_{event_num}_MapInfo_correct_scheme1.csv")
    output2 = map_path.with_name(f"MaxSpdlim_event_{event_num}_MapInfo_correct_scheme2.csv")
    write_csv_atomic(update_map_info(map_info, lane_count, scheme1_ego, speeds), output1)
    write_csv_atomic(update_map_info(map_info, lane_count, scheme2_ego, speeds), output2)
    return output1, output2


def parse_enum_series(series: pd.Series) -> np.ndarray:
    return series.map(parse_enum_value).to_numpy(dtype=float)


def calibrate_segment(event_dir: Path, csv_all_root: Path, csv_selected_root: Path) -> list[tuple[Path, Path]]:
    segment = event_dir.name
    events_path = event_dir / "MaxSpdlim_events.csv"
    line_path = csv_selected_root / segment / "CSV" / LINE_FILE
    recommended_path = csv_all_root / segment / RECOMMENDED_FILE
    travel_speed_path = csv_all_root / segment / TRAVEL_SPEED_FILE

    events = pd.read_csv(events_path)
    line_df = pd.read_csv(line_path, usecols=["CommomPackage.sendTime", "CommomPackage.receiveTime"])
    recommended = read_csv_columns(recommended_path, [RECOMMENDED_LANE_SUFFIX, RECOMMENDED_LANE_NUM_SUFFIX])
    travel_speed = read_csv_columns(travel_speed_path, list(LANE_SPEED_SUFFIXES.values()))

    outputs = []
    for _, event_row in events.iterrows():
        outputs.append(calibrate_event(event_dir, event_row, line_df, recommended, travel_speed))
    return outputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Correct MaxSpdlim MapInfo lane fields with map raw signals.")
    parser.add_argument("--event-root", type=Path, default=EVENT_ROOT)
    parser.add_argument("--csv-all-root", type=Path, default=CSV_ALL_ROOT)
    parser.add_argument("--csv-selected-root", type=Path, default=CSV_SELECTED_ROOT)
    args = parser.parse_args()

    all_outputs = []
    for events_path in sorted(args.event_root.glob("*_CSV/MaxSpdlim_events.csv")):
        event_dir = events_path.parent
        outputs = calibrate_segment(event_dir, args.csv_all_root, args.csv_selected_root)
        all_outputs.extend(outputs)
        print(f"{event_dir.name}: wrote {len(outputs) * 2} files")
    print(f"Done. Corrected outputs: {len(all_outputs) * 2}")


if __name__ == "__main__":
    main()
