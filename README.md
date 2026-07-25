# Tenon

Tenon is a customizable open-source SoC framework for hierarchical multi-project integration. Tier0 Foundation defines one QFN package contract and PDK-specific padframe adapters for IHP SG13G2 (IHP130), Sky130A and GF180MCU D.

## Three-tier model

| Tier | Role | Integration model |
|---|---|---|
| Tier0 Foundation | Padframe, package pinout, core/IO PDN and hardened reference views | Reused by source, manifest and physical constraints. |
| Tier1 Management | IO mapping, clock multiplication, debug probes and reserved application area | Re-hardens a complete chip around Tier1 logic. |
| Tier2 Application | User design | May originate as RTL or a hardened macro. |

Tier0 hardening outputs are standalone physical reference views. A pad-ring GDS cannot be placed as an ordinary macro around a separate Tier1 block; Tier1 must reuse the selected Tier0 wrapper and produce the final full-chip GDS.

## Tier0 Profiles

| Profile | Package leads | IOVDD/IOVSS/VDD/VSS pads | Management pins | GPIOs |
|---|---:|---:|---:|---:|
| QFN32 | 32 | 2 each | 8 | 16 |
| QFN64 | 64 | 4 each | 8 | 40 |
| QFN88 | 88 | 6 each | 8 | 56 |
| QFN128 | 128 | 8 each | 8 | 88 |

The fixed management pins are `mgmt_clk`, `mgmt_rst_n`, JTAG `TCK/TMS/TDI/TDO`, and UART `RX/TX`. The common core-facing interface exposes `mgmt_clk_i`, `mgmt_rst_ni`, `jtag_tck_i`, `jtag_tms_i`, `jtag_tdi_i`, `uart_rx_i`, `jtag_tdo_o`, `uart_tx_o`, `gpio_i[]`, `gpio_o[]`, and `gpio_oe[]`.

QFN N means N wire-bondable package leads. An optional exposed pad is a package and assembly decision, normally tied to VSS, and is not a Tier0 pad. The exact pin maps are generated under `docs/pinout/`; numbering is top-view from the south-west corner and advances counter-clockwise.

## PDK Support

`specs/tier0_profiles.json` is the package source of truth. `specs/tier0_pdks.json` records PDK-specific cell, rail and floorplan contracts.

| PDK | Adapter | IO cells | Core rails | IO rails | Physical flow |
|---|---|---|---|---|---|
| IHP SG13G2 | `tenon_tier0_padframe` | `sg13g2_io` | `VDD/VSS` | `IOVDD/IOVSS` | `flow/ihp130/qfn*.yaml` |
| Sky130A | `tenon_tier0_padframe_sky130` | `sky130_fd_io` | `VCCD/VSSD` | `VDDIO/VSSIO` | RTL, lint and simulation |
| GF180MCU D | `tenon_tier0_padframe_gf180` | `gf180mcu_ocd_io` | `VDD/VSS` | `DVDD/DVSS` | `flow/gf180/qfn*.yaml` |

Sky130 uses the Tiny Tapeout workflow baseline `TinyTapeout/tt-gds-action@ttsky26c` with `sky130A`. GF180 uses `gf180mcuD`, `gf180mcu_fd_sc_mcu7t5v0` and `gf180mcu_ocd_io`; the audited Ciel PDK revision is `f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7`.

GF180's core standard-cell library uses `VDD/VSS`; the OCD IO library calls its separate IO rails `DVDD/DVSS`. The GF180 pad ring therefore connects the core PDN only to `VDD/VSS` and maintains `DVDD/DVSS` as a separate abutted ring.

## Commands

PDK-independent checks and simulations use CI-only PadCell models:

```bash
make check-generated
make format-check
make sim
make sim-compile
```

Use existing PDK installations for real-library linting:

```bash
make lint PDK_ROOT=/path/to/IHP-Open-PDK
make test PDK_ROOT=/path/to/IHP-Open-PDK

make lint-sky130 PDK_ROOT=/path/to/open-pdks
make lint-gf180 GF180_PDK_ROOT=/path/to/gf180mcu
```

IHP reference hardening remains available for all package profiles:

```bash
make harden-all PDK_ROOT=/path/to/IHP-Open-PDK
```

GF180 hardening uses the OCD IO PDK assets:

```bash
make harden-gf180-all GF180_PDK_ROOT=/path/to/gf180mcu
```

Set `SKIP_DRC=1` only for a user-authorized non-signoff iteration. It skips Magic and KLayout DRC only; LVS, antenna, connectivity and the remaining checks continue to run. Default hardening retains all checks except the IHP template's intentional bondpad/pad `Checker.IllegalOverlap` suppression.


LibreLane retains complete native runs and final views under `flow/ihp130/runs/tenon-qfn*/final/` and `flow/gf180/runs/tenon-qfn*/final/`. The PDK-specific design directories make the identical run tags unambiguous; hardening does not create duplicate views under `build/`.
## Verification

`make sim` runs the same management and GPIO behavior test against IHP, Sky130 and GF180 PadCell stand-ins for QFN32/64/88/128. `make test` additionally runs the IHP behavioral IO library. `make lint-gf180` compiles each GF180 fixed QFN top against the installed `gf180mcu_ocd_io` library.

## Physical Asset Attribution

The IHP bondpad macro in `ip/bondpad_70x70_novias` is copied from the Apache-2.0 IHP LibreLane reference template. The GF180 padring Tcl follows the Apache-2.0 LibreLane implementation supplied with the wafer.space GF180 project template. IHP attribution is recorded in `third_party/NOTICE.md`.
