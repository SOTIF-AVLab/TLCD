"""Add the missing DrivingMode field to rebuilt validation-set record JSON files."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


ROOTS = (
    (Path(r"Z:\HongqiData\Nanjing_valid\01_MaxSpdlim"), "MaxSpdlim_event_*_record.json"),
    (Path(r"Z:\HongqiData\Changchun_valid\01_MaxSpdlim"), "MaxSpdlim_event_*_record.json"),
    (Path(r"Z:\HongqiData\Nanjing_valid\03_FollowDis"), "FollowDis_event_*_record.json"),
    (Path(r"Z:\HongqiData\Changchun_valid\03_FollowDis"), "FollowDis_event_*_record.json"),
    (Path(r"Z:\HongqiData\Nanjing_valid\05_LaneChange"), "lane_change_event_*_record.json"),
    (Path(r"Z:\HongqiData\Changchun_valid\05_LaneChange"), "lane_change_event_*_record.json"),
    (Path(r"Z:\HongqiData\Nanjing_valid\06_ContinueLaneChange"), "ContinueLC_event_*_record.json"),
    (Path(r"Z:\HongqiData\Changchun_valid\06_ContinueLaneChange"), "ContinueLC_event_*_record.json"),
)


def add_mode(path: Path) -> bool:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if "DrivingMode" in data:
        return False
    rebuilt = {}
    for key, value in data.items():
        rebuilt[key] = value
        if key == "Timestamp":
            rebuilt["DrivingMode"] = "Manual Driving"
    if "DrivingMode" not in rebuilt:
        raise ValueError(f"Timestamp is missing: {path}")
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(rebuilt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write missing DrivingMode fields.")
    args = parser.parse_args()
    paths = sorted(
        path
        for root, pattern in ROOTS
        for path in root.glob(f"*/event_*/{pattern}")
    )
    missing = [path for path in paths if "DrivingMode" not in json.loads(path.read_text(encoding="utf-8-sig"))]
    if args.apply:
        for path in missing:
            add_mode(path)
    print(json.dumps({"records": len(paths), "missing_before": len(missing), "written": len(missing) if args.apply else 0}, ensure_ascii=False))


if __name__ == "__main__":
    main()
