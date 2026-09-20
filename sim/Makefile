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
	$(RTL_DIR)/top/system_top.sv

VERILATOR_LINT_FLAGS := \
	--lint-only \
	-Wall \
	--timing \
	--top-module $(TOP)

WAIVERS := $(ROOT)/lint/waivers.vlt

.PHONY: all lint lint-rtl lint-style test waves mutate clean help

all: lint test

#------------------------------------------------------------------------------
# Lint: Verilator -Wall with zero warnings, plus the structural checks Verilator
# does not perform. Both must pass.
#------------------------------------------------------------------------------
lint: lint-style lint-rtl

lint-rtl:
	@echo "== verilator --lint-only -Wall =="
	$(VERILATOR) $(VERILATOR_LINT_FLAGS) $(WAIVERS) $(RTL_SRCS)
	@echo "lint: clean"

lint-style:
	@echo "== structural style checks =="
	@$(SCRIPTS)/check_style.sh $(RTL_DIR)

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
