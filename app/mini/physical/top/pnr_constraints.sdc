create_clock -name control_clock -period 20.0 [get_ports ctrl_clk_i]

set_false_path -from [get_ports {ctrl_rst_n_i rst_n_i[0] rst_n_i[1] clk_i[0] clk_i[1]}]
set_input_delay 2.0 -clock control_clock \
    [get_ports {project_addr_i[*] select_commit_i fabric_enable_i gpio_i[*]}]
set_output_delay 2.0 -clock control_clock \
    [get_ports {select_ready_o select_valid_o gpio_o[*] gpio_oe[*]}]
