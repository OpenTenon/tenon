// SPDX-License-Identifier: Apache-2.0
// Behavioral commercial-ICS55 stand-ins used only by CI simulation.

`default_nettype none

module PB4 (
    inout  wire VSSD,
    inout  wire VSS,
    inout  wire PAD,
    input  wire OEN,
    output wire C,
    input  wire IE,
    input  wire I,
    inout  wire VDD25,
    inout  wire VDD,
    inout  wire FP,
    inout  wire FPB
);
  assign PAD = OEN ? 1'bz : I;
  assign C   = IE ? PAD : 1'b0;
endmodule

module PVDD1 (
    inout wire VDD25,
    inout wire VSS,
    inout wire VDD,
    inout wire VSSD,
    inout wire FP,
    inout wire FPB
);
endmodule
module PVSS1 (
    inout wire VSSD,
    inout wire VSS,
    inout wire VDD25,
    inout wire VDD,
    inout wire FPB,
    inout wire FP
);
endmodule
module PVDD2 (
    inout wire VDD25,
    inout wire VDD,
    inout wire FP,
    inout wire FPB,
    inout wire VSS,
    inout wire VSSD
);
endmodule
module PVSS2 (
    inout wire VDD25,
    inout wire FP,
    inout wire FPB,
    inout wire VDD,
    inout wire VSSD,
    inout wire VSS
);
endmodule
module PVDD1CAP (
    inout wire SVDD1CAP
);
endmodule
module PVSS1CAP (
    inout wire SVSS1CAP
);
endmodule
module PVDD3AP (
    inout wire SAVDD
);
endmodule
module PVSS3AP (
    inout wire SAVSS
);
endmodule
module PADI30 ();
endmodule
module PADO30 ();
endmodule
module PFILL10 ();
endmodule
module PFILL50 ();
endmodule
module PFILL20 ();
endmodule
module PFILL5 ();
endmodule
module PFILL2 ();
endmodule
module PFILL1 ();
endmodule
module PFILL01 ();
endmodule
module PFILL001 ();
endmodule
module PCORNER ();
endmodule

module PXWE1 (
    inout  wire VSSD,
    inout  wire VSS,
    inout  wire VDD25,
    inout  wire VDD,
    inout  wire FP,
    inout  wire FPB,
    inout  wire XIN,
    input  wire E,
    output wire XC,
    inout  wire XOUT
);
  assign XC   = E ? XIN : 1'b0;
  assign XOUT = 1'bz;
endmodule

module PLL_TOP (
    input  wire       BP,
    output wire       CKOUT1,
    output wire       CKOUT2,
    output wire       CKTST,
    input  wire       EN,
    input  wire [7:0] N,
    input  wire [1:0] OD,
    input  wire       REFCLK,
    input  wire       SELECT,
    inout  wire       AVDD,
    inout  wire       AVSS,
    inout  wire       DVDD,
    inout  wire       DVDD_DRV,
    inout  wire       DVSS,
    inout  wire       DVSS_DRV
);
  assign CKOUT1 = EN && !BP ? REFCLK : 1'b0;
  assign CKOUT2 = EN && !BP ? REFCLK : 1'b0;
  assign CKTST  = SELECT ? REFCLK : 1'b0;
endmodule


module P65_1233_PBMUX (
    output wire C,
    inout  wire PAD,
    input  wire IE,
    input  wire CS,
    input  wire I,
    input  wire OE,
    input  wire OD,
    input  wire PU,
    input  wire PD,
    input  wire DS0,
    input  wire DS1,
    inout  wire VDD,
    inout  wire VDDIO,
    inout  wire VSS,
    inout  wire VSSIO
);
  assign PAD = OE ? I : 1'bz;
  assign C   = IE ? PAD : I;
endmodule
module P65_1233_VDD3 (
    inout wire VDD,
    inout wire VDDIO,
    inout wire VSS,
    inout wire VSSIO
);
endmodule
module P65_1233_VSS3 (
    inout wire VSS,
    inout wire VDD,
    inout wire VDDIO,
    inout wire VSSIO
);
endmodule
module P65_1233_VDDIO3 (
    inout wire VDDIO
);
endmodule
module P65_1233_VSSIO3 (
    inout wire VSSIO
);
endmodule
module P65_1233_CORNER (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule
module P65_1233_FILLER50 (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule
module P65_1233_FILLER20 (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule
module P65_1233_FILLER10 (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule
module P65_1233_FILLER5 (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule
module P65_1233_FILLER2 (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule
module P65_1233_FILLER1 (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule
module P65_1233_FILLER01 (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule
module P65_1233_FILLER001 (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule
module P65_1233_FILLER0005 (
    inout wire VDD,
    VSS,
    VDDIO,
    VSSIO
);
endmodule

`default_nettype wire
