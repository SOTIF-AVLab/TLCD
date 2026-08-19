clear; clc
script_dir = fileparts(mfilename('fullpath'));
if isempty(script_dir)
    script_dir = pwd;
end
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) Root directory =========
main_folder = 'Z:\HongqiData\Changchun';

% ========= 2) Find RoadMarking_events.csv files =========
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

    event_root = fullfile(date_path, 'zEvent_RoadMarking');
    if ~exist(event_root, 'dir')
        continue;
    end

    seg_dirs = dir(event_root);
    seg_dirs = seg_dirs([seg_dirs.isdir]);
    seg_dirs = seg_dirs(~ismember({seg_dirs.name},{'.','..'}));

    for s = 1:numel(seg_dirs)
        segment_name = seg_dirs(s).name;
        segment_path = fullfile(seg_dirs(s).folder, segment_name);
        event_path = fullfile(segment_path, 'RoadMarking_events.csv');
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

numEventsFiles = numel(meta);
fprintf('Found %d RoadMarking_events.csv files.\n', numEventsFiles);
if numEventsFiles == 0
    error('No RoadMarking_events.csv files found. Expected YYYYMMDD\\zEvent_RoadMarking\\<segment>\\RoadMarking_events.csv.');
end

% ========= 3) Correct lanechange_stage from overlap columns =========
total_events = 0;
total_evidence = 0;
total_changed_files = 0;
total_changed_rows = 0;
total_missing_evidence = 0;

for i = 1:numEventsFiles
    event_path = meta(i).event_path;
    out_dir = meta(i).out_dir;

    fprintf('[%d/%d] Processing: %s\n', i, numEventsFiles, event_path);

    event_opts = detectImportOptions(event_path, 'TextType', 'string');
    event_data = readtable(event_path, event_opts);
    num_event = height(event_data);
    total_events = total_events + num_event;

    changed_files = 0;
    changed_rows = 0;
    missing_evidence = 0;

    for j = 1:num_event
        evidence_name = sprintf('RoadMarking_event_%d_EvidenceChain.csv', j);
        evidence_path = fullfile(out_dir, evidence_name);

        if ~exist(evidence_path, 'file')
            missing_evidence = missing_evidence + 1;
            continue;
        end

        evidence_opts = detectImportOptions(evidence_path, 'TextType', 'string');
        evidence_data = readtable(evidence_path, evidence_opts);
        total_evidence = total_evidence + 1;

        required_cols = {'overlap_LeftLine', 'overlap_RightLine'};
        if ~all(ismember(required_cols, evidence_data.Properties.VariableNames))
            fprintf('  Skip %s: missing overlap columns.\n', evidence_name);
            continue;
        end
        if ~ismember('lanechange_stage', evidence_data.Properties.VariableNames)
            evidence_data.lanechange_stage = zeros(height(evidence_data), 1);
        end

        old_stage = to_numeric_vector(evidence_data.lanechange_stage);
        overlap_left = to_numeric_vector(evidence_data.overlap_LeftLine) ~= 0;
        overlap_right = to_numeric_vector(evidence_data.overlap_RightLine) ~= 0;
        new_stage = build_lanechange_stage(overlap_left, overlap_right);

        changed_mask = old_stage ~= new_stage;
        if any(changed_mask)
            evidence_data.lanechange_stage = new_stage;
            write_event_table(evidence_data, evidence_path);

            this_changed_rows = sum(changed_mask);
            changed_files = changed_files + 1;
            changed_rows = changed_rows + this_changed_rows;
            fprintf('  Corrected %s: %d rows.\n', evidence_name, this_changed_rows);
        end
    end

    total_changed_files = total_changed_files + changed_files;
    total_changed_rows = total_changed_rows + changed_rows;
    total_missing_evidence = total_missing_evidence + missing_evidence;

    fprintf('  Summary: changed files=%d, changed rows=%d, missing EvidenceChain=%d.\n', ...
        changed_files, changed_rows, missing_evidence);
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

function stage = build_lanechange_stage(overlap_left, overlap_right)
% lanechange_stage enum:
% {0:not-change, 1:left_lanechange_s1, 2:left_lanechange_s2, 3:right_lanechange_s1, 4:right_lanechange_s2, 5:unknown}
overlap_left = overlap_left(:) ~= 0;
overlap_right = overlap_right(:) ~= 0;

n_rows = numel(overlap_left);
stage = zeros(n_rows, 1);

dir = 0;    % 1:left, -1:right
phase = 0;  % 0:idle, 1:stage-1, 2:stage-2

for i = 1:n_rows
    left_now = overlap_left(i);
    right_now = overlap_right(i);

    if ~left_now && ~right_now
        dir = 0;
        phase = 0;
        stage(i) = 0;
        continue;
    end

    if phase == 0
        if left_now && ~right_now
            dir = 1;
            phase = 1;
        elseif right_now && ~left_now
            dir = -1;
            phase = 1;
        else
            stage(i) = 5;
            continue;
        end
    elseif phase == 1
        if dir == 1 && right_now
            phase = 2;
        elseif dir == -1 && left_now
            phase = 2;
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
    end
end
end

function values = to_numeric_vector(values)
if isnumeric(values) || islogical(values)
    values = double(values);
else
    values = str2double(string(values));
end
values = values(:);
values(~isfinite(values)) = 0;
end
