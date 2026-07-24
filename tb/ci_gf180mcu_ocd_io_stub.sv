// SPDX-License-Identifier: Apache-2.0
// Minimal functional GF180MCU OCD IO model for PDK-independent CI simulation.

`default_nettype none

module gf180mcu_ocd_io__in_s (
    inout  wire PAD,
    output wire Y,
    input  wire PU,
    input  wire PD,
    inout  wire DVDD,
    inout  wire DVSS,
    inout  wire VDD,
    inout  wire VSS
);
  assign Y = PAD;
endmodule

module gf180mcu_ocd_io__in_c (
    inout  wire PAD,
    output wire Y,
    input  wire PU,
    input  wire PD,
    inout  wire DVDD,
    inout  wire DVSS,
    inout  wire VDD,
    inout  wire VSS
);
  assign Y = PAD;
endmodule

module gf180mcu_ocd_io__bi_24t (
    inout  wire PAD,
    input  wire A,
    input  wire OE,
    output wire Y,
    input  wire CS,
    input  wire SL,
    input  wire IE,
    input  wire PU,
    input  wire PD,
    inout  wire DVDD,
    inout  wire DVSS,
    inout  wire VDD,
    inout  wire VSS
);
  assign PAD = OE ? A : 1'bz;
  assign Y   = PAD;
endmodule

module gf180mcu_ocd_io__dvdd (
    inout wire DVDD,
    inout wire DVSS,
    inout wire VDD,
    inout wire VSS
);
endmodule

module gf180mcu_ocd_io__dvss (
    inout wire DVDD,
    inout wire DVSS,
    inout wire VDD,
    inout wire VSS
);
endmodule

module gf180mcu_ocd_io__vdd (
    inout wire DVDD,
    inout wire DVSS,
    inout wire VDD,
    inout wire VSS
);
endmodule

module gf180mcu_ocd_io__vss (
    inout wire DVDD,
    inout wire DVSS,
    inout wire VDD,
    inout wire VSS
);
endmodule

`default_nettype wire
