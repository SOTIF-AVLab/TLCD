from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


EGO_WIDTH_M = 1.914
CENTERLINE_THRESHOLD_M = 0.375
ROOTS = {
    "Nanjing": Path(os.environ.get("TLCD_NANJING_LATERAL_ROOT", ".")),
    "Changchun": Path(os.environ.get("TLCD_CHANGCHUN_LATERAL_ROOT", ".")),
}
SCRIPT_DIR = Path(__file__).resolve().parent
AUDIT_JSONL = SCRIPT_DIR / "lateraldis_vlm_batch_audit.jsonl"
AUDIT_CSV = SCRIPT_DIR / "lateraldis_vlm_batch_audit.csv"
VLM_KEYS = ("Scenario_description_VLM", "Driving_suggestion_VLM")

ROAD_LABELS = {
    0: "当前道路",
    1: "常规多车道道路",
    9: "JCT道路",
    31: "高速公路入口匝道",
    32: "高速公路出口匝道",
    34: "隧道路段",
    36: "当前道路",
    39: "当前道路",
}
LANE_LABELS = {
    0: "所在车道",
    1: "所在车道",
    2: "减速车道",
    3: "加速车道",
    4: "复合车道",
    10: "路肩车道",
    15: "当前车道",
}


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def mode_int(values: pd.Series, default: int = 0) -> int:
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    if numeric.empty:
        return default
    return int(numeric.mode().iloc[0])


def robust_value(values: np.ndarray, start: bool) -> float:
    count = max(3, min(20, int(np.ceil(len(values) * 0.1))))
    sample = values[:count] if start else values[-count:]
    return float(np.nanmedian(sample))


def compressed_position_states(distance_x: np.ndarray) -> list[int]:
    smoothed = (
        pd.Series(distance_x)
        .rolling(window=min(11, max(3, len(distance_x) // 10 * 2 + 1)), center=True, min_periods=1)
        .median()
        .to_numpy()
    )
    raw_states = np.where(smoothed > 0.75, 1, np.where(smoothed < -0.75, -1, 0))
    states: list[int] = []
    for state in raw_states:
        state = int(state)
        if state and (not states or state != states[-1]):
            states.append(state)
    return states


def classify_motion(distance_x: np.ndarray) -> tuple[str, float, float, float, list[int]]:
    start_x = robust_value(distance_x, start=True)
    end_x = robust_value(distance_x, start=False)
    closest_x = float(distance_x[np.nanargmin(np.abs(distance_x))])
    states = compressed_position_states(distance_x)

    if len(states) >= 3 and any(
        states[index] != states[index + 1] for index in range(len(states) - 1)
    ):
        motion = "position_alternation"
    elif states and states[0] == 1 and states[-1] == -1:
        motion = "ego_passes"
    elif states and states[0] == -1 and states[-1] == 1:
        motion = "target_passes"
    elif start_x < -0.75:
        maximum_x = float(np.nanmax(distance_x))
        if maximum_x > start_x + 1.5:
            motion = (
                "target_approach_fallback"
                if end_x < maximum_x - 1.0
                else "target_approaches"
            )
        elif end_x < start_x - 1.5:
            motion = "ego_pulls_away"
        else:
            motion = "rear_parallel"
    elif start_x > 0.75:
        minimum_x = float(np.nanmin(distance_x))
        if minimum_x < start_x - 1.5:
            motion = (
                "ego_approach_fallback"
                if end_x > minimum_x + 1.0
                else "ego_approaches"
            )
        elif end_x > start_x + 1.5:
            motion = "target_pulls_away"
        else:
            motion = "front_parallel"
    elif end_x > 1.0:
        motion = "target_passes"
    elif end_x < -1.0:
        motion = "ego_passes"
    else:
        motion = "parallel"

    return motion, start_x, closest_x, end_x, states


def find_physical_segments(trace: pd.DataFrame) -> pd.Series:
    delta_t = trace["event_time"].diff().fillna(0)
    delta_x = trace["distance_x"].diff().abs().fillna(0)
    delta_y = trace["distance_y"].diff().abs().fillna(0)
    kinematic_x_limit = np.maximum(6.0, 15.0 * delta_t.to_numpy())
    new_segment = (
        (delta_t > 0.75)
        | (delta_x.to_numpy() > kinematic_x_limit)
        | (delta_y > 2.5)
    )
    return pd.Series(np.cumsum(new_segment), index=trace.index)


def stable_lane_value(values: np.ndarray, start: bool) -> tuple[int | None, float]:
    count = max(5, min(30, int(np.ceil(len(values) * 0.2))))
    sample = values[:count] if start else values[-count:]
    sample = sample[np.isfinite(sample) & (sample != 0)]
    if len(sample) == 0:
        return None, 0.0
    counts = Counter(int(value) for value in sample)
    lane, lane_count = counts.most_common(1)[0]
    return lane, lane_count / len(sample)


def analyze_side(
    side: str,
    evidence: pd.DataFrame,
    objects: pd.DataFrame,
    required_distance: float,
    violation_label: bool,
) -> dict[str, Any] | None:
    distance_column = "Dis_LV" if side == "left" else "Dis_RV"
    valid_mask = pd.to_numeric(evidence[distance_column], errors="coerce").to_numpy() >= 0
    if not valid_mask.any():
        return None

    row_indices = np.flatnonzero(valid_mask)
    valid_evidence = evidence.iloc[row_indices].reset_index(drop=True)
    sign = 1 if side == "left" else -1
    object_numbers = list(range(1, 31))

    def matrix(suffix: str) -> np.ndarray:
        columns = [f"Obj{index:02d}_{suffix}" for index in object_numbers]
        return objects.iloc[row_indices][columns].to_numpy(dtype=float)

    distance_x = matrix("DistanceX")
    distance_y = matrix("DistanceY")
    width = matrix("Width")
    relative_vx = matrix("RelativeVx")
    relative_lane = matrix("RelativeLane")
    track_status = matrix("TrackStatus")
    measured_distance = valid_evidence[distance_column].to_numpy(dtype=float)
    clearance = np.abs(distance_y) - EGO_WIDTH_M / 2 - width / 2

    finite_object = (
        np.isfinite(distance_x)
        & np.isfinite(distance_y)
        & np.isfinite(width)
        & (width > 0)
    )
    raw_error = np.abs(clearance - measured_distance[:, None])
    matching_stages = [
        finite_object
        & (np.abs(distance_x) <= 10)
        & (sign * distance_y > 0)
        & (track_status > 0),
        finite_object
        & (np.abs(distance_x) <= 10)
        & (sign * distance_y > 0),
        finite_object
        & (np.abs(distance_x) <= 15)
        & (sign * distance_y > 0)
        & (track_status > 0),
        finite_object
        & (np.abs(distance_x) <= 15)
        & (sign * distance_y > 0),
        finite_object
        & (np.abs(distance_x) <= 20)
        & (sign * distance_y > 0),
        finite_object & (np.abs(distance_x) <= 20),
    ]
    best_columns = np.full(len(measured_distance), -1, dtype=int)
    best_errors = np.full(len(measured_distance), np.inf, dtype=float)
    selected_stage = np.full(len(measured_distance), -1, dtype=int)
    unresolved = np.ones(len(measured_distance), dtype=bool)
    for stage_number, stage_eligible in enumerate(matching_stages):
        stage_errors = np.where(stage_eligible, raw_error, np.inf)
        stage_columns = np.argmin(stage_errors, axis=1)
        stage_minimum = stage_errors[np.arange(len(stage_columns)), stage_columns]
        accepted = unresolved & np.isfinite(stage_minimum) & (stage_minimum <= 0.02)
        best_columns[accepted] = stage_columns[accepted]
        best_errors[accepted] = stage_minimum[accepted]
        selected_stage[accepted] = stage_number
        unresolved[accepted] = False

    matched_rows = np.flatnonzero(~unresolved)
    if len(matched_rows) < 2 or len(matched_rows) < len(measured_distance) * 0.5:
        return {
            "side": side,
            "blocker": (
                f"only {len(matched_rows)}/{len(measured_distance)} valid frames "
                "have a reliable object match"
            ),
            "valid_frames": int(valid_mask.sum()),
        }

    matched_columns = best_columns[matched_rows]
    matched_evidence = valid_evidence.iloc[matched_rows].reset_index(drop=True)
    trace = pd.DataFrame(
        {
            "event_time": matched_evidence["event_time"].to_numpy(dtype=float),
            "measured_distance": measured_distance[matched_rows],
            "object_index": matched_columns + 1,
            "distance_x": distance_x[matched_rows, matched_columns],
            "distance_y": distance_y[matched_rows, matched_columns],
            "relative_vx": relative_vx[matched_rows, matched_columns],
            "relative_lane": relative_lane[matched_rows, matched_columns],
            "clearance": clearance[matched_rows, matched_columns],
            "match_error": best_errors[matched_rows],
        }
    )
    trace["segment"] = find_physical_segments(trace)
    segment_sizes = trace.groupby("segment").size()
    minimum_index = trace["measured_distance"].idxmin()
    minimum_segment = int(trace.loc[minimum_index, "segment"])
    if violation_label and trace["measured_distance"].min() < required_distance:
        selected_segment = minimum_segment
    else:
        selected_segment = int(segment_sizes.idxmax())
    episode = trace.loc[trace["segment"] == selected_segment].reset_index(drop=True)

    motion, start_x, closest_x, end_x, position_states = classify_motion(
        episode["distance_x"].to_numpy(dtype=float)
    )
    start_lane, start_lane_share = stable_lane_value(
        episode["relative_lane"].to_numpy(dtype=float), start=True
    )
    end_lane, end_lane_share = stable_lane_value(
        episode["relative_lane"].to_numpy(dtype=float), start=False
    )
    target_lane_change = bool(
        start_lane is not None
        and end_lane is not None
        and start_lane != end_lane
        and start_lane_share >= 0.6
        and end_lane_share >= 0.6
    )
    lateral_change = robust_value(
        episode["distance_y"].to_numpy(dtype=float), start=False
    ) - robust_value(episode["distance_y"].to_numpy(dtype=float), start=True)
    if target_lane_change:
        moving_toward_ego = lateral_change < -0.5 if side == "left" else lateral_change > 0.5
        target_lane_direction = "toward_ego" if moving_toward_ego else "away_from_ego"
    else:
        target_lane_direction = "none"

    return {
        "side": side,
        "valid_frames": int(len(measured_distance)),
        "matched_frames": int(len(trace)),
        "unmatched_frames": int(unresolved.sum()),
        "selected_frames": int(len(episode)),
        "valid_start_s": float(valid_evidence["event_time"].iloc[0]),
        "valid_end_s": float(valid_evidence["event_time"].iloc[-1]),
        "episode_start_s": float(episode["event_time"].iloc[0]),
        "episode_end_s": float(episode["event_time"].iloc[-1]),
        "segment_count": int(trace["segment"].nunique()),
        "object_indices": sorted(int(value) for value in episode["object_index"].unique()),
        "match_max_error_m": float(episode["match_error"].max()),
        "match_median_error_m": float(episode["match_error"].median()),
        "min_distance_m": float(measured_distance.min()),
        "episode_min_distance_m": float(episode["measured_distance"].min()),
        "motion": motion,
        "position_states": position_states,
        "start_x_m": start_x,
        "closest_x_m": closest_x,
        "end_x_m": end_x,
        "median_relative_vx_mps": float(np.nanmedian(episode["relative_vx"])),
        "target_lane_change": target_lane_change,
        "target_lane_direction": target_lane_direction,
        "target_lateral_change_m": float(lateral_change),
        "used_track_status_fallback": bool(
            np.any(track_status[matched_rows, matched_columns] <= 0)
        ),
        "used_longitudinal_fallback": bool(np.any(selected_stage[matched_rows] >= 2)),
        "used_side_fallback": bool(np.any(selected_stage[matched_rows] >= 5)),
    }


def crossing_direction(values: np.ndarray, quality: np.ndarray) -> str | None:
    valid = np.isfinite(values) & np.isfinite(quality) & (quality > 0) & (np.abs(values) < 10)
    values = values[valid]
    if len(values) < 2:
        return None
    states = np.where(values > 0.2, 1, np.where(values < -0.2, -1, 0))
    last = 0
    for state in states:
        state = int(state)
        if state == 0:
            continue
        if last == 1 and state == -1:
            return "left"
        if last == -1 and state == 1:
            return "right"
        last = state
    return None


def ego_lane_change(
    evidence: pd.DataFrame,
    map_info: pd.DataFrame,
    interaction_mask: np.ndarray,
) -> tuple[bool, str]:
    subset = map_info.loc[interaction_mask]
    left_crossing = crossing_direction(
        subset["MAP_C0_Left1"].to_numpy(dtype=float),
        subset["MAP_Q_Left1"].to_numpy(dtype=float),
    )
    right_crossing = crossing_direction(
        subset["MAP_C0_Right1"].to_numpy(dtype=float),
        subset["MAP_Q_Right1"].to_numpy(dtype=float),
    )
    if left_crossing == "left":
        return True, "left"
    if right_crossing == "right":
        return True, "right"

    lane_index = pd.to_numeric(subset["EgoLaneIndex"], errors="coerce").dropna()
    centerline = pd.to_numeric(
        evidence.loc[interaction_mask, "Dis_centerline"], errors="coerce"
    ).dropna()
    if lane_index.nunique() > 1 and not centerline.empty:
        center_change = float(centerline.iloc[-1] - centerline.iloc[0])
        if abs(center_change) > 1.5:
            return True, "right" if center_change > 0 else "left"
    return False, "none"


def road_lane_opening(road_code: int, lane_code: int) -> str:
    road = ROAD_LABELS.get(road_code, "当前道路")
    lane = LANE_LABELS.get(lane_code, "所在车道")
    if lane == "所在车道":
        return f"自车在{road}上沿所在车道行驶"
    if road == "当前道路":
        return f"自车在{lane}上行驶"
    return f"自车在{road}的{lane}上行驶"


def motion_phrase(side_analysis: dict[str, Any]) -> str:
    side = side_analysis["side"]
    side_lane = "左侧车道" if side == "left" else "右侧车道"
    pass_side = "右侧" if side == "left" else "左侧"
    rear = "左后方" if side == "left" else "右后方"
    front = "左前方" if side == "left" else "右前方"
    motion = side_analysis["motion"]

    if motion == "ego_passes":
        return (
            f"{side_lane}前方有一辆速度较低的车辆，自车持续接近并从其{pass_side}"
            f"完成超越，目标车由{front}移动至{rear}"
        )
    if motion == "target_passes":
        return (
            f"{side_lane}后方一辆速度较高的车辆不断接近，并从{side_lane[:2]}"
            f"完成对自车的超越，目标车由{rear}移动至{front}"
        )
    if motion == "target_approach_fallback":
        return (
            f"{side_lane}后方一辆车先持续接近至与自车近乎并行，随后又逐渐"
            f"退回{rear}，未完成超越"
        )
    if motion == "target_approaches":
        return (
            f"{side_lane}后方一辆车不断接近，自车与其逐渐接近并行，但该车"
            f"在事件结束前尚未完成超越"
        )
    if motion == "ego_approach_fallback":
        return (
            f"{side_lane}前方一辆车先被自车逐渐追近，接近并行后又重新拉开"
            f"距离，自车未完成超越"
        )
    if motion == "ego_approaches":
        return (
            f"{side_lane}前方有一辆车，自车持续接近至与其近乎并行，但在事件"
            f"结束前尚未完成超越"
        )
    if motion == "target_pulls_away":
        return f"{side_lane}前方一辆车逐渐加速远离，与自车拉开纵向距离"
    if motion == "ego_pulls_away":
        return f"{side_lane}后方一辆车逐渐落后，自车与其拉开纵向距离"
    if motion == "position_alternation":
        return (
            f"{side_lane}一辆车与自车的纵向相对位置发生多次交替，双方先后"
            f"出现接近和拉开距离的过程"
        )
    if motion == "front_parallel":
        return f"{side_lane}前方一辆车与自车保持相对稳定的纵向距离"
    if motion == "rear_parallel":
        return f"{side_lane}后方一辆车与自车保持相对稳定的纵向距离"
    return f"{side_lane}内一辆车与自车保持近似并行，纵向相对位置变化不大"


def compliance_basis(
    evidence: pd.DataFrame,
    compliance_label: str,
    required_distance: float,
) -> dict[str, Any]:
    left_distance = pd.to_numeric(evidence["Dis_LV"], errors="coerce")
    right_distance = pd.to_numeric(evidence["Dis_RV"], errors="coerce")
    below_threshold = (
        left_distance.between(0, required_distance, inclusive="left")
        | right_distance.between(0, required_distance, inclusive="left")
    )
    centerline_ok = (
        pd.to_numeric(evidence["Dis_centerline"], errors="coerce").abs()
        <= CENTERLINE_THRESHOLD_M
    )
    avoidance_ok = (
        pd.to_numeric(evidence["Is_Lat_avoidance"], errors="coerce") > 0
    )

    below_count = int(below_threshold.sum())
    centerline_count = int((below_threshold & centerline_ok).sum())
    avoidance_count = int((below_threshold & avoidance_ok).sum())
    covered_count = int((below_threshold & (centerline_ok | avoidance_ok)).sum())
    event_avoidance = bool(avoidance_ok.any())

    if compliance_label.lower() != "compliance" or below_count == 0:
        basis = "not_applicable"
    elif centerline_count == below_count:
        basis = "centerline_or_avoidance" if event_avoidance else "centerline"
    elif covered_count == below_count:
        basis = "avoidance" if centerline_count == 0 else "centerline_or_avoidance"
    elif event_avoidance:
        basis = "avoidance"
    else:
        basis = "unverified"

    return {
        "basis": basis,
        "below_threshold_frames": below_count,
        "centerline_compliant_frames": centerline_count,
        "avoidance_compliant_frames": avoidance_count,
        "uncovered_below_threshold_frames": below_count - covered_count,
    }


def suggestion_text(
    compliance_label: str,
    primary: dict[str, Any],
    included: list[dict[str, Any]],
    required_distance: float,
    ego_changed_lane: bool,
    avoidance: bool,
    compliance_basis_name: str,
) -> str:
    violation = compliance_label.lower() == "violation"
    side = primary["side"]
    side_cn = "左侧" if side == "left" else "右侧"
    away_cn = "右侧" if side == "left" else "左侧"
    both = len(included) > 1
    minimum_distance = min(item["min_distance_m"] for item in included)

    if violation:
        violating_sides = [
            item for item in included if item["min_distance_m"] < required_distance
        ]
        if len(violating_sides) > 1 or both and not violating_sides:
            return (
                f"两侧同时有车辆接近时，应稳定保持车道位置并平顺调整车速，"
                f"避免在车辆并行时压缩任一侧的空间，确保与相邻车辆始终保持"
                f"至少{required_distance:.1f}米的横向安全距离。"
            )
        if ego_changed_lane:
            return (
                f"换道前应充分确认{side_cn}车辆的位置和速度，待安全空间充足后"
                f"再平顺完成横向移动，避免换道过程中将侧向间距压缩到"
                f"{required_distance:.1f}米以下。"
            )
        if primary["target_lane_change"]:
            return (
                f"发现{side_cn}车辆有并线趋势时，应及时减速或向{away_cn}平顺"
                f"留出空间，避免与其长时间近距离并行，确保横向间距不小于"
                f"{required_distance:.1f}米。"
            )
        return (
            f"遇到{side_cn}车辆接近或并行时，应提前保持车道居中并适当向"
            f"{away_cn}留出空间，必要时平顺调整车速，确保全程与相邻车辆"
            f"保持至少{required_distance:.1f}米的横向安全距离。"
        )

    if minimum_distance < required_distance:
        if compliance_basis_name == "centerline":
            return (
                f"近距离交互阶段自车保持在道路中心线±{CENTERLINE_THRESHOLD_M:.3f}米"
                "范围内，当前行为不属于违规；后续继续稳定居中行驶，并关注相邻"
                "车辆的速度和位置变化即可。"
            )
        if compliance_basis_name == "avoidance":
            return (
                "此次横向避让及时，当前行为不属于违规；后续继续采用平顺、小幅的"
                "轨迹调整，并持续观察相邻车辆动态即可。"
            )
        if compliance_basis_name == "centerline_or_avoidance":
            return (
                f"近距离交互阶段自车保持在道路中心线±{CENTERLINE_THRESHOLD_M:.3f}米"
                "范围内或进行了横向避让，当前行为不属于违规；后续继续保持平稳"
                "驾驶，并持续关注相邻车辆动态即可。"
            )
        return (
            "原始事件标签将自车行为判定为合规，可以继续保持当前平稳的驾驶方式；"
            "同时仍应关注相邻车辆，避免侧向间距进一步缩小。"
        )
    if avoidance:
        return (
            "此次横向避让较为及时，后续可以继续采用平顺、小幅的轨迹调整，"
            "并持续观察相邻车辆动态，保持当前充足的侧向间距。"
        )
    if ego_changed_lane:
        return (
            "此次换道过程中侧向间距保持充足，可以继续维持平顺的横向控制，"
            "并在换道前后持续确认相邻车道车辆的位置和速度。"
        )
    if both:
        return (
            "当前在两侧均有车辆的情况下仍保持了稳定轨迹和充足间距，可以继续"
            "平稳保持本车道，并持续关注两侧车辆的速度和位置变化。"
        )
    if primary["motion"] == "ego_passes":
        return (
            "此次超越过程中侧向间距充足、行驶轨迹也较稳定，可以继续保持平稳"
            "车速和当前车道位置，同时留意相邻车辆的后续动态。"
        )
    if primary["motion"] == "target_passes":
        return (
            "当前的车道保持和侧向间距控制较为稳妥，继续平稳保持本车道，并"
            "持续观察相邻超车车辆的行驶动态即可。"
        )
    return (
        "当前行驶轨迹和侧向间距控制较为稳定，可以继续保持平顺驾驶，并持续"
        "关注相邻车辆的接近或并线趋势。"
    )


def description_text(
    opening: str,
    included: list[dict[str, Any]],
    ego_changed_lane: bool,
    ego_lane_direction: str,
    avoidance: bool,
    avoidance_direction: str,
    required_distance: float,
    compliance_label: str,
    compliance_basis_name: str,
) -> str:
    interactions = "；同时，".join(motion_phrase(item) for item in included)
    if ego_changed_lane:
        direction = {"left": "向左", "right": "向右"}.get(ego_lane_direction, "")
        ego_clause = f"自车{direction}完成了换道" if direction else "自车发生了换道"
    else:
        ego_clause = "自车保持在原车道内"

    changed_targets = [item for item in included if item["target_lane_change"]]
    if changed_targets:
        target_clauses = []
        for item in changed_targets:
            side_cn = "左侧" if item["side"] == "left" else "右侧"
            movement = (
                "向自车所在车道靠近"
                if item["target_lane_direction"] == "toward_ego"
                else "向外侧车道移动"
            )
            target_clauses.append(f"{side_cn}目标车曾{movement}")
        target_clause = "，".join(target_clauses)
    elif len(included) > 1:
        target_clause = "相邻车辆均保持在各自车道内"
    else:
        target_clause = "目标车保持在原车道内"

    if avoidance:
        direction = {"left": "向左", "right": "向右"}.get(avoidance_direction, "")
        avoidance_clause = (
            f"自车还{direction}进行了明显的横向避让"
            if direction
            else "自车还进行了明显的横向避让"
        )
    else:
        avoidance_clause = "自车未进行额外横向避让"

    minimum_distance = min(item["min_distance_m"] for item in included)
    if compliance_label.lower() == "violation":
        gap_clause = (
            f"最近横向净距约为{minimum_distance:.3f}米，低于"
            f"{required_distance:.1f}米的要求"
        )
    elif minimum_distance < required_distance:
        if compliance_basis_name == "centerline":
            gap_clause = (
                f"最近横向净距约为{minimum_distance:.3f}米，虽低于"
                f"{required_distance:.1f}米，但相应阶段自车保持在道路中心线"
                f"±{CENTERLINE_THRESHOLD_M:.3f}米范围内，因此不属于自车违规"
            )
        elif compliance_basis_name == "avoidance":
            gap_clause = (
                f"最近横向净距约为{minimum_distance:.3f}米，虽低于"
                f"{required_distance:.1f}米，但自车进行了横向避让，因此不属于自车违规"
            )
        elif compliance_basis_name == "centerline_or_avoidance":
            gap_clause = (
                f"最近横向净距约为{minimum_distance:.3f}米，虽低于"
                f"{required_distance:.1f}米，但相应阶段自车保持在道路中心线"
                f"±{CENTERLINE_THRESHOLD_M:.3f}米范围内或进行了横向避让，"
                "因此不属于自车违规"
            )
        else:
            gap_clause = (
                f"最近横向净距约为{minimum_distance:.3f}米，原始事件标签将"
                "自车行为判定为合规"
            )
    else:
        gap_clause = (
            f"最近横向净距约为{minimum_distance:.3f}米，满足"
            f"{required_distance:.1f}米的安全距离要求"
        )
    return (
        f"{opening}，{interactions}。过程中，{ego_clause}，{target_clause}，"
        f"{avoidance_clause}；{gap_clause}。"
    )


def align_event_frames(
    evidence: pd.DataFrame,
    objects: pd.DataFrame,
    map_info: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not (len(evidence) == len(objects) == len(map_info)):
        raise ValueError(
            f"row count mismatch: evidence={len(evidence)}, objects={len(objects)}, map={len(map_info)}"
        )
    evidence_time = evidence["event_time"].to_numpy(dtype=float)
    if not np.allclose(evidence_time, objects["event_time"].to_numpy(dtype=float), atol=1e-6):
        raise ValueError("EvidenceChain and ObjInfo event_time values are not aligned")
    if not np.allclose(evidence_time, map_info["event_time"].to_numpy(dtype=float), atol=1e-6):
        raise ValueError("EvidenceChain and MapInfo event_time values are not aligned")
    return evidence, objects, map_info


def analyze_event(city: str, json_path: Path) -> dict[str, Any]:
    raw_json = json_path.read_bytes()
    metadata = json.loads(raw_json.decode("utf-8-sig"))
    result = metadata["Result"]
    evidence_meta = metadata["Evidence"]
    compliance_label = str(result["Compliance_label"])
    required_distance = float(evidence_meta.get("Required_lateral_distance_m", 1.5))
    violation_label = compliance_label.lower() == "violation"

    event_dir = json_path.parent
    evidence_path = next(event_dir.glob("*_EvidenceChain.csv"))
    object_path = next(event_dir.glob("*_ObjInfo.csv"))
    map_path = next(event_dir.glob("*_MapInfo.csv"))

    evidence_columns = [
        "event_time",
        "Dis_LV",
        "Dis_RV",
        "Dis_centerline",
        "Is_Lat_avoidance",
        "Ego_velocity",
        "Road_type",
        "Lane_type",
    ]
    object_suffixes = [
        "DistanceX",
        "DistanceY",
        "Width",
        "RelativeVx",
        "RelativeLane",
        "TrackStatus",
    ]
    object_columns = ["event_time"] + [
        f"Obj{index:02d}_{suffix}"
        for suffix in object_suffixes
        for index in range(1, 31)
    ]
    map_columns = [
        "event_time",
        "EgoLaneIndex",
        "MAP_Q_Left1",
        "MAP_Q_Right1",
        "MAP_C0_Left1",
        "MAP_C0_Right1",
    ]
    evidence = pd.read_csv(evidence_path, usecols=evidence_columns)
    objects = pd.read_csv(object_path, usecols=object_columns)
    map_info = pd.read_csv(map_path, usecols=map_columns)
    evidence, objects, map_info = align_event_frames(evidence, objects, map_info)

    left = analyze_side(
        "left", evidence, objects, required_distance, violation_label
    )
    right = analyze_side(
        "right", evidence, objects, required_distance, violation_label
    )
    side_analyses = [
        item for item in (left, right) if item is not None and "blocker" not in item
    ]
    blockers = [
        item["blocker"] for item in (left, right) if item is not None and "blocker" in item
    ]
    if not side_analyses:
        blockers.append("event has no analyzable left or right target")
        return {
            "city": city,
            "json_path": str(json_path),
            "source_json_sha256": sha256_bytes(raw_json),
            "blockers": blockers,
        }

    interaction_mask = (
        (pd.to_numeric(evidence["Dis_LV"], errors="coerce").to_numpy() >= 0)
        | (pd.to_numeric(evidence["Dis_RV"], errors="coerce").to_numpy() >= 0)
    )
    ego_changed_lane, ego_lane_direction = ego_lane_change(
        evidence, map_info, interaction_mask
    )
    interaction_evidence = evidence.loc[interaction_mask]
    avoidance = bool(
        (pd.to_numeric(interaction_evidence["Is_Lat_avoidance"], errors="coerce") > 0).any()
    )
    road_code = mode_int(interaction_evidence["Road_type"])
    lane_code = mode_int(interaction_evidence["Lane_type"], default=1)

    if violation_label:
        primary = min(side_analyses, key=lambda item: item["min_distance_m"])
    else:
        primary = max(side_analyses, key=lambda item: item["valid_frames"])
    included = [primary]
    for item in side_analyses:
        if item is primary:
            continue
        significant = (
            item["valid_frames"] >= max(20, int(primary["valid_frames"] * 0.25))
            or item["min_distance_m"] <= primary["min_distance_m"] + 0.3
        )
        if significant:
            included.append(item)

    centerline = pd.to_numeric(
        interaction_evidence["Dis_centerline"], errors="coerce"
    ).dropna()
    center_change = (
        float(centerline.iloc[-1] - centerline.iloc[0]) if len(centerline) >= 2 else 0.0
    )
    if avoidance:
        if len(included) == 1:
            avoidance_direction = "right" if primary["side"] == "left" else "left"
        elif abs(center_change) >= 0.2:
            avoidance_direction = "right" if center_change > 0 else "left"
        else:
            avoidance_direction = "none"
    else:
        avoidance_direction = "none"

    opening = road_lane_opening(road_code, lane_code)
    basis = compliance_basis(evidence, compliance_label, required_distance)
    description = description_text(
        opening,
        included,
        ego_changed_lane,
        ego_lane_direction,
        avoidance,
        avoidance_direction,
        required_distance,
        compliance_label,
        basis["basis"],
    )
    suggestion = suggestion_text(
        compliance_label,
        primary,
        included,
        required_distance,
        ego_changed_lane,
        avoidance,
        basis["basis"],
    )

    overall_min = min(item["min_distance_m"] for item in side_analyses)
    warnings: list[str] = []
    for item in side_analyses:
        if item["match_max_error_m"] > 0.02:
            warnings.append(
                f"{item['side']} match error {item['match_max_error_m']:.3f} m"
            )
        if item["selected_frames"] < 10:
            warnings.append(f"{item['side']} selected episode has fewer than 10 frames")
        if item["used_track_status_fallback"]:
            warnings.append(f"{item['side']} used candidates without valid TrackStatus")
        if item["used_longitudinal_fallback"]:
            warnings.append(f"{item['side']} used edge frames beyond 10 m longitudinally")
        if item["used_side_fallback"]:
            warnings.append(f"{item['side']} used a candidate with inconsistent lateral sign")
        if item["unmatched_frames"]:
            warnings.append(
                f"{item['side']} omitted {item['unmatched_frames']} unmatched edge frames"
            )
    if violation_label and overall_min >= required_distance:
        warnings.append("Violation label but computed minimum distance is not below threshold")
    if basis["basis"] == "unverified":
        warnings.append(
            "Compliance below threshold is not fully covered by centerline/avoidance rule"
        )

    minimum_meta = [
        value
        for value in (
            evidence_meta.get("Minimum_left_vehicle_distance_m"),
            evidence_meta.get("Minimum_right_vehicle_distance_m"),
        )
        if value is not None
    ]
    if minimum_meta and abs(float(min(minimum_meta)) - overall_min) > 0.02:
        warnings.append("JSON minimum distance differs from EvidenceChain minimum")

    return {
        "city": city,
        "json_path": str(json_path),
        "source_json_sha256": sha256_bytes(raw_json),
        "trip": json_path.parent.parent.name,
        "event": json_path.parent.name,
        "compliance_label": compliance_label,
        "required_distance_m": required_distance,
        "centerline_threshold_m": CENTERLINE_THRESHOLD_M,
        "overall_min_distance_m": overall_min,
        "compliance_basis": basis["basis"],
        "below_threshold_frames": basis["below_threshold_frames"],
        "centerline_compliant_frames": basis["centerline_compliant_frames"],
        "avoidance_compliant_frames": basis["avoidance_compliant_frames"],
        "uncovered_below_threshold_frames": basis[
            "uncovered_below_threshold_frames"
        ],
        "road_code": road_code,
        "road_label": ROAD_LABELS.get(road_code, "当前道路"),
        "lane_code": lane_code,
        "lane_label": LANE_LABELS.get(lane_code, "所在车道"),
        "primary_side": primary["side"],
        "included_sides": [item["side"] for item in included],
        "ego_lane_change": ego_changed_lane,
        "ego_lane_direction": ego_lane_direction,
        "lateral_avoidance": avoidance,
        "avoidance_direction": avoidance_direction,
        "left": left,
        "right": right,
        "Scenario_description_VLM": description,
        "Driving_suggestion_VLM": suggestion,
        "warnings": warnings,
        "blockers": blockers,
    }


def discover_json_files() -> list[tuple[str, Path]]:
    discovered: list[tuple[str, Path]] = []
    for city, root in ROOTS.items():
        city_files = sorted(root.rglob("*.json"))
        print(f"Discovered {len(city_files)} JSON files in {city}", flush=True)
        discovered.extend((city, path) for path in city_files)
    return discovered


def write_audit(records: list[dict[str, Any]]) -> None:
    with AUDIT_JSONL.open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")

    flat_rows = []
    for record in records:
        flat_rows.append(
            {
                "city": record.get("city"),
                "trip": record.get("trip"),
                "event": record.get("event"),
                "json_path": record.get("json_path"),
                "compliance_label": record.get("compliance_label"),
                "road_label": record.get("road_label"),
                "lane_label": record.get("lane_label"),
                "primary_side": record.get("primary_side"),
                "included_sides": ",".join(record.get("included_sides", [])),
                "overall_min_distance_m": record.get("overall_min_distance_m"),
                "compliance_basis": record.get("compliance_basis"),
                "below_threshold_frames": record.get("below_threshold_frames"),
                "centerline_compliant_frames": record.get(
                    "centerline_compliant_frames"
                ),
                "avoidance_compliant_frames": record.get(
                    "avoidance_compliant_frames"
                ),
                "uncovered_below_threshold_frames": record.get(
                    "uncovered_below_threshold_frames"
                ),
                "left_motion": (record.get("left") or {}).get("motion"),
                "right_motion": (record.get("right") or {}).get("motion"),
                "ego_lane_change": record.get("ego_lane_change"),
                "lateral_avoidance": record.get("lateral_avoidance"),
                "warnings": " | ".join(record.get("warnings", [])),
                "blockers": " | ".join(record.get("blockers", [])),
                "Scenario_description_VLM": record.get("Scenario_description_VLM"),
                "Driving_suggestion_VLM": record.get("Driving_suggestion_VLM"),
            }
        )
    pd.DataFrame(flat_rows).to_csv(AUDIT_CSV, index=False, encoding="utf-8-sig")


def read_audit() -> list[dict[str, Any]]:
    if not AUDIT_JSONL.exists():
        raise FileNotFoundError(f"Audit file not found: {AUDIT_JSONL}")
    with AUDIT_JSONL.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_json_fields(records: list[dict[str, Any]]) -> None:
    blockers = [record for record in records if record.get("blockers")]
    if blockers:
        raise RuntimeError(f"Refusing to write: {len(blockers)} audit records have blockers")

    for number, record in enumerate(records, start=1):
        path = Path(record["json_path"])
        raw = path.read_bytes()
        if sha256_bytes(raw) != record["source_json_sha256"]:
            raise RuntimeError(f"Source JSON changed after analysis: {path}")
        has_bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw.decode("utf-8-sig")
        newline = "\r\n" if "\r\n" in text else "\n"
        data = json.loads(text)
        result = data["Result"]
        if "Driving_suggestion" not in result:
            raise KeyError(f"Driving_suggestion missing from {path}")

        new_result: dict[str, Any] = {}
        for key, value in result.items():
            if key in VLM_KEYS:
                continue
            new_result[key] = value
            if key == "Driving_suggestion":
                new_result["Scenario_description_VLM"] = record[
                    "Scenario_description_VLM"
                ]
                new_result["Driving_suggestion_VLM"] = record[
                    "Driving_suggestion_VLM"
                ]
        data["Result"] = new_result
        rendered = json.dumps(data, ensure_ascii=False, indent=2)
        if newline == "\r\n":
            rendered = rendered.replace("\n", "\r\n")
        rendered += newline
        output = (b"\xef\xbb\xbf" if has_bom else b"") + rendered.encode("utf-8")

        temp_path = path.with_name(path.name + ".vlm_tmp")
        temp_path.write_bytes(output)
        json.loads(temp_path.read_text(encoding="utf-8-sig"))
        os.replace(temp_path, path)
        if number % 100 == 0 or number == len(records):
            print(f"Wrote {number}/{len(records)} JSON files", flush=True)


def validate_written_files(records: list[dict[str, Any]]) -> None:
    expected_paths = {record["json_path"] for record in records}
    actual_paths = {str(path) for _, path in discover_json_files()}
    if expected_paths != actual_paths:
        raise RuntimeError(
            f"Audit/file path mismatch: audit={len(expected_paths)}, actual={len(actual_paths)}"
        )

    errors: list[str] = []
    for number, record in enumerate(records, start=1):
        path = Path(record["json_path"])
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            result = data["Result"]
            keys = list(result)
            if keys.index("Scenario_description_VLM") != keys.index("Driving_suggestion") + 1:
                errors.append(f"field order error: {path}")
            if keys.index("Driving_suggestion_VLM") != keys.index("Scenario_description_VLM") + 1:
                errors.append(f"field order error: {path}")
            for key in VLM_KEYS:
                if result.get(key) != record[key]:
                    errors.append(f"{key} mismatch: {path}")
        except Exception as exc:
            errors.append(f"{path}: {exc}")
        if number % 250 == 0 or number == len(records):
            print(f"Validated {number}/{len(records)} JSON files", flush=True)
    if errors:
        raise RuntimeError("\n".join(errors[:20]))


def summarize(records: list[dict[str, Any]]) -> None:
    blockers = sum(bool(record.get("blockers")) for record in records)
    warnings = sum(bool(record.get("warnings")) for record in records)
    city_counts = Counter(record.get("city") for record in records)
    compliance = Counter(record.get("compliance_label") for record in records)
    compliance_bases = Counter(record.get("compliance_basis") for record in records)
    motions = Counter()
    for record in records:
        for side in ("left", "right"):
            analysis = record.get(side)
            if analysis and "motion" in analysis:
                motions[analysis["motion"]] += 1
    print(f"Records: {len(records)}; cities={dict(city_counts)}", flush=True)
    print(
        f"Compliance={dict(compliance)}; blockers={blockers}; records_with_warnings={warnings}",
        flush=True,
    )
    print(f"Motion categories={dict(motions)}", flush=True)
    print(f"Compliance bases={dict(compliance_bases)}", flush=True)


def validate_audit_language(records: list[dict[str, Any]]) -> None:
    issues: list[str] = []
    for record in records:
        description = record.get("Scenario_description_VLM", "")
        suggestion = record.get("Driving_suggestion_VLM", "")
        label = record.get("compliance_label")
        required = record.get("required_distance_m")
        minimum = record.get("overall_min_distance_m")
        path = record.get("json_path")
        if not description or not suggestion:
            issues.append(f"empty generated field: {path}")
            continue
        if record.get("road_code") == 9 and "JCT道路" not in description:
            issues.append(f"JCT wording missing: {path}")
        if "道路交汇区域" in description:
            issues.append(f"obsolete JCT wording: {path}")
        if label == "Violation" and "低于" not in description:
            issues.append(f"violation wording mismatch: {path}")
        if label == "Compliance" and minimum >= required and "满足" not in description:
            issues.append(f"compliance threshold wording mismatch: {path}")
        if label == "Compliance" and minimum < required:
            basis = record.get("compliance_basis")
            if basis == "centerline":
                expected = f"道路中心线±{record['centerline_threshold_m']:.3f}米"
                if expected not in description or "不属于自车违规" not in description:
                    issues.append(f"centerline compliance wording mismatch: {path}")
            elif basis == "avoidance":
                if "横向避让" not in description or "不属于自车违规" not in description:
                    issues.append(f"avoidance compliance wording mismatch: {path}")
            elif basis == "centerline_or_avoidance":
                expected = f"道路中心线±{record['centerline_threshold_m']:.3f}米"
                if (
                    expected not in description
                    or "横向避让" not in description
                    or "不属于自车违规" not in description
                ):
                    issues.append(f"combined compliance wording mismatch: {path}")
            elif "判定为合规" not in description:
                issues.append(f"compliance source wording mismatch: {path}")
        if record.get("lateral_avoidance") and "横向避让" not in description:
            issues.append(f"avoidance wording missing: {path}")
        if not record.get("lateral_avoidance") and "未进行额外横向避让" not in description:
            issues.append(f"no-avoidance wording missing: {path}")
        if not description.endswith("。") or not suggestion.endswith("。"):
            issues.append(f"punctuation error: {path}")
    if issues:
        raise RuntimeError("\n".join(issues[:20]))
    print(f"Language QA passed for {len(records)} records", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--mode",
        choices=("analyze", "write", "validate"),
        required=True,
    )
    args = parser.parse_args()

    if args.mode == "analyze":
        files = discover_json_files()
        records: list[dict[str, Any]] = []
        for number, (city, path) in enumerate(files, start=1):
            try:
                records.append(analyze_event(city, path))
            except Exception as exc:
                records.append(
                    {
                        "city": city,
                        "json_path": str(path),
                        "source_json_sha256": sha256_bytes(path.read_bytes()),
                        "blockers": [f"{type(exc).__name__}: {exc}"],
                    }
                )
            if number % 25 == 0 or number == len(files):
                print(f"Analyzed {number}/{len(files)} events", flush=True)
        write_audit(records)
        summarize(records)
        print(f"Audit JSONL: {AUDIT_JSONL}", flush=True)
        print(f"Audit CSV: {AUDIT_CSV}", flush=True)
    elif args.mode == "write":
        records = read_audit()
        summarize(records)
        validate_audit_language(records)
        write_json_fields(records)
    else:
        records = read_audit()
        summarize(records)
        validate_audit_language(records)
        validate_written_files(records)


if __name__ == "__main__":
    main()
