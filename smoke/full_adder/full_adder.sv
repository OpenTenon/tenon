// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module full_adder (
    inout  wire VDD,
    inout  wire VSS,
    input  wire a_i,
    input  wire b_i,
    input  wire carry_i,
    output wire sum_o,
    output wire carry_o
);
  assign sum_o   = a_i ^ b_i ^ carry_i;
  assign carry_o = (a_i & b_i) | (a_i & carry_i) | (b_i & carry_i);
endmodule
