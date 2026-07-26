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
| GF180MCU D | `tenon_tier0_padframe_gf180` | `gf180mcu_ocd_io` | `VDD/VSS` | `DVDD/DVSS` | `flow/gf180/qfn*.yaml` |
| Sky130A | `tenon_tier0_padframe_sky130` | `sky130_fd_io` with `sky130_ef_io` wrappers | `VCCD/VSSD` | `VDDIO/VSSIO` | `flow/sky130/qfn*.yaml` |


All physical flows default to installed Ciel revisions: IHP SG13G2 `3b5a704ba6738aa686b08706187830e6284d2a10`, Sky130A `8afc8346a57fe1ab7934ba5a6056ea8b43078e71`, and GF180MCU `f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7`.

Sky130 uses `sky130_fd_sc_hd`, raw `sky130_fd_io` base cells, and PDK-provided `sky130_ef_io` physical wrappers. Each GPIO retains its local `TIE_HI_ESD`/`TIE_LO_ESD` static-control loop. `make generate` emits fixed-coordinate, RTL-backed pad placement Tcl for every Sky130 QFN profile, plus PSM-visible package-to-PDN bridge Tcl. Magic extracts the final GDS and uses LEF interfaces only for the named PDK IO hard-macro hierarchy, so LVS checks top-level routing and every macro boundary without re-extracting unsupported internal geometry. The core grid serves only `VCCD/VSSD`; `VDDIO/VSSIO` remain separate peripheral special-net rings and never enter the core grid. Its pad placement is committed PDK-specific Tcl rather than repository-managed PDK installation.

GF180's core standard-cell library uses `VDD/VSS`; the OCD IO library calls its separate IO rails `DVDD/DVSS`. The GF180 pad ring therefore connects the core PDN only to `VDD/VSS` and maintains `DVDD/DVSS` as a separate abutted ring.

## Commands

PDK-independent checks and simulations use CI-only PadCell models:

```bash
make check-generated
make format-check
make sim
make sim-compile
```

Real-library linting uses the audited Ciel defaults:

```bash
make lint
make test
make lint-sky130
make lint-gf180
```

IHP reference hardening remains available for all package profiles:

```bash
make harden-all
```

GF180 hardening uses the OCD IO PDK assets:

```bash
make harden-gf180-all
```

All hardening targets use their audited Ciel defaults. Existing PDK roots may be supplied explicitly when required:

```bash
make harden-sky130-all
make harden-all PDK_ROOT=/path/to/IHP-Open-PDK
make harden-sky130-all SKY130_PDK_ROOT=/path/to/sky130-pdk-root
make harden-gf180-all GF180_PDK_ROOT=/path/to/gf180mcu
```

Set `SKIP_DRC=1` only for a user-authorized non-signoff iteration. It skips Magic and KLayout DRC only; LVS, antenna, connectivity and the remaining checks continue to run. `SKY130_DRT_OPT_ITERS` defaults to `64`; completion runs must retain a nonzero detailed-routing budget. Default hardening retains all checks except the IHP template's intentional bondpad/pad `Checker.IllegalOverlap` suppression.

LibreLane retains complete native runs and final views under `flow/ihp130/runs/tenon-qfn*/final/`, `flow/sky130/runs/tenon-qfn*/final/`, and `flow/gf180/runs/tenon-qfn*/final/`. The PDK-specific design directories make identical run tags unambiguous; hardening does not create duplicate views under `build/`.

## Verification

`make sim` runs the same management and GPIO behavior test against IHP, Sky130 and GF180 PadCell stand-ins for QFN32/64/88/128. `make test` additionally runs the IHP behavioral IO library. `make lint-sky130` and `make lint-gf180` compile each fixed QFN top against their respective installed production IO libraries.

## Physical Asset Attribution

The IHP bondpad macro in `ip/bondpad_70x70_novias` is copied from the Apache-2.0 IHP LibreLane reference template. The GF180 padring Tcl follows the Apache-2.0 LibreLane implementation supplied with the wafer.space GF180 project template. IHP attribution is recorded in `third_party/NOTICE.md`.
