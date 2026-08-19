clear; clc
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) 鏍圭洰褰曪細Data_Nanjing_collection =========
main_folder = getenv('TLCD_DATA_ROOT');
if isempty(main_folder)
    error('Set TLCD_DATA_ROOT to the city-level source directory.');
end

% ========= 2) 鎼滅储鎵€鏈?all2.mat 浠ュ強瀵瑰簲鐨?MinSpdlim_events.csv =========
date_dirs = dir(main_folder);
date_dirs = date_dirs([date_dirs.isdir]);
date_dirs = date_dirs(~ismember({date_dirs.name},{'.','..'}));

allMatPaths = {};
meta = struct('date_name',{}, 'segment_name',{}, 'mat_path',{}, 'out_dir',{});
monitorMatPaths = {};

allEventPaths = {};
meta2 = struct('date_name',{}, 'segment_name',{}, 'event_path',{}, 'out_dir',{});

for d = 1:numel(date_dirs)
    date_name = date_dirs(d).name;
    date_path = fullfile(date_dirs(d).folder, date_name);

    % 鍙鐞?8 浣嶆暟瀛楁棩鏈熺洰褰?
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

    monitor_result_root = fullfile(date_path, 'sim_result');
    if ~exist(monitor_result_root, 'dir')
        continue;
    end

    Event_record_root = fullfile(date_path, 'zEvent_MinSpdlim');
    if ~exist(Event_record_root, 'dir')
        continue;
    end

    seg_dirs = dir(allmat_root);
    seg_dirs = seg_dirs([seg_dirs.isdir]);
    seg_dirs = seg_dirs(~ismember({seg_dirs.name},{'.','..'}));

    % 杈撳嚭鏍癸細涓?sim_result 鍚岀骇
    sim_root = fullfile(date_path, 'zEvent_MinSpdlim');
    if ~exist(sim_root,'dir')
        mkdir(sim_root);
    end

    for s = 1:numel(seg_dirs)
        segment_name = seg_dirs(s).name;
        seg_path = fullfile(seg_dirs(s).folder, segment_name);
        allmat_path = fullfile(seg_path, 'all2.mat');
        if ~exist(allmat_path,'file')
            continue;
        end

        monitor_result_path = fullfile(monitor_result_root, segment_name, 'monitor_result.mat');
        if ~exist(monitor_result_path, 'file')
            continue;
        end

        Event_name = segment_name;
        Event_path = fullfile(Event_record_root, Event_name);
        Event_record_path = fullfile(Event_path, 'MinSpdlim_events.csv');
        if ~exist(Event_record_path,'file')
            continue;
        end

        % 杈撳嚭鐩綍锛?..\YYYYMMDD\zEvent_MinSpdlim\<segment>\
        out_dir = fullfile(sim_root, Event_name);
        if ~exist(out_dir,'dir')
            mkdir(out_dir);
        end

        allMatPaths{end+1,1} = allmat_path; %#ok<AGROW>
        meta(end+1).date_name = date_name; %#ok<AGROW>
        meta(end).segment_name = segment_name;
        meta(end).mat_path = allmat_path;
        meta(end).out_dir = out_dir;
        monitorMatPaths{end+1,1} = monitor_result_path; %#ok<AGROW>

        allEventPaths{end+1,1} = Event_record_path; %#ok<AGROW>
        meta2(end+1).date_name = date_name; %#ok<AGROW>
        meta2(end).segment_name = Event_name;
        meta2(end).event_path = Event_record_path;
        meta2(end).out_dir = out_dir;
    end
end

numMats = numel(allMatPaths);
numEvents = numel(allEventPaths);
fprintf('Found %d all2.mat files.\n', numMats);
fprintf('Found %d MinSpdlim_events.csv files.\n', numEvents);
if numMats == 0
    error('No all2.mat found. Expected YYYYMMDD\\mat\\<segment>\\all2.mat');
end

if numMats ~= numEvents
    error('The number of all2.mat files does not match the number of MinSpdlim_events.csv files.');
end


% ========= 3) 鍒嗗壊姣忎竴涓?min segment 涓褰曠殑姣忎竴涓?event =========
% ========= 4) 鍗曠嫭淇濆瓨姣忎竴涓?event 鐨勫叧閿瘉鎹摼           =========
for i = 1:numMats
    % ---- 鍔犺浇鏁版嵁 ----
    mat_path = meta(i).mat_path;
    monitor_path = monitorMatPaths{i};
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
    M = load(monitor_path);
    seg_data = M.sim_output;

    % ---- 1.鑷溅淇℃伅 ----
    Ego_GNSS_Longitude = S.VH_1_Sf_GNSS_struct.LLALongitude(:,2);
    Ego_GNSS_Latitude  = S.VH_1_Sf_GNSS_struct.LLALatitude(:,2);
    Ego_GNSS_Azimuth   = S.VH_1_Sf_GNSS_struct.Sf_GNSS_Azimuth(:,2);
    Ego_INS_Velocity = S.VH_1_Sf_INS_struct.Sf_INS_VelocitySpeed(:,2);

    % ---- 2.鎰熺煡淇℃伅 ----
    % ---- 2.1闅滅鐗╀俊鎭?----
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

    % ---- 2.2杞﹂亾绾夸俊鎭?----
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

    % ---- 3.鍦板浘淇℃伅 ----
    Road_type  = S.VH_1_IDT_Sf_MapLocSrv_KpRoad_struct.Sf_EHRKpRoadCurrentType(:,2);
    Road_Curve = S.VH_1_IDT_Sf_MapLocSrv_RoadParameter_struct.Sf_EHRRoadParameterCurrentCurve(:,2);
    Road_Slope = S.VH_1_IDT_Sf_MapLocSrv_RoadParameter_struct.Sf_EHRRoadParameterCurrentSlopex(:,2);

    Lane_type_CurrentLane = S.VH_1_IDT_Sf_MapLocSrv_LaneType_struct.Sf_EHRLaneTypeCurrentType(:,2);
    Lane_type_LeftLane    = S.VH_1_IDT_Sf_MapLocSrv_LaneType_struct.Sf_EHRLaneTypeLeftType(:,2);
    Lane_type_RightLane   = S.VH_1_IDT_Sf_MapLocSrv_LaneType_struct.Sf_EHRLaneTypeRightType(:,2);

    LaneMaxSpdlim = compact_sign_speed_limit_max(get_sign_speed_limit_max(seg_data));
    LaneMinSpdlim = calc_lane_min_spdlim_matrix(LaneMaxSpdlim);
    LaneNumSameDirection = sum(LaneMaxSpdlim > 60, 2);
    IsExistLeftLane = get_signal(seg_data, 'IsExistLeftLane');
    IsExistLeft2Lane = get_signal(seg_data, 'IsExistLeft2Lane');
    IsExistRightLane = get_signal(seg_data, 'IsExistRightLane');
    IsExistRight2Lane = get_signal(seg_data, 'IsExistRight2Lane');
    EgoLaneIndexBase = double(IsExistLeft2Lane == 1) + double(IsExistLeftLane == 1) + 1;
    LanePos = derive_lane_pos(IsExistLeftLane, IsExistLeft2Lane, IsExistRightLane, IsExistRight2Lane);

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

    % ---- 姣忎釜浜嬩欢鍗曠嫭澶勭悊 ----
    num_event = size(event_data,1);
    for j = 1:num_event
        event_start_idx = event_data.start_idx(j);
        event_end_idx   = event_data.end_idx(j);
        event_time      = 0.01*(1:event_data.len(j))';

        idx = event_start_idx:event_end_idx;

        % 1.鍐欏叆鑷溅淇℃伅
        T1 = table( ...
            event_time, Ego_GNSS_Longitude(idx), Ego_GNSS_Latitude(idx), ...
            Ego_GNSS_Azimuth(idx), Ego_INS_Velocity(idx), ...
            'VariableNames', {'event_time','Ego_GNSS_Longitude','Ego_GNSS_Latitude', ...
            'Ego_GNSS_Azimuth','Ego_velocity'} ...
            );

        % 2.鍐欏叆闅滅鐗╂劅鐭ヤ俊鎭?
        % ===== 2.1 闅滅鐗╀俊鎭紙瑁佸壀鍒颁簨浠跺尯闂达級=====
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

        % ===== 2.2 鏋勯€犲垪鍚嶏細Obj01_Speed ... Obj30_Speed =====
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

        % ===== 2.3 鎷兼垚涓€涓煩闃碉紝鍐嶈浆 table =====
        ObjMat = [ ...
            Obj_Class_ev, Obj_Flagstate_ev, Obj_Speed_ev, ...
            Obj_RelativeLane_ev, Obj_Confidence_ev, ...
            Obj_DistanceX_ev, Obj_DistanceY_ev, ...
            Obj_RelativeVx_ev, Obj_RelativeVy_ev, ...
            Obj_Length_ev, Obj_Width_ev, Obj_TrackStatus_ev ...
            ];

        T2 = array2table(ObjMat, 'VariableNames', varNames);

        % ===== 2.4 鎶?event_time 涔熸斁杩涘幓锛堟柟渚垮榻愶級 =====
        T2 = addvars(T2, event_time, 'Before', 1, 'NewVariableNames', 'event_time');

        % 3.鍐欏叆鍦板浘淇℃伅
        % ===== 浜嬩欢鍖洪棿鍐?Road_type锛屾瀯閫犳劅鐭ヨ溅閬撶嚎瑕嗙洊mask =====
        Road_type_ev = Road_type(idx);
        use_fp_lane = (Road_type_ev > -1);   % 瀵规湁鏁堥亾璺抚锛岀粺涓€浣跨敤鎰熺煡杞﹂亾绾?
        % ===== 鍏堝彇 MAP / FP 瀵瑰簲瀛楁锛堜簨浠跺尯闂磋鍓級=====
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

        % ===== 鏈夋晥閬撹矾甯э細鐢?FP 鏇挎崲 MAP =====
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

        % ===== 鍏朵粬鍦板浘淇℃伅锛堜綘涔熷彲浠ヤ竴璧峰啓杩汿3锛?====
        Road_Curve_ev = Road_Curve(idx);
        Road_Slope_ev = Road_Slope(idx);

        Lane_type_CurrentLane_ev = Lane_type_CurrentLane(idx);
        Lane_type_LeftLane_ev    = Lane_type_LeftLane(idx);
        Lane_type_RightLane_ev   = Lane_type_RightLane(idx);

        LaneMaxSpdlim_ev = LaneMaxSpdlim(idx, :);
        LaneMinSpdlim_ev = LaneMinSpdlim(idx, :);
        if ismember('extract_type', event_data.Properties.VariableNames) && ...
                ismember('key_idx_s', event_data.Properties.VariableNames) && ...
                string(event_data.extract_type(j)) == "sign_1_to_0"
            LaneMinSpdlim_ev(idx < event_data.key_idx_s(j), :) = 0;
        end
        LaneNumSameDirection_ev = LaneNumSameDirection(idx);
        EgoLaneIndex_ev = refine_lane_index_with_map(EgoLaneIndexBase(idx), MAP_L1_C0, MAP_R1_C0);

        % ===== 缁勮〃 T3 =====
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


        % ---- 鍐機SV鍒板搴攅vent鐩綍 ----
        % 寤鸿鏂囦欢鍚嶏細MinSpdlim_event_<n>_EgoInfo.csv
        EgoInfo = ['MinSpdlim_event_', num2str(j), '_EgoInfo.csv'];
        csv_path = fullfile(out_dir, EgoInfo);
        writetable(T1, csv_path);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, EgoInfo);

        ObjInfo = ['MinSpdlim_event_', num2str(j), '_ObjInfo.csv'];
        csv_path2 = fullfile(out_dir, ObjInfo);
        writetable(T2, csv_path2);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, ObjInfo);

        MapInfo = ['MinSpdlim_event_', num2str(j), '_MapInfo.csv'];
        csv_path3 = fullfile(out_dir, MapInfo);
        writetable(T3, csv_path3);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, MapInfo);


    end


end

function signal = get_signal(data, field_name)
try
    if isa(data, 'Simulink.SimulationOutput')
        signal = get(data, field_name);
    else
        signal = data.(field_name);
    end
catch
    error('Missing required signal: %s', field_name);
end

if isa(signal, 'timeseries')
    signal = signal.Data;
end
signal = double(signal);
signal = signal(:);
end

function sign_speed_limit_max = get_sign_speed_limit_max(data)
field_name = 'SignSpeedLimit_MAX';
try
    if isa(data, 'Simulink.SimulationOutput')
        raw = get(data, field_name);
    else
        raw = data.(field_name);
    end
catch
    error('Missing required signal: %s', field_name);
end

if isa(raw, 'timeseries')
    raw = raw.Data;
end
raw = double(raw);
dims = size(raw);

if ndims(raw) == 3
    if dims(1) == 1 && dims(2) == 5
        sign_speed_limit_max = squeeze(raw(1, :, :)).';
    elseif dims(1) == 5 && dims(2) == 1
        sign_speed_limit_max = squeeze(raw(:, 1, :)).';
    elseif dims(2) == 1 && dims(3) == 5
        sign_speed_limit_max = squeeze(raw(:, 1, :));
    elseif dims(1) == 1 && dims(3) == 5
        sign_speed_limit_max = squeeze(raw(1, :, :));
    else
        error('Unsupported SignSpeedLimit_MAX dimensions: %s', mat2str(dims));
    end
elseif ismatrix(raw)
    if size(raw, 2) == 5
        sign_speed_limit_max = raw;
    elseif size(raw, 1) == 5
        sign_speed_limit_max = raw.';
    elseif isvector(raw) && mod(numel(raw), 5) == 0
        sign_speed_limit_max = reshape(raw, 5, []).';
    else
        error('Unsupported SignSpeedLimit_MAX dimensions: %s', mat2str(dims));
    end
else
    error('Unsupported SignSpeedLimit_MAX dimensions: %s', mat2str(dims));
end
end

function sign_speed_limit_max = compact_sign_speed_limit_max(sign_speed_limit_max)
for i = 1:size(sign_speed_limit_max, 1)
    row = sign_speed_limit_max(i, :);
    positive_values = row(isfinite(row) & row > 0);
    sign_speed_limit_max(i, :) = 0;
    sign_speed_limit_max(i, 1:numel(positive_values)) = positive_values;
end
end

function lane_min_spdlim = calc_lane_min_spdlim_matrix(lane_max_spdlim)
lane_min_spdlim = zeros(size(lane_max_spdlim));
for lane_idx = 1:size(lane_max_spdlim, 2)
    for i = 1:size(lane_max_spdlim, 1)
        lane_min_spdlim(i, lane_idx) = min_speed_from_sign_lane(lane_idx, lane_max_spdlim(i, lane_idx));
        if lane_min_spdlim(i, lane_idx) < 0
            lane_min_spdlim(i, lane_idx) = 0;
        end
    end
end
end

function min_speed = min_speed_from_sign_lane(lane_index, max_speed)
max_speed = round(max_speed);
if ~isfinite(max_speed)
    min_speed = -1;
    return;
end

if max_speed < 60
    min_speed = 0;
    return;
end

switch lane_index
    case 1
        min_speed = map_max_to_min(max_speed, [80, 100, 120], [60, 80, 110]);
    case 2
        min_speed = map_max_to_min(max_speed, [80, 100, 120], [40, 60, 90]);
    case 3
        min_speed = map_max_to_min(max_speed, [60, 80, 100, 120], [0, 40, 60, 80]);
    case 4
        min_speed = map_max_to_min(max_speed, [60, 80, 100, 120], [0, 40, 60, 60]);
    case 5
        min_speed = map_max_to_min(max_speed, [60, 80, 100, 120], [0, 40, 60, 60]);
    otherwise
        min_speed = -1;
end
end

function min_speed = map_max_to_min(max_speed, max_values, min_values)
match_idx = find(max_values == max_speed, 1, 'first');
if isempty(match_idx)
    min_speed = -1;
else
    min_speed = min_values(match_idx);
end
end

function ego_lane_index = refine_lane_index_with_map(base_lane_index, left_c0, right_c0)
ego_lane_index = base_lane_index(:);
left_c0 = left_c0(:);
right_c0 = right_c0(:);
offset = 0;
for i = 1:numel(ego_lane_index)
    if i > 1 && base_lane_index(i) ~= base_lane_index(i - 1)
        offset = 0;
    end

    if i > 1 && offset == 0
        left_change = abs(left_c0(i - 1)) < 0.75 && abs(right_c0(i)) < 0.75 && ...
            left_c0(i) > 2 && ego_lane_index(i) > 1;
        right_change = abs(right_c0(i - 1)) < 0.75 && abs(left_c0(i)) < 0.75 && ...
            right_c0(i) < -2 && ego_lane_index(i) < 5;
        if left_change
            offset = -1;
        elseif right_change
            offset = 1;
        end
    end

    ego_lane_index(i) = min(5, max(1, ego_lane_index(i) + offset));
end
end

function lane_pos = derive_lane_pos(is_exist_left_lane, is_exist_left2_lane, is_exist_right_lane, is_exist_right2_lane)
left_count = double(is_exist_left_lane == 1) + double(is_exist_left2_lane == 1);
right_count = double(is_exist_right_lane == 1) + double(is_exist_right2_lane == 1);

lane_pos = zeros(size(left_count));
lane_pos(left_count == 0) = 1;
lane_pos(left_count > 0 & right_count > 0) = 2;
lane_pos(left_count > 0 & right_count == 0) = 3;
end
