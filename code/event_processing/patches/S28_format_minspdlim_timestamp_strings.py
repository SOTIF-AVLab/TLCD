"""Convert MinSpdlim Timestamp objects to the shared string format."""

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from S26_update_valid_record_times import CITY_ROOTS, atomic_write_json


TARGET_PATTERN = "MinSpdlim_event_*_record.json"
TIMESTAMP_PATTERN = re.compile(r"^\d{13} -- \d{13}$")


def target_timestamp(value: object) -> str:
    if isinstance(value, str):
        if not TIMESTAMP_PATTERN.fullmatch(value):
            raise ValueError("Timestamp string must be '13 digits -- 13 digits'")
        return value
    if not isinstance(value, dict):
        raise ValueError("Timestamp must be an object or a formatted string")

    start = value.get("t_start")
    end = value.get("t_end")
    if not isinstance(start, int) or not isinstance(end, int):
        raise ValueError("Timestamp object values must be integers")
    if len(str(start)) != 13 or len(str(end)) != 13 or end <= start:
        raise ValueError("Timestamp object values are invalid")
    return f"{start} -- {end}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert MinSpdlim Timestamp objects to 't_start -- t_end' strings."
    )
    parser.add_argument("--apply", action="store_true", help="Write converted Timestamp strings.")
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(__file__).with_name("S28_minspdlim_timestamp_format_summary.json"),
    )
    args = parser.parse_args()

    results = []
    for _, (valid_root, _) in CITY_ROOTS.items():
        for record_path in sorted((valid_root / "02_MinSpdlim").rglob(TARGET_PATTERN)):
            try:
                record = json.loads(record_path.read_text(encoding="utf-8-sig"))
                formatted = target_timestamp(record.get("Timestamp"))
                if record.get("Timestamp") == formatted:
                    status = "unchanged"
                elif args.apply:
                    record["Timestamp"] = formatted
                    atomic_write_json(record, record_path)
                    status = "updated"
                else:
                    status = "would_update"
                results.append({"record": str(record_path), "status": status})
            except Exception as error:
                results.append(
                    {"record": str(record_path), "status": "error", "detail": str(error)}
                )

    counts = Counter(result["status"] for result in results)
    report = {
        "apply": args.apply,
        "target": TARGET_PATTERN,
        "records": len(results),
        "status_counts": dict(counts),
        "results": results,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.report), **report["status_counts"]}, ensure_ascii=False))
    return 1 if counts["error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
