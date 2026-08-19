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

% ========= 2) Find monitor_result.mat and corresponding RoadMarking_events.csv =========
date_dirs = dir(main_folder);
date_dirs = date_dirs([date_dirs.isdir]);
date_dirs = date_dirs(~ismember({date_dirs.name},{'.','..'}));

allMatPaths = {};
meta = struct('date_name',{}, 'segment_name',{}, 'monitor_result_path',{}, 'event_path',{}, 'out_dir',{});

for d = 1:numel(date_dirs)
    date_name = date_dirs(d).name;
    date_path = fullfile(date_dirs(d).folder, date_name);

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

    event_record_root = fullfile(date_path, 'zEvent_RoadMarking');
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

        event_path = fullfile(event_record_root, segment_name, 'RoadMarking_events.csv');
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
fprintf('Found %d matched monitor_result.mat/RoadMarking_events.csv files.\n', numMats);
if numMats == 0
    error('No matched monitor_result.mat/RoadMarking_events.csv files found. Expected YYYYMMDD\\sim_result\\<segment>\\monitor_result.mat and YYYYMMDD\\zEvent_RoadMarking\\<segment>\\RoadMarking_events.csv.');
end

thres_max_continuous_overlap = 6;

% ========= 3) Build evidence chain for each road-marking event =========
for i = 1:numMats
    monitor_result_path = meta(i).monitor_result_path;
    event_path = meta(i).event_path;
    out_dir = meta(i).out_dir;
    fprintf('[%d/%d] Preparing: %s\n', i, numMats, event_path);

    S_monitor = load(monitor_result_path);
    seg_data = S_monitor.sim_output;

    seg_trigger_TSM_4_3_1 = get_required_signal(seg_data, 'Trigger_TSM_4_3_1');
    seg_trigger_TSM_4_5_2 = get_required_signal(seg_data, 'Trigger_TSM_4_5_2');
    seg_trigger_TSM_4_5_3 = get_required_signal(seg_data, 'Trigger_TSM_4_5_3');
    seg_com_TSM_4_3_1 = get_required_signal(seg_data, 'Com_TSM_4_3_1');
    seg_com_TSM_4_5_2 = get_required_signal(seg_data, 'Com_TSM_4_5_2');
    seg_com_TSM_4_5_3 = get_required_signal(seg_data, 'Com_TSM_4_5_3');
    seg_overlap_LeftLine = get_required_signal(seg_data, 'Left_line_intersect');
    seg_overlap_RightLine = get_required_signal(seg_data, 'Right_line_intersect');

    event_opts = detectImportOptions(event_path, 'TextType', 'string');
    event_data = readtable(event_path, event_opts);

    num_event = size(event_data, 1);
    for j = 1:num_event
        event_start_idx = event_data.start_idx(j);
        event_end_idx = event_data.end_idx(j);
        idx = event_start_idx:event_end_idx;
        n_event = numel(idx);

        map_path = fullfile(out_dir, ['RoadMarking_event_', num2str(j), '_MapInfo.csv']);
        if ~exist(map_path, 'file')
            error('Missing S5 input file for event %d. Expected: %s', j, map_path);
        end

        map_opts = detectImportOptions(map_path, 'TextType', 'string');
        map_info = readtable(map_path, map_opts);
        if height(map_info) ~= n_event
            error('S5 MapInfo row count mismatch for event %d: event len=%d, MapInfo=%d', ...
                j, n_event, height(map_info));
        end

        event_time = read_event_time_from_s5(map_info, n_event);

        raw_trigger_TSM_4_3_1 = seg_trigger_TSM_4_3_1(idx);
        raw_trigger_TSM_4_5_2 = seg_trigger_TSM_4_5_2(idx);
        raw_trigger_TSM_4_5_3 = seg_trigger_TSM_4_5_3(idx);
        raw_com_TSM_4_3_1 = seg_com_TSM_4_3_1(idx);
        raw_com_TSM_4_5_2 = seg_com_TSM_4_5_2(idx);
        raw_com_TSM_4_5_3 = seg_com_TSM_4_5_3(idx);
        raw_overlap_LeftLine = seg_overlap_LeftLine(idx);
        raw_overlap_RightLine = seg_overlap_RightLine(idx);

        lag_frames = infer_s5_lag(raw_overlap_LeftLine, raw_overlap_RightLine, map_info);

        event_trigger_TSM_4_3_1 = double(shift_signal_to_s5(raw_trigger_TSM_4_3_1, lag_frames) ~= 0);
        event_trigger_TSM_4_5_2 = double(shift_signal_to_s5(raw_trigger_TSM_4_5_2, lag_frames) ~= 0);
        event_trigger_TSM_4_5_3 = double(shift_signal_to_s5(raw_trigger_TSM_4_5_3, lag_frames) ~= 0);
        event_com_TSM_4_3_1 = normalize_compliance( ...
            shift_signal_to_s5(raw_com_TSM_4_3_1, lag_frames), event_trigger_TSM_4_3_1);
        event_com_TSM_4_5_2 = normalize_compliance( ...
            shift_signal_to_s5(raw_com_TSM_4_5_2, lag_frames), event_trigger_TSM_4_5_2);
        event_com_TSM_4_5_3 = normalize_compliance( ...
            shift_signal_to_s5(raw_com_TSM_4_5_3, lag_frames), event_trigger_TSM_4_5_3);
        event_overlap_LeftLine = double(shift_signal_to_s5(raw_overlap_LeftLine, lag_frames) ~= 0);
        event_overlap_RightLine = double(shift_signal_to_s5(raw_overlap_RightLine, lag_frames) ~= 0);
        event_time_continuous_overlap = build_cached_overlap_time( ...
            event_overlap_LeftLine ~= 0, event_overlap_RightLine ~= 0, 1.0, 0.01);

        event_lanechange_stage = build_lanechange_stage( ...
            event_overlap_LeftLine ~= 0, event_overlap_RightLine ~= 0);

        event_map_type_left1 = read_table_column_or_default(map_info, 'MAP_Type_Left1', n_event, 0);
        event_map_type_right1 = read_table_column_or_default(map_info, 'MAP_Type_Right1', n_event, 0);
        thres_max_continuous_line_overlap = thres_max_continuous_overlap * ones(n_event, 1);

        T = table( ...
            event_time, ...
            event_trigger_TSM_4_3_1, event_trigger_TSM_4_5_2, event_trigger_TSM_4_5_3, ...
            event_com_TSM_4_3_1, event_com_TSM_4_5_2, event_com_TSM_4_5_3, ...
            event_overlap_LeftLine, event_overlap_RightLine, ...
            event_lanechange_stage, event_map_type_left1, event_map_type_right1, ...
            event_time_continuous_overlap, thres_max_continuous_line_overlap, ...
            'VariableNames', {'event_time', ...
            'trigger_TSM_4_3_1', 'trigger_TSM_4_5_2', 'trigger_TSM_4_5_3', ...
            'com_TSM_4_3_1', 'com_TSM_4_5_2', 'com_TSM_4_5_3', ...
            'overlap_LeftLine', 'overlap_RightLine', ...
            'lanechange_stage', 'MAP_Type_Left1', 'MAP_Type_Right1', ...
            'Time_ContinuousLineOverlap', 'Thres_MaxContinuousLineOverlap'} ...
            );

        event_name = ['RoadMarking_event_', num2str(j), '_EvidenceChain.csv'];
        csv_path = fullfile(out_dir, event_name);
        write_event_table(T, csv_path);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, event_name);
    end
end

function event_time = read_event_time_from_s5(map_info, n_event)
if ismember('event_time', map_info.Properties.VariableNames)
    event_time = map_info.event_time;
    if iscell(event_time) || isstring(event_time) || ischar(event_time)
        event_time = str2double(string(event_time));
    end
    event_time = double(event_time(:));
else
    event_time = 0.01*(1:n_event)';
end

if numel(event_time) ~= n_event
    error('S5 event_time length mismatch: expected %d rows, got %d.', n_event, numel(event_time));
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

function signal = get_optional_signal(seg_data, field_name, default_signal)
try
    signal = get_required_signal(seg_data, field_name);
catch
    signal = default_signal;
end
signal = signal(:);
end

function com = normalize_compliance(raw_com, trigger)
raw_com = raw_com(:);
trigger = trigger(:) ~= 0;
com = zeros(size(raw_com));
com(raw_com > 0) = 1;
com(raw_com < 0) = -1;
com(~trigger) = 0;
end

function lag_frames = infer_s5_lag(overlap_left, overlap_right, map_info)
side = overlap_side(overlap_left, overlap_right);
sim_switch_idx = find((side(2:end) ~= side(1:end-1)) & ...
    ((side(2:end) ~= 0) | (side(1:end-1) ~= 0))) + 1;

if isempty(sim_switch_idx)
    lag_frames = 0;
    return;
end

matched_lags = [];
for k = 1:numel(sim_switch_idx)
    idx = sim_switch_idx(k);
    side_now = side(idx);
    if side_now == 0 && idx > 1
        side_now = side(idx-1);
    end

    map_switch_idx = find_map_switch_idx(map_info, side_now);
    if isempty(map_switch_idx)
        continue;
    end

    [gap, nearest_pos] = min(abs(map_switch_idx - idx));
    if gap <= 5
        matched_lags(end+1,1) = idx - map_switch_idx(nearest_pos); %#ok<AGROW>
    end
end

if isempty(matched_lags)
    lag_frames = 0;
else
    lag_frames = round(median(matched_lags));
    lag_frames = max(min(lag_frames, 5), -5);
end
end

function side = overlap_side(overlap_left, overlap_right)
left = double(overlap_left(:) ~= 0);
right = double(overlap_right(:) ~= 0);
side = zeros(size(left));
side(left ~= 0 & right == 0) = 1;
side(right ~= 0 & left == 0) = -1;
end

function map_switch_idx = find_map_switch_idx(map_info, side)
if side > 0
    var_name = 'MAP_C0_Left1';
elseif side < 0
    var_name = 'MAP_C0_Right1';
else
    map_switch_idx = unique([ ...
        find_map_switch_idx(map_info, 1)
        find_map_switch_idx(map_info, -1)]);
    return;
end

if ~ismember(var_name, map_info.Properties.VariableNames)
    map_switch_idx = [];
    return;
end

c0 = table_column_to_double(map_info.(var_name));
if numel(c0) < 2
    map_switch_idx = [];
else
    map_switch_idx = find(abs(diff(c0)) > 1e-9) + 1;
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

function stage = build_lanechange_stage(overlap_left, overlap_right)
% lanechange_stage enum:
% {0:not-change, 1:left_lanechange_s1, 2:left_lanechange_s2, 3:right_lanechange_s1, 4:right_lanechange_s2, 5:unknown}
overlap_left = overlap_left(:) ~= 0;
overlap_right = overlap_right(:) ~= 0;

n_rows = numel(overlap_left);
stage = zeros(n_rows, 1);

if n_rows == 0
    return;
end

overlap_any = overlap_left | overlap_right;
start_idx = 1;

% If event starts from overlap, this part may belong to the previous lane change.
if overlap_any(1)
    k = 1;
    while k <= n_rows && overlap_any(k)
        stage(k) = 5;
        k = k + 1;
    end
    if k > n_rows
        return;
    end
    start_idx = k;
end

% State machine driven only by overlap sequence:
% first overlap side decides direction; crossing the opposite side switches to stage-2.
dir = 0;    % 1:left, -1:right
phase = 0;  % 0:idle, 1:stage-1, 2:stage-2

for i = start_idx:n_rows
    left_now = overlap_left(i);
    right_now = overlap_right(i);

    if phase == 0
        if left_now && ~right_now
            dir = 1;
            phase = 1;
        elseif right_now && ~left_now
            dir = -1;
            phase = 1;
        elseif left_now && right_now
            stage(i) = 5;  % ambiguous side at lane-change start
            continue;
        end
    elseif phase == 1
        if dir == 1 && right_now
            phase = 2;
        elseif dir == -1 && left_now
            phase = 2;
        end
    else % phase == 2
        % Lane change ends when no line overlap is observed.
        if ~left_now && ~right_now
            phase = 0;
            dir = 0;
        end
    end

    if phase == 1
        if dir == 1
            stage(i) = 1;
        else
            stage(i) = 3;
        end
    elseif phase == 2
        if dir == 1
            stage(i) = 2;
        else
            stage(i) = 4;
        end
    else
        stage(i) = 0;
    end
end

end

function col = table_column_to_double(col)
if iscell(col) || isstring(col) || ischar(col)
    col = str2double(string(col));
end
col = double(col(:));
end

function time_continuous_overlap = build_cached_overlap_time(overlap_left, overlap_right, cache_gap_sec, dt_sec)
% Build continuous overlap time with short-gap cache.
% If a non-overlap gap is shorter than cache_gap_sec, accumulated overlap
% time is held and continues when overlap resumes; otherwise it resets.

if nargin < 4 || isempty(dt_sec)
    dt_sec = 0.01;
end
if nargin < 3 || isempty(cache_gap_sec)
    cache_gap_sec = 1.0;
end

overlap_any = (overlap_left(:) ~= 0) | (overlap_right(:) ~= 0);
n = numel(overlap_any);
time_continuous_overlap = zeros(n, 1);

% Strictly "< cache_gap_sec": for dt=0.01 and cache=1.0, allow up to 99 frames.
max_gap_frames = max(0, ceil(cache_gap_sec / dt_sec) - 1);

run_time = 0;
gap_frames = 0;

for i = 1:n
    if overlap_any(i)
        run_time = run_time + dt_sec;
        gap_frames = 0;
    else
        if run_time > 0 && gap_frames < max_gap_frames
            gap_frames = gap_frames + 1;  % hold run_time during cached gap
        else
            run_time = 0;
            gap_frames = 0;
        end
    end

    time_continuous_overlap(i) = run_time;
end
end

function col = read_table_column_or_default(tbl, var_name, n_rows, default_value)
if ismember(var_name, tbl.Properties.VariableNames)
    col = tbl.(var_name);
    if iscell(col) || isstring(col) || ischar(col)
        col = str2double(string(col));
    end
    col = double(col(:));
else
    col = default_value * ones(n_rows, 1);
end

if numel(col) ~= n_rows
    error('Column %s length mismatch. Expected %d rows, got %d.', var_name, n_rows, numel(col));
end

col(~isfinite(col)) = default_value;
end
