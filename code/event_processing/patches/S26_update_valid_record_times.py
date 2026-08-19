from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path


CITY_ROOTS = {
    "Nanjing": (
        Path(r"Z:\HongqiData\Nanjing_valid"),
        Path(r"Z:\HongqiData\Nanjing"),
    ),
    "Changchun": (
        Path(r"Z:\HongqiData\Changchun_valid"),
        Path(r"Z:\HongqiData\Changchun"),
    ),
}
TIME_PATTERN = re.compile(
    r"^(\d{2}:\d{2}:\d{2})\.(\d{3}) -- (\d{2}:\d{2}:\d{2})\.(\d{3})$"
)
SEGMENT_TIME_PATTERN = re.compile(
    r"(\d{8})_(\d{6})_\d+-(\d{8})_(\d{6})_\d+_CSV$"
)
CHINA_TZ = timezone(timedelta(hours=8))


def number(value: str) -> int:
    return int(Fraction(value))


def velocity_key(value: str) -> str:
    numeric = Fraction(value)
    return "0" if numeric == 0 else str(numeric)


def first_last_send_time(path: Path) -> tuple[int, int]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        index = (
            1
            if path.name.endswith("MapLocSrv_Line_struct.csv")
            else header.index("CommomPackage.sendTime")
        )
        first = next(reader)
        last = first
        for row in reader:
            if row:
                last = row
    return number(first[index]), number(last[index])


def map_system_bounds(path: Path) -> tuple[int, int]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        send_index = header.index("CommomPackage.sendTime")
        state_index = next(
            index
            for index, name in enumerate(header)
            if "Sf_EHRSystemStPositioningSt_enum" in name
        )
        start = end = None
        for row in reader:
            if not row:
                continue
            match = re.search(r"\((\d+)\)", row[state_index])
            state = int(match.group(1)) if match else int(float(row[state_index]))
            if state:
                stamp = number(row[send_index])
                if start is None:
                    start = stamp
                end = stamp
    if start is None:
        raise ValueError("Map system has no valid positioning interval")
    return start, end


def read_map_line(path: Path) -> tuple[list[int], list[int]]:
    sends: list[int] = []
    receives: list[int] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.reader(handle)
        next(reader)
        for row in reader:
            if row:
                sends.append(number(row[1]))
                receives.append(number(row[2]))
    return sends, receives


def read_ins(path: Path) -> tuple[list[int], list[str]]:
    sends: list[int] = []
    velocities: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            sends.append(number(row["CommomPackage.sendTime"]))
            velocities.append(
                velocity_key(row["VH_1_Sf_INS_struct.Sf_INS_VelocitySpeed"])
            )
    return sends, velocities


def nearest_index(values: list[int], target: int) -> int:
    return min(range(len(values)), key=lambda index: abs(values[index] - target))


def kmp_matches(text: list[str], pattern: list[str]) -> list[int]:
    if not pattern:
        return []
    prefix = [0] * len(pattern)
    length = 0
    for index in range(1, len(pattern)):
        while length and pattern[index] != pattern[length]:
            length = prefix[length - 1]
        if pattern[index] == pattern[length]:
            length += 1
            prefix[index] = length

    matches: list[int] = []
    length = 0
    for index, value in enumerate(text):
        while length and value != pattern[length]:
            length = prefix[length - 1]
        if value == pattern[length]:
            length += 1
        if length == len(pattern):
            matches.append(index - len(pattern) + 1)
            length = prefix[length - 1]
    return matches


def source_csv_dir(item: dict) -> Path | None:
    base = item["source_root"] / item["date"] / "csv_selected"
    names = [item["segment"]]
    match = re.fullmatch(r"(.+)__\d{8}", item["segment"])
    if match:
        names.append(match.group(1))
    for name in names:
        candidate = base / name / "CSV"
        if candidate.is_dir():
            return candidate
    for date_dir in item["source_root"].iterdir():
        if not date_dir.is_dir():
            continue
        for name in names:
            candidate = date_dir / "csv_selected" / name / "CSV"
            if candidate.is_dir():
                return candidate
    return None


def build_segment_model(item: dict) -> tuple[int, Fraction, list[str]]:
    csv_dir = source_csv_dir(item)
    if csv_dir is None:
        raise FileNotFoundError("csv_selected segment is unavailable")

    csv_files = list(csv_dir.glob("*.csv"))
    if not csv_files:
        raise ValueError("CSV directory is empty")
    sub_min = min(first_last_send_time(path)[0] for path in csv_files)
    sub_max = max(first_last_send_time(path)[1] for path in csv_files)

    map_system = csv_dir / "select_VH_1_IDT_Sf_MapLocSrv_SystemSt_struct.csv"
    map_line = csv_dir / "select_VH_1_IDT_Sf_MapLocSrv_Line_struct.csv"
    ins_file = csv_dir / "select_VH_1_Sf_INS_struct.csv"
    for path in (map_system, map_line, ins_file):
        if not path.is_file():
            raise FileNotFoundError(path.name)

    map_start, map_end = map_system_bounds(map_system)
    sub_min = max(sub_min, map_start)
    sub_max = min(sub_max, map_end)
    if sub_max - sub_min < 5_000_000:
        raise ValueError("valid interval is shorter than 5 seconds")

    map_sends, map_receives = read_map_line(map_line)
    start_index = nearest_index(map_sends, sub_min)
    end_index = nearest_index(map_sends, sub_max)
    time_diff = Fraction(
        (sub_min - map_receives[start_index] * 1000)
        + (sub_max - map_receives[end_index] * 1000),
        2,
    )

    ins_sends, ins_velocities = read_ins(ins_file)
    grid_velocities: list[str] = []
    raw_index = 0
    for stamp in range(sub_min, sub_max + 1, 10_000):
        if stamp < ins_sends[0]:
            grid_velocities.append("0")
            continue
        while (
            raw_index + 1 < len(ins_sends)
            and ins_sends[raw_index + 1] <= stamp
        ):
            raw_index += 1
        grid_velocities.append(ins_velocities[raw_index])
    return sub_min, time_diff, grid_velocities


def parse_record_endpoint(record: dict, position: int) -> int:
    match = TIME_PATTERN.fullmatch(str(record.get("Time", "")))
    if match is None:
        raise ValueError("Time format is invalid")
    groups = (match.group(1), match.group(2)) if position == 0 else (match.group(3), match.group(4))
    value = datetime.strptime(
        f"{record['Date']} {groups[0]}.{groups[1]}", "%Y-%m-%d %H:%M:%S.%f"
    ).replace(tzinfo=CHINA_TZ)
    return int(value.timestamp() * 1000)


def segment_bounds(segment: str) -> tuple[int, int] | None:
    match = SEGMENT_TIME_PATTERN.search(segment)
    if match is None:
        return None
    start = datetime.strptime(
        f"{match.group(1)} {match.group(2)}", "%Y%m%d %H%M%S"
    ).replace(tzinfo=CHINA_TZ)
    end = datetime.strptime(
        f"{match.group(3)} {match.group(4)}", "%Y%m%d %H%M%S"
    ).replace(tzinfo=CHINA_TZ)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def milliseconds(value: Fraction) -> int:
    return value.numerator // (value.denominator * 1000)


def format_time(value: Fraction) -> str:
    stamp = milliseconds(value)
    date = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=stamp)
    return date.astimezone(CHINA_TZ).strftime("%H:%M:%S.%f")[:-3]


def format_date(value: Fraction) -> str:
    stamp = milliseconds(value)
    date = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=stamp)
    return date.astimezone(CHINA_TZ).strftime("%Y-%m-%d")


def atomic_write_json(record: dict, path: Path) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        suffix=".tmp",
        delete=False,
        dir=path.parent,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        temporary.replace(path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def read_event_velocities(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return [velocity_key(row["Ego_velocity"]) for row in csv.DictReader(handle)]


def process_segment(
    items: list[dict],
    apply: bool,
    max_error_ms: int,
    update_outside_segment: bool,
    force_update: bool = False,
) -> list[dict]:
    try:
        sub_min, time_diff, grid = build_segment_model(items[0])
    except Exception as error:
        return [
            {"record": str(item["record_path"]), "status": "source_error", "detail": str(error)}
            for item in items
        ]

    results = []
    for item in items:
        try:
            record = item["record"]
            ego = read_event_velocities(item["ego_path"])
            candidates = kmp_matches(grid, ego)
            if len(candidates) != 1:
                status = "velocity_not_found" if not candidates else "velocity_not_unique"
                results.append(
                    {
                        "record": str(item["record_path"]),
                        "status": status,
                        "candidate_count": len(candidates),
                    }
                )
                continue

            start = Fraction(sub_min + candidates[0] * 10_000) - time_diff
            end = Fraction(sub_min + (candidates[0] + len(ego) - 1) * 10_000) - time_diff
            actual_start = milliseconds(start)
            actual_end = milliseconds(end)
            updated_time = f"{format_time(start)} -- {format_time(end)}"
            updated_timestamp = {"t_start": actual_start, "t_end": actual_end}

            if force_update:
                if (
                    record.get("Date") == format_date(start)
                    and record.get("Time") == updated_time
                    and record.get("Timestamp") == updated_timestamp
                ):
                    status = "unchanged"
                elif apply:
                    record["Date"] = format_date(start)
                    record["Time"] = updated_time
                    record["Timestamp"] = updated_timestamp
                    atomic_write_json(record, item["record_path"])
                    status = "forced_updated"
                else:
                    status = "would_force_update"
                results.append(
                    {"record": str(item["record_path"]), "status": status}
                )
                continue

            expected_start = parse_record_endpoint(record, 0)
            expected_end = parse_record_endpoint(record, 1)
            start_error = actual_start - expected_start
            end_error = actual_end - expected_end

            if abs(start_error) > max_error_ms or abs(end_error) > max_error_ms:
                bounds = segment_bounds(item["segment"])
                outside_segment = bounds is not None and (
                    expected_start < bounds[0] or expected_end > bounds[1]
                )
                if update_outside_segment and outside_segment:
                    if apply:
                        record["Date"] = format_date(start)
                        record["Time"] = updated_time
                        record["Timestamp"] = updated_timestamp
                        atomic_write_json(record, item["record_path"])
                        status = "updated_outside_segment"
                    else:
                        status = "would_update_outside_segment"
                    results.append(
                        {
                            "record": str(item["record_path"]),
                            "status": status,
                            "start_error_ms": start_error,
                            "end_error_ms": end_error,
                        }
                    )
                    continue
                results.append(
                    {
                        "record": str(item["record_path"]),
                        "status": "over_threshold",
                        "start_error_ms": start_error,
                        "end_error_ms": end_error,
                    }
                )
                continue

            if (
                record["Time"] == updated_time
                and record.get("Timestamp") == updated_timestamp
            ):
                status = "unchanged"
            elif apply:
                record["Time"] = updated_time
                record["Timestamp"] = updated_timestamp
                atomic_write_json(record, item["record_path"])
                status = "updated"
            else:
                status = "would_update"
            results.append(
                {
                    "record": str(item["record_path"]),
                    "status": status,
                    "start_error_ms": start_error,
                    "end_error_ms": end_error,
                }
            )
        except Exception as error:
            results.append(
                {"record": str(item["record_path"]), "status": "event_error", "detail": str(error)}
            )
    return results


def item_from_record_path(record_path: Path) -> dict:
    city = next(
        city
        for city, (valid_root, _) in CITY_ROOTS.items()
        if record_path.is_relative_to(valid_root)
    )
    valid_root, source_root = CITY_ROOTS[city]
    record = json.loads(record_path.read_text(encoding="utf-8-sig"))
    event_dir = record_path.parent
    ego_path = next(event_dir.glob("*_EgoInfo.csv"), None)
    if ego_path is None:
        raise FileNotFoundError("EgoInfo.csv is unavailable")
    return {
        "city": city,
        "record_path": record_path,
        "record": record,
        "ego_path": ego_path,
        "date": str(record["Date"]).replace("-", ""),
        "segment": event_dir.parent.name,
        "source_root": source_root,
    }


def collect_items(city: str, valid_root: Path, source_root: Path) -> list[dict]:
    output = subprocess.run(
        ["rg", "--files", str(valid_root), "-g", "*_record.json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    items = []
    for raw_path in output.stdout.splitlines():
        record_path = Path(raw_path)
        try:
            items.append(item_from_record_path(record_path))
        except Exception as error:
            items.append(
                {
                    "city": city,
                    "record_path": record_path,
                    "record": {},
                    "ego_path": Path(),
                    "date": "",
                    "segment": "",
                    "source_root": source_root,
                    "pre_error": str(error),
                }
            )
    return items


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rebuild valid-event Time fields from INS velocity and MapLoc receiveTime."
    )
    parser.add_argument("--apply", action="store_true", help="Write eligible Time updates.")
    parser.add_argument(
        "--force-update",
        action="store_true",
        help="Write each uniquely reconstructed interval without reading the current Time.",
    )
    parser.add_argument("--max-error-ms", type=int, default=100)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--limit", type=int, default=0, help="Process at most this many records.")
    parser.add_argument(
        "--update-outside-segment",
        action="store_true",
        help="Update unique matches whose current JSON interval falls outside the segment interval.",
    )
    parser.add_argument(
        "--input-report",
        type=Path,
        help="Process selected records from an earlier summary JSON.",
    )
    parser.add_argument(
        "--input-status",
        default="over_threshold",
        help="Comma-separated statuses to process with --input-report.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path(__file__).with_name("S26_record_time_update_summary.json"),
    )
    args = parser.parse_args()

    if args.input_report:
        source_report = json.loads(args.input_report.read_text(encoding="utf-8"))
        input_statuses = {status.strip() for status in args.input_status.split(",")}
        source_paths = [
            Path(result["record"])
            for result in source_report["results"]
            if result["status"] in input_statuses
        ]
        items = []
        for record_path in source_paths:
            try:
                items.append(item_from_record_path(record_path))
            except Exception as error:
                items.append(
                    {
                        "record_path": record_path,
                        "record": {},
                        "ego_path": Path(),
                        "date": "",
                        "segment": "",
                        "source_root": Path(),
                        "pre_error": str(error),
                    }
                )
    else:
        items = []
        for city, (valid_root, source_root) in CITY_ROOTS.items():
            items.extend(collect_items(city, valid_root, source_root))
    if args.limit:
        items = items[: args.limit]

    pre_errors = [item for item in items if "pre_error" in item]
    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for item in items:
        if "pre_error" not in item:
            groups[(item["city"], item["date"], item["segment"])].append(item)

    results = [
        {"record": str(item["record_path"]), "status": "event_error", "detail": item["pre_error"]}
        for item in pre_errors
    ]
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = [
            executor.submit(
                process_segment,
                group,
                args.apply,
                args.max_error_ms,
                args.update_outside_segment,
                args.force_update,
            )
            for group in groups.values()
        ]
        for completed, future in enumerate(as_completed(futures), 1):
            results.extend(future.result())
            if completed % 25 == 0 or completed == len(futures):
                print(f"processed segments: {completed}/{len(futures)}", flush=True)

    counts = Counter(result["status"] for result in results)
    report = {
        "apply": args.apply,
        "max_error_ms": args.max_error_ms,
        "update_outside_segment": args.update_outside_segment,
        "force_update": args.force_update,
        "input_statuses": sorted(input_statuses) if args.input_report else None,
        "records": len(results),
        "status_counts": dict(counts),
        "results": results,
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(args.report), **report["status_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
