// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module decoder2to4 (
    inout  wire        VDD,
    inout  wire        VSS,
    input  wire        en_i,
    input  wire  [1:0] sel_i,
    output logic [3:0] y_o
);
  always_comb begin
    y_o = 4'b0000;
    if (en_i) begin
      y_o[sel_i] = 1'b1;
    end
  end
endmodule
