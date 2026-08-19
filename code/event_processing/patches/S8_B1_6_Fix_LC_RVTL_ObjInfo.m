clear; clc
script_dir = fileparts(mfilename('fullpath'));
if isempty(script_dir)
    script_dir = pwd;
end
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) Root directory =========
main_folder = 'Z:\HongqiData\Changchun
fprintf('WARNING: Run this one-time migration only once for each source EvidenceChain file.\n');

% ========= 2) Find lane_change_events.csv files =========
date_dirs = dir(main_folder);
date_dirs = date_dirs([date_dirs.isdir]);
date_dirs = date_dirs(~ismember({date_dirs.name},{'.','..'}));
target_dates = parse_target_dates();

meta = struct('date_name',{}, 'segment_name',{}, 'event_path',{}, 'out_dir',{});

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

    seg_dirs = dir(event_root);
    seg_dirs = seg_dirs([seg_dirs.isdir]);
    seg_dirs = seg_dirs(~ismember({seg_dirs.name},{'.','..'}));

    for s = 1:numel(seg_dirs)
        segment_name = seg_dirs(s).name;
        segment_path = fullfile(seg_dirs(s).folder, segment_name);
        event_path = fullfile(segment_path, 'lane_change_events.csv');
        if ~exist(event_path, 'file')
            continue;
        end

        next_idx = numel(meta) + 1;
        meta(next_idx).date_name = date_name;
        meta(end).segment_name = segment_name;
        meta(end).event_path = event_path;
        meta(end).out_dir = segment_path;
    end
end

num_event_files = numel(meta);
fprintf('Found %d lane_change_events.csv files.\n', num_event_files);
if num_event_files == 0
    error('No lane_change_events.csv files found. Expected YYYYMMDD\\zEvent_LaneChange\\<segment>\\lane_change_events.csv.');
end

% ========= 3) Shift predicates and patch obstacle boundary frames =========
total_events = 0;
total_evidence = 0;
total_changed_files = 0;
total_changed_rows = 0;
total_missing_evidence = 0;
total_missing_obj = 0;

for i = 1:num_event_files
    event_path = meta(i).event_path;
    out_dir = meta(i).out_dir;

    fprintf('[%d/%d] Processing: %s\n', i, num_event_files, event_path);

    event_opts = detectImportOptions(event_path, 'TextType', 'string');
    event_data = readtable(event_path, event_opts);
    num_event = height(event_data);
    total_events = total_events + num_event;

    changed_files = 0;
    changed_rows = 0;
    missing_evidence = 0;
    missing_obj = 0;

    for j = 1:num_event
        evidence_name = sprintf('lane_change_event_%d_EvidenceChain.csv', j);
        evidence_path = fullfile(out_dir, evidence_name);
        obj_path = fullfile(out_dir, sprintf('lane_change_event_%d_ObjInfo.csv', j));

        if ~exist(evidence_path, 'file')
            missing_evidence = missing_evidence + 1;
            continue;
        end
        if ~exist(obj_path, 'file')
            missing_obj = missing_obj + 1;
            continue;
        end

        evidence_opts = detectImportOptions(evidence_path, 'TextType', 'string');
        evidence_data = readtable(evidence_path, evidence_opts);
        obj_opts = detectImportOptions(obj_path, 'TextType', 'string');
        obj_info = readtable(obj_path, obj_opts);
        total_evidence = total_evidence + 1;

        if height(evidence_data) ~= height(obj_info)
            fprintf('  Skip %s: row count mismatch EvidenceChain=%d, ObjInfo=%d.\n', ...
                evidence_name, height(evidence_data), height(obj_info));
            continue;
        end

        [evidence_data, this_changed_rows] = fix_lanechange_rvtl_frames(evidence_data, obj_info);
        if this_changed_rows > 0
            write_event_table(evidence_data, evidence_path);
            changed_files = changed_files + 1;
            changed_rows = changed_rows + this_changed_rows;
            fprintf('  Corrected %s: %d rows.\n', evidence_name, this_changed_rows);
        end
    end

    total_changed_files = total_changed_files + changed_files;
    total_changed_rows = total_changed_rows + changed_rows;
    total_missing_evidence = total_missing_evidence + missing_evidence;
    total_missing_obj = total_missing_obj + missing_obj;

    fprintf('  Summary: changed files=%d, changed rows=%d, missing EvidenceChain=%d, missing ObjInfo=%d.\n', ...
        changed_files, changed_rows, missing_evidence, missing_obj);
end

fprintf('Done. Events checked: %d, EvidenceChain files checked: %d, files changed: %d, rows changed: %d, missing EvidenceChain files: %d, missing ObjInfo files: %d.\n', ...
    total_events, total_evidence, total_changed_files, total_changed_rows, total_missing_evidence, total_missing_obj);

function target_dates = parse_target_dates()
target_text = strtrim(string(getenv('TLCD_TARGET_DATES')));
if strlength(target_text) == 0
    target_dates = strings(0, 1);
else
    target_dates = strtrim(split(target_text, ','));
    target_dates = target_dates(strlength(target_dates) > 0);
end
end

function [T, changed_rows] = fix_lanechange_rvtl_frames(T, obj_info)
changed = false(height(T), 1);
required_cols = {'trigger_IMR_44_1', 'com_IMR_44_1', 'com_IMR_44_1_RVTL', ...
    'com_IMR_44_1_FV', 'RVTL_Velocity', 'overlap_LeftLine', ...
    'overlap_RightLine', 'TTC_FV', 'dis_RVTL', 'Thres_dis_RVTL'};
if isempty(T) || ~all(ismember(required_cols, T.Properties.VariableNames))
    changed_rows = 0;
    return;
end

original_rvtl_velocity = to_numeric_vector(T.RVTL_Velocity);

obj_speed = read_obj_matrix(obj_info, 'Speed');
obj_distance_x = read_obj_matrix(obj_info, 'DistanceX');
if isempty(obj_speed) || isempty(obj_distance_x)
    changed_rows = 0;
    return;
end

runs = find_true_runs(to_numeric_vector(T.trigger_IMR_44_1) ~= 0);
for k = 1:size(runs, 1)
    s = runs(k, 1);
    e = runs(k, 2);

    [T, changed] = shift_predicates_down_one(T, changed, s, e);
    [T, changed] = remove_first_overlap_frame(T, changed, s, e);

    shifted_trigger = to_numeric_vector(T.trigger_IMR_44_1) ~= 0;
    shifted_idx = find(shifted_trigger(s:e)) + s - 1;
    if isempty(shifted_idx)
        continue;
    end

    shifted_s = shifted_idx(1);
    shifted_e = shifted_idx(end);
    rvtl_velocity = to_numeric_vector(T.RVTL_Velocity);
    dis_rvtl = to_numeric_vector(T.dis_RVTL);
    valid_rvtl = rvtl_velocity(shifted_s:shifted_e) ~= -1 | dis_rvtl(shifted_s:shifted_e) ~= -1;
    if any(valid_rvtl)
        first_valid = shifted_s + find(valid_rvtl, 1, 'first') - 1;
        last_valid = shifted_s + find(valid_rvtl, 1, 'last') - 1;

        if first_valid > shifted_s
            obj_idx = infer_rvtl_obj_index(first_valid, rvtl_velocity, dis_rvtl, obj_speed, obj_distance_x);
            fill_idx = shifted_s:first_valid-1;
            [T, changed] = fill_rvtl_from_obj(T, changed, fill_idx, obj_idx, obj_speed, obj_distance_x);
        end

        if last_valid < shifted_e
            obj_idx = infer_rvtl_obj_index(last_valid, rvtl_velocity, dis_rvtl, obj_speed, obj_distance_x);
            fill_idx = last_valid+1:shifted_e;
            [T, changed] = fill_rvtl_from_obj(T, changed, fill_idx, obj_idx, obj_speed, obj_distance_x);
        end
    end

    clear_idx = shifted_e + 1;
    if clear_idx <= height(T) && ~shifted_trigger(clear_idx)
        [T, changed] = clear_obstacle_row(T, changed, clear_idx);
    end
end

current_rvtl_velocity = to_numeric_vector(T.RVTL_Velocity);
rvtl_changed = original_rvtl_velocity ~= current_rvtl_velocity;
T = recompute_rvtl_threshold(T, rvtl_changed);
changed_rows = sum(changed);
end

function [T, changed] = shift_predicates_down_one(T, changed, s, e)
predicate_cols = {'trigger_IMR_44_1', 'com_IMR_44_1', ...
    'com_IMR_44_1_RVTL', 'com_IMR_44_1_FV'};

for k = 1:numel(predicate_cols)
    col_name = predicate_cols{k};
    col = to_numeric_vector(T.(col_name));
    old_segment = col(s:e);
    new_segment = [0; old_segment(1:end-1)];
    row_changed = old_segment ~= new_segment;
    col(s:e) = new_segment;
    T.(col_name) = col;
    changed(find(row_changed) + s - 1) = true;
end
end

function [T, changed] = remove_first_overlap_frame(T, changed, s, e)
overlap_left = to_numeric_vector(T.overlap_LeftLine);
overlap_right = to_numeric_vector(T.overlap_RightLine);
first_overlap = find(overlap_left(s:e) ~= 0 | overlap_right(s:e) ~= 0, 1, 'first');
if isempty(first_overlap)
    return;
end

row_idx = s + first_overlap - 1;
if overlap_left(row_idx) ~= 0 || overlap_right(row_idx) ~= 0
    overlap_left(row_idx) = 0;
    overlap_right(row_idx) = 0;
    T.overlap_LeftLine = overlap_left;
    T.overlap_RightLine = overlap_right;
    changed(row_idx) = true;
end
end

function [T, changed] = fill_rvtl_from_obj(T, changed, rows, obj_idx, obj_speed, obj_distance_x)
if isempty(rows) || isnan(obj_idx) || obj_idx < 1 || obj_idx > size(obj_speed, 2)
    return;
end

rows = rows(:);
new_velocity = obj_speed(rows, obj_idx);
new_distance = abs(obj_distance_x(rows, obj_idx));
valid_obj = isfinite(new_velocity) & isfinite(new_distance) & new_velocity ~= 0 & new_distance ~= 0;
rows = rows(valid_obj);
new_velocity = new_velocity(valid_obj);
new_distance = new_distance(valid_obj);

if isempty(rows)
    return;
end

old_velocity = to_numeric_vector(T.RVTL_Velocity);
old_distance = to_numeric_vector(T.dis_RVTL);
row_changed = old_velocity(rows) ~= new_velocity | old_distance(rows) ~= new_distance;

old_velocity(rows) = new_velocity;
old_distance(rows) = new_distance;

T.RVTL_Velocity = old_velocity;
T.dis_RVTL = old_distance;
changed(rows(row_changed)) = true;
end

function [T, changed] = clear_obstacle_row(T, changed, row_idx)
cols = {'RVTL_Velocity', 'TTC_FV', 'dis_RVTL', 'Thres_dis_RVTL'};
for k = 1:numel(cols)
    col_name = cols{k};
    if ~ismember(col_name, T.Properties.VariableNames)
        continue;
    end
    col = to_numeric_vector(T.(col_name));
    if col(row_idx) ~= -1
        col(row_idx) = -1;
        T.(col_name) = col;
        changed(row_idx) = true;
    end
end
end

function T = recompute_rvtl_threshold(T, rvtl_changed)
if ~all(ismember({'Ego_velocity', 'RVTL_Velocity', 'Thres_dis_RVTL'}, T.Properties.VariableNames))
    return;
end

ego_velocity = to_numeric_vector(T.Ego_velocity);
rvtl_velocity = to_numeric_vector(T.RVTL_Velocity);
threshold = to_numeric_vector(T.Thres_dis_RVTL);
threshold(rvtl_changed) = -3.4 * (ego_velocity(rvtl_changed) - rvtl_velocity(rvtl_changed)) + 13.6;
invalid = rvtl_changed & (rvtl_velocity == -1 | ~isfinite(rvtl_velocity));
threshold(invalid) = -1;
T.Thres_dis_RVTL = threshold;
end

function obj_idx = infer_rvtl_obj_index(row_idx, rvtl_velocity, dis_rvtl, obj_speed, obj_distance_x)
obj_idx = NaN;
speed_row = obj_speed(row_idx, :);
distance_row = abs(obj_distance_x(row_idx, :));

speed_target = rvtl_velocity(row_idx);
distance_target = dis_rvtl(row_idx);

valid = isfinite(speed_row) & isfinite(distance_row) & speed_row ~= 0 & distance_row ~= 0;
if ~any(valid)
    return;
end

score = inf(size(speed_row));
if isfinite(speed_target) && speed_target ~= -1
    score(valid) = abs(speed_row(valid) - speed_target);
end
if isfinite(distance_target) && distance_target ~= -1
    score(valid) = score(valid) + abs(distance_row(valid) - distance_target);
end

[best_score, best_idx] = min(score);
if isfinite(best_score) && best_score < 0.5
    obj_idx = best_idx;
end
end

function runs = find_true_runs(mask)
mask = mask(:) ~= 0;
edges = diff([false; mask; false]);
starts = find(edges == 1);
ends = find(edges == -1) - 1;
runs = [starts, ends];
end

function obj_mat = read_obj_matrix(obj_info, suffix)
obj_mat = [];
for k = 1:30
    col_name = sprintf('Obj%02d_%s', k, suffix);
    if ~ismember(col_name, obj_info.Properties.VariableNames)
        obj_mat = [];
        return;
    end
    obj_mat(:, k) = to_numeric_vector(obj_info.(col_name)); %#ok<AGROW>
end
end

function values = to_numeric_vector(values)
if isnumeric(values) || islogical(values)
    values = double(values);
else
    values = str2double(string(values));
end
values = values(:);
end
