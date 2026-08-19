function S6_run_selected_Evidence_chain()
% Run selected S6 evidence-chain scripts in order.

script_dir = fileparts(mfilename('fullpath'));
script_names = {
    'S6_B1_2_Evidence_chain.m'
    'S6_B1_3_Evidence_chain_MinSpdlim.m'
    'S6_B1_4_Evidence_chain_FD.m'
    'S6_B1_5_Evidence_chain_LD.m'
    'S6_B1_6_Evidence_chain_CLC.m'
    'S6_B1_6_Evidence_chain_LC.m'
    'S6_B1_7_Evidence_chain_RM.m'
    'S6_B1_8_Evidence_chain_OT.m'
    };
old_dir = pwd;
restore_dir = onCleanup(@() cd(old_dir));
cd(script_dir);

for i = 1:numel(script_names)
    fprintf('\nRunning %s (%d/%d)\n', script_names{i}, i, numel(script_names));
    evalin('base', sprintf('run(''%s'');', script_names{i}));
end
end
