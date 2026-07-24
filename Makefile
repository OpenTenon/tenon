PYTHON         ?= python3
IVERILOG       ?= iverilog
VVP            ?= vvp
MBAKE          ?= mbake
VERIBLE_FORMAT ?= verible-verilog-format
LIBRELANE      ?= librelane
PDK            ?= ihp-sg13g2
PDK_ROOT       ?=
SKIP_DRC       ?= 0

IHP_RTL    := rtl/tenon_tier0_padframe.sv rtl/tenon_tier0_reference.sv rtl/tenon_tier0_variants.sv
SKY130_RTL := rtl/tenon_tier0_padframe_sky130.sv rtl/tenon_tier0_pdk_reference.sv rtl/tenon_tier0_pdk_variants.sv
GF180_RTL  := rtl/tenon_tier0_padframe_gf180.sv rtl/tenon_tier0_pdk_reference.sv rtl/tenon_tier0_pdk_variants.sv

IHP_IO_MODEL    ?= $(PDK_ROOT)/$(PDK)/libs.ref/sg13g2_io/verilog/sg13g2_io.v
SKY130_PDK      ?= sky130A
SKY130_IO_MODEL ?= $(PDK_ROOT)/$(SKY130_PDK)/libs.ref/sky130_fd_io/verilog/sky130_fd_io.v
GF180_PDK       ?= gf180mcuD
GF180_IO_MODEL  ?= $(PDK_ROOT)/$(GF180_PDK)/libs.ref/gf180mcu_ocd_io/verilog/gf180mcu_ocd_io.v

CI_IHP_IO_MODEL    := tb/ci_sg13g2_io_stub.sv
CI_SKY130_IO_MODEL := tb/ci_sky130_fd_io_stub.sv
CI_GF180_IO_MODEL  := tb/ci_gf180mcu_ocd_io_stub.sv
BUILD_DIR          := build

.DEFAULT_GOAL := help

ifneq ($(filter 1 true TRUE yes YES,$(SKIP_DRC)),)
DRC_OVERRIDES := --override-config RUN_MAGIC_DRC=false --override-config RUN_KLAYOUT_DRC=false
endif

.PHONY: help generate check-generated check format format-check mk-format mk-format-check rtl-format rtl-format-check sim sim-compile sim-compile-ihp sim-compile-sky130 sim-compile-gf180 sim-ihp sim-sky130 sim-gf180 check-pdk check-pdk-ihp check-pdk-sky130 check-pdk-gf180 lint lint-ihp lint-sky130 lint-gf180 lint-qfn32 lint-qfn64 lint-qfn88 lint-qfn128 test harden-all harden-qfn32 harden-qfn64 harden-qfn88 harden-qfn128

help:
	@echo "Usage: make <target> PDK_ROOT=/path/to/installed-pdk"
	@echo ""
	@echo "  generate            Regenerate IHP pin manifests and LibreLane configs"
	@echo "  check-generated     Verify generated files are current"
	@echo "  check               Run PDK-independent repository checks"
	@echo "  format              Format tracked Makefiles, RTL, and testbench SystemVerilog"
	@echo "  format-check        Verify tracked Makefile and SystemVerilog formatting"
	@echo "  sim                 Run functional simulations for IHP, Sky130, and GF180"
	@echo "  sim-compile         Compile all three PDK adapter test configurations"
	@echo "  lint                Compile IHP fixed package tops with an installed IHP IO library"
	@echo "  lint-sky130         Compile Sky130 fixed package tops with SKY130_IO_MODEL"
	@echo "  lint-gf180          Compile GF180 fixed package tops with GF180_IO_MODEL"
	@echo "  test                Run IHP IO behavioral-library tests"
	@echo "  harden-qfn32        Run the IHP LibreLane flow for one package profile"
	@echo "  harden-qfn64"
	@echo "  harden-qfn88"
	@echo "  harden-qfn128"
	@echo "  harden-all          Run all IHP hardening targets sequentially"
	@echo "  SKIP_DRC=1          Skip Magic and KLayout DRC only (off by default)"

generate:
	$(PYTHON) tools/generate_tier0.py

check-generated:
	$(PYTHON) tools/generate_tier0.py --check

check: check-generated

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

sim-compile: sim-compile-ihp sim-compile-sky130 sim-compile-gf180

sim-compile-ihp:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_tb $(CI_IHP_IO_MODEL) $(IHP_RTL) tb/tenon_tier0_tb.sv

sim-compile-sky130:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_tb -DTENON_TIER0_DUT=tenon_tier0_padframe_sky130 $(CI_SKY130_IO_MODEL) $(SKY130_RTL) tb/tenon_tier0_tb.sv

sim-compile-gf180:
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_tb -DTENON_TIER0_DUT=tenon_tier0_padframe_gf180 $(CI_GF180_IO_MODEL) $(GF180_RTL) tb/tenon_tier0_tb.sv

sim: sim-ihp sim-sky130 sim-gf180

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

check-pdk: check-pdk-ihp

check-pdk-ihp:
	@test -n "$(PDK_ROOT)" || (echo "PDK_ROOT must point to an existing IHP Open PDK root" && exit 2)
	@test -d "$(PDK_ROOT)/$(PDK)" || (echo "Missing $(PDK_ROOT)/$(PDK)" && exit 2)
	@test -f "$(IHP_IO_MODEL)" || (echo "Missing $(IHP_IO_MODEL)" && exit 2)

check-pdk-sky130:
	@test -n "$(PDK_ROOT)" || (echo "PDK_ROOT must point to an installed Sky130 PDK root" && exit 2)
	@test -d "$(PDK_ROOT)/$(SKY130_PDK)" || (echo "Missing $(PDK_ROOT)/$(SKY130_PDK)" && exit 2)
	@test -f "$(SKY130_IO_MODEL)" || (echo "Missing $(SKY130_IO_MODEL); set SKY130_IO_MODEL if your PDK packages the IO model elsewhere" && exit 2)

check-pdk-gf180:
	@test -n "$(PDK_ROOT)" || (echo "PDK_ROOT must point to an installed GF180 PDK root" && exit 2)
	@test -d "$(PDK_ROOT)/$(GF180_PDK)" || (echo "Missing $(PDK_ROOT)/$(GF180_PDK)" && exit 2)
	@test -f "$(GF180_IO_MODEL)" || (echo "Missing $(GF180_IO_MODEL); set GF180_IO_MODEL if your PDK packages the IO model elsewhere" && exit 2)

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
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_sky130_qfn32 $(SKY130_IO_MODEL) $(SKY130_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_sky130_qfn64 $(SKY130_IO_MODEL) $(SKY130_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_sky130_qfn88 $(SKY130_IO_MODEL) $(SKY130_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_sky130_qfn128 $(SKY130_IO_MODEL) $(SKY130_RTL)

lint-gf180: check-pdk-gf180
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_gf180_qfn32 $(GF180_IO_MODEL) $(GF180_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_gf180_qfn64 $(GF180_IO_MODEL) $(GF180_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_gf180_qfn88 $(GF180_IO_MODEL) $(GF180_RTL)
	$(IVERILOG) -g2012 -tnull -s tenon_tier0_gf180_qfn128 $(GF180_IO_MODEL) $(GF180_RTL)

test: check-pdk-ihp
	@mkdir -p $(BUILD_DIR)/tests
	$(IVERILOG) -g2012 -s tenon_tier0_tb -o $(BUILD_DIR)/tests/tenon_tier0_tb.vvp $(IHP_IO_MODEL) $(IHP_RTL) tb/tenon_tier0_tb.sv
	$(VVP) $(BUILD_DIR)/tests/tenon_tier0_tb.vvp

harden-qfn32: generate check-pdk-ihp
	$(LIBRELANE) --manual-pdk --pdk $(PDK) --pdk-root $(PDK_ROOT) $(DRC_OVERRIDES) --run-tag tenon-qfn32 --overwrite --save-views-to $(BUILD_DIR)/qfn32/final flow/qfn32.yaml

harden-qfn64: generate check-pdk-ihp
	$(LIBRELANE) --manual-pdk --pdk $(PDK) --pdk-root $(PDK_ROOT) $(DRC_OVERRIDES) --run-tag tenon-qfn64 --overwrite --save-views-to $(BUILD_DIR)/qfn64/final flow/qfn64.yaml

harden-qfn88: generate check-pdk-ihp
	$(LIBRELANE) --manual-pdk --pdk $(PDK) --pdk-root $(PDK_ROOT) $(DRC_OVERRIDES) --run-tag tenon-qfn88 --overwrite --save-views-to $(BUILD_DIR)/qfn88/final flow/qfn88.yaml

harden-qfn128: generate check-pdk-ihp
	$(LIBRELANE) --manual-pdk --pdk $(PDK) --pdk-root $(PDK_ROOT) $(DRC_OVERRIDES) --run-tag tenon-qfn128 --overwrite --save-views-to $(BUILD_DIR)/qfn128/final flow/qfn128.yaml

harden-all:
	$(MAKE) harden-qfn32 PDK_ROOT="$(PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-qfn64 PDK_ROOT="$(PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-qfn88 PDK_ROOT="$(PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"
	$(MAKE) harden-qfn128 PDK_ROOT="$(PDK_ROOT)" SKIP_DRC="$(SKIP_DRC)"