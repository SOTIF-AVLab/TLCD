from __future__ import annotations

from pathlib import Path

import pandas as pd

from S15_correct_maxspdlim_mapinfo import calibrate_segment
from S16_correct_maxspdlim_evidence_json import correct_evidence, regenerate_json


ROOT = Path(r"Z:\HongqiData")
CITIES = ("Nanjing", "Changchun")
SUMMARY_PATH = Path("01Event_Extraction") / "S17_maxspdlim_batch_run_summary.csv"


def eligible_dates(city_root: Path) -> list[Path]:
    dates = []
    for date_dir in sorted(path for path in city_root.iterdir() if path.is_dir()):
        event_root = date_dir / "zEvent_MaxSpdlim"
        if not event_root.is_dir():
            continue
        if not (date_dir / "csv_all").is_dir() or not (date_dir / "csv_selected").is_dir():
            continue
        if not any(event_root.glob("*_CSV/MaxSpdlim_events.csv")):
            continue
        dates.append(date_dir)
    return dates


def run_s15(date_dir: Path) -> tuple[int, int, list[str]]:
    event_root = date_dir / "zEvent_MaxSpdlim"
    csv_all_root = date_dir / "csv_all"
    csv_selected_root = date_dir / "csv_selected"
    segments = 0
    output_files = 0
    failures = []
    for events_path in sorted(event_root.glob("*_CSV/MaxSpdlim_events.csv")):
        try:
            outputs = calibrate_segment(events_path.parent, csv_all_root, csv_selected_root)
        except Exception as exc:
            failures.append(f"{events_path.parent.name}: {type(exc).__name__}: {exc}")
            continue
        segments += 1
        output_files += len(outputs) * 2
    return segments, output_files, failures


def event_number_from_evidence(path: Path) -> int:
    return int(path.name.split("_")[2])


def run_s16(date_dir: Path) -> tuple[int, int, int, list[str]]:
    event_root = date_dir / "zEvent_MaxSpdlim"
    events = 0
    changed_rows = 0
    violation_rows = 0
    failures = []
    for evidence_path in sorted(event_root.glob("*_CSV/MaxSpdlim_event_*_EvidenceChain.csv")):
        event_num = event_number_from_evidence(evidence_path)
        map_path = evidence_path.with_name(f"MaxSpdlim_event_{event_num}_MapInfo_correct_scheme1.csv")
        if not map_path.exists():
            failures.append(f"{evidence_path}: missing scheme1 MapInfo")
            continue
        try:
            event_changed, event_violation = correct_evidence(evidence_path, map_path)
        except Exception as exc:
            failures.append(f"{evidence_path}: {type(exc).__name__}: {exc}")
            continue
        events += 1
        changed_rows += event_changed
        violation_rows += event_violation
    return events, changed_rows, violation_rows, failures


def main() -> None:
    rows = []
    for city in CITIES:
        city_root = ROOT / city
        for date_dir in eligible_dates(city_root):
            print(f"START {city} {date_dir.name}")
            segments, map_outputs, s15_failures = run_s15(date_dir)
            events, changed_rows, violation_rows, s16_failures = run_s16(date_dir)
            json_written = 0
            json_failure = ""
            if not s16_failures:
                try:
                    json_written = regenerate_json(city_root, date_dir.name)
                except Exception as exc:
                    json_failure = f"{type(exc).__name__}: {exc}"
            rows.append(
                {
                    "city": city,
                    "date": date_dir.name,
                    "segments": segments,
                    "map_outputs": map_outputs,
                    "events": events,
                    "changed_rows": changed_rows,
                    "violation_rows": violation_rows,
                    "json_written": json_written,
                    "s15_failures": " || ".join(s15_failures),
                    "s16_failures": " || ".join(s16_failures),
                    "json_failure": json_failure,
                }
            )
            print(
                f"DONE {city} {date_dir.name}: segments={segments}, "
                f"map_outputs={map_outputs}, events={events}, json={json_written}, "
                f"failures={len(s15_failures) + len(s16_failures) + int(bool(json_failure))}"
            )

    summary = pd.DataFrame(rows)
    summary.to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")
    print(f"SUMMARY {SUMMARY_PATH}")
    print(summary[["city", "date", "segments", "events", "json_written"]].to_string(index=False))
    failed = summary[
        (summary["s15_failures"].astype(str) != "")
        | (summary["s16_failures"].astype(str) != "")
        | (summary["json_failure"].astype(str) != "")
    ]
    if not failed.empty:
        print("FAILURES")
        print(failed[["city", "date", "s15_failures", "s16_failures", "json_failure"]].to_string(index=False))


if __name__ == "__main__":
    main()
