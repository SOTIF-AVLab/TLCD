#!/usr/bin/env python3
"""Generate reproducible event-level statistics from TLCD record.json files."""

import csv
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DATASET_ROOT = Path(
    os.environ.get("TLCD_DATASET_ROOT", REPOSITORY_ROOT / "Dataset")
).expanduser()
OUTPUT_DIR = Path(
    os.environ.get("TLCD_OUTPUT_DIR", REPOSITORY_ROOT / "statistics")
).expanduser()
CITY_MAP = {"Changchun_valid": "Changchun", "Nanjing_valid": "Nanjing"}
CATEGORY_ORDER = [
    "MaxSpdlim",
    "MinSpdlim",
    "FollowDis",
    "LateralDis",
    "LaneChange",
    "ContinueLaneChange",
    "RoadMarking",
    "Overtake",
]
COMBINATION_ORDER = ["CAL", "CAI", "CML", "CMI", "NAL", "NAI", "NML", "NMI"]
JSON_PREFIX = {"LaneChange": "lane_change", "ContinueLaneChange": "ContinueLC"}


def list_segments(category_item):
    city_dir, category_dir, category_path = category_item
    return [
        (city_dir, category_dir, os.path.join(category_path, name))
        for name in sorted(os.listdir(category_path))
    ]


def list_event_jsons(segment_item):
    city_dir, category_dir, segment_path = segment_item
    category = category_dir.split("_", 1)[1]
    output = []
    for name in sorted(os.listdir(segment_path)):
        if not name.startswith("event_"):
            continue
        try:
            event_number = int(name.split("_", 1)[1])
        except ValueError:
            continue
        prefix = JSON_PREFIX.get(category, category)
        event_suffix = name.split("_", 1)[1]
        filenames = [
            f"{prefix}_event_{event_number}_record.json",
            f"{prefix}_event_{event_suffix}_record.json",
        ]
        candidate_paths = list(dict.fromkeys(os.path.join(segment_path, name, item) for item in filenames))
        output.append(
            {
                "city_dir": city_dir,
                "category_dir": category_dir,
                "category": category,
                "segment": os.path.basename(segment_path),
                "event_dir": name,
                "path": candidate_paths[0],
                "candidate_paths": candidate_paths,
            }
        )
    return output


def discover_event_jsons():
    categories = []
    for city_dir in sorted(os.listdir(DATASET_ROOT)):
        if city_dir not in CITY_MAP:
            continue
        city_path = DATASET_ROOT / city_dir
        for category_dir in sorted(os.listdir(city_path)):
            if len(category_dir) >= 4 and category_dir[2] == "_":
                categories.append((city_dir, category_dir, str(city_path / category_dir)))

    with ThreadPoolExecutor(max_workers=16) as executor:
        segments = [item for group in executor.map(list_segments, categories) for item in group]
    with ThreadPoolExecutor(max_workers=48) as executor:
        events = [item for group in executor.map(list_event_jsons, segments) for item in group]
    return sorted(events, key=lambda x: x["path"])


def load_event(event):
    for candidate in event["candidate_paths"]:
        try:
            with open(candidate, "r", encoding="utf-8-sig") as handle:
                data = json.load(handle)
            event["path"] = candidate
            return event, data, "", ""
        except FileNotFoundError:
            continue
        except Exception as exc:  # keep parse/read failures in the audit output
            return event, None, "parse_error", f"{type(exc).__name__}: {exc}"
    return event, None, "missing_json", "No record.json matched the known naming conventions"


def normalize_text(value):
    if isinstance(value, list):
        return " | ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def driving_code(value):
    text = normalize_text(value).strip().lower()
    if "autonomous" in text:
        return "A"
    if "manual" in text or "human" in text:
        return "M"
    return "U"


def compliance_code(value):
    text = normalize_text(value).strip().lower().replace("_", " ").replace("-", " ")
    text = " ".join(text.split())
    if text == "compliance" or text == "compliant":
        return "L"
    if text in {"violation", "violating", "non compliance", "noncompliance"}:
        return "I"
    return "U"


def article_ids(value):
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    output = []
    for item in values:
        output.extend(part.strip() for part in re.split(r"\s*(?:&|;)\s*", str(item)) if part.strip())
    return output


def write_csv(path, rows, fieldnames):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(headers, rows):
    def clean(value):
        return str(value).replace("|", "\\|").replace("\n", " ")

    output = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    output.extend("| " + " | ".join(clean(value) for value in row) + " |" for row in rows)
    return "\n".join(output)


def main():
    event_files = discover_event_jsons()
    print(f"Discovered {len(event_files)} event directories; loading JSON files...", flush=True)

    with ThreadPoolExecutor(max_workers=64) as executor:
        loaded = list(executor.map(load_event, event_files))

    event_rows = []
    law_rows = []
    missing_json = []
    parse_errors = []
    article_ids_missing_status = []
    extra_article_status_ids = []
    empty_reported_article_ids = []

    for event, data, error_kind, error in loaded:
        if error_kind:
            issue = {"event_dir": os.path.dirname(event["path"]), "error": error}
            if error_kind == "missing_json":
                missing_json.append(issue)
            else:
                parse_errors.append(issue)
            continue

        city = CITY_MAP[event["city_dir"]]
        driving_raw = normalize_text(data.get("DrivingMode"))
        event_status_raw = normalize_text(data.get("Result", {}).get("Compliance_label"))
        drive = driving_code(driving_raw)
        event_status = compliance_code(event_status_raw)
        reported_ids = article_ids(data.get("Article", {}).get("ID"))
        status_map = data.get("Evidence", {}).get("Article_status", {})
        if not isinstance(status_map, dict):
            status_map = {}
        status_ids = [str(item) for item in status_map]

        missing_status_ids = sorted(set(reported_ids) - set(status_ids))
        extra_status_ids = sorted(set(status_ids) - set(reported_ids))
        if not reported_ids:
            empty_reported_article_ids.append({"path": event["path"]})
        if missing_status_ids:
            article_ids_missing_status.append(
                {
                    "path": event["path"],
                    "article_ids_missing_status": ";".join(missing_status_ids),
                }
            )
        if extra_status_ids:
            extra_article_status_ids.append(
                {
                    "path": event["path"],
                    "extra_status_ids": ";".join(extra_status_ids),
                }
            )

        event_rows.append(
            {
                "event_id": os.path.relpath(event["path"], DATASET_ROOT),
                "city": city,
                "category": event["category"],
                "segment": event["segment"],
                "event_dir": event["event_dir"],
                "location_raw": normalize_text(data.get("Location")),
                "driving_mode_raw": driving_raw,
                "driving_code": drive,
                "compliance_label_raw": event_status_raw,
                "compliance_code": event_status,
                "article_ids_reported": ";".join(reported_ids),
                "article_status_ids": ";".join(status_ids),
                "json_path": event["path"],
            }
        )

        for law_id in reported_ids:
            raw_status = status_map.get(law_id, "")
            law_rows.append(
                {
                    "event_id": os.path.relpath(event["path"], DATASET_ROOT),
                    "city": city,
                    "category": event["category"],
                    "article_id": str(law_id),
                    "article_status_raw": normalize_text(raw_status),
                    "event_compliance_label_raw": event_status_raw,
                    "event_compliance_code": event_status,
                }
            )

    category_rank = {name: i for i, name in enumerate(CATEGORY_ORDER)}
    event_rows.sort(key=lambda r: (category_rank[r["category"]], r["city"], r["event_id"]))
    law_rows.sort(key=lambda r: (r["city"], r["article_id"], r["event_id"]))

    city_category = []
    for city in ["Changchun", "Nanjing"]:
        for category in CATEGORY_ORDER:
            subset = [r for r in event_rows if r["city"] == city and r["category"] == category]
            city_category.append(
                {
                    "city": city,
                    "category": category,
                    "total_events": len(subset),
                    "compliant_events": sum(r["compliance_code"] == "L" for r in subset),
                    "violating_events": sum(r["compliance_code"] == "I" for r in subset),
                }
            )

    city_article = []
    for city in ["Changchun", "Nanjing"]:
        law_ids = sorted({r["article_id"] for r in law_rows if r["city"] == city})
        for law_id in law_ids:
            subset = [r for r in law_rows if r["city"] == city and r["article_id"] == law_id]
            city_article.append(
                {
                    "city": city,
                    "article_id": law_id,
                    "total_events": len(subset),
                    "compliant_events": sum(r["event_compliance_code"] == "L" for r in subset),
                    "violating_events": sum(r["event_compliance_code"] == "I" for r in subset),
                }
            )

    category_compliance = []
    category_driving = []
    combinations = []
    for category in CATEGORY_ORDER:
        subset = [r for r in event_rows if r["category"] == category]
        category_compliance.append(
            {
                "category": category,
                "total_events": len(subset),
                "compliant_events": sum(r["compliance_code"] == "L" for r in subset),
                "violating_events": sum(r["compliance_code"] == "I" for r in subset),
            }
        )
        category_driving.append(
            {
                "category": category,
                "total_events": len(subset),
                "autonomous_events": sum(r["driving_code"] == "A" for r in subset),
                "manual_events": sum(r["driving_code"] == "M" for r in subset),
            }
        )
        state_counts = Counter(
            ("C" if r["city"] == "Changchun" else "N")
            + r["driving_code"]
            + r["compliance_code"]
            for r in subset
        )
        for state in COMBINATION_ORDER:
            count = state_counts[state]
            combinations.append(
                {
                    "category": category,
                    "combination": state,
                    "count": count,
                    "share_pct_within_category": round(100 * count / len(subset), 2) if subset else 0,
                    "category_total": len(subset),
                }
            )

    write_csv(OUTPUT_DIR / "event_manifest.csv", event_rows, list(event_rows[0]))
    write_csv(OUTPUT_DIR / "article_event_manifest.csv", law_rows, list(law_rows[0]))
    write_csv(OUTPUT_DIR / "01_city_category.csv", city_category, list(city_category[0]))
    write_csv(OUTPUT_DIR / "01_city_article.csv", city_article, list(city_article[0]))
    write_csv(OUTPUT_DIR / "02_category_compliance.csv", category_compliance, list(category_compliance[0]))
    write_csv(OUTPUT_DIR / "03_category_driving.csv", category_driving, list(category_driving[0]))
    write_csv(OUTPUT_DIR / "04_category_combination.csv", combinations, list(combinations[0]))

    raw_driving = Counter(r["driving_mode_raw"] for r in event_rows)
    raw_event_status = Counter(r["compliance_label_raw"] for r in event_rows)
    raw_law_status = Counter(r["article_status_raw"] for r in law_rows)
    unknown_driving = sum(r["driving_code"] == "U" for r in event_rows)
    unknown_event_status = sum(r["compliance_code"] == "U" for r in event_rows)
    empty_article_status = sum(not r["article_status_ids"] for r in event_rows)
    combination_total_errors = []
    for category in CATEGORY_ORDER:
        event_total = sum(r["category"] == category for r in event_rows)
        state_total = sum(r["count"] for r in combinations if r["category"] == category)
        if event_total != state_total:
            combination_total_errors.append({"category": category, "events": event_total, "states": state_total})

    qa = {
        "discovered_event_directories": len(event_files),
        "loaded_json_records": len(event_rows),
        "article_event_pairs": len(law_rows),
        "event_directories_without_record_json": missing_json,
        "json_parse_or_read_errors": parse_errors,
        "raw_driving_mode_counts": dict(raw_driving),
        "raw_event_compliance_counts": dict(raw_event_status),
        "raw_article_status_counts": dict(raw_law_status),
        "unknown_driving_records": unknown_driving,
        "unknown_event_compliance_records": unknown_event_status,
        "empty_article_status_records": empty_article_status,
        "empty_reported_article_ids": empty_reported_article_ids,
        "article_ids_missing_status": article_ids_missing_status,
        "extra_article_status_ids": extra_article_status_ids,
        "combination_total_errors": combination_total_errors,
    }
    with (OUTPUT_DIR / "qa_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(qa, handle, ensure_ascii=False, indent=2)

    city_category_md = [
        [r["city"], r["category"], r["total_events"], r["compliant_events"], r["violating_events"]]
        for r in city_category
    ]
    city_article_md = [
        [r["city"], r["article_id"], r["total_events"], r["compliant_events"], r["violating_events"]]
        for r in city_article
    ]
    compliance_md = [
        [r["category"], r["total_events"], r["compliant_events"], r["violating_events"]]
        for r in category_compliance
    ]
    driving_md = [
        [r["category"], r["total_events"], r["autonomous_events"], r["manual_events"]]
        for r in category_driving
    ]
    combination_lookup = {(r["category"], r["combination"]): r for r in combinations}
    combination_md = []
    for category in CATEGORY_ORDER:
        row = [category]
        for state in COMBINATION_ORDER:
            item = combination_lookup[category, state]
            row.append(f'{item["count"]} ({item["share_pct_within_category"]:.2f}%)')
        combination_md.append(row)

    report = f"""# TLCD 1.0 事件统计

## 统计口径

- 统计对象：`Dataset` 中每个事件目录的 `record.json`；每个 JSON 计为 1 个事件记录。
- 类别：由事件所在的 8 个类别目录确定。
- 城市：由 `Changchun_valid` 和 `Nanjing_valid` 目录确定。
- 事件合规性：使用 `Result.Compliance_label`；合规记为 L，违规记为 I。
- 单条法规涉及范围：使用 `Article.ID` 的适用法规 ID；其合规/违规二分类使用该事件的最终 `Result.Compliance_label`。`Evidence.Article_status` 中的过程状态（如 `Compliance→Violation`）保留在明细中，不直接强行二分类。法规表以“事件—法规”对为单位；若一个事件对应多条法规，会在每条法规下各计 1 次。超车事件中仅作为候选、但未写入 `Article.ID` 的法规不会计入。
- 驾驶方式：`DrivingMode` 中只要包含 `Autonomous` 就记为 A；否则包含 `Manual` 或 `Human` 时记为 M。
- 组合占比：以每个类别的全部事件记录数为分母，CAL、CAI、CML、CMI、NAL、NAI、NML、NMI 在该类别内合计为 100%。

## 1a. 分城市、分类别的事件数与合规性

{markdown_table(["城市", "类别", "总数", "合规", "违规"], city_category_md)}

## 1b. 分城市、分法规的事件数与合规性

{markdown_table(["城市", "法规 ID", "总数", "合规", "违规"], city_article_md)}

## 2. 不分城市：分类别的合规与违规事件数

{markdown_table(["类别", "总数", "合规", "违规"], compliance_md)}

## 3. 不分城市：分类别的自动驾驶与人工驾驶事件数

{markdown_table(["类别", "总数", "自动驾驶", "人工驾驶"], driving_md)}

## 4. 分类别的城市 × 驾驶方式 × 合规性组合

单元格为“数量（占该类别全部事件的比例）”。

{markdown_table(["类别"] + COMBINATION_ORDER, combination_md)}

## 质量核验

- 发现事件目录：{len(event_files)}
- 成功读取 JSON：{len(event_rows)}
- 事件—法规对：{len(law_rows)}
- 无 `record.json` 的事件目录：{len(missing_json)}（不纳入统计）
- JSON 解析或读取失败：{len(parse_errors)}
- 未识别驾驶方式：{unknown_driving}
- 未识别事件合规标签：{unknown_event_status}
- 缺少法规状态：{empty_article_status}
- 缺少 `Article.ID`：{len(empty_reported_article_ids)}
- `Article.ID` 在 `Evidence.Article_status` 中无对应状态：{len(article_ids_missing_status)}
- `Evidence.Article_status` 中存在未适用的候选法规：{len(extra_article_status_ids)} 个事件（不计入法规统计）
- 8 组合合计不等于类别总数的类别：{len(combination_total_errors)}
"""
    (OUTPUT_DIR / "dataset_event_statistics.md").write_text(report, encoding="utf-8")

    hard_failures = (
        len(parse_errors)
        + unknown_driving
        + unknown_event_status
        + empty_article_status
        + len(empty_reported_article_ids)
        + len(article_ids_missing_status)
        + len(combination_total_errors)
    )
    print(f"Loaded {len(event_rows)} JSON records and {len(law_rows)} event-article pairs.")
    print(f"DrivingMode values: {dict(raw_driving)}")
    print(f"Event compliance values: {dict(raw_event_status)}")
    print(f"Article status values: {dict(raw_law_status)}")
    print(f"Article.ID values missing Article_status entries: {len(article_ids_missing_status)}")
    print(f"Events with extra candidate Article_status entries: {len(extra_article_status_ids)}")
    print(f"Hard QA failures: {hard_failures}")
    if hard_failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
