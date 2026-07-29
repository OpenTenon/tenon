// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module counter8 (
    inout  wire        VDD,
    inout  wire        VSS,
    input  wire        clk_i,
    input  wire        rst_i,
    input  wire        en_i,
    output logic [7:0] count_o
);
  always_ff @(posedge clk_i) begin
    if (rst_i) begin
      count_o <= 8'b00000000;
    end else if (en_i) begin
      count_o <= count_o + 8'd1;
    end
  end
endmodule
