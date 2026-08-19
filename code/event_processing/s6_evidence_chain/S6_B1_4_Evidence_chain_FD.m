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

    Event_record_root = fullfile(date_path, 'zEvent_FollowDis');
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
    sim_root = fullfile(date_path, 'zEvent_FollowDis');
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
        Event_record_path = fullfile(Event_path, 'FollowDis_events.csv');
        if ~exist(Event_record_path,'file')
            continue;
        end

        % 输出目录：...\YYYYMMDD\zEvent_FollowDis\<segment>\
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
fprintf('Found %d FollowDis_events.csv files.\n', numEvents);
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
    seg_trigger_IMR_80_1 = seg_data.Trigger_IMR_80_1;
    seg_trigger_IMR_80_2 = seg_data.Trigger_IMR_80_2;
    seg_com_IMR_80_1     = seg_data.Com_IMR_80_1;
    seg_com_IMR_80_2     = seg_data.Com_IMR_80_2;
    seg_Ego_velocity     = seg_data.INS_VelocitySpeed;
    seg_Road_type        = seg_data.MAP_RoadType_Curr;
    seg_Lane_type        = seg_data.LaneType;
    seg_Dis_FV           = seg_data.Dis_FronVeh;
    seg_Congestion       = seg_data.Congestion;


    event_data = readtable(event_path);
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
        event_time              = 0.01*(1:event_data.len(j))';
        raw_trigger_IMR_80_1    = seg_trigger_IMR_80_1(event_start_idx:event_end_idx);
        raw_trigger_IMR_80_2    = seg_trigger_IMR_80_2(event_start_idx:event_end_idx);
        raw_com_IMR_80_1        = seg_com_IMR_80_1(event_start_idx:event_end_idx);
        raw_com_IMR_80_2        = seg_com_IMR_80_2(event_start_idx:event_end_idx);
        event_Ego_velocity      = seg_Ego_velocity(event_start_idx:event_end_idx) / 3.6;
        event_Road_type         = seg_Road_type(event_start_idx:event_end_idx);
        event_Lane_type         = seg_Lane_type(event_start_idx:event_end_idx);
        raw_Dis_FV              = seg_Dis_FV(event_start_idx:event_end_idx);

        event_trigger_IMR_80_1  = shift_signal_to_s5(raw_trigger_IMR_80_1, 1);
        event_trigger_IMR_80_2  = shift_signal_to_s5(raw_trigger_IMR_80_2, 1);
        event_com_IMR_80_1      = shift_signal_to_s5(raw_com_IMR_80_1, 1);
        event_com_IMR_80_2      = shift_signal_to_s5(raw_com_IMR_80_2, 1);
        event_Dis_FV            = shift_signal_to_s5(raw_Dis_FV, 1);
        event_Dis_FV(event_Dis_FV==0) = -1;
        event_Congestion        = seg_Congestion(event_start_idx:event_end_idx);

        Thres_Dis_FV = zeros(size(event_time,1), 1);
        Thres_Dis_FV(event_Ego_velocity>=100/3.6) = 100;
        Thres_Dis_FV(event_Ego_velocity<100/3.6) = 50;



        T = table( ...
            event_time, event_trigger_IMR_80_1, event_trigger_IMR_80_2, event_com_IMR_80_1, event_com_IMR_80_2, ...
            event_Ego_velocity, event_Road_type, event_Lane_type, event_Congestion, ...
            event_Dis_FV, Thres_Dis_FV, ...
            'VariableNames', {'event_time','trigger_IMR_80_1','trigger_IMR_80_2','com_IMR_80_1', ...
            'com_IMR_80_2','Ego_velocity','Road_type', 'Lane_type', ...
            'Congestion', 'Dis_FV', 'Thres_Dis_FV'} ...
        );

        % ---- 写CSV到对应event目录 ----
        % 建议文件名：FollowDis_event_<n>_EvidenceChain.csv
        event_name = ['FollowDis_event_', num2str(j), '_EvidenceChain.csv'];
        csv_path = fullfile(out_dir, event_name);
        writetable(T, csv_path);
        fprintf('[%d/%d] File "%s" has been written successfully!\n', j, num_event, event_name);

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
