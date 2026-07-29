// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module mux2 (
    inout  wire VDD,
    inout  wire VSS,
    input  wire d0_i,
    input  wire d1_i,
    input  wire sel_i,
    output wire y_o
);
  assign y_o = sel_i ? d1_i : d0_i;
endmodule
