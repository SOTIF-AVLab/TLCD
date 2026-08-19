from pathlib import Path
import sys


ROOT = Path(r"Z:\HongqiData\Nanjing2")
PATTERN = "*_EgoInfo.csv"
OLD_HEADERS = (b"Ego_vx", b"Ego_INS_Velocity")
NEW_HEADER = b"Ego_velocity"


def patch_file(path: Path) -> str:
    data = path.read_bytes()
    newline_index = data.find(b"\n")
    header_end = len(data) if newline_index == -1 else newline_index + 1
    header = data[:header_end]

    matched_header = None
    for old_header in OLD_HEADERS:
        if old_header in header:
            matched_header = old_header
            break

    if matched_header is None:
        return "skipped"

    updated_header = header.replace(matched_header, NEW_HEADER)
    path.write_bytes(updated_header + data[header_end:])
    return "patched"


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT

    print(f"Scanning root: {root}")
    print(f"File pattern: {PATTERN}")

    if not root.exists():
        print(f"Root path does not exist: {root}")
        return 1

    csv_paths = list(root.rglob(PATTERN))
    total = len(csv_paths)
    print(f"Found {total} matching CSV files.")

    patched = 0
    skipped = 0
    failed = 0

    for index, csv_path in enumerate(csv_paths, start=1):
        print(f"[{index}/{total}] Processing: {csv_path}")

        try:
            result = patch_file(csv_path)
        except OSError as exc:
            failed += 1
            print(f"[{index}/{total}] FAILED: {exc}")
            continue

        if result == "patched":
            patched += 1
            print(f"[{index}/{total}] PATCHED")
        else:
            skipped += 1
            print(f"[{index}/{total}] SKIPPED: header not found")

    print(f"Done. patched={patched}, skipped={skipped}, failed={failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
