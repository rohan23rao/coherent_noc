#==============================================================================
# syn/yosys/Makefile -- the synthesis flow that actually runs.
#
#   make pdk-asap7            fetch and merge the ASAP7 Liberty
#   make pdk-sky130           fetch the sky130_fd_sc_hd Liberty
#   make asap7                sweep every block on ASAP7, typical corner
#   make asap7 CORNER=ss      the pessimistic corner
#   make sky130               the same on sky130
#   make report               collect every summary.json into a markdown table
#   make clean
#
# Tools: yosys, sv2v, OpenSTA (`sta`), 7z and python3. `make tools` prints
# which are missing and where to get them.
#
# This flow is deliberately separate from syn/scripts/synth.tcl. That one
# targets Design Compiler and a foundry kit and has never been executed; this
# one uses only free tools and free PDKs, so the numbers in docs/synthesis.md
# came out of it rather than out of an estimate.
#==============================================================================

SHELL   := /bin/bash
HERE    := $(abspath $(CURDIR))
ROOT    := $(abspath $(CURDIR)/../..)
PYTHON  ?= python3
PDK_ROOT ?= /opt/pdk
CORNER  ?= tt
SRAM    ?= blackbox

# Smallest first: the small blocks shake out the flow, and a failure on
# router after twenty minutes of l1_cache is a bad way to find a typo.
BLOCKS  ?= tile_nic dir_ctrl l1_cache router

.PHONY: tools pdk-asap7 pdk-sky130 asap7 sky130 report clean $(BLOCKS)

tools:
	@for t in yosys sv2v sta 7z $(PYTHON); do \
	  printf '  %-8s ' "$$t"; \
	  if command -v $$t >/dev/null; then command -v $$t; else \
	    echo "MISSING"; fi; \
	done
	@echo ""
	@echo "  yosys   apt install yosys, or oss-cad-suite"
	@echo "  sv2v    github.com/zachjs/sv2v/releases (a single binary)"
	@echo "  sta     github.com/parallaxsw/OpenSTA -- needs CUDD; without it"
	@echo "          the flow still reports area and cells, but no timing"

pdk-asap7:
	@$(HERE)/fetch_pdk.sh asap7 $(PDK_ROOT)

pdk-sky130:
	@$(HERE)/fetch_pdk.sh sky130 $(PDK_ROOT)

define sweep_one
	@$(PYTHON) $(HERE)/oss_synth.py --top $(1) --pdk $(2) --corner $(CORNER) \
	   --sram $(SRAM) --sweep

endef

asap7:
	$(foreach b,$(BLOCKS),$(call sweep_one,$(b),asap7))

sky130:
	$(foreach b,$(BLOCKS),$(call sweep_one,$(b),sky130))

report:
	@$(PYTHON) $(HERE)/collect.py $(HERE)/out

clean:
	rm -rf $(HERE)/out $(HERE)/.work
