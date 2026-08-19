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

% ========= 2) Find monitor_result.mat and corresponding Overtake_events.csv =========
date_dirs = dir(main_folder);
date_dirs = date_dirs([date_dirs.isdir]);
date_dirs = date_dirs(~ismember({date_dirs.name},{'.','..'}));
target_dates = parse_target_dates();

allMatPaths = {};
meta = struct('date_name',{}, 'segment_name',{}, 'monitor_result_path',{}, 'event_path',{}, 'out_dir',{});

for d = 1:numel(date_dirs)
    date_name = date_dirs(d).name;
    date_path = fullfile(date_dirs(d).folder, date_name);

    if length(date_name) ~= 8 || any(~isstrprop(date_name,'digit'))
        continue;
    end
    if ~isempty(target_dates) && ~ismember(string(date_name), target_dates)
        continue;
    end

    % if ~strcmp(date_name, '20241025')
    %     continue
    % end

    monitor_result_root = fullfile(date_path, 'sim_result');
    if ~exist(monitor_result_root, 'dir')
        continue;
    end

    event_record_root = fullfile(date_path, 'zEvent_Overtake');
    if ~exist(event_record_root, 'dir')
        continue;
    end

    seg_dirs = dir(monitor_result_root);
    seg_dirs = seg_dirs([seg_dirs.isdir]);
    seg_dirs = seg_dirs(~ismember({seg_dirs.name},{'.','..'}));

    for s = 1:numel(seg_dirs)
        segment_name = seg_dirs(s).name;
        monitor_result_path = fullfile(seg_dirs(s).folder, segment_name, 'monitor_result.mat');
        if ~exist(monitor_result_path, 'file')
            continue;
        end

        event_path = fullfile(event_record_root, segment_name, 'Overtake_events.csv');
        if ~exist(event_path, 'file')
            continue;
        end

        out_dir = fullfile(event_record_root, segment_name);
        if ~exist(out_dir, 'dir')
            mkdir(out_dir);
        end

        allMatPaths{end+1,1} = monitor_result_path; %#ok<AGROW>
        meta(end+1).date_name = date_name; %#ok<AGROW>
        meta(end).segment_name = segment_name;
        meta(end).monitor_result_path = monitor_result_path;
        meta(end).event_path = event_path;
        meta(end).out_dir = out_dir;
    end
end

numMats = numel(allMatPaths);
fprintf('Found %d matched monitor_result.mat/Overtake_events.csv files.\n', numMats);
if numMats == 0
    error('No matched monitor_result.mat/Overtake_events.csv files found. Expected YYYYMMDD\\sim_result\\<segment>\\monitor_result.mat and YYYYMMDD\\zEvent_Overtake\\<segment>\\Overtake_events.csv.');
end

% ========= 3) Build evidence chain for each overtake event =========
for i = 1:numMats
    monitor_result_path = meta(i).monitor_result_path;
    event_path = meta(i).event_path;
    out_dir = meta(i).out_dir;
    fprintf('[%d/%d] Preparing: %s\n', i, numMats, event_path);

    S_monitor = load(monitor_result_path);
    seg_data = S_monitor.sim_output;

    seg_trigger_overtake_raw = get_required_signal(seg_data, 'T_Overtake');
    seg_com_IMR_47_4_raw = get_required_signal(seg_data, 'Com_IMR_47_4');
    seg_com_IMR_82_5_raw = get_required_signal(seg_data, 'Com_IMR_82_5');
    seg_com_TSL_43_6_raw = get_required_signal(seg_data, 'Com_TSL_43_6');
    seg_com_TSL_43_8_raw = get_required_signal(seg_data, 'Com_TSL_43_8');
    seg_Ego_velocity = get_required_signal(seg_data, 'INS_VelocitySpeed') / 3.6;
    seg_Congestion = get_required_signal(seg_data, 'Congestion');
    seg_overlap_LeftLine = get_required_signal(seg_data, 'Left_line_intersect');
    seg_overlap_RightLine = get_required_signal(seg_data, 'Right_line_intersect');

    event_opts = detectImportOptions(event_path, 'TextType', 'string');
    event_data = readtable(event_path, event_opts);

    num_event = size(event_data, 1);
    for j = 1:num_event
        event_start_idx = event_data.start_idx(j);
        event_end_idx = event_data.end_idx(j);
        idx = event_start_idx:event_end_idx;

        map_path = fullfile(out_dir, ['Overtake_event_', num2str(j), '_MapInfo.csv']);
        obj_path = fullfile(out_dir, ['Overtake_event_', num2str(j), '_ObjInfo.csv']);
        if ~exist(map_path, 'file') || ~exist(obj_path, 'file')
            warning('Skipping event %d because S5 input files are missing. Expected:\n  %s\n  %s', ...
                j, map_path, obj_path);
            continue;
        end

        map_opts = detectImportOptions(map_path, 'TextType', 'string');
        obj_opts = detectImportOptions(obj_path, 'TextType', 'string');
        map_info = readtable(map_path, map_opts);
        obj_info = readtable(obj_path, obj_opts);

        n_event = numel(idx);
        if height(map_info) ~= n_event || height(obj_info) ~= n_event
            warning('Skipping event %d because S5 input row count mismatch: event len=%d, MapInfo=%d, ObjInfo=%d', ...
                j, n_event, height(map_info), height(obj_info));
            continue;
        end
        event_time = read_event_time_from_s5(map_info, n_event);

        event_trigger_overtake_raw = seg_trigger_overtake_raw(idx);
        event_com_IMR_47_4_raw = seg_com_IMR_47_4_raw(idx);
        event_com_IMR_82_5_raw = seg_com_IMR_82_5_raw(idx);
        event_com_TSL_43_6_raw = seg_com_TSL_43_6_raw(idx);
        event_com_TSL_43_8_raw = seg_com_TSL_43_8_raw(idx);
        event_Ego_velocity = seg_Ego_velocity(idx);
        event_Congestion = seg_Congestion(idx);
        raw_overlap_LeftLine = seg_overlap_LeftLine(idx);
        raw_overlap_RightLine = seg_overlap_RightLine(idx);

        Road_type = map_info.Road_type;
        Lane_type = map_info.Lane_type_CurrentLane;

        MAP_C0_Left1 = map_info.MAP_C0_Left1;
        MAP_C1_Left1 = map_info.MAP_C1_Left1;
        MAP_C2_Left1 = map_info.MAP_C2_Left1;
        MAP_C3_Left1 = map_info.MAP_C3_Left1;
        MAP_C0_Right1 = map_info.MAP_C0_Right1;
        MAP_C1_Right1 = map_info.MAP_C1_Right1;
        MAP_C2_Right1 = map_info.MAP_C2_Right1;
        MAP_C3_Right1 = map_info.MAP_C3_Right1;

        LC1_s_value = get_table_scalar(event_data, j, 'LC1_s', -1);
        LC1_c_value = get_table_scalar(event_data, j, 'LC1_c', -1);
        LC1_e_value = get_table_scalar(event_data, j, 'LC1_e', -1);
        LC1_dir_value = get_table_scalar(event_data, j, 'LC1_dir', 0);
        LC2_s_value = get_table_scalar(event_data, j, 'LC2_s', -1);
        LC2_c_value = get_table_scalar(event_data, j, 'LC2_c', -1);
        LC2_e_value = get_table_scalar(event_data, j, 'LC2_e', -1);
        LC2_dir_value = get_table_scalar(event_data, j, 'LC2_dir', 0);
        cross_gap_value = get_table_scalar(event_data, j, 'cross_gap', -1);
        overtake_direction_value = get_table_scalar(event_data, j, 'OvertakeDirection', 0);
        thres_time_gap_value = get_table_scalar(event_data, j, 'Thres_Time_OvertakeGap', 20);

        overlap_lag_frames = infer_s5_lag(raw_overlap_LeftLine, raw_overlap_RightLine, map_info);
        event_overlap_LeftLine = double(shift_signal_to_s5(raw_overlap_LeftLine, overlap_lag_frames) ~= 0);
        event_overlap_RightLine = double(shift_signal_to_s5(raw_overlap_RightLine, overlap_lag_frames) ~= 0);

        anchor_idx = time_to_row_index(LC1_s_value, event_time);
        [FV_ObjID, FV_DistanceX, FV_DistanceY, FV_vx, FV_velocity, FV_MatchScore] = ...
            track_overtaken_vehicle(obj_info, map_info, anchor_idx);

        Dis_FV = sqrt(FV_DistanceX.^2 + FV_DistanceY.^2);
        Dis_FV(FV_ObjID == -1) = -1;

        FV_isBehind = (FV_ObjID ~= -1) & (FV_DistanceX < 0);
        FV_isLost = (FV_ObjID == -1);
        FV_everBehind = cumsum(FV_isBehind) > 0;
        FV_LostAfterBehind = FV_isLost & FV_everBehind;
        FV_PassedOrLostAfterBehind = FV_isBehind | FV_LostAfterBehind;

        [event_last_cross_time, event_last_cross_dir, event_current_cross_dir] = ...
            build_cross_context(event_time, event_overlap_LeftLine, event_overlap_RightLine, ...
            LC1_s_value, LC1_c_value, LC1_e_value, LC1_dir_value, ...
            LC2_s_value, LC2_c_value, LC2_e_value, LC2_dir_value);

        Is_SecondLCPhase = make_range_mask(event_time, LC2_s_value, LC2_e_value);
        Is_CrossGapValid = (cross_gap_value >= 0) & (cross_gap_value < thres_time_gap_value);
        Is_OppositeDirection = (LC1_dir_value * LC2_dir_value) == -1;
        Is_RampOrAccelDecelLane = is_ramp_or_accdec(Road_type, Lane_type);
        Is_Tunnel = (Road_type == 34);
        Is_Congestion = event_Congestion ~= 0;

        trigger_overtake = double(Is_SecondLCPhase & Is_CrossGapValid & ...
            Is_OppositeDirection & FV_PassedOrLostAfterBehind);
        active_mask = trigger_overtake ~= 0;

        com_IMR_47_4 = zeros(n_event, 1);
        if overtake_direction_value == 1
            com_IMR_47_4(active_mask) = 1;
        elseif overtake_direction_value == 2
            com_IMR_47_4(active_mask) = -1;
        end

        com_IMR_82_5 = zeros(n_event, 1);
        com_IMR_82_5(active_mask & ~Is_RampOrAccelDecelLane) = 1;
        com_IMR_82_5(active_mask & Is_RampOrAccelDecelLane) = -1;

        com_TSL_43_6 = zeros(n_event, 1);
        com_TSL_43_6(active_mask & ~Is_Tunnel) = 1;
        com_TSL_43_6(active_mask & Is_Tunnel) = -1;

        com_TSL_43_8 = zeros(n_event, 1);
        com_TSL_43_8(active_mask & ~Is_Congestion) = 1;
        com_TSL_43_8(active_mask & Is_Congestion) = -1;

        OvertakeDirection = zeros(n_event, 1);
        OvertakeDirection(active_mask) = overtake_direction_value;
        Is_Congestion = double(Is_Congestion);

        T = table( ...
            event_time, trigger_overtake, ...
            com_IMR_47_4, com_IMR_82_5, com_TSL_43_6, com_TSL_43_8, ...
            event_Ego_velocity, FV_velocity, FV_DistanceX, FV_DistanceY, FV_ObjID, ...
            OvertakeDirection, Road_type, Lane_type, Is_Congestion, ...
            event_overlap_LeftLine, event_overlap_RightLine, event_last_cross_time, ...
            event_last_cross_dir, event_current_cross_dir, ...
            'VariableNames', {'event_time','trigger_overtake', ...
            'com_IMR_47_4','com_IMR_82_5','com_TSL_43_6','com_TSL_43_8', ...
            'Ego_velocity','FV_velocity','FV_DistanceX','FV_DistanceY','FV_ObjID', ...
            'OvertakeDirection','Road_type','Lane_type','Congestion', ...
            'overlap_LeftLine','overlap_RightLine','last_cross_time', ...
            'last_cross_dir','current_cross_dir'} ...
            );

        event_name = ['Overtake_event_', num2str(j), '_EvidenceChain.csv'];
        csv_path = fullfile(out_dir, event_name);
        write_event_table(T, csv_path);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, event_name);
    end
end

function signal = get_required_signal(seg_data, field_name)
if isa(seg_data, 'Simulink.SimulationOutput')
    try
        signal = get(seg_data, field_name);
    catch
        available_names = "";
        try
            available_names = strjoin(string(who(seg_data)), ', ');
        catch
        end
        error('Missing required SimulationOutput variable: %s. Available variables: %s', ...
            field_name, available_names);
    end
elseif istable(seg_data) || istimetable(seg_data)
    if ~ismember(field_name, seg_data.Properties.VariableNames)
        error('Missing required seg_data variable: %s', field_name);
    end
    signal = seg_data.(field_name);
elseif isstruct(seg_data)
    if ~isfield(seg_data, field_name)
        error('Missing required seg_data field: %s', field_name);
    end
    signal = seg_data.(field_name);
elseif isobject(seg_data)
    try
        signal = seg_data.(field_name);
    catch
        try
            signal = get(seg_data, field_name);
        catch
            error('Missing required seg_data property/variable: %s', field_name);
        end
    end
else
    try
        signal = seg_data.(field_name);
    catch
        error('Missing required seg_data field/variable/property: %s', field_name);
    end
end
if istable(signal)
    signal = table2array(signal);
end
signal = signal(:);
end

function target_dates = parse_target_dates()
target_text = strtrim(string(getenv('TLCD_TARGET_DATES')));
if strlength(target_text) == 0
    target_dates = strings(0, 1);
else
    target_dates = strtrim(split(target_text, ','));
    target_dates = target_dates(strlength(target_dates) > 0);
end
end

function value = get_table_scalar(tbl, row_idx, var_name, default_value)
if ismember(var_name, tbl.Properties.VariableNames)
    value = tbl.(var_name)(row_idx);
else
    value = default_value;
end
value = double(value);
end

function event_time = read_event_time_from_s5(map_info, n_event)
if ismember('event_time', map_info.Properties.VariableNames) && height(map_info) == n_event
    event_time = map_info.event_time;
    event_time = event_time(:);
else
    event_time = 0.01*(1:n_event)';
end
end

function lag_frames = infer_s5_lag(overlap_left, overlap_right, map_info)
map_switch = find_map_switch_indices(map_info);
overlap_switch = find_overlap_switch_indices(overlap_left, overlap_right);
lags = [];
for k = 1:numel(map_switch)
    [dist, pos] = min(abs(overlap_switch - map_switch(k)));
    if ~isempty(dist) && dist <= 5
        lags(end+1, 1) = overlap_switch(pos) - map_switch(k); %#ok<AGROW>
    end
end
if isempty(lags)
    lag_frames = 0;
else
    lag_frames = round(median(lags));
end
end

function switch_idx = find_map_switch_indices(map_info)
switch_idx = [];
if ~all(ismember({'MAP_C0_Left1','MAP_C0_Right1'}, map_info.Properties.VariableNames))
    return;
end
left_c0 = map_info.MAP_C0_Left1(:);
right_c0 = map_info.MAP_C0_Right1(:);
delta = abs(diff(left_c0)) + abs(diff(right_c0));
threshold = max(1, 0.5 * max(delta));
switch_idx = find(delta >= threshold) + 1;
end

function switch_idx = find_overlap_switch_indices(overlap_left, overlap_right)
overlap_left = overlap_left(:) ~= 0;
overlap_right = overlap_right(:) ~= 0;
switch_idx = find(diff(double(overlap_left)) ~= 0 | diff(double(overlap_right)) ~= 0) + 1;
end

function aligned = shift_signal_to_s5(signal, lag_frames)
signal = signal(:);
n = numel(signal);
aligned = signal;
lag_frames = round(lag_frames);
if lag_frames > 0
    aligned(1:n-lag_frames) = signal(1+lag_frames:n);
    aligned(n-lag_frames+1:n) = signal(n);
elseif lag_frames < 0
    lag = -lag_frames;
    aligned(1:lag) = signal(1);
    aligned(lag+1:n) = signal(1:n-lag);
end
end

function row_idx = time_to_row_index(time_s, event_time)
if ~isfinite(time_s) || time_s < 0
    row_idx = 1;
    return;
end
[~, row_idx] = min(abs(event_time(:) - double(time_s)));
row_idx = max(1, min(numel(event_time), row_idx));
end

function mask = make_range_mask(event_time, start_time, end_time)
mask = false(numel(event_time), 1);
if ~isfinite(start_time) || ~isfinite(end_time) || start_time < 0 || end_time < 0
    return;
end
start_idx = time_to_row_index(start_time, event_time);
end_idx = time_to_row_index(end_time, event_time);
if end_idx < start_idx
    return;
end
mask(start_idx:end_idx) = true;
end

function [last_cross_time, last_cross_dir, current_cross_dir] = build_cross_context( ...
    n_event_time, overlap_left, overlap_right, LC1_s, LC1_c, LC1_e, LC1_dir, LC2_s, LC2_c, LC2_e, LC2_dir)
n_rows = numel(n_event_time);
last_cross_time = -1 * ones(n_rows, 1);
last_cross_dir = zeros(n_rows, 1);
current_cross_dir = zeros(n_rows, 1);

LC1_s_idx = time_to_row_index(LC1_s, n_event_time);
LC1_e_idx = time_to_row_index(LC1_e, n_event_time);
LC2_s_idx = time_to_row_index(LC2_s, n_event_time);
LC2_e_idx = time_to_row_index(LC2_e, n_event_time);

if LC1_dir ~= 0
    current_cross_dir(LC1_s_idx:LC1_e_idx) = LC1_dir;
    LC1_c_idx = time_to_row_index(LC1_c, n_event_time);
    last_cross_time(LC1_c_idx:end) = LC1_c;
    last_cross_dir(LC1_c_idx:end) = LC1_dir;
end

if LC2_dir ~= 0
    current_cross_dir(LC2_s_idx:LC2_e_idx) = LC2_dir;
    LC2_c_idx = time_to_row_index(LC2_c, n_event_time);
    last_cross_time(LC2_c_idx:end) = LC2_c;
    last_cross_dir(LC2_c_idx:end) = LC2_dir;
end
end

function is_target = is_ramp_or_accdec(road_type, lane_type)
ramp_road_types = [6, 7, 31, 32];
accdec_lane_types = [2, 3];
is_target = ismember(road_type, ramp_road_types) | ismember(lane_type, accdec_lane_types);
end

function [obj_id, dis_x, dis_y, rel_vx, speed, score] = track_overtaken_vehicle(obj_info, map_info, anchor_idx)
n_rows = height(obj_info);
obj_id = -1 * ones(n_rows, 1);
dis_x = -1 * ones(n_rows, 1);
dis_y = -1 * ones(n_rows, 1);
rel_vx = -1 * ones(n_rows, 1);
speed = -1 * ones(n_rows, 1);
score = -1 * ones(n_rows, 1);

[target_id, target_state] = find_initial_front_vehicle(obj_info, map_info, anchor_idx);
if target_id < 0
    return;
end

last_state = target_state;
miss_count = 0;
max_gap = 20;

for r = anchor_idx:n_rows
    [cur_id, cur_state, cur_score] = match_vehicle_at_row(obj_info, r, last_state);

    if cur_id < 0
        miss_count = miss_count + 1;
        if miss_count > max_gap
            last_state = [NaN, NaN, NaN, NaN];
        end
        continue;
    end

    miss_count = 0;
    obj_id(r) = cur_id;
    dis_x(r) = cur_state(1);
    dis_y(r) = cur_state(2);
    rel_vx(r) = cur_state(3);
    speed(r) = cur_state(4);
    score(r) = cur_score;

    last_state = cur_state;
end
end

function [target_id, target_state] = find_initial_front_vehicle(obj_info, map_info, row_idx)
[ids, states, classes] = read_object_states(obj_info, row_idx);
valid = is_valid_vehicle(states, classes);
valid = valid & states(:,1) > 0;

if ~any(valid)
    target_id = -1;
    target_state = [NaN, NaN, NaN, NaN];
    return;
end

lane_mask = is_same_lane(map_info, row_idx, states(:,1), states(:,2));
candidate_mask = valid & lane_mask;

if ~any(candidate_mask)
    % Fallback: if lane lines are unavailable, use the nearest valid front object.
    candidate_mask = valid;
end

candidate_ids = ids(candidate_mask);
candidate_states = states(candidate_mask, :);
[~, min_idx] = min(candidate_states(:,1));
target_id = candidate_ids(min_idx);
target_state = candidate_states(min_idx, :);
end

function [cur_id, cur_state, cur_score] = match_vehicle_at_row(obj_info, row_idx, last_state)
[ids, states, classes] = read_object_states(obj_info, row_idx);
valid = is_valid_vehicle(states, classes);

if ~any(valid) || any(~isfinite(last_state(1:3)))
    cur_id = -1;
    cur_state = [NaN, NaN, NaN, NaN];
    cur_score = -1;
    return;
end

ids = ids(valid);
states = states(valid, :);

dx = abs(states(:,1) - last_state(1));
dy = abs(states(:,2) - last_state(2));
dvx = abs(states(:,3) - last_state(3));

cost = sqrt((dx / 8).^2 + (dy / 2.5).^2 + (dvx / 5).^2);
hard_gate = (dx <= 12) & (dy <= 4) & (dvx <= 8);
cost(~hard_gate) = Inf;

[best_cost, best_idx] = min(cost);
if ~isfinite(best_cost) || best_cost > 2.5
    cur_id = -1;
    cur_state = [NaN, NaN, NaN, NaN];
    cur_score = -1;
    return;
end

cur_id = ids(best_idx);
cur_state = states(best_idx, :);
cur_score = best_cost;
end

function valid = is_valid_vehicle(states, classes)
valid_class = classes > 0;
valid_state = all(isfinite(states(:,1:3)), 2) & abs(states(:,1)) < 250 & abs(states(:,2)) < 30;
valid = valid_class & valid_state;
end

function same_lane = is_same_lane(map_info, row_idx, x, y)
left_coef = get_lane_coef(map_info, row_idx, 'Left1');
right_coef = get_lane_coef(map_info, row_idx, 'Right1');

left_y = eval_lane(left_coef, x);
right_y = eval_lane(right_coef, x);
has_lane = isfinite(left_y) & isfinite(right_y) & abs(left_y - right_y) > 1;

lower_y = min(left_y, right_y) - 0.6;
upper_y = max(left_y, right_y) + 0.6;
same_lane = has_lane & (y >= lower_y) & (y <= upper_y);
end

function coef = get_lane_coef(map_info, row_idx, side)
names = {'C0','C1','C2','C3'};
coef = NaN(1, 4);
for k = 1:4
    var_name = ['MAP_', names{k}, '_', side];
    if ismember(var_name, map_info.Properties.VariableNames)
        coef(k) = map_info.(var_name)(row_idx);
    end
end
if all(coef == 0)
    coef(:) = NaN;
end
end

function y = eval_lane(coef, x)
if any(~isfinite(coef))
    y = NaN(size(x));
    return;
end
y = coef(1) + coef(2).*x + coef(3).*x.^2 + coef(4).*x.^3;
end

function [ids, states, classes] = read_object_states(obj_info, row_idx)
num_obj = 30;
ids = (1:num_obj)';
classes = NaN(num_obj, 1);
speed = NaN(num_obj, 1);
distance_x = NaN(num_obj, 1);
distance_y = NaN(num_obj, 1);
relative_vx = NaN(num_obj, 1);

for k = 1:num_obj
    tag = sprintf('Obj%02d', k);
    classes(k) = read_scalar(obj_info, row_idx, [tag, '_Class']);
    speed(k) = read_scalar(obj_info, row_idx, [tag, '_Speed']);
    distance_x(k) = read_scalar(obj_info, row_idx, [tag, '_DistanceX']);
    distance_y(k) = read_scalar(obj_info, row_idx, [tag, '_DistanceY']);
    relative_vx(k) = read_scalar(obj_info, row_idx, [tag, '_RelativeVx']);
end

states = [distance_x, distance_y, relative_vx, speed];
end

function value = read_scalar(tbl, row_idx, var_name)
if ismember(var_name, tbl.Properties.VariableNames)
    value = tbl.(var_name)(row_idx);
else
    value = NaN;
end
end
