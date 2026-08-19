clear; clc
script_dir = fileparts(mfilename('fullpath'));
if isempty(script_dir)
    script_dir = pwd;
end
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) Root directory =========
main_folder = getenv('TLCD_DATA_ROOT');
if isempty(main_folder)
    error('Set TLCD_DATA_ROOT to the city-level source directory.');
end


% ========= 2) Find all.mat and corresponding RoadMarking_events.csv =========
date_dirs = dir(main_folder);
date_dirs = date_dirs([date_dirs.isdir]);
date_dirs = date_dirs(~ismember({date_dirs.name},{'.','..'}));

allMatPaths = {};
meta = struct('date_name',{}, 'segment_name',{}, 'mat_path',{}, 'event_path',{}, 'out_dir',{});

for d = 1:numel(date_dirs)
    date_name = date_dirs(d).name;
    date_path = fullfile(date_dirs(d).folder, date_name);

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

    event_record_root = fullfile(date_path, 'zEvent_RoadMarking');
    if ~exist(event_record_root, 'dir')
        continue;
    end

    seg_dirs = dir(allmat_root);
    seg_dirs = seg_dirs([seg_dirs.isdir]);
    seg_dirs = seg_dirs(~ismember({seg_dirs.name},{'.','..'}));

    for s = 1:numel(seg_dirs)
        segment_name = seg_dirs(s).name;
        seg_path = fullfile(seg_dirs(s).folder, segment_name);

        allmat_path = fullfile(seg_path, 'all.mat');
        if ~exist(allmat_path, 'file')
            continue;
        end

        event_record_path = fullfile(event_record_root, segment_name, 'RoadMarking_events.csv');
        if ~exist(event_record_path, 'file')
            continue;
        end

        out_dir = fullfile(event_record_root, segment_name);
        if ~exist(out_dir, 'dir')
            mkdir(out_dir);
        end

        allMatPaths{end+1,1} = allmat_path; %#ok<AGROW>
        meta(end+1).date_name = date_name; %#ok<AGROW>
        meta(end).segment_name = segment_name;
        meta(end).mat_path = allmat_path;
        meta(end).event_path = event_record_path;
        meta(end).out_dir = out_dir;
    end
end

numMats = numel(allMatPaths);
fprintf('Found %d all.mat files with RoadMarking_events.csv.\n', numMats);
if numMats == 0
    error('No matched all.mat/RoadMarking_events.csv files found. Expected YYYYMMDD\\mat\\<segment>\\all.mat and YYYYMMDD\\zEvent_RoadMarking\\<segment>\\RoadMarking_events.csv.');
end

% ========= 3) Split each road-marking event into input CSV files =========
for i = 1:numMats
    mat_path = meta(i).mat_path;
    event_path = meta(i).event_path;
    out_dir = meta(i).out_dir;
    fprintf('[%d/%d] Preparing: %s\n', i, numMats, event_path);

    event_opts = detectImportOptions(event_path, 'TextType', 'string');
    event_data = readtable(event_path, event_opts);
    if ~all(ismember({'start_idx', 'end_idx'}, event_data.Properties.VariableNames))
        continue;
    end
    valid_event_rows = ~ismissing(event_data.start_idx) & ~ismissing(event_data.end_idx);
    event_data = event_data(valid_event_rows, :);
    S = load(mat_path);

    ego = load_ego_signals(S);
    obj = load_object_signals(S);
    map = load_map_signals(S);

    num_event = size(event_data, 1);
    for j = 1:num_event
        event_start_idx = event_data.start_idx(j);
        event_end_idx = event_data.end_idx(j);
        idx = event_start_idx:event_end_idx;
        event_time = 0.01*(1:numel(idx))';

        T1 = build_ego_table(event_time, ego, idx);
        T2 = build_object_table(event_time, obj, idx);
        T3 = build_map_table(event_time, map, idx);

        EgoInfo = ['RoadMarking_event_', num2str(j), '_EgoInfo.csv'];
        csv_path = fullfile(out_dir, EgoInfo);
        writetable(T1, csv_path, 'Encoding', 'UTF-8');
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, EgoInfo);

        ObjInfo = ['RoadMarking_event_', num2str(j), '_ObjInfo.csv'];
        csv_path2 = fullfile(out_dir, ObjInfo);
        writetable(T2, csv_path2, 'Encoding', 'UTF-8');
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, ObjInfo);

        MapInfo = ['RoadMarking_event_', num2str(j), '_MapInfo.csv'];
        csv_path3 = fullfile(out_dir, MapInfo);
        writetable(T3, csv_path3, 'Encoding', 'UTF-8');
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, MapInfo);
    end
end

function ego = load_ego_signals(S)
ego.Longitude = S.VH_1_Sf_GNSS_struct.LLALongitude(:,2);
ego.Latitude = S.VH_1_Sf_GNSS_struct.LLALatitude(:,2);
ego.Azimuth = S.VH_1_Sf_GNSS_struct.Sf_GNSS_Azimuth(:,2);
ego.Velocity = S.VH_1_Sf_INS_struct.Sf_INS_VelocitySpeed(:,2);
end

function obj = load_object_signals(S)
obj.Class = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjClass(:,2:31);
obj.Flagstate = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjFlagstate(:,2:31);
obj.Speed = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjVelocityX(:,2:31);
obj.RelativeLane = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjlane(:,2:31);
obj.Confidence = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjLivingCounter(:,2:31);
obj.DistanceX = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjDistanceX(:,2:31);
obj.DistanceY = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjDistanceY(:,2:31);
obj.RelativeVx = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjRelativeVelocityX(:,2:31);
obj.RelativeVy = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjRelativeVelocityY(:,2:31);
obj.Length = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjBoxSizeX(:,2:31);
obj.Width = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjBoxSizeY(:,2:31);
obj.TrackStatus = S.VH_2_IDT_Sf_SenFusMovableOBS_struct.Sf_SENObjTrackStatus(:,2:31);
end

function map = load_map_signals(S)
map.Road_type = S.VH_1_IDT_Sf_MapLocSrv_KpRoad_struct.Sf_EHRKpRoadCurrentType(:,2);
map.Road_Curve = S.VH_1_IDT_Sf_MapLocSrv_RoadParameter_struct.Sf_EHRRoadParameterCurrentCurve(:,2);
map.Road_Slope = S.VH_1_IDT_Sf_MapLocSrv_RoadParameter_struct.Sf_EHRRoadParameterCurrentSlopex(:,2);

map.Lane_type_CurrentLane = S.VH_1_IDT_Sf_MapLocSrv_LaneType_struct.Sf_EHRLaneTypeCurrentType(:,2);
map.Lane_type_LeftLane = S.VH_1_IDT_Sf_MapLocSrv_LaneType_struct.Sf_EHRLaneTypeLeftType(:,2);
map.Lane_type_RightLane = S.VH_1_IDT_Sf_MapLocSrv_LaneType_struct.Sf_EHRLaneTypeRightType(:,2);

[map.LaneMaxSpdlim, map.LaneNumSameDirection, map.EgoLaneIndexBase] = derive_s5_lane_speed_limit_info(S);

map.MAP_Q_Left1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineConfidences.left(:,2);
map.MAP_Q_Left2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineConfidences.left2(:,2);
map.MAP_Q_Right1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineConfidences.right(:,2);
map.MAP_Q_Right2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineConfidences.right2(:,2);

map.MAP_C0_Left1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left(:,2);
map.MAP_C0_Left2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left2(:,2);
map.MAP_C0_Right1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right(:,2);
map.MAP_C0_Right2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right2(:,2);

map.MAP_C1_Left1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left(:,3);
map.MAP_C1_Left2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left2(:,3);
map.MAP_C1_Right1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right(:,3);
map.MAP_C1_Right2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right2(:,3);

map.MAP_C2_Left1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left(:,4);
map.MAP_C2_Left2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left2(:,4);
map.MAP_C2_Right1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right(:,4);
map.MAP_C2_Right2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right2(:,4);

map.MAP_C3_Left1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left(:,5);
map.MAP_C3_Left2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.left2(:,5);
map.MAP_C3_Right1 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right(:,5);
map.MAP_C3_Right2 = S.VH_1_IDT_Sf_MapLocSrv_Line_struct.MAP_LineFlineParas.right2(:,5);

map.MAP_Type_Left1 = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeLeft1CurrentLinearType(:,2);
map.MAP_Type_Left2 = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeLeft2CurrentLinearType(:,2);
map.MAP_Type_Right1 = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeRight1CurrentLinearType(:,2);
map.MAP_Type_Right2 = S.VH_1_IDT_Sf_MapLocSrv_LineType_struct.Sf_EHRLineTypeRight2CurrentLinearType(:,2);

fp = load_fp_lane_signals(S);
map = replace_map_lines_with_fp(map, fp);
end

function fp = load_fp_lane_signals(S)
fp.MAP_Q_Left1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANQuality_Cnt_enum(:,6);
fp.MAP_Q_Left2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANQuality_Cnt_enum(:,8);
fp.MAP_Q_Right1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANQuality_Cnt_enum(:,7);
fp.MAP_Q_Right2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANQuality_Cnt_enum(:,9);

fp.MAP_C0_Left1 = laneline0_memory(S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC0_m_float32(:,6));
fp.MAP_C0_Left2 = laneline0_memory(S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC0_m_float32(:,8));
fp.MAP_C0_Right1 = laneline0_memory(S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC0_m_float32(:,7));
fp.MAP_C0_Right2 = laneline0_memory(S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC0_m_float32(:,9));

fp.MAP_C1_Left1 = laneline0_memory(S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC1_rad_float32(:,6));
fp.MAP_C1_Left2 = laneline0_memory(S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC1_rad_float32(:,8));
fp.MAP_C1_Right1 = laneline0_memory(S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC1_rad_float32(:,7));
fp.MAP_C1_Right2 = laneline0_memory(S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC1_rad_float32(:,9));

fp.MAP_C2_Left1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC2_1m_float32(:,6);
fp.MAP_C2_Left2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC2_1m_float32(:,8);
fp.MAP_C2_Right1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC2_1m_float32(:,7);
fp.MAP_C2_Right2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC2_1m_float32(:,9);

fp.MAP_C3_Left1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC3_1m2_float32(:,6);
fp.MAP_C3_Left2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC3_1m2_float32(:,8);
fp.MAP_C3_Right1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC3_1m2_float32(:,7);
fp.MAP_C3_Right2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANC3_1m2_float32(:,9);

fp.MAP_Type_Left1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANType_Cnt_enum(:,6);
fp.MAP_Type_Left2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANType_Cnt_enum(:,8);
fp.MAP_Type_Right1 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANType_Cnt_enum(:,7);
fp.MAP_Type_Right2 = S.VH_2_IDT_Sf_SenFusLAN_struct1.Sf_SENLANType_Cnt_enum(:,9);
end

function map = replace_map_lines_with_fp(map, fp)
use_fp_lane = (map.Road_type > -1);   % 对有效道路帧，统一使用感知车道线
line_names = {'Left1','Left2','Right1','Right2'};
fields = {'Q','C0','C1','C2','C3','Type'};
for i = 1:numel(line_names)
    for j = 1:numel(fields)
        field_name = ['MAP_', fields{j}, '_', line_names{i}];
        if isfield(map, field_name) && isfield(fp, field_name)
            map_value = map.(field_name);
            fp_value = fp.(field_name);
            map_value(use_fp_lane) = fp_value(use_fp_lane);
            map.(field_name) = map_value;
        end
    end
end
end

function T = build_ego_table(event_time, ego, idx)
T = table( ...
    event_time, ego.Longitude(idx), ego.Latitude(idx), ego.Azimuth(idx), ego.Velocity(idx), ...
    'VariableNames', {'event_time','Ego_GNSS_Longitude','Ego_GNSS_Latitude', ...
    'Ego_GNSS_Azimuth','Ego_velocity'} ...
    );
end

function T = build_object_table(event_time, obj, idx)
ObjMat = [ ...
    obj.Class(idx, :), obj.Flagstate(idx, :), obj.Speed(idx, :), ...
    obj.RelativeLane(idx, :), obj.Confidence(idx, :), ...
    obj.DistanceX(idx, :), obj.DistanceY(idx, :), ...
    obj.RelativeVx(idx, :), obj.RelativeVy(idx, :), ...
    obj.Length(idx, :), obj.Width(idx, :), obj.TrackStatus(idx, :) ...
    ];

numObj = size(obj.Class, 2);
objTags = arrayfun(@(k) sprintf('Obj%02d', k), 1:numObj, 'UniformOutput', false);
makeNames = @(suffix) strcat(objTags, "_", suffix);

varNames = [ ...
    makeNames("Class"), makeNames("Flagstate"), makeNames("Speed"), ...
    makeNames("RelativeLane"), makeNames("Confidence"), ...
    makeNames("DistanceX"), makeNames("DistanceY"), ...
    makeNames("RelativeVx"), makeNames("RelativeVy"), ...
    makeNames("Length"), makeNames("Width"), makeNames("TrackStatus") ...
    ];

T = array2table(ObjMat, 'VariableNames', varNames);
T = addvars(T, event_time, 'Before', 1, 'NewVariableNames', 'event_time');
end

function T = build_map_table(event_time, map, idx)
lane_max_spdlim = map.LaneMaxSpdlim(idx, :);
lane_min_spdlim = zeros(size(lane_max_spdlim));
ego_lane_index = refine_s5_ego_lane_index( ...
    map.EgoLaneIndexBase(idx), map.MAP_C0_Left1(idx), map.MAP_C0_Right1(idx));
T = table( ...
    event_time, map.Road_type(idx), map.Road_Curve(idx), map.Road_Slope(idx), ...
    map.Lane_type_CurrentLane(idx), map.Lane_type_LeftLane(idx), map.Lane_type_RightLane(idx), ...
    lane_max_spdlim(:,1), lane_max_spdlim(:,2), lane_max_spdlim(:,3), lane_max_spdlim(:,4), lane_max_spdlim(:,5), ...
    lane_min_spdlim(:,1), lane_min_spdlim(:,2), lane_min_spdlim(:,3), lane_min_spdlim(:,4), lane_min_spdlim(:,5), ...
    map.LaneNumSameDirection(idx), ego_lane_index, ...
    map.MAP_Q_Left1(idx), map.MAP_Q_Left2(idx), map.MAP_Q_Right1(idx), map.MAP_Q_Right2(idx), ...
    map.MAP_C0_Left1(idx), map.MAP_C0_Left2(idx), map.MAP_C0_Right1(idx), map.MAP_C0_Right2(idx), ...
    map.MAP_C1_Left1(idx), map.MAP_C1_Left2(idx), map.MAP_C1_Right1(idx), map.MAP_C1_Right2(idx), ...
    map.MAP_C2_Left1(idx), map.MAP_C2_Left2(idx), map.MAP_C2_Right1(idx), map.MAP_C2_Right2(idx), ...
    map.MAP_C3_Left1(idx), map.MAP_C3_Left2(idx), map.MAP_C3_Right1(idx), map.MAP_C3_Right2(idx), ...
    map.MAP_Type_Left1(idx), map.MAP_Type_Left2(idx), map.MAP_Type_Right1(idx), map.MAP_Type_Right2(idx), ...
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
end
