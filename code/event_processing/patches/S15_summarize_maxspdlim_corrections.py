from __future__ import annotations

from pathlib import Path

import pandas as pd


ROOT = Path(r"Z:\HongqiData\Nanjing\20240910\zEvent_MaxSpdlim")
TARGET_COLUMNS = [
    "EgoLaneIndex",
    "LaneNumSameDirection",
    "LaneMaxSpdlim_1",
    "LaneMaxSpdlim_2",
    "LaneMaxSpdlim_3",
    "LaneMaxSpdlim_4",
    "LaneMaxSpdlim_5",
]


def value_counts_text(series: pd.Series) -> str:
    counts = series.value_counts().sort_index()
    return ";".join(f"{int(k)}:{int(v)}" for k, v in counts.items())


def change_points_text(df: pd.DataFrame, column: str, limit: int = 8) -> str:
    changes = df.index[df[column].ne(df[column].shift())].tolist()
    parts = []
    for idx in changes[:limit]:
        parts.append(f"{float(df.loc[idx, 'event_time']):.2f}->{int(df.loc[idx, column])}")
    if len(changes) > limit:
        parts.append(f"...(+{len(changes) - limit})")
    return ";".join(parts)


def check_output(df: pd.DataFrame) -> list[str]:
    issues = []
    if (df["EgoLaneIndex"] < 1).any() or (df["EgoLaneIndex"] > 5).any():
        issues.append("EgoLaneIndex越界")
    if (df["LaneNumSameDirection"] < 1).any() or (df["LaneNumSameDirection"] > 5).any():
        issues.append("LaneNumSameDirection越界")
    speed_cols = [f"LaneMaxSpdlim_{i}" for i in range(1, 6)]
    for _, row in df.iterrows():
        n = int(row["LaneNumSameDirection"])
        if any(row[f"LaneMaxSpdlim_{i}"] != 0 for i in range(n + 1, 6)):
            issues.append("超出车道数的限速列非0")
            break
        if sum(row[col] > 0 for col in speed_cols) > n:
            issues.append("正限速车道数超过LaneNumSameDirection")
            break
    return issues


def main() -> None:
    rows = []
    issue_rows = []
    diff_rows_total = 0
    same_event_count = 0
    event_count = 0

    for scheme1_path in sorted(ROOT.glob("*_CSV/MaxSpdlim_event_*_MapInfo_correct_scheme1.csv")):
        scheme2_path = scheme1_path.with_name(scheme1_path.name.replace("_scheme1.csv", "_scheme2.csv"))
        if not scheme2_path.exists():
            issue_rows.append((scheme1_path.parent.name, scheme1_path.name, "缺少scheme2"))
            continue
        event_count += 1
        segment = scheme1_path.parent.name
        event_num = int(scheme1_path.name.split("_")[2])
        df1 = pd.read_csv(scheme1_path)
        df2 = pd.read_csv(scheme2_path)
        diff_rows = int((df1[TARGET_COLUMNS] != df2[TARGET_COLUMNS]).any(axis=1).sum())
        diff_rows_total += diff_rows
        if diff_rows == 0:
            same_event_count += 1
        for scheme, df in ((1, df1), (2, df2)):
            issues = check_output(df)
            if issues:
                issue_rows.append((segment, f"event_{event_num}_scheme{scheme}", "|".join(issues)))
        rows.append(
            {
                "segment": segment,
                "event_num": event_num,
                "rows": len(df1),
                "scheme_diff_rows": diff_rows,
                "scheme1_ego_counts": value_counts_text(df1["EgoLaneIndex"]),
                "scheme1_lane_num_counts": value_counts_text(df1["LaneNumSameDirection"]),
                "scheme1_ego_changes": change_points_text(df1, "EgoLaneIndex"),
                "scheme1_lane_num_changes": change_points_text(df1, "LaneNumSameDirection"),
                "scheme2_ego_counts": value_counts_text(df2["EgoLaneIndex"]),
                "scheme2_lane_num_counts": value_counts_text(df2["LaneNumSameDirection"]),
                "scheme2_ego_changes": change_points_text(df2, "EgoLaneIndex"),
                "scheme2_lane_num_changes": change_points_text(df2, "LaneNumSameDirection"),
            }
        )

    summary = pd.DataFrame(rows)
    report_path = Path("01Event_Extraction") / "S15_maxspdlim_correction_summary.csv"
    summary.to_csv(report_path, index=False, encoding="utf-8-sig")

    print(f"events={event_count}")
    print(f"same_events={same_event_count}")
    print(f"diff_events={event_count - same_event_count}")
    print(f"diff_rows_total={diff_rows_total}")
    print(f"issues={len(issue_rows)}")
    print(f"report={report_path}")
    if issue_rows:
        for row in issue_rows[:20]:
            print("ISSUE", row)
    diff_summary = summary[summary["scheme_diff_rows"] > 0]
    if not diff_summary.empty:
        print("DIFF_EVENTS")
        for _, row in diff_summary.head(20).iterrows():
            print(
                row["segment"],
                int(row["event_num"]),
                int(row["scheme_diff_rows"]),
                row["scheme1_ego_counts"],
                row["scheme2_ego_counts"],
            )


if __name__ == "__main__":
    main()
