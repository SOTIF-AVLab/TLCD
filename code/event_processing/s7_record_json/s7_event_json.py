from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


DEFAULT_ROOT = Path(os.environ.get("TLCD_DATA_ROOT", "."))


@dataclass(frozen=True)
class ArticleRule:
    article_id: str
    text: str
    trigger_column: str
    compliance_column: str


@dataclass(frozen=True)
class CategoryConfig:
    key: str
    event_root: str
    event_csv: str
    evidence_pattern: str
    evidence_regex: str
    record_name: str
    anchor_description: str
    articles: Tuple[ArticleRule, ...]
    evidence_builder: Callable[[Sequence[Dict[str, str]], Sequence[bool]], Tuple[Dict[str, Any], str]]
    violation_reason_builder: Callable[[Dict[str, str], Dict[str, Any]], str]
    driving_suggestion: str
    shared_trigger_column: str = ""


ROAD_TYPES = {
    0: "unknown road",
    1: "multiple carriageway",
    2: "single carriageway",
    4: "service road",
    6: "entry ramp",
    7: "exit ramp",
    9: "junction",
    18: "service-area approach",
    19: "service-area junction",
    20: "service-area approach junction",
    27: "toll booth",
    31: "motorway entry ramp",
    32: "motorway exit ramp",
    34: "tunnel",
    37: "toll area",
    38: "rest area",
}

LANE_TYPES = {
    0: "unknown lane",
    1: "regular lane",
    2: "deceleration lane",
    3: "acceleration lane",
    4: "compound lane",
    5: "drivable parking lane",
    8: "slow lane",
    9: "drivable shoulder lane",
    10: "shoulder lane",
    12: "regulated-access lane",
    13: "variable driving lane",
    14: "emergency strip",
    15: "other lane",
}

LINE_TYPES = {
    0: "unknown line",
    1: "solid line",
    2: "dashed line",
    3: "double solid line",
    4: "double dashed line",
    5: "left-solid right-dashed line",
    6: "right-solid left-dashed line",
    7: "channelizing line",
    10: "other line",
}

ARTICLE_REASONS = {
    "IMR_45.1": "The ego vehicle exceeded the maximum speed indicated by a traffic sign or road marking.",
    "IMR_46.3": "The ego vehicle exceeded 30 km/h while negotiating a sharp turn.",
    "IMR_46.4": "The ego vehicle exceeded 30 km/h on a narrow road or bridge.",
    "IMR_46.5": "The ego vehicle exceeded 30 km/h while descending a steep slope.",
    "IMR_78.1": "The ego vehicle exceeded the statutory maximum speed on an expressway.",
    "IMR_78.3": "The ego vehicle exceeded the maximum speed shown by the applicable road sign.",
    "IMR_78.2": "The ego vehicle traveled below the statutory minimum expressway speed without congestion.",
    "IMR_78.4": "The ego vehicle traveled below the minimum speed shown by the applicable road sign without congestion.",
    "IMR_78.5": "The ego vehicle traveled below the minimum speed for the left lane of a two-lane expressway.",
    "IMR_78.6": "The ego vehicle traveled below the minimum speed for the leftmost lane of a multi-lane expressway.",
    "IMR_78.7": "The ego vehicle traveled below the minimum speed for a middle lane of a multi-lane expressway.",
    "IMR_80.1": "The ego vehicle did not maintain at least 100 m from the preceding vehicle while traveling at 100 km/h or more.",
    "IMR_80.2": "The ego vehicle did not maintain at least 50 m from the preceding vehicle while traveling below 100 km/h.",
    "OSP_8.2.1": "The ego vehicle did not maintain the required lateral safety distance from an adjacent vehicle.",
    "IMR_44.1": "The lane change affected the safe operation of another vehicle.",
    "OSP_9.3.1": "The ego vehicle changed across two or more lanes consecutively within the prohibited interval.",
    "TSM_4.3.1": "The ego vehicle remained on a dashed lane line longer than permitted.",
    "TSM_4.5.2": "The ego vehicle crossed or drove on a solid lane boundary.",
    "TSM_4.5.3": "The ego vehicle crossed or drove on a channelizing line.",
    "IMR_47.4": "The ego vehicle overtook from the right side.",
    "IMR_82.5": "The ego vehicle overtook on a ramp, acceleration lane, or deceleration lane.",
    "TSL_43.6": "The ego vehicle overtook while traveling through a tunnel.",
    "TSL_43.8": "The ego vehicle overtook on a congested urban road section.",
}


def _number(value: Any, default: float = math.nan) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _round(value: float, digits: int = 3) -> Optional[float]:
    if not math.isfinite(value):
        return None
    rounded = float(round(value, digits))
    return int(rounded) if rounded.is_integer() else rounded


def _format_number(value: Any, digits: int = 2) -> str:
    number = _number(value)
    if not math.isfinite(number):
        return "unknown"
    text = f"{number:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def _format_seconds(value: Any) -> str:
    number = _number(value, -1)
    return f"{_format_number(number)} s"


def _column(
    rows: Sequence[Dict[str, str]],
    column: str,
    mask: Optional[Sequence[bool]] = None,
    minimum: Optional[float] = None,
) -> List[float]:
    values: List[float] = []
    for index, row in enumerate(rows):
        if mask is not None and not mask[index]:
            continue
        value = _number(row.get(column))
        if not math.isfinite(value):
            continue
        if minimum is not None and value < minimum:
            continue
        values.append(value)
    return values


def _range(values: Iterable[float], scale: float = 1.0) -> Dict[str, Optional[float]]:
    scaled = [value * scale for value in values if math.isfinite(value)]
    if not scaled:
        return {"min": None, "max": None}
    return {"min": _round(min(scaled)), "max": _round(max(scaled))}


def _mode(values: Iterable[float]) -> Optional[float]:
    rounded = [round(value, 6) for value in values if math.isfinite(value)]
    if not rounded:
        return None
    return _round(Counter(rounded).most_common(1)[0][0])


def _any_nonzero(rows: Sequence[Dict[str, str]], column: str, mask: Sequence[bool]) -> bool:
    return any(value != 0 for value in _column(rows, column, mask))


def _labels(
    rows: Sequence[Dict[str, str]],
    column: str,
    labels: Dict[int, str],
    mask: Sequence[bool],
) -> List[str]:
    result: List[str] = []
    saw_unknown = False
    for value in _column(rows, column, mask):
        if int(round(value)) == 0:
            saw_unknown = True
            continue
        label = labels.get(int(round(value)), f"code {int(round(value))}")
        if label not in result:
            result.append(label)
    if not result and saw_unknown:
        result.append(labels.get(0, "unknown"))
    return result


def _join_labels(values: Sequence[str], fallback: str) -> str:
    if not values:
        return fallback
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + f" and {values[-1]}"


def _longest_run(mask: Sequence[bool]) -> Optional[Tuple[int, int]]:
    best: Optional[Tuple[int, int]] = None
    start: Optional[int] = None
    for index, active in enumerate(list(mask) + [False]):
        if active and start is None:
            start = index
        elif not active and start is not None:
            candidate = (start, index - 1)
            if best is None or candidate[1] - candidate[0] > best[1] - best[0]:
                best = candidate
            start = None
    return best


def _runs(mask: Sequence[bool]) -> List[Tuple[int, int]]:
    result: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for index, active in enumerate(list(mask) + [False]):
        if active and start is None:
            start = index
        elif not active and start is not None:
            result.append((start, index - 1))
            start = None
    return result


def _row_with_minimum(
    rows: Sequence[Dict[str, str]],
    column: str,
    mask: Sequence[bool],
    minimum: Optional[float] = None,
) -> Optional[Tuple[int, float]]:
    candidates: List[Tuple[int, float]] = []
    for index, row in enumerate(rows):
        if not mask[index]:
            continue
        value = _number(row.get(column))
        if not math.isfinite(value):
            continue
        if minimum is not None and value < minimum:
            continue
        candidates.append((index, value))
    return min(candidates, key=lambda item: item[1]) if candidates else None


def _row_with_maximum(
    rows: Sequence[Dict[str, str]],
    column: str,
    mask: Sequence[bool],
) -> Optional[Tuple[int, float]]:
    candidates = [
        (index, _number(row.get(column)))
        for index, row in enumerate(rows)
        if mask[index] and math.isfinite(_number(row.get(column)))
    ]
    return max(candidates, key=lambda item: item[1]) if candidates else None


def _status_from_column(
    rows: Sequence[Dict[str, str]],
    column: str,
    mask: Sequence[bool],
) -> str:
    values = _column(rows, column, mask)
    if any(value < 0 for value in values):
        return "Violation"
    if any(value > 0 for value in values):
        return "Compliance"
    return "Unknown"


def _decision(
    rows: Sequence[Dict[str, str]],
    config: CategoryConfig,
) -> Tuple[str, Dict[str, str], List[bool]]:
    row_count = len(rows)
    statuses: Dict[str, str] = {}
    trigger_union = [False] * row_count
    violation_union = [False] * row_count

    shared_trigger = [False] * row_count
    if config.shared_trigger_column:
        shared_trigger = [
            _number(row.get(config.shared_trigger_column), 0) != 0
            for row in rows
        ]

    for article in config.articles:
        if config.shared_trigger_column:
            trigger = shared_trigger
        else:
            trigger = [
                _number(row.get(article.trigger_column), 0) != 0
                for row in rows
            ]
        compliance = [
            _number(row.get(article.compliance_column), 0)
            for row in rows
        ]
        active = [
            trigger[index] or compliance[index] != 0
            for index in range(row_count)
        ]
        if not any(active):
            continue

        for index, is_active in enumerate(active):
            trigger_union[index] = trigger_union[index] or is_active

        if any(value < 0 for value in compliance):
            statuses[article.article_id] = "Violation"
            for index, value in enumerate(compliance):
                violation_union[index] = violation_union[index] or value < 0
        elif any(value > 0 for value in compliance):
            statuses[article.article_id] = "Compliance"
        else:
            statuses[article.article_id] = "Unknown"

    if any(status == "Violation" for status in statuses.values()):
        return "Violation", statuses, violation_union
    if statuses and all(status == "Compliance" for status in statuses.values()):
        return "Compliance", statuses, trigger_union
    return "Unknown", statuses, trigger_union


def _active_mask(mask: Sequence[bool]) -> List[bool]:
    if any(mask):
        return list(mask)
    return [True] * len(mask)


def _overlap_direction(
    rows: Sequence[Dict[str, str]],
    mask: Sequence[bool],
) -> str:
    for index, row in enumerate(rows):
        if not mask[index]:
            continue
        left = _number(row.get("overlap_LeftLine"), 0) != 0
        right = _number(row.get("overlap_RightLine"), 0) != 0
        if left and not right:
            return "left"
        if right and not left:
            return "right"
    return "unknown"


def _lane_change_maneuvers(
    rows: Sequence[Dict[str, str]],
    trigger_column: str = "",
) -> List[Dict[str, Any]]:
    if rows and "current_cross_dir" in rows[0]:
        active = [_number(row.get("current_cross_dir"), 0) != 0 for row in rows]
    elif trigger_column:
        active = [_number(row.get(trigger_column), 0) != 0 for row in rows]
    else:
        active = [
            _number(row.get("overlap_LeftLine"), 0) != 0
            or _number(row.get("overlap_RightLine"), 0) != 0
            for row in rows
        ]

    maneuvers: List[Dict[str, Any]] = []
    direction_text = {-1: "right", 0: "unknown", 1: "left"}
    for start, end in _runs(active):
        direction_code = 0
        for index in range(start, end + 1):
            current_direction = int(round(_number(rows[index].get("current_cross_dir"), 0)))
            if current_direction in (-1, 1):
                direction_code = current_direction
                break
        if direction_code == 0:
            overlap_direction = _overlap_direction(rows, [
                start <= index <= end for index in range(len(rows))
            ])
            direction_code = 1 if overlap_direction == "left" else (
                -1 if overlap_direction == "right" else 0
            )

        first_overlap_index: Optional[int] = None
        first_side = 0
        cross_index: Optional[int] = None
        for index in range(start, end + 1):
            left = _number(rows[index].get("overlap_LeftLine"), 0) != 0
            right = _number(rows[index].get("overlap_RightLine"), 0) != 0
            side = 1 if left and not right else (-1 if right and not left else 0)
            if first_overlap_index is None and side != 0:
                first_overlap_index = index
                first_side = side
            elif first_overlap_index is not None and side == -first_side:
                cross_index = index
                break

        if first_overlap_index is None:
            first_overlap_index = start
        if cross_index is None:
            cross_index = first_overlap_index

        maneuvers.append({
            "direction_code": direction_code,
            "direction": direction_text.get(direction_code, "unknown"),
            "start_index": start,
            "cross_index": cross_index,
            "end_index": end,
            "start_time_s": _round(_number(rows[start].get("event_time"), 0.01 * (start + 1))),
            "line_overlap_start_time_s": _round(_number(
                rows[first_overlap_index].get("event_time"), 0.01 * (first_overlap_index + 1)
            )),
            "cross_line_time_s": _round(_number(
                rows[cross_index].get("event_time"), 0.01 * (cross_index + 1)
            )),
            "end_time_s": _round(_number(rows[end].get("event_time"), 0.01 * (end + 1))),
        })
    return maneuvers


def _public_maneuver(maneuver: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "Direction": maneuver["direction"],
        "Start_time_s": maneuver["start_time_s"],
        "Line_overlap_start_time_s": maneuver["line_overlap_start_time_s"],
        "Cross_line_time_s": maneuver["cross_line_time_s"],
        "End_time_s": maneuver["end_time_s"],
    }


def _sign_context(
    rows: Sequence[Dict[str, str]],
    column: str,
    mask: Sequence[bool],
) -> str:
    active_indices = [index for index, active in enumerate(mask) if active]
    if not active_indices:
        return "no_decisive_interval"

    start = active_indices[0]
    end = active_indices[-1]
    values = [_number(row.get(column), 0) != 0 for row in rows]

    if values[start] and start > 0 and not values[start - 1]:
        return "entered_sign_controlled_area"
    if values[end] and end + 1 < len(values) and not values[end + 1]:
        return "left_sign_controlled_area"
    if all(values[index] for index in active_indices):
        return "remained_inside_sign_controlled_area"
    if not any(values[index] for index in active_indices):
        return "no_sign_controlled_area"

    for index in range(start + 1, end + 1):
        if not values[index - 1] and values[index]:
            return "entered_sign_controlled_area"
        if values[index - 1] and not values[index]:
            return "left_sign_controlled_area"
    return "mixed_sign_controlled_area"


def _describe_range(value_range: Dict[str, Optional[float]], unit: str) -> str:
    low = value_range.get("min")
    high = value_range.get("max")
    if low is None or high is None:
        return f"an unknown {unit}".strip()
    if low == high:
        return f"{_format_number(low)} {unit}".strip()
    return f"{_format_number(low)}-{_format_number(high)} {unit}".strip()


def _max_speed_evidence(
    rows: Sequence[Dict[str, str]], mask: Sequence[bool]
) -> Tuple[Dict[str, Any], str]:
    active = _active_mask(mask)
    speed = _range(_column(rows, "Ego_velocity", active), 3.6)
    limit = _range(_column(rows, "Thres_MaxSpdlim", active, 0))
    peak_row = _row_with_maximum(rows, "Ego_velocity", active)
    peak_limit = _round(_number(rows[peak_row[0]].get("Thres_MaxSpdlim"))) if peak_row else None
    peak_time = _round(_number(rows[peak_row[0]].get("event_time"))) if peak_row else None
    roads = _labels(rows, "Road_type", ROAD_TYPES, active)
    lanes = _labels(rows, "Lane_type", LANE_TYPES, active)
    sign_context = _sign_context(rows, "IsMaxSpdsignArea", active)
    sign_description = next((row.get("Event_description", "") for row in rows if row.get("Event_description")), "")
    sign_effective = _any_nonzero(rows, "Speed_limit_sign_effective", active)
    evidence = {
        "Ego_speed_kph": speed,
        "Applicable_max_speed_limit_kph": limit,
        "Applicable_max_speed_limit_at_peak_speed_kph": peak_limit,
        "Time_of_peak_speed_s": peak_time,
        "Road_types": roads,
        "Lane_types": lanes,
        "Sign_description": sign_description,
        "Speed_limit_sign_effective": sign_effective,
        "Inside_speed_limit_sign_area": _any_nonzero(rows, "IsMaxSpdsignArea", active),
        "Speed_limit_sign_context": sign_context,
    }
    if sign_context == "entered_sign_controlled_area":
        source_text = "The ego vehicle passed a maximum-speed sign and entered its controlled area"
    elif sign_context == "left_sign_controlled_area":
        source_text = "The ego vehicle left the area controlled by a maximum-speed sign"
    elif sign_context == "no_sign_controlled_area":
        source_text = "No maximum-speed sign was active during the decisive interval"
    else:
        source_text = "The ego vehicle remained in an area controlled by a maximum-speed sign"
    scenario = (
        f"{source_text}. It traveled on {_join_labels(lanes, 'an unknown lane')} of "
        f"{_join_labels(roads, 'an unknown road')} at {_describe_range(speed, 'km/h')} "
        f"while the applicable maximum speed limit was {_describe_range(limit, 'km/h')}. "
        f"The recorded sign description was {sign_description or 'not provided'}, and the sign was "
        f"{'treated as effective' if sign_effective else 'not treated as effective'}."
    )
    return evidence, scenario


def _min_speed_evidence(
    rows: Sequence[Dict[str, str]], mask: Sequence[bool]
) -> Tuple[Dict[str, Any], str]:
    active = _active_mask(mask)
    speed = _range(_column(rows, "Ego_velocity", active), 3.6)
    limit = _range(_column(rows, "Thres_MinSpdlim", active, 0))
    minimum_row = _row_with_minimum(rows, "Ego_velocity", active)
    minimum_limit = _round(_number(rows[minimum_row[0]].get("Thres_MinSpdlim"))) if minimum_row else None
    minimum_time = _round(_number(rows[minimum_row[0]].get("event_time"))) if minimum_row else None
    roads = _labels(rows, "Road_type", ROAD_TYPES, active)
    lanes = _labels(rows, "Lane_type", LANE_TYPES, active)
    lane_count = _mode(_column(rows, "LaneNumSameDirection", active, 0))
    main_lane_count = _mode(_column(rows, "mainLaneNum", active, 0))
    lane_index = _mode(_column(rows, "EgoLaneIndex", active, 0))
    special_case = _mode(_column(rows, "Special_case", active, 0))
    if special_case is None:
        special_case = 1 if _any_nonzero(rows, "Congestion", active) else 0
    special_case = int(special_case)
    sign_context = _sign_context(rows, "IsMinSpdsignArea", active)
    evidence = {
        "Ego_speed_kph": speed,
        "Applicable_min_speed_limit_kph": limit,
        "Applicable_min_speed_limit_at_minimum_speed_kph": minimum_limit,
        "Time_of_minimum_speed_s": minimum_time,
        "Road_types": roads,
        "Lane_types": lanes,
        "Same_direction_lane_count": lane_count,
        "Same_direction_main_lane_count": main_lane_count,
        "Ego_lane_index_from_left": lane_index,
        "Special_case": special_case,
        "Inside_speed_limit_sign_area": _any_nonzero(rows, "IsMinSpdsignArea", active),
        "Speed_limit_sign_context": sign_context,
    }
    lane_context = ""
    lane_count_for_context = main_lane_count if main_lane_count is not None else lane_count
    if lane_count_for_context is not None and lane_index is not None:
        lane_context = (
            f" The road had {_format_number(lane_count_for_context)} same-direction main lanes, "
            f"and the ego vehicle occupied lane {_format_number(lane_index)} from the left."
        )
    if sign_context == "no_sign_controlled_area":
        source_text = (
            "No minimum-speed sign was active during the event, so the applicable minimum "
            "speed came from the expressway lane rule"
        )
    elif sign_context == "entered_sign_controlled_area":
        source_text = (
            "The ego vehicle passed a minimum-speed sign and entered its controlled area"
        )
    elif sign_context == "left_sign_controlled_area":
        source_text = "The ego vehicle left the area controlled by a minimum-speed sign"
    else:
        source_text = "The ego vehicle remained in an area controlled by a minimum-speed sign"
    special_case_labels = {
        0: "no special case",
        1: "congestion",
        2: "construction",
        3: "uphill slow driving",
        4: "downhill slow driving",
        5: "curve",
    }
    scenario = (
        f"{source_text}. It traveled on {_join_labels(lanes, 'an unknown lane')} of "
        f"{_join_labels(roads, 'an unknown road')} at {_describe_range(speed, 'km/h')} "
        f"while the applicable minimum speed was {_describe_range(limit, 'km/h')}; "
        f"special case was {special_case_labels.get(special_case, 'unknown')}.{lane_context}"
    )
    return evidence, scenario


def _follow_distance_evidence(
    rows: Sequence[Dict[str, str]], mask: Sequence[bool]
) -> Tuple[Dict[str, Any], str]:
    active = _active_mask(mask)
    minimum_row = _row_with_minimum(rows, "Dis_FV", active, 0)
    distance = _round(minimum_row[1]) if minimum_row else None
    required_at_minimum = _round(
        _number(rows[minimum_row[0]].get("Thres_Dis_FV"))
    ) if minimum_row else None
    speed_at_minimum = _round(
        _number(rows[minimum_row[0]].get("Ego_velocity")) * 3.6
    ) if minimum_row else None
    time_at_minimum = _round(
        _number(rows[minimum_row[0]].get("event_time"))
    ) if minimum_row else None
    threshold = _range(_column(rows, "Thres_Dis_FV", active, 0))
    speed = _range(_column(rows, "Ego_velocity", active), 3.6)
    roads = _labels(rows, "Road_type", ROAD_TYPES, active)
    lanes = _labels(rows, "Lane_type", LANE_TYPES, active)
    congestion = _any_nonzero(rows, "Congestion", active)
    evidence = {
        "Minimum_front_vehicle_distance_m": distance,
        "Required_following_distance_m": threshold,
        "Required_following_distance_at_minimum_m": required_at_minimum,
        "Ego_speed_at_minimum_distance_kph": speed_at_minimum,
        "Time_of_minimum_distance_s": time_at_minimum,
        "Ego_speed_kph": speed,
        "Road_types": roads,
        "Lane_types": lanes,
        "Congestion": congestion,
    }
    scenario = (
        f"The ego vehicle followed a vehicle ahead on {_join_labels(lanes, 'an unknown lane')} "
        f"of {_join_labels(roads, 'an unknown road')} at {_describe_range(speed, 'km/h')}. "
        f"The minimum separation was {_format_number(distance)} m at "
        f"{_format_number(speed_at_minimum)} km/h, compared with a required distance of "
        f"{_format_number(required_at_minimum)} m; traffic was "
        f"{'congested' if congestion else 'not congested'}."
    )
    return evidence, scenario


def _lateral_distance_evidence(
    rows: Sequence[Dict[str, str]], mask: Sequence[bool]
) -> Tuple[Dict[str, Any], str]:
    active = _active_mask(mask)
    left_values = _column(rows, "Dis_LV", active, 0)
    right_values = _column(rows, "Dis_RV", active, 0)
    offsets = [abs(value) for value in _column(rows, "Dis_centerline", active)]
    threshold = _mode(_column(rows, "Thres_min_LatDis", active, 0))
    offset_threshold = _mode(_column(rows, "Thres_Offset_centerline", active, 0))
    left_distance = _round(min(left_values)) if left_values else None
    right_distance = _round(min(right_values)) if right_values else None
    max_offset = _round(max(offsets)) if offsets else None
    avoidance = _any_nonzero(rows, "Is_Lat_avoidance", active)
    evidence = {
        "Minimum_left_vehicle_distance_m": left_distance,
        "Minimum_right_vehicle_distance_m": right_distance,
        "Required_lateral_distance_m": threshold,
        "Maximum_abs_centerline_offset_m": max_offset,
        "Centerline_offset_threshold_m": offset_threshold,
        "Left_lane_line_present": _any_nonzero(rows, "Exist_LeftLine", active),
        "Right_lane_line_present": _any_nonzero(rows, "Exist_RightLine", active),
        "Lateral_avoidance_observed": avoidance,
    }
    distances = []
    if left_distance is not None:
        distances.append(f"{_format_number(left_distance)} m to the left vehicle")
    if right_distance is not None:
        distances.append(f"{_format_number(right_distance)} m to the right vehicle")
    scenario = (
        f"The ego vehicle passed adjacent traffic with "
        f"{_join_labels(distances, 'no valid adjacent-vehicle distance')} and a maximum "
        f"lane-center offset of {_format_number(max_offset)} m. The required lateral clearance "
        f"was {_format_number(threshold)} m"
        f"{', and a lateral avoidance maneuver was observed' if avoidance else ''}."
    )
    return evidence, scenario


def _lane_change_evidence(
    rows: Sequence[Dict[str, str]], mask: Sequence[bool]
) -> Tuple[Dict[str, Any], str]:
    active = _active_mask(mask)
    trigger = [
        _number(row.get("trigger_IMR_44_1"), 0) != 0
        for row in rows
    ]
    maneuver = trigger if any(trigger) else active
    maneuvers = _lane_change_maneuvers(rows, "trigger_IMR_44_1")
    lane_change = maneuvers[0] if maneuvers else {}
    ttc_values = _column(rows, "TTC_FV", maneuver, 0)
    minimum_rear_row = _row_with_minimum(rows, "dis_RVTL", maneuver, 0)
    ttc_threshold = _mode(_column(rows, "Thres_TTC_FV", maneuver, 0))
    rear_threshold = _range(_column(rows, "Thres_dis_RVTL", maneuver, 0))
    rear_distance = _round(minimum_rear_row[1]) if minimum_rear_row else None
    rear_threshold_at_minimum = _round(
        _number(rows[minimum_rear_row[0]].get("Thres_dis_RVTL"))
    ) if minimum_rear_row else None
    rear_gap_time = _round(
        _number(rows[minimum_rear_row[0]].get("event_time"))
    ) if minimum_rear_row else None
    direction = lane_change.get("direction", _overlap_direction(rows, maneuver))
    speed = _range(_column(rows, "Ego_velocity", maneuver), 3.6)
    roads = _labels(rows, "Road_type", ROAD_TYPES, maneuver)
    lanes = _labels(rows, "Lane_type", LANE_TYPES, maneuver)
    evidence = {
        "Lane_change_direction": direction,
        "Lane_line_overlap_start_time_s": lane_change.get("line_overlap_start_time_s"),
        "Cross_line_time_s": lane_change.get("cross_line_time_s"),
        "Lane_change_end_time_s": lane_change.get("end_time_s"),
        "Minimum_front_vehicle_TTC_s": _round(min(ttc_values)) if ttc_values else None,
        "Required_front_vehicle_TTC_s": ttc_threshold,
        "Minimum_target_lane_rear_gap_m": rear_distance,
        "Required_target_lane_rear_gap_m": rear_threshold,
        "Required_target_lane_rear_gap_at_minimum_m": rear_threshold_at_minimum,
        "Time_of_minimum_target_lane_rear_gap_s": rear_gap_time,
        "Front_vehicle_TTC_status": _status_from_column(rows, "com_IMR_44_1_FV", maneuver),
        "Target_lane_rear_gap_status": _status_from_column(rows, "com_IMR_44_1_RVTL", maneuver),
        "Ego_speed_kph": speed,
        "Road_types": roads,
        "Lane_types": lanes,
    }
    scenario = (
        f"On {_join_labels(lanes, 'an unknown lane')} of "
        f"{_join_labels(roads, 'an unknown road')}, the ego vehicle traveled at "
        f"{_describe_range(speed, 'km/h')} and changed lanes to the {direction}. It began "
        f"overlapping the {direction} lane line at "
        f"{_format_seconds(lane_change.get('line_overlap_start_time_s'))} and completed the "
        f"lane-line crossing at {_format_seconds(lane_change.get('cross_line_time_s'))}. "
        f"During the maneuver, the minimum TTC to the front vehicle was "
        f"{_format_number(evidence['Minimum_front_vehicle_TTC_s'])} s against a "
        f"{_format_number(ttc_threshold)} s requirement, and the minimum gap to the target-lane "
        f"rear vehicle was {_format_number(rear_distance)} m against the "
        f"{_format_number(rear_threshold_at_minimum)} m requirement at that moment."
    )
    return evidence, scenario


def _continuous_lane_change_evidence(
    rows: Sequence[Dict[str, str]], mask: Sequence[bool]
) -> Tuple[Dict[str, Any], str]:
    active = _active_mask(mask)
    maneuvers = _lane_change_maneuvers(rows, "trigger_IMR_44_1")
    relevant = maneuvers[:2]
    crossing_times = [
        maneuver["cross_line_time_s"]
        for maneuver in relevant
        if maneuver.get("cross_line_time_s") is not None
    ]
    gap = crossing_times[1] - crossing_times[0] if len(crossing_times) >= 2 else math.nan
    threshold = _mode(_column(rows, "Thres_LaneChangeCross_gap", active, 0))
    same_direction = (
        len(relevant) == 2
        and relevant[0]["direction_code"] != 0
        and relevant[0]["direction_code"] == relevant[1]["direction_code"]
    )
    gap_within_threshold = (
        same_direction
        and math.isfinite(gap)
        and threshold is not None
        and gap < threshold
    )
    compliance = "Violation" if gap_within_threshold else "Compliance"
    evidence = {
        "Lane_change_count": len(maneuvers),
        "First_lane_change": _public_maneuver(relevant[0]) if relevant else None,
        "Second_lane_change": _public_maneuver(relevant[1]) if len(relevant) >= 2 else None,
        "Same_direction_lane_changes": same_direction,
        "Continuous_lane_change_behavior_confirmed": same_direction,
        "Cross_line_times_s": [_round(value) for value in crossing_times],
        "Cross_line_time_gap_s": _round(gap),
        "Maximum_allowed_cross_line_gap_s": threshold,
        "Gap_within_prohibited_interval": gap_within_threshold,
        "_compliance_label": compliance,
        "_anchor_type": "behavior_interval",
        "_anchor_start_time_s": relevant[0]["start_time_s"] if relevant else None,
        "_anchor_end_time_s": relevant[-1]["end_time_s"] if relevant else None,
    }
    if len(relevant) < 2:
        scenario = "Only one complete lane-change maneuver was identified, so continuous lane changing was not confirmed."
    elif not same_direction:
        scenario = (
            f"The first lane change was to the {relevant[0]['direction']} and the second was to "
            f"the {relevant[1]['direction']}. Because the directions were opposite, this pair "
            "does not constitute continuous lane changing, regardless of the crossing interval."
        )
    else:
        scenario = (
            f"The ego vehicle changed lanes to the {relevant[0]['direction']} twice. The completed "
            f"lane-line crossings occurred at {_format_seconds(crossing_times[0])} and "
            f"{_format_seconds(crossing_times[1])}, giving a gap of {_format_number(gap)} s "
            f"against the {_format_number(threshold)} s continuous-lane-change threshold."
        )
    return evidence, scenario


def _road_marking_evidence(
    rows: Sequence[Dict[str, str]], mask: Sequence[bool]
) -> Tuple[Dict[str, Any], str]:
    active = _active_mask(mask)
    interactions: List[Dict[str, Any]] = []
    stage_values = [int(round(_number(row.get("lanechange_stage"), 0))) for row in rows]
    stage_active = [value in (1, 2, 3, 4) for value in stage_values]
    for start, end in _runs(stage_active):
        stages_in_run = set(stage_values[start:end + 1])
        if stages_in_run & {1, 2}:
            direction = "left"
            first_stage, second_stage = 1, 2
            line_column, type_column = "overlap_LeftLine", "MAP_Type_Left1"
        else:
            direction = "right"
            first_stage, second_stage = 3, 4
            line_column, type_column = "overlap_RightLine", "MAP_Type_Right1"
        completed = second_stage in stages_in_run
        first_stage_mask = [
            start <= index <= end and stage_values[index] == first_stage
            for index in range(len(rows))
        ]
        interaction_mask = [
            start <= index <= end for index in range(len(rows))
        ]
        line_types = _labels(rows, type_column, LINE_TYPES, first_stage_mask)
        cross_time = None
        if completed:
            cross_index = next(
                index for index in range(start, end + 1)
                if stage_values[index] == second_stage
            )
            cross_time = _round(_number(rows[cross_index].get("event_time")))
        interactions.append({
            "Side": direction,
            "Action": "crossed" if completed else "overlapped",
            "Line_types": line_types,
            "Overlap_start_time_s": _round(_number(rows[start].get("event_time"))),
            "Cross_line_time_s": cross_time,
            "Interaction_end_time_s": _round(_number(rows[end].get("event_time"))),
            "Initial_line_overlap_detected": _any_nonzero(rows, line_column, interaction_mask),
        })

    if not interactions:
        for side, overlap_column, type_column in (
            ("left", "overlap_LeftLine", "MAP_Type_Left1"),
            ("right", "overlap_RightLine", "MAP_Type_Right1"),
        ):
            side_mask = [
                active[index] and _number(row.get(overlap_column), 0) != 0
                for index, row in enumerate(rows)
            ]
            if any(side_mask):
                run = _longest_run(side_mask)
                interactions.append({
                    "Side": side,
                    "Action": "overlapped",
                    "Line_types": _labels(rows, type_column, LINE_TYPES, side_mask),
                    "Overlap_start_time_s": _round(_number(rows[run[0]].get("event_time"))),
                    "Cross_line_time_s": None,
                    "Interaction_end_time_s": _round(_number(rows[run[1]].get("event_time"))),
                    "Initial_line_overlap_detected": True,
                })

    overlap_values = _column(rows, "Time_ContinuousLineOverlap", active, 0)
    overlap_time = _round(max(overlap_values)) if overlap_values else None
    threshold = _mode(_column(rows, "Thres_MaxContinuousLineOverlap", active, 0))
    evidence = {
        "Line_interactions": interactions,
        "Maximum_continuous_line_overlap_s": overlap_time,
        "Maximum_allowed_line_overlap_s": threshold,
    }
    descriptions = [
        f"{item['Action']} the {item['Side']} {_join_labels(item['Line_types'], 'lane line')}"
        for item in interactions
    ]
    scenario = (
        f"The ego vehicle {_join_labels(descriptions, 'had no classified lane-line interaction')}. "
        "Stage-two overlap with the opposite current-lane boundary is treated as part of the same "
        f"lane crossing. The longest continuous overlap was {_format_number(overlap_time)} s, "
        f"compared with the {_format_number(threshold)} s allowance."
    )
    return evidence, scenario


def _overtake_evidence(
    rows: Sequence[Dict[str, str]], mask: Sequence[bool]
) -> Tuple[Dict[str, Any], str]:
    active = _active_mask(mask)
    maneuvers = _lane_change_maneuvers(rows)
    relevant = maneuvers[:2]
    opposite_directions = (
        len(relevant) == 2
        and relevant[0]["direction_code"] != 0
        and relevant[0]["direction_code"] == -relevant[1]["direction_code"]
    )
    crossing_times = [
        maneuver["cross_line_time_s"]
        for maneuver in relevant
        if maneuver.get("cross_line_time_s") is not None
    ]
    gap = crossing_times[1] - crossing_times[0] if len(crossing_times) >= 2 else math.nan
    threshold = 20.0
    gap_within_threshold = math.isfinite(gap) and 0 < gap < threshold
    direction_code = _mode(_column(rows, "OvertakeDirection", active, 0))
    direction = (
        relevant[0]["direction"]
        if relevant
        else {0: "unknown", 1: "left", 2: "right"}.get(int(direction_code or 0), "unknown")
    )
    target_rows = [
        _number(row.get("FV_ObjID"), -1) > 0
        for row in rows
    ]
    first_start_distance = (
        _number(rows[relevant[0]["start_index"]].get("FV_DistanceX"))
        if relevant else math.nan
    )
    second_start_distance = (
        _number(rows[relevant[1]["start_index"]].get("FV_DistanceX"))
        if len(relevant) >= 2 else math.nan
    )
    target_passed = (
        math.isfinite(first_start_distance)
        and math.isfinite(second_start_distance)
        and first_start_distance > 0
        and second_start_distance < 0
    )
    overtake_confirmed = (
        len(relevant) == 2
        and opposite_directions
        and gap_within_threshold
        and target_passed
    )
    roads = _labels(rows, "Road_type", ROAD_TYPES, active)
    lanes = _labels(rows, "Lane_type", LANE_TYPES, active)
    congestion = _any_nonzero(rows, "Congestion", active)
    speed = _range(_column(rows, "Ego_velocity", active), 3.6)
    evidence = {
        "Overtake_direction": direction,
        "Overtake_behavior_confirmed": overtake_confirmed,
        "First_lane_change": _public_maneuver(relevant[0]) if relevant else None,
        "Second_lane_change": _public_maneuver(relevant[1]) if len(relevant) >= 2 else None,
        "Opposite_lane_change_directions": opposite_directions,
        "Cross_line_time_gap_s": _round(gap),
        "Maximum_overtake_cross_line_gap_s": threshold,
        "Gap_within_overtake_threshold": gap_within_threshold,
        "Target_vehicle_observed": any(target_rows),
        "Target_vehicle_distance_at_first_lane_change_start_m": _round(first_start_distance),
        "Target_vehicle_distance_at_second_lane_change_start_m": _round(second_start_distance),
        "Target_vehicle_passed_between_lane_changes": target_passed,
        "Ego_speed_kph": speed,
        "Road_types": roads,
        "Lane_types": lanes,
        "Congestion": congestion,
        "_anchor_type": "behavior_interval",
        "_anchor_start_time_s": relevant[0]["start_time_s"] if relevant else None,
        "_anchor_end_time_s": relevant[-1]["end_time_s"] if relevant else None,
        "_compliance_label": None if overtake_confirmed else "Unknown",
    }
    if len(relevant) == 2:
        first_position = (
            f"{_format_number(abs(first_start_distance))} m "
            f"{'ahead' if first_start_distance >= 0 else 'behind'}"
        )
        second_position = (
            f"{_format_number(abs(second_start_distance))} m "
            f"{'ahead' if second_start_distance >= 0 else 'behind'}"
        )
        scenario = (
            f"The ego vehicle first changed to the {relevant[0]['direction']} and then to the "
            f"{relevant[1]['direction']}. The completed lane-line crossings occurred at "
            f"{_format_seconds(relevant[0]['cross_line_time_s'])} and "
            f"{_format_seconds(relevant[1]['cross_line_time_s'])}, giving an "
            f"{_format_number(gap)} s gap against the {_format_number(threshold)} s overtake "
            f"threshold. The target vehicle was {first_position} at the first lane-change start "
            f"and {second_position} at the second start, so the behavior was "
            f"{'confirmed as a ' + direction + '-side overtake' if overtake_confirmed else 'not confirmed as an overtake'}."
        )
    else:
        scenario = "Two complete, opposite-direction lane changes were not identified, so an overtake was not confirmed."
    return evidence, scenario


def _generic_violation_reason(statuses: Dict[str, str], _: Dict[str, Any]) -> str:
    reasons = [
        ARTICLE_REASONS[article_id]
        for article_id, status in statuses.items()
        if status == "Violation"
    ]
    return " ".join(reasons)


def _lane_change_violation_reason(statuses: Dict[str, str], evidence: Dict[str, Any]) -> str:
    reasons: List[str] = []
    if evidence.get("Front_vehicle_TTC_status") == "Violation":
        reasons.append("The TTC to the front vehicle was below the required threshold.")
    if evidence.get("Target_lane_rear_gap_status") == "Violation":
        reasons.append("The gap to the rear vehicle in the target lane was below the required threshold.")
    return " ".join(reasons) or _generic_violation_reason(statuses, evidence)


MAX_SPEED_ARTICLES = (
    ArticleRule("IMR_45.1", "Vehicles shall not exceed speed limits indicated by traffic signs or markings.", "trigger_IMR_45_1", "com_IMR_45_1"),
    ArticleRule("IMR_46.3", "A motor vehicle shall not exceed 30 km/h on a sharp turn.", "trigger_IMR_46_3", "com_IMR_46_3"),
    ArticleRule("IMR_46.4", "A motor vehicle shall not exceed 30 km/h on a narrow road or bridge.", "trigger_IMR_46_4", "com_IMR_46_4"),
    ArticleRule("IMR_46.5", "A motor vehicle shall not exceed 30 km/h while descending a steep slope.", "trigger_IMR_46_5", "com_IMR_46_5"),
    ArticleRule("IMR_78.1", "A small passenger vehicle on an expressway shall not exceed 120 km/h.", "trigger_IMR_78_1", "com_IMR_78_1"),
    ArticleRule("IMR_78.3", "A vehicle shall follow the maximum speed indicated by the road speed-limit sign.", "trigger_IMR_78_3", "com_IMR_78_3"),
)

MIN_SPEED_ARTICLES = (
    ArticleRule("IMR_78.2", "A vehicle on an expressway shall not travel below 60 km/h when conditions permit.", "trigger_IMR_78_2", "com_IMR_78_2"),
    ArticleRule("IMR_78.4", "A vehicle shall follow the minimum speed indicated by the road speed-limit sign.", "trigger_IMR_78_4", "com_IMR_78_4"),
    ArticleRule("IMR_78.5", "On a two-lane expressway, the minimum speed in the left lane is 100 km/h.", "trigger_IMR_78_5", "com_IMR_78_5"),
    ArticleRule("IMR_78.6", "On an expressway with at least three lanes, the minimum speed in the leftmost lane is 110 km/h.", "trigger_IMR_78_6", "com_IMR_78_6"),
    ArticleRule("IMR_78.7", "On an expressway with at least three lanes, the minimum speed in a middle lane is 90 km/h.", "trigger_IMR_78_7", "com_IMR_78_7"),
)

FOLLOW_DISTANCE_ARTICLES = (
    ArticleRule("IMR_80.1", "At 100 km/h or more on an expressway, a vehicle shall keep at least 100 m from the vehicle ahead.", "trigger_IMR_80_1", "com_IMR_80_1"),
    ArticleRule("IMR_80.2", "Below 100 km/h on an expressway, a vehicle shall keep at least 50 m from the vehicle ahead.", "trigger_IMR_80_2", "com_IMR_80_2"),
)

LATERAL_DISTANCE_ARTICLES = (
    ArticleRule("OSP_8.2.1", "A vehicle should maintain more than 1.5 m lateral clearance from vehicles on either side.", "trigger_OSP_8_2_1", "com_OSP_8_2_1"),
)

LANE_CHANGE_ARTICLES = (
    ArticleRule("IMR_44.1", "A vehicle changing lanes shall not interfere with vehicles traveling normally in the relevant lane.", "trigger_IMR_44_1", "com_IMR_44_1"),
)

CONTINUOUS_LANE_CHANGE_ARTICLES = (
    ArticleRule("OSP_9.3.1", "A vehicle should not change across two or more lanes consecutively.", "trigger_OSP_9_3_1", "com_OSP_9_3_1"),
)

ROAD_MARKING_ARTICLES = (
    ArticleRule("TSM_4.3.1", "A dashed lane line may be crossed briefly only when safety is ensured.", "trigger_TSM_4_3_1", "com_TSM_4_3_1"),
    ArticleRule("TSM_4.5.2", "Vehicles shall not cross a solid lane boundary.", "trigger_TSM_4_5_2", "com_TSM_4_5_2"),
    ArticleRule("TSM_4.5.3", "Vehicles shall follow channelizing lines and shall not drive on or cross them.", "trigger_TSM_4_5_3", "com_TSM_4_5_3"),
)

OVERTAKE_ARTICLES = (
    ArticleRule("IMR_47.4", "A vehicle shall overtake the vehicle ahead from the left side.", "", "com_IMR_47_4"),
    ArticleRule("IMR_82.5", "A vehicle on an expressway shall not overtake on ramps, acceleration lanes, or deceleration lanes.", "", "com_IMR_82_5"),
    ArticleRule("TSL_43.6", "A vehicle shall not overtake while traveling through a tunnel.", "", "com_TSL_43_6"),
    ArticleRule("TSL_43.8", "A vehicle shall not overtake on a congested urban road section.", "", "com_TSL_43_8"),
)


CATEGORIES: Dict[str, CategoryConfig] = {
    "max_speed": CategoryConfig(
        "max_speed", "zEvent_MaxSpdlim", "MaxSpdlim_events.csv",
        "MaxSpdlim_event_*_EvidenceChain.csv", r"MaxSpdlim_event_(\d+)_EvidenceChain\.csv",
        "MaxSpdlim_event_{event_number}_record.json",
        "The interval is taken from the longest decisive maximum-speed compliance segment in the evidence chain.",
        MAX_SPEED_ARTICLES, _max_speed_evidence, _generic_violation_reason,
        "Reduce speed to the applicable maximum limit and respond promptly to speed-limit changes.",
    ),
    "min_speed": CategoryConfig(
        "min_speed", "zEvent_MinSpdlim", "MinSpdlim_events.csv",
        "MinSpdlim_event_*_EvidenceChain.csv", r"MinSpdlim_event_(\d+)_EvidenceChain\.csv",
        "MinSpdlim_event_{event_number}_record.json",
        "The interval is taken from the longest decisive minimum-speed compliance segment in the evidence chain.",
        MIN_SPEED_ARTICLES, _min_speed_evidence, _generic_violation_reason,
        "When traffic and safety conditions permit, maintain at least the applicable minimum speed.",
    ),
    "follow_distance": CategoryConfig(
        "follow_distance", "zEvent_FollowDis", "FollowDis_events.csv",
        "FollowDis_event_*_EvidenceChain.csv", r"FollowDis_event_(\d+)_EvidenceChain\.csv",
        "FollowDis_event_{event_number}_record.json",
        "The interval is taken from the longest decisive following-distance compliance segment in the evidence chain.",
        FOLLOW_DISTANCE_ARTICLES, _follow_distance_evidence, _generic_violation_reason,
        "Increase the gap to the preceding vehicle until it meets the applicable distance requirement.",
    ),
    "lateral_distance": CategoryConfig(
        "lateral_distance", "zEvent_LateralDis", "LateralDis_events.csv",
        "LateralDis_event_*_EvidenceChain.csv", r"LateralDis_event_(\d+)_EvidenceChain\.csv",
        "LateralDis_event_{event_number}_record.json",
        "The interval is taken from the longest decisive lateral-clearance compliance segment in the evidence chain.",
        LATERAL_DISTANCE_ARTICLES, _lateral_distance_evidence, _generic_violation_reason,
        "Maintain sufficient lateral clearance from adjacent vehicles and a stable lane position when safe.",
    ),
    "lane_change": CategoryConfig(
        "lane_change", "zEvent_LaneChange", "lane_change_events.csv",
        "lane_change_event_*_EvidenceChain.csv", r"lane_change_event_(\d+)_EvidenceChain\.csv",
        "lane_change_event_{event_number}_record.json",
        "The interval is taken from the longest decisive lane-change compliance segment in the evidence chain.",
        LANE_CHANGE_ARTICLES, _lane_change_evidence, _lane_change_violation_reason,
        "Change lanes only after ensuring adequate TTC to the front vehicle and sufficient gap to the target-lane rear vehicle.",
    ),
    "continuous_lane_change": CategoryConfig(
        "continuous_lane_change", "zEvent_ContinueLaneChange", "ContinueLC_events.csv",
        "ContinueLC_event_*_EvidenceChain.csv", r"ContinueLC_event_(\d+)_EvidenceChain\.csv",
        "ContinueLC_event_{event_number}_record.json",
        "The interval covers the two lane-change maneuvers used to determine whether continuous lane changing occurred.",
        CONTINUOUS_LANE_CHANGE_ARTICLES, _continuous_lane_change_evidence, _generic_violation_reason,
        "Complete one lane change, stabilize in the lane, and avoid crossing another lane within the prohibited interval.",
    ),
    "road_marking": CategoryConfig(
        "road_marking", "zEvent_RoadMarking", "RoadMarking_events.csv",
        "RoadMarking_event_*_EvidenceChain.csv", r"RoadMarking_event_(\d+)_EvidenceChain\.csv",
        "RoadMarking_event_{event_number}_record.json",
        "The interval is taken from the longest decisive road-marking compliance segment in the evidence chain.",
        ROAD_MARKING_ARTICLES, _road_marking_evidence, _generic_violation_reason,
        "Follow lane markings, keep permitted dashed-line overlap brief, and do not cross solid or channelizing lines.",
    ),
    "overtake": CategoryConfig(
        "overtake", "zEvent_Overtake", "Overtake_events.csv",
        "Overtake_event_*_EvidenceChain.csv", r"Overtake_event_(\d+)_EvidenceChain\.csv",
        "Overtake_event_{event_number}_record.json",
        "The interval covers the two lane changes used to confirm the complete overtaking behavior.",
        OVERTAKE_ARTICLES, _overtake_evidence, _generic_violation_reason,
        "Overtake from the permitted side only under suitable road, lane, and traffic conditions.",
        shared_trigger_column="trigger_overtake",
    ),
}


def _read_csv(path: Path) -> List[Dict[str, str]]:
    last_error: Optional[UnicodeDecodeError] = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError as error:
            last_error = error
    if last_error is not None:
        raise last_error
    return []


def _date_directory(evidence_path: Path) -> str:
    for parent in evidence_path.parents:
        if re.fullmatch(r"\d{8}", parent.name):
            return parent.name
    return ""


def _segment_start(segment_name: str) -> Optional[datetime]:
    match = re.search(r"(\d{8})_(\d{6})_(\d{3})", segment_name)
    if not match:
        return None
    return datetime.strptime("".join(match.groups()), "%Y%m%d%H%M%S%f")


def _parse_event_datetime(raw: str, date_name: str, segment_name: str) -> Optional[datetime]:
    value = str(raw or "").strip()
    if not value:
        return None
    normalized = value.replace("/", "-")
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y%m%d %H:%M:%S.%f",
        "%Y%m%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            pass

    if not re.fullmatch(r"\d{8}", date_name):
        return None
    date_value = datetime.strptime(date_name, "%Y%m%d")
    parts = value.split(":")
    try:
        if len(parts) == 3:
            hour, minute, second = int(parts[0]), int(parts[1]), float(parts[2])
            return date_value.replace(
                hour=hour,
                minute=minute,
                second=int(second),
                microsecond=round((second % 1) * 1_000_000),
            )
        if len(parts) == 2:
            minute, second = int(parts[0]), float(parts[1])
            segment_start = _segment_start(segment_name)
            base_hour = segment_start.hour if segment_start else 0
            candidate = date_value.replace(
                hour=base_hour,
                minute=minute,
                second=int(second),
                microsecond=round((second % 1) * 1_000_000),
            )
            if segment_start:
                candidates = [candidate - timedelta(hours=1), candidate, candidate + timedelta(hours=1)]
                return min(candidates, key=lambda item: abs(item - segment_start))
            return candidate
    except (ValueError, OverflowError):
        return None
    return None


def _metadata(
    evidence_path: Path,
    event_number: int,
    config: CategoryConfig,
    event_cache: Dict[Path, List[Dict[str, str]]],
) -> Tuple[str, str]:
    date_name = _date_directory(evidence_path)
    date_text = (
        datetime.strptime(date_name, "%Y%m%d").strftime("%Y-%m-%d")
        if date_name
        else ""
    )
    event_path = evidence_path.parent / config.event_csv
    if not event_path.exists():
        return date_text, ""
    if event_path not in event_cache:
        event_cache[event_path] = _read_csv(event_path)
    event_rows = event_cache[event_path]
    event_row = next(
        (row for row in event_rows if int(_number(row.get("event_num"), -1)) == event_number),
        None,
    )
    if event_row is None:
        return date_text, ""
    start = _parse_event_datetime(event_row.get("dt_start", ""), date_name, evidence_path.parent.name)
    end = _parse_event_datetime(event_row.get("dt_end", ""), date_name, evidence_path.parent.name)
    if start is not None:
        date_text = start.strftime("%Y-%m-%d")
    if start is None or end is None:
        return date_text, ""
    return date_text, f"{start.strftime('%H:%M:%S.%f')[:-3]} -- {end.strftime('%H:%M:%S.%f')[:-3]}"


def _location_for_root(root: Path, override: str) -> str:
    if override:
        return override
    root_name = root.name.lower()
    if "changchun" in root_name:
        return "China, Changchun"
    if "nanjing" in root_name:
        return "China, Nanjing"
    return "China"


def _enrich_lane_change_context(
    evidence_path: Path,
    rows: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    map_path = evidence_path.with_name(
        evidence_path.name.replace("_EvidenceChain.csv", "_MapInfo.csv")
    )
    if not map_path.exists():
        return rows
    map_rows = _read_csv(map_path)
    if len(map_rows) != len(rows):
        return rows

    enriched = [dict(row) for row in rows]
    for row, map_row in zip(enriched, map_rows):
        if "Road_type" not in row:
            row["Road_type"] = map_row.get("Road_type", "")
        if "Lane_type" not in row:
            row["Lane_type"] = map_row.get("Lane_type_CurrentLane", "")
    return enriched


def _record(
    evidence_path: Path,
    event_number: int,
    config: CategoryConfig,
    location: str,
    event_cache: Dict[Path, List[Dict[str, str]]],
) -> Dict[str, Any]:
    rows = _read_csv(evidence_path)
    if config.key == "lane_change":
        rows = _enrich_lane_change_context(evidence_path, rows)
    compliance_label, statuses, decision_mask = _decision(rows, config)
    active = _active_mask(decision_mask)
    evidence, scenario = config.evidence_builder(rows, active)
    evidence_compliance = evidence.pop("_compliance_label", None)
    anchor_override_type = evidence.pop("_anchor_type", None)
    anchor_override_start = evidence.pop("_anchor_start_time_s", None)
    anchor_override_end = evidence.pop("_anchor_end_time_s", None)
    if evidence_compliance:
        compliance_label = evidence_compliance
        status_article_ids = list(statuses) or [article.article_id for article in config.articles]
        statuses = {
            article_id: compliance_label
            for article_id in status_article_ids
        }
    evidence = {"Article_status": statuses, **evidence}

    run = _longest_run(decision_mask)
    if run is None:
        trigger_start = "-1 s"
        trigger_end = "-1 s"
        anchor_type = "unconfirmed"
    else:
        trigger_start = _format_seconds(rows[run[0]].get("event_time"))
        trigger_end = _format_seconds(rows[run[1]].get("event_time"))
        anchor_type = "violation_interval" if compliance_label == "Violation" else "trigger_interval"
    if anchor_override_start is not None and anchor_override_end is not None:
        trigger_start = _format_seconds(anchor_override_start)
        trigger_end = _format_seconds(anchor_override_end)
        anchor_type = anchor_override_type or "behavior_interval"

    selected_articles = [
        article
        for article in config.articles
        if article.article_id in statuses
    ]
    date_text, time_text = _metadata(evidence_path, event_number, config, event_cache)

    if compliance_label == "Violation":
        violation_reason = config.violation_reason_builder(statuses, evidence)
        driving_suggestion = config.driving_suggestion
    elif compliance_label == "Compliance":
        violation_reason = "None"
        driving_suggestion = "Maintain the observed compliant driving behavior."
    else:
        violation_reason = "No triggered compliance decision was found in the evidence chain."
        driving_suggestion = "Review or regenerate the evidence chain before making a compliance determination."

    return {
        "Location": location,
        "Date": date_text,
        "Time": time_text,
        "Article": {
            "ID": " & ".join(article.article_id for article in selected_articles),
            "Text": [article.text for article in selected_articles],
        },
        "EventAnchor": {
            "Anchor_type": anchor_type,
            "Trigger_start_time": trigger_start,
            "Trigger_end_time": trigger_end,
            "Description": config.anchor_description,
        },
        "Evidence": evidence,
        "Result": {
            "Compliance_label": compliance_label,
            "Violation_reason": violation_reason,
            "Driving_suggestion": driving_suggestion,
            "Scenario_description": scenario,
        },
    }


def generate_category(
    root: Path,
    category: str,
    dates: Optional[Sequence[str]] = None,
    location: str = "",
    event_root: str = "",
) -> List[Path]:
    config = CATEGORIES[category]
    if event_root:
        config = replace(config, event_root=event_root)
    selected_dates = set(dates or [])
    location_text = _location_for_root(root, location)
    event_cache: Dict[Path, List[Dict[str, str]]] = {}
    written: List[Path] = []

    date_dirs = sorted(
        path
        for path in root.iterdir()
        if path.is_dir() and re.fullmatch(r"\d{8}", path.name)
    )
    for date_dir in date_dirs:
        if selected_dates and date_dir.name not in selected_dates:
            continue
        event_root = date_dir / config.event_root
        if not event_root.is_dir():
            continue
        segment_dirs = [event_root]
        segment_dirs.extend(sorted(path for path in event_root.iterdir() if path.is_dir()))
        for segment_dir in segment_dirs:
            for evidence_path in sorted(segment_dir.glob(config.evidence_pattern)):
                match = re.fullmatch(config.evidence_regex, evidence_path.name)
                if not match:
                    continue
                event_number = int(match.group(1))
                record = _record(
                    evidence_path,
                    event_number,
                    config,
                    location_text,
                    event_cache,
                )
                output_path = segment_dir / config.record_name.format(event_number=event_number)
                output_path.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                written.append(output_path)
    return written


def _target_dates(cli_dates: str) -> List[str]:
    value = cli_dates or os.environ.get("TLCD_TARGET_DATES", "")
    return [item.strip() for item in value.split(",") if item.strip()]


def run_category_cli(category: str) -> int:
    parser = argparse.ArgumentParser(
        description=f"Generate {category} record.json files from EvidenceChain CSV files."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Dataset root directory.")
    parser.add_argument("--dates", default="", help="Comma-separated YYYYMMDD date directories.")
    parser.add_argument("--location", default="", help="Override the JSON Location value.")
    args = parser.parse_args()

    if not args.root.is_dir():
        parser.error(f"Dataset root does not exist: {args.root}")
    written = generate_category(
        args.root,
        category,
        dates=_target_dates(args.dates),
        location=args.location,
    )
    print(f"{category}: wrote {len(written)} record.json files.")
    return 0


def run_all_cli() -> int:
    parser = argparse.ArgumentParser(
        description="Generate all S7 record.json files from EvidenceChain CSV files."
    )
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Dataset root directory.")
    parser.add_argument("--dates", default="", help="Comma-separated YYYYMMDD date directories.")
    parser.add_argument("--location", default="", help="Override the JSON Location value.")
    args = parser.parse_args()

    if not args.root.is_dir():
        parser.error(f"Dataset root does not exist: {args.root}")
    dates = _target_dates(args.dates)
    total = 0
    for category in CATEGORIES:
        written = generate_category(args.root, category, dates=dates, location=args.location)
        total += len(written)
        print(f"{category}: wrote {len(written)} record.json files.")
    print(f"all categories: wrote {total} record.json files.")
    return 0
