#!/usr/bin/env python3
"""Audit and summarize TLCD event-scene diversity from record.json files.

Primary scene labels are mutually exclusive within each event category. Special
scene labels are multi-label; therefore their percentages need not sum to 100%.
"""

from __future__ import annotations

import csv
import json
import os
import re
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DATASET_ROOT = Path(
    os.environ.get("TLCD_DATASET_ROOT", REPOSITORY_ROOT / "Dataset")
).expanduser()
OUTPUT_DIR = Path(
    os.environ.get(
        "TLCD_SCENE_OUTPUT_DIR", REPOSITORY_ROOT / "statistics" / "scene_diversity"
    )
).expanduser()
EVENT_MANIFEST = Path(
    os.environ.get(
        "TLCD_EVENT_MANIFEST", REPOSITORY_ROOT / "statistics" / "event_manifest.csv"
    )
).expanduser()
CITY_MAP = {"Changchun_valid": "Changchun", "Nanjing_valid": "Nanjing"}
CATEGORY_ORDER = [
    "MaxSpdlim",
    "MinSpdlim",
    "FollowDis",
    "LateralDis",
    "LaneChange",
    "ContinueLaneChange",
    "RoadMarking",
    "Overtake",
]
JSON_PREFIX = {"LaneChange": "lane_change", "ContinueLaneChange": "ContinueLC"}


PRIMARY_LABELS = {
    "max_lane_sign": ("Lane-level maximum-speed sign", "车道级最高限速标志控制"),
    "max_road_sign": ("Road-level roadside maximum-speed sign", "路侧道路/路段级最高限速标志控制"),
    "max_sign_unspecified": ("Maximum-speed sign, level unspecified", "最高限速标志控制（层级无法区分）"),
    "max_map": ("Map-based maximum-speed control", "地图控制的最高限速"),
    "max_no_control": ("No explicit maximum-speed-limit control", "无明显最高限速控制"),
    "max_special_rule": ("Special statutory maximum-speed scenario", "特殊法定最高限速场景"),
    "max_other": ("Other/unclear maximum-speed scenario", "其他/无法判定的最高限速场景"),
    "min_lane_sign": ("Lane-level minimum-speed sign", "车道级最低限速标志控制"),
    "min_road_sign": ("Road-level roadside minimum-speed sign", "路侧道路/路段级最低限速标志控制"),
    "min_sign_unspecified": ("Minimum-speed sign, level unspecified", "最低限速标志控制（层级无法区分）"),
    "min_no_sign_regular": ("Conventional event without a minimum-speed sign", "无最低限速标志控制的常规场景"),
    "min_special_congestion": ("Special scenario: congestion", "特殊场景：拥堵"),
    "min_special_construction": ("Special scenario: construction", "特殊场景：施工"),
    "min_special_uphill": ("Special scenario: uphill slow driving", "特殊场景：上坡低速"),
    "min_special_downhill": ("Special scenario: downhill slow driving", "特殊场景：下坡低速"),
    "min_special_curve": ("Special scenario: curve", "特殊场景：弯道"),
    "min_special_other": ("Other special minimum-speed scenario", "其他特殊最低限速场景"),
    "min_other": ("Other/unclear minimum-speed scenario", "其他/无法判定的最低限速场景"),
    "follow_continuous": ("Continuous following throughout", "全程跟车"),
    "follow_ego_lane_change": ("Ego-vehicle lane change", "自车换道"),
    "follow_lead_cut_out": ("Lead-vehicle cut-out", "前车切出（cut-out）"),
    "follow_adjacent_cut_in": ("Adjacent-vehicle cut-in", "邻车切入（cut-in）"),
    "follow_multiple_changes": ("Multiple following-target changes", "多次跟车目标切换/复合交互"),
    "follow_no_clear_lead": ("No clear same-lane lead vehicle", "无明确同车道前车"),
    "follow_measurement_unclear": ("Following target unresolved from available evidence", "跟车目标无法由现有证据确认"),
    "follow_other": ("Other/unclear following interaction", "其他/无法判定的跟车交互"),
    "lat_left_passes_ego": ("Left-side vehicle passes ego", "左侧邻车超越自车"),
    "lat_ego_passes_left": ("Ego passes left-side vehicle", "左侧邻车被自车超越"),
    "lat_right_passes_ego": ("Right-side vehicle passes ego", "右侧邻车超越自车"),
    "lat_ego_passes_right": ("Ego passes right-side vehicle", "右侧邻车被自车超越"),
    "lat_stable_relative": ("Stable relative longitudinal position", "相对纵向位置稳定"),
    "lat_separating_no_pass": ("Vehicles separate without a pass", "车辆逐渐远离且未发生超越"),
    "lat_incomplete_pass": ("Approach/partial pass not completed", "接近或尝试超越但未完成"),
    "lat_non_passing_interaction": ("Adjacent-vehicle interaction without a completed pass", "未形成完整超越的相邻车交互"),
    "lat_complex_interaction": ("Complex or multi-vehicle lateral interaction", "复合/多车横向交互"),
    "lat_other": ("Other/unclear lateral interaction", "其他/无法判定的横向交互"),
    "lc_left": ("Completed left lane change", "向左换道"),
    "lc_right": ("Completed right lane change", "向右换道"),
    "lc_line_only": ("Line overlap without completed lane change", "压线但未完成换道"),
    "lc_other": ("Other/unclear lane-change event", "其他/无法判定的换道事件"),
    "clc_left_left": ("Left then left", "连续向左换道"),
    "clc_right_right": ("Right then right", "连续向右换道"),
    "clc_left_right": ("Left then right", "先左后右换道"),
    "clc_right_left": ("Right then left", "先右后左换道"),
    "clc_other": ("Other/unclear consecutive-lane-change sequence", "其他/无法判定的连续换道"),
    "mark_dashed": ("Dashed line", "压/跨虚线"),
    "mark_solid": ("Solid line", "压/跨实线"),
    "mark_other_line": ("Other or mixed line", "压/跨其他或混合标线"),
    "mark_unclear": ("Line type unavailable", "标线类型无法判定"),
    "overtake_left": ("Confirmed left-side overtake", "左侧超车"),
    "overtake_right": ("Confirmed right-side overtake", "右侧超车"),
    "overtake_lane_change_only": ("Left-right lane changes without overtaking", "仅左右换道未超车"),
    "overtake_other": ("Other/unclear overtaking event", "其他/无法判定的超车事件"),
}

SPECIAL_LABELS = {
    "multi_lane": ("Multi-lane road", "多车道道路"),
    "single_lane": ("Single-lane road", "单车道道路"),
    "single_carriageway": ("Single carriageway", "单幅道路"),
    "ramp": ("Ramp", "包含上/下匝道"),
    "accel_decel_lane": ("Acceleration/deceleration lane", "加/减速车道"),
    "jct_intersection": ("JCT/intersection link", "JCT/交叉连接道"),
    "tunnel": ("Tunnel", "隧道"),
    "construction": ("Construction", "施工路段"),
    "night": ("Night", "夜间"),
    "slope": ("Slope/grade", "坡道"),
    "curve": ("Curve", "弯道"),
    "congestion": ("Congestion", "拥堵"),
    "toll": ("Toll area/booth", "收费站/收费区"),
    "bridge_elevated": ("Bridge/elevated road", "桥梁/高架道路"),
    "adverse_weather": ("Rain, wet road, fog, or low visibility", "雨天/湿滑/雾/低能见度"),
}

DEPRECATED_SPECIAL_LABELS = {
    "service_area": "服务区/休息区标志（未实际驶入）",
    "auxiliary_lane": "辅助/特殊用途车道（忽略该属性）",
    "snow_ice": "冰雪警示标志（无实际冰雪路况）",
}


# Authoritative manual review of the 14 double-lane-change candidates, in the
# numbered order supplied to the author. These labels override the structured
# Overtake_behavior_confirmed field for the three confirmed overtakes while
# retaining the other 11 events as lane-change-only sequences.
MANUAL_OVERTAKE_REVIEW = {
    "Changchun_valid/08_Overtake/D-6.0-PP16-20240912_101237_857-20240912_101338_335_CSV/event_01/Overtake_event_1_record.json": (1, "overtake_lane_change_only"),
    "Changchun_valid/08_Overtake/D-6.0-PP16-20240929_130548_112-20240929_131048_177_CSV/event_01/Overtake_event_1_record.json": (2, "overtake_lane_change_only"),
    "Changchun_valid/08_Overtake/D-6.0-PP16-20240929_134048_173-20240929_134548_205_CSV/event_01/Overtake_event_1_record.json": (3, "overtake_lane_change_only"),
    "Changchun_valid/08_Overtake/D-6.0-PP16-20240929_134048_173-20240929_134548_205_CSV/event_02/Overtake_event_2_record.json": (4, "overtake_lane_change_only"),
    "Changchun_valid/08_Overtake/D-6.0-PP16-20240929_140048_163-20240929_140548_171_CSV/event_01/Overtake_event_1_record.json": (5, "overtake_lane_change_only"),
    "Changchun_valid/08_Overtake/D-6.0-PP16-20240929_142048_201-20240929_142548_167_CSV/event_01/Overtake_event_1_record.json": (6, "overtake_lane_change_only"),
    "Changchun_valid/08_Overtake/D-6.0-PP16-20240929_143548_190-20240929_144048_269_CSV/event_01/Overtake_event_1_record.json": (7, "overtake_lane_change_only"),
    "Nanjing_valid/08_Overtake/D-6.0-22-20240930_104634_287-20240930_105134_257_CSV/event_01/Overtake_event_1_record.json": (8, "overtake_lane_change_only"),
    "Nanjing_valid/08_Overtake/D-6.0-22-20240930_105634_290-20240930_110134_260_CSV/event_01/Overtake_event_1_record.json": (9, "overtake_left"),
    "Nanjing_valid/08_Overtake/D-6.0-22-20241008_160537_085-20241008_161037_124_CSV/event_01/Overtake_event_1_record.json": (10, "overtake_lane_change_only"),
    "Nanjing_valid/08_Overtake/D-6.0-27-20241012_144945_751-20241012_145445_738_CSV/event_01/Overtake_event_1_record.json": (11, "overtake_lane_change_only"),
    "Nanjing_valid/08_Overtake/D-6.0-31-20241022_142014_697-20241022_142514_689_CSV/event_01/Overtake_event_1_record.json": (12, "overtake_lane_change_only"),
    "Nanjing_valid/08_Overtake/D-6.0-31-20241023_093700_685-20241023_094200_692_CSV/event_04/Overtake_event_4_record.json": (13, "overtake_right"),
    "Nanjing_valid/08_Overtake/D-6.0-31-20241023_094200_692-20241023_094700_700_CSV/event_01/Overtake_event_1_record.json": (14, "overtake_right"),
}


# Author-approved review of the 11 events that genuinely contain explicit
# minimum-speed-sign control. Review indices follow mins4_event_list.csv.
MANUAL_MIN_SIGN_REVIEW = {
    "Changchun_valid/02_MinSpdlim/D-6.0-OTT73-20240827_150827_278-20240827_151327_264_CSV/event_01/MinSpdlim_event_1_record.json": (2, "min_road_sign"),
    "Changchun_valid/02_MinSpdlim/D-6.0-PP16-20240919_164252_218-20240919_164752_264_CSV/event_06/MinSpdlim_event_6_record.json": (29, "min_road_sign"),
    "Changchun_valid/02_MinSpdlim/D-6.0-PP16-20240929_142548_169-20240929_143048_185_CSV/event_02/MinSpdlim_event_2_record.json": (51, "min_road_sign"),
    "Nanjing_valid/02_MinSpdlim/D-6.0-22-20240910_112644_238-20240910_113144_234_CSV/event_01/MinSpdlim_event_1_record.json": (71, "min_road_sign"),
    "Nanjing_valid/02_MinSpdlim/D-6.0-22-20240910_151638_769-20240910_152138_740_CSV/event_01/MinSpdlim_event_1_record.json": (75, "min_road_sign"),
    "Nanjing_valid/02_MinSpdlim/D-6.0-22-20240925_154851_926-20240925_155351_837_CSV/event_02/MinSpdlim_event_2_record.json": (83, "min_road_sign"),
    "Nanjing_valid/02_MinSpdlim/D-6.0-22-20241008_150146_628-20241008_150646_637_CSV/event_01/MinSpdlim_event_1_record.json": (87, "min_lane_sign"),
    "Nanjing_valid/02_MinSpdlim/D-6.0-28-20241008_155411_632-20241008_155911_620_CSV/event_02/MinSpdlim_event_2_record.json": (124, "min_lane_sign"),
    "Nanjing_valid/02_MinSpdlim/D-6.0-28-20241008_160411_644-20241008_160911_617_CSV/event_02/MinSpdlim_event_2_record.json": (126, "min_road_sign"),
    "Nanjing_valid/02_MinSpdlim/D-6.0-31-20241021_161244_805-20241021_161744_900_CSV/event_04/MinSpdlim_event_4_record.json": (131, "min_road_sign"),
    "Nanjing_valid/02_MinSpdlim/D-6.0-31-20241023_093700_685-20241023_094200_692_CSV/event_04/MinSpdlim_event_4_record.json": (142, "min_road_sign"),
}


@dataclass
class Classification:
    code: str
    confidence: str
    rule: str
    evidence: str


def scalar_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return " | ".join(scalar_text(item) for item in value)
    if isinstance(value, dict):
        return " | ".join(f"{key}: {scalar_text(item)}" for key, item in value.items())
    return str(value)


def get_in(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    value: Any = data
    for key in keys:
        if not isinstance(value, dict):
            return default
        value = value.get(key, default)
    return value


def short_evidence(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def discover_events() -> list[dict[str, str]]:
    # The manifest stores paths only, not scene classifications. Reusing it
    # avoids a very slow recursive directory walk on the network volume while
    # every record.json is still reopened below, so updated JSON content is
    # always reflected in this analysis.
    if EVENT_MANIFEST.exists():
        with EVENT_MANIFEST.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        events = []
        for row in rows:
            path = row["json_path"]
            events.append(
                {
                    "city": row["city"],
                    "category": row["category"],
                    "segment": row["segment"],
                    "event_dir": row["event_dir"],
                    "path": path,
                    "event_id": row["event_id"],
                }
            )
        return events

    events: list[dict[str, str]] = []
    for city_dir, city in CITY_MAP.items():
        city_path = DATASET_ROOT / city_dir
        for category_dir in sorted(city_path.iterdir()):
            if not category_dir.is_dir() or "_" not in category_dir.name:
                continue
            category = category_dir.name.split("_", 1)[1]
            if category not in CATEGORY_ORDER:
                continue
            prefix = JSON_PREFIX.get(category, category)
            for segment in sorted(item for item in category_dir.iterdir() if item.is_dir()):
                for event_dir in sorted(item for item in segment.iterdir() if item.is_dir() and item.name.startswith("event_")):
                    suffix = event_dir.name.split("_", 1)[1]
                    candidates = [
                        event_dir / f"{prefix}_event_{suffix}_record.json",
                    ]
                    path = next((item for item in candidates if item.exists()), candidates[0])
                    events.append(
                        {
                            "city": city,
                            "category": category,
                            "segment": segment.name,
                            "event_dir": event_dir.name,
                            "path": str(path),
                            "event_id": str(path.relative_to(DATASET_ROOT)),
                        }
                    )
    return events


def load_event(event: dict[str, str]) -> tuple[dict[str, str], dict[str, Any] | None, str]:
    try:
        with open(event["path"], "r", encoding="utf-8-sig") as handle:
            return event, json.load(handle), ""
    except Exception as exc:
        return event, None, f"{type(exc).__name__}: {exc}"


def combined_description(data: dict[str, Any]) -> str:
    return " | ".join(
        part
        for part in [
            scalar_text(get_in(data, "Result", "Scenario_description")),
            scalar_text(get_in(data, "Result", "Scenario_description_VLM")),
            scalar_text(get_in(data, "Result", "Violation_reason")),
        ]
        if part
    )


def article_ids(data: dict[str, Any]) -> set[str]:
    text = scalar_text(get_in(data, "Article", "ID"))
    return {part.strip() for part in re.split(r"\s*(?:&|;|\|)\s*", text) if part.strip()}


def classify_max(data: dict[str, Any]) -> Classification:
    text = combined_description(data)
    lower = text.lower()
    articles = article_ids(data)
    # IMR_78.1 is the statutory motorway maximum used when no external
    # maximum-speed-limit information governs the event. It takes precedence
    # over generic map/sign wording in the generated description.
    if "IMR_78.1" in articles:
        return Classification("max_no_control", "structured", "MAX00", "Article.ID contains IMR_78.1")
    if re.search(r"车道级.{0,10}(最高)?限速标志|分车道.{0,10}(最高)?限速标志|各车道.{0,16}最高限速标志", text):
        return Classification("max_lane_sign", "explicit_text", "MAX01", short_evidence(text))
    if re.search(r"路侧.{0,8}(道路级|路段级).{0,12}(最高)?限速标志|道路级.{0,12}最高限速标志|路段级.{0,12}最高限速标志", text):
        return Classification("max_road_sign", "explicit_text", "MAX02", short_evidence(text))
    if "map-based speed-limit control area" in lower or "地图限速管理区域" in text or "地图限速控制" in text:
        return Classification("max_map", "explicit_text", "MAX03", short_evidence(text))
    if "without maximum-speed-limit control" in lower or re.search(r"无(?:明显)?最高限速(?:标志|控制)|未见.{0,10}最高限速标志", text):
        return Classification("max_no_control", "explicit_text", "MAX04", short_evidence(text))
    if articles & {"IMR_46.3", "IMR_46.4", "IMR_46.5"}:
        return Classification("max_special_rule", "structured", "MAX05", "; ".join(sorted(articles)))
    if re.search(
        r"最高限速标志|限速标志牌|道路限速标志|"
        r"speed[- ]limit sign is present|speed limit applies|LED display shows an? \d+ km/h speed|temporary \d+ km/h speed[- ]limit sign",
        text,
        re.I,
    ):
        # Author decision: signs whose lane/road level cannot be distinguished
        # are pooled with the road-side/road-level sign group (MaxS2).
        return Classification("max_road_sign", "explicit_text", "MAX02B", short_evidence(text))
    return Classification("max_other", "unclear", "MAX99", short_evidence(text))


MIN_SPECIAL = {
    1: "min_special_congestion",
    2: "min_special_construction",
    3: "min_special_uphill",
    4: "min_special_downhill",
    5: "min_special_curve",
}


def classify_min(data: dict[str, Any]) -> Classification:
    text = combined_description(data)
    value = get_in(data, "Evidence", "Special_case")
    try:
        special_case = int(float(value))
    except (TypeError, ValueError):
        special_case = -1
    if special_case in MIN_SPECIAL:
        return Classification(MIN_SPECIAL[special_case], "structured", "MIN01", f"Evidence.Special_case={special_case}")
    if special_case not in {-1, 0}:
        return Classification("min_special_other", "structured", "MIN02", f"Evidence.Special_case={special_case}")
    if re.search(r"车道级.{0,10}最低限速标志|分车道.{0,12}最低限速标志|各车道.{0,16}最低限速标志", text):
        return Classification("min_lane_sign", "explicit_text", "MIN03", short_evidence(text))
    if re.search(r"路侧.{0,8}(道路级|路段级).{0,12}最低限速标志|路侧.{0,16}最低限速标志|道路级.{0,12}最低限速标志", text):
        return Classification("min_road_sign", "explicit_text", "MIN04", short_evidence(text))
    anchor = scalar_text(get_in(data, "EventAnchor", "Anchor_type")).lower()
    inside = get_in(data, "Evidence", "Inside_speed_limit_sign_area")
    if inside is False or re.search(
        r"no minimum-speed sign was active|without (?:an? )?minimum-speed sign|"
        r"无明确最低限速|无最低限速标志|未观察到.{0,16}最低限速标志|未见.{0,12}最低限速标志",
        text,
        re.I,
    ):
        return Classification(
            "min_no_sign_regular",
            "structured_inference",
            "MIN06A",
            f"Special_case=0; Inside_speed_limit_sign_area={inside}; {short_evidence(text)}",
        )
    if "minimum_speed_sign" in anchor or inside is True or re.search(r"最低限速标志|最低限速标志牌", text):
        return Classification("min_sign_unspecified", "structured_inference", "MIN05", short_evidence(text))
    if special_case == 0:
        return Classification("min_no_sign_regular", "structured_inference", "MIN06", f"Special_case=0; Inside_speed_limit_sign_area={inside}")
    return Classification("min_other", "unclear", "MIN99", short_evidence(text))


def classify_follow(data: dict[str, Any]) -> Classification:
    text = scalar_text(get_in(data, "Result", "Scenario_description_VLM"))
    if re.search(
        r"跟车关系发生多次变化|跟车目标.{0,12}多次.{0,8}切换|目标车.{0,8}多次切换|"
        r"切入.{0,80}切出|切出.{0,80}切入|前车目标切换.{0,30}切换|"
        r"(?:前车|前方.{0,12}车辆).{0,20}右换道.{0,20}左换道|目标车辆切换|前车目标切换",
        text,
    ):
        return Classification("follow_multiple_changes", "explicit_text", "FOL00", short_evidence(text))
    cut_in = re.search(
        r"(?:从.{0,14}(?:侧|中间)车道.{0,35}(?:切入|驶入|变道进入).{0,16}自车(?:所在)?车道|"
        r"(?:相邻|左侧|右侧).{0,20}车辆.{0,30}切入自车(?:所在)?车道|"
        r"切入自车(?:所在)?车道|切入自车前方|切入(?:到)?本车道|"
        r"切入本车道前方|切入本车道|切入本车到|切入(?:到)?自车所在(?:的)?.{0,8}车道|"
        r"右侧前车切入|被左侧车道.{0,12}切入)",
        text,
    )
    if cut_in:
        return Classification("follow_adjacent_cut_in", "explicit_text", "FOL01", short_evidence(text))
    lead_cut_out = re.search(
        r"(?:同车道)?前车.{0,45}(?:向左|向右|变道|换道|切出|驶离|离开).{0,35}(?:自车(?:所在)?车道|当前车道)|"
        r"前方车辆.{0,35}(?:切出|驶离|离开)|"
        r"(?:前车|前方.{0,10}车|同车道.{0,10}车).{0,35}(?:变道至|换道至|向左切出|向右切出|切出)|"
        r"(?:SUV|轿车|货车|面包车).{0,20}(?:变道至|换道至)(?:左|右)侧车道|"
        r"(?:车辆|轿车|SUV|货车|面包车).{0,28}(?:变道)?驶离自车车道",
        text,
    )
    if lead_cut_out:
        return Classification("follow_lead_cut_out", "explicit_text", "FOL02", short_evidence(text))
    ego_change = re.search(
        r"自车.{0,55}(?:向左|向右|左侧|右侧).{0,24}(?:变道|换道|跨越车道线|驶入.{0,8}车道)|"
        r"自车.{0,20}(?:变道至|换道至|汇入).{0,16}车道|"
        r"自车变道|自车换道|自车进行(?:左|右)换道",
        text,
    )
    if ego_change:
        return Classification("follow_ego_lane_change", "explicit_text", "FOL03", short_evidence(text))
    if re.search(r"全程.{0,12}跟随|始终.{0,12}跟随|持续.{0,12}跟随|稳定.{0,12}跟随|保持.{0,12}跟车|同车道跟车", text):
        return Classification("follow_continuous", "explicit_text", "FOL04", short_evidence(text))
    if re.search(r"跟随|跟车|同车道前车|车距", text) and not re.search(r"切入|切出|变道|换道|驶离", text):
        return Classification("follow_continuous", "text_inference", "FOL05", short_evidence(text))
    if re.search(r"前方同车道.{0,28}车|自车正前方.{0,18}车|前方有.{0,15}车", text) and not re.search(r"切入|切出|变道|换道|驶离|目标切换", text):
        return Classification("follow_continuous", "text_inference", "FOL05B", short_evidence(text))
    if re.search(r"自车保持车道行驶|自车保持前行|自车始终行驶", text) and re.search(r"正前方|前方同车道|前方.{0,12}(?:SUV|轿车|货车|面包车)", text):
        return Classification("follow_continuous", "text_inference", "FOL05C", short_evidence(text))
    if re.search(r"未见明确.{0,8}同车道前车|无明确.{0,8}同车道前车|同车道前方无车|无车辆构成跟随关系", text):
        return Classification("follow_multiple_changes", "explicit_text", "FOL06", short_evidence(text))
    if re.search(r"无法.{0,20}确认|原因无法|测距目标可能|测距数据.{0,18}跳变", text):
        return Classification("follow_measurement_unclear", "explicit_text", "FOL07", short_evidence(text))
    return Classification("follow_other", "unclear", "FOL99", short_evidence(text))


def lateral_side(data: dict[str, Any]) -> str:
    evidence = get_in(data, "Evidence", default={}) or {}
    left, right = evidence.get("Minimum_left_vehicle_distance_m"), evidence.get("Minimum_right_vehicle_distance_m")
    if left is not None and right is None:
        return "left"
    if right is not None and left is None:
        return "right"
    return ""


def classify_lateral(data: dict[str, Any]) -> Classification:
    text = scalar_text(get_in(data, "Result", "Scenario_description_VLM"))
    exact = [
        (r"目标车由左后方移动至左前方", "lat_left_passes_ego", "LAT01"),
        (r"目标车由左前方移动至左后方", "lat_ego_passes_left", "LAT02"),
        (r"目标车由右后方移动至右前方", "lat_right_passes_ego", "LAT03"),
        (r"目标车由右前方移动至右后方", "lat_ego_passes_right", "LAT04"),
    ]
    for pattern, code, rule in exact:
        if re.search(pattern, text):
            return Classification(code, "explicit_text", rule, short_evidence(text))
    side = lateral_side(data)
    if side and re.search(r"自车.{0,30}(?:完成|从.{0,5}侧)?超越|自车.{0,30}超过", text):
        code = "lat_ego_passes_left" if side == "left" else "lat_ego_passes_right"
        return Classification(code, "text_inference", "LAT05", short_evidence(text))
    if side and re.search(r"车辆.{0,40}(?:完成)?(?:对自车的)?超越|超越自车", text):
        code = "lat_left_passes_ego" if side == "left" else "lat_right_passes_ego"
        return Classification(code, "text_inference", "LAT06", short_evidence(text))
    if re.search(r"纵向相对位置发生多次交替|双方先后出现接近和拉开|同时.{0,70}车道", text):
        return Classification("lat_complex_interaction", "explicit_text", "LAT07", short_evidence(text))
    if re.search(r"未完成超越|尚未完成超越|未完成对.{0,10}超越", text):
        return Classification("lat_non_passing_interaction", "explicit_text", "LAT08", short_evidence(text))
    if re.search(r"保持相对稳定的纵向距离|纵向相对位置保持稳定|保持近似并行|纵向相对位置变化不大", text):
        return Classification("lat_non_passing_interaction", "explicit_text", "LAT09", short_evidence(text))
    if re.search(r"逐渐加速远离|逐渐落后|与自车拉开纵向距离|自车与其拉开纵向距离", text):
        return Classification("lat_non_passing_interaction", "explicit_text", "LAT10", short_evidence(text))
    return Classification("lat_other", "unclear", "LAT99", short_evidence(text))


def classify_lane_change(data: dict[str, Any]) -> Classification:
    evidence = get_in(data, "Evidence", default={}) or {}
    text = scalar_text(get_in(data, "Result", "Scenario_description_VLM"))
    direction = scalar_text(evidence.get("Lane_change_direction")).lower()
    cross = evidence.get("Cross_line_time_s")
    end = evidence.get("Lane_change_end_time_s")
    if cross is None or end is None or re.search(r"未(?:完成|实际).{0,8}(?:变道|换道)|仅.{0,8}压线|未跨越车道线", text):
        return Classification("lc_line_only", "structured", "LC01", f"direction={direction or 'missing'}; cross={cross}; end={end}")
    if direction == "left":
        return Classification("lc_left", "structured", "LC02", f"Evidence.Lane_change_direction={direction}")
    if direction == "right":
        return Classification("lc_right", "structured", "LC03", f"Evidence.Lane_change_direction={direction}")
    return Classification("lc_other", "unclear", "LC99", short_evidence(text))


def classify_continue_lane_change(data: dict[str, Any]) -> Classification:
    evidence = get_in(data, "Evidence", default={}) or {}
    first = scalar_text(get_in(evidence, "First_lane_change", "Direction")).lower()
    second = scalar_text(get_in(evidence, "Second_lane_change", "Direction")).lower()
    mapping = {
        ("left", "left"): "clc_left_left",
        ("right", "right"): "clc_right_right",
        ("left", "right"): "clc_left_right",
        ("right", "left"): "clc_right_left",
    }
    if (first, second) in mapping:
        return Classification(mapping[(first, second)], "structured", "CLC01", f"{first}→{second}")
    return Classification("clc_other", "unclear", "CLC99", f"count={evidence.get('Lane_change_count')}; {first or '?'}→{second or '?'}")


def road_marking_line_types(data: dict[str, Any]) -> list[str]:
    interactions = get_in(data, "Evidence", "Line_interactions", default=[]) or []
    output: list[str] = []
    for interaction in interactions:
        if isinstance(interaction, dict):
            values = interaction.get("Line_types") or []
            if not isinstance(values, list):
                values = [values]
            output.extend(str(value).strip().lower() for value in values if str(value).strip())
    return output


def classify_road_marking(data: dict[str, Any]) -> Classification:
    values = road_marking_line_types(data)
    if not values:
        return Classification("mark_unclear", "unclear", "MARK99", "Evidence.Line_interactions.Line_types missing")
    dashed = {"dashed line", "double dashed line", "dashed_line", "double_dashed_line"}
    solid = {"solid line", "double solid line", "solid_line", "double_solid_line"}
    value_set = set(values)
    if value_set <= dashed:
        return Classification("mark_dashed", "structured", "MARK01", "; ".join(sorted(value_set)))
    if value_set <= solid:
        return Classification("mark_solid", "structured", "MARK02", "; ".join(sorted(value_set)))
    return Classification("mark_other_line", "structured", "MARK03", "; ".join(sorted(value_set)))


def classify_overtake(data: dict[str, Any]) -> Classification:
    evidence = get_in(data, "Evidence", default={}) or {}
    confirmed = evidence.get("Overtake_behavior_confirmed")
    direction = scalar_text(evidence.get("Overtake_direction")).lower()
    if confirmed is False:
        return Classification("overtake_lane_change_only", "structured", "OVT01", f"confirmed={confirmed}; direction={direction or 'missing'}")
    if confirmed is True and direction == "left":
        return Classification("overtake_left", "structured", "OVT02", "confirmed=True; direction=left")
    if confirmed is True and direction == "right":
        return Classification("overtake_right", "structured", "OVT03", "confirmed=True; direction=right")
    return Classification("overtake_other", "unclear", "OVT99", f"confirmed={confirmed}; direction={direction or 'missing'}")


CLASSIFIERS = {
    "MaxSpdlim": classify_max,
    "MinSpdlim": classify_min,
    "FollowDis": classify_follow,
    "LateralDis": classify_lateral,
    "LaneChange": classify_lane_change,
    "ContinueLaneChange": classify_continue_lane_change,
    "RoadMarking": classify_road_marking,
    "Overtake": classify_overtake,
}


def list_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return [str(value).strip().lower()]


def special_scene_tags(data: dict[str, Any]) -> set[str]:
    evidence = get_in(data, "Evidence", default={}) or {}
    roads = set(list_text(evidence.get("Road_types")))
    lanes = set(list_text(evidence.get("Lane_types")))
    text = combined_description(data)
    tags: set[str] = set()

    if "multiple_carriageway" in roads or "多车道" in text or re.search(r"同向(?:设有|有|共)?\s*[2-9二三四五六七八九]条(?:主)?车道", text):
        tags.add("multi_lane")
    if re.search(r"单车道|同向(?:仅)?(?:有)?\s*(?:1|一)条(?:主)?车道", text):
        tags.add("single_lane")
    if "single_carriageway" in roads:
        tags.add("single_carriageway")

    ramp_roads = {"ramp_entry", "ramp_exit", "motorway_entry_ramp", "motorway_exit_ramp"}
    if roads & ramp_roads:
        tags.add("ramp")
    if lanes & {"acceleration_lane", "deceleration_lane"} or re.search(r"自车.{0,20}(?:位于|行驶在|行驶于|驶入|进入).{0,8}(?:加速车道|减速车道)", text):
        tags.add("accel_decel_lane")
    if any("jct" in item for item in roads) or "inner_link_of_an_intersection" in roads:
        tags.add("jct_intersection")
    if "tunnel" in roads:
        tags.add("tunnel")

    try:
        special_case = int(float(evidence.get("Special_case")))
    except (TypeError, ValueError):
        special_case = -1
    if special_case == 2 or "施工" in text:
        tags.add("construction")
    if re.search(r"夜间|夜晚|晚上|凌晨|天色已暗|天黑", text):
        tags.add("night")
    if special_case in {3, 4} or re.search(r"上坡|下坡|坡道|斜坡|坡度|陡坡", text):
        tags.add("slope")
    if special_case == 5 or "IMR_46.3" in article_ids(data) or re.search(r"弯道路段|急弯路|弯曲道路|道路弯曲|自车.{0,12}(?:驶入|进入|通过).{0,8}弯道", text):
        tags.add("curve")
    if evidence.get("Congestion") is True or special_case == 1 or ("拥堵" in text and not re.search(r"无拥堵|不拥堵|未见拥堵|非拥堵", text)):
        tags.add("congestion")
    if roads & {"toll_booth", "toll_area"} or re.search(r"收费站|收费区", text):
        tags.add("toll")
    if re.search(r"高架|立交桥|跨线桥|桥梁|桥面", text):
        tags.add("bridge_elevated")
    if re.search(r"雨天|下雨|小雨|大雨|阵雨|路面湿滑|湿漉|大雾|雾天|起雾|低能见度|能见度较低|能见度差", text):
        tags.add("adverse_weather")
    return tags


def deprecated_scene_tags(data: dict[str, Any]) -> set[str]:
    """Return explicitly retired scene attributes for reassignment auditing."""
    evidence = get_in(data, "Evidence", default={}) or {}
    roads = set(list_text(evidence.get("Road_types")))
    lanes = set(list_text(evidence.get("Lane_types")))
    text = combined_description(data)
    tags: set[str] = set()
    if roads & {"service_area_approach", "service_area_jct", "service_area_approach_jct", "rest_area"} or re.search(r"服务区|休息区", text):
        tags.add("service_area")
    special_lanes = {
        "compound_lane", "drivable_parking_lane", "slow_lane", "drivable_shoulder_lane",
        "shoulder_lane", "regulated_access_lane", "variable_driving_lane", "emergency_strip", "other_lane",
    }
    if "auxiliary_lane" in roads or lanes & special_lanes or re.search(r"辅助车道|应急车道|路肩", text):
        tags.add("auxiliary_lane")
    if re.search(r"下雪|积雪|冰雪|结冰|路面结冰", text):
        tags.add("snow_ice")
    return tags


def is_special_road_overtake(data: dict[str, Any]) -> tuple[bool, list[str]]:
    evidence = get_in(data, "Evidence", default={}) or {}
    if evidence.get("Overtake_behavior_confirmed") is not True:
        return False, []
    roads = set(list_text(evidence.get("Road_types")))
    lanes = set(list_text(evidence.get("Lane_types")))
    special_roads = {
        "ramp_entry", "ramp_exit", "motorway_entry_ramp", "motorway_exit_ramp", "tunnel",
    }
    special_lanes = {"acceleration_lane", "deceleration_lane"}
    values = sorted((roads & special_roads) | (lanes & special_lanes))
    return bool(values), values


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def md_table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    def clean(value: Any) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")
    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    output.extend("| " + " | ".join(clean(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def main() -> None:
    if not DATASET_ROOT.exists():
        raise SystemExit(f"Dataset is unavailable: {DATASET_ROOT}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    events = discover_events()
    print(f"Discovered {len(events)} event directories; loading JSON files...", flush=True)
    with ThreadPoolExecutor(max_workers=48) as executor:
        loaded = list(executor.map(load_event, events))

    classified_rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    line_combinations: Counter[str] = Counter()
    special_overtake_types: Counter[str] = Counter()
    deprecated_reassignment_rows: list[dict[str, Any]] = []
    unconfirmed_overtake_rows: list[dict[str, Any]] = []
    manual_overtake_review_rows: list[dict[str, Any]] = []
    min_sign_unspecified_rows: list[dict[str, Any]] = []
    manual_min_matches = 0

    for event, data, error in loaded:
        if data is None:
            errors.append({"event_id": event["event_id"], "error": error})
            continue
        automatic_result = CLASSIFIERS[event["category"]](data)
        result = automatic_result
        manual_review = MANUAL_OVERTAKE_REVIEW.get(event["event_id"])
        if manual_review:
            review_index, manual_code = manual_review
            result = Classification(
                manual_code,
                "manual_review",
                "OVT_MANUAL",
                f"manual review #{review_index}; automatic={automatic_result.code}",
            )
        manual_min_review = MANUAL_MIN_SIGN_REVIEW.get(event["event_id"])
        if manual_min_review:
            manual_min_matches += 1
            review_index, manual_code = manual_min_review
            result = Classification(
                manual_code,
                "manual_review",
                "MIN_MANUAL",
                f"author-approved MinS4 review #{review_index}; automatic={automatic_result.code}",
            )
        en, zh = PRIMARY_LABELS[result.code]
        tags = sorted(special_scene_tags(data))
        deprecated_tags = sorted(deprecated_scene_tags(data))
        if event["category"] == "RoadMarking":
            line_combinations[" + ".join(sorted(set(road_marking_line_types(data)))) or "missing"] += 1
        if event["category"] == "Overtake":
            is_special, special_types = is_special_road_overtake(data)
            if is_special:
                special_overtake_types[" + ".join(special_types)] += 1
        else:
            is_special, special_types = False, []
        for deprecated_tag in deprecated_tags:
            deprecated_reassignment_rows.append(
                {
                    "event_id": event["event_id"],
                    "city": event["city"],
                    "category": event["category"],
                    "deprecated_scene_code": deprecated_tag,
                    "deprecated_scene_zh": DEPRECATED_SPECIAL_LABELS[deprecated_tag],
                    "replacement_scene_codes": ";".join(tags),
                    "replacement_scene_zh": ";".join(SPECIAL_LABELS[tag][1] for tag in tags),
                    "road_types_raw": scalar_text(get_in(data, "Evidence", "Road_types")),
                    "lane_types_raw": scalar_text(get_in(data, "Evidence", "Lane_types")),
                    "classification_evidence": short_evidence(combined_description(data)),
                    "json_path": event["path"],
                }
            )
        if event["category"] == "Overtake" and manual_review:
            evidence = get_in(data, "Evidence", default={}) or {}
            review_index, manual_code = manual_review
            manual_en, manual_zh = PRIMARY_LABELS[manual_code]
            automatic_en, automatic_zh = PRIMARY_LABELS[automatic_result.code]
            manual_overtake_review_rows.append(
                {
                    "review_index": review_index,
                    "event_id": event["event_id"],
                    "city": event["city"],
                    "segment": event["segment"],
                    "event_dir": event["event_dir"],
                    "driving_mode": scalar_text(data.get("DrivingMode")),
                    "compliance_label": scalar_text(get_in(data, "Result", "Compliance_label")),
                    "article_ids": ";".join(sorted(article_ids(data))),
                    "automatic_scene_code": automatic_result.code,
                    "automatic_scene_zh": automatic_zh,
                    "manual_scene_code": manual_code,
                    "manual_scene_en": manual_en,
                    "manual_scene_zh": manual_zh,
                    "overtake_direction": scalar_text(evidence.get("Overtake_direction")),
                    "first_lane_change_direction": scalar_text(get_in(evidence, "First_lane_change", "Direction")),
                    "second_lane_change_direction": scalar_text(get_in(evidence, "Second_lane_change", "Direction")),
                    "overtake_behavior_confirmed": scalar_text(evidence.get("Overtake_behavior_confirmed")),
                    "target_vehicle_observed": scalar_text(evidence.get("Target_vehicle_observed")),
                    "target_vehicle_passed_between_lane_changes": scalar_text(evidence.get("Target_vehicle_passed_between_lane_changes")),
                    "cross_line_time_gap_s": scalar_text(evidence.get("Cross_line_time_gap_s")),
                    "road_types": scalar_text(evidence.get("Road_types")),
                    "lane_types": scalar_text(evidence.get("Lane_types")),
                    "scenario_description_vlm": scalar_text(get_in(data, "Result", "Scenario_description_VLM")),
                    "json_path": event["path"],
                }
            )
        if event["category"] == "Overtake" and result.code == "overtake_lane_change_only":
            evidence = get_in(data, "Evidence", default={}) or {}
            unconfirmed_overtake_rows.append(
                {
                    "review_index": manual_review[0] if manual_review else "",
                    "event_id": event["event_id"],
                    "city": event["city"],
                    "segment": event["segment"],
                    "event_dir": event["event_dir"],
                    "driving_mode": scalar_text(data.get("DrivingMode")),
                    "compliance_label": scalar_text(get_in(data, "Result", "Compliance_label")),
                    "article_ids": ";".join(sorted(article_ids(data))),
                    "overtake_direction": scalar_text(evidence.get("Overtake_direction")),
                    "first_lane_change_direction": scalar_text(get_in(evidence, "First_lane_change", "Direction")),
                    "second_lane_change_direction": scalar_text(get_in(evidence, "Second_lane_change", "Direction")),
                    "overtake_behavior_confirmed": scalar_text(evidence.get("Overtake_behavior_confirmed")),
                    "target_vehicle_observed": scalar_text(evidence.get("Target_vehicle_observed")),
                    "target_vehicle_passed_between_lane_changes": scalar_text(evidence.get("Target_vehicle_passed_between_lane_changes")),
                    "cross_line_time_gap_s": scalar_text(evidence.get("Cross_line_time_gap_s")),
                    "road_types": scalar_text(evidence.get("Road_types")),
                    "lane_types": scalar_text(evidence.get("Lane_types")),
                    "scenario_description_vlm": scalar_text(get_in(data, "Result", "Scenario_description_VLM")),
                    "json_path": event["path"],
                }
            )
        if event["category"] == "MinSpdlim" and result.code == "min_sign_unspecified":
            evidence = get_in(data, "Evidence", default={}) or {}
            min_sign_unspecified_rows.append(
                {
                    "event_id": event["event_id"],
                    "city": event["city"],
                    "segment": event["segment"],
                    "event_dir": event["event_dir"],
                    "article_ids": ";".join(sorted(article_ids(data))),
                    "driving_mode": scalar_text(data.get("DrivingMode")),
                    "compliance_label": scalar_text(get_in(data, "Result", "Compliance_label")),
                    "anchor_type": scalar_text(get_in(data, "EventAnchor", "Anchor_type")),
                    "inside_speed_limit_sign_area": scalar_text(evidence.get("Inside_speed_limit_sign_area")),
                    "special_case": scalar_text(evidence.get("Special_case")),
                    "applicable_min_speed_limit_kph": scalar_text(evidence.get("Applicable_min_speed_limit_kph")),
                    "applicable_min_speed_at_minimum_speed_kph": scalar_text(
                        evidence.get("Applicable_min_speed_limit_at_minimum_speed_kph")
                    ),
                    "ego_speed_kph": scalar_text(evidence.get("Ego_speed_kph")),
                    "road_types": scalar_text(evidence.get("Road_types")),
                    "lane_types": scalar_text(evidence.get("Lane_types")),
                    "same_direction_lane_count": scalar_text(evidence.get("Same_direction_lane_count")),
                    "ego_lane_index_from_left": scalar_text(evidence.get("Ego_lane_index_from_left")),
                    "scenario_description_en": scalar_text(get_in(data, "Result", "Scenario_description")),
                    "scenario_description_zh": scalar_text(get_in(data, "Result", "Scenario_description_VLM")),
                    "json_path": event["path"],
                }
            )
        classified_rows.append(
            {
                "event_id": event["event_id"],
                "city": event["city"],
                "category": event["category"],
                "segment": event["segment"],
                "event_dir": event["event_dir"],
                "primary_scene_code": result.code,
                "primary_scene_en": en,
                "primary_scene_zh": zh,
                "confidence": result.confidence,
                "rule_id": result.rule,
                "classification_evidence": result.evidence,
                "road_types_raw": scalar_text(get_in(data, "Evidence", "Road_types")),
                "lane_types_raw": scalar_text(get_in(data, "Evidence", "Lane_types")),
                "special_case_raw": scalar_text(get_in(data, "Evidence", "Special_case")),
                "congestion_raw": scalar_text(get_in(data, "Evidence", "Congestion")),
                "deprecated_scene_codes": ";".join(deprecated_tags),
                "special_scene_codes": ";".join(tags),
                "special_scene_zh": ";".join(SPECIAL_LABELS[tag][1] for tag in tags),
                "special_road_overtake": int(is_special),
                "special_road_overtake_types": ";".join(special_types),
                "json_path": event["path"],
            }
        )

    manual_overtake_review_rows.sort(key=lambda row: int(row["review_index"]))
    unconfirmed_overtake_rows.sort(key=lambda row: int(row["review_index"]))
    min_sign_unspecified_rows.sort(
        key=lambda row: (str(row["city"]), str(row["segment"]), str(row["event_dir"]))
    )

    if len(manual_overtake_review_rows) != len(MANUAL_OVERTAKE_REVIEW):
        raise RuntimeError(
            f"Manual overtaking review matched {len(manual_overtake_review_rows)} of "
            f"{len(MANUAL_OVERTAKE_REVIEW)} configured events"
        )
    if manual_min_matches != len(MANUAL_MIN_SIGN_REVIEW):
        raise RuntimeError(
            f"Manual minimum-speed review matched {manual_min_matches} of "
            f"{len(MANUAL_MIN_SIGN_REVIEW)} configured events"
        )

    category_totals = Counter(row["category"] for row in classified_rows)
    primary_counts = Counter((row["category"], row["primary_scene_code"]) for row in classified_rows)
    primary_rows: list[dict[str, Any]] = []
    for category in CATEGORY_ORDER:
        for (cat, code), count in sorted(primary_counts.items()):
            if cat != category:
                continue
            en, zh = PRIMARY_LABELS[code]
            primary_rows.append(
                {
                    "category": category,
                    "primary_scene_code": code,
                    "primary_scene_en": en,
                    "primary_scene_zh": zh,
                    "count": count,
                    "percentage": round(100 * count / category_totals[category], 2),
                }
            )

    special_counts: Counter[tuple[str, str]] = Counter()
    special_totals: Counter[str] = Counter()
    for row in classified_rows:
        for tag in filter(None, row["special_scene_codes"].split(";")):
            special_counts[(row["category"], tag)] += 1
            special_totals[tag] += 1
    special_long_rows: list[dict[str, Any]] = []
    for tag in SPECIAL_LABELS:
        for category in CATEGORY_ORDER:
            count = special_counts[(category, tag)]
            special_long_rows.append(
                {
                    "special_scene_code": tag,
                    "special_scene_en": SPECIAL_LABELS[tag][0],
                    "special_scene_zh": SPECIAL_LABELS[tag][1],
                    "category": category,
                    "count": count,
                    "category_total": category_totals[category],
                    "percentage_within_category": round(100 * count / category_totals[category], 2),
                }
            )
    matrix_count_rows: list[dict[str, Any]] = []
    matrix_pct_rows: list[dict[str, Any]] = []
    for tag in SPECIAL_LABELS:
        base = {
            "special_scene_code": tag,
            "special_scene_en": SPECIAL_LABELS[tag][0],
            "special_scene_zh": SPECIAL_LABELS[tag][1],
        }
        matrix_count_rows.append({**base, **{cat: special_counts[(cat, tag)] for cat in CATEGORY_ORDER}, "All": special_totals[tag]})
        matrix_pct_rows.append(
            {
                **base,
                **{cat: round(100 * special_counts[(cat, tag)] / category_totals[cat], 2) for cat in CATEGORY_ORDER},
            }
        )

    confidence_counts = Counter((row["category"], row["confidence"]) for row in classified_rows)
    qa_rows = [
        {
            "category": category,
            "total": category_totals[category],
            "structured": confidence_counts[(category, "structured")],
            "explicit_text": confidence_counts[(category, "explicit_text")],
            "structured_inference": confidence_counts[(category, "structured_inference")],
            "text_inference": confidence_counts[(category, "text_inference")],
            "manual_review": confidence_counts[(category, "manual_review")],
            "unclear": confidence_counts[(category, "unclear")],
        }
        for category in CATEGORY_ORDER
    ]

    write_csv(
        OUTPUT_DIR / "event_scene_classification.csv",
        classified_rows,
        list(classified_rows[0].keys()),
    )
    write_csv(
        OUTPUT_DIR / "category_scene_counts.csv",
        primary_rows,
        ["category", "primary_scene_code", "primary_scene_en", "primary_scene_zh", "count", "percentage"],
    )
    write_csv(
        OUTPUT_DIR / "special_scene_by_category.csv",
        special_long_rows,
        ["special_scene_code", "special_scene_en", "special_scene_zh", "category", "count", "category_total", "percentage_within_category"],
    )
    write_csv(
        OUTPUT_DIR / "special_scene_matrix_counts.csv",
        matrix_count_rows,
        ["special_scene_code", "special_scene_en", "special_scene_zh", *CATEGORY_ORDER, "All"],
    )
    write_csv(
        OUTPUT_DIR / "special_scene_matrix_percent.csv",
        matrix_pct_rows,
        ["special_scene_code", "special_scene_en", "special_scene_zh", *CATEGORY_ORDER],
    )
    write_csv(
        OUTPUT_DIR / "classification_qa.csv",
        qa_rows,
        ["category", "total", "structured", "explicit_text", "structured_inference", "text_inference", "manual_review", "unclear"],
    )
    write_csv(
        OUTPUT_DIR / "read_errors.csv",
        errors,
        ["event_id", "error"],
    )
    write_csv(
        OUTPUT_DIR / "deprecated_special_scene_reassignment.csv",
        deprecated_reassignment_rows,
        [
            "event_id", "city", "category", "deprecated_scene_code", "deprecated_scene_zh",
            "replacement_scene_codes", "replacement_scene_zh", "road_types_raw", "lane_types_raw",
            "classification_evidence", "json_path",
        ],
    )
    write_csv(
        OUTPUT_DIR / "unconfirmed_overtake_event_list.csv",
        unconfirmed_overtake_rows,
        [
            "review_index", "event_id", "city", "segment", "event_dir", "driving_mode", "compliance_label",
            "article_ids", "overtake_direction", "first_lane_change_direction",
            "second_lane_change_direction", "overtake_behavior_confirmed", "target_vehicle_observed",
            "target_vehicle_passed_between_lane_changes", "cross_line_time_gap_s", "road_types",
            "lane_types", "scenario_description_vlm", "json_path",
        ],
    )
    write_csv(
        OUTPUT_DIR / "lane_change_only_no_overtake_event_list.csv",
        unconfirmed_overtake_rows,
        [
            "review_index", "event_id", "city", "segment", "event_dir", "driving_mode", "compliance_label",
            "article_ids", "overtake_direction", "first_lane_change_direction",
            "second_lane_change_direction", "overtake_behavior_confirmed", "target_vehicle_observed",
            "target_vehicle_passed_between_lane_changes", "cross_line_time_gap_s", "road_types",
            "lane_types", "scenario_description_vlm", "json_path",
        ],
    )
    write_csv(
        OUTPUT_DIR / "manual_overtake_review.csv",
        manual_overtake_review_rows,
        [
            "review_index", "event_id", "city", "segment", "event_dir", "driving_mode",
            "compliance_label", "article_ids", "automatic_scene_code", "automatic_scene_zh",
            "manual_scene_code", "manual_scene_en", "manual_scene_zh", "overtake_direction",
            "first_lane_change_direction", "second_lane_change_direction",
            "overtake_behavior_confirmed", "target_vehicle_observed",
            "target_vehicle_passed_between_lane_changes", "cross_line_time_gap_s", "road_types",
            "lane_types", "scenario_description_vlm", "json_path",
        ],
    )
    if min_sign_unspecified_rows:
        write_csv(
            OUTPUT_DIR / "mins_unresolved_sign_event_list.csv",
            min_sign_unspecified_rows,
            [
                "event_id", "city", "segment", "event_dir", "article_ids", "driving_mode",
                "compliance_label", "anchor_type", "inside_speed_limit_sign_area", "special_case",
                "applicable_min_speed_limit_kph", "applicable_min_speed_at_minimum_speed_kph",
                "ego_speed_kph", "road_types", "lane_types", "same_direction_lane_count",
                "ego_lane_index_from_left", "scenario_description_en", "scenario_description_zh", "json_path",
            ],
        )

    deprecated_combo_counts = Counter(
        (
            row["deprecated_scene_code"],
            row["replacement_scene_zh"] or "无其他特殊场景标签",
        )
        for row in deprecated_reassignment_rows
    )
    min_special_case_counts = Counter(
        row["special_case_raw"] or "missing"
        for row in classified_rows
        if row["category"] == "MinSpdlim"
    )
    report: list[str] = [
        "# TLCD 场景多样性统计",
        "",
        f"- 扫描事件目录：{len(events)}",
        f"- 成功读取并分类的 JSON：{len(classified_rows)}",
        f"- 读取失败：{len(errors)}",
        "- 主场景类别在每一事件类别内互斥；特殊场景标签为多标签，占比不要求合计为 100% 。",
        "",
    ]
    for category in CATEGORY_ORDER:
        report.extend(
            [
                f"## {category} (n={category_totals[category]})",
                "",
                md_table(
                    ["场景类型", "数量", "类内占比"],
                    [
                        (row["primary_scene_zh"], row["count"], f"{row['percentage']:.2f}%")
                        for row in primary_rows
                        if row["category"] == category
                    ],
                ),
                "",
            ]
        )
    report.extend(
        [
            "## 超车类事件中的特殊道路（多标签补充统计）",
            "",
            f"- 确认完成的超车中，特殊道路/车道事件共 {sum(special_overtake_types.values())} 条。",
            "",
            md_table(["特殊道路/车道组合", "数量"], special_overtake_types.most_common()),
            "",
            "## 道路标线精细类型（用于解释‘其他或混合标线’）",
            "",
            md_table(["JSON 标线组合", "数量"], line_combinations.most_common()),
            "",
            "## 特殊场景总体频数（多标签）",
            "",
            md_table(
                ["特殊场景", "事件数"],
                [(SPECIAL_LABELS[tag][1], special_totals[tag]) for tag in SPECIAL_LABELS],
            ),
            "",
            "## 已删除属性的重新归类审计",
            "",
            md_table(
                ["已删除属性", "剩余特殊场景标签", "事件数"],
                [
                    (DEPRECATED_SPECIAL_LABELS[tag], replacement, count)
                    for (tag, replacement), count in sorted(deprecated_combo_counts.items())
                ],
            ),
            "",
            "## 最低限速 Special_case 原始取值复核",
            "",
            md_table(["Special_case", "事件数"], sorted(min_special_case_counts.items())),
            "",
            "## 原 MinS4 事件人工复核",
            "",
            "- 原 MinS4 的 142 条事件已重新归类：131 条归入无最低限速标志常规场景；复核序号 87 和 124 归入车道级标志；其余 9 条归入路段级标志。",
            "- 完整复核轨迹保存于 `mins4_event_list.csv` 和 `mins4_event_list.md`。",
            "",
            "## 超车候选事件人工复核",
            "",
            "- 对14条双换道候选事件进行人工复核：第9条归为左侧超车，第13、14条归为右侧超车，其余11条归为‘仅左右换道未超车’。",
            f"- 仅左右换道未超车事件共 {len(unconfirmed_overtake_rows)} 条，详见 `unconfirmed_overtake_event_list.csv`；完整人工复核轨迹见 `manual_overtake_review.csv`。",
            "",
            "## 分类可判定性 QA",
            "",
            md_table(
                ["事件类别", "总数", "结构化明确", "文本明确", "结构化推断", "文本推断", "人工复核", "无法判定"],
                [
                    (
                        row["category"], row["total"], row["structured"], row["explicit_text"],
                        row["structured_inference"], row["text_inference"], row["manual_review"], row["unclear"],
                    )
                    for row in qa_rows
                ],
            ),
            "",
            "## 分类规则说明",
            "",
            "- 最高限速事件中，`Article.ID` 包含 `IMR_78.1` 时优先归入‘无最高限速信息控制’。",
            "- 最高限速标志层级无法区分的事件已按作者决定并入路侧道路/路段级标志。",
            "- 最低限速事件中，无激活标志的记录归入无标志常规场景；11 条明确标志事件按作者复核结果分为 2 条车道级和 9 条路段级标志。",
            "- 最低限速特殊场景优先使用 `Evidence.Special_case` 的结构化枚举：1 拥堵、2 施工、3 上坡、4 下坡、5 弯道。",
            "- 跟车和横向距离的过程类型依据 `Scenario_description_VLM` 中明确的时序语句；需要宽松语义判断的记为 `text_inference`。",
            "- 超车类别对14条双换道候选事件采用作者人工复核标签，并以 `manual_review` 标记覆盖结构化自动判定。",
            "- 超车左/右方向仅统计 `Overtake_behavior_confirmed=True` 的事件；特殊道路是与左/右方向正交的多标签统计。",
            "- 服务区/休息区标志、冰雪警示标志以及辅助/特殊用途车道属性已从特殊场景中删除；雨天/湿滑与雾/低能见度合并为一类。",
            "- `single_carriageway` 是道路结构类型，不等同于‘单车道’；两者在结果中分开统计。",
        ]
    )
    (OUTPUT_DIR / "scene_diversity_statistics.md").write_text("\n".join(report), encoding="utf-8")
    print(f"Wrote scene-diversity audit to {OUTPUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
