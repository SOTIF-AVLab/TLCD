clear; clc
script_dir = fileparts(mfilename('fullpath'));
if isempty(script_dir)
    script_dir = pwd;
end
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) Root directory =========
main_folder = 'Z:\HongqiData\Changchun';

% ========= 2) Find lane_change_events.csv files =========
date_dirs = dir(main_folder);
date_dirs = date_dirs([date_dirs.isdir]);
date_dirs = date_dirs(~ismember({date_dirs.name},{'.','..'}));
target_dates = parse_target_dates();

meta = struct('event_path',{}, 'out_dir',{});

for d = 1:numel(date_dirs)
    date_name = date_dirs(d).name;
    date_path = fullfile(date_dirs(d).folder, date_name);

    if length(date_name) ~= 8 || any(~isstrprop(date_name,'digit'))
        continue;
    end
    if ~isempty(target_dates) && ~ismember(string(date_name), target_dates)
        continue;
    end

    event_root = fullfile(date_path, 'zEvent_LaneChange');
    if ~exist(event_root, 'dir')
        continue;
    end

    segment_dirs = dir(event_root);
    segment_dirs = segment_dirs([segment_dirs.isdir]);
    segment_dirs = segment_dirs(~ismember({segment_dirs.name},{'.','..'}));

    for s = 1:numel(segment_dirs)
        out_dir = fullfile(segment_dirs(s).folder, segment_dirs(s).name);
        event_path = fullfile(out_dir, 'lane_change_events.csv');
        if ~exist(event_path, 'file')
            continue;
        end

        next_idx = numel(meta) + 1;
        meta(next_idx).event_path = event_path;
        meta(end).out_dir = out_dir;
    end
end

fprintf('Found %d lane_change_events.csv files.\n', numel(meta));
if isempty(meta)
    error('No lane_change_events.csv files found.');
end

% ========= 3) Correct the first frame of each trigger interval =========
total_events = 0;
total_evidence = 0;
total_changed_files = 0;
total_changed_rows = 0;
total_missing_evidence = 0;

for i = 1:numel(meta)
    event_opts = detectImportOptions(meta(i).event_path, 'TextType', 'string');
    event_data = readtable(meta(i).event_path, event_opts);
    num_event = height(event_data);
    total_events = total_events + num_event;

    fprintf('[%d/%d] Processing: %s\n', i, numel(meta), meta(i).event_path);

    for j = 1:num_event
        evidence_name = sprintf('lane_change_event_%d_EvidenceChain.csv', j);
        evidence_path = fullfile(meta(i).out_dir, evidence_name);
        if ~exist(evidence_path, 'file')
            total_missing_evidence = total_missing_evidence + 1;
            continue;
        end

        evidence_opts = detectImportOptions(evidence_path, 'TextType', 'string');
        evidence_data = readtable(evidence_path, evidence_opts);
        total_evidence = total_evidence + 1;

        [evidence_data, changed_rows] = correct_first_trigger_judgment(evidence_data);
        if changed_rows > 0
            write_event_table(evidence_data, evidence_path);
            total_changed_files = total_changed_files + 1;
            total_changed_rows = total_changed_rows + changed_rows;
            fprintf('  Corrected %s: %d first-trigger rows.\n', evidence_name, changed_rows);
        end
    end
end

fprintf('Done. Events checked: %d, EvidenceChain files checked: %d, files changed: %d, rows changed: %d, missing EvidenceChain files: %d.\n', ...
    total_events, total_evidence, total_changed_files, total_changed_rows, total_missing_evidence);

function target_dates = parse_target_dates()
target_text = strtrim(string(getenv('TLCD_TARGET_DATES')));
if strlength(target_text) == 0
    target_dates = strings(0, 1);
else
    target_dates = strtrim(split(target_text, ','));
    target_dates = target_dates(strlength(target_dates) > 0);
end
end

function [T, changed_rows] = correct_first_trigger_judgment(T)
required_cols = {'trigger_IMR_44_1', 'com_IMR_44_1', ...
    'com_IMR_44_1_RVTL', 'com_IMR_44_1_FV', ...
    'TTC_FV', 'dis_RVTL', 'Thres_TTC_FV', 'Thres_dis_RVTL'};
if isempty(T) || ~all(ismember(required_cols, T.Properties.VariableNames))
    changed_rows = 0;
    return;
end

trigger = to_numeric_vector(T.trigger_IMR_44_1) ~= 0;
trigger_starts = find(trigger & ~[false; trigger(1:end-1)]);

com_all = to_numeric_vector(T.com_IMR_44_1);
com_rvtl = to_numeric_vector(T.com_IMR_44_1_RVTL);
com_fv = to_numeric_vector(T.com_IMR_44_1_FV);
ttc_fv = to_numeric_vector(T.TTC_FV);
dis_rvtl = to_numeric_vector(T.dis_RVTL);
thres_ttc_fv = to_numeric_vector(T.Thres_TTC_FV);
thres_dis_rvtl = to_numeric_vector(T.Thres_dis_RVTL);

changed = false(height(T), 1);
for k = 1:numel(trigger_starts)
    row_idx = trigger_starts(k);
    old_values = [com_all(row_idx), com_rvtl(row_idx), com_fv(row_idx)];

    if is_valid_measurement(dis_rvtl(row_idx), thres_dis_rvtl(row_idx))
        if dis_rvtl(row_idx) < thres_dis_rvtl(row_idx)
            com_rvtl(row_idx) = -1;
        else
            com_rvtl(row_idx) = 1;
        end
    end

    if is_valid_measurement(ttc_fv(row_idx), thres_ttc_fv(row_idx))
        if ttc_fv(row_idx) < thres_ttc_fv(row_idx)
            com_fv(row_idx) = -1;
        else
            com_fv(row_idx) = 1;
        end
    end

    if com_rvtl(row_idx) < 0 || com_fv(row_idx) < 0
        com_all(row_idx) = -1;
    else
        com_all(row_idx) = 1;
    end

    new_values = [com_all(row_idx), com_rvtl(row_idx), com_fv(row_idx)];
    changed(row_idx) = any(old_values ~= new_values);
end

T.com_IMR_44_1 = com_all;
T.com_IMR_44_1_RVTL = com_rvtl;
T.com_IMR_44_1_FV = com_fv;
changed_rows = sum(changed);
end

function valid = is_valid_measurement(value, threshold)
valid = isfinite(value) && isfinite(threshold) && value ~= -1 && threshold ~= -1;
end

function values = to_numeric_vector(values)
if isnumeric(values) || islogical(values)
    values = double(values);
else
    values = str2double(string(values));
end
values = values(:);
end
