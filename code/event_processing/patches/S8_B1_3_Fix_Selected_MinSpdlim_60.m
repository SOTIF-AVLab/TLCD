clear; clc

main_folder = 'Z:\HongqiData\Nanjing';
targets = {
    '20240910', 'D-6.0-22-20240910_112644_238-20240910_113144_234_CSV', 1;
    '20240910', 'D-6.0-22-20240910_112644_238-20240910_113144_234_CSV', 3;
    '20240910', 'D-6.0-22-20240910_151638_769-20240910_152138_740_CSV', 1;
    '20240910', 'D-6.0-22-20240910_151638_769-20240910_152138_740_CSV', 5;
    '20240925', 'D-6.0-22-20240925_154851_926-20240925_155351_837_CSV', 4;
    '20240927', 'D-6.0-28-20240927_110120_086-20240927_110620_075_CSV', 2;
    '20240927', 'D-6.0-28-20240927_110620_075-20240927_111120_105_CSV', 4;
    '20240929', 'D-6.0-28-20240929_113846_530-20240929_114346_378_CSV', 2;
    '20240930', 'D-6.0-22-20240930_153411_574-20240930_153910_889_CSV', 1;
    '20240930', 'D-6.0-22-20240930_153411_574-20240930_153910_889_CSV', 3;
    '20241008', 'D-6.0-28-20241008_160411_644-20241008_160911_617_CSV', 5;
    '20241011', 'D-6.0-27-20241011_110924_265-20241011_111424_015_CSV', 1;
    '20241011', 'D-6.0-27-20241011_110924_265-20241011_111424_015_CSV', 3;
    '20241012', 'D-6.0-27-20241012_151945_749-20241012_152446_003_CSV', 1;
    '20241014', 'D-6.0-27-20241014_154401_720-20241014_154901_710_CSV', 3;
    '20241021', 'D-6.0-31-20241021_160557_780-20241021_161057_787_CSV', 2;
    '20241021', 'D-6.0-31-20241021_161244_805-20241021_161744_900_CSV', 1;
    '20241021', 'D-6.0-31-20241021_161244_805-20241021_161744_900_CSV', 5;
    '20241022', 'D-6.0-31-20241022_150014_730-20241022_150514_745_CSV', 1;
    '20241022', 'D-6.0-31-20241022_150014_730-20241022_150514_745_CSV', 5;
    '20241023', 'D-6.0-31-20241023_093700_685-20241023_094200_692_CSV', 6;
};

for i = 1:size(targets, 1)
    date_name = targets{i, 1};
    segment_name = targets{i, 2};
    event_num = targets{i, 3};
    event_dir = fullfile(main_folder, date_name, 'zEvent_MinSpdlim', segment_name);

    map_path = fullfile(event_dir, sprintf('MinSpdlim_event_%d_MapInfo.csv', event_num));
    evidence_path = fullfile(event_dir, sprintf('MinSpdlim_event_%d_EvidenceChain.csv', event_num));
    events_path = fullfile(event_dir, 'MinSpdlim_events.csv');
    record_path = fullfile(event_dir, sprintf('MinSpdlim_event_%d_record.json', event_num));

    map_info = readtable(map_path);
    evidence = readtable(evidence_path);
    if height(map_info) ~= height(evidence)
        error('Row count mismatch: %s', event_dir);
    end

    effective_lanes = false(height(map_info), 5);
    for lane = 1:5
        max_name = sprintf('LaneMaxSpdlim_%d', lane);
        min_name = sprintf('LaneMinSpdlim_%d', lane);
        effective_lanes(:, lane) = map_info.(max_name) > 0;
        map_info.(min_name) = 60 * double(effective_lanes(:, lane));
        evidence.(min_name) = map_info.(min_name);
    end

    active = any(effective_lanes, 2);
    lane_count = sum(effective_lanes, 2);
    map_info.LaneNumSameDirection = lane_count;
    evidence.LaneNumSameDirection = lane_count;

    article_suffixes = {'78_2', '78_4', '78_5', '78_6', '78_7'};
    for article = 1:numel(article_suffixes)
        evidence.(['trigger_IMR_', article_suffixes{article}])(:) = 0;
        evidence.(['com_IMR_', article_suffixes{article}])(:) = 0;
    end

    evidence.trigger_IMR_78_4(active) = 1;
    evidence.com_IMR_78_4(active) = 1;
    violation = active & evidence.Congestion ~= 1 & evidence.Ego_velocity * 3.6 < 60;
    evidence.com_IMR_78_4(violation) = -1;
    evidence.IsMinSpdsignArea = double(active);
    evidence.Thres_MinSpdlim(:) = -1;
    evidence.Thres_MinSpdlim(active) = 60;

    writetable(map_info, map_path);
    writetable(evidence, evidence_path);

    event_opts = detectImportOptions(events_path, 'TextType', 'string');
    string_vars = intersect({'violated_article', 'extract_type', 'Event_Validity', ...
        'Data_issue', 'Event_description'}, event_opts.VariableNames, 'stable');
    if ~isempty(string_vars)
        event_opts = setvartype(event_opts, string_vars, 'string');
    end
    events = readtable(events_path, event_opts);
    row = find(events.event_num == event_num, 1);
    if isempty(row)
        error('Event %d not found: %s', event_num, events_path);
    end

    for article = 1:numel(article_suffixes)
        events.(['trigger_IMR_', article_suffixes{article}])(row) = 0;
        events.(['com_IMR_', article_suffixes{article}])(row) = 0;
    end
    events.trigger_IMR_78_4(row) = double(any(active));
    if any(violation)
        events.com_IMR_78_4(row) = -1;
        events.violated_article(row) = "78.4";
    else
        events.com_IMR_78_4(row) = double(any(active));
        events.violated_article(row) = "Com-78.4";
    end

    active_rows = find(active);
    events.trigger_start_idx(row) = events.start_idx(row) + active_rows(1) - 1;
    events.trigger_end_idx(row) = events.start_idx(row) + active_rows(end) - 1;
    events.Thres_MinSpdlim(row) = 60;
    events.LaneNumSameDirection(row) = mode(lane_count(active));
    events.LanePos(row) = mode(evidence.EgoLaneIndex(active));
    events.Congestion(row) = double(any(evidence.Congestion(active) == 1));
    events.Ego_speed_min_kph(row) = min(evidence.Ego_velocity(active) * 3.6);
    writetable(events, events_path);

    record = jsondecode(fileread(record_path));
    record.Article.ID = 'IMR_78.4';
    record.Article.Text = {'Where there is any discrepancy between the minimum speed indicated by a speed limit sign and the lane-speed rules, a motor vehicle shall be driven at the minimum speed indicated by the road sign.'};
    record.EventAnchor.Trigger_start_time = sprintf('%.2g s', evidence.event_time(active_rows(1)));
    record.EventAnchor.Trigger_end_time = sprintf('%.2g s', evidence.event_time(active_rows(end)));
    record.Evidence.Thres_MinSpdlim = 60;
    record.Evidence.LaneNumSameDirection = mode(lane_count(active));
    record.Evidence.LanePos = mode(evidence.EgoLaneIndex(active));
    record.Evidence.Congestion = double(any(evidence.Congestion(active) == 1));
    record.Evidence.Ego_speed_min_kph = min(evidence.Ego_velocity(active) * 3.6);
    if any(violation)
        record.Result.Compliance_label = 'Violation';
        record.Result.Violation_reason = 'The ego vehicle speed is lower than 60 km/h while traffic is not congested.';
        record.Result.Driving_suggestion = 'Increase speed to at least 60 km/h when safe and traffic is not congested.';
    else
        record.Result.Compliance_label = 'Compliance';
        record.Result.Violation_reason = 'None';
        record.Result.Driving_suggestion = 'Maintain a speed not lower than 60 km/h when traffic conditions allow.';
    end

    fid = fopen(record_path, 'w', 'n', 'UTF-8');
    if fid < 0
        error('Cannot open file for writing: %s', record_path);
    end
    fwrite(fid, jsonencode(record, 'PrettyPrint', true), 'char');
    fclose(fid);

    fprintf('[%d/%d] Corrected %s event %d (%d rows, violations: %d).\n', ...
        i, size(targets, 1), segment_name, event_num, height(evidence), nnz(violation));
end
