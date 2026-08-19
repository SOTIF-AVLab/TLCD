from __future__ import annotations

from pathlib import Path

import pandas as pd

from S18_correct_minspdlim_mapinfo import correct_segment
from S19_correct_minspdlim_evidence_json import correct_evidence, event_number_from_evidence, regenerate_json, update_events_summary


ROOT = Path(r"Z:\HongqiData")
CITY = "Changchun"
EVENT_ROOT_NAME = "zEvent_MinSpdlim"
SUMMARY_PATH = Path(__file__).resolve().parent / "S20_minspdlim_batch_run_summary.csv"


def eligible_dates(city_root: Path) -> list[Path]:
    dates = []
    for date_dir in sorted(path for path in city_root.iterdir() if path.is_dir()):
        event_root = date_dir / EVENT_ROOT_NAME
        if not event_root.is_dir():
            continue
        if not any(event_root.glob("*_CSV/MinSpdlim_events.csv")):
            continue
        dates.append(date_dir)
    return dates


def run_map_correction(date_dir: Path) -> tuple[int, int, list[str]]:
    event_root = date_dir / EVENT_ROOT_NAME
    csv_all_root = date_dir / "csv_all"
    csv_selected_root = date_dir / "csv_selected"
    segments = 0
    output_files = 0
    failures = []
    for events_path in sorted(event_root.glob("*_CSV/MinSpdlim_events.csv")):
        try:
            outputs = correct_segment(events_path.parent, csv_all_root, csv_selected_root)
        except Exception as exc:
            failures.append(f"{events_path.parent.name}: {type(exc).__name__}: {exc}")
            continue
        segments += 1
        output_files += len(outputs)
    return segments, output_files, failures


def run_evidence_correction(date_dir: Path) -> tuple[int, int, int, int, int, list[str]]:
    event_root = date_dir / EVENT_ROOT_NAME
    events = 0
    changed_rows = 0
    violation_rows = 0
    event_summary_rows = 0
    violation_events = 0
    failures = []
    for events_path in sorted(event_root.glob("*_CSV/MinSpdlim_events.csv")):
        event_rows = pd.read_csv(events_path).set_index("event_num")
        for evidence_path in sorted(events_path.parent.glob("MinSpdlim_event_*_EvidenceChain.csv")):
            event_num = event_number_from_evidence(evidence_path)
            map_path = evidence_path.with_name(f"MinSpdlim_event_{event_num}_MapInfo_correct_scheme1.csv")
            if not map_path.exists():
                failures.append(f"{evidence_path}: missing scheme1 MapInfo")
                continue
            try:
                event_changed, event_violation = correct_evidence(evidence_path, map_path, event_rows.loc[event_num])
            except Exception as exc:
                failures.append(f"{evidence_path}: {type(exc).__name__}: {exc}")
                continue
            events += 1
            changed_rows += event_changed
            violation_rows += event_violation
        try:
            summary_changed, summary_violations = update_events_summary(events_path)
        except Exception as exc:
            failures.append(f"{events_path}: {type(exc).__name__}: {exc}")
            continue
        event_summary_rows += summary_changed
        violation_events += summary_violations
    return events, changed_rows, violation_rows, event_summary_rows, violation_events, failures


def main() -> None:
    rows = []
    city_root = ROOT / CITY
    for date_dir in eligible_dates(city_root):
        print(f"START {CITY} {date_dir.name}")
        segments, map_outputs, map_failures = run_map_correction(date_dir)
        events, changed_rows, violation_rows, event_summary_rows, violation_events, evidence_failures = run_evidence_correction(date_dir)
        json_written = 0
        json_failure = ""
        if not evidence_failures:
            try:
                json_written = regenerate_json(city_root, date_dir.name, EVENT_ROOT_NAME)
            except Exception as exc:
                json_failure = f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "city": CITY,
                "date": date_dir.name,
                "segments": segments,
                "map_outputs": map_outputs,
                "events": events,
                "changed_rows": changed_rows,
                "violation_rows": violation_rows,
                "event_summary_rows": event_summary_rows,
                "violation_events": violation_events,
                "json_written": json_written,
                "map_failures": " || ".join(map_failures),
                "evidence_failures": " || ".join(evidence_failures),
                "json_failure": json_failure,
            }
        )
        print(
            f"DONE {CITY} {date_dir.name}: segments={segments}, map_outputs={map_outputs}, "
            f"events={events}, event_summary_rows={event_summary_rows}, json={json_written}, "
            f"failures={len(map_failures) + len(evidence_failures) + int(bool(json_failure))}"
        )

    summary = pd.DataFrame(rows)
    summary.to_csv(SUMMARY_PATH, index=False, encoding="utf-8-sig")
    print(f"SUMMARY {SUMMARY_PATH}")
    if not summary.empty:
        print(summary[["city", "date", "segments", "events", "json_written"]].to_string(index=False))
        failed = summary[
            (summary["map_failures"].astype(str) != "")
            | (summary["evidence_failures"].astype(str) != "")
            | (summary["json_failure"].astype(str) != "")
        ]
        if not failed.empty:
            print("FAILURES")
            print(failed[["city", "date", "map_failures", "evidence_failures", "json_failure"]].to_string(index=False))


if __name__ == "__main__":
    main()
