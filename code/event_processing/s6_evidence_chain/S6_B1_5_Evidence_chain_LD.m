clear; clc
script_dir = fileparts(mfilename('fullpath'));
if isempty(script_dir)
    script_dir = pwd;
end
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) 根目录：Data_Nanjing_collection =========
main_folder = getenv('TLCD_DATA_ROOT');
if isempty(main_folder)
    error('Set TLCD_DATA_ROOT to the city-level source directory.');
end

% ========= 2) 搜索所有 S3生成的 monitor_result.mat、all.mat 以及对应 S4生成的 csv =========
date_dirs = dir(main_folder);
date_dirs = date_dirs([date_dirs.isdir]);
date_dirs = date_dirs(~ismember({date_dirs.name},{'.','..'}));

allMatPaths = {};
meta = struct('date_name',{}, 'segment_name',{}, 'monitor_result_path',{}, 'allmat_path',{}, 'out_dir',{});

allEventPaths = {};
meta2 = struct('date_name',{}, 'segment_name',{}, 'event_path',{}, 'out_dir',{});

for d = 1:numel(date_dirs)
    date_name = date_dirs(d).name;
    date_path = fullfile(date_dirs(d).folder, date_name);

    % 只处理 8 位数字日期目录
    if length(date_name) ~= 8 || any(~isstrprop(date_name,'digit'))
        continue;
    end
    %
    % if ~strcmp(date_name, '20241025')
    %     continue
    % end

    monitor_result_root = fullfile(date_path, 'sim_result');
    if ~exist(monitor_result_root, 'dir')
        continue;
    end

    allmat_root = fullfile(date_path, 'mat');
    if ~exist(allmat_root, 'dir')
        continue;
    end

    event_record_root = fullfile(date_path, 'zEvent_LateralDis');
    if ~exist(event_record_root, 'dir')
        continue;
    end

    seg_dirs = dir(monitor_result_root);
    seg_dirs = seg_dirs([seg_dirs.isdir]);
    seg_dirs = seg_dirs(~ismember({seg_dirs.name},{'.','..'}));

    sim_root = event_record_root;
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

        allmat_path = fullfile(allmat_root, segment_name, 'all.mat');
        if ~exist(allmat_path,'file')
            continue;
        end

        event_path = fullfile(event_record_root, segment_name);
        event_record_path = fullfile(event_path, 'LateralDis_events.csv');
        if ~exist(event_record_path,'file')
            continue;
        end

        out_dir = fullfile(sim_root, segment_name);
        if ~exist(out_dir,'dir')
            mkdir(out_dir);
        end

        allMatPaths{end+1,1} = monitor_result_path; %#ok<AGROW>
        meta(end+1).date_name = date_name; %#ok<AGROW>
        meta(end).segment_name = segment_name;
        meta(end).monitor_result_path = monitor_result_path;
        meta(end).allmat_path = allmat_path;
        meta(end).out_dir = out_dir;

        allEventPaths{end+1,1} = event_record_path; %#ok<AGROW>
        meta2(end+1).date_name = date_name; %#ok<AGROW>
        meta2(end).segment_name = segment_name;
        meta2(end).event_path = event_record_path;
        meta2(end).out_dir = out_dir;
    end
end

numMats = numel(allMatPaths);
numEvents = numel(allEventPaths);
fprintf('Found %d matched monitor_result.mat/all.mat files.\n', numMats);
fprintf('Found %d LateralDis_events.csv files.\n', numEvents);
if numMats == 0
    error('未找到任何 monitor_result.mat/all.mat：请确认目录结构为 YYYYMMDD\\sim_result\\<segment>\\monitor_result.mat 和 YYYYMMDD\\mat\\<segment>\\all.mat');
end

if numMats ~= numEvents
    error('monitor_result.mat/all.mat匹配数量与csv事件数量不匹配，请检查');
end


% ========= 3) 分割每一个5min segment 中记录的每一个 event =========
% ========= 4) 单独保存每一个 event 的关键证据链           =========
for i = 1:numMats
    % ---- 加载数据 ----
    monitor_result_path = meta(i).monitor_result_path;
    event_path = meta2(i).event_path;
    out_dir  = meta2(i).out_dir;
    fprintf('[%d/%d] Preparing: %s\n', i, numMats, event_path);

    S_monitor = load(monitor_result_path);
    seg_data = S_monitor.sim_output;
    % ---- 读取关键命题、关键中间状态量 ----
    seg_trigger_OSP_8_2_1 = seg_data.Trigger_OSP_8_2_1(:);
    seg_com_OSP_8_2_1     = seg_data.Com_OSP_8_2_1(:);
    seg_Dis_LV            = reduce_side_vehicle_distance(seg_data.YDis_LeftSideVeh);
    seg_Dis_RV            = reduce_side_vehicle_distance(seg_data.YDis_RightSideVeh);
    if isstruct(seg_data)
        sim_output_names = fieldnames(seg_data);
    else
        sim_output_names = seg_data.who;
    end
    if ismember('Dis_centerline', sim_output_names)
        seg_Dis_centerline = seg_data.Dis_centerline(:);
    elseif ismember('dis_centerline', sim_output_names)
        seg_Dis_centerline = seg_data.dis_centerline(:);
    else
        error('Missing centerline distance signal in monitor_result.mat: %s', monitor_result_path);
    end

    Thres_min_LatDis_value = 1.5;
    Thres_Offset_centerline_value = 0.375;

    opts = detectImportOptions(event_path, 'TextType', 'string');
    event_data = readtable(event_path, opts);
    required_event_vars = {'start_idx','end_idx','len'};
    if ~all(ismember(required_event_vars, event_data.Properties.VariableNames))
        fprintf('  Skipped empty/invalid event file: %s\n', event_path);
        continue;
    end
    valid_event_rows = isfinite(event_data.start_idx) & isfinite(event_data.end_idx) & isfinite(event_data.len);
    if any(~valid_event_rows)
        fprintf('  Skipped %d empty/invalid event rows in %s\n', sum(~valid_event_rows), event_path);
        event_data = event_data(valid_event_rows,:);
    end

    % ---- 每个事件单独处理 ----
    num_event = size(event_data,1);
    for j = 1:num_event
        event_start_idx = event_data.start_idx(j);
        event_end_idx   = event_data.end_idx(j);
        idx = event_start_idx:event_end_idx;
        n_event = numel(idx);

        map_path = fullfile(out_dir, ['LateralDis_event_', num2str(j), '_MapInfo.csv']);
        if ~exist(map_path, 'file')
            error('Missing S5 MapInfo for lateral-distance event %d: %s', j, map_path);
        end
        map_info = readtable(map_path);
        if height(map_info) ~= n_event
            error('S5 MapInfo row count mismatch for lateral-distance event %d: event len=%d, MapInfo=%d', ...
                j, n_event, height(map_info));
        end
        event_time = read_event_time_from_s5(map_info, n_event);

        lag_frames = 1;

        event_trigger_OSP_8_2_1 = double(shift_signal_to_s5(seg_trigger_OSP_8_2_1(idx), lag_frames) ~= 0);
        event_com_OSP_8_2_1     = shift_signal_to_s5(seg_com_OSP_8_2_1(idx), lag_frames);
        event_com_OSP_8_2_1(event_trigger_OSP_8_2_1 == 0) = 0;
        event_Dis_LV            = shift_signal_to_s5(seg_Dis_LV(idx), lag_frames);
        event_Dis_RV            = shift_signal_to_s5(seg_Dis_RV(idx), lag_frames);
        event_Dis_centerline    = shift_signal_to_s5(seg_Dis_centerline(idx), lag_frames);
        event_Exist_LeftLine    = table_column_to_double(map_info.MAP_C0_Left1) ~= 0;
        event_Exist_RightLine   = table_column_to_double(map_info.MAP_C0_Right1) ~= 0;
        event_Is_Lat_avoidance  = build_lat_avoidance( ...
            event_Dis_LV, event_Dis_RV, event_Exist_LeftLine, event_Exist_RightLine, ...
            event_Dis_centerline, Thres_Offset_centerline_value);
        event_com_OSP_8_2_1(event_Is_Lat_avoidance) = 1;

        Thres_min_LatDis = Thres_min_LatDis_value * ones(size(event_time,1), 1);
        Thres_Offset_centerline = Thres_Offset_centerline_value * ones(size(event_time,1), 1);

        T = table( ...
            event_time, event_trigger_OSP_8_2_1, event_com_OSP_8_2_1, ...
            event_Dis_LV, event_Dis_RV, event_Exist_LeftLine, event_Exist_RightLine, ...
            event_Dis_centerline, event_Is_Lat_avoidance, ...
            Thres_min_LatDis, Thres_Offset_centerline, ...
            'VariableNames', {'event_time','trigger_OSP_8_2_1','com_OSP_8_2_1', ...
            'Dis_LV','Dis_RV','Exist_LeftLine','Exist_RightLine', ...
            'Dis_centerline','Is_Lat_avoidance', ...
            'Thres_min_LatDis','Thres_Offset_centerline'} ...
        );

        % ---- 写CSV到对应event目录 ----
        event_name = ['LateralDis_event_', num2str(j), '_EvidenceChain.csv'];
        csv_path = fullfile(out_dir, event_name);
        writetable(T, csv_path, 'Encoding', 'UTF-8');
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

function aligned = shift_signal_to_s5(raw_signal, lag_frames)
aligned = raw_signal(:);
if lag_frames <= 0 || isempty(aligned)
    return;
end

n = numel(aligned);
if lag_frames >= n
    aligned(:) = aligned(end);
    return;
end

aligned(1:n-lag_frames) = aligned(1+lag_frames:n);
aligned(n-lag_frames+1:n) = aligned(n-lag_frames);
end

function is_lat_avoidance = build_lat_avoidance(dis_lv, dis_rv, exist_left, exist_right, dis_centerline, threshold)
is_lat_avoidance = false(size(dis_centerline));
is_lat_avoidance = is_lat_avoidance | ...
    ((dis_lv ~= -1) & exist_left & exist_right & (dis_centerline > threshold));
is_lat_avoidance = is_lat_avoidance | ...
    ((dis_rv ~= -1) & exist_left & exist_right & (dis_centerline < -threshold));
end

function col = table_column_to_double(col)
if iscell(col) || isstring(col) || ischar(col)
    col = str2double(string(col));
end
col = double(col(:));
end

function dis_side_veh = reduce_side_vehicle_distance(raw_dis)
% 将 1*3*n 或 n*3 横向相邻车距离转换为 n*1，每帧取最小有效距离。
raw_size = size(raw_dis);
if ndims(raw_dis) == 3 && raw_size(1) == 1 && raw_size(2) == 3
    dis = squeeze(raw_dis(1,:,:));
    if isvector(dis)
        dis = dis(:).';
    else
        dis = dis.';
    end
else
    dis = squeeze(raw_dis);
    if isvector(dis)
        dis = dis(:).';
    elseif size(dis, 2) ~= 3 && size(dis, 1) == 3
        dis = dis.';
    end
end

if size(dis, 2) ~= 3
    error('reduce_side_vehicle_distance:UnexpectedSize', ...
        'Expected side vehicle distance to be 1x3xn or nx3, got size [%s].', ...
        num2str(size(raw_dis)));
end

dis(dis >= 999) = NaN;
dis_side_veh = min(dis, [], 2, 'omitnan');
dis_side_veh(isnan(dis_side_veh)) = -1;
end
