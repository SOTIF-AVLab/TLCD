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

% ========= 2) Find monitor_result.mat and MinSpdlim_events.csv =========
date_dirs = dir(main_folder);
date_dirs = date_dirs([date_dirs.isdir]);
date_dirs = date_dirs(~ismember({date_dirs.name}, {'.','..'}));

monitor_paths = {};
event_paths = {};
out_dirs = {};

for d = 1:numel(date_dirs)
    date_name = date_dirs(d).name;
    date_path = fullfile(date_dirs(d).folder, date_name);

    if length(date_name) ~= 8 || any(~isstrprop(date_name, 'digit'))
        continue;
    end

    monitor_root = fullfile(date_path, 'sim_result');
    event_root = fullfile(date_path, 'zEvent_MinSpdlim');
    if ~exist(monitor_root, 'dir') || ~exist(event_root, 'dir')
        continue;
    end

    event_dirs = dir(event_root);
    event_dirs = event_dirs([event_dirs.isdir]);
    event_dirs = event_dirs(~ismember({event_dirs.name}, {'.','..'}));

    for s = 1:numel(event_dirs)
        segment_name = event_dirs(s).name;
        event_path = fullfile(event_dirs(s).folder, segment_name, 'MinSpdlim_events.csv');
        monitor_path = fullfile(monitor_root, segment_name, 'monitor_result.mat');

        if ~exist(event_path, 'file') || ~exist(monitor_path, 'file')
            continue;
        end

        monitor_paths{end+1, 1} = monitor_path; %#ok<AGROW>
        event_paths{end+1, 1} = event_path; %#ok<AGROW>
        out_dirs{end+1, 1} = fullfile(event_root, segment_name); %#ok<AGROW>
    end
end

numMats = numel(monitor_paths);
fprintf('Found %d matched monitor_result.mat/MinSpdlim_events.csv files.\n', numMats);
if numMats == 0
    error('No matched minimum speed-limit events found. Expected YYYYMMDD\\sim_result\\<segment>\\monitor_result.mat and YYYYMMDD\\zEvent_MinSpdlim\\<segment>\\MinSpdlim_events.csv');
end

% ========= 3) Write per-event evidence chains =========
for i = 1:numMats
    monitor_path = monitor_paths{i};
    event_path = event_paths{i};
    out_dir = out_dirs{i};

    fprintf('[%d/%d] Preparing: %s\n', i, numMats, event_path);

    S = load(monitor_path);
    seg_data = S.sim_output;

    opts = detectImportOptions(event_path, 'TextType', 'string');
    string_vars = intersect({'violated_article', 'extract_type'}, opts.VariableNames, 'stable');
    if ~isempty(string_vars)
        opts = setvartype(opts, string_vars, 'string');
    end
    event_data = readtable(event_path, opts);
    if isempty(event_data)
        continue;
    end

    road_type = get_signal(seg_data, 'MAP_RoadType_Curr');
    lane_type = get_signal(seg_data, 'LaneType');
    ego_speed_kph = get_signal(seg_data, 'INS_VelocitySpeed');
    congestion = get_signal(seg_data, 'Congestion');

    sign_speed_limit_max = compact_sign_speed_limit_max(get_sign_speed_limit_max(seg_data));
    lane_min_spdlim = calc_lane_min_spdlim_matrix(sign_speed_limit_max);
    is_exist_left_lane = get_signal(seg_data, 'IsExistLeftLane');
    is_exist_left2_lane = get_signal(seg_data, 'IsExistLeft2Lane');
    is_exist_right_lane = get_signal(seg_data, 'IsExistRightLane');
    is_exist_right2_lane = get_signal(seg_data, 'IsExistRight2Lane');

    n = min([numel(road_type), numel(lane_type), numel(ego_speed_kph), numel(congestion), ...
        size(sign_speed_limit_max, 1), size(lane_min_spdlim, 1), ...
        numel(is_exist_left_lane), numel(is_exist_left2_lane), ...
        numel(is_exist_right_lane), numel(is_exist_right2_lane)]);

    road_type = road_type(1:n);
    lane_type = lane_type(1:n);
    ego_speed_kph = ego_speed_kph(1:n);
    congestion = congestion(1:n);
    sign_speed_limit_max = sign_speed_limit_max(1:n, :);
    lane_min_spdlim = lane_min_spdlim(1:n, :);
    is_exist_left_lane = is_exist_left_lane(1:n);
    is_exist_left2_lane = is_exist_left2_lane(1:n);
    is_exist_right_lane = is_exist_right_lane(1:n);
    is_exist_right2_lane = is_exist_right2_lane(1:n);

    lane_num_approx = sum(sign_speed_limit_max > 60, 2);
    lane_pos_approx = derive_lane_pos(is_exist_left_lane, is_exist_left2_lane, ...
        is_exist_right_lane, is_exist_right2_lane);

    num_event = height(event_data);
    for j = 1:num_event
        event_start_idx = max(1, event_data.start_idx(j));
        event_end_idx = min(n, event_data.end_idx(j));
        if event_end_idx < event_start_idx
            continue;
        end

        idx = event_start_idx:event_end_idx;
        event_len = numel(idx);
        event_time = 0.01 * (1:event_len)';
        extract_type = string(event_data.extract_type(j));
        map_info = read_s5_map_info(out_dir, j, event_len);
        ego_info = read_s5_ego_info(out_dir, j, event_len);
        has_map_info = ~isempty(map_info);
        has_ego_info = ~isempty(ego_info);
        if has_map_info && ismember('event_time', map_info.Properties.VariableNames)
            event_time = table_column_to_double(map_info.event_time);
        end

        trigger_IMR_78_2 = zeros(event_len, 1);
        trigger_IMR_78_4 = zeros(event_len, 1);
        trigger_IMR_78_5 = zeros(event_len, 1);
        trigger_IMR_78_6 = zeros(event_len, 1);
        trigger_IMR_78_7 = zeros(event_len, 1);

        com_IMR_78_2 = zeros(event_len, 1);
        com_IMR_78_4 = zeros(event_len, 1);
        com_IMR_78_5 = zeros(event_len, 1);
        com_IMR_78_6 = zeros(event_len, 1);
        com_IMR_78_7 = zeros(event_len, 1);

        Thres_MinSpdlim = -1 * ones(event_len, 1);

        event_Road_type = road_type(idx);
        event_Lane_type = lane_type(idx);
        event_Ego_velocity = ego_speed_kph(idx) / 3.6;
        event_LaneNumSameDirection = lane_num_approx(idx);
        event_LaneMinSpdlim = lane_min_spdlim(idx, :);
        if extract_type == "sign_1_to_0"
            event_LaneMinSpdlim(idx < event_data.key_idx_s(j), :) = 0;
        end
        event_LanePos = lane_pos_approx(idx);
        event_EgoLaneIndex = event_LanePos;
        event_Congestion = congestion(idx);

        if has_map_info
            event_Road_type = choose_map_column(map_info, 'Road_type', event_Road_type);
            event_Lane_type = choose_map_column(map_info, 'Lane_type_CurrentLane', event_Lane_type);
            event_LaneNumSameDirection = choose_map_column(map_info, 'LaneNumSameDirection', event_LaneNumSameDirection);
            event_EgoLaneIndex = choose_map_column(map_info, 'EgoLaneIndex', event_EgoLaneIndex);
            event_LaneMinSpdlim = choose_map_min_speed(map_info, event_LaneMinSpdlim);
        end
        if has_ego_info && ismember('Ego_INS_Velocity', ego_info.Properties.VariableNames)
            event_Ego_velocity = table_column_to_double(ego_info.Ego_INS_Velocity);
        end

        if extract_type == "sign_1_to_0"
            key_idx = event_data.key_idx_s(j);
            active_global_idx = max(event_start_idx, key_idx):event_end_idx;
            local_idx = active_global_idx - event_start_idx + 1;

            [trigger_tmp, com_tmp, thres_tmp] = calc_sign_78_4( ...
                active_global_idx, sign_speed_limit_max, is_exist_left_lane, is_exist_left2_lane, ...
                congestion, ego_speed_kph, map_info, event_start_idx);

            trigger_IMR_78_4(local_idx) = trigger_tmp;
            com_IMR_78_4(local_idx) = com_tmp;
            Thres_MinSpdlim(local_idx) = thres_tmp;
        else
            [trigger_IMR_78_2, trigger_IMR_78_5, trigger_IMR_78_6, trigger_IMR_78_7, ...
                com_IMR_78_2, com_IMR_78_5, com_IMR_78_6, com_IMR_78_7, Thres_MinSpdlim] = ...
                calc_lane_min_speed_local(event_LaneNumSameDirection, event_LanePos, ...
                event_Congestion, event_Ego_velocity * 3.6);
        end

        if extract_type == "sign_1_to_0" && has_map_info
            [trigger_IMR_78_4, com_IMR_78_4, Thres_MinSpdlim, event_EgoLaneIndex] = align_sign_78_4_to_map( ...
                trigger_IMR_78_4, event_LaneMinSpdlim, event_Ego_velocity, event_Congestion, ...
                event_start_idx, event_data.key_idx_s(j), is_exist_left_lane, is_exist_left2_lane, map_info);
        end
        event_IsMinSpdsignArea = trigger_IMR_78_4 ~= 0;

        event_time = ensure_event_column(event_time, event_len, 'event_time');
        trigger_IMR_78_2 = ensure_event_column(trigger_IMR_78_2, event_len, 'trigger_IMR_78_2');
        trigger_IMR_78_4 = ensure_event_column(trigger_IMR_78_4, event_len, 'trigger_IMR_78_4');
        trigger_IMR_78_5 = ensure_event_column(trigger_IMR_78_5, event_len, 'trigger_IMR_78_5');
        trigger_IMR_78_6 = ensure_event_column(trigger_IMR_78_6, event_len, 'trigger_IMR_78_6');
        trigger_IMR_78_7 = ensure_event_column(trigger_IMR_78_7, event_len, 'trigger_IMR_78_7');
        com_IMR_78_2 = ensure_event_column(com_IMR_78_2, event_len, 'com_IMR_78_2');
        com_IMR_78_4 = ensure_event_column(com_IMR_78_4, event_len, 'com_IMR_78_4');
        com_IMR_78_5 = ensure_event_column(com_IMR_78_5, event_len, 'com_IMR_78_5');
        com_IMR_78_6 = ensure_event_column(com_IMR_78_6, event_len, 'com_IMR_78_6');
        com_IMR_78_7 = ensure_event_column(com_IMR_78_7, event_len, 'com_IMR_78_7');
        event_Road_type = ensure_event_column(event_Road_type, event_len, 'Road_type');
        event_Lane_type = ensure_event_column(event_Lane_type, event_len, 'Lane_type');
        event_Ego_velocity = ensure_event_column(event_Ego_velocity, event_len, 'Ego_velocity');
        event_IsMinSpdsignArea = ensure_event_column(event_IsMinSpdsignArea, event_len, 'IsMinSpdsignArea');
        event_LaneNumSameDirection = ensure_event_column(event_LaneNumSameDirection, event_len, 'LaneNumSameDirection');
        event_LaneMinSpdlim = ensure_event_matrix(event_LaneMinSpdlim, event_len, 5, 'LaneMinSpdlim');
        event_EgoLaneIndex = ensure_event_column(event_EgoLaneIndex, event_len, 'EgoLaneIndex');
        event_Congestion = ensure_event_column(event_Congestion, event_len, 'Congestion');
        Thres_MinSpdlim = ensure_event_column(Thres_MinSpdlim, event_len, 'Thres_MinSpdlim');

        T = table( ...
            event_time, ...
            trigger_IMR_78_2, trigger_IMR_78_4, trigger_IMR_78_5, trigger_IMR_78_6, trigger_IMR_78_7, ...
            com_IMR_78_2, com_IMR_78_4, com_IMR_78_5, com_IMR_78_6, com_IMR_78_7, ...
            event_Road_type, event_Lane_type, event_Ego_velocity, event_IsMinSpdsignArea, ...
            event_LaneNumSameDirection, ...
            event_LaneMinSpdlim(:,1), event_LaneMinSpdlim(:,2), event_LaneMinSpdlim(:,3), event_LaneMinSpdlim(:,4), event_LaneMinSpdlim(:,5), ...
            event_EgoLaneIndex, event_Congestion, Thres_MinSpdlim, ...
            'VariableNames', {'event_time', ...
            'trigger_IMR_78_2','trigger_IMR_78_4','trigger_IMR_78_5','trigger_IMR_78_6','trigger_IMR_78_7', ...
            'com_IMR_78_2','com_IMR_78_4','com_IMR_78_5','com_IMR_78_6','com_IMR_78_7', ...
            'Road_type','Lane_type','Ego_velocity','IsMinSpdsignArea', ...
            'LaneNumSameDirection', ...
            'LaneMinSpdlim_1','LaneMinSpdlim_2','LaneMinSpdlim_3','LaneMinSpdlim_4','LaneMinSpdlim_5', ...
            'EgoLaneIndex','Congestion','Thres_MinSpdlim'} ...
            );

        event_name = ['MinSpdlim_event_', num2str(j), '_EvidenceChain.csv'];
        csv_path = fullfile(out_dir, event_name);
        writetable(T, csv_path);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, event_name);
    end
end

function map_info = read_s5_map_info(out_dir, event_num, event_len)
map_path = fullfile(out_dir, sprintf('MinSpdlim_event_%d_MapInfo.csv', event_num));
map_info = [];
if ~exist(map_path, 'file')
    return;
end
tmp = readtable(map_path);
if height(tmp) ~= event_len
    warning('S5 MapInfo row count mismatch for event %d. Use monitor_result fallback.', event_num);
    return;
end
map_info = tmp;
end

function ego_info = read_s5_ego_info(out_dir, event_num, event_len)
ego_path = fullfile(out_dir, sprintf('MinSpdlim_event_%d_EgoInfo.csv', event_num));
ego_info = [];
if ~exist(ego_path, 'file')
    return;
end
tmp = readtable(ego_path);
if height(tmp) ~= event_len
    warning('S5 EgoInfo row count mismatch for event %d. Use monitor_result fallback.', event_num);
    return;
end
ego_info = tmp;
end

function values = choose_map_column(map_info, var_name, fallback_values)
values = fallback_values;
if ismember(var_name, map_info.Properties.VariableNames)
    values = table_column_to_double(map_info.(var_name));
end
end

function lane_min = choose_map_min_speed(map_info, fallback_values)
lane_min = fallback_values;
var_names = "LaneMinSpdlim_" + string(1:5);
if all(ismember(var_names, map_info.Properties.VariableNames))
    lane_min = zeros(height(map_info), 5);
    for k = 1:5
        lane_min(:, k) = table_column_to_double(map_info.(var_names(k)));
    end
end
end

function col = table_column_to_double(col)
if isnumeric(col)
    col = double(col(:));
else
    col = str2double(string(col(:)));
end
end

function values = ensure_event_column(values, event_len, field_name)
values = values(:);
if numel(values) ~= event_len
    error('Field %s has %d rows, expected %d rows.', field_name, numel(values), event_len);
end
end

function values = ensure_event_matrix(values, event_len, expected_cols, field_name)
if size(values, 1) ~= event_len && size(values, 2) == event_len
    values = values.';
end
if size(values, 1) ~= event_len || size(values, 2) ~= expected_cols
    error('Field %s has size %s, expected [%d %d].', ...
        field_name, mat2str(size(values)), event_len, expected_cols);
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

function lane_pos = derive_lane_pos(is_exist_left_lane, is_exist_left2_lane, is_exist_right_lane, is_exist_right2_lane)
left_count = double(is_exist_left_lane == 1) + double(is_exist_left2_lane == 1);
right_count = double(is_exist_right_lane == 1) + double(is_exist_right2_lane == 1);

lane_pos = zeros(size(left_count));
lane_pos(left_count == 0) = 1;
lane_pos(left_count > 0 & right_count > 0) = 2;
lane_pos(left_count > 0 & right_count == 0) = 3;
end

function [trigger_78_4, com_78_4, thres_min_spdlim] = calc_sign_78_4( ...
    idx, sign_speed_limit_max, is_exist_left_lane, is_exist_left2_lane, ...
    congestion, ego_speed_kph, map_info, event_start_idx)

trigger_78_4 = zeros(numel(idx), 1);
com_78_4 = zeros(numel(idx), 1);
thres_min_spdlim = -1 * ones(numel(idx), 1);
ego_lane_index_series = double(is_exist_left2_lane(idx) == 1) + double(is_exist_left_lane(idx) == 1) + 1;
if ~isempty(map_info)
    local_idx = idx - event_start_idx + 1;
    ego_lane_index_series = refine_lane_index_with_map(ego_lane_index_series, map_info, local_idx);
end

for ii = 1:numel(idx)
    p = idx(ii);
    ego_lane_index = ego_lane_index_series(ii);
    if ego_lane_index < 1 || ego_lane_index > 5
        trigger_78_4(ii) = 1;
        continue;
    end

    max_speed = sign_speed_limit_max(p, ego_lane_index);
    min_speed = min_speed_from_sign_lane(ego_lane_index, max_speed);

    trigger_78_4(ii) = 1;
    if min_speed < 0
        continue;
    end

    thres_min_spdlim(ii) = min_speed;
    if congestion(p) ~= 1 && ego_speed_kph(p) < min_speed
        com_78_4(ii) = -1;
    else
        com_78_4(ii) = 1;
    end
end
end

function [trigger_78_4, com_78_4, thres_min_spdlim, ego_lane_index] = align_sign_78_4_to_map( ...
    trigger_78_4, lane_min_spdlim, ego_velocity, congestion, event_start_idx, key_idx, ...
    is_exist_left_lane, is_exist_left2_lane, map_info)

event_len = numel(trigger_78_4);
global_idx = event_start_idx:(event_start_idx + event_len - 1);
base_lane_index = double(is_exist_left2_lane(global_idx) == 1) + ...
    double(is_exist_left_lane(global_idx) == 1) + 1;
ego_lane_index = refine_lane_index_with_map(base_lane_index, map_info, 1:event_len);

active = global_idx(:) >= key_idx;
trigger_78_4(:) = 0;
trigger_78_4(active) = 1;
thres_min_spdlim = -1 * ones(event_len, 1);
com_78_4(:) = 0;

for i = find(active).'
    lane_idx = ego_lane_index(i);
    if lane_idx < 1 || lane_idx > size(lane_min_spdlim, 2)
        continue;
    end

    thres = lane_min_spdlim(i, lane_idx);
    if ~isfinite(thres) || thres < 0
        continue;
    end

    thres_min_spdlim(i) = thres;
    if thres == 0
        com_78_4(i) = 1;
    elseif congestion(i) ~= 1 && ego_velocity(i) * 3.6 < thres
        com_78_4(i) = -1;
    else
        com_78_4(i) = 1;
    end
end
end

function ego_lane_index = refine_lane_index_with_map(base_lane_index, map_info, local_idx)
ego_lane_index = base_lane_index(:);
if isempty(map_info) || ~all(ismember({'MAP_C0_Left1','MAP_C0_Right1'}, map_info.Properties.VariableNames))
    return;
end

left_c0 = table_column_to_double(map_info.MAP_C0_Left1);
right_c0 = table_column_to_double(map_info.MAP_C0_Right1);
offset = 0;
for i = 1:numel(ego_lane_index)
    loc = local_idx(i);
    if loc < 1 || loc > numel(left_c0)
        continue;
    end

    if i > 1 && base_lane_index(i) ~= base_lane_index(i - 1)
        offset = 0;
    end

    if i > 1 && offset == 0
        prev_loc = local_idx(i - 1);
        if prev_loc >= 1 && prev_loc <= numel(left_c0)
            left_change = abs(left_c0(prev_loc)) < 0.75 && abs(right_c0(loc)) < 0.75 && ...
                left_c0(loc) > 2 && ego_lane_index(i) > 1;
            right_change = abs(right_c0(prev_loc)) < 0.75 && abs(left_c0(loc)) < 0.75 && ...
                right_c0(loc) < -2 && ego_lane_index(i) < 5;
            if left_change
                offset = -1;
            elseif right_change
                offset = 1;
            end
        end
    end

    ego_lane_index(i) = min(5, max(1, ego_lane_index(i) + offset));
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

function [trigger_78_2, trigger_78_5, trigger_78_6, trigger_78_7, ...
    com_78_2, com_78_5, com_78_6, com_78_7, thres_min_spdlim] = ...
    calc_lane_min_speed_local(lane_num, lane_pos, congestion, ego_speed_kph)

n = numel(lane_num);
trigger_78_2 = zeros(n, 1);
trigger_78_5 = zeros(n, 1);
trigger_78_6 = zeros(n, 1);
trigger_78_7 = zeros(n, 1);
com_78_2 = zeros(n, 1);
com_78_5 = zeros(n, 1);
com_78_6 = zeros(n, 1);
com_78_7 = zeros(n, 1);
thres_min_spdlim = -1 * ones(n, 1);

lane_num_rule = lane_num(:);
lane_num_rule(lane_num_rule >= 3) = 3;
lane_pos = lane_pos(:);
congestion = congestion(:);
ego_speed_kph = ego_speed_kph(:);

for ii = 1:n
    article = "";
    thres = -1;

    if lane_num_rule(ii) == 2 && lane_pos(ii) == 1
        article = "78.5";
        thres = 100;
        trigger_78_5(ii) = 1;
    elseif lane_num_rule(ii) == 2 && lane_pos(ii) == 3
        article = "78.2";
        thres = 60;
        trigger_78_2(ii) = 1;
    elseif lane_num_rule(ii) == 3 && lane_pos(ii) == 1
        article = "78.6";
        thres = 110;
        trigger_78_6(ii) = 1;
    elseif lane_num_rule(ii) == 3 && lane_pos(ii) == 2
        article = "78.7";
        thres = 90;
        trigger_78_7(ii) = 1;
    elseif lane_num_rule(ii) == 3 && lane_pos(ii) == 3
        article = "78.2";
        thres = 60;
        trigger_78_2(ii) = 1;
    end

    if article == ""
        continue;
    end

    thres_min_spdlim(ii) = thres;
    if congestion(ii) ~= 1 && ego_speed_kph(ii) < thres
        com_val = -1;
    else
        com_val = 1;
    end

    switch article
        case "78.2"
            com_78_2(ii) = com_val;
        case "78.5"
            com_78_5(ii) = com_val;
        case "78.6"
            com_78_6(ii) = com_val;
        case "78.7"
            com_78_7(ii) = com_val;
    end
end
end
