# TLCD 1.0 事件统计

## 统计口径

- 统计对象：`Dataset` 中每个事件目录的 `record.json`；每个 JSON 计为 1 个事件记录。
- 类别：由事件所在的 8 个类别目录确定。
- 城市：由 `Changchun_valid` 和 `Nanjing_valid` 目录确定。
- 事件合规性：使用 `Result.Compliance_label`；合规记为 L，违规记为 I。
- 单条法规涉及范围：使用 `Article.ID` 的适用法规 ID；其合规/违规二分类使用该事件的最终 `Result.Compliance_label`。`Evidence.Article_status` 中的过程状态（如 `Compliance→Violation`）保留在明细中，不直接强行二分类。法规表以“事件—法规”对为单位；若一个事件对应多条法规，会在每条法规下各计 1 次。超车事件中仅作为候选、但未写入 `Article.ID` 的法规不会计入。
- 驾驶方式：`DrivingMode` 中只要包含 `Autonomous` 就记为 A；否则包含 `Manual` 或 `Human` 时记为 M。
- 组合占比：以每个类别的全部事件记录数为分母，CAL、CAI、CML、CMI、NAL、NAI、NML、NMI 在该类别内合计为 100%。

## 1a. 分城市、分类别的事件数与合规性

| 城市 | 类别 | 总数 | 合规 | 违规 |
| --- | --- | --- | --- | --- |
| Changchun | MaxSpdlim | 309 | 143 | 166 |
| Changchun | MinSpdlim | 219 | 161 | 58 |
| Changchun | FollowDis | 186 | 23 | 163 |
| Changchun | LateralDis | 262 | 258 | 4 |
| Changchun | LaneChange | 219 | 201 | 18 |
| Changchun | ContinueLaneChange | 20 | 17 | 3 |
| Changchun | RoadMarking | 179 | 140 | 39 |
| Changchun | Overtake | 13 | 12 | 1 |
| Nanjing | MaxSpdlim | 1005 | 419 | 586 |
| Nanjing | MinSpdlim | 189 | 134 | 55 |
| Nanjing | FollowDis | 395 | 46 | 349 |
| Nanjing | LateralDis | 1001 | 953 | 48 |
| Nanjing | LaneChange | 587 | 502 | 85 |
| Nanjing | ContinueLaneChange | 77 | 57 | 20 |
| Nanjing | RoadMarking | 473 | 367 | 106 |
| Nanjing | Overtake | 39 | 22 | 17 |

## 1b. 分城市、分法规的事件数与合规性

| 城市 | 法规 ID | 总数 | 合规 | 违规 |
| --- | --- | --- | --- | --- |
| Changchun | IMR_44.1 | 219 | 201 | 18 |
| Changchun | IMR_45.1 | 93 | 9 | 84 |
| Changchun | IMR_47.4 | 13 | 12 | 1 |
| Changchun | IMR_78.1 | 70 | 59 | 11 |
| Changchun | IMR_78.2 | 74 | 60 | 14 |
| Changchun | IMR_78.3 | 174 | 78 | 96 |
| Changchun | IMR_78.4 | 20 | 20 | 0 |
| Changchun | IMR_78.5 | 70 | 63 | 7 |
| Changchun | IMR_78.6 | 35 | 3 | 32 |
| Changchun | IMR_78.7 | 30 | 19 | 11 |
| Changchun | IMR_80.1 | 72 | 8 | 64 |
| Changchun | IMR_80.2 | 147 | 16 | 131 |
| Changchun | OSP_8.2.1 | 262 | 258 | 4 |
| Changchun | OSP_9.3.1 | 20 | 17 | 3 |
| Changchun | TSM_4.3.1 | 159 | 140 | 19 |
| Changchun | TSM_4.5.2 | 33 | 0 | 33 |
| Nanjing | IMR_44.1 | 587 | 502 | 85 |
| Nanjing | IMR_45.1 | 358 | 63 | 295 |
| Nanjing | IMR_47.4 | 37 | 22 | 15 |
| Nanjing | IMR_78.1 | 74 | 65 | 9 |
| Nanjing | IMR_78.2 | 32 | 25 | 7 |
| Nanjing | IMR_78.3 | 666 | 327 | 339 |
| Nanjing | IMR_78.4 | 54 | 48 | 6 |
| Nanjing | IMR_78.6 | 20 | 1 | 19 |
| Nanjing | IMR_78.7 | 94 | 61 | 33 |
| Nanjing | IMR_80.1 | 74 | 10 | 64 |
| Nanjing | IMR_80.2 | 379 | 39 | 340 |
| Nanjing | IMR_82.5 | 7 | 0 | 7 |
| Nanjing | OSP_8.2.1 | 1001 | 953 | 48 |
| Nanjing | OSP_9.3.1 | 77 | 57 | 20 |
| Nanjing | TSM_4.3.1 | 437 | 367 | 70 |
| Nanjing | TSM_4.5.2 | 82 | 0 | 82 |

## 2. 不分城市：分类别的合规与违规事件数

| 类别 | 总数 | 合规 | 违规 |
| --- | --- | --- | --- |
| MaxSpdlim | 1314 | 562 | 752 |
| MinSpdlim | 408 | 295 | 113 |
| FollowDis | 581 | 69 | 512 |
| LateralDis | 1263 | 1211 | 52 |
| LaneChange | 806 | 703 | 103 |
| ContinueLaneChange | 97 | 74 | 23 |
| RoadMarking | 652 | 507 | 145 |
| Overtake | 52 | 34 | 18 |

## 3. 不分城市：分类别的自动驾驶与人工驾驶事件数

| 类别 | 总数 | 自动驾驶 | 人工驾驶 |
| --- | --- | --- | --- |
| MaxSpdlim | 1314 | 283 | 1031 |
| MinSpdlim | 408 | 326 | 82 |
| FollowDis | 581 | 478 | 103 |
| LateralDis | 1263 | 903 | 360 |
| LaneChange | 806 | 545 | 261 |
| ContinueLaneChange | 97 | 43 | 54 |
| RoadMarking | 652 | 401 | 251 |
| Overtake | 52 | 19 | 33 |

## 4. 分类别的城市 × 驾驶方式 × 合规性组合

单元格为“数量（占该类别全部事件的比例）”。

| 类别 | CAL | CAI | CML | CMI | NAL | NAI | NML | NMI |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MaxSpdlim | 132 (10.05%) | 151 (11.49%) | 11 (0.84%) | 15 (1.14%) | 0 (0.00%) | 0 (0.00%) | 419 (31.89%) | 586 (44.60%) |
| MinSpdlim | 158 (38.73%) | 48 (11.76%) | 3 (0.74%) | 10 (2.45%) | 82 (20.10%) | 38 (9.31%) | 52 (12.75%) | 17 (4.17%) |
| FollowDis | 19 (3.27%) | 147 (25.30%) | 4 (0.69%) | 16 (2.75%) | 41 (7.06%) | 271 (46.64%) | 5 (0.86%) | 78 (13.43%) |
| LateralDis | 227 (17.97%) | 2 (0.16%) | 31 (2.45%) | 2 (0.16%) | 661 (52.34%) | 13 (1.03%) | 292 (23.12%) | 35 (2.77%) |
| LaneChange | 188 (23.33%) | 14 (1.74%) | 13 (1.61%) | 4 (0.50%) | 297 (36.85%) | 46 (5.71%) | 205 (25.43%) | 39 (4.84%) |
| ContinueLaneChange | 17 (17.53%) | 0 (0.00%) | 0 (0.00%) | 3 (3.09%) | 26 (26.80%) | 0 (0.00%) | 31 (31.96%) | 20 (20.62%) |
| RoadMarking | 125 (19.17%) | 23 (3.53%) | 15 (2.30%) | 16 (2.45%) | 226 (34.66%) | 27 (4.14%) | 141 (21.63%) | 79 (12.12%) |
| Overtake | 12 (23.08%) | 1 (1.92%) | 0 (0.00%) | 0 (0.00%) | 4 (7.69%) | 2 (3.85%) | 18 (34.62%) | 15 (28.85%) |

## 质量核验

- 发现事件目录：5174
- 成功读取 JSON：5173
- 事件—法规对：5470
- 无 `record.json` 的事件目录：1（不纳入统计）
- JSON 解析或读取失败：0
- 未识别驾驶方式：0
- 未识别事件合规标签：0
- 缺少法规状态：0
- 缺少 `Article.ID`：0
- `Article.ID` 在 `Evidence.Article_status` 中无对应状态：0
- `Evidence.Article_status` 中存在未适用的候选法规：40 个事件（不计入法规统计）
- 8 组合合计不等于类别总数的类别：0
