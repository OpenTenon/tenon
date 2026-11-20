create_clock -name control_clock -period 20.0 [get_ports ctrl_clk_i]
create_clock -name user_clock_0 -period 20.0 [get_ports {cluster_clock_i[0]}]
create_clock -name user_clock_1 -period 25.0 [get_ports {cluster_clock_i[1]}]
set_clock_groups -asynchronous -group {control_clock} -group {user_clock_0} -group {user_clock_1}

set_false_path -from [get_ports {ctrl_rst_n_i rst_n_i[0] rst_n_i[1]}]
set_input_delay 2.0 -clock control_clock \
    [get_ports {branch_addr_i[*] site_addr_i[*] branch_clock_request_i route_enable_i gpio_i[*]}]
set_output_delay 2.0 -clock control_clock \
    [get_ports {branch_clock_active_o[*] gpio_o[*] gpio_oe[*]}]
