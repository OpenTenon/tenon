PYTHON           ?= python3
IVERILOG         ?= iverilog
VVP              ?= vvp
MBAKE            ?= mbake
VERIBLE_FORMAT   ?= verible-verilog-format
LIBRELANE        ?= librelane
PDK              ?= ihp-sg13g2
IHP_CIEL_ROOT    ?= $(HOME)/.ciel/ciel/ihp-sg13g2/versions/3b5a704ba6738aa686b08706187830e6284d2a10
OPENROAD         ?= openroad
PDK_ROOT         ?= $(IHP_CIEL_ROOT)
SKIP_DRC         ?= 0
SKIP_MAGIC_SPICE ?= 0
SKIP_NETGEN_LVS  ?= 0
UNSIGNED_IO_PG   ?= 0

IHP_RTL    := rtl/tenon_tier0_padframe.sv rtl/tenon_tier0_reference.sv rtl/tenon_tier0_variants.sv
SKY130_RTL := rtl/tenon_tier0_padframe_sky130.sv rtl/tenon_tier0_pdk_reference.sv rtl/tenon_tier0_pdk_variants.sv
GF180_RTL  := rtl/tenon_tier0_padframe_gf180.sv rtl/tenon_tier0_pdk_reference.sv rtl/tenon_tier0_pdk_variants.sv
ICS55_RTL  := rtl/tenon_tier0_padframe_ics55.sv rtl/tenon_tier0_padframe_ics55_p65.sv rtl/tenon_tier0_padframe_ics55_p65_fillers.sv rtl/tenon_tier0_padframe_ics55_qfn32_no_pll_fillers.sv rtl/tenon_tier0_pdk_reference.sv rtl/tenon_tier0_pdk_variants.sv

IHP_IO_MODEL         ?= $(PDK_ROOT)/$(PDK)/libs.ref/sg13g2_io/verilog/sg13g2_io.v
SKY130_PDK           ?= sky130A
SKY130_CIEL_ROOT     ?= $(HOME)/.ciel/ciel/sky130/versions/8afc8346a57fe1ab7934ba5a6056ea8b43078e71
SKY130_PDK_ROOT      ?= $(SKY130_CIEL_ROOT)
SKY130_SCL           ?= sky130_fd_sc_hd
SKY130_DRT_OPT_ITERS ?= 64
SKY130_IO_MODEL      ?= $(SKY130_PDK_ROOT)/$(SKY130_PDK)/libs.ref/sky130_fd_io/verilog/sky130_fd_io.v
SKY130_EF_IO_MODEL   ?= $(SKY130_PDK_ROOT)/$(SKY130_PDK)/libs.ref/sky130_fd_io/verilog/sky130_ef_io.v
GF180_PDK            ?= gf180mcuD
GF180_CIEL_ROOT      ?= $(HOME)/.ciel/ciel/gf180mcu/versions/f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7
GF180_PDK_ROOT       ?= $(GF180_CIEL_ROOT)
GF180_SCL            ?= gf180mcu_fd_sc_mcu7t5v0
GF180_PAD            ?= gf180mcu_ocd_io
ICS55_PDK            ?= ics55
ICS55_PDK_ROOT       ?= $(HOME)/.ciel/manual
ICS55_SCL            ?= ics55_LLSC_H7CR
ICS55_P65_PAD        ?= ics55_io_p65
ICS55_LEGACY_PAD     ?= ics55_io_3p3
ICS55_DRT_THREADS    ?= 16
ICS55_DRT_OPT_ITERS  ?= 1
GF180_IO_MODEL       ?= $(GF180_PDK_ROOT)/$(GF180_PDK)/libs.ref/$(GF180_PAD)/verilog/$(GF180_PAD).v

CI_IHP_IO_MODEL    := tb/ci_sg13g2_io_stub.sv
CI_SKY130_IO_MODEL := tb/ci_sky130_fd_io_stub.sv
CI_ICS55_IO_MODEL  := tb/ci_ics55_io_stub.sv
CI_GF180_IO_MODEL  := tb/ci_gf180mcu_ocd_io_stub.sv
BUILD_DIR          := build

IHP_FLOW_DIR    := flow/ihp130
GF180_FLOW_DIR  := flow/gf180
ICS55_FLOW_DIR  := flow/ics55
SMOKE_DIR       := smoke
SKY130_FLOW_DIR := flow/sky130
.DEFAULT_GOAL := help

ifneq ($(filter 1 true TRUE yes YES,$(SKIP_DRC)),)
IHP_DRC_OVERRIDES := --override-config RUN_MAGIC_DRC=false --override-config RUN_KLAYOUT_DRC=false
GF180_DRC_SKIPS   := --skip Magic.DRC --skip KLayout.DRC
ICS55_DRC_SKIPS   := --skip Magic.DRC --skip KLayout.DRC
SKY130_DRC_SKIPS  := --skip Magic.DRC --skip KLayout.DRC
endif

ifneq ($(filter 1 true TRUE yes YES,$(SKIP_MAGIC_SPICE)),)
ICS55_MAGIC_SPICE_SKIP := --skip Magic.SpiceExtraction
endif

ifneq ($(filter 1 true TRUE yes YES,$(SKIP_NETGEN_LVS)),)
ICS55_NETGEN_LVS_SKIP := --skip Netgen.LVS
endif

.PHONY: help generate check-generated check format format-check mk-format mk-format-check rtl-format rtl-format-check sim sim-compile sim-compile-ihp sim-compile-sky130 sim-compile-gf180 sim-compile-ics55 sim-ihp sim-sky130 sim-gf180 sim-ics55 check-pdk check-pdk-ihp check-pdk-sky130 check-pdk-gf180 check-pdk-ics55 lint lint-ihp lint-sky130 lint-gf180 lint-ics55 lint-ics55-no-pll lint-ics55-pll lint-smoke smoke-run smoke-report lint-qfn32 lint-qfn64 lint-qfn88 lint-qfn128 test harden-all harden-qfn32 harden-qfn64 harden-qfn88 harden-qfn128 harden-gf180-all harden-gf180-qfn32 harden-gf180-qfn64 harden-gf180-qfn88 harden-gf180-qfn128
.PHONY: harden-sky130-all harden-sky130-qfn32 harden-sky130-qfn64 harden-sky130-qfn88 harden-sky130-qfn128 harden-ics55-no-pll-all harden-ics55-pll-all harden-ics55-qfn32-no-pll harden-ics55-qfn64-no-pll harden-ics55-qfn88-no-pll harden-ics55-qfn128-no-pll harden-ics55-qfn32-pll harden-ics55-qfn64-pll harden-ics55-qfn88-pll harden-ics55-qfn128-pll harden-ics55-qfn32-no-pll-unsigned-io-pg

help:
	@echo "Usage: make <target> [PDK root override]"
	@echo ""
	@echo "  generate              Regenerate pin manifests and LibreLane configs"
	@echo "  check-generated       Verify generated files are current"
	@echo "  check                 Run PDK-independent repository checks"
	@echo "  format                Format tracked Makefiles, RTL, and testbench SystemVerilog"
	@echo "  format-check          Verify tracked Makefile and SystemVerilog formatting"
	@echo "  sim                   Run functional simulations for IHP, Sky130, and GF180"
	@echo "  sim-compile           Compile all three PDK adapter test configurations"
	@echo "  lint                  Compile IHP fixed package tops with the Ciel IO library"
	@echo "  lint-sky130           Compile Sky130 fixed package tops with the Ciel IO library"
	@echo "  lint-gf180            Compile GF180 fixed package tops with the Ciel IO library"
	@echo "  test                  Run IHP IO behavioral-library tests"
	@echo "  harden-qfn32          Run the IHP LibreLane flow for one package profile"
	@echo "  harden-qfn64"
	@echo "  harden-qfn88"
	@echo "  harden-qfn128"
	@echo "  harden-all            Run all IHP hardening targets sequentially"
	@echo "  harden-sky130-qfn32  Run Sky130 LibreLane for one package profile"
	@echo "  harden-sky130-qfn64"
	@echo "  harden-sky130-qfn88"
	@echo "  harden-sky130-qfn128"
	@echo "  harden-sky130-all    Run all Sky130 hardening targets sequentially"
	@echo "  harden-gf180-qfn32    Run GF180 OCD LibreLane for one package profile"
	@echo "  harden-gf180-qfn64"
	@echo "  harden-gf180-qfn88"
	@echo "  harden-gf180-qfn128"
	@echo "  harden-gf180-all      Run all GF180 hardening targets sequentially"
	@echo "  lint-ics55-no-pll     Compile ICS55 QFN32 SP55/PB4 and QFN64+ P65 no-PLL package tops"
	@echo "  lint-ics55-pll        Compile ICS55 legacy-PB4 PLL package tops"
	@echo "  lint-smoke            Compile all standalone ICS55 smoke RTL examples"
	@echo "  smoke-run             Run all non-signoff ICS55 smoke LibreLane flows"
	@echo "  smoke-report          Regenerate smoke/REPORTS.md from final metrics"
	@echo "  check-pdk-ics55       Verify the external ICS55 manual PDK and OpenROAD views"
	@echo "  harden-ics55-qfn32-no-pll / harden-ics55-qfn32-pll"
	@echo "  harden-ics55-qfn32-no-pll-unsigned-io-pg  Explicit non-signoff QFN32 SP55 IO-PG continuation"
	@echo "  harden-ics55-no-pll-all / harden-ics55-pll-all"
	@echo "  SKIP_DRC=1            Skip Magic and KLayout DRC only (off by default)"
	@echo "  SKIP_MAGIC_SPICE=1    Skip ICS55 Magic Spice extraction only (non-signoff; off by default)"
	@echo "  SKIP_NETGEN_LVS=1     Skip ICS55 Netgen LVS only (non-signoff; off by default)"
	@echo "  UNSIGNED_IO_PG=1      Required by the explicit QFN32 SP55 IO-PG continuation target"
	@echo "  SKY130_DRT_OPT_ITERS=64  Sky130 detailed-routing iterations (set 0 for a fast reference run)"

generate:
	$(PYTHON) tools/generate_tier0.py

check-generated:
	$(PYTHON) tools/generate_tier0.py --check

check: check-generated lint-smoke

lint-smoke:
	$(PYTHON) $(SMOKE_DIR)/check_rtl.py

smoke-run:
	$(PYTHON) $(SMOKE_DIR)/run_all.py

smoke-report:
	$(PYTHON) $(SMOKE_DIR)/report_metrics.py --write

format: mk-format rtl-format

format-check: mk-format-check rtl-format-check

mk-format:
	$(PYTHON) scripts/check_format.py --root . --kind make --apply --mbake $(MBAKE)

mk-format-check:
	$(PYTHON) scripts/check_format.py --root . --kind make --mbake $(MBAKE)

rtl-format:
	$(PYTHON) scripts/check_format.py --root . --kind rtl --apply --verible-verilog-format $(VERIBLE_FORMAT)

rtl-format-check:
	$(PYTHON) scripts/check_format.py --root . --kind rtl --verible-verilog-format $(VERIBLE_FORMAT)

sim-compile: sim-compile-ihp sim-compile-sky130 sim-compile-gf180 sim-compile-ics55

sim-compile-ihp:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_tb $(CI_IHP_IO_MODEL) $(IHP_RTL) tb/tenon_tier0_tb.sv

sim-compile-sky130:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_tb -DTENON_TIER0_DUT=tenon_tier0_padframe_sky130 $(CI_SKY130_IO_MODEL) $(SKY130_RTL) tb/tenon_tier0_tb.sv

sim-compile-gf180:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_tb -DTENON_TIER0_DUT=tenon_tier0_padframe_gf180 $(CI_GF180_IO_MODEL) $(GF180_RTL) tb/tenon_tier0_tb.sv

sim-compile-ics55:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_tb -DTENON_TIER0_DUT=tenon_tier0_padframe_ics55_no_pll $(CI_ICS55_IO_MODEL) $(ICS55_RTL) tb/tenon_tier0_tb.sv
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_ics55_pll_tb $(CI_ICS55_IO_MODEL) $(ICS55_RTL) tb/tenon_tier0_ics55_pll_tb.sv

sim: sim-ihp sim-sky130 sim-gf180 sim-ics55

sim-ihp:
	@mkdir -p $(BUILD_DIR)/sim
	$(IVERILOG) -g2012 -s tenon_tier0_tb -o $(BUILD_DIR)/sim/ihp.vvp $(CI_IHP_IO_MODEL) $(IHP_RTL) tb/tenon_tier0_tb.sv
	$(VVP) $(BUILD_DIR)/sim/ihp.vvp

sim-sky130:
	@mkdir -p $(BUILD_DIR)/sim
	$(IVERILOG) -g2012 -s tenon_tier0_tb -DTENON_TIER0_DUT=tenon_tier0_padframe_sky130 -o $(BUILD_DIR)/sim/sky130.vvp $(CI_SKY130_IO_MODEL) $(SKY130_RTL) tb/tenon_tier0_tb.sv
	$(VVP) $(BUILD_DIR)/sim/sky130.vvp

sim-gf180:
	@mkdir -p $(BUILD_DIR)/sim
	$(IVERILOG) -g2012 -s tenon_tier0_tb -DTENON_TIER0_DUT=tenon_tier0_padframe_gf180 -o $(BUILD_DIR)/sim/gf180.vvp $(CI_GF180_IO_MODEL) $(GF180_RTL) tb/tenon_tier0_tb.sv
	$(VVP) $(BUILD_DIR)/sim/gf180.vvp

sim-ics55:
	@mkdir -p $(BUILD_DIR)/sim
	$(IVERILOG) -g2012 -s tenon_tier0_tb -DTENON_TIER0_DUT=tenon_tier0_padframe_ics55_no_pll -o $(BUILD_DIR)/sim/ics55-no-pll.vvp $(CI_ICS55_IO_MODEL) $(ICS55_RTL) tb/tenon_tier0_tb.sv
	$(VVP) $(BUILD_DIR)/sim/ics55-no-pll.vvp
	$(IVERILOG) -g2012 -s tenon_tier0_ics55_pll_tb -o $(BUILD_DIR)/sim/ics55-pll.vvp $(CI_ICS55_IO_MODEL) $(ICS55_RTL) tb/tenon_tier0_ics55_pll_tb.sv
	$(VVP) $(BUILD_DIR)/sim/ics55-pll.vvp

check-pdk: check-pdk-ihp

check-pdk-ihp:
	@test -n "$(PDK_ROOT)" || (echo "PDK_ROOT must point to an existing IHP Open PDK root" && exit 2)
	@test -d "$(PDK_ROOT)/$(PDK)" || (echo "Missing $(PDK_ROOT)/$(PDK)" && exit 2)
	@test -f "$(IHP_IO_MODEL)" || (echo "Missing $(IHP_IO_MODEL)" && exit 2)

check-pdk-sky130:
	@test -n "$(SKY130_PDK_ROOT)" || (echo "SKY130_PDK_ROOT must point to an installed Sky130 PDK root" && exit 2)
	@test -d "$(SKY130_PDK_ROOT)/$(SKY130_PDK)" || (echo "Missing $(SKY130_PDK_ROOT)/$(SKY130_PDK)" && exit 2)
	@test -f "$(SKY130_IO_MODEL)" || (echo "Missing $(SKY130_IO_MODEL); set SKY130_IO_MODEL if your PDK packages the IO model elsewhere" && exit 2)
	@test -f "$(SKY130_EF_IO_MODEL)" || (echo "Missing $(SKY130_EF_IO_MODEL); set SKY130_EF_IO_MODEL if your PDK packages the EF-IO model elsewhere" && exit 2)

check-pdk-gf180:
	@test -n "$(GF180_PDK_ROOT)" || (echo "GF180_PDK_ROOT must point to an installed GF180 PDK root" && exit 2)
	@test -d "$(GF180_PDK_ROOT)/$(GF180_PDK)" || (echo "Missing $(GF180_PDK_ROOT)/$(GF180_PDK)" && exit 2)
	@test -f "$(GF180_IO_MODEL)" || (echo "Missing $(GF180_IO_MODEL); set GF180_IO_MODEL if your PDK packages the IO model elsewhere" && exit 2)

check-pdk-ics55:
	@test -d "$(ICS55_PDK_ROOT)/$(ICS55_PDK)" || (echo "Missing $(ICS55_PDK_ROOT)/$(ICS55_PDK)" && exit 2)
	@test -f "$(ICS55_PDK_ROOT)/$(ICS55_PDK)/libs.tech/librelane/config.tcl" || (echo "Missing ICS55 LibreLane adapter" && exit 2)
	@test -f "$(ICS55_PDK_ROOT)/$(ICS55_PDK)/libs.ref/ics55_LLSC_H7CR/lef/ics55_LLSC_H7CR_M2.lef" || (echo "Missing ICS55 H7CR LEF" && exit 2)
	@test -f "$(ICS55_PDK_ROOT)/$(ICS55_PDK)/libs.ref/ics55_io_3p3/lef/SP55NLLD2P_3P3V_V0p4a_6MT_1TM.lef" || (echo "Missing ICS55 SP55 LEF" && exit 2)
	@test -f "$(ICS55_PDK_ROOT)/$(ICS55_PDK)/libs.ref/ics55_io_p65/lef/ICSIOA_N55_3P3_1P6M1TM.lef" || (echo "Missing ICS55 P65 LEF" && exit 2)
	@test -f "$(ICS55_PDK_ROOT)/$(ICS55_PDK)/libs.ref/ics55_pll/lef/PLL_TOP.lef" || (echo "Missing ICS55 PLL LEF" && exit 2)
	ICS55_PDK_ROOT="$(ICS55_PDK_ROOT)" $(OPENROAD) -no_init -exit scripts/check_ics55_pdk.tcl

lint: lint-ihp

lint-ihp: check-pdk-ihp lint-qfn32 lint-qfn64 lint-qfn88 lint-qfn128

lint-qfn32:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_qfn32 $(IHP_IO_MODEL) $(IHP_RTL)

lint-qfn64:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_qfn64 $(IHP_IO_MODEL) $(IHP_RTL)

lint-qfn88:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_qfn88 $(IHP_IO_MODEL) $(IHP_RTL)

lint-qfn128:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_qfn128 $(IHP_IO_MODEL) $(IHP_RTL)

lint-sky130: check-pdk-sky130
	$(IVERILOG) -g2012 -DUSE_POWER_PINS -tnull -s tenon_tier0_sky130_qfn32 $(SKY130_IO_MODEL) $(SKY130_EF_IO_MODEL) $(SKY130_RTL)
	$(IVERILOG) -g2012 -DUSE_POWER_PINS -tnull -s tenon_tier0_sky130_qfn64 $(SKY130_IO_MODEL) $(SKY130_EF_IO_MODEL) $(SKY130_RTL)
	$(IVERILOG) -g2012 -DUSE_POWER_PINS -tnull -s tenon_tier0_sky130_qfn88 $(SKY130_IO_MODEL) $(SKY130_EF_IO_MODEL) $(SKY130_RTL)
	$(IVERILOG) -g2012 -DUSE_POWER_PINS -tnull -s tenon_tier0_sky130_qfn128 $(SKY130_IO_MODEL) $(SKY130_EF_IO_MODEL) $(SKY130_RTL)

lint-gf180: check-pdk-gf180
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_gf180_qfn32 $(GF180_IO_MODEL) $(GF180_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_gf180_qfn64 $(GF180_IO_MODEL) $(GF180_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_gf180_qfn88 $(GF180_IO_MODEL) $(GF180_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_gf180_qfn128 $(GF180_IO_MODEL) $(GF180_RTL)

test: check-pdk-ihp
lint-ics55: lint-ics55-no-pll lint-ics55-pll

lint-ics55-no-pll: check-pdk-ics55
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_ics55_qfn32_no_pll flow/ics55/ics55_io_blackbox.v $(ICS55_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_ics55_qfn64_no_pll flow/ics55/ics55_io_blackbox.v $(ICS55_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_ics55_qfn88_no_pll flow/ics55/ics55_io_blackbox.v $(ICS55_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_ics55_qfn128_no_pll flow/ics55/ics55_io_blackbox.v $(ICS55_RTL)

lint-ics55-pll: check-pdk-ics55
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_ics55_qfn32_pll flow/ics55/ics55_io_blackbox.v $(ICS55_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_ics55_qfn64_pll flow/ics55/ics55_io_blackbox.v $(ICS55_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_ics55_qfn88_pll flow/ics55/ics55_io_blackbox.v $(ICS55_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_ics55_qfn128_pll flow/ics55/ics55_io_blackbox.v $(ICS55_RTL)

	@mkdir -p $(BUILD_DIR)/tests
	$(IVERILOG) -g2012 -s tenon_tier0_tb -o $(BUILD_DIR)/tests/tenon_tier0_tb.vvp $(IHP_IO_MODEL) $(IHP_RTL) tb/tenon_tier0_tb.sv
	$(VVP) $(BUILD_DIR)/tests/tenon_tier0_tb.vvp

harden-qfn32: generate check-pdk-ihp
	$(LIBRELANE) --manual-pdk --pdk $(PDK) --pdk-root $(PDK_ROOT) $(IHP_DRC_OVERRIDES) --run-tag tenon-qfn32 --overwrite $(IHP_FLOW_DIR)/qfn32.yaml

harden-qfn64: generate check-pdk-ihp
	$(LIBRELANE) --manual-pdk --pdk $(PDK) --pdk-root $(PDK_ROOT) $(IHP_DRC_OVERRIDES) --run-tag tenon-qfn64 --overwrite $(IHP_FLOW_DIR)/qfn64.yaml

harden-qfn88: generate check-pdk-ihp
	$(LIBRELANE) --manual-pdk --pdk $(PDK) --pdk-root $(PDK_ROOT) $(IHP_DRC_OVERRIDES) --run-tag tenon-qfn88 --overwrite $(IHP_FLOW_DIR)/qfn88.yaml

harden-qfn128: generate check-pdk-ihp
	$(LIBRELANE) --manual-pdk --pdk $(PDK) --pdk-root $(PDK_ROOT) $(IHP_DRC_OVERRIDES) --run-tag tenon-qfn128 --overwrite $(IHP_FLOW_DIR)/qfn128.yaml

harden-all:
	$(MAKE) harden-qfn32 PDK_ROOT="$(PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-qfn64 PDK_ROOT="$(PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-qfn88 PDK_ROOT="$(PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-qfn128 PDK_ROOT="$(PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"

harden-sky130-qfn32: generate check-pdk-sky130
	$(LIBRELANE) --manual-pdk --pdk $(SKY130_PDK) --pdk-root $(SKY130_PDK_ROOT) --scl $(SKY130_SCL) $(SKY130_DRC_SKIPS) --override-config DRT_OPT_ITERS=$(SKY130_DRT_OPT_ITERS) --run-tag tenon-qfn32 --overwrite $(SKY130_FLOW_DIR)/qfn32.yaml

harden-sky130-qfn64: generate check-pdk-sky130
	$(LIBRELANE) --manual-pdk --pdk $(SKY130_PDK) --pdk-root $(SKY130_PDK_ROOT) --scl $(SKY130_SCL) $(SKY130_DRC_SKIPS) --override-config DRT_OPT_ITERS=$(SKY130_DRT_OPT_ITERS) --run-tag tenon-qfn64 --overwrite $(SKY130_FLOW_DIR)/qfn64.yaml

harden-sky130-qfn88: generate check-pdk-sky130
	$(LIBRELANE) --manual-pdk --pdk $(SKY130_PDK) --pdk-root $(SKY130_PDK_ROOT) --scl $(SKY130_SCL) $(SKY130_DRC_SKIPS) --override-config DRT_OPT_ITERS=$(SKY130_DRT_OPT_ITERS) --run-tag tenon-qfn88 --overwrite $(SKY130_FLOW_DIR)/qfn88.yaml

harden-sky130-qfn128: generate check-pdk-sky130
	$(LIBRELANE) --manual-pdk --pdk $(SKY130_PDK) --pdk-root $(SKY130_PDK_ROOT) --scl $(SKY130_SCL) $(SKY130_DRC_SKIPS) --override-config DRT_OPT_ITERS=$(SKY130_DRT_OPT_ITERS) --run-tag tenon-qfn128 --overwrite $(SKY130_FLOW_DIR)/qfn128.yaml

harden-sky130-all:
	$(MAKE) harden-sky130-qfn32 SKY130_PDK_ROOT="$(SKY130_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)" SKY130_DRT_OPT_ITERS="$(SKY130_DRT_OPT_ITERS)"
	$(MAKE) harden-sky130-qfn64 SKY130_PDK_ROOT="$(SKY130_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)" SKY130_DRT_OPT_ITERS="$(SKY130_DRT_OPT_ITERS)"
	$(MAKE) harden-sky130-qfn88 SKY130_PDK_ROOT="$(SKY130_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)" SKY130_DRT_OPT_ITERS="$(SKY130_DRT_OPT_ITERS)"
	$(MAKE) harden-sky130-qfn128 SKY130_PDK_ROOT="$(SKY130_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)" SKY130_DRT_OPT_ITERS="$(SKY130_DRT_OPT_ITERS)"

harden-gf180-qfn32: generate check-pdk-gf180
	$(LIBRELANE) --manual-pdk --pdk $(GF180_PDK) --pdk-root $(GF180_PDK_ROOT) --scl $(GF180_SCL) --pad $(GF180_PAD) $(GF180_DRC_SKIPS) --run-tag tenon-qfn32 --overwrite $(GF180_FLOW_DIR)/qfn32.yaml

harden-gf180-qfn64: generate check-pdk-gf180
	$(LIBRELANE) --manual-pdk --pdk $(GF180_PDK) --pdk-root $(GF180_PDK_ROOT) --scl $(GF180_SCL) --pad $(GF180_PAD) $(GF180_DRC_SKIPS) --run-tag tenon-qfn64 --overwrite $(GF180_FLOW_DIR)/qfn64.yaml

harden-gf180-qfn88: generate check-pdk-gf180
	$(LIBRELANE) --manual-pdk --pdk $(GF180_PDK) --pdk-root $(GF180_PDK_ROOT) --scl $(GF180_SCL) --pad $(GF180_PAD) $(GF180_DRC_SKIPS) --run-tag tenon-qfn88 --overwrite $(GF180_FLOW_DIR)/qfn88.yaml

harden-gf180-qfn128: generate check-pdk-gf180
	$(LIBRELANE) --manual-pdk --pdk $(GF180_PDK) --pdk-root $(GF180_PDK_ROOT) --scl $(GF180_SCL) --pad $(GF180_PAD) $(GF180_DRC_SKIPS) --run-tag tenon-qfn128 --overwrite $(GF180_FLOW_DIR)/qfn128.yaml

harden-gf180-all:
	$(MAKE) harden-gf180-qfn32 GF180_PDK_ROOT="$(GF180_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-gf180-qfn64 GF180_PDK_ROOT="$(GF180_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-gf180-qfn88 GF180_PDK_ROOT="$(GF180_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-gf180-qfn128 GF180_PDK_ROOT="$(GF180_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"

define ICS55_PAD_FOR_PROFILE
$(if $(and $(filter 32,$(1)),$(filter no-pll,$(2))),$(ICS55_LEGACY_PAD),$(if $(filter no-pll,$(2)),$(ICS55_P65_PAD),$(ICS55_LEGACY_PAD)))
endef

define ICS55_HARDEN_RULE
harden-ics55-qfn$(1)-$(2): generate check-pdk-ics55
	DRT_THREADS=$(ICS55_DRT_THREADS) $(LIBRELANE) --manual-pdk --pdk $(ICS55_PDK) --pdk-root $(ICS55_PDK_ROOT) --scl $(ICS55_SCL) --pad $(call ICS55_PAD_FOR_PROFILE,$(1),$(2)) $(ICS55_DRC_SKIPS) $(ICS55_MAGIC_SPICE_SKIP) $(ICS55_NETGEN_LVS_SKIP) --override-config DRT_OPT_ITERS=$(ICS55_DRT_OPT_ITERS) --run-tag tenon-qfn$(1)-$(2) --overwrite $(ICS55_FLOW_DIR)/qfn$(1)-$(2).yaml
endef

$(eval $(call ICS55_HARDEN_RULE,32,no-pll))
$(eval $(call ICS55_HARDEN_RULE,64,no-pll))
$(eval $(call ICS55_HARDEN_RULE,88,no-pll))
$(eval $(call ICS55_HARDEN_RULE,128,no-pll))
$(eval $(call ICS55_HARDEN_RULE,32,pll))
$(eval $(call ICS55_HARDEN_RULE,64,pll))
$(eval $(call ICS55_HARDEN_RULE,88,pll))
$(eval $(call ICS55_HARDEN_RULE,128,pll))

# The SP55 LEF has no package-level VDD25/VSSD or RDL supply geometry. Keep PSM
# in this explicitly named target, but retain its report as non-fatal only after
# an operator explicitly acknowledges that the output is not a signoff result.
harden-ics55-qfn32-no-pll-unsigned-io-pg: generate check-pdk-ics55
	@test "$(UNSIGNED_IO_PG)" = "1" || { echo "Set UNSIGNED_IO_PG=1 to run this non-signoff target."; exit 2; }
	@echo "WARNING: retaining non-fatal PSM violations; result is not signed off."
# OpenROAD.IRDropReport repeats PSM without a non-fatal control, so this target
# omits only that reanalysis after the PowerGrid checker has captured violations.
	DRT_THREADS=$(ICS55_DRT_THREADS) $(LIBRELANE) --manual-pdk --pdk $(ICS55_PDK) --pdk-root $(ICS55_PDK_ROOT) --scl $(ICS55_SCL) --pad $(ICS55_LEGACY_PAD) $(ICS55_DRC_SKIPS) $(ICS55_MAGIC_SPICE_SKIP) $(ICS55_NETGEN_LVS_SKIP) --override-config DRT_OPT_ITERS=$(ICS55_DRT_OPT_ITERS) --override-config ERROR_ON_PDN_VIOLATIONS=false --skip OpenROAD.IRDropReport --run-tag tenon-qfn32-no-pll-unsigned-io-pg --overwrite $(ICS55_FLOW_DIR)/qfn32-no-pll.yaml

harden-ics55-no-pll-all:
	$(MAKE) harden-ics55-qfn32-no-pll ICS55_PDK_ROOT="$(ICS55_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-ics55-qfn64-no-pll ICS55_PDK_ROOT="$(ICS55_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-ics55-qfn88-no-pll ICS55_PDK_ROOT="$(ICS55_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-ics55-qfn128-no-pll ICS55_PDK_ROOT="$(ICS55_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"

harden-ics55-pll-all:
	$(MAKE) harden-ics55-qfn32-pll ICS55_PDK_ROOT="$(ICS55_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-ics55-qfn64-pll ICS55_PDK_ROOT="$(ICS55_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-ics55-qfn88-pll ICS55_PDK_ROOT="$(ICS55_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-ics55-qfn128-pll ICS55_PDK_ROOT="$(ICS55_PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"