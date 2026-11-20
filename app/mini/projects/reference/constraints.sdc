create_clock -name user_clock_0 -period 20.0 [get_ports {clk_i[0]}]
create_clock -name user_clock_1 -period 25.0 [get_ports {clk_i[1]}]
set_clock_groups -asynchronous -group {user_clock_0} -group {user_clock_1}

set_false_path -from [get_ports {rst_n_i[0] rst_n_i[1]}]
set_input_delay 2.0 -clock user_clock_0 [get_ports {enable_i gpio_i[*]}]
set_output_delay 2.0 -clock user_clock_0 [get_ports {gpio_o[*] gpio_oe[*]}]
