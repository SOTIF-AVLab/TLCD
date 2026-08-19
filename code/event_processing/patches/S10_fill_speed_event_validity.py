from __future__ import annotations

import csv
import re
from pathlib import Path


ROOT = Path(r"Z:\HongqiData\Nanjing")
MAX_MD = Path(r"D:\下载\最高限速有效事件清单.md")
MIN_MD = Path(r"D:\下载\最低限速事件有效性统计.md")
SPECIAL_DESCRIPTION = "通过依车辆类型限速的标志牌"


SPECIAL_MIN_EVENTS = {
    ("20240910", "D-6.0-22-20240910_112644_238-20240910_113144_234_CSV", 1),
    ("20240910", "D-6.0-22-20240910_112644_238-20240910_113144_234_CSV", 3),
    ("20240910", "D-6.0-22-20240910_151638_769-20240910_152138_740_CSV", 1),
    ("20240910", "D-6.0-22-20240910_151638_769-20240910_152138_740_CSV", 5),
    ("20240925", "D-6.0-22-20240925_154851_926-20240925_155351_837_CSV", 4),
    ("20240927", "D-6.0-28-20240927_110120_086-20240927_110620_075_CSV", 2),
    ("20240927", "D-6.0-28-20240927_110620_075-20240927_111120_105_CSV", 4),
    ("20240929", "D-6.0-28-20240929_113846_530-20240929_114346_378_CSV", 2),
    ("20240930", "D-6.0-22-20240930_153411_574-20240930_153910_889_CSV", 1),
    ("20240930", "D-6.0-22-20240930_153411_574-20240930_153910_889_CSV", 3),
    ("20241008", "D-6.0-28-20241008_160411_644-20241008_160911_617_CSV", 5),
    ("20241011", "D-6.0-27-20241011_110924_265-20241011_111424_015_CSV", 1),
    ("20241011", "D-6.0-27-20241011_110924_265-20241011_111424_015_CSV", 3),
    ("20241012", "D-6.0-27-20241012_151945_749-20241012_152446_003_CSV", 1),
    ("20241014", "D-6.0-27-20241014_154401_720-20241014_154901_710_CSV", 3),
    ("20241021", "D-6.0-31-20241021_160557_780-20241021_161057_787_CSV", 2),
    ("20241021", "D-6.0-31-20241021_161244_805-20241021_161744_900_CSV", 1),
    ("20241021", "D-6.0-31-20241021_161244_805-20241021_161744_900_CSV", 5),
    ("20241022", "D-6.0-31-20241022_150014_730-20241022_150514_745_CSV", 1),
    ("20241022", "D-6.0-31-20241022_150014_730-20241022_150514_745_CSV", 5),
    ("20241023", "D-6.0-31-20241023_093700_685-20241023_094200_692_CSV", 6),
}


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, str(path))


def parse_max_keys() -> set[tuple[str, str, int]]:
    keys: set[tuple[str, str, int]] = set()
    pattern = re.compile(
        r"\|\s*\d+\s*\|\s*(\d{8})\s*\|\s*(\d+)\s*\|\s*"
        r"(\d{8}\\zEvent_MaxSpdlim\\([^|`]+?))\s*\|"
    )
    for match in pattern.finditer(read_text(MAX_MD)):
        date = match.group(1)
        event_num = int(match.group(2))
        segment = match.group(4).strip()
        keys.add((date, segment, event_num))
    return keys


def parse_min_keys() -> set[tuple[str, str, int]]:
    keys: set[tuple[str, str, int]] = set()
    pattern = re.compile(
        r"\|\s*(\d{8})\s*\|\s*(D-[^|`]+?_CSV)\s*\|\s*(\d+)\s*\|"
    )
    for match in pattern.finditer(read_text(MIN_MD)):
        keys.add((match.group(1), match.group(2).strip(), int(match.group(3))))
    return keys


def event_files(event_kind: str) -> list[Path]:
    glob = f"*/zEvent_{event_kind}/*/{event_kind}_events.csv"
    return sorted(ROOT.glob(glob))


def update_events_file(path: Path, valid_keys: set[tuple[str, str, int]], event_kind: str) -> tuple[int, int, int]:
    date = path.parents[2].name
    segment = path.parent.name

    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    required = {"event_num", "Event_Validity", "Event_description"}
    missing = required.difference(fieldnames)
    if missing:
        raise ValueError(f"{path} missing columns: {sorted(missing)}")

    valid_count = 0
    special_count = 0
    changed = 0
    for row in rows:
        event_num = int(row["event_num"])
        key = (date, segment, event_num)
        validity = "1" if key in valid_keys else "0"
        description = row["Event_description"]

        if event_kind == "MinSpdlim" and key in SPECIAL_MIN_EVENTS:
            validity = "1"
            description = SPECIAL_DESCRIPTION
            special_count += 1

        if row["Event_Validity"] != validity:
            row["Event_Validity"] = validity
            changed += 1
        if row["Event_description"] != description:
            row["Event_description"] = description
            changed += 1
        if validity == "1":
            valid_count += 1

    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)

    return len(rows), valid_count, special_count


def main() -> None:
    max_keys = parse_max_keys()
    min_keys = parse_min_keys()
    if not max_keys or not min_keys:
        raise RuntimeError(f"Parsed key counts are suspicious: max={len(max_keys)}, min={len(min_keys)}")

    totals = {}
    for event_kind, keys in (("MaxSpdlim", max_keys), ("MinSpdlim", min_keys)):
        files = event_files(event_kind)
        event_total = valid_total = special_total = 0
        for path in files:
            events, valid, special = update_events_file(path, keys, event_kind)
            event_total += events
            valid_total += valid
            special_total += special
        totals[event_kind] = (len(files), event_total, valid_total, special_total)

    print(f"Parsed max valid keys: {len(max_keys)}")
    print(f"Parsed min valid keys: {len(min_keys)}")
    for event_kind, (file_count, event_total, valid_total, special_total) in totals.items():
        print(
            f"{event_kind}: files={file_count}, events={event_total}, "
            f"valid={valid_total}, special={special_total}"
        )


if __name__ == "__main__":
    main()
