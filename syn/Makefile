#==============================================================================
# syn/Makefile -- drive Design Compiler without remembering any of its flags.
#
#   make router            synthesise one block with the defaults
#   make all               every block, in the order worth reading them
#   make router PDK=asap7 PERIOD=0.6
#   make sweep BLOCK=router          find the period where it stops meeting
#   make clean
#
# Variables:
#   PDK     saed32 | asap7 | sky130        default saed32
#   PERIOD  clock period, library units    default: per PDK (see setup/pdk.tcl)
#   SRAM    blackbox | flops               default blackbox
#   EFFORT  ultra | high | flatten         default ultra (needs DC Ultra)
#   DC      path to dc_shell               default dc_shell
#
# The RTL file list is not kept here. It is pulled from sim/Makefile every run,
# so what is synthesised is by construction what is simulated and linted.
#==============================================================================

SHELL   := /bin/bash
SYN_DIR := $(abspath $(CURDIR))
ROOT    := $(abspath $(CURDIR)/..)

DC        ?= dc_shell
PYTHON    ?= python3
VERILATOR ?= $(shell test -x /opt/verilator/bin/verilator \
               && echo /opt/verilator/bin/verilator || echo verilator)
PDK     ?= saed32
SRAM    ?= blackbox
EFFORT  ?= ultra

BLOCKS  := router tile_nic l1_cache dir_ctrl tile_top system_top

.PHONY: all filelist dryrun clean help $(BLOCKS)

help:
	@echo "make <block>        one of: $(BLOCKS)"
	@echo "make all            every block, smallest first"
	@echo "make filelist       refresh filelist.f from sim/Makefile"
	@echo "make dryrun         run the flow with dc_shell stubbed -- no tools needed"
	@echo ""
	@echo "PDK=saed32|asap7|sky130   PERIOD=<ns>   SRAM=blackbox|flops"
	@echo "EFFORT=ultra|high|flatten DC=<path to dc_shell>"
	@echo "VT=lvt|rvt|hvt  CORNER=tt|ss  WLM=<model>  DB=<explicit .db>"
	@echo ""
	@echo "Set SAED32_ROOT / ASAP7_ROOT / SKY130_ROOT for the PDK you pick."
	@echo "See syn/README.md -- it lists exactly which reports to send back."

filelist:
	@$(MAKE) --no-print-directory -C $(ROOT)/sim print-rtl-srcs > $(SYN_DIR)/filelist.f
	@echo "filelist.f: $$(wc -l < $(SYN_DIR)/filelist.f) files, from sim/Makefile"

# Elaborating with SYNTHESIS defined catches a front-end problem in two seconds
# instead of two minutes of dc_shell start-up, so it runs first, every time.
$(BLOCKS): filelist
	@$(MAKE) --no-print-directory -C $(ROOT)/sim lint-synth
	@mkdir -p $(SYN_DIR)/out/$@.$(PDK)
	cd $(SYN_DIR) && \
	  SYN_TOP=$@ SYN_PDK=$(PDK) SYN_SRAM=$(SRAM) SYN_EFFORT=$(EFFORT) \
	  $(if $(VT),SYN_VT=$(VT),) $(if $(CORNER),SYN_CORNER=$(CORNER),) \
	  $(if $(WLM),SYN_WLM=$(WLM),) $(if $(DB),SYN_DB=$(DB),) \
	  $(if $(PERIOD),SYN_PERIOD=$(PERIOD),) \
	  SYN_FILELIST=$(SYN_DIR)/filelist.f \
	  SYN_OUT=$(SYN_DIR)/out/$@.$(PDK) \
	  $(DC) -f $(SYN_DIR)/scripts/synth.tcl \
	    2>&1 | tee $(SYN_DIR)/out/$@.$(PDK)/synth.log
	@echo ""
	@grep -E "slack|Critical Path" $(SYN_DIR)/out/$@.$(PDK)/qor.rpt | head -8 || true

# Run the whole flow with Design Compiler stubbed out: every variable, every
# branch, every SDC, and every get_ports pattern checked against the block's
# real port list. It does not need a licence, a PDK or a tool, so it runs on
# the machine the RTL is developed on -- which is the only place the scripts
# can otherwise not be checked at all. See the header of scripts/dryrun.tcl for
# what it does and does not prove.
dryrun: filelist
	@rm -rf $(SYN_DIR)/.dryrun; mkdir -p $(SYN_DIR)/.dryrun
	@fail=0; for b in $(BLOCKS); do \
	  printf '  %-12s ' "$$b"; \
	  $(PYTHON) $(SYN_DIR)/scripts/ports.py $$b $(SYN_DIR)/filelist.f $(VERILATOR) \
	    > $(SYN_DIR)/.dryrun/$$b.ports 2>$(SYN_DIR)/.dryrun/$$b.porterr || \
	    { echo "FAIL (port extraction)"; cat $(SYN_DIR)/.dryrun/$$b.porterr; fail=1; continue; }; \
	  ( cd $(SYN_DIR)/.dryrun && \
	    SYN_TOP=$$b SYN_SRAM=$(SRAM) SYN_FILELIST=$(SYN_DIR)/filelist.f \
	    SYN_OUT=$(SYN_DIR)/.dryrun/$$b DRY_PORTS=$(SYN_DIR)/.dryrun/$$b.ports \
	    tclsh $(SYN_DIR)/scripts/dryrun.tcl > $(SYN_DIR)/.dryrun/$$b.log 2>&1 ) \
	    && echo "clean" \
	    || { echo "FAIL"; sed -n '/---- dry run ----/,$$p' $(SYN_DIR)/.dryrun/$$b.log; fail=1; }; \
	done; exit $$fail

all: router tile_nic l1_cache dir_ctrl tile_top system_top

clean:
	rm -rf $(SYN_DIR)/out $(SYN_DIR)/.dryrun $(SYN_DIR)/.dryrun_pdk $(SYN_DIR)/filelist.f $(SYN_DIR)/*.log \
	       $(SYN_DIR)/command.log $(SYN_DIR)/default.svf
