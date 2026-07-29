// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module stopwatch (
    inout wire VDD, inout wire VSS,
    input wire [7:0] ui_in, output logic [7:0] uo_out,
    input wire [7:0] uio_in, output logic [7:0] uio_out, output logic [7:0] uio_oe,
    input wire ena, input wire clk, input wire rst_n
);
  logic [23:0] ticks, lap;
  always_ff @(posedge clk) begin
    if (!rst_n || ui_in[1]) begin ticks <= 24'd0; lap <= 24'd0; end
    else if (ena) begin
      if (ui_in[0]) ticks <= ticks + 24'd1;
      if (uio_in[0]) lap <= ticks;
    end
  end
  always_comb begin
    case (ui_in[4:2])
      3'd0: uo_out = ticks[7:0];
      3'd1: uo_out = ticks[15:8];
      3'd2: uo_out = ticks[23:16];
      3'd3: uo_out = lap[7:0];
      3'd4: uo_out = lap[15:8];
      default: uo_out = lap[23:16];
    endcase
    uio_out = {uio_in[0], ticks[6:0]};
    uio_oe = 8'hfe;
  end
endmodule
