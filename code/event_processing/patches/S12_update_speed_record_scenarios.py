from __future__ import annotations

import csv
import json
from pathlib import Path


ROOT = Path(r"Z:\HongqiData\Nanjing")


def event_files(event_kind: str) -> list[Path]:
    return sorted(ROOT.glob(f"*/zEvent_{event_kind}/*/{event_kind}_events.csv"))


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def write_json(path: Path, data: dict) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def sync_event_kind(event_kind: str) -> tuple[int, int, int, int]:
    event_count = 0
    described_count = 0
    updated_count = 0
    missing_record_count = 0

    for events_path in event_files(event_kind):
        prefix = "MaxSpdlim" if event_kind == "MaxSpdlim" else "MinSpdlim"
        with events_path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))

        for row in rows:
            event_count += 1
            description = (row.get("Event_description") or "").strip()
            if not description:
                continue

            described_count += 1
            event_num = int(row["event_num"])
            record_path = events_path.parent / f"{prefix}_event_{event_num}_record.json"
            if not record_path.exists():
                missing_record_count += 1
                continue

            record = load_json(record_path)
            result = record.setdefault("Result", {})
            if result.get("Scenario_description") == description:
                continue

            result["Scenario_description"] = description
            write_json(record_path, record)
            updated_count += 1

    return event_count, described_count, updated_count, missing_record_count


def main() -> None:
    for event_kind in ("MaxSpdlim", "MinSpdlim"):
        event_count, described_count, updated_count, missing_record_count = sync_event_kind(event_kind)
        print(
            f"{event_kind}: events={event_count}, described={described_count}, "
            f"updated_records={updated_count}, missing_records={missing_record_count}"
        )
        if missing_record_count:
            raise RuntimeError(f"{event_kind} has {missing_record_count} missing record.json files.")


if __name__ == "__main__":
    main()
