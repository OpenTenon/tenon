`default_nettype none

module tenon_mini_cluster #(
    parameter logic [255:0] ANCHOR_VALID = {256{1'b1}}
) (
    input  logic             ctrl_clk_i,
    input  logic             ctrl_rst_n_i,
    input  logic [1:0]       cluster_clock_i,
    input  logic [3:0]       branch_addr_i,
    input  logic [3:0]       site_addr_i,
    input  logic             branch_clock_request_i,
    input  logic             route_enable_i,
    input  logic [256*12-1:0] site_gpio_o_i,
    input  logic [256*12-1:0] site_gpio_oe_i,
    output logic [255:0]     site_enable_o,
    output logic [256*2-1:0] site_clock_o,
    output logic [1:0]       branch_clock_active_o,
    output logic [11:0]      gpio_o,
    output logic [11:0]      gpio_oe
);

    logic [16*12-1:0] branch_gpio_o;
    logic [16*12-1:0] branch_gpio_oe;
    logic [16*2-1:0] branch_clock;
    logic [16*2-1:0] branch_clock_active;

    genvar branch_index;
    genvar clock_index;
    genvar site_index;
    generate
        for (branch_index = 0; branch_index < 16; branch_index++) begin : generate_branch
            logic branch_selected;
            assign branch_selected = branch_addr_i == branch_index[3:0];

            for (clock_index = 0; clock_index < 2; clock_index++) begin : generate_clock
                tenon_mini_clock_channel #(
                    .DRIVE(2)
                ) clock_channel_I (
                    .ctrl_clk_i,
                    .ctrl_rst_n_i,
                    .clock_i(cluster_clock_i[clock_index]),
                    .request_i(branch_clock_request_i && branch_selected),
                    .clock_o(branch_clock[branch_index*2+clock_index]),
                    .active_o(branch_clock_active[branch_index*2+clock_index])
                );
            end

            for (site_index = 0; site_index < 16; site_index++) begin : generate_site_clock
                assign site_clock_o[((branch_index*16+site_index)*2)+:2] =
                    branch_clock[branch_index*2+:2];
            end

            tenon_mini_branch branch_I (
                .site_addr_i,
                .route_enable_i(route_enable_i && branch_selected),
                .anchor_valid_i(ANCHOR_VALID[branch_index*16+:16]),
                .site_gpio_o_i(site_gpio_o_i[branch_index*16*12+:16*12]),
                .site_gpio_oe_i(site_gpio_oe_i[branch_index*16*12+:16*12]),
                .site_enable_o(site_enable_o[branch_index*16+:16]),
                .gpio_o(branch_gpio_o[branch_index*12+:12]),
                .gpio_oe(branch_gpio_oe[branch_index*12+:12])
            );
        end
    endgenerate

    always_comb begin
        branch_clock_active_o = branch_clock_active[branch_addr_i*2+:2];
        gpio_o                 = branch_gpio_o[branch_addr_i*12+:12];
        gpio_oe                = branch_gpio_oe[branch_addr_i*12+:12];
    end

endmodule

`default_nettype wire
