function S5_run_selected_Event_input()
% Run selected S5 event-input scripts in order.

script_dir = fileparts(mfilename('fullpath'));
script_names = {
    'S5_B1_2_Event_input.m'
    'S5_B1_3_Event_input_MinSpdlim.m'
    'S5_B1_4_Event_input_FD.m'
    'S5_B1_5_Event_input_LD.m'
    'S5_B1_6_Event_input_CLC.m'
    'S5_B1_6_Event_input_LC.m'
    'S5_B1_7_Event_input_RM.m'
    'S5_B1_8_Event_input_OT.m'
    };
old_dir = pwd;
restore_dir = onCleanup(@() cd(old_dir));
cd(script_dir);

for i = 1:numel(script_names)
    fprintf('\nRunning %s (%d/%d)\n', script_names{i}, i, numel(script_names));
    evalin('base', sprintf('run(''%s'');', script_names{i}));
end
end
