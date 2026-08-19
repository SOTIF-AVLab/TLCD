function ego_lane_index = refine_s5_ego_lane_index(base_lane_index, left_c0, right_c0)
ego_lane_index = base_lane_index(:);
left_c0 = left_c0(:);
right_c0 = right_c0(:);
offset = 0;
for i = 1:numel(ego_lane_index)
    if i > 1 && base_lane_index(i) ~= base_lane_index(i - 1)
        offset = 0;
    end

    if i > 1 && offset == 0
        left_change = abs(left_c0(i - 1)) < 0.75 && abs(right_c0(i)) < 0.75 && ...
            left_c0(i) > 2 && ego_lane_index(i) > 1;
        right_change = abs(right_c0(i - 1)) < 0.75 && abs(left_c0(i)) < 0.75 && ...
            right_c0(i) < -2 && ego_lane_index(i) < 5;
        if left_change
            offset = -1;
        elseif right_change
            offset = 1;
        end
    end

    ego_lane_index(i) = min(5, max(1, ego_lane_index(i) + offset));
end
end
