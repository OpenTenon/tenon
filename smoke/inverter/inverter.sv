// SPDX-License-Identifier: Apache-2.0
`default_nettype none

module inverter (
    inout  wire VDD,
    inout  wire VSS,
    input  wire a_i,
    output wire y_o
);
  assign y_o = ~a_i;
endmodule
