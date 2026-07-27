# QFN32 Pin Manifest

Top view: P1 is the south-west corner; numbering proceeds counter-clockwise. QFN lead count excludes an exposed pad.

| Pin | Side | Slot | Function | Direction | PDK cell | Core-facing signal |
|---|---|---:|---|---|---|---|
| P1 | south | 1 | IOVDD | power | PVDD2 | IOVDD |
| P2 | south | 2 | mgmt_clk | input | PB4 | mgmt_clk_i |
| P3 | south | 3 | GPIO | inout | PB4 | gpio[0] |
| P4 | south | 4 | GPIO | inout | PB4 | gpio[1] |
| P5 | south | 5 | IOVSS | ground | PVSS2 | IOVSS |
| P6 | south | 6 | GPIO | inout | PB4 | gpio[2] |
| P7 | south | 7 | mgmt_rst_n | input | PB4 | mgmt_rst_n_i |
| P8 | south | 8 | GPIO | inout | PB4 | gpio[3] |
| P9 | east | 1 | VDD | power | PVDD1 | VDD |
| P10 | east | 2 | jtag_tck | input | PB4 | jtag_tck_i |
| P11 | east | 3 | GPIO | inout | PB4 | gpio[4] |
| P12 | east | 4 | GPIO | inout | PB4 | gpio[5] |
| P13 | east | 5 | VSS | ground | PVSS1 | VSS |
| P14 | east | 6 | GPIO | inout | PB4 | gpio[6] |
| P15 | east | 7 | jtag_tms | input | PB4 | jtag_tms_i |
| P16 | east | 8 | GPIO | inout | PB4 | gpio[7] |
| P17 | north | 1 | IOVDD | power | PVDD2 | IOVDD |
| P18 | north | 2 | jtag_tdi | input | PB4 | jtag_tdi_i |
| P19 | north | 3 | GPIO | inout | PB4 | gpio[8] |
| P20 | north | 4 | GPIO | inout | PB4 | gpio[9] |
| P21 | north | 5 | IOVSS | ground | PVSS2 | IOVSS |
| P22 | north | 6 | GPIO | inout | PB4 | gpio[10] |
| P23 | north | 7 | jtag_tdo | output | PB4 | jtag_tdo_o |
| P24 | north | 8 | GPIO | inout | PB4 | gpio[11] |
| P25 | west | 1 | VDD | power | PVDD1 | VDD |
| P26 | west | 2 | uart_rx | input | PB4 | uart_rx_i |
| P27 | west | 3 | GPIO | inout | PB4 | gpio[12] |
| P28 | west | 4 | GPIO | inout | PB4 | gpio[13] |
| P29 | west | 5 | VSS | ground | PVSS1 | VSS |
| P30 | west | 6 | GPIO | inout | PB4 | gpio[14] |
| P31 | west | 7 | uart_tx | output | PB4 | uart_tx_o |
| P32 | west | 8 | GPIO | inout | PB4 | gpio[15] |
