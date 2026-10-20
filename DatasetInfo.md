# TLCD数据集介绍

## 1. 总体介绍

本数据集来源于车端采集的**连续行驶原始数据**（多传感器/车身/地图等时序信号）。在连续数据上运行**交通法规合规性监测算法**，以规则触发与判定结果为核心，将长序列切分、整理为可审计的**事件级合规性样本（event-level compliance events）**。每个事件样本对应一段明确的时间窗口，包含：
- **上游结构化输入**：EgoInfo / ObjInfo / MapInfo（车端原始输入信号在事件窗口内的裁剪与结构化表示）
- **下游证据链输出**：EvidenceChain.csv（用于支撑判定结论的关键证据字段）
- **事件摘要记录**：record.json（面向人工复核/审计的时间、法规条款、结论、原因与建议等摘要信息）
- **七路相机视频**：`video` 文件夹（面向事件回放与人工复核的多视角视频数据，内容与同一事件的时间窗口对齐，包含前视 30°、前视 120°、后视、左前、左后、右前、右后七路视频，覆盖六个观察方向）

---

## 2. 文件与目录组织规范

### 2.1 事件文件集合
每个事件实例输出一组文件（同一目录内）：

- `*_EgoInfo.csv`：自车信息（上游输入）
- `*_ObjInfo.csv`：障碍物信息（上游输入）
- `*_MapInfo.csv`：地图与车道线信息（上游输入）
- `*_EvidenceChain.csv`：证据链关键字段（下游输出）
- `*_record.json`：事件可读化摘要（下游输出）
- `video/video_*.mp4`：事件记录视频文件夹（包含七路视频，覆盖六个观察方向）

数据集根目录另提供 `camera_parameters.json`，保存七套相机参数的完整数值。

### 2.2 文件命名规范
每个事件实例的组合文件采用统一命名模板（对应 2.1 节中的 `*` 部分）：
> `<event_type>_event_<n>_<artifact>.<ext>`

其中：
- `<event_type>`：事件类型标识，用于区分不同法规/场景事件，例如 `lane_change` / `FollowDis` / `Overtake` 。
- `<n>`：事件在当前 `segment` 内的顺序编号（从 1 开始），同一 `segment` 内唯一。
- `<artifact>`：文件内容类型标识，包括 `EgoInfo` / `ObjInfo` / `MapInfo` / `EvidenceChain` / `record`。
- `<ext>`：文件扩展名，`csv` 或 `json`。

`segment` 定义：车端采集的连续行驶原始数据在落盘时以 5 min 为一个 `segment` 进行保存与处理；事件以 `segment` 分文件夹保存，与 `segment` 对应。

所有 CSV 统一包含 `event_time` 列，表示事件区间内的相对时间轴，且所有CSV统一插值处理为100 Hz 采样率。

---

# Part A：上游结构化输入（车端原始输入）

## A1. EgoInfo（自车信息）

**文件：** `*_EgoInfo.csv`  
**性质：** 上游输入（车端原始信号结构化）  
**内容：** 事件区间内的自车状态序列（定位、航向、车速等），按事件区间裁剪。

### A1.1 字段说明表

| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| Ego_GNSS_Longitude | 自车经度 | deg |  | 0 |
| Ego_GNSS_Latitude | 自车纬度 | deg |  | 0 |
| Ego_GNSS_Azimuth | 自车航向角 | deg |  | 0 |
| Ego_velocity | 自车车速 | m/s |  | 保持上一帧 |

---

## A2. ObjInfo（障碍物信息）

**文件：** `*_ObjInfo.csv`  
**性质：** 上游输入（车端融合感知障碍物信息结构化）  
**内容：** 事件区间内障碍物列表（最多 30 个）的属性序列。

### A2.1 表结构约定
- 每行：事件区间内一帧
- 每列：某个 `Obj<xx>` 的某属性（例如 `Obj05_Speed`）
- 目标数量：固定上限 30（Obj01 … Obj30）
- 若目标不存在：按缺失规则填充

### A2.2 字段说明表
| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| Obj<xx>_Class | 障碍物类型 | enum | {0:invalid, 1:car, 2:truck, 3:bike, 4:bicycle, 5:pedestrain, 6:animal, 7:uncertain vehicle, 8:general object} | 0 |
| Obj<xx>_Flagstate | 障碍物相对自车行驶方向 | enum | {0:same direction, 1:opposite direction, 2:traverse, 3:other undefined} | 3 |
| Obj<xx>_Speed | 障碍物标量速度 | m/s |  | 0 |
| Obj<xx>_RelativeLane | 障碍物相对自车所在车道 | enum | {0:unknown, 1:current_lane, 2:adjacent_lane_left, 3:adjacent_lane_right, 4:left_left_lane, 5:right_right_lane} | 0 |
| Obj<xx>_Confidence | 障碍物置信度 | / | [0, 100] | 0 |
| Obj<xx>_DistanceX | 自车坐标系下障碍物纵坐标 | m |  | 0 |
| Obj<xx>_DistanceY | 自车坐标系下障碍物横坐标 | m |  | 0 |
| Obj<xx>_RelativeVx | 障碍物纵向相对速度 | m/s |  | 0 |
| Obj<xx>_RelativeVy | 障碍物横向相对速度 | m/s |  | 0 |
| Obj<xx>_Length | 障碍物长度 | m |  | 0 |
| Obj<xx>_Width | 障碍物宽度 | m |  | 0 |
| Obj<xx>_TrackStatus | 障碍物轨迹状态 | enum | {0:invalid, 1:new, 2:coasted, 3:update} | 0 |

### A2.3 补充信息
- 槽位编号：同一目标在 `Obj<xx>_TrackStatus` 有效过程中，保持同一槽位。
- 坐标系约定：自车坐标系采用右手坐标系，坐标系原点位于自车后轴中心，X轴为纵向，以原点指向车头作为X轴正方向；Y轴为横向，以原点指向车辆左侧为正方向。


---

## A3. MapInfo（地图与车道线信息）

**文件：** `*_MapInfo.csv`  
**性质：** 上游输入（地图定位及车道线相关信息结构化）  
**内容：** 事件区间内道路属性（道路类型、曲率、坡度、限速、车道类型等）与车道线参数（Quality、车道线拟合参数C0~C3、Type等）。


### A3.1 字段说明表
| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| Road_type | 自车所在道路类型 | enum | {0:unknown, 1:multiple_carriageway, 2:single_carriageway, 4:service_road, 6:ramp_entry, 7:ramp_exit, 9:jct, 18:service_area_approach, 19:service_area_jct, 20:service_area_approach_jct, 27:toll_booth, 31:motorway_entry_ramp, 32:motorway_exit_ramp, 34:tunnel, 36:inner_link_of_an_intersection, 37:toll_area, 38:rest_area, 39:auxiliary_lane} | 0 |
| Road_Curve | 自车所在道路曲率 | 1/m |  | 0 |
| Road_Slope | 自车所在道路坡度角 | rad |  | 0 |
| Lane_type_<*Lane> | 车道类型 | enum | {0:unknown, 1:regular_lane, 2:deceleration_lane, 3:acceleration_lane, 4:compound_lane, 5:drivable_parking_lane, 8:slow_lane, 9:drivable_shoulder_lane, 10:shoulder_lane, 12:regulated_access_lane, 13:variable_driving_lane, 14:emergency_strip, 15:other_lane} | 0 |
| LaneMaxSpdlim_<n> | 第 n 条主车道最高限速 | km/h | n=1..5，从左至右编号 | 0 |
| LaneMinSpdlim_<n> | 第 n 条主车道最低限速 | km/h | n=1..5，从左至右编号 | 0 |
| LaneNumSameDirection | 同方向主车道数 | / |  | 0 |
| EgoLaneIndex | 自车所在主车道索引 | / | 1,2,3,4,5，从左至右编号 | 0 |
| MAP_Q_<*line> | 车道线质量 | / | [0, 1] | 0 |
| MAP_C0_<*line> | 车道线拟合参数C0 | m |  | 0 |
| MAP_C1_<*line> | 车道线拟合参数C1 | / |  | 0 |
| MAP_C2_<*line> | 车道线拟合参数C2 | 1/m |  | 0 |
| MAP_C3_<*line> | 车道线拟合参数C3 | 1/m² |  | 0 |
| MAP_Type_<*line> | 车道线类型 | enum | {0:unknown, 1:solid_line, 2:dashed_line, 3:double_solid_line, 4:double_dashed_line, 5:left_solid_right_dashed, 6:right_solid_left_dashed, 7:DiversionLine, 10:others} | 0 |

### A3.2 补充信息
- `Lane_type_<*Lane>` 包含的车道 `<*Lane>` 共三个，分别是自车所在车道 `CurrentLane`、自车左侧车道 `LeftLane` 以及自车右侧车道 `RightLane`；
- `LaneMaxSpdlim_<n>`、`LaneMinSpdlim_<n>`、`LaneNumSameDirection` 和 `EgoLaneIndex` 字段存在于全部事件类型的 `MapInfo.csv` 中；
- `MAP_xx_<*line>` 包含的车道线 `<*line>`共四条，分别是自车所在车道左侧车道线 `Left1`、自车左侧车道的左侧车道线 `Left2`、自车所在车道右侧车道线 `Right1` 以及自车右侧车道的右侧车道线 `Right2`；
- 车道线拟合根据车道线形点拟合三次多项式：`y=C3*x^3 + C2*x^2 + C1*x +C0`。

---

# Part B：证据链输出与事件记录

## B1. EvidenceChain（证据链）

**文件：**`*_EvidenceChain.csv`  
**性质：** 下游输出（监测结果与关键证据抽取）  
**内容：** 用于支撑合规判定的关键证据字段，便于复核与审计。证据链字段通常来自：
- 上游输入（EgoInfo / ObjInfo / MapInfo）
- 监测算法内部关键变量（例如 TTC、距离阈值）
- 监测算法内部关键命题（计算命题 / 触发情况 / 违规判定）


### B1.1 字段说明
证据链涉及的字段与监测的事件类型相关，不同事件类型对应的法规条款、触发逻辑与判定依据不同，但各类事件的证据链结构是类似的，均包含以下三部分：
- `event_time`，事件相对时间轴;
- 相关条例的触发及判断结果：`trigger_*`, `com_*`;
- 用于相关条例违规判断的关键命题或关键变量，以及对应的合规性阈值；

### B1.2 最高限速类法规

#### B1.2.1 相关条例
本数据集中涉及的最高限速法规共计 `6` 条，各条例的条例编号及对应法规文本如下：

| 条例编号 | 法规文本 |
|---|---|
| IMR_45.1 | 机动车在道路上行驶不得超过限速标志、标线标明的速度 |
| IMR_46.3 | 机动车通过急弯路时，最高行驶速度不得超过30km/h |
| IMR_46.4 | 机动车通过窄路、窄桥时，最高行驶速度不得超过30km/h |
| IMR_46.5 | 机动车下陡坡时，最高行驶速度不得超过30km/h |
| IMR_78.1 | 在高速公路上行驶的小型载客汽车最高车速不得超过每小时120公里，其他机动车不得超过每小时100公里 |
| IMR_78.3 | 道路限速标志标明的最高车速与上述车道行驶车速的规定不一致的，按照道路限速标志标明的最高车速行驶 |

#### B1.2.2 字段说明表

| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| trigger_IMR_45_1 | IMR_45.1是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_46_3 | IMR_46.3是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_46_4 | IMR_46.4是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_46_5 | IMR_46.5是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_78_1 | IMR_78.1是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_78_3 | IMR_78.3是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| com_IMR_45_1 | IMR_45.1是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_46_3 | IMR_46.3是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_46_4 | IMR_46.4是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_46_5 | IMR_46.5是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_78_1 | IMR_78.1是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_78_3 | IMR_78.3是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| Road_type | 自车所在道路类型 | enum | 见A3.1 | 0 |
| Lane_type | 自车所在车道类型 | enum | 见A3.1 | 0 |
| Ego_velocity | 自车车速 | m/s |  | -1 |
| IsMaxSpdsignArea | 自车位于最高限速标志管理区域 | bool |  | 0 |
| Thres_MaxSpdlim | 最高限速阈值 | km/h |  | -1 |
| LaneNumSameDirection | 同方向主车道数 | / |  | 0 |
| EgoLaneIndex | 自车所在主车道索引，从左至右编号 | / | 1,2,3,4,5 | 0 |
| LaneMaxSpdlim_<n> | 第 n 条主车道最高限速 | km/h | n=1..5，从左至右编号 | 0 |

### B1.3 最低限速类法规

#### B1.3.1 相关条例
本数据集中涉及的最低限速法规共计 `5` 条，各条例的条例编号及对应法规文本如下：

| 条例编号 | 法规文本 |
|---|---|
| IMR_78.2 | 在高速公路上行驶的车辆最低车速不得低于每小时60公里 |
| IMR_78.4 | 道路限速标志标明的最低车速与上述车道行驶车速的规定不一致的，按照道路限速标志标明的最低车速行驶 |
| IMR_78.5 | 同方向有2条车道的，左侧车道的最低车速为每小时100公里 |
| IMR_78.6 | 同方向有3条以上车道的，最左侧车道的最低车速为每小时110公里 |
| IMR_78.7 | 同方向有3条以上车道的，中间车道的最低车速为每小时90公里 |

#### B1.3.2 字段说明表

| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| trigger_IMR_78.2 | IMR_78.2是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_78.4 | IMR_78.4是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_78.5 | IMR_78.5是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_78.6 | IMR_78.6是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_78.7 | IMR_78.7是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| com_IMR_78.2 | IMR_78.2是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_78.4 | IMR_78.4是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_78.5 | IMR_78.5是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_78.6 | IMR_78.6是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_78.7 | IMR_78.7是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| Road_type | 自车所在道路类型 | enum | 见A3.1 | 0 |
| Lane_type | 自车所在车道类型 | enum | 见A3.1 | 0 |
| Ego_velocity | 自车车速 | m/s |  | -1 |
| IsMinSpdsignArea | 自车位于最低限速标志管理区域 | bool |  | 0 |
| LaneNumSameDirection | 同方向主车道数 | / |  | 0 |
| LaneMinSpdlim_<n> | 第 n 条主车道最低限速 | km/h | n=1..5，从左至右编号 | 0 |
| mainLaneNum | 有效最低限速主车道数（即 `LaneMinSpdlim_1`～`LaneMinSpdlim_5` 中取值大于 0 的字段数） | / | {2,3,4,5} | 0 |
| EgoLaneIndex | 自车所在主车道索引，从左至右编号 | / | 1,2,3,4,5 | 0 |
| Special_case | 最低限速规则的特殊情况 | enum | {0:none, 1:congestion, 2:construction, 3:uphill_slow_driving, 4:downhill_slow_driving, 5:curve} | 0 |
| Thres_MinSpdlim | 最低限速阈值 | km/h |  | -1 |


### B1.4 跟车距离类法规

#### B1.4.1 相关条例
本数据集中涉及的跟车距离类法规共计 `2` 条，各条例的条例编号及对应法规文本如下：

| 条例编号 | 法规文本 |
|---|---|
| IMR_80.1 | 机动车在高速公路上行驶，车速超过每小时100公里时，应当与同车道前车保持100米以上的距离 |
| IMR_80.2 | 机动车在高速公路上行驶，车速不超过每小时100公里时，应当与同车道前车保持50米以上的距离 |

#### B1.4.2 字段说明表

| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| trigger_IMR_80_1 | IMR_80.1是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_IMR_80_2 | IMR_80.2是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| com_IMR_80_1 | IMR_80.1是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_80_2 | IMR_80.2是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| Ego_velocity | 自车车速 | m/s |  | -1 |
| Road_type | 自车所在道路类型 | enum | 见A3.1 | 0 |
| Congestion | 当前交通是否拥堵 | bool |  | 0 |
| Lane_type | 自车所在车道类型 | enum | 见A3.1 | 0 |
| Dis_FV | 自车与同车道前车的距离 | m |  | -1 |
| Thres_Dis_FV | 跟车距离阈值 | m |  | -1 |

### B1.5 横向距离类法规

#### B1.5.1 相关条例
本数据集中涉及的横向距离类法规共计 `1` 条，条例编号及对应法规文本如下：
| 条例编号 | 法规文本 |
|---|---|
| OSP_8.2.1 | 车辆行驶过程中，宜与左右两侧车辆保持大于1.5m横向安全距离 |

#### B1.5.2 字段说明表
| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| trigger_OSP_8_2_1 | OSP_8.2.1是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| com_OSP_8_2_1 | OSP_8.2.1是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| Ego_velocity | 自车车速 | m/s |  | -1 |
| Road_type | 自车所在道路类型 | enum | 见A3.1 | 0 |
| Lane_type | 自车所在车道类型 | enum | 见A3.1 | 0 |
| Dis_LV | 自车与左侧相邻车的距离 | m |  | -1 |
| Dis_RV | 自车与右侧相邻车的距离 | m |  | -1 |
| Exist_LeftLine | 存在左侧车道线 | bool |  | 0 |
| Exist_RightLine | 存在右侧车道线 | bool |  | 0 |
| Dis_centerline | 自车相对所在车道中心线的横向偏移；正值表示向右偏移，负值表示向左偏移，0 表示位于车道中心线（符号方向与 A2.3 自车坐标系 Y 轴相反） | m |  | 无 |
| Is_Lat_avoidance | 偏离车道中心线以避让横向他车 | bool |  | 0 |
| Thres_min_LatDis | 最小横向距离阈值 | m |  | -1 |
| Thres_Offset_centerline | 偏离中心线判定阈值 | m |  | -1 |


### B1.6 变道类法规
#### B1.6.1 相关条例
本数据集中涉及的变道类法规共计 `2` 条，条例编号及对应法规文本如下：
| 条例编号 | 法规文本 |
|---|---|
| IMR_44.1 | 变更车道的机动车不得影响相关车道内行驶的机动车的正常行驶 |
| OSP_9.3.1 | 变更车道时，不应连续变更两条或两条以上车道 |


#### B1.6.2 普通换道证据链字段说明表

普通换道事件证据链对应 `lane_change_event_<n>_EvidenceChain.csv`，用于支撑 `IMR_44.1` 的合规判定。

| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| trigger_IMR_44_1 | IMR_44.1是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| com_IMR_44_1 | IMR_44.1是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_44_1_RVTL | IMR_44.1是否与目标车道后车违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_44_1_FV | IMR_44.1是否与前车违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| Ego_velocity | 自车车速 | m/s |  | -1 |
| RVTL_Velocity | 目标车道后车车速 | m/s |  | -1 |
| overlap_LeftLine | 自车与左侧车道线相交 | bool |  | 0 |
| overlap_RightLine | 自车与右侧车道线相交 | bool |  | 0 |
| TTC_FV | 与前车的TTC | s |  | -1 |
| dis_RVTL | 与目标车道后车的距离 | m |  | -1 |
| Thres_TTC_FV | 换道开始时与前车的最小TTC | s |  | -1 |
| Thres_dis_RVTL | 换道过程中与目标车道后车的最小换道距离 | m |  | -1 |

#### B1.6.3 连续换道证据链字段说明表

连续换道事件证据链对应 `ContinueLC_event_<n>_EvidenceChain.csv`，用于支撑 `OSP_9.3.1` 的合规判定。

| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| trigger_IMR_44_1 | IMR_44.1换道触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_OSP_9_3_1 | OSP_9.3.1是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| com_OSP_9_3_1 | OSP_9.3.1是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| Ego_velocity | 自车车速 | m/s |  | -1 |
| overlap_LeftLine | 自车与左侧车道线相交 | bool |  | 0 |
| overlap_RightLine | 自车与右侧车道线相交 | bool |  | 0 |
| last_cross_time | 上一次跨越车道线的时刻 | s |  | -1 |
| last_cross_dir | 上一次跨越车道线的方向 | enum | {-1:right, 0:none, 1:left} | 0 |
| current_cross_dir | 当前跨越车道线的方向 | enum | {-1:right, 0:none, 1:left} | 0 |
| Thres_LaneChangeCross_gap | 连续换道判定的两次跨线最大时间间隔 | s |  | -1 |

#### B1.6.4 补充说明
- 普通换道事件与连续换道事件分别由 `lane_change_events.csv` 和 `ContinueLC_events.csv` 记录事件区间，并分别导出对应的证据链文件；
- 普通换道证据链仅输出 `IMR_44.1` 相关字段，连续换道证据链仅输出 `OSP_9.3.1` 的合规字段；连续换道表中的 `trigger_IMR_44_1` 用于表示底层换道触发状态；
- `Thres_TTC_FV` 在普通换道证据链中固定为 `1.8 s`；
- `Thres_dis_RVTL` 按 `-3.4 * (Ego_velocity - RVTL_Velocity) + 13.6` 计算；当目标车道后车索引无效时，`Thres_dis_RVTL` 置为 `-1`；
- `Thres_LaneChangeCross_gap` 在连续换道证据链中固定为 `5 s`。

### B1.7 道路标线类

#### B1.7.1 相关条例
本数据集中涉及的道路标线类法规共计 `3` 条，各条例的条例编号及对应法规文本如下：

| 条例编号 | 法规文本 |
|---|---|
| TSM_4_3_1 | 白色虚线在保证安全的情况下,允许车辆短时越线行驶 |
| TSM_4_5_2 | 白色实线用于指示禁止车辆跨越的车行道边缘 |
| TSM_4_5_3 | 导流线表示车辆需按规定的路线行驶,不得压线或越线行驶 |


#### B1.7.2 字段说明表
| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| trigger_TSM_4_3_1 | TSM_4_3_1是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_TSM_4_5_2 | TSM_4_5_2是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| trigger_TSM_4_5_3 | TSM_4_5_3是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| com_TSM_4_3_1 | TSM_4_3_1是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_TSM_4_5_2 | TSM_4_5_2是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_TSM_4_5_3 | TSM_4_5_3是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| overlap_LeftLine | 自车与左侧车道线相交 | bool |  | 0 |
| overlap_RightLine | 自车与右侧车道线相交 | bool |  | 0 |
| lanechange_stage | 换道阶段 | enum | {0:not-change, 1:left_lanechange_s1, 2:left_lanechange_s2, 3:right_lanechange_s1, 4:right_lanechange_s2, 5:unknown} | 0 |
| MAP_Type_Left1 | 自车左侧车道线类型 | enum | 见A3.1 | 0 |
| MAP_Type_Right1 | 自车右侧车道线类型 | enum | 见A3.1 | 0 |
| Time_ContinuousLineOverlap | 自车持续压线时间 | s |  | 0 |
| Thres_MaxContinuousLineOverlap | 最大允许压线时间阈值 | s |  | -1 |

### B1.8 超车类法规

#### B1.8.1 相关条例
本数据集中涉及的超车类法规共计 `4` 条，各条例如下：

| 条例编号 | 法规文本 |
|---|---|
| IMR_47.4 | 后车应从前车的左侧超越前车 |
| IMR_82.5 | 机动车在高速公路上行驶，不得在匝道、加速车道或者减速车道上超车 |
| TSL_43.6 | 行经隧道时，不得超车 |
| TSL_43.8 | 行经市区交通流量大的路段时，不得超车 |

#### B1.8.2 字段说明表
| 字段名 | 含义 | 单位 | 取值范围/枚举 | 缺失规则 |
|---|---|---|---|---|
| event_time | 事件相对时间轴 | s |  |  |
| trigger_overtake | 超车是否触发 | enum | {0:not-triggered, 1:trigger} | 0 |
| com_IMR_47_4 | IMR_47.4是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_IMR_82_5 | IMR_82.5是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_TSL_43_6 | TSL_43.6是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| com_TSL_43_8 | TSL_43.8是否违规 | enum | {0:not-triggered, 1:compliance, -1:violation} | 0 |
| Ego_velocity | 自车车速 | m/s |  | -1 |
| FV_velocity | 被超前车车速 | m/s |  | -1 |
| FV_DistanceX | 被超前车相对自车纵向距离 | m |  | -1 |
| FV_DistanceY | 被超前车相对自车横向距离 | m |  | -1 |
| FV_ObjID | 被超前车目标编号 | id | {-1:unavailable, 1-30:object id} | -1 |
| OvertakeDirection | 超车方向 | enum | {0:none, 1:left, 2:right} | 0 |
| Road_type | 自车所在道路类型 | enum | 见A3.1 | 0 |
| Lane_type | 自车所在车道类型 | enum | 见A3.1 | 0 |
| Congestion | 当前交通是否拥堵 | bool |  | 0 |
| overlap_LeftLine | 自车与左侧车道线相交 | bool |  | 0 |
| overlap_RightLine | 自车与右侧车道线相交 | bool |  | 0 |
| last_cross_time | 上一次跨越车道线的时刻 | s |  | -1 |
| last_cross_dir | 上一次跨越车道线的方向 | enum | {-1:right, 0:none, 1:left} | 0 |
| current_cross_dir | 当前跨越车道线的方向 | enum | {-1:right, 0:none, 1:left} | 0 |

#### B1.8.3 补充说明
- 超车事件证据链对应文件名形如 `Overtake_event_<n>_EvidenceChain.csv`，字段名与上表保持一致；
- `trigger_overtake` 仅在确认构成超车的帧取 `1`：第二次换道过程中，两次越线间隔小于 `20 s`，两次换道方向相反，且被超车辆已位于自车后方，或目标消失前已位于自车后方；
- 被超车辆在第一次换道开始时刻锁定：根据 S7 生成的 `MapInfo.csv` 中左右车道线三次拟合方程确定自车当前车道，再从 `ObjInfo.csv` 中选择同车道最近前车作为目标；后续逐帧根据目标的 `x/y/vx` 状态相似度重新匹配，不直接信任旧索引；
- `FV_DistanceX < 0` 表示被超车辆位于自车后方；当目标不可用或匹配丢失时，`FV_velocity`、`FV_DistanceX`、`FV_DistanceY` 置为 `-1`，`FV_ObjID` 置为 `-1`；
- `OvertakeDirection` 仅在 `trigger_overtake == 1` 的帧有效，左侧超车取 `1`，右侧超车取 `2`，未触发帧统一为 `0`；


---

## B2. record.json（事件可读化摘要）

**文件：** `*_record.json`（示例：`lane_change_event_1_record.json`）  
**性质：** 下游输出（事件级可读化摘要 / 审计记录）  
**内容：** 基于同一事件对应的 `EvidenceChain.csv` 生成的可读化摘要文件，面向人工复核、审计留痕与结果展示。该文件不承载原始时序数据，也不重复展开全部证据链字段，而是对单个事件的关键信息进行结构化摘要，包括事件时间、适用法规条款、事件锚点、关键事实、合规结论、违规原因及驾驶建议等；其中结论性字段应与 `EvidenceChain.csv` 保持一致。

### B2.1 统一JSON结构
各类事件的 `record.json` 统一采用如下顶层结构：

```json
{
  "Location": "China, Nanjing",
  "Date": "YYYY-MM-DD",
  "Time": "HH:MM:SS.xxx -- HH:MM:SS.xxx",
  "Timestamp": "xxxxxxxxxxxxx -- xxxxxxxxxxxxx",
  "DrivingMode": "Manual Driving",
  "camera_set_id": "camera_017bfd08f0c7",
  "Article": {
    "ID": "IMR_44.1",
    "Text": ["..."]
  },
  "EventAnchor": {
    "Anchor_type": "...",
    "anchor_time": "xx s"
  },
  "Evidence": {
    "Article_status": {
      "IMR_44.1": "Compliance/Violation/Unknown"
    },
    "...类别相关关键证据...": "..."
  },
  "Result": {
    "Compliance_label": "Compliance/Violation/Unknown",
    "Violation_reason": "...",
    "Driving_suggestion_VLM": "...",
    "Scenario_description_VLM": "..."
  }
}
```

### B2.2 字段说明
| 字段名 | 含义 | 说明 |
|---|---|---|
| Location | 事件采集地点 | 按采集城市填写为 `China, Nanjing` 或 `China, Changchun` |
| Date | 事件日期 | 由事件起始绝对时间解析得到 |
| Time | 事件起止时间 | 绝对时间范围，格式为 `HH:MM:SS.xxx -- HH:MM:SS.xxx` |
| Timestamp | 事件起止时间戳 | 13 位毫秒级 Unix 时间戳范围，格式为 `xxxxxxxxxxxxx -- xxxxxxxxxxxxx` |
| DrivingMode | 驾驶模式 | 取值为 `Manual Driving` 或 `Autonomous Driving` |
| camera_set_id | 相机参数套编号 | 引用根目录 `camera_parameters.json` 中同名编号的参数套 |
| Article.ID | 适用法规编号 | 多个法规以 ` & ` 连接 |
| Article.Text | 适用法规文本 | 字符串或字符串列表，内容与 `Article.ID` 对应 |
| EventAnchor.Anchor_type | 事件锚点类型 | 表示事件样本的抽取锚点或状态变化类型；具体取值随事件类别而异 |
| EventAnchor.anchor_time | 事件锚点时间 | 字符串或字符串列表，表示一个锚点时刻或一组锚点/区间边界时刻，单位为 `s`；具体语义由 `Anchor_type` 决定 |
| Evidence | 关键事实摘要 | 不要求各事件类型字段完全相同；用于记录与法规判断直接相关的可读化证据，如跨线时刻、目标车距离、道路类型、阈值等 |
| Evidence.Article_status | 条款级判定 | 使用英文记录各触发条款的 `Compliance`、`Violation` 或 `Unknown` 状态；发生状态变化时使用由 `→` 连接的英文状态序列，例如 `Compliance→Violation` |
| Result.Compliance_label | 合规结论 | 取值为 `Compliance`、`Violation` 或 `Unknown` |
| Result.Violation_reason | 违规原因 | 若合规则通常为 `None`；若证据不足则说明缺失原因 |
| Result.Driving_suggestion_VLM | 驾驶建议 | 基于视觉语言模型生成并经修订的英文辅助建议，针对当前事件记录区间，供人工复核和结果展示使用 |
| Result.Scenario_description_VLM | 场景描述 | 结合对应视频与结构化证据生成并经修订的英文场景描述，涵盖当前法规类别关注的道路、车道、车辆、距离、速度、标线或换道过程等事实 |

### B2.3 约定
- `record.json` 是 `EvidenceChain.csv` 的可读化摘要，法规判定以 `EvidenceChain.csv` 中的触发和合规字段为准；
- `record.json` 的键、枚举值及自然语言文本统一使用英文，不使用中文内容；
- `Scenario_description_VLM` 和 `Driving_suggestion_VLM` 为辅助文本注释，不直接决定 `Compliance_label`；最终发布记录不再包含旧字段 `Scenario_description` 和 `Driving_suggestion`；
- `EventAnchor` 在所有事件类型中均由 `Anchor_type` 和 `anchor_time` 构成；`anchor_time` 可为字符串或字符串列表，用于描述事件抽取或状态变化的核心时刻；
- `Evidence` 字段允许不同事件类型保留不同的关键事实摘要，但不应重复存放完整逐帧时序数据；
- `camera_set_id` 为字符串；一个事件使用一个参数套编号，通过视频标签选择该套内的具体相机。


# Part C：事件视频数据

## C1. video（事件视频文件夹）
 
**内容：** 存放与单个事件样本对应的七路相机视频片段，用于人工复核、事件回放、结果展示与审计留痕。该文件夹中的视频应与同一事件的 `EgoInfo / ObjInfo / MapInfo`、`EvidenceChain.csv` 以及 `record.json` 对应，并在时间窗口上保持一致。

### C1.1 目录结构
- 每个事件样本对应一个独立的 `video` 文件夹。
- 文件夹内通常包含以下七路视频文件（覆盖六个观察方向）：`video_30_event_<n>`, `video_120_event_<n>`, `video_rear_event_<n>`, `video_Side_B_event_<n>`, `video_Side_F_event_<n>`, `video_Side_L_event_<n>`, `video_Side_R_event_<n>`.


### C1.2 相机方向、视频分辨率与处理状态

前方包含 30° 窄角与 120° 广角两路相机。下表分辨率均为提供的视频分辨率，按宽×高表示，单位为像素。

| 视频标签 | 相机方向 | 视频分辨率 | 视频处理状态 |
|---|---|---|---|
| `30` | 前视 30° 窄角 | 1408×792 | 已矫正 |
| `120` | 前视 120° 广角 | 1408×792 | 仍保留畸变 |
| `rear` | 后视 | 1920×1200 | 仍保留畸变 |
| `Side_F` | 左前视 | 1920×1200 | 仍保留畸变 |
| `Side_L` | 左后视 | 1920×1200 | 仍保留畸变 |
| `Side_R` | 右前视 | 1920×1200 | 仍保留畸变 |
| `Side_B` | 右后视 | 1920×1200 | 仍保留畸变 |

视频处理状态采用本数据集的统一使用约定：前视 30° 按已矫正图像处理，其余六路仍保留畸变。

### C1.3 时间对齐约定
- 视频片段与同一事件的 `record.json` 时间窗口关联；`event_time` 和 `record.json` 中的事件时间范围是结构化信号的权威时间基准。
- 视频采用 30 Hz 固定帧率，结构化 CSV 采用 100 Hz 采样率；视频帧与结构化采样点并非一一对应，应按事件相对时间关联。
- 视频播放轴与事件窗口的对应关系表示时间窗口及播放时长上的对齐，不等同于原始相机采集时刻与结构化信号逐帧精确同步；若存在帧级误差、丢帧或单视角缺失，应在生成流程中显式记录。

### C1.4 使用说明
- `*_video` 主要用于人工复核与可视化展示，不作为法规判定的唯一依据。
- 事件最终的结构化判定结果以 `EvidenceChain.csv` 为准，`record.json` 为其可读化摘要，视频用于辅助解释事件过程与结论。
- 若视频中叠加了检测框、车道线、文本说明等可视化信息，应视为展示层信息，不替代原始结构化字段。


## C2. 相机参数

**文件：** 数据集根目录 [camera_parameters.json](camera_parameters.json)。文件包含以下七套参数，每套均提供七路相机的完整数值。

### C2.1 参数套

| 参数套 | camera_set_id |
|---|---|
| 22 号车 · 第一套 | `camera_017bfd08f0c7` |
| 22 号车 · 第二套 | `camera_8613fcc9f76c` |
| 27 号车 | `camera_921a87bc52d9` |
| 28 号车 | `camera_98a4ec8935ef` |
| 31 号车 | `camera_d207758e4ce3` |
| PP16 | `camera_c5816f8fa3c8` |
| OTT73 | `camera_2f758d838450` |

### C2.2 参数字段

参数文件的 `camera_sets` 中，每套的 `cameras` 包含七路相机；每路参数由 `video_tag` 标识。内参均对应 `image_size` 指定的最终视频分辨率。

| 字段 | 含义与单位 |
|---|---|
| `video_tag` | 视频标签：`30`、`120`、`rear`、`Side_F`、`Side_L`、`Side_R`、`Side_B` |
| `image_size` | 视频尺寸 `[width, height]`，单位 px |
| `camera_intrinsic_video` | 视频内参矩阵 K，大小为 3×3，焦距和主点坐标单位为 px |
| `distortion_coefficients_video` | 视频畸变系数 D，顺序为 `[k1, k2, p1, p2, k3, k4, k5, k6]`，无量纲 |
| `rotation_camera_to_ego` | 相机坐标系到自车坐标系的 3×3 旋转矩阵 R |
| `translation_camera_to_ego` | 相机中心在自车坐标系中的位置 `[tx, ty, tz]`，单位 m |
| `rotation_quaternion_xyzw` | 与 R 等价的四元数，顺序固定为 `[x, y, z, w]` |
| `T_camera_to_ego` | 相机坐标系到自车坐标系的 4×4 齐次变换矩阵 |
| `T_ego_to_camera` | 自车坐标系到相机坐标系的 4×4 逆变换矩阵 |
| `video_projection_model` | `pinhole_rectified`：矫正后的针孔模型；`opencv_rational_8`：OpenCV 八参数有理畸变模型 |
| `video_processing_status` | `rectified`：已矫正；`residual_distortion`：仍保留畸变 |
| `camera_intrinsic_video_status` | 内参的采用状态及适用条件，结合本节使用说明读取 |

内参矩阵为 `K = [[fx, 0, cx], [0, fy, cy], [0, 0, 1]]`。其中 `fx`、`fy` 为以像素为单位的焦距，`cx`、`cy` 为主点坐标。

### C2.3 坐标系与外参方向

相机坐标系以相机光学中心为原点，X 向图像右方、Y 向图像下方、Z 沿光轴向前。自车坐标系以车辆后轴中心在地面的投影为原点，X 向车辆前方、Y 向左方、Z 向上方。

采用列向量，外参变换为 `p_ego = R × p_camera + t`。将自车坐标中的三维点投影至图像时，应先使用 `T_ego_to_camera` 得到相机坐标，再使用对应视频的 K 与 D 完成投影。四元数使用 x、y、z、w 顺序。

### C2.4 视频参数使用说明

- **前视 30°：** 按已矫正图像处理，文件中提供的 K 作为 1408×792 视频的有效内参，D 为八个零；使用针孔投影，不再次去畸变。这是本数据集采用的使用约定。
- **前视 120°：** 提供 1408×792 视频对应的 K 和八参数 D。该 K 以全幅等比例缩放、没有额外裁剪或重投影为适用条件，`camera_intrinsic_video_status` 标记为 `conditional_full_frame_resize`。
- **后视及四路侧视：** 提供 1920×1200 视频对应的 K 和八参数 D，按仍保留畸变的图像处理。
- 对视频进一步缩放、裁剪或去畸变后，应使用与处理结果匹配的输出内参。畸变系数不随单纯的像素尺寸缩放同比例变化。

OpenCV 八参数模型中的 `k1` 至 `k6` 为径向畸变系数，`p1`、`p2` 为切向畸变系数；定义见 [OpenCV 官方说明](https://docs.opencv.org/4.13.0/d9/d0c/group__calib3d.html)。
