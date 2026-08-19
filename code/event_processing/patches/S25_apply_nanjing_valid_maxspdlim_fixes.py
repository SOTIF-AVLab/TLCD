from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s7_record_json"))
import S24_audit_nanjing_valid_maxspdlim as audit
import s7_event_json as s7


AUDIT_PATH = (
    Path(__file__).resolve().parent
    / "audit_outputs"
    / "MaxSpdlim_principle_audit_20260709.json"
)
MANIFEST_PATH = (
    Path(__file__).resolve().parent
    / "audit_outputs"
    / "MaxSpdlim_fixes_20260709_manifest.json"
)


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8-sig",
        newline="",
        suffix=".tmp",
        delete=False,
        dir=path.parent,
    ) as handle:
        temp_path = Path(handle.name)
        frame.to_csv(handle, index=False)
    try:
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def atomic_write_json(data: dict, path: Path) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        suffix=".tmp",
        delete=False,
        dir=path.parent,
    ) as handle:
        temp_path = Path(handle.name)
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    try:
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def validate_event_path(raw_path: str, root: Path) -> Path:
    event_path = Path(raw_path).resolve()
    root = root.resolve()
    if not event_path.is_relative_to(root):
        raise ValueError(f"event path is outside root: {event_path}")
    if event_path.parent.parent != root:
        raise ValueError(f"unexpected event directory depth: {event_path}")
    if not re.fullmatch(r"event_\d+", event_path.name):
        raise ValueError(f"unexpected event directory name: {event_path}")
    if not event_path.is_dir():
        raise FileNotFoundError(event_path)
    return event_path


def repair_event(event_path: Path) -> dict:
    event_number = int(event_path.name.split("_")[1])
    prefix = f"MaxSpdlim_event_{event_number}"
    map_path = event_path / f"{prefix}_MapInfo.csv"
    evidence_path = event_path / f"{prefix}_EvidenceChain.csv"
    record_path = event_path / f"{prefix}_record.json"

    map_info = pd.read_csv(map_path, encoding="utf-8-sig")
    evidence = pd.read_csv(evidence_path, encoding="utf-8-sig")
    if len(map_info) != len(evidence):
        raise ValueError(
            f"row count mismatch during repair: {event_path} "
            f"map={len(map_info)} evidence={len(evidence)}"
        )

    expected, diagnostics = audit.expected_evidence(map_info, evidence)
    if diagnostics["no_valid_limit"].any():
        raise ValueError(f"no valid maximum limit during repair: {event_path}")
    actual_numeric = evidence[audit.COMPARE_COLUMNS].apply(audit.numeric)
    expected_numeric = expected[audit.COMPARE_COLUMNS].apply(audit.numeric)
    differences = ~(
        actual_numeric.eq(expected_numeric)
        | (actual_numeric.isna() & expected_numeric.isna())
    )
    changed_rows = int(differences.any(axis=1).sum())
    if changed_rows == 0:
        raise ValueError(f"repair target no longer has evidence differences: {event_path}")
    atomic_write_csv(expected, evidence_path)

    current_record = json.loads(record_path.read_text(encoding="utf-8-sig"))
    rebuilt = s7._record(
        evidence_path,
        event_number,
        s7.CATEGORIES["max_speed"],
        current_record.get("Location", "China, Nanjing"),
        {},
    )
    for key in ("Location", "Date", "Time"):
        rebuilt[key] = current_record.get(key, rebuilt.get(key, ""))
    rebuilt["Evidence"].pop("Sign_description", None)
    rebuilt["Evidence"].pop("Speed_limit_sign_effective", None)
    scenario = rebuilt["Result"].get("Scenario_description", "")
    rebuilt["Result"]["Scenario_description"] = scenario.split(
        " The recorded sign description", 1
    )[0]
    atomic_write_json(rebuilt, record_path)

    verification = audit.audit_event(event_path)
    if verification["status"] != "PASS":
        raise ValueError(
            f"post-repair verification failed: {event_path} "
            f"issue={verification.get('issue')}"
        )
    return {
        "event_path": str(event_path),
        "changed_rows": changed_rows,
        "total_frames": len(expected),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Delete 8 invalid events and repair 40 Nanjing_valid MaxSpdlim events."
    )
    parser.add_argument("--audit", type=Path, default=AUDIT_PATH)
    parser.add_argument("--root", type=Path, default=audit.DATA_ROOT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    args = parser.parse_args()

    report = json.loads(args.audit.read_text(encoding="utf-8"))
    delete_rows = [
        row
        for row in report["review_events"]
        if row["status"] == "DATA_ISSUE" or row.get("no_valid_limit_frames", 0) > 0
    ]
    repair_rows = [
        row
        for row in report["review_events"]
        if row.get("mismatch_frames", 0) > 0
        and row["status"] != "DATA_ISSUE"
        and not row.get("no_valid_limit_frames", 0)
    ]
    if len(delete_rows) != 8 or len(repair_rows) != 40:
        raise ValueError(
            f"unexpected scope: delete={len(delete_rows)} repair={len(repair_rows)}"
        )

    delete_paths = [
        validate_event_path(row["event_path"], args.root) for row in delete_rows
    ]
    repair_paths = [
        validate_event_path(row["event_path"], args.root) for row in repair_rows
    ]
    if set(delete_paths) & set(repair_paths):
        raise ValueError("delete and repair scopes overlap")

    repaired = [repair_event(path) for path in repair_paths]
    deleted = []
    for path in delete_paths:
        shutil.rmtree(path)
        if path.exists():
            raise OSError(f"failed to delete event directory: {path}")
        deleted.append(str(path))

    manifest = {
        "source_audit": str(args.audit),
        "root": str(args.root),
        "deleted_count": len(deleted),
        "repaired_count": len(repaired),
        "deleted_events": deleted,
        "repaired_events": repaired,
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest, args.manifest)
    print(json.dumps(
        {
            "deleted_count": len(deleted),
            "repaired_count": len(repaired),
            "changed_rows": sum(row["changed_rows"] for row in repaired),
            "manifest": str(args.manifest),
        },
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
