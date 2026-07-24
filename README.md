# Tenon

Tenon is a customizable open-source SoC framework for hierarchical multi-project integration. Tier0 Foundation defines one QFN package contract and PDK-specific padframe adapters for IHP SG13G2 (IHP130), Sky130A and GF180MCU.

## Three-tier model

| Tier | Role | Integration model |
|---|---|---|
| Tier0 Foundation | Padframe, package pinout, core/IO PDN and hardened reference views | Reused by source, manifest and physical constraints. |
| Tier1 Management | IO mapping, clock multiplication, debug probes and reserved application area | Re-hardens a complete chip around Tier1 logic. |
| Tier2 Application | User design | May originate as RTL or a hardened macro. |

Tier0 hardening outputs are standalone physical reference views. A pad-ring GDS cannot be placed as an ordinary macro around a separate Tier1 block; Tier1 must reuse the selected Tier0 wrapper and produce the final full-chip GDS.

## Tier0 profiles

| Profile | Package leads | IOVDD/IOVSS/VDD/VSS pads | Management pins | GPIOs |
|---|---:|---:|---:|---:|
| QFN32 | 32 | 2 each | 8 | 16 |
| QFN64 | 64 | 4 each | 8 | 40 |
| QFN88 | 88 | 6 each | 8 | 56 |
| QFN128 | 128 | 8 each | 8 | 88 |

The fixed management pins are `mgmt_clk`, `mgmt_rst_n`, JTAG `TCK/TMS/TDI/TDO`, and UART `RX/TX`. The common core-facing interface exposes `mgmt_clk_i`, `mgmt_rst_ni`, `jtag_tck_i`, `jtag_tms_i`, `jtag_tdi_i`, `uart_rx_i`, `jtag_tdo_o`, `uart_tx_o`, `gpio_i[]`, `gpio_o[]`, and `gpio_oe[]`.

QFN N means N wire-bondable package leads. An optional exposed pad is a package and assembly decision, normally tied to VSS, and is not a Tier0 pad. The exact pin maps are generated under `docs/pinout/`; numbering is top-view from the south-west corner and advances counter-clockwise.

## PDK support

`specs/tier0_profiles.json` is the package source of truth. `specs/tier0_pdks.json` records PDK-specific cell and rail contracts.

| PDK | Adapter | IO cells | Core rails | IO rails | Status |
|---|---|---|---|---|---|
| IHP SG13G2 | `tenon_tier0_padframe` | `sg13g2_io` | `VDD/VSS` | `IOVDD/IOVSS` | QFN32/64/88/128 LibreLane reference flows |
| Sky130A | `tenon_tier0_padframe_sky130` | `sky130_fd_io` | `VCCD/VSSD` | `VDDIO/VSSIO` | RTL, fixed QFN tops, installed-PDK lint, CI simulation |
| GF180MCU D | `tenon_tier0_padframe_gf180` | `gf180mcu_ocd_io` | `DVDD/DVSS` | `VDD/VSS` | RTL, fixed QFN tops, installed-PDK lint, CI simulation |

Sky130 uses the Tiny Tapeout workflow baseline `TinyTapeout/tt-gds-action@ttsky26c` with `sky130A`. GF180 uses the wafer.space template's `gf180mcuD` / `gf180mcu_ocd_io` choice and reference PDK tag `1.8.0`.

The four logical rails remain separate for every PDK. For Sky130, `VDDIO_Q`, `VCCHIB`, `VDDA`, `VSWITCH`, `VSSA` and `VSSIO_Q` are connected only as required by the `sky130_fd_io` cell topology; they are not an external rail merge. For GF180, the apparently reversed names are intentional: the OCD IO library calls its digital core supply `DVDD/DVSS` and its IO supply `VDD/VSS`.

The IHP flows are the only committed physical hardening flows. Sky130 and GF180 require an installed-PDK audit of IO LEF/GDS, pad pitch, corner/filler cells, supply geometry and PDN before adding physical LibreLane configurations. This repository deliberately does not invent those values or include PDK download, Nix, Docker or LibreLane startup configuration.

## Commands

PDK-independent checks and simulations use CI-only PadCell models:

```bash
make check-generated
make format-check
make sim
make sim-compile
```

Use existing PDK installations for real-library linting. The IO model variables can be overridden when a PDK packages its Verilog views differently.

```bash
make lint PDK_ROOT=/path/to/IHP-Open-PDK
make test PDK_ROOT=/path/to/IHP-Open-PDK

make lint-sky130 PDK_ROOT=/path/to/open-pdks
make lint-gf180 PDK_ROOT=/path/to/open-pdks
```

IHP reference hardening remains available for all four package profiles:

```bash
make harden-qfn32 PDK_ROOT=/path/to/IHP-Open-PDK
make harden-all PDK_ROOT=/path/to/IHP-Open-PDK
```

Set `SKIP_DRC=1` only for a non-signoff iteration. It skips Magic and KLayout DRC; default hardening retains all checks except the reference template's intentional bondpad/pad `Checker.IllegalOverlap` suppression. Outputs are saved below `build/qfn*/final/` and run logs below `flow/runs/`.

## Verification

`make sim` runs the same management and GPIO behavior test against IHP, Sky130 and GF180 PadCell stand-ins for QFN32/64/88/128. `make test` additionally runs the IHP behavioral IO library. `make lint-sky130` and `make lint-gf180` compile each fixed QFN top against the installed vendor/open-PDK library.

## Physical asset attribution

The bondpad macro in `ip/bondpad_70x70_novias` is copied from the Apache-2.0 IHP LibreLane reference template. Its attribution is recorded in `third_party/NOTICE.md`.
