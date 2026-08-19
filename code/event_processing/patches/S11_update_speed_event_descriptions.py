from __future__ import annotations

import csv
import re
from collections import defaultdict, deque
from pathlib import Path


ROOT = Path(r"Z:\HongqiData\Nanjing")
MAX_MD = Path(r"D:\下载\最高限速事件-场景描述.md")
MIN_MD = Path(r"D:\下载\最低限速事件-场景描述.md")


def read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise UnicodeDecodeError("unknown", b"", 0, 1, str(path))


def split_md_row(line: str) -> list[str]:
    line = line.strip()
    if not line.startswith("|") or re.match(r"^\|\s*-", line):
        return []
    cells = [cell.strip() for cell in line.strip("|").split("|")]
    return cells


def polish_description(text: str) -> str:
    text = text.strip().strip("`").strip()
    text = text.strip(' "\',，,')
    text = re.sub(r"\s+", "", text)
    text = text.replace("MapInfoC0趋势", "地图车道线变化趋势")
    text = text.replace("视频与地图车道线变化趋势均支持", "视频和地图车道线变化趋势均支持")
    text = text.replace("视频中未见明显主动换道", "视频中未见明显主动换道行为")
    text = text.replace("未见明显换道", "未见明显主动换道")
    text = text.replace("更像道路", "更像是道路")
    text = text.replace("场景支持道路规则类最低限速事件语义", "场景符合道路规则类最低限速事件")
    text = text.replace("场景支持最低限速标志牌事件语义", "场景符合最低限速标志牌事件")
    text = text.replace("场景支持最高限速标志牌事件语义", "场景符合最高限速标志牌事件")
    text = text.replace("的道路规则类事件语义", "的道路规则类事件")
    text = text.replace("事件语义", "事件")
    text = re.sub(r"经过(\d+)\s*限速牌", r"经过\1 km/h限速标志牌", text)
    text = re.sub(r"限速为(\d+)\s*km/h", r"限速为\1 km/h", text)
    text = re.sub(r"最低限速(\d+)", r"最低限速\1 km/h", text)
    text = re.sub(r"最高限速(\d+)", r"最高限速\1 km/h", text)
    text = text.replace("；。", "。").replace("，。", "。")
    text = text.rstrip("。；;，, ")
    return text + "。"


def parse_max_descriptions() -> dict[tuple[str, str, int], str]:
    descriptions: dict[tuple[str, str, int], str] = {}
    for line in read_text(MAX_MD).splitlines():
        cells = split_md_row(line)
        if len(cells) != 5 or cells[0] == "序号":
            continue
        date, event_num_text, folder, description = cells[1], cells[2], cells[3], cells[4]
        match = re.search(r"(\d{8})\\zEvent_MaxSpdlim\\(.+?_CSV)$", folder)
        if not match:
            continue
        key = (date, match.group(2), int(event_num_text))
        descriptions[key] = polish_description(description)
    return descriptions


def parse_min_descriptions() -> dict[tuple[str, str], deque[str]]:
    descriptions: dict[tuple[str, str], deque[str]] = defaultdict(deque)
    for line in read_text(MIN_MD).splitlines():
        cells = split_md_row(line)
        if len(cells) != 3 or cells[0] == "序号":
            continue
        folder = cells[1].strip("`")
        description = cells[2]
        match = re.search(r"(\d{8})\\zEvent_MinSpdlim\\(.+?_CSV)$", folder)
        if not match:
            continue
        descriptions[(match.group(1), match.group(2))].append(polish_description(description))
    return descriptions


def event_files(event_kind: str) -> list[Path]:
    return sorted(ROOT.glob(f"*/zEvent_{event_kind}/*/{event_kind}_events.csv"))


def update_max_files(descriptions: dict[tuple[str, str, int], str]) -> tuple[int, int]:
    updated = 0
    event_count = 0
    for path in event_files("MaxSpdlim"):
        date = path.parents[2].name
        segment = path.parent.name
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = list(reader.fieldnames or [])
        changed = False
        for row in rows:
            event_count += 1
            key = (date, segment, int(row["event_num"]))
            if key not in descriptions:
                continue
            if row.get("Event_description", "") != descriptions[key]:
                row["Event_description"] = descriptions[key]
                changed = True
            updated += 1
        if changed:
            write_csv(path, fieldnames, rows)
    return event_count, updated


def update_min_files(descriptions: dict[tuple[str, str], deque[str]]) -> tuple[int, int, int, int]:
    updated = 0
    event_count = 0
    valid_without_md = 0
    for path in event_files("MinSpdlim"):
        date = path.parents[2].name
        segment = path.parent.name
        key = (date, segment)
        queue = descriptions.get(key, deque())
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            fieldnames = list(reader.fieldnames or [])
        changed = False
        for row in rows:
            event_count += 1
            if row.get("Event_Validity", "") != "1":
                continue
            if not queue:
                valid_without_md += 1
                continue
            description = queue.popleft()
            if row.get("Event_description", "") != description:
                row["Event_description"] = description
                changed = True
            updated += 1
        if changed:
            write_csv(path, fieldnames, rows)
    leftover_md = sum(len(items) for items in descriptions.values())
    return event_count, updated, valid_without_md, leftover_md


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    max_descriptions = parse_max_descriptions()
    min_descriptions = parse_min_descriptions()
    min_description_count = sum(len(v) for v in min_descriptions.values())
    if not max_descriptions or not min_descriptions:
        raise RuntimeError(
            f"Parsed description counts are suspicious: "
            f"max={len(max_descriptions)}, min={sum(len(v) for v in min_descriptions.values())}"
        )

    max_events, max_updated = update_max_files(max_descriptions)
    min_events, min_updated, valid_without_md, leftover_md = update_min_files(min_descriptions)

    print(f"Parsed max descriptions: {len(max_descriptions)}")
    print(f"Parsed min descriptions: {min_description_count}")
    print(f"MaxSpdlim: events={max_events}, descriptions_updated={max_updated}")
    print(
        f"MinSpdlim: events={min_events}, descriptions_updated={min_updated}, "
        f"valid_without_md={valid_without_md}, leftover_md={leftover_md}"
    )
    if max_updated != len(max_descriptions) or leftover_md:
        raise RuntimeError("Description update did not fully match the markdown records.")


if __name__ == "__main__":
    main()
