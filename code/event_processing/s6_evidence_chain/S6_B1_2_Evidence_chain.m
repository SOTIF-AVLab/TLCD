clear; clc
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) 根目录：Data_Nanjing_collection =========
main_folder = getenv('TLCD_DATA_ROOT');
if isempty(main_folder)
    error('Set TLCD_DATA_ROOT to the city-level source directory.');
end

% ========= 2) 搜索所有 S3生成的 monitor_result.mat  以及对应  S4生成的 csv  =========
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

    monitor_result_root = fullfile(date_path, 'sim_result');
    if ~exist(monitor_result_root, 'dir')
        continue;
    end

    Event_record_root = fullfile(date_path, 'zEvent_MaxSpdlim');
    if ~exist(Event_record_root, 'dir')
        continue;
    end

    seg_dirs = dir(monitor_result_root);
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
        monitor_result_path = fullfile(seg_path, 'monitor_result.mat');
        if ~exist(monitor_result_path,'file')
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

        allMatPaths{end+1,1} = monitor_result_path; %#ok<AGROW>
        meta(end+1).date_name = date_name; %#ok<AGROW>
        meta(end).segment_name = segment_name;
        meta(end).mat_path = monitor_result_path;
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
fprintf('Found %d monitor_result.mat files.\n', numMats);
fprintf('Found %d MaxSpdlim_events.csv files.\n', numEvents);
if numMats == 0
    error('未找到任何 monitor_result.mat：请确认目录结构为 YYYYMMDD\\sim_result\\<segment>\\monitor_result.mat');
end

if numMats ~= numEvents
    error('mat文件数量与csv事件数量不匹配，请检查');
end


% ========= 3) 分割每一个5min segment 中记录的每一个 event =========
% ========= 4) 单独保存每一个 event 的关键证据链           =========
for i = 1:numMats
    % ---- 加载数据 ----
    mat_path = meta(i).mat_path;
    event_path = meta2(i).event_path;
    out_dir  = meta2(i).out_dir;
    fprintf('[%d/%d] Preparing: %s\n', i, numMats, event_path);

    S = load(mat_path);
    seg_data = S.sim_output;

    % ---- 读取关键命题、关键中间状态量 ----
    seg_trigger_IMR_45_1  = seg_data.Trigger_IMR_45_1;
    seg_trigger_IMR_46_3  = seg_data.Trigger_IMR_46_3;
    seg_trigger_IMR_46_4  = seg_data.Trigger_IMR_46_4;
    seg_trigger_IMR_46_5  = seg_data.Trigger_IMR_46_5;
    seg_trigger_IMR_78_1  = seg_data.Trigger_IMR_78_1;
    seg_trigger_IMR_78_3  = seg_data.Trigger_IMR_78_3;
    seg_com_IMR_45_1  = seg_data.Com_IMR_45_1;
    seg_com_IMR_46_3  = seg_data.Com_IMR_46_3;
    seg_com_IMR_46_4  = seg_data.Com_IMR_46_4;
    seg_com_IMR_46_5  = seg_data.Com_IMR_46_5;
    seg_com_IMR_78_1  = seg_data.Com_IMR_78_1;
    seg_com_IMR_78_3  = seg_data.Com_IMR_78_3;

    seg_Ego_velocity     = seg_data.INS_VelocitySpeed;
    seg_Road_type        = seg_data.MAP_RoadType_Curr;
    seg_Lane_type        = seg_data.LaneType;
    seg_IsMaxSpdsignArea = seg_data.IsMaxSpdsignArea;
    seg_Thres_MaxSpdlim  = seg_data.MaxSpeed_Limit;

    opts = detectImportOptions(event_path, 'TextType', 'string');
    opts = setvartype(opts, 'violated_article', 'string');
    event_data = readtable(event_path, opts);

    % ---- 每个事件单独处理 ----
    num_event = size(event_data,1);
    for j = 1:num_event
        event_start_idx = event_data.start_idx(j);
        event_end_idx   = event_data.end_idx(j);
        n_event = event_data.len(j);
        event_time              = 0.01*(1:n_event)';
        event_trigger_IMR_45_1  = seg_trigger_IMR_45_1(event_start_idx:event_end_idx);
        event_trigger_IMR_46_3  = seg_trigger_IMR_46_3(event_start_idx:event_end_idx);
        event_trigger_IMR_46_4  = seg_trigger_IMR_46_4(event_start_idx:event_end_idx);
        event_trigger_IMR_46_5  = seg_trigger_IMR_46_5(event_start_idx:event_end_idx);
        event_trigger_IMR_78_1  = seg_trigger_IMR_78_1(event_start_idx:event_end_idx);
        event_trigger_IMR_78_3  = seg_trigger_IMR_78_3(event_start_idx:event_end_idx);
        event_com_IMR_45_1  = seg_com_IMR_45_1(event_start_idx:event_end_idx);
        event_com_IMR_46_3  = seg_com_IMR_46_3(event_start_idx:event_end_idx);
        event_com_IMR_46_4  = seg_com_IMR_46_4(event_start_idx:event_end_idx);
        event_com_IMR_46_5  = seg_com_IMR_46_5(event_start_idx:event_end_idx);
        event_com_IMR_78_1  = seg_com_IMR_78_1(event_start_idx:event_end_idx);
        event_com_IMR_78_3  = seg_com_IMR_78_3(event_start_idx:event_end_idx);

        event_Ego_velocity     = seg_Ego_velocity(event_start_idx:event_end_idx) / 3.6;
        event_Road_type        = seg_Road_type(event_start_idx:event_end_idx);
        event_Lane_type        = seg_Lane_type(event_start_idx:event_end_idx);
        raw_IsMaxSpdsignArea   = seg_IsMaxSpdsignArea(event_start_idx:event_end_idx);
        raw_Thres_MaxSpdlim    = seg_Thres_MaxSpdlim(event_start_idx:event_end_idx);
        event_Thres_MaxSpdlim  = raw_Thres_MaxSpdlim;

        map_info = read_s5_map_info(out_dir, j, n_event);
        if ~isempty(map_info)
            lag_frames = infer_speed_limit_lag(raw_Thres_MaxSpdlim, map_info);
            event_Thres_MaxSpdlim = shift_signal_to_s5(raw_Thres_MaxSpdlim, lag_frames);
            event_Thres_MaxSpdlim = repair_evidence_speed_limit(event_Thres_MaxSpdlim, map_info);
            [event_IsMaxSpdsignArea, event_Thres_MaxSpdlim] = apply_map_speed_limit_area( ...
                map_info, event_Thres_MaxSpdlim);
        else
            event_Thres_MaxSpdlim = repair_evidence_speed_limit(event_Thres_MaxSpdlim, []);
            event_IsMaxSpdsignArea = normalize_binary_signal(raw_IsMaxSpdsignArea);
        end
        event_Lane_type = round_near_integer(event_Lane_type);

        predicate_lag_frames = infer_predicate_lag_from_speed(event_Ego_velocity, event_Thres_MaxSpdlim, ...
            event_com_IMR_45_1, event_com_IMR_46_3, event_com_IMR_46_4, event_com_IMR_46_5, ...
            event_com_IMR_78_1, event_com_IMR_78_3);
        event_trigger_IMR_45_1 = double(shift_signal_to_s5(event_trigger_IMR_45_1, predicate_lag_frames) ~= 0);
        event_trigger_IMR_46_3 = double(shift_signal_to_s5(event_trigger_IMR_46_3, predicate_lag_frames) ~= 0);
        event_trigger_IMR_46_4 = double(shift_signal_to_s5(event_trigger_IMR_46_4, predicate_lag_frames) ~= 0);
        event_trigger_IMR_46_5 = double(shift_signal_to_s5(event_trigger_IMR_46_5, predicate_lag_frames) ~= 0);
        event_trigger_IMR_78_1 = double(shift_signal_to_s5(event_trigger_IMR_78_1, predicate_lag_frames) ~= 0);
        event_trigger_IMR_78_3 = double(shift_signal_to_s5(event_trigger_IMR_78_3, predicate_lag_frames) ~= 0);
        event_com_IMR_45_1 = shift_signal_to_s5(event_com_IMR_45_1, predicate_lag_frames);
        event_com_IMR_46_3 = shift_signal_to_s5(event_com_IMR_46_3, predicate_lag_frames);
        event_com_IMR_46_4 = shift_signal_to_s5(event_com_IMR_46_4, predicate_lag_frames);
        event_com_IMR_46_5 = shift_signal_to_s5(event_com_IMR_46_5, predicate_lag_frames);
        event_com_IMR_78_1 = shift_signal_to_s5(event_com_IMR_78_1, predicate_lag_frames);
        event_com_IMR_78_3 = shift_signal_to_s5(event_com_IMR_78_3, predicate_lag_frames);
        event_com_IMR_45_1 = align_com_with_speed(event_com_IMR_45_1, event_trigger_IMR_45_1, ...
            event_Ego_velocity, event_Thres_MaxSpdlim);
        event_com_IMR_46_3 = align_com_with_speed(event_com_IMR_46_3, event_trigger_IMR_46_3, ...
            event_Ego_velocity, event_Thres_MaxSpdlim);
        event_com_IMR_46_4 = align_com_with_speed(event_com_IMR_46_4, event_trigger_IMR_46_4, ...
            event_Ego_velocity, event_Thres_MaxSpdlim);
        event_com_IMR_46_5 = align_com_with_speed(event_com_IMR_46_5, event_trigger_IMR_46_5, ...
            event_Ego_velocity, event_Thres_MaxSpdlim);
        event_com_IMR_78_1 = align_com_with_speed(event_com_IMR_78_1, event_trigger_IMR_78_1, ...
            event_Ego_velocity, event_Thres_MaxSpdlim);
        event_com_IMR_78_3 = align_com_with_speed(event_com_IMR_78_3, event_trigger_IMR_78_3, ...
            event_Ego_velocity, event_Thres_MaxSpdlim);


        T = table( ...
            event_time, event_trigger_IMR_45_1, event_trigger_IMR_46_3, event_trigger_IMR_46_4, ...
            event_trigger_IMR_46_5, event_trigger_IMR_78_1, event_trigger_IMR_78_3, ...
            event_com_IMR_45_1, event_com_IMR_46_3, event_com_IMR_46_4, event_com_IMR_46_5, ...
            event_com_IMR_78_1, event_com_IMR_78_3, ...
            event_Ego_velocity, event_Road_type, event_Lane_type, ...
            event_IsMaxSpdsignArea,event_Thres_MaxSpdlim, ...
            'VariableNames', {'event_time','trigger_IMR_45_1','trigger_IMR_46_3','trigger_IMR_46_4', ...
            'trigger_IMR_46_5','trigger_IMR_78_1','trigger_IMR_78_3', ...
            'com_IMR_45_1', 'com_IMR_46_3', 'com_IMR_46_4', 'com_IMR_46_5', ...
            'com_IMR_78_1', 'com_IMR_78_3', ...
            'Ego_velocity', 'Road_type', 'Lane_type', ...
            'IsMaxSpdsignArea', 'Thres_MaxSpdlim'} ...
        );

        % ---- 写CSV到对应event目录 ----
        % 建议文件名：MaxSpdlim_event_<n>_EvidenceChain.csv
        event_name = ['MaxSpdlim_event_', num2str(j), '_EvidenceChain.csv'];
        csv_path = fullfile(out_dir, event_name);
        writetable(T, csv_path);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, event_name);

    end


end

function map_info = read_s5_map_info(out_dir, event_num, n_event)
map_info = [];
map_path = fullfile(out_dir, sprintf('MaxSpdlim_event_%d_MapInfo.csv', event_num));
if ~exist(map_path, 'file')
    return;
end

map_info = readtable(map_path);
if height(map_info) ~= n_event
    map_info = [];
end
end

function lag_frames = infer_speed_limit_lag(thres_max_spdlim, map_info)
lag_frames = 0;

thres = round(table_column_to_double(thres_max_spdlim));
map_limit = round(read_map_current_lane_speed_limit(map_info));
if numel(thres) < 2 || numel(map_limit) < 2
    return;
end

sim_switch_idx = find((thres(2:end) ~= thres(1:end-1)) & thres(2:end) > 0) + 1;
map_switch_idx = find((map_limit(2:end) ~= map_limit(1:end-1)) & map_limit(2:end) > 0) + 1;
if isempty(sim_switch_idx) || isempty(map_switch_idx)
    return;
end

matched_lags = [];
for k = 1:numel(map_switch_idx)
    [gap, nearest_pos] = min(abs(sim_switch_idx - map_switch_idx(k)));
    if gap <= 5
        matched_lags(end+1,1) = sim_switch_idx(nearest_pos) - map_switch_idx(k); %#ok<AGROW>
    end
end

if ~isempty(matched_lags)
    lag_frames = round(median(matched_lags));
    lag_frames = max(min(lag_frames, 5), -5);
end
end

function lag_frames = infer_predicate_lag_from_speed(ego_velocity, thres_max_spdlim, varargin)
lag_frames = 0;
ego_velocity = table_column_to_double(ego_velocity);
thres = table_column_to_double(thres_max_spdlim);
if numel(ego_velocity) < 2 || numel(thres) ~= numel(ego_velocity)
    return;
end

speed_violation = (ego_velocity * 3.6 > thres) & (thres > 0);
speed_switch_idx = find_signal_switch_idx(speed_violation);
if isempty(speed_switch_idx)
    return;
end

com_violation = false(size(speed_violation));
for k = 1:numel(varargin)
    com_values = table_column_to_double(varargin{k});
    if numel(com_values) == numel(com_violation)
        com_violation = com_violation | (com_values < 0);
    end
end

predicate_switch_idx = find_signal_switch_idx(com_violation);
if isempty(predicate_switch_idx)
    return;
end

matched_lags = [];
for k = 1:numel(speed_switch_idx)
    [gap, nearest_pos] = min(abs(predicate_switch_idx - speed_switch_idx(k)));
    if gap <= 5
        matched_lags(end+1,1) = predicate_switch_idx(nearest_pos) - speed_switch_idx(k); %#ok<AGROW>
    end
end

if ~isempty(matched_lags)
    lag_frames = round(median(matched_lags));
    lag_frames = max(min(lag_frames, 5), -5);
end
end

function switch_idx = find_signal_switch_idx(signal)
signal = signal(:) ~= 0;
if numel(signal) < 2
    switch_idx = [];
else
    switch_idx = find(signal(2:end) ~= signal(1:end-1)) + 1;
end
end

function aligned = shift_signal_to_s5(signal, lag_frames)
signal = signal(:);
aligned = signal;
n = numel(signal);
if n == 0 || lag_frames == 0
    return;
end

lag_frames = round(lag_frames);
if lag_frames > 0
    if lag_frames >= n
        aligned(:) = signal(end);
    else
        aligned(1:n-lag_frames) = signal(1+lag_frames:n);
        aligned(n-lag_frames+1:n) = signal(end);
    end
elseif lag_frames < 0
    lead_frames = -lag_frames;
    if lead_frames >= n
        aligned(:) = signal(1);
    else
        aligned(1:lead_frames) = signal(1);
        aligned(lead_frames+1:n) = signal(1:n-lead_frames);
    end
end
end

function signal = normalize_binary_signal(signal)
signal = double(table_column_to_double(signal) ~= 0);
end

function [is_max_spd_sign_area, thres] = apply_map_speed_limit_area(map_info, thres)
thres = thres(:);
map_limit = read_map_current_lane_speed_limit(map_info);
is_max_spd_sign_area = double(map_limit > 0);
if numel(map_limit) == numel(thres)
    thres(map_limit <= 0) = 120;
    thres(map_limit > 0) = map_limit(map_limit > 0);
end
end

function values = round_near_integer(values)
values = table_column_to_double(values);
rounded_values = round(values);
near_integer = abs(values - rounded_values) < 1e-6;
values(near_integer) = rounded_values(near_integer);
end

function com_values = align_com_with_speed(com_values, trigger_values, ego_velocity, thres_max_spdlim)
com_values = table_column_to_double(com_values);
trigger_values = table_column_to_double(trigger_values);
ego_velocity = table_column_to_double(ego_velocity);
thres_max_spdlim = table_column_to_double(thres_max_spdlim);

valid = trigger_values ~= 0;
speed_violation = (ego_velocity * 3.6 > thres_max_spdlim) & (thres_max_spdlim > 0);
com_values(~valid) = 0;
com_values(valid & speed_violation) = -1;
com_values(valid & ~speed_violation) = 1;
end

function thres = repair_evidence_speed_limit(thres, map_info)
thres = thres(:);
if ~isempty(map_info)
    map_limit = read_map_current_lane_speed_limit(map_info);
    if numel(map_limit) == numel(thres)
        fill_from_map = (thres <= 0) & (map_limit > 0);
        thres(fill_from_map) = map_limit(fill_from_map);
    end
end

thres = fill_zero_runs_with_neighbors(thres);
end

function map_limit = read_map_current_lane_speed_limit(map_info)
if ismember('Lane_Spdlim_CurrentLane', map_info.Properties.VariableNames)
    map_limit = table_column_to_double(map_info.Lane_Spdlim_CurrentLane);
    return;
end

lane_vars = "LaneMaxSpdlim_" + string(1:5);
required_vars = [lane_vars, "EgoLaneIndex"];
if ~all(ismember(required_vars, string(map_info.Properties.VariableNames)))
    map_limit = zeros(height(map_info), 1);
    return;
end

ego_lane_index = round(table_column_to_double(map_info.EgoLaneIndex));
map_limit = zeros(height(map_info), 1);
for lane_idx = 1:numel(lane_vars)
    lane_rows = ego_lane_index == lane_idx;
    lane_limit = table_column_to_double(map_info.(lane_vars(lane_idx)));
    map_limit(lane_rows) = lane_limit(lane_rows);
end
end

function signal = fill_zero_runs_with_neighbors(signal)
signal = signal(:);
is_gap = (signal <= 0);
d = diff([false; is_gap; false]);
start_idx = find(d == 1);
end_idx = find(d == -1) - 1;

for k = 1:numel(start_idx)
    s = start_idx(k);
    e = end_idx(k);
    prev_value = NaN;
    next_value = NaN;
    if s > 1
        prev_value = signal(s - 1);
    end
    if e < numel(signal)
        next_value = signal(e + 1);
    end

    if isfinite(prev_value) && prev_value > 0 && isfinite(next_value) && next_value > 0 && prev_value == next_value
        signal(s:e) = prev_value;
    elseif isfinite(prev_value) && prev_value > 0
        signal(s:e) = prev_value;
    elseif isfinite(next_value) && next_value > 0
        signal(s:e) = next_value;
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
