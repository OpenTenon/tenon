// SPDX-License-Identifier: Apache-2.0
// Minimal functional Sky130 IO model for PDK-independent CI simulation.

`default_nettype none

module sky130_fd_io__top_gpiov2 (
    inout  wire       IN_H,
    inout  wire       PAD_A_NOESD_H,
    inout  wire       PAD_A_ESD_0_H,
    inout  wire       PAD_A_ESD_1_H,
    inout  wire       PAD,
    input  wire [2:0] DM,
    input  wire       HLD_H_N,
    output wire       IN,
    input  wire       INP_DIS,
    input  wire       IB_MODE_SEL,
    input  wire       ENABLE_H,
    input  wire       ENABLE_VDDA_H,
    input  wire       ENABLE_INP_H,
    input  wire       OE_N,
    output wire       TIE_HI_ESD,
    output wire       TIE_LO_ESD,
    input  wire       SLOW,
    input  wire       VTRIP_SEL,
    input  wire       HLD_OVR,
    input  wire       ANALOG_EN,
    input  wire       ANALOG_SEL,
    input  wire       ENABLE_VDDIO,
    input  wire       ENABLE_VSWITCH_H,
    input  wire       ANALOG_POL,
    input  wire       OUT,
    inout  wire       AMUXBUS_A,
    inout  wire       AMUXBUS_B,
    inout  wire       VSSA,
    inout  wire       VDDA,
    inout  wire       VSWITCH,
    inout  wire       VDDIO_Q,
    inout  wire       VCCHIB,
    inout  wire       VDDIO,
    inout  wire       VCCD,
    inout  wire       VSSIO,
    inout  wire       VSSD,
    inout  wire       VSSIO_Q
);
  assign PAD        = (OE_N == 1'b0 && DM != 3'b000 && DM != 3'b001) ? OUT : 1'bz;
  assign IN         = PAD;
  assign TIE_HI_ESD = 1'b1;
  assign TIE_LO_ESD = 1'b0;
endmodule

module sky130_fd_io__top_power_hvc_wpad (
    inout wire P_PAD,
    inout wire AMUXBUS_A,
    inout wire AMUXBUS_B,
    inout wire P_CORE,
    inout wire DRN_HVC,
    inout wire OGC_HVC,
    inout wire SRC_BDY_HVC,
    inout wire VSSA,
    inout wire VDDA,
    inout wire VSWITCH,
    inout wire VDDIO_Q,
    inout wire VCCHIB,
    inout wire VDDIO,
    inout wire VCCD,
    inout wire VSSIO,
    inout wire VSSD,
    inout wire VSSIO_Q
);
  tran (P_CORE, P_PAD);
endmodule

module sky130_fd_io__top_ground_hvc_wpad (
    inout wire G_PAD,
    inout wire AMUXBUS_A,
    inout wire AMUXBUS_B,
    inout wire G_CORE,
    inout wire DRN_HVC,
    inout wire OGC_HVC,
    inout wire SRC_BDY_HVC,
    inout wire VSSA,
    inout wire VDDA,
    inout wire VSWITCH,
    inout wire VDDIO_Q,
    inout wire VCCHIB,
    inout wire VDDIO,
    inout wire VCCD,
    inout wire VSSIO,
    inout wire VSSD,
    inout wire VSSIO_Q
);
  tran (G_CORE, G_PAD);
endmodule

module sky130_ef_io__gpiov2_pad_wrapped (
    input  wire       OUT,
    OE_N,
    HLD_H_N,
    ENABLE_H,
    ENABLE_INP_H,
    ENABLE_VDDA_H,
    input  wire       ENABLE_VSWITCH_H,
    ENABLE_VDDIO,
    INP_DIS,
    IB_MODE_SEL,
    VTRIP_SEL,
    input  wire       SLOW,
    HLD_OVR,
    ANALOG_EN,
    ANALOG_SEL,
    ANALOG_POL,
    input  wire [2:0] DM,
    inout  wire       VDDIO,
    VDDIO_Q,
    VDDA,
    VCCD,
    VSWITCH,
    VCCHIB,
    VSSA,
    VSSD,
    VSSIO_Q,
    inout  wire       VSSIO,
    PAD,
    PAD_A_NOESD_H,
    PAD_A_ESD_0_H,
    PAD_A_ESD_1_H,
    AMUXBUS_A,
    inout  wire       AMUXBUS_B,
    output wire       IN,
    IN_H,
    TIE_HI_ESD,
    TIE_LO_ESD
);
  assign PAD        = (OE_N == 1'b0 && DM != 3'b000 && DM != 3'b001) ? OUT : 1'bz;
  assign IN         = PAD;
  assign IN_H       = PAD;
  assign TIE_HI_ESD = 1'b1;
  assign TIE_LO_ESD = 1'b0;
endmodule

module sky130_ef_io__vccd_hvc_pad (
    inout wire AMUXBUS_A,
    AMUXBUS_B,
    DRN_HVC,
    SRC_BDY_HVC,
    VSSA,
    VDDA,
    VSWITCH,
    inout wire VDDIO_Q,
    VCCHIB,
    VDDIO,
    VCCD,
    VCCD_PAD,
    VSSIO,
    VSSD,
    VSSIO_Q
);
  tran (VCCD, VCCD_PAD);
endmodule

module sky130_ef_io__vddio_hvc_pad (
    inout wire AMUXBUS_A,
    AMUXBUS_B,
    DRN_HVC,
    SRC_BDY_HVC,
    VSSA,
    VDDA,
    VSWITCH,
    inout wire VDDIO_Q,
    VCCHIB,
    VDDIO,
    VDDIO_PAD,
    VCCD,
    VSSIO,
    VSSD,
    VSSIO_Q
);
  tran (VDDIO, VDDIO_PAD);
endmodule

module sky130_ef_io__vssd_hvc_pad (
    inout wire AMUXBUS_A,
    AMUXBUS_B,
    DRN_HVC,
    SRC_BDY_HVC,
    VSSA,
    VDDA,
    VSWITCH,
    inout wire VDDIO_Q,
    VCCHIB,
    VDDIO,
    VCCD,
    VSSIO,
    VSSD,
    VSSD_PAD,
    VSSIO_Q
);
  tran (VSSD, VSSD_PAD);
endmodule

module sky130_ef_io__vssio_hvc_pad (
    inout wire AMUXBUS_A,
    AMUXBUS_B,
    DRN_HVC,
    SRC_BDY_HVC,
    VSSA,
    VDDA,
    VSWITCH,
    inout wire VDDIO_Q,
    VCCHIB,
    VDDIO,
    VCCD,
    VSSIO,
    VSSIO_PAD,
    VSSD,
    VSSIO_Q
);
  tran (VSSIO, VSSIO_PAD);
endmodule

`default_nettype wire
