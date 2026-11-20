`default_nettype none

(* blackbox *)
module tenon_mini_cluster_tile (
    input  wire        ctrl_clk_i,
    input  wire        ctrl_rst_n_i,
    input  wire [1:0]  cluster_clock_i,
    input  wire [1:0]  rst_n_i,
    input  wire [3:0]  branch_addr_i,
    input  wire [3:0]  site_addr_i,
    input  wire        branch_clock_request_i,
    input  wire        route_enable_i,
    input  wire [11:0] gpio_i,
    output wire [1:0]  branch_clock_active_o,
    output wire [11:0] gpio_o,
    output wire [11:0] gpio_oe,
    inout  wire        VDD,
    inout  wire        VSS
);
endmodule

`default_nettype wire
