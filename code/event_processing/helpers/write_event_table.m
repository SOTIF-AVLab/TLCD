function write_event_table(T, csv_path)
%WRITE_EVENT_TABLE Write event CSVs as UTF-8 with BOM for Excel compatibility.
%   MATLAB and VSCode can read UTF-8 CSVs without a BOM, but Excel often
%   guesses the local ANSI code page when opening a CSV directly. The BOM
%   makes Excel recognize UTF-8 and keeps Chinese review text readable.

out_dir = fileparts(csv_path);
if strlength(string(out_dir)) == 0
    out_dir = pwd;
end

tmp_path = [tempname(out_dir), '.csv'];
cleanup_tmp = onCleanup(@() delete_tmp_file(tmp_path));

T = format_epoch_ms_columns(T);
writetable(T, tmp_path, 'Encoding', 'UTF-8');

fid = fopen(tmp_path, 'r');
if fid < 0
    error('write_event_table:ReadTempFailed', 'Failed to read temporary CSV: %s', tmp_path);
end
cleanup_read = onCleanup(@() fclose(fid));
bytes = fread(fid, Inf, 'uint8=>uint8');
clear cleanup_read;

utf8_bom = uint8([239; 187; 191]);
if numel(bytes) < 3 || ~isequal(bytes(1:3), utf8_bom)
    bytes = [utf8_bom; bytes];
end

fid = fopen(csv_path, 'w');
if fid < 0
    error('write_event_table:WriteCsvFailed', 'Failed to write CSV: %s', csv_path);
end
cleanup_write = onCleanup(@() fclose(fid));
fwrite(fid, bytes, 'uint8');
clear cleanup_write;

clear cleanup_tmp;
delete_tmp_file(tmp_path);
end

function T = format_epoch_ms_columns(T)
vars = T.Properties.VariableNames;
if ismember('t_start', vars) && ismember('t_end', vars)
    T.t_start = epoch_to_ms_text(T.t_start);
    T.t_end = epoch_to_ms_text(T.t_end);
end
end

function text_values = epoch_to_ms_text(values)
values = double(values);
text_values = strings(size(values));
for k = 1:numel(values)
    v = values(k);
    if isnan(v)
        text_values(k) = "";
        continue;
    end

    av = abs(v);
    if av > 1e17
        ms = round(v / 1e6);
    elseif av > 1e14
        ms = round(v / 1e3);
    elseif av > 1e11
        ms = round(v);
    else
        ms = round(v * 1e3);
    end
    text_values(k) = compose('%.0f', ms);
end
end

function delete_tmp_file(tmp_path)
if exist(tmp_path, 'file')
    delete(tmp_path);
end
end
