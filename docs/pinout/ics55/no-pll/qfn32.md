# QFN32 Pin Manifest

Top view: P1 is the south-west corner; numbering proceeds counter-clockwise. QFN lead count excludes an exposed pad.

| Pin | Side | Slot | Function | Direction | PDK cell | Core-facing signal |
|---|---|---:|---|---|---|---|
| P1 | south | 1 | IOVDD | power | P65_1233_VDDIO3 | IOVDD |
| P2 | south | 2 | mgmt_clk | input | P65_1233_PBMUX | mgmt_clk_i |
| P3 | south | 3 | GPIO | inout | P65_1233_PBMUX | gpio[0] |
| P4 | south | 4 | GPIO | inout | P65_1233_PBMUX | gpio[1] |
| P5 | south | 5 | IOVSS | ground | P65_1233_VSSIO3 | IOVSS |
| P6 | south | 6 | GPIO | inout | P65_1233_PBMUX | gpio[2] |
| P7 | south | 7 | mgmt_rst_n | input | P65_1233_PBMUX | mgmt_rst_n_i |
| P8 | south | 8 | GPIO | inout | P65_1233_PBMUX | gpio[3] |
| P9 | east | 1 | VDD | power | P65_1233_VDD3 | VDD |
| P10 | east | 2 | jtag_tck | input | P65_1233_PBMUX | jtag_tck_i |
| P11 | east | 3 | GPIO | inout | P65_1233_PBMUX | gpio[4] |
| P12 | east | 4 | GPIO | inout | P65_1233_PBMUX | gpio[5] |
| P13 | east | 5 | VSS | ground | P65_1233_VSS3 | VSS |
| P14 | east | 6 | GPIO | inout | P65_1233_PBMUX | gpio[6] |
| P15 | east | 7 | jtag_tms | input | P65_1233_PBMUX | jtag_tms_i |
| P16 | east | 8 | GPIO | inout | P65_1233_PBMUX | gpio[7] |
| P17 | north | 1 | IOVDD | power | P65_1233_VDDIO3 | IOVDD |
| P18 | north | 2 | jtag_tdi | input | P65_1233_PBMUX | jtag_tdi_i |
| P19 | north | 3 | GPIO | inout | P65_1233_PBMUX | gpio[8] |
| P20 | north | 4 | GPIO | inout | P65_1233_PBMUX | gpio[9] |
| P21 | north | 5 | IOVSS | ground | P65_1233_VSSIO3 | IOVSS |
| P22 | north | 6 | GPIO | inout | P65_1233_PBMUX | gpio[10] |
| P23 | north | 7 | jtag_tdo | output | P65_1233_PBMUX | jtag_tdo_o |
| P24 | north | 8 | GPIO | inout | P65_1233_PBMUX | gpio[11] |
| P25 | west | 1 | VDD | power | P65_1233_VDD3 | VDD |
| P26 | west | 2 | uart_rx | input | P65_1233_PBMUX | uart_rx_i |
| P27 | west | 3 | GPIO | inout | P65_1233_PBMUX | gpio[12] |
| P28 | west | 4 | GPIO | inout | P65_1233_PBMUX | gpio[13] |
| P29 | west | 5 | VSS | ground | P65_1233_VSS3 | VSS |
| P30 | west | 6 | GPIO | inout | P65_1233_PBMUX | gpio[14] |
| P31 | west | 7 | uart_tx | output | P65_1233_PBMUX | uart_tx_o |
| P32 | west | 8 | GPIO | inout | P65_1233_PBMUX | gpio[15] |
