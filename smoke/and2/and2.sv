// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module and2 (
    inout  wire VDD,
    inout  wire VSS,
    input  wire a_i,
    input  wire b_i,
    output wire y_o
);
  assign y_o = a_i & b_i;
endmodule
