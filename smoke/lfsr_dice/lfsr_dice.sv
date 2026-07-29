// SPDX-License-Identifier: Apache-2.0
`default_nettype none
module lfsr_dice (
    inout wire VDD, inout wire VSS,
    input wire [7:0] ui_in, output logic [7:0] uo_out,
    input wire [7:0] uio_in, output logic [7:0] uio_out, output logic [7:0] uio_oe,
    input wire ena, input wire clk, input wire rst_n
);
  logic [15:0] lfsr;
  always_ff @(posedge clk) begin
    if (!rst_n) lfsr <= 16'h1ace;
    else if (ena) begin
      if (ui_in[0]) lfsr <= {ui_in, uio_in};
      else if (!ui_in[1]) lfsr <= {lfsr[14:0], lfsr[15] ^ lfsr[13] ^ lfsr[12] ^ lfsr[10]};
    end
  end
  always_comb begin
    uo_out = lfsr[7:0];
    uio_out = lfsr[15:8];
    uio_oe = 8'hff;
  end
endmodule
