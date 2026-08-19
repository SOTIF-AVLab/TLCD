from __future__ import annotations

import shutil
from pathlib import Path

import pandas as pd


ROOT = Path(r"Z:\HongqiData")
TARGET_ROOT_NAME = "zEvent_MaxSpdlim_sign_invalid"
SOURCES = (
    ("Nanjing", "zEvent_MaxSpdlim_sign_1"),
    ("Nanjing", "zEvent_MaxSpdlim_sign_2"),
    ("Changchun", "zEvent_MaxSpdlim_sign_1"),
)


class LockedEventCsvError(Exception):
    pass


def read_events(path: Path) -> tuple[pd.DataFrame, str]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return pd.read_csv(path, encoding=encoding), encoding
        except UnicodeDecodeError:
            pass
    raise UnicodeDecodeError("csv", b"", 0, 1, f"cannot decode {path}")


def assert_writable(path: Path) -> None:
    try:
        with path.open("a", encoding="utf-8"):
            pass
    except PermissionError as exc:
        raise LockedEventCsvError(path) from exc


def write_events(df: pd.DataFrame, path: Path, encoding: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding=encoding)


def move_path(src: Path, dst: Path) -> bool:
    if not src.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.move(str(src), str(dst))
    return True


def event_dirs(city_root: Path, event_root_name: str) -> list[Path]:
    roots = []
    for date_dir in sorted(path for path in city_root.iterdir() if path.is_dir()):
        event_root = date_dir / event_root_name
        if not event_root.is_dir():
            continue
        if (event_root / "MaxSpdlim_events.csv").exists():
            roots.append(event_root)
        roots.extend(sorted(path.parent for path in event_root.glob("*/MaxSpdlim_events.csv")))
    return roots


def destination_dir(src_event_dir: Path, event_root_name: str) -> Path:
    date_dir = next(parent for parent in src_event_dir.parents if parent.name.isdigit() and len(parent.name) == 8)
    src_event_root = date_dir / event_root_name
    dst_event_root = date_dir / TARGET_ROOT_NAME
    if src_event_dir == src_event_root:
        return dst_event_root
    return dst_event_root / src_event_dir.relative_to(src_event_root)


def move_event_files(src_event_dir: Path, dst_event_dir: Path, event_num: int) -> int:
    moved = 0
    for src in sorted(src_event_dir.glob(f"MaxSpdlim_event_{event_num}_*")):
        if src.is_file():
            moved += int(move_path(src, dst_event_dir / src.name))
    event_video_name = f"event_{event_num:03d}.*"
    for src in sorted(src_event_dir.glob(f"video_*/mp4/{event_video_name}")):
        if src.is_file():
            moved += int(move_path(src, dst_event_dir / src.relative_to(src_event_dir)))
    for src in sorted(src_event_dir.glob(f"video_*/{event_video_name}")):
        if src.is_file():
            moved += int(move_path(src, dst_event_dir / src.relative_to(src_event_dir)))
    return moved


def process_event_dir(src_event_dir: Path, event_root_name: str) -> dict[str, object]:
    events_path = src_event_dir / "MaxSpdlim_events.csv"
    events, encoding = read_events(events_path)
    validity = pd.to_numeric(events["Event_Validity"], errors="coerce").fillna(0)
    invalid_mask = validity != 1
    invalid = events.loc[invalid_mask].copy()
    valid = events.loc[~invalid_mask].copy()
    if invalid.empty:
        return {"event_dir": str(src_event_dir), "invalid_events": 0, "moved_files": 0}
    assert_writable(events_path)

    dst_event_dir = destination_dir(src_event_dir, event_root_name)
    dst_events_path = dst_event_dir / "MaxSpdlim_events.csv"
    if dst_events_path.exists():
        dst_events, dst_encoding = read_events(dst_events_path)
        combined = pd.concat([dst_events, invalid], ignore_index=True)
        combined = combined.drop_duplicates(subset=["event_num"], keep="last")
        combined = combined.sort_values("event_num").reset_index(drop=True)
        write_events(combined, dst_events_path, dst_encoding)
    else:
        write_events(invalid, dst_events_path, encoding)
    write_events(valid, events_path, encoding)

    moved_files = 0
    for event_num in invalid["event_num"].astype(int):
        moved_files += move_event_files(src_event_dir, dst_event_dir, event_num)
    return {
        "event_dir": str(src_event_dir),
        "invalid_events": int(len(invalid)),
        "moved_files": moved_files,
        "destination": str(dst_event_dir),
    }


def main() -> None:
    rows = []
    for city, event_root_name in SOURCES:
        city_root = ROOT / city
        for src_event_dir in event_dirs(city_root, event_root_name):
            try:
                result = process_event_dir(src_event_dir, event_root_name)
            except LockedEventCsvError as exc:
                result = {
                    "event_dir": str(src_event_dir),
                    "invalid_events": 0,
                    "moved_files": 0,
                    "destination": "",
                    "skipped": f"locked csv: {exc}",
                }
                print(f"SKIPPED {city} {event_root_name} {src_event_dir}: locked MaxSpdlim_events.csv")
            result["city"] = city
            result["source_root"] = event_root_name
            rows.append(result)
            if result["invalid_events"]:
                print(
                    f"{city} {event_root_name} {Path(result['event_dir']).name}: "
                    f"invalid={result['invalid_events']}, moved_files={result['moved_files']}"
                )

    summary = pd.DataFrame(rows)
    summary_path = Path(__file__).resolve().parent / "S23_move_invalid_maxspdlim_sign_events_summary.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    print(f"summary={summary_path}")
    if not summary.empty:
        print(summary.groupby(["city", "source_root"], dropna=False)[["invalid_events", "moved_files"]].sum().to_string())


if __name__ == "__main__":
    main()
