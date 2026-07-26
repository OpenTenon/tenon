// SPDX-License-Identifier: Apache-2.0

`default_nettype none

(* blackbox *) module sky130_ef_io__gpiov2_pad_wrapped (
    IN_H,
    PAD_A_NOESD_H,
    PAD_A_ESD_0_H,
    PAD_A_ESD_1_H,
    PAD,
    DM,
    HLD_H_N,
    IN,
    INP_DIS,
    IB_MODE_SEL,
    ENABLE_H,
    ENABLE_VDDA_H,
    ENABLE_INP_H,
    OE_N,
    TIE_HI_ESD,
    TIE_LO_ESD,
    SLOW,
    VTRIP_SEL,
    HLD_OVR,
    ANALOG_EN,
    ANALOG_SEL,
    ENABLE_VDDIO,
    ENABLE_VSWITCH_H,
    ANALOG_POL,
    OUT,
    AMUXBUS_A,
    AMUXBUS_B,
    VSSA,
    VDDA,
    VSWITCH,
    VDDIO_Q,
    VCCHIB,
    VDDIO,
    VCCD,
    VSSIO,
    VSSD,
    VSSIO_Q
);
  input OUT, OE_N, HLD_H_N, ENABLE_H, ENABLE_INP_H, ENABLE_VDDA_H;
  input ENABLE_VSWITCH_H, ENABLE_VDDIO, INP_DIS, IB_MODE_SEL, VTRIP_SEL;
  input SLOW, HLD_OVR, ANALOG_EN, ANALOG_SEL, ANALOG_POL;
  input [2:0] DM;
  inout VDDIO, VDDIO_Q, VDDA, VCCD, VSWITCH, VCCHIB, VSSA, VSSD, VSSIO_Q;
  inout VSSIO, PAD, PAD_A_NOESD_H, PAD_A_ESD_0_H, PAD_A_ESD_1_H, AMUXBUS_A, AMUXBUS_B;
  output IN, IN_H, TIE_HI_ESD, TIE_LO_ESD;
endmodule
(* blackbox *) module sky130_ef_io__vccd_hvc_pad (
    AMUXBUS_A,
    AMUXBUS_B,
    DRN_HVC,
    SRC_BDY_HVC,
    VSSA,
    VDDA,
    VSWITCH,
    VDDIO_Q,
    VCCHIB,
    VDDIO,
    VCCD,
    VCCD_PAD,
    VSSIO,
    VSSD,
    VSSIO_Q
);
  inout AMUXBUS_A, AMUXBUS_B, DRN_HVC, SRC_BDY_HVC, VSSA, VDDA, VSWITCH, VDDIO_Q, VCCHIB, VDDIO, VCCD, VCCD_PAD, VSSIO, VSSD, VSSIO_Q;
endmodule
(* blackbox *) module sky130_ef_io__vddio_hvc_pad (
    AMUXBUS_A,
    AMUXBUS_B,
    DRN_HVC,
    SRC_BDY_HVC,
    VSSA,
    VDDA,
    VSWITCH,
    VDDIO_Q,
    VCCHIB,
    VDDIO,
    VDDIO_PAD,
    VCCD,
    VSSIO,
    VSSD,
    VSSIO_Q
);
  inout AMUXBUS_A, AMUXBUS_B, DRN_HVC, SRC_BDY_HVC, VSSA, VDDA, VSWITCH, VDDIO_Q, VCCHIB, VDDIO, VDDIO_PAD, VCCD, VSSIO, VSSD, VSSIO_Q;
endmodule
(* blackbox *) module sky130_ef_io__vssd_hvc_pad (
    AMUXBUS_A,
    AMUXBUS_B,
    DRN_HVC,
    SRC_BDY_HVC,
    VSSA,
    VDDA,
    VSWITCH,
    VDDIO_Q,
    VCCHIB,
    VDDIO,
    VCCD,
    VSSIO,
    VSSD,
    VSSD_PAD,
    VSSIO_Q
);
  inout AMUXBUS_A, AMUXBUS_B, DRN_HVC, SRC_BDY_HVC, VSSA, VDDA, VSWITCH, VDDIO_Q, VCCHIB, VDDIO, VCCD, VSSIO, VSSD, VSSD_PAD, VSSIO_Q;
endmodule
(* blackbox *) module sky130_ef_io__vssio_hvc_pad (
    AMUXBUS_A,
    AMUXBUS_B,
    DRN_HVC,
    SRC_BDY_HVC,
    VSSA,
    VDDA,
    VSWITCH,
    VDDIO_Q,
    VCCHIB,
    VDDIO,
    VCCD,
    VSSIO,
    VSSIO_PAD,
    VSSD,
    VSSIO_Q
);
  inout AMUXBUS_A, AMUXBUS_B, DRN_HVC, SRC_BDY_HVC, VSSA, VDDA, VSWITCH, VDDIO_Q, VCCHIB, VDDIO, VCCD, VSSIO, VSSIO_PAD, VSSD, VSSIO_Q;
endmodule

`default_nettype wire
