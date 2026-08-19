clear; clc
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) 根目录：Data_Nanjing_collection =========
main_folder = getenv('TLCD_DATA_ROOT');
if isempty(main_folder)
    error('Set TLCD_DATA_ROOT to the city-level source directory.');
end

% ========= 2) 搜索所有 S3生成的 monitor_result.mat  以及对应 S4生成的 lane_change_events.csv =========
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

    Event_record_root = fullfile(date_path, 'zEvent_LaneChange');
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
    sim_root = fullfile(date_path, 'zEvent_LaneChange');
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
        Event_record_path = fullfile(Event_path, 'lane_change_events.csv');
        if ~exist(Event_record_path,'file')
            continue;
        end

        % 输出目录：...\YYYYMMDD\zEvent_LaneChange\<segment>\
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
fprintf('Found %d lane_change_events.csv files.\n', numEvents);
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
    seg_trigger_IMR_44_1  = seg_data.Trigger_IMR_44_1;
    seg_com_IMR_44_1      = seg_data.Com_IMR_44_1;
    seg_com_IMR_44_1_RVTL = seg_data.Com_IMR_44_1_Dis_RVTL;
    seg_com_IMR_44_1_FV   = seg_data.Com_IMR_44_1_TTC_FV;
    seg_Ego_velocity      = seg_data.INS_VelocitySpeed;
    seg_Obj_Velocity      = seg_data.Obj_VelocityX;
    seg_RVTL_index        = seg_data.Index_RVTL_cl;
    seg_TTC_FV            = seg_data.TTC_FrontVeh;
    seg_dis_RVTL          = seg_data.Dis_RVTL_cl;
    seg_overlap_LeftLine  = seg_data.Left_line_intersect;
    seg_overlap_RightLine = seg_data.Right_line_intersect;

    event_data = readtable(event_path);

    % ---- 每个事件单独处理 ----
    num_event = size(event_data,1);
    for j = 1:num_event
        event_start_idx = event_data.start_idx(j);
        event_end_idx   = event_data.end_idx(j);
        idx = event_start_idx:event_end_idx;
        n_event = numel(idx);

        map_path = fullfile(out_dir, ['lane_change_event_', num2str(j), '_MapInfo.csv']);
        if ~exist(map_path, 'file')
            error('Missing S5 MapInfo for lane-change event %d: %s', j, map_path);
        end
        map_info = readtable(map_path);
        if height(map_info) ~= n_event
            error('S5 MapInfo row count mismatch for lane-change event %d: event len=%d, MapInfo=%d', ...
                j, n_event, height(map_info));
        end
        event_time = read_event_time_from_s5(map_info, n_event);

        raw_trigger_IMR_44_1  = seg_trigger_IMR_44_1(idx);
        raw_com_IMR_44_1      = seg_com_IMR_44_1(idx);
        raw_com_IMR_44_1_RVTL = seg_com_IMR_44_1_RVTL(idx);
        raw_com_IMR_44_1_FV   = seg_com_IMR_44_1_FV(idx);
        raw_Ego_velocity      = seg_Ego_velocity(idx) / 3.6;
        raw_TTC_FV            = seg_TTC_FV(idx);
        raw_dis_RVTL          = seg_dis_RVTL(idx);
        raw_overlap_LeftLine  = seg_overlap_LeftLine(idx);
        raw_overlap_RightLine = seg_overlap_RightLine(idx);
        raw_Obj_Velocity      = seg_Obj_Velocity(idx,:);
        raw_RVTL_index        = seg_RVTL_index(idx);

        % Simulink-derived evidence signals can lag S5 by different amounts.
        lag_frames = infer_s5_lag(raw_overlap_LeftLine, raw_overlap_RightLine, map_info);
        predicate_lag_frames = lag_frames + infer_predicate_lag( ...
            raw_overlap_LeftLine, raw_overlap_RightLine, ...
            raw_trigger_IMR_44_1, raw_com_IMR_44_1, ...
            raw_com_IMR_44_1_RVTL, raw_com_IMR_44_1_FV);

        event_trigger_IMR_44_1  = double(shift_signal_to_s5(raw_trigger_IMR_44_1, predicate_lag_frames) ~= 0);
        event_com_IMR_44_1      = shift_signal_to_s5(raw_com_IMR_44_1, predicate_lag_frames);
        event_com_IMR_44_1_RVTL = shift_signal_to_s5(raw_com_IMR_44_1_RVTL, predicate_lag_frames);
        event_com_IMR_44_1_FV   = shift_signal_to_s5(raw_com_IMR_44_1_FV, predicate_lag_frames);
        event_Ego_velocity      = choose_s5_velocity(out_dir, j, n_event, ...
            shift_signal_to_s5(raw_Ego_velocity, lag_frames));
        event_TTC_FV            = shift_signal_to_s5(raw_TTC_FV, lag_frames);
        event_TTC_FV(~isfinite(event_TTC_FV)) = -1;
        event_dis_RVTL          = shift_signal_to_s5(raw_dis_RVTL, lag_frames);
        event_dis_RVTL(event_dis_RVTL==0) = -1;
        event_overlap_LeftLine  = double(shift_signal_to_s5(raw_overlap_LeftLine, lag_frames) ~= 0);
        event_overlap_RightLine = double(shift_signal_to_s5(raw_overlap_RightLine, lag_frames) ~= 0);

        event_RVTL_index        = round(shift_signal_to_s5(raw_RVTL_index, lag_frames));
        % 默认：不存在RVTL => -1
        event_RVTL_Velocity = choose_s5_rvtl_velocity(out_dir, j, n_event, ...
            event_RVTL_index, raw_Obj_Velocity);
        % 合法索引：1..30
        valid = (event_RVTL_index >= 1) & (event_RVTL_index <= 30) & ~isnan(event_RVTL_index);
        % 将合法索引转成线性索引并取值
        Thres_TTC_FV = 1.8 * ones(size(event_time,1), 1);
        Thres_dis_RVTL = -3.4 * (event_Ego_velocity-event_RVTL_Velocity) + 13.6;
        Thres_dis_RVTL(~valid) = -1;


        T = table( ...
            event_time, event_trigger_IMR_44_1, event_com_IMR_44_1, event_com_IMR_44_1_RVTL, ...
            event_com_IMR_44_1_FV, event_Ego_velocity, event_RVTL_Velocity, ...
            event_overlap_LeftLine, event_overlap_RightLine, event_TTC_FV, ...
            event_dis_RVTL, Thres_TTC_FV, Thres_dis_RVTL, ...
            'VariableNames', {'event_time','trigger_IMR_44_1','com_IMR_44_1','com_IMR_44_1_RVTL', ...
            'com_IMR_44_1_FV','Ego_velocity','RVTL_Velocity', ...
            'overlap_LeftLine', 'overlap_RightLine', 'TTC_FV', ...
            'dis_RVTL', 'Thres_TTC_FV', 'Thres_dis_RVTL'} ...
        );

        % ---- 写CSV到对应event目录 ----
        % 建议文件名：lane_change_event_<n>_EvidenceChain.csv
        event_name = ['lane_change_event_', num2str(j), '_EvidenceChain.csv'];
        csv_path = fullfile(out_dir, event_name);
        writetable(T, csv_path);
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

function lag_frames = infer_s5_lag(overlap_left, overlap_right, map_info)
side = overlap_side(overlap_left, overlap_right);
sim_switch_idx = find((side(2:end) ~= side(1:end-1)) & ...
    ((side(2:end) ~= 0) | (side(1:end-1) ~= 0))) + 1;

map_switch_idx = find_map_switch_idx(map_info);
if isempty(sim_switch_idx) || isempty(map_switch_idx)
    lag_frames = 0;
    return;
end

matched_lags = [];
for k = 1:numel(map_switch_idx)
    [gap, nearest_pos] = min(abs(sim_switch_idx - map_switch_idx(k)));
    if gap <= 5
        matched_lags(end+1,1) = sim_switch_idx(nearest_pos) - map_switch_idx(k); %#ok<AGROW>
    end
end

if isempty(matched_lags)
    lag_frames = 0;
else
    lag_frames = round(median(matched_lags));
    lag_frames = max(min(lag_frames, 5), -5);
end
end

function predicate_lag = infer_predicate_lag(overlap_left, overlap_right, ...
    trigger_IMR_44_1, com_IMR_44_1, com_IMR_44_1_RVTL, com_IMR_44_1_FV)
side = overlap_side(overlap_left, overlap_right);
overlap_switch_idx = find((side(2:end) ~= side(1:end-1)) & ...
    ((side(2:end) ~= 0) | (side(1:end-1) ~= 0))) + 1;

predicate_switch_idx = unique([
    find_signal_switch_idx(trigger_IMR_44_1)
    find_signal_switch_idx(com_IMR_44_1)
    find_signal_switch_idx(com_IMR_44_1_RVTL)
    find_signal_switch_idx(com_IMR_44_1_FV)
    ]);

if isempty(overlap_switch_idx) || isempty(predicate_switch_idx)
    predicate_lag = 0;
    return;
end

matched_lags = [];
for k = 1:numel(overlap_switch_idx)
    [gap, nearest_pos] = min(abs(predicate_switch_idx - overlap_switch_idx(k)));
    if gap <= 5
        matched_lags(end+1,1) = predicate_switch_idx(nearest_pos) - overlap_switch_idx(k); %#ok<AGROW>
    end
end

if isempty(matched_lags)
    predicate_lag = 0;
else
    predicate_lag = round(median(matched_lags));
    predicate_lag = max(min(predicate_lag, 5), -5);
end
end

function side = overlap_side(overlap_left, overlap_right)
left = double(overlap_left(:) ~= 0);
right = double(overlap_right(:) ~= 0);
side = zeros(size(left));
side(left ~= 0 & right == 0) = 1;
side(right ~= 0 & left == 0) = -1;
end

function switch_idx = find_signal_switch_idx(signal)
signal = double(signal(:));
if numel(signal) < 2
    switch_idx = [];
else
    switch_idx = find(signal(2:end) ~= signal(1:end-1)) + 1;
end
end

function map_switch_idx = find_map_switch_idx(map_info)
required = {'MAP_C0_Left1', 'MAP_C0_Right1'};
if ~all(ismember(required, map_info.Properties.VariableNames))
    map_switch_idx = [];
    return;
end

left_c0 = table_column_to_double(map_info.MAP_C0_Left1);
right_c0 = table_column_to_double(map_info.MAP_C0_Right1);
if numel(left_c0) < 2 || numel(right_c0) < 2
    map_switch_idx = [];
    return;
end

jump_left = abs(diff(left_c0));
jump_right = abs(diff(right_c0));
map_switch_idx = find(jump_left > 1.0 & jump_right > 1.0) + 1;
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

function event_Ego_velocity = choose_s5_velocity(out_dir, event_num, n_event, sim_velocity)
event_Ego_velocity = sim_velocity(:);
ego_path = fullfile(out_dir, ['lane_change_event_', num2str(event_num), '_EgoInfo.csv']);
if ~exist(ego_path, 'file')
    return;
end

ego_info = readtable(ego_path);
velocity_names = {'Ego_vx', 'Ego_velocity', 'Ego_INS_Velocity'};
velocity_name = '';
for k = 1:numel(velocity_names)
    if ismember(velocity_names{k}, ego_info.Properties.VariableNames)
        velocity_name = velocity_names{k};
        break;
    end
end

if height(ego_info) ~= n_event || isempty(velocity_name)
    return;
end

s5_velocity = table_column_to_double(ego_info.(velocity_name));
if numel(s5_velocity) == n_event && any(isfinite(s5_velocity) & abs(s5_velocity) > 0.1)
    event_Ego_velocity = s5_velocity(:);
end
end

function event_RVTL_Velocity = choose_s5_rvtl_velocity(out_dir, event_num, n_event, rvtl_index, sim_obj_velocity)
event_RVTL_Velocity = compute_rvtl_velocity(sim_obj_velocity, rvtl_index);
obj_path = fullfile(out_dir, ['lane_change_event_', num2str(event_num), '_ObjInfo.csv']);
if ~exist(obj_path, 'file')
    return;
end

obj_info = readtable(obj_path);
if height(obj_info) ~= n_event
    return;
end

obj_speed = read_s5_obj_speed(obj_info, n_event);
if ~isempty(obj_speed)
    event_RVTL_Velocity = compute_rvtl_velocity(obj_speed, rvtl_index);
end
end

function event_RVTL_Velocity = compute_rvtl_velocity(obj_velocity, rvtl_index)
rvtl_index = rvtl_index(:);
event_RVTL_Velocity = -1 * ones(size(rvtl_index,1), 1);
valid = (rvtl_index >= 1) & (rvtl_index <= size(obj_velocity, 2)) & ~isnan(rvtl_index);
rows = (1:size(rvtl_index,1))';
lin = sub2ind(size(obj_velocity), rows(valid), rvtl_index(valid));
event_RVTL_Velocity(valid) = obj_velocity(lin);
end

function obj_speed = read_s5_obj_speed(obj_info, n_event)
obj_speed = [];
for k = 1:30
    name = sprintf('Obj%02d_Speed', k);
    if ~ismember(name, obj_info.Properties.VariableNames)
        return;
    end
    obj_speed(:,k) = table_column_to_double(obj_info.(name)); %#ok<AGROW>
end

if size(obj_speed, 1) ~= n_event
    obj_speed = [];
end
end

function col = table_column_to_double(col)
if iscell(col) || isstring(col) || ischar(col)
    col = str2double(string(col));
end
col = double(col(:));
end
