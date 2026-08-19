from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from S21_correct_maxspdlim_sign2 import (
    DATA_ROOT as DEFAULT_DATA_ROOT,
    build_evidence,
    classify_speed_sign,
    read_events,
    write_csv_safe,
)


EVENT_ROOT_NAME = "zEvent_MaxSpdlim_sign_1"


def apply_speed_rule_keep_ego(map_info: pd.DataFrame, event_row: pd.Series) -> tuple[pd.DataFrame, str, bool]:
    corrected = map_info.copy()
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


def correct_event(event_dir: Path, event_row: pd.Series) -> tuple[Path, Path]:
    event_num = int(event_row["event_num"])
    map_path = event_dir / f"MaxSpdlim_event_{event_num}_MapInfo_correct_scheme1.csv"
    if not map_path.exists():
        raise FileNotFoundError(map_path)
    map_info = pd.read_csv(map_path)
    original_ego = pd.to_numeric(map_info["EgoLaneIndex"], errors="coerce").to_numpy()
    corrected_map, rule, sign_effective = apply_speed_rule_keep_ego(map_info, event_row)
    if not np.array_equal(original_ego, pd.to_numeric(corrected_map["EgoLaneIndex"], errors="coerce").to_numpy()):
        raise AssertionError(f"EgoLaneIndex changed for {map_path}")
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
    parser = argparse.ArgumentParser(description="Correct MaxSpdlim sign_1 MapInfo speed limits, EvidenceChain, and JSON.")
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--summary-name", default="")
    args = parser.parse_args()

    rows = []
    failures = []
    dates = []
    for date_dir in sorted(path for path in args.data_root.iterdir() if path.is_dir()):
        event_root = date_dir / EVENT_ROOT_NAME
        if not event_root.is_dir():
            continue
        dates.append(date_dir.name)
        valid_total = 0
        corrected_total = 0
        pending_maps = []
        for events_path in sorted(event_root.glob("*/MaxSpdlim_events.csv")):
            events = read_events(events_path)
            valid = pd.to_numeric(events["Event_Validity"], errors="coerce").fillna(0) == 1
            valid_total += int(valid.sum())
            for _, event_row in events.loc[valid].iterrows():
                try:
                    map_output, _ = correct_event(events_path.parent, event_row)
                except Exception as exc:
                    failures.append(
                        f"{date_dir.name} {events_path.parent.name} event {int(event_row['event_num'])}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    continue
                if map_output.name.endswith("_pending_overwrite.csv"):
                    pending_maps.append(str(map_output))
                corrected_total += 1
        rows.append({
            "date": date_dir.name,
            "valid_events": valid_total,
            "corrected_events": corrected_total,
            "pending_map_overwrites": " || ".join(pending_maps),
        })
        print(f"{date_dir.name}: valid={valid_total}, corrected={corrected_total}, pending_maps={len(pending_maps)}")

    if failures:
        for failure in failures:
            print(f"FAILED {failure}")
        raise SystemExit(f"failed to correct {len(failures)} events")

    json_written = regenerate_json(args.data_root, dates)
    summary = pd.DataFrame(rows)
    summary["json_written_total"] = json_written
    summary_name = args.summary_name or f"S22_maxspdlim_sign1_{args.data_root.name}_summary.csv"
    summary_path = Path(__file__).resolve().parent / summary_name
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(f"json_written={json_written}")
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
