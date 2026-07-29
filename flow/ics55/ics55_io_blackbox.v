// SPDX-License-Identifier: Apache-2.0
// Structural black boxes matching the commercial ICS55 IO and PLL LEF views.

`default_nettype none

module PB4 (
    VSSD,
    VSS,
    PAD,
    OEN,
    C,
    IE,
    I,
    VDD25,
    VDD,
    FP,
    FPB
);
  inout VSSD, VSS, PAD, VDD25, VDD, FP, FPB;
  input OEN, IE, I;
  output C;
endmodule

module PVDD1 (
    VDD25,
    VSS,
    VDD,
    VSSD,
    FP,
    FPB
);
  inout VDD25, VSS, VDD, VSSD, FP, FPB;
endmodule
module PVSS1 (
    VSSD,
    VSS,
    VDD25,
    VDD,
    FPB,
    FP
);
  inout VSSD, VSS, VDD25, VDD, FPB, FP;
endmodule
module PVDD2 (
    VDD25,
    VDD,
    FP,
    FPB,
    VSS,
    VSSD
);
  inout VDD25, VDD, FP, FPB, VSS, VSSD;
endmodule
module PVSS2 (
    VDD25,
    FP,
    FPB,
    VDD,
    VSSD,
    VSS
);
  inout VDD25, FP, FPB, VDD, VSSD, VSS;
endmodule

module PVDD1CAP (
    SVDD1CAP
);
  inout SVDD1CAP;
endmodule
module PVSS1CAP (
    SVSS1CAP
);
  inout SVSS1CAP;
endmodule
module PVDD3AP (
    SAVDD
);
  inout SAVDD;
endmodule
module PVSS3AP (
    SAVSS
);
  inout SAVSS;
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
    VSSD,
    VSS,
    VDD25,
    VDD,
    FP,
    FPB,
    XIN,
    E,
    XC,
    XOUT
);
  inout VSSD, VSS, VDD25, VDD, FP, FPB, XIN, XOUT;
  input E;
  output XC;
endmodule

module PLL_TOP (
    BP,
    CKOUT1,
    CKOUT2,
    CKTST,
    EN,
    N,
    OD,
    REFCLK,
    SELECT,
    AVDD,
    AVSS,
    DVDD,
    DVDD_DRV,
    DVSS,
    DVSS_DRV
);
  input BP, EN, REFCLK, SELECT;
  input [7:0] N;
  input [1:0] OD;
  output CKOUT1, CKOUT2, CKTST;
  inout AVDD, AVSS, DVDD, DVDD_DRV, DVSS, DVSS_DRV;
endmodule



`default_nettype wire
