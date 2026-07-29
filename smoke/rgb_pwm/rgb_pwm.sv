// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module rgb_pwm (
    inout  wire        VDD,
    inout  wire        VSS,
    input  wire  [7:0] ui_in,
    output logic [7:0] uo_out,
    input  wire  [7:0] uio_in,
    output logic [7:0] uio_out,
    output logic [7:0] uio_oe,
    input  wire        ena,
    input  wire        clk,
    input  wire        rst_n
);
  logic [7:0] phase;
  logic [7:0] red_duty, green_duty, blue_duty;
  always_ff @(posedge clk) begin
    if (!rst_n) phase <= 8'd0;
    else if (ena) phase <= phase + 8'd1;
  end
  always_comb begin
    red_duty   = {ui_in[7:5], 5'b0};
    green_duty = {ui_in[4:2], 5'b0};
    blue_duty  = {uio_in[2:0], 5'b0};
    uo_out     = {phase[7:3], phase < blue_duty, phase < green_duty, phase < red_duty};
    uio_out    = {5'b0, blue_duty[7:5]};
    uio_oe     = 8'h07;
  end
endmodule
