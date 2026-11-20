`default_nettype none

(* blackbox *)
module tenon_mini_reference_project (
    input  wire [1:0]  clk_i,
    input  wire [1:0]  rst_n_i,
    input  wire        enable_i,
    input  wire [11:0] gpio_i,
    output wire [11:0] gpio_o,
    output wire [11:0] gpio_oe,
    inout  wire        VDD,
    inout  wire        VSS
);
endmodule

`default_nettype wire
