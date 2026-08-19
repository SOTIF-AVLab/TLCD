clear; clc
script_dir = fileparts(mfilename('fullpath'));
if isempty(script_dir)
    script_dir = pwd;
end
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) Root directory =========
main_folder = 'Z:\HongqiData\Changchun';

% ========= 2) Find Overtake_events.csv files =========
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

    % if ~strcmp(date_name, '20241025')
    %     continue
    % end

    event_root = fullfile(date_path, 'zEvent_Overtake');
    if ~exist(event_root, 'dir')
        continue;
    end

    seg_dirs = dir(event_root);
    seg_dirs = seg_dirs([seg_dirs.isdir]);
    seg_dirs = seg_dirs(~ismember({seg_dirs.name},{'.','..'}));

    for s = 1:numel(seg_dirs)
        segment_name = seg_dirs(s).name;
        segment_path = fullfile(seg_dirs(s).folder, segment_name);
        event_path = fullfile(segment_path, 'Overtake_events.csv');
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
fprintf('Found %d Overtake_events.csv files.\n', numEventsFiles);
if numEventsFiles == 0
    error('No Overtake_events.csv files found. Expected YYYYMMDD\\zEvent_Overtake\\<segment>\\Overtake_events.csv.');
end

% ========= 3) Correct OvertakeDirection from EvidenceChain files =========
total_events = 0;
total_changed = 0;
total_missing_evidence = 0;

for i = 1:numEventsFiles
    event_path = meta(i).event_path;
    out_dir = meta(i).out_dir;

    fprintf('[%d/%d] Processing: %s\n', i, numEventsFiles, event_path);

    event_opts = detectImportOptions(event_path, 'TextType', 'string');
    event_data = readtable(event_path, event_opts);

    num_event = height(event_data);
    total_events = total_events + num_event;
    if num_event == 0
        fprintf('  Empty event table. Skipped.\n');
        continue;
    end

    if ~ismember('OvertakeDirection', event_data.Properties.VariableNames)
        event_data.OvertakeDirection = zeros(num_event, 1);
    end
    if ~ismember('trigger_overtake', event_data.Properties.VariableNames)
        event_data.trigger_overtake = zeros(num_event, 1);
    end

    old_direction = to_numeric_vector(event_data.OvertakeDirection);
    old_trigger = to_numeric_vector(event_data.trigger_overtake);
    new_direction = old_direction;
    new_trigger = old_trigger;
    missing_evidence = 0;

    for j = 1:num_event
        evidence_name = sprintf('Overtake_event_%d_EvidenceChain.csv', j);
        evidence_path = fullfile(out_dir, evidence_name);

        if ~exist(evidence_path, 'file')
            missing_evidence = missing_evidence + 1;
            continue;
        end

        evidence_opts = detectImportOptions(evidence_path, 'TextType', 'string');
        evidence_data = readtable(evidence_path, evidence_opts);
        [new_trigger(j), new_direction(j)] = get_evidence_overtake_summary(evidence_data);
    end

    changed_mask = old_direction ~= new_direction | old_trigger ~= new_trigger;
    changed_count = sum(changed_mask);
    total_changed = total_changed + changed_count;
    total_missing_evidence = total_missing_evidence + missing_evidence;

    if changed_count > 0
        event_data.trigger_overtake = new_trigger;
        event_data.OvertakeDirection = new_direction;
        write_event_table(event_data, event_path);
        fprintf('  Corrected %d/%d rows. Missing EvidenceChain: %d.\n', ...
            changed_count, num_event, missing_evidence);
        print_changes(find(changed_mask), old_direction, new_direction);
    else
        fprintf('  No OvertakeDirection changes. Missing EvidenceChain: %d.\n', ...
            missing_evidence);
    end
end

fprintf('Done. Events checked: %d, directions corrected: %d, missing EvidenceChain files: %d.\n', ...
    total_events, total_changed, total_missing_evidence);

function target_dates = parse_target_dates()
target_text = strtrim(string(getenv('TLCD_TARGET_DATES')));
if strlength(target_text) == 0
    target_dates = strings(0, 1);
else
    target_dates = strtrim(split(target_text, ','));
    target_dates = target_dates(strlength(target_dates) > 0);
end
end

function [trigger_value, direction] = get_evidence_overtake_summary(evidence_data)
trigger_value = 0;
direction = 0;
if isempty(evidence_data) || ~ismember('OvertakeDirection', evidence_data.Properties.VariableNames)
    return;
end

directions = to_numeric_vector(evidence_data.OvertakeDirection);

if ismember('trigger_overtake', evidence_data.Properties.VariableNames)
    trigger = to_numeric_vector(evidence_data.trigger_overtake);
    trigger = trigger(:) ~= 0;
    trigger_value = double(any(trigger));
    if numel(trigger) == numel(directions)
        active_directions = directions(trigger & isfinite(directions));
        active_directions = active_directions(active_directions ~= 0);
        if ~isempty(active_directions)
            direction = mode(active_directions);
            return;
        end
    end
end

directions = directions(isfinite(directions) & directions ~= 0);
if ~isempty(directions)
    direction = mode(directions);
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

function print_changes(row_idx, old_direction, new_direction)
max_print = min(numel(row_idx), 20);
for k = 1:max_print
    j = row_idx(k);
    fprintf('    event %d: %g -> %g\n', j, old_direction(j), new_direction(j));
end
if numel(row_idx) > max_print
    fprintf('    ... %d more rows changed.\n', numel(row_idx) - max_print);
end
end
