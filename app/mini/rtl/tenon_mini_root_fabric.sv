`default_nettype none

module tenon_mini_root_fabric #(
    parameter int CLUSTER_COUNT = 8,
    parameter int ADDRESS_WIDTH = 8 + $clog2(CLUSTER_COUNT),
    parameter int TOTAL_SITES = CLUSTER_COUNT * 256,
    parameter logic [TOTAL_SITES-1:0] ANCHOR_VALID = {TOTAL_SITES{1'b1}}
) (
    input  logic                       ctrl_clk_i,
    input  logic                       ctrl_rst_n_i,
    input  logic [ADDRESS_WIDTH-1:0]   project_addr_i,
    input  logic                       select_commit_i,
    output logic                       select_ready_o,
    output logic                       select_valid_o,
    input  logic                       fabric_enable_i,
    input  logic [1:0]                 clk_i,
    output logic [CLUSTER_COUNT*2-1:0] cluster_clock_o,
    output logic [3:0]                 branch_addr_o,
    output logic [3:0]                 site_addr_o,
    output logic [CLUSTER_COUNT-1:0]   branch_clock_request_o,
    output logic [CLUSTER_COUNT-1:0]   route_enable_o,
    input  logic [CLUSTER_COUNT*2-1:0] branch_clock_active_i,
    input  logic [CLUSTER_COUNT*12-1:0] cluster_gpio_o_i,
    input  logic [CLUSTER_COUNT*12-1:0] cluster_gpio_oe_i,
    output logic [11:0]                gpio_o,
    output logic [11:0]                gpio_oe
);

    localparam int CLUSTER_WIDTH = $clog2(CLUSTER_COUNT);
    localparam logic [ADDRESS_WIDTH:0] TOTAL_SITES_VALUE =
        (ADDRESS_WIDTH + 1)'(TOTAL_SITES);
    localparam logic [CLUSTER_WIDTH:0] CLUSTER_COUNT_VALUE =
        (CLUSTER_WIDTH + 1)'(CLUSTER_COUNT);

    logic requested_valid;
    logic [ADDRESS_WIDTH-1:0] active_address;
    logic [CLUSTER_WIDTH-1:0] active_cluster;
    logic active_cluster_in_range;
    logic route_enable;
    logic cluster_clock_request;
    logic branch_clock_request;
    logic [CLUSTER_COUNT*2-1:0] cluster_clock_active;
    logic [1:0] selected_cluster_clock_active;
    logic [1:0] selected_branch_clock_active;
    logic [11:0] selected_gpio_o;
    logic [11:0] selected_gpio_oe;

    always_comb begin
        if ({1'b0, project_addr_i} < TOTAL_SITES_VALUE) begin
            requested_valid = ANCHOR_VALID[project_addr_i];
        end else begin
            requested_valid = 1'b0;
        end
    end

    assign active_cluster = active_address[ADDRESS_WIDTH-1:8];
    assign active_cluster_in_range = {1'b0, active_cluster} < CLUSTER_COUNT_VALUE;
    assign branch_addr_o  = active_address[7:4];
    assign site_addr_o    = active_address[3:0];

    tenon_mini_root_controller #(
        .ADDRESS_WIDTH(ADDRESS_WIDTH)
    ) controller_I (
        .ctrl_clk_i,
        .ctrl_rst_n_i,
        .project_addr_i,
        .requested_valid_i(requested_valid),
        .select_commit_i,
        .fabric_enable_i,
        .cluster_clock_active_i(selected_cluster_clock_active),
        .branch_clock_active_i(selected_branch_clock_active),
        .active_address_o(active_address),
        .route_enable_o(route_enable),
        .cluster_clock_request_o(cluster_clock_request),
        .branch_clock_request_o(branch_clock_request),
        .select_ready_o,
        .select_valid_o
    );

    genvar cluster_index;
    genvar clock_index;
    generate
        for (cluster_index = 0; cluster_index < CLUSTER_COUNT; cluster_index++) begin : generate_cluster_clock
            logic cluster_selected;
            assign cluster_selected = active_cluster_in_range &&
                                      active_cluster == cluster_index[CLUSTER_WIDTH-1:0];
            assign branch_clock_request_o[cluster_index] =
                branch_clock_request && cluster_selected;
            assign route_enable_o[cluster_index] = route_enable && cluster_selected;

            for (clock_index = 0; clock_index < 2; clock_index++) begin : generate_clock
                tenon_mini_clock_channel #(
                    .DRIVE(4)
                ) clock_channel_I (
                    .ctrl_clk_i,
                    .ctrl_rst_n_i,
                    .clock_i(clk_i[clock_index]),
                    .request_i(cluster_clock_request && cluster_selected),
                    .clock_o(cluster_clock_o[cluster_index*2+clock_index]),
                    .active_o(cluster_clock_active[cluster_index*2+clock_index])
                );
            end
        end
    endgenerate

    always_comb begin
        selected_cluster_clock_active = '0;
        selected_branch_clock_active = '0;
        selected_gpio_o = '0;
        selected_gpio_oe = '0;
        if (active_cluster_in_range) begin
            selected_cluster_clock_active = cluster_clock_active[active_cluster*2+:2];
            selected_branch_clock_active = branch_clock_active_i[active_cluster*2+:2];
            selected_gpio_o = cluster_gpio_o_i[active_cluster*12+:12];
            selected_gpio_oe = cluster_gpio_oe_i[active_cluster*12+:12];
        end
        gpio_o = selected_gpio_o & {12{route_enable && select_valid_o && fabric_enable_i}};
        gpio_oe = selected_gpio_oe & {12{route_enable && select_valid_o && fabric_enable_i}};
    end

endmodule

`default_nettype wire
