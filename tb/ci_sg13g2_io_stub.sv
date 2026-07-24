// SPDX-License-Identifier: Apache-2.0
// Minimal functional IHP SG13G2 PadCell model for PDK-independent CI simulation.

`default_nettype none

module sg13g2_IOPadIOVdd ();
endmodule

module sg13g2_IOPadIOVss ();
endmodule

module sg13g2_IOPadVdd ();
endmodule

module sg13g2_IOPadVss ();
endmodule

module sg13g2_IOPadIn (
    input  wire pad,
    output wire p2c
);
  assign p2c = pad;
endmodule

module sg13g2_IOPadOut30mA (
    output wire pad,
    input  wire c2p
);
  assign pad = c2p;
endmodule

module sg13g2_IOPadInOut30mA (
    inout  wire pad,
    input  wire c2p,
    input  wire c2p_en,
    output wire p2c
);
  assign pad = c2p_en ? c2p : 1'bz;
  assign p2c = pad;
endmodule

`default_nettype wire
