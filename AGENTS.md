# Tenon Engineering Guide

## Architecture

Tenon is a hierarchical open-source SoC framework with three tiers.

| Tier | Name | Depends on | Responsibility |
|---|---|---|---|
| Tier0 | Foundation | None | Package-specific padframe, power domains, pin manifest and hardened reference views. |
| Tier1 | Management | Tier0 | Thin SoC wrapper for IO mapping, clock multiplication, debug and a large reserved Tier2 integration region. |
| Tier2 | Application | Tier0 and Tier1 | User design, supplied as RTL or a hardened macro, integrated through Tier1. |

Tier0's GDS/LEF is a standalone reference hardening result. It is not a normal macro that can surround a separately placed Tier1 macro. Tier1 must reuse the selected Tier0 SystemVerilog wrapper, pin manifest and physical constraints in its own chip-level hardening run so that the pad ring, PDN and center logic share one top-level route and signoff context.

## Tier0 Contract

- Supported profiles are QFN32, QFN64, QFN88 and QFN128. Their lead count equals the Tier0 package-pin count and excludes an exposed pad.
- Keep the logical `VDD/VSS` (core) rails electrically separate from `IOVDD/IOVSS` (IO) rails. Do not merge rails in RTL, PDN Tcl, package diagrams or macro integration.
- Management pins are stable: `mgmt_clk`, `mgmt_rst_n`, JTAG `TCK/TMS/TDI/TDO`, and UART `RX/TX`. GPIOs use the `gpio_i/gpio_o/gpio_oe` interface.
- `specs/tier0_profiles.json` is the source of truth for the common package contract. `specs/tier0_pdks.json` maps those logical rails and profiles to each PDK. Never hand-edit generated files in `flow/qfn*.yaml` or `docs/pinout/qfn*`; run `make generate` instead.
- Pin numbering is top-view, starts at the south-west corner, and advances counter-clockwise. The generated CSV is the package/wire-bond handoff source.

## PDK Adapters

- IHP SG13G2 uses `tenon_tier0_padframe`, `sg13g2_io`, and the committed LibreLane reference flows. IHP GPIOs use 30 mA bidirectional cells.
- Sky130A uses `tenon_tier0_padframe_sky130` and `sky130_fd_io__top_gpiov2`. Map core rails to `VCCD/VSSD` and IO rails to `VDDIO/VSSIO`; the support rails `VDDIO_Q`, `VCCHIB`, `VDDA`, `VSWITCH`, `VSSA` and `VSSIO_Q` must remain within the documented IO-cell topology. The baseline is `TinyTapeout/tt-gds-action@ttsky26c` with `sky130A`.
- GF180MCU uses `tenon_tier0_padframe_gf180` and `gf180mcu_ocd_io`. Map core rails to `DVDD/DVSS` and IO rails to `VDD/VSS`; do not short either pair. The reference PDK is `gf180mcuD` tag `1.8.0`.
- Sky130A and GF180MCU currently provide synthesizable adapters, fixed QFN tops, installed-PDK lint targets and CI simulations. Do not add physical LibreLane pad placement, floorplan or PDN configuration for either PDK until its installed IO LEF/GDS, pad pitch, corner cells, supply pin geometry and signoff flow have been audited.
- Preserve every production IO pad instance with synthesis attributes. CI-only behavioral PadCell models belong under `tb/`; never substitute them into synthesizable sources.

## Tier1 and Tier2 Rules

- Tier1 owns safe reset behavior, IO muxing, clock policy and debug access; Tier0 only transports signals through qualified IO cells.
- Tier1 must retain the selected Tier0 pinout and must produce the final full-chip GDS. It may reserve package pins only through a new, versioned profile.
- Tier2 RTL is hardened within Tier1. A hardened Tier2 macro must provide its GDS, LEF, timing library, power-pin names, halo and PDN connection data before physical integration. Tier2 never instantiates package pads directly.

## Implementation and Verification

- Use SystemVerilog for RTL and keep `default_nettype none` around every module.
- Run `make check-generated`, `make sim`, and `make format-check` before review. Run `make lint` and `make test` with the installed IHP PDK before IHP hardening; run `make lint-sky130` or `make lint-gf180` with the corresponding installed PDK before using those adapters.
- Hardening requires an existing `PDK_ROOT`; do not add Nix, Docker, PDK download or LibreLane installation/startup configuration to this repository.
- Do not suppress DRC, LVS, antenna or connectivity checks. The only allowed exception is the reference template's intentional `Checker.IllegalOverlap` suppression for bondpad/pad overlap reporting.
