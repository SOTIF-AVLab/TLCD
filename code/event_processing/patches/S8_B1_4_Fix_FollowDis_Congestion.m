clear; clc
script_dir = fileparts(mfilename('fullpath'));
if isempty(script_dir)
    script_dir = pwd;
end
addpath(fullfile(fileparts(mfilename('fullpath')), '..', 'helpers'));

% ========= 1) Root directory =========
main_folder = 'Z:\HongqiData\Changchun';

% ========= 2) Find FollowDis_events.csv files =========
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

    event_root = fullfile(date_path, 'zEvent_FollowDis');
    if ~exist(event_root, 'dir')
        continue;
    end

    seg_dirs = dir(event_root);
    seg_dirs = seg_dirs([seg_dirs.isdir]);
    seg_dirs = seg_dirs(~ismember({seg_dirs.name},{'.','..'}));

    for s = 1:numel(seg_dirs)
        segment_name = seg_dirs(s).name;
        segment_path = fullfile(seg_dirs(s).folder, segment_name);
        event_path = fullfile(segment_path, 'FollowDis_events.csv');
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
fprintf('Found %d FollowDis_events.csv files.\n', num_event_files);
if num_event_files == 0
    error('No FollowDis_events.csv files found. Expected YYYYMMDD\\zEvent_FollowDis\\<segment>\\FollowDis_events.csv.');
end

% ========= 3) Fix congested frames and regenerate JSON =========
total_events = 0;
total_evidence = 0;
total_changed_files = 0;
total_changed_rows = 0;
total_json_written = 0;
total_missing_evidence = 0;

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
    json_written = 0;
    missing_evidence = 0;

    for j = 1:num_event
        evidence_name = sprintf('FollowDis_event_%d_EvidenceChain.csv', j);
        evidence_path = fullfile(out_dir, evidence_name);

        if ~exist(evidence_path, 'file')
            missing_evidence = missing_evidence + 1;
            write_followdis_json(event_data, j, out_dir, evidence_name, table());
            json_written = json_written + 1;
            continue;
        end

        evidence_opts = detectImportOptions(evidence_path, 'TextType', 'string');
        evidence_data = readtable(evidence_path, evidence_opts);
        total_evidence = total_evidence + 1;

        [evidence_data, this_changed_rows] = make_congested_followdis_frames_compliant(evidence_data);
        if this_changed_rows > 0
            write_event_table(evidence_data, evidence_path);
            changed_files = changed_files + 1;
            changed_rows = changed_rows + this_changed_rows;
            fprintf('  Corrected %s: %d congested violation rows.\n', evidence_name, this_changed_rows);
        end

        write_followdis_json(event_data, j, out_dir, evidence_name, evidence_data);
        json_written = json_written + 1;
    end

    total_changed_files = total_changed_files + changed_files;
    total_changed_rows = total_changed_rows + changed_rows;
    total_json_written = total_json_written + json_written;
    total_missing_evidence = total_missing_evidence + missing_evidence;

    fprintf('  Summary: changed files=%d, changed rows=%d, JSON written=%d, missing EvidenceChain=%d.\n', ...
        changed_files, changed_rows, json_written, missing_evidence);
end

fprintf('Done. Events checked: %d, EvidenceChain files checked: %d, files changed: %d, rows changed: %d, JSON written: %d, missing EvidenceChain files: %d.\n', ...
    total_events, total_evidence, total_changed_files, total_changed_rows, total_json_written, total_missing_evidence);

function target_dates = parse_target_dates()
target_text = strtrim(string(getenv('TLCD_TARGET_DATES')));
if strlength(target_text) == 0
    target_dates = strings(0, 1);
else
    target_dates = strtrim(split(target_text, ','));
    target_dates = target_dates(strlength(target_dates) > 0);
end
end

function [T, changed_rows] = make_congested_followdis_frames_compliant(T)
changed_rows = 0;
required_cols = {'Congestion', 'com_IMR_80_1', 'com_IMR_80_2'};
if isempty(T) || ~all(ismember(required_cols, T.Properties.VariableNames))
    return;
end

congested = to_numeric_vector(T.Congestion) == 1;

com_80_1 = to_numeric_vector(T.com_IMR_80_1);
com_80_2 = to_numeric_vector(T.com_IMR_80_2);

fix_80_1 = congested & com_80_1 < 0;
fix_80_2 = congested & com_80_2 < 0;

com_80_1(fix_80_1) = 1;
com_80_2(fix_80_2) = 1;

changed_rows = sum(fix_80_1 | fix_80_2);
if changed_rows > 0
    T.com_IMR_80_1 = com_80_1;
    T.com_IMR_80_2 = com_80_2;
end
end

function write_followdis_json(event_data, event_idx, out_dir, evidence_name, evidence_data)
dt_start = string(event_data.dt_start(event_idx));
dt_end = string(event_data.dt_end(event_idx));
[json_date, json_time] = split_datetime_range(dt_start, dt_end);

summary = summarize_followdis_event(evidence_data);
if summary.HasEvidence
    json_compliance_label = summary.Compliance_label;
    json_article_id = summary.Article_ID;
    json_article_text = summary.Article_Text;
else
    violated_str = string(event_data.violated_article(event_idx));
    trigger_str = string(event_data.trigger_article(event_idx));

    if violated_str ~= "Com"
        article_str = violated_str;
        json_compliance_label = "Violation";
    else
        article_str = trigger_str;
        json_compliance_label = "Compliance";
    end

    [json_article_id, json_article_text] = build_article_fields(article_str);
end

extract_type = string(event_data.extract_type(event_idx));
[anchor_type, description] = build_anchor_fields(extract_type);

if summary.HasEvidence && strlength(summary.Trigger_start_time) > 0
    json_trigger_start_time = summary.Trigger_start_time;
    json_trigger_end_time = summary.Trigger_end_time;
else
    json_trigger_start_time = string(event_data.trigger_start_time(event_idx)) + " s";
    json_trigger_end_time = string(event_data.trigger_end_time(event_idx)) + " s";
end

if json_compliance_label == "Violation"
    json_violation_reason = "Following distance requirement violated.";
    json_driving_suggestion = "Increase distance to front vehicle.";
else
    json_violation_reason = "None";
    json_driving_suggestion = "Maintain safe following distance.";
end

J = struct();
J.Location = "China, Nanjing";
J.Date = json_date;
J.Time = json_time;

J.Article = struct();
J.Article.ID = json_article_id;
J.Article.Text = json_article_text;

J.EventAnchor = struct();
J.EventAnchor.Anchor_type = anchor_type;
J.EventAnchor.Trigger_start_time = json_trigger_start_time;
J.EventAnchor.Trigger_end_time = json_trigger_end_time;
J.EventAnchor.Description = description;

J.Evidence = struct();
J.Evidence.Evidence_chain_file = evidence_name;

J.Result = struct();
J.Result.Compliance_label = json_compliance_label;
J.Result.Violation_reason = json_violation_reason;
J.Result.Driving_suggestion = json_driving_suggestion;

json_name = sprintf('FollowDis_event_%d_record.json', event_idx);
json_path = fullfile(out_dir, json_name);

try
    json_txt = jsonencode(J, 'PrettyPrint', true);
catch
    json_txt = jsonencode(J);
end

fid = fopen(json_path, 'w', 'n', 'UTF-8');
if fid < 0
    error('Cannot open JSON output file: %s', json_path);
end
fwrite(fid, json_txt, 'char');
fclose(fid);
end

function Summary = summarize_followdis_event(evidence_data)
Summary = struct( ...
    'HasEvidence', false, ...
    'Compliance_label', "", ...
    'Article_ID', "", ...
    'Article_Text', {{}}, ...
    'Trigger_start_time', "", ...
    'Trigger_end_time', "");

if isempty(evidence_data)
    return;
end

trigger_80_1 = read_numeric_column(evidence_data, 'trigger_IMR_80_1');
trigger_80_2 = read_numeric_column(evidence_data, 'trigger_IMR_80_2');
com_80_1 = read_numeric_column(evidence_data, 'com_IMR_80_1');
com_80_2 = read_numeric_column(evidence_data, 'com_IMR_80_2');

trigger_mask = (trigger_80_1 ~= 0) | (trigger_80_2 ~= 0);
violation_80_1 = com_80_1 < 0;
violation_80_2 = com_80_2 < 0;

if any(violation_80_1) || any(violation_80_2)
    Summary.Compliance_label = "Violation";
    article_list = strings(0, 1);
    if any(violation_80_1)
        article_list(end+1, 1) = "80.1";
    end
    if any(violation_80_2)
        article_list(end+1, 1) = "80.2";
    end
else
    Summary.Compliance_label = "Compliance";
    article_list = strings(0, 1);
    if any(trigger_80_1 ~= 0)
        article_list(end+1, 1) = "80.1";
    end
    if any(trigger_80_2 ~= 0)
        article_list(end+1, 1) = "80.2";
    end
end

[Summary.Article_ID, Summary.Article_Text] = build_article_fields(article_list);

if any(trigger_mask)
    event_time = read_event_time(evidence_data);
    trigger_idx = find(trigger_mask);
    Summary.Trigger_start_time = format_event_seconds(event_time(trigger_idx(1)));
    Summary.Trigger_end_time = format_event_seconds(event_time(trigger_idx(end)));
end

Summary.HasEvidence = true;
end

function [date_text, time_text] = split_datetime_range(dt_start, dt_end)
dt_parts = split(dt_start);
if numel(dt_parts) >= 2
    date_text = dt_parts(1);
    start_time = dt_parts(2);
else
    date_text = "";
    start_time = dt_start;
end

dt_end_parts = split(dt_end);
if numel(dt_end_parts) >= 2
    end_time = dt_end_parts(2);
else
    end_time = dt_end;
end

time_text = start_time + " -- " + end_time;
end

function [anchor_type, description] = build_anchor_fields(extract_type)
switch extract_type
    case "trigger_interval_200_600"
        anchor_type = "trigger_interval";
        description = "Event extracted from a medium-length trigger interval.";
    case "long_trigger_start"
        anchor_type = "long_trigger_start_point";
        description = "Start of long trigger interval without state switch.";
    case "long_trigger_end"
        anchor_type = "long_trigger_end_point";
        description = "End of long trigger interval without state switch.";
    case "long_trigger_single_switch"
        anchor_type = "single_switch_point";
        description = "Single compliance-state switch in long trigger interval.";
    case "long_trigger_max_adjacent_pair_switch"
        anchor_type = "dominant_adjacent_pair_switch";
        description = "Switch between adjacent segments with largest duration.";
    case "long_trigger_mixed_cluster"
        anchor_type = "state_cluster_interval";
        description = "Clustered interval with multiple compliance/violation changes.";
    case "long_trigger_violation_cluster"
        anchor_type = "state_cluster_interval";
        description = "Cluster dominated by violation segments.";
    case "long_trigger_compliance_cluster"
        anchor_type = "state_cluster_interval";
        description = "Cluster dominated by compliance segments.";
    otherwise
        anchor_type = "unknown";
        description = "Unknown extraction type.";
end
end

function values = read_numeric_column(T, column_name)
if ismember(column_name, T.Properties.VariableNames)
    values = to_numeric_vector(T.(column_name));
else
    values = zeros(height(T), 1);
end
end

function event_time = read_event_time(T)
if ismember('event_time', T.Properties.VariableNames)
    event_time = to_numeric_vector(T.event_time);
else
    event_time = 0.01 * (1:height(T))';
end
end

function [article_id, article_text] = build_article_fields(article_input)
article_list = strings(0, 1);

if ischar(article_input) || (isstring(article_input) && isscalar(article_input))
    article_str = string(article_input);
    if strlength(article_str) > 0
        article_list = split(article_str, ";");
        article_list = strtrim(article_list);
        article_list = article_list(strlength(article_list) > 0);
    end
else
    article_list = string(article_input);
end

if isempty(article_list)
    article_id = "";
    article_text = {};
    return;
end

article_id = "IMR_" + strjoin(article_list, " & ");
article_text_list = strings(size(article_list));

for k = 1:numel(article_list)
    switch article_list(k)
        case "80.1"
            article_text_list(k) = "Keep >100m distance when speed >100km/h.";
        case "80.2"
            article_text_list(k) = "Keep >50m distance when speed <=100km/h.";
        otherwise
            article_text_list(k) = "unknown";
    end
end

article_text = cellstr(article_text_list);
end

function text_value = format_event_seconds(value)
text_value = string(value) + " s";
end

function values = to_numeric_vector(values)
if iscell(values)
    values = string(values);
end
if isstring(values) || ischar(values)
    values = str2double(values);
else
    values = double(values);
end
values = values(:);
end
