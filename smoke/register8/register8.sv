// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module register8 (
    inout  wire        VDD,
    inout  wire        VSS,
    input  wire        clk_i,
    input  wire        rst_i,
    input  wire        en_i,
    input  wire  [7:0] d_i,
    output logic [7:0] q_o
);
  always_ff @(posedge clk_i) begin
    if (rst_i) begin
      q_o <= 8'b00000000;
    end else if (en_i) begin
      q_o <= d_i;
    end
  end
endmodule
