function u = laneline0_memory(u1)
eps0 = 1e-6;

u = u1;   % 复制一份，避免改原数组

for i = 2:length(u)
    if abs(u(i)) < eps0
        u(i) = u(i-1);
    end
end

end
