clear; clc
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) 根目录：Data_Nanjing_collection =========
main_folder = getenv('TLCD_DATA_ROOT');
if isempty(main_folder)
    error('Set TLCD_DATA_ROOT to the city-level source directory.');
end

% ========= 2) 搜索所有 all.mat 以及对应的 MaxSpdlim_events.csv =========
date_dirs = dir(main_folder);
date_dirs = date_dirs([date_dirs.isdir]);
date_dirs = date_dirs(~ismember({date_dirs.name},{'.','..'}));

allMatPaths = {};
meta = struct('date_name',{}, 'segment_name',{}, 'mat_path',{}, 'out_dir',{});

allEventPaths = {};
meta2 = struct('date_name',{}, 'segment_name',{}, 'event_path',{}, 'out_dir',{});

for d = 1:numel(date_dirs)
    date_name = date_dirs(d).name;
    date_path = fullfile(date_dirs(d).folder, date_name);

    % 只处理 8 位数字日期目录
    if length(date_name) ~= 8 || any(~isstrprop(date_name,'digit'))
        continue;
    end

    % if ~strcmp(date_name, '20241025')
    %     continue
    % end

    allmat_root = fullfile(date_path, 'mat');
    if ~exist(allmat_root, 'dir')
        continue;
    end

    Event_record_root = fullfile(date_path, 'zEvent_MaxSpdlim');
    if ~exist(Event_record_root, 'dir')
        continue;
    end

    seg_dirs = dir(allmat_root);
    seg_dirs = seg_dirs([seg_dirs.isdir]);
    seg_dirs = seg_dirs(~ismember({seg_dirs.name},{'.','..'}));

    event_dirs = dir(Event_record_root);
    event_dirs = event_dirs([event_dirs.isdir]);
    event_dirs = event_dirs(~ismember({event_dirs.name},{'.','..'}));

    % 输出根：与 sim_result 同级
    sim_root = fullfile(date_path, 'zEvent_MaxSpdlim');
    if ~exist(sim_root,'dir')
        mkdir(sim_root);
    end

    for s = 1:numel(seg_dirs)
        segment_name = seg_dirs(s).name;
        seg_path = fullfile(seg_dirs(s).folder, segment_name);
        allmat_path = fullfile(seg_path, 'all.mat');
        if ~exist(allmat_path,'file')
            continue;
        end

        Event_name = event_dirs(s).name;
        Event_path = fullfile(event_dirs(s).folder, Event_name);
        Event_record_path = fullfile(Event_path, 'MaxSpdlim_events.csv');
        if ~exist(Event_record_path,'file')
            continue;
        end

        % 输出目录：...\YYYYMMDD\zEvent_MaxSpdlim\<segment>\
        out_dir = fullfile(sim_root, Event_name);
        if ~exist(out_dir,'dir')
            mkdir(out_dir);
        end

        allMatPaths{end+1,1} = allmat_path; %#ok<AGROW>
        meta(end+1).date_name = date_name; %#ok<AGROW>
        meta(end).segment_name = segment_name;
        meta(end).mat_path = allmat_path;
        meta(end).out_dir = out_dir;

        allEventPaths{end+1,1} = Event_record_path; %#ok<AGROW>
        meta2(end+1).date_name = date_name; %#ok<AGROW>
        meta2(end).segment_name = Event_name;
        meta2(end).event_path = Event_record_path;
        meta2(end).out_dir = out_dir;
    end
end



numMats = numel(allMatPaths);
numEvents = numel(allEventPaths);
fprintf('Found %d all.mat files.\n', numMats);
fprintf('Found %d MaxSpdlim_events.csv files.\n', numEvents);
if numMats == 0
    error('未找到任何 all.mat：请确认目录结构为 YYYYMMDD\\mat\\<segment>\\all.mat');
end

if numMats ~= numEvents
    error('all.mat文件数量与csv事件数量不匹配，请检查');
end


% ========= 3) 分割每一个5min segment 中记录的每一个 event =========
% ========= 4) 单独保存每一个 event 的关键证据链           =========
for i = 1:numMats
    % ---- 加载数据 ----
    mat_path = meta(i).mat_path;
    event_path = meta2(i).event_path;
    out_dir  = meta2(i).out_dir;
    fprintf('[%d/%d] Preparing: %s\n', i, numMats, event_path);

    event_data = readtable(event_path);
    if ~all(ismember({'start_idx', 'end_idx'}, event_data.Properties.VariableNames))
        continue;
    end
    valid_event_rows = ~ismissing(event_data.start_idx) & ~ismissing(event_data.end_idx);
    event_data = event_data(valid_event_rows, :);
    S = load(mat_path);

    % ---- 1.自车信息 ----
    Ego_GNSS_Longitude = S.VH_1_Sf_GNSS_struct.LLALongitude(:,2);
    Ego_GNSS_Latitude  = S.VH_1_Sf_GNSS_struct.LLALatitude(:,2);
    Ego_GNSS_Azimuth   = S.VH_1_Sf_GNSS_struct.Sf_GNSS_Azimuth(:,2);
    Ego_INS_Velocity = S.VH_1_Sf_INS_struct.Sf_INS_VelocitySpeed(:,2);

    % ---- 2.感知信息 ----
    % ---- 2.1障碍物信息 ----
    Obj_Class        = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjClass(:,2:31);
    Obj_Flagstate    = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjFlagstate(:,2:31);
    Obj_Speed        = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjVelocityX(:,2:31);
    Obj_RelativeLane = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjlane(:,2:31);
    Obj_Confidence   = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjLivingCounter(:,2:31);
    Obj_DistanceX    = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjDistanceX(:,2:31);
    Obj_DistanceY    = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjDistanceY(:,2:31);
    Obj_RelativeVx   = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjRelativeVelocityX(:,2:31);
    Obj_RelativeVy   = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjRelativeVelocityY(:,2:31);
    Obj_Length       = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjBoxSizeX(:,2:31);
    Obj_Width        = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjBoxSizeY(:,2:31);
    Obj_TrackStatus  = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjTrackStatus(:,2:31);

    % ---- 2.2车道线信息 ----
    FP_LaneLine_Left1_Quality  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANQuality_Cnt_enum(:,6);
    FP_LaneLine_Left2_Quality  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANQuality_Cnt_enum(:,8);
    FP_LaneLine_Right1_Quality = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANQuality_Cnt_enum(:,7);
    FP_LaneLine_Right2_Quality = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANQuality_Cnt_enum(:,9);

    FP_LaneLine_Left1_C0  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC0_m_float32(:,6);
    FP_LaneLine_Left2_C0  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC0_m_float32(:,8);
    FP_LaneLine_Right1_C0 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC0_m_float32(:,7);
    FP_LaneLine_Right2_C0 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC0_m_float32(:,9);

    FP_LaneLine_Left1_C1  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC1_rad_float32(:,6);
    FP_LaneLine_Left2_C1  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC1_rad_float32(:,8);
    FP_LaneLine_Right1_C1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC1_rad_float32(:,7);
    FP_LaneLine_Right2_C1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC1_rad_float32(:,9);

    FP_LaneLine_Left1_C0  = laneline0_memory(FP_LaneLine_Left1_C0);
    FP_LaneLine_Left2_C0  = laneline0_memory(FP_LaneLine_Left2_C0);
    FP_LaneLine_Right1_C0 = laneline0_memory(FP_LaneLine_Right1_C0);
    FP_LaneLine_Right2_C0 = laneline0_memory(FP_LaneLine_Right2_C0);

    FP_LaneLine_Left1_C1  = laneline0_memory(FP_LaneLine_Left1_C1);
    FP_LaneLine_Left2_C1  = laneline0_memory(FP_LaneLine_Left2_C1);
    FP_LaneLine_Right1_C1 = laneline0_memory(FP_LaneLine_Right1_C1);
    FP_LaneLine_Right2_C1 = laneline0_memory(FP_LaneLine_Right2_C1);

    FP_LaneLine_Left1_C2  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC2_1m_float32(:,6);
    FP_LaneLine_Left2_C2  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC2_1m_float32(:,8);
    FP_LaneLine_Right1_C2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC2_1m_float32(:,7);
    FP_LaneLine_Right2_C2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC2_1m_float32(:,9);

    FP_LaneLine_Left1_C3  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC3_1m2_float32(:,6);
    FP_LaneLine_Left2_C3  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC3_1m2_float32(:,8);
    FP_LaneLine_Right1_C3 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC3_1m2_float32(:,7);
    FP_LaneLine_Right2_C3 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC3_1m2_float32(:,9);

    FP_LaneLine_Left1_Type  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANType_Cnt_enum(:,6);
    FP_LaneLine_Left2_Type  = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANType_Cnt_enum(:,8);
    FP_LaneLine_Right1_Type = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANType_Cnt_enum(:,7);
    FP_LaneLine_Right2_Type = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANType_Cnt_enum(:,9);

    % ---- 3.地图信息 ----
    Road_type  = S.VH_1_IDT_Sf_MapLocSrv_KpRoad_struct.Sf_EHRKpRoadCurrentType(:,2);
    Road_Curve = S.VH_1_IDT_Sf_MapLocSrv_RoadParameter_struct.Sf_EHRRoadParameterCurrentCurve(:,2);
    Road_Slope = S.VH_1_IDT_Sf_MapLocSrv_RoadParameter_struct.Sf_EHRRoadParameterCurrentSlopex(:,2);

    Lane_type_CurrentLane = S.VH_1_IDT_Sf_MapLocSrv_LaneType_struct.Sf_EHRLaneTypeCurrentType(:,2);
    Lane_type_LeftLane    = S.VH_1_IDT_Sf_MapLocSrv_LaneType_struct.Sf_EHRLaneTypeLeftType(:,2);
    Lane_type_RightLane   = S.VH_1_IDT_Sf_MapLocSrv_LaneType_struct.Sf_EHRLaneTypeRightType(:,2);

    [LaneMaxSpdlim, LaneNumSameDirection, EgoLaneIndexBase] = derive_s5_lane_speed_limit_info(S);

    MAP_LaneLine_Left1_Quality  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineConfidences.left(:,2);
    MAP_LaneLine_Left2_Quality  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineConfidences.left2(:,2);
    MAP_LaneLine_Right1_Quality = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineConfidences.right(:,2);
    MAP_LaneLine_Right2_Quality = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineConfidences.right2(:,2);

    MAP_LaneLine_Left1_C0  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left(:,2);
    MAP_LaneLine_Left2_C0  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left2(:,2);
    MAP_LaneLine_Right1_C0 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right(:,2);
    MAP_LaneLine_Right2_C0 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right2(:,2);

    MAP_LaneLine_Left1_C1  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left(:,3);
    MAP_LaneLine_Left2_C1  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left2(:,3);
    MAP_LaneLine_Right1_C1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right(:,3);
    MAP_LaneLine_Right2_C1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right2(:,3);

    MAP_LaneLine_Left1_C2  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left(:,4);
    MAP_LaneLine_Left2_C2  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left2(:,4);
    MAP_LaneLine_Right1_C2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right(:,4);
    MAP_LaneLine_Right2_C2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right2(:,4);

    MAP_LaneLine_Left1_C3  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left(:,5);
    MAP_LaneLine_Left2_C3  = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left2(:,5);
    MAP_LaneLine_Right1_C3 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right(:,5);
    MAP_LaneLine_Right2_C3 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right2(:,5);

    MAP_LaneLine_Left1_Type  = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeLeft1CurrentLinearType(:,2);
    MAP_LaneLine_Left2_Type  = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeLeft2CurrentLinearType(:,2);
    MAP_LaneLine_Right1_Type = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeRight1CurrentLinearType(:,2);
    MAP_LaneLine_Right2_Type = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeRight2CurrentLinearType(:,2);

    MAP_LaneLine_Left1_Attribute  = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeLeft1CurrentLinearObjType(:,2);
    MAP_LaneLine_Left2_Attribute  = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeLeft2CurrentLinearObjType(:,2);
    MAP_LaneLine_Right1_Attribute = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeRight1CurrentLinearObjType(:,2);
    MAP_LaneLine_Right2_Attribute = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeRight2CurrentLinearObjType(:,2);

    MAP_LaneLine_Left1_Color  = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeLeft1CurrentLaneMarkingColor(:,2);
    MAP_LaneLine_Left2_Color  = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeLeft2CurrentLaneMarkingColor(:,2);
    MAP_LaneLine_Right1_Color = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeRight1CurrentLaneMarkingColor(:,2);
    MAP_LaneLine_Right2_Color = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeRight2CurrentLaneMarkingColor(:,2);

    % ---- 每个事件单独处理 ----
    num_event = size(event_data,1);
    for j = 1:num_event
        event_start_idx = event_data.start_idx(j);
        event_end_idx   = event_data.end_idx(j);
        event_time      = 0.01*(1:event_data.len(j))';

        idx = event_start_idx:event_end_idx;

        % 1.写入自车信息
        T1 = table( ...
            event_time, Ego_GNSS_Longitude(idx), Ego_GNSS_Latitude(idx), ...
            Ego_GNSS_Azimuth(idx), Ego_INS_Velocity(idx), ...
            'VariableNames', {'event_time','Ego_GNSS_Longitude','Ego_GNSS_Latitude', ...
            'Ego_GNSS_Azimuth','Ego_velocity'} ...
            );

        % 2.写入障碍物感知信息
        % ===== 2.1 障碍物信息（裁剪到事件区间）=====
        Obj_Class_ev        = Obj_Class(idx, :);
        Obj_Flagstate_ev    = Obj_Flagstate(idx, :);
        Obj_Speed_ev        = Obj_Speed(idx, :);
        Obj_RelativeLane_ev = Obj_RelativeLane(idx, :);
        Obj_Confidence_ev   = Obj_Confidence(idx, :);
        Obj_DistanceX_ev    = Obj_DistanceX(idx, :);
        Obj_DistanceY_ev    = Obj_DistanceY(idx, :);
        Obj_RelativeVx_ev   = Obj_RelativeVx(idx, :);
        Obj_RelativeVy_ev   = Obj_RelativeVy(idx, :);
        Obj_Length_ev       = Obj_Length(idx, :);
        Obj_Width_ev        = Obj_Width(idx, :);
        Obj_TrackStatus_ev  = Obj_TrackStatus(idx, :);

        % ===== 2.2 构造列名：Obj01_Speed ... Obj30_Speed =====
        numObj = size(Obj_Class_ev, 2);
        objTags = arrayfun(@(k) sprintf('Obj%02d', k), 1:numObj, 'UniformOutput', false);

        makeNames = @(suffix) strcat(objTags, "_", suffix);

        varNames = [ ...
            makeNames("Class"), makeNames("Flagstate"), makeNames("Speed"), ...
            makeNames("RelativeLane"), makeNames("Confidence"), ...
            makeNames("DistanceX"), makeNames("DistanceY"), ...
            makeNames("RelativeVx"), makeNames("RelativeVy"), ...
            makeNames("Length"), makeNames("Width"), makeNames("TrackStatus") ...
            ];

        % ===== 2.3 拼成一个矩阵，再转 table =====
        ObjMat = [ ...
            Obj_Class_ev, Obj_Flagstate_ev, Obj_Speed_ev, ...
            Obj_RelativeLane_ev, Obj_Confidence_ev, ...
            Obj_DistanceX_ev, Obj_DistanceY_ev, ...
            Obj_RelativeVx_ev, Obj_RelativeVy_ev, ...
            Obj_Length_ev, Obj_Width_ev, Obj_TrackStatus_ev ...
            ];

        T2 = array2table(ObjMat, 'VariableNames', varNames);

        % ===== 2.4 把 event_time 也放进去（方便对齐） =====
        T2 = addvars(T2, event_time, 'Before', 1, 'NewVariableNames', 'event_time');

        % 3.写入地图信息
        % ===== 事件区间内 Road_type，构造感知车道线覆盖mask =====
        Road_type_ev = Road_type(idx);
        use_fp_lane = (Road_type_ev > -1);   % 对有效道路帧，统一使用感知车道线

        % ===== 先取 MAP / FP 对应字段（事件区间裁剪）=====
        % ---- Quality
        MAP_L1_Q = MAP_LaneLine_Left1_Quality(idx);   FP_L1_Q = FP_LaneLine_Left1_Quality(idx);
        MAP_L2_Q = MAP_LaneLine_Left2_Quality(idx);   FP_L2_Q = FP_LaneLine_Left2_Quality(idx);
        MAP_R1_Q = MAP_LaneLine_Right1_Quality(idx);  FP_R1_Q = FP_LaneLine_Right1_Quality(idx);
        MAP_R2_Q = MAP_LaneLine_Right2_Quality(idx);  FP_R2_Q = FP_LaneLine_Right2_Quality(idx);

        % ---- C0~C3
        MAP_L1_C0 = MAP_LaneLine_Left1_C0(idx);  FP_L1_C0 = FP_LaneLine_Left1_C0(idx);
        MAP_L2_C0 = MAP_LaneLine_Left2_C0(idx);  FP_L2_C0 = FP_LaneLine_Left2_C0(idx);
        MAP_R1_C0 = MAP_LaneLine_Right1_C0(idx); FP_R1_C0 = FP_LaneLine_Right1_C0(idx);
        MAP_R2_C0 = MAP_LaneLine_Right2_C0(idx); FP_R2_C0 = FP_LaneLine_Right2_C0(idx);

        MAP_L1_C1 = MAP_LaneLine_Left1_C1(idx);  FP_L1_C1 = FP_LaneLine_Left1_C1(idx);
        MAP_L2_C1 = MAP_LaneLine_Left2_C1(idx);  FP_L2_C1 = FP_LaneLine_Left2_C1(idx);
        MAP_R1_C1 = MAP_LaneLine_Right1_C1(idx); FP_R1_C1 = FP_LaneLine_Right1_C1(idx);
        MAP_R2_C1 = MAP_LaneLine_Right2_C1(idx); FP_R2_C1 = FP_LaneLine_Right2_C1(idx);

        MAP_L1_C2 = MAP_LaneLine_Left1_C2(idx);  FP_L1_C2 = FP_LaneLine_Left1_C2(idx);
        MAP_L2_C2 = MAP_LaneLine_Left2_C2(idx);  FP_L2_C2 = FP_LaneLine_Left2_C2(idx);
        MAP_R1_C2 = MAP_LaneLine_Right1_C2(idx); FP_R1_C2 = FP_LaneLine_Right1_C2(idx);
        MAP_R2_C2 = MAP_LaneLine_Right2_C2(idx); FP_R2_C2 = FP_LaneLine_Right2_C2(idx);

        MAP_L1_C3 = MAP_LaneLine_Left1_C3(idx);  FP_L1_C3 = FP_LaneLine_Left1_C3(idx);
        MAP_L2_C3 = MAP_LaneLine_Left2_C3(idx);  FP_L2_C3 = FP_LaneLine_Left2_C3(idx);
        MAP_R1_C3 = MAP_LaneLine_Right1_C3(idx); FP_R1_C3 = FP_LaneLine_Right1_C3(idx);
        MAP_R2_C3 = MAP_LaneLine_Right2_C3(idx); FP_R2_C3 = FP_LaneLine_Right2_C3(idx);

        % ---- Type
        MAP_L1_T = MAP_LaneLine_Left1_Type(idx);   FP_L1_T = FP_LaneLine_Left1_Type(idx);
        MAP_L2_T = MAP_LaneLine_Left2_Type(idx);   FP_L2_T = FP_LaneLine_Left2_Type(idx);
        MAP_R1_T = MAP_LaneLine_Right1_Type(idx);  FP_R1_T = FP_LaneLine_Right1_Type(idx);
        MAP_R2_T = MAP_LaneLine_Right2_Type(idx);  FP_R2_T = FP_LaneLine_Right2_Type(idx);

        % ===== 有效道路帧：用 FP 替换 MAP =====
        MAP_L1_Q(use_fp_lane) = FP_L1_Q(use_fp_lane);
        MAP_L2_Q(use_fp_lane) = FP_L2_Q(use_fp_lane);
        MAP_R1_Q(use_fp_lane) = FP_R1_Q(use_fp_lane);
        MAP_R2_Q(use_fp_lane) = FP_R2_Q(use_fp_lane);

        MAP_L1_C0(use_fp_lane) = FP_L1_C0(use_fp_lane);
        MAP_L2_C0(use_fp_lane) = FP_L2_C0(use_fp_lane);
        MAP_R1_C0(use_fp_lane) = FP_R1_C0(use_fp_lane);
        MAP_R2_C0(use_fp_lane) = FP_R2_C0(use_fp_lane);

        MAP_L1_C1(use_fp_lane) = FP_L1_C1(use_fp_lane);
        MAP_L2_C1(use_fp_lane) = FP_L2_C1(use_fp_lane);
        MAP_R1_C1(use_fp_lane) = FP_R1_C1(use_fp_lane);
        MAP_R2_C1(use_fp_lane) = FP_R2_C1(use_fp_lane);

        MAP_L1_C2(use_fp_lane) = FP_L1_C2(use_fp_lane);
        MAP_L2_C2(use_fp_lane) = FP_L2_C2(use_fp_lane);
        MAP_R1_C2(use_fp_lane) = FP_R1_C2(use_fp_lane);
        MAP_R2_C2(use_fp_lane) = FP_R2_C2(use_fp_lane);

        MAP_L1_C3(use_fp_lane) = FP_L1_C3(use_fp_lane);
        MAP_L2_C3(use_fp_lane) = FP_L2_C3(use_fp_lane);
        MAP_R1_C3(use_fp_lane) = FP_R1_C3(use_fp_lane);
        MAP_R2_C3(use_fp_lane) = FP_R2_C3(use_fp_lane);

        MAP_L1_T(use_fp_lane) = FP_L1_T(use_fp_lane);
        MAP_L2_T(use_fp_lane) = FP_L2_T(use_fp_lane);
        MAP_R1_T(use_fp_lane) = FP_R1_T(use_fp_lane);
        MAP_R2_T(use_fp_lane) = FP_R2_T(use_fp_lane);

        % ===== 其他地图信息（你也可以一起写进T3）=====
        Road_Curve_ev = Road_Curve(idx);
        Road_Slope_ev = Road_Slope(idx);

        Lane_type_CurrentLane_ev = Lane_type_CurrentLane(idx);
        Lane_type_LeftLane_ev    = Lane_type_LeftLane(idx);
        Lane_type_RightLane_ev   = Lane_type_RightLane(idx);

        LaneMaxSpdlim_ev = LaneMaxSpdlim(idx, :);
        LaneMinSpdlim_ev = zeros(size(LaneMaxSpdlim_ev));
        LaneNumSameDirection_ev = LaneNumSameDirection(idx);
        EgoLaneIndex_ev = refine_s5_ego_lane_index(EgoLaneIndexBase(idx), MAP_L1_C0, MAP_R1_C0);

        % ===== 组表 T3 =====
        T3 = table( ...
            event_time, Road_type_ev, Road_Curve_ev, Road_Slope_ev, ...
            Lane_type_CurrentLane_ev, Lane_type_LeftLane_ev, Lane_type_RightLane_ev, ...
            LaneMaxSpdlim_ev(:,1), LaneMaxSpdlim_ev(:,2), LaneMaxSpdlim_ev(:,3), LaneMaxSpdlim_ev(:,4), LaneMaxSpdlim_ev(:,5), ...
            LaneMinSpdlim_ev(:,1), LaneMinSpdlim_ev(:,2), LaneMinSpdlim_ev(:,3), LaneMinSpdlim_ev(:,4), LaneMinSpdlim_ev(:,5), ...
            LaneNumSameDirection_ev, EgoLaneIndex_ev, ...
            MAP_L1_Q, MAP_L2_Q, MAP_R1_Q, MAP_R2_Q, ...
            MAP_L1_C0, MAP_L2_C0, MAP_R1_C0, MAP_R2_C0, ...
            MAP_L1_C1, MAP_L2_C1, MAP_R1_C1, MAP_R2_C1, ...
            MAP_L1_C2, MAP_L2_C2, MAP_R1_C2, MAP_R2_C2, ...
            MAP_L1_C3, MAP_L2_C3, MAP_R1_C3, MAP_R2_C3, ...
            MAP_L1_T, MAP_L2_T, MAP_R1_T, MAP_R2_T, ...
            'VariableNames', { ...
            'event_time','Road_type','Road_Curve','Road_Slope', ...
            'Lane_type_CurrentLane','Lane_type_LeftLane','Lane_type_RightLane', ...
            'LaneMaxSpdlim_1','LaneMaxSpdlim_2','LaneMaxSpdlim_3','LaneMaxSpdlim_4','LaneMaxSpdlim_5', ...
            'LaneMinSpdlim_1','LaneMinSpdlim_2','LaneMinSpdlim_3','LaneMinSpdlim_4','LaneMinSpdlim_5', ...
            'LaneNumSameDirection','EgoLaneIndex', ...
            'MAP_Q_Left1','MAP_Q_Left2','MAP_Q_Right1','MAP_Q_Right2', ...
            'MAP_C0_Left1','MAP_C0_Left2','MAP_C0_Right1','MAP_C0_Right2', ...
            'MAP_C1_Left1','MAP_C1_Left2','MAP_C1_Right1','MAP_C1_Right2', ...
            'MAP_C2_Left1','MAP_C2_Left2','MAP_C2_Right1','MAP_C2_Right2', ...
            'MAP_C3_Left1','MAP_C3_Left2','MAP_C3_Right1','MAP_C3_Right2', ...
            'MAP_Type_Left1','MAP_Type_Left2','MAP_Type_Right1','MAP_Type_Right2'} ...
            );


        % ---- 写CSV到对应event目录 ----
        % 建议文件名：MaxSpdlim_event_<n>_EgoInfo.csv
        EgoInfo = ['MaxSpdlim_event_', num2str(j), '_EgoInfo.csv'];
        csv_path = fullfile(out_dir, EgoInfo);
        writetable(T1, csv_path);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, EgoInfo);

        ObjInfo = ['MaxSpdlim_event_', num2str(j), '_ObjInfo.csv'];
        csv_path2 = fullfile(out_dir, ObjInfo);
        writetable(T2, csv_path2);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, ObjInfo);

        MapInfo = ['MaxSpdlim_event_', num2str(j), '_MapInfo.csv'];
        csv_path3 = fullfile(out_dir, MapInfo);
        writetable(T3, csv_path3);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, MapInfo);


    end


end
