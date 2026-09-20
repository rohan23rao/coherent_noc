#==============================================================================
# sim/Makefile -- the real build. The repo-root Makefile forwards here.
#
# Targets: lint  test  test TEST=<name>  waves TEST=<name>  mutate  all  clean
#==============================================================================

SHELL      := /bin/bash
ROOT       := $(abspath $(CURDIR)/..)
RTL_DIR    := $(ROOT)/rtl
TB_DIR     := $(ROOT)/tb
SCRIPTS    := $(ROOT)/scripts
WAVE_DIR   := $(CURDIR)/waves

# Prefer the locally built Verilator; cocotb 2.x requires >= 5.036 and the
# distro package is 5.020.
VERILATOR  ?= $(shell test -x /opt/verilator/bin/verilator \
                && echo /opt/verilator/bin/verilator || echo verilator)
PYTEST     ?= python3 -m pytest

TOP        ?= system_top

# Package first, then leaves, then the top.
RTL_SRCS := \
	$(RTL_DIR)/pkg/coh_pkg.sv \
	$(RTL_DIR)/lib/reset_sync.sv \
	$(RTL_DIR)/lib/rr_arbiter.sv \
	$(RTL_DIR)/lib/credit_counter.sv \
	$(RTL_DIR)/lib/fifo.sv \
	$(RTL_DIR)/lib/skid_buffer.sv \
	$(RTL_DIR)/lib/sram_1rw.sv \
	$(RTL_DIR)/noc/route_compute.sv \
	$(RTL_DIR)/noc/input_unit.sv \
	$(RTL_DIR)/noc/vc_allocator.sv \
	$(RTL_DIR)/noc/switch_allocator.sv \
	$(RTL_DIR)/noc/crossbar.sv \
	$(RTL_DIR)/noc/router.sv \
	$(RTL_DIR)/noc/noc_top.sv \
	$(RTL_DIR)/mem/mem_model.sv \
	$(RTL_DIR)/l1/l1_cache.sv \
	$(RTL_DIR)/top/system_top.sv

VERILATOR_LINT_FLAGS_BASE := \
	--lint-only \
	-Wall \
	--timing

VERILATOR_LINT_FLAGS := $(VERILATOR_LINT_FLAGS_BASE) --top-module $(TOP)

WAIVERS := $(ROOT)/lint/waivers.vlt

.PHONY: all lint lint-rtl lint-style lint-libs lint-slang test waves mutate clean help

all: lint test

#------------------------------------------------------------------------------
# Lint: Verilator -Wall with zero warnings, plus the structural checks Verilator
# does not perform. Both must pass.
#------------------------------------------------------------------------------
lint: lint-style lint-rtl lint-libs lint-slang

lint-rtl:
	@echo "== verilator --lint-only -Wall =="
	$(VERILATOR) $(VERILATOR_LINT_FLAGS) $(WAIVERS) $(RTL_SRCS)
	@echo "lint: clean"

lint-style:
	@echo "== structural style checks =="
	@$(SCRIPTS)/check_style.sh $(RTL_DIR)

# Elaborating only from the system top leaves modules that nothing instantiates
# yet partially unchecked. Lint each library module as its own top as well, so a
# module is fully linted the day it is written rather than the day it is wired
# in.
LIB_MODULES := reset_sync rr_arbiter credit_counter fifo skid_buffer sram_1rw
NOC_MODULES := route_compute crossbar input_unit vc_allocator switch_allocator router noc_top mem_model l1_cache

# A second, independent SystemVerilog front end. slang's elaboration is
# stricter than Verilator's in different places, so a warning from either is
# worth acting on. Skipped with a notice if slang is not installed, so the gate
# still runs on a machine that only has Verilator.
SLANG ?= $(shell test -x /opt/slang/bin/slang && echo /opt/slang/bin/slang || command -v slang)

lint-slang:
	@if [ -n "$(SLANG)" ]; then \
	  echo "== slang --lint-only =="; \
	  $(SLANG) --lint-only $(RTL_SRCS) || exit 1; \
	else \
	  echo "== slang: not installed, skipping second-opinion lint =="; \
	fi

lint-libs:
	@echo "== verilator -Wall, each library module as its own top =="
	@for m in $(LIB_MODULES); do \
	  printf '  %-16s ' "$$m"; \
	  $(VERILATOR) $(VERILATOR_LINT_FLAGS_BASE) --top-module $$m \
	    $(WAIVERS) $(RTL_DIR)/pkg/coh_pkg.sv $(RTL_DIR)/lib/$$m.sv \
	    > /dev/null 2>$(CURDIR)/.lint_$$m.err || true; \
	  if [ -s $(CURDIR)/.lint_$$m.err ]; then \
	    echo "FAIL"; cat $(CURDIR)/.lint_$$m.err; rm -f $(CURDIR)/.lint_*.err; exit 1; \
	  else \
	    echo "clean"; rm -f $(CURDIR)/.lint_$$m.err; \
	  fi; \
	done
	@for m in $(NOC_MODULES); do \
	  printf '  %-18s ' "$$m"; \
	  $(VERILATOR) $(VERILATOR_LINT_FLAGS_BASE) --top-module $$m \
	    $(WAIVERS) $(RTL_SRCS) \
	    > /dev/null 2>$(CURDIR)/.lint_$$m.err || true; \
	  if [ -s $(CURDIR)/.lint_$$m.err ]; then \
	    echo "FAIL"; cat $(CURDIR)/.lint_$$m.err; rm -f $(CURDIR)/.lint_*.err; exit 1; \
	  else \
	    echo "clean"; rm -f $(CURDIR)/.lint_$$m.err; \
	  fi; \
	done

#------------------------------------------------------------------------------
# Test. `make test` runs everything; `make test TEST=<name>` selects by pytest
# node-id substring.
#
# pytest exits 5 when it collects zero tests. At early phases that is the
# correct, passing outcome, so it is translated to 0 explicitly -- never with
# `|| true`, which would swallow real failures too.
#------------------------------------------------------------------------------
test:
	@echo "== pytest =="
	@cd $(TB_DIR) && $(PYTEST) tests -v $(if $(TEST),-k "$(TEST)",); \
	rc=$$?; \
	if [ $$rc -eq 5 ]; then \
	  echo "pytest: no tests collected (expected at this phase) -- treating as pass"; \
	  exit 0; \
	fi; \
	exit $$rc

#------------------------------------------------------------------------------
# Waves: same as test, but force trace generation and keep the VCD.
#------------------------------------------------------------------------------
waves:
ifndef TEST
	$(error `make waves` requires TEST=<name>)
endif
	@mkdir -p $(WAVE_DIR)
	@cd $(TB_DIR) && COCOTB_TRACE=1 WAVE_DIR=$(WAVE_DIR) $(PYTEST) tests -v -k "$(TEST)"
	@echo "waves written under $(WAVE_DIR)"

#------------------------------------------------------------------------------
# Mutation testing. Populated in Phase 10-11: each mutation is a single-line RTL
# edit plus the test that must fail because of it.
#------------------------------------------------------------------------------
mutate:
	@if [ -x $(SCRIPTS)/mutate.sh ]; then \
	  $(SCRIPTS)/mutate.sh; \
	else \
	  echo "mutate: no mutation set registered yet (arrives in Phase 10)"; \
	fi

clean:
	rm -rf obj_dir sim_build $(WAVE_DIR)/*.vcd $(WAVE_DIR)/*.fst
	find $(TB_DIR) -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf $(TB_DIR)/.pytest_cache $(ROOT)/.pytest_cache

help:
	@echo "make lint                 verilator -Wall + structural checks"
	@echo "make test                 run the whole cocotb suite"
	@echo "make test TEST=<name>     run tests matching <name>"
	@echo "make waves TEST=<name>    run <name> and keep the waveform"
	@echo "make mutate               run the mutation set"
	@echo "make all                  lint + test"
