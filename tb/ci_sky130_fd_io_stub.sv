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
    inout  wire       TIE_HI_ESD,
    inout  wire       TIE_LO_ESD,
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
  assign PAD = (OE_N == 1'b0 && DM != 3'b000 && DM != 3'b001) ? OUT : 1'bz;
  assign IN  = PAD;
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

`default_nettype wire
