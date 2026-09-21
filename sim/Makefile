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
PYTHON     ?= python3

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
	$(RTL_DIR)/l1/mshr_file.sv \
	$(RTL_DIR)/l1/l1_coh_fsm.sv \
	$(RTL_DIR)/l2/dir_coh_fsm.sv \
	$(RTL_DIR)/l1/l1_cache.sv \
	$(RTL_DIR)/l2/l2_bank.sv \
	$(RTL_DIR)/l2/tbe_file.sv \
	$(RTL_DIR)/l2/dir_ctrl.sv \
	$(RTL_DIR)/lib/msg_hold.sv \
	$(RTL_DIR)/tile/tile_nic.sv \
	$(RTL_DIR)/tile/tile_top.sv \
	$(RTL_DIR)/top/system_top.sv

VERILATOR_LINT_FLAGS_BASE := \
	--lint-only \
	-Wall \
	--timing

VERILATOR_LINT_FLAGS := $(VERILATOR_LINT_FLAGS_BASE) --top-module $(TOP)

WAIVERS := $(ROOT)/lint/waivers.vlt
# Extra waivers for the per-module pass only -- see the file's header for why
# they are not global.
WAIVERS_PERFILE := $(ROOT)/lint/waivers_perfile.vlt

.PHONY: all lint lint-rtl lint-style lint-libs lint-slang lint-synth lint-synth-bb lint-scale test waves mutate diagrams diagrams-check print-rtl-srcs clean help

all: lint test

#------------------------------------------------------------------------------
# Lint: Verilator -Wall with zero warnings, plus the structural checks Verilator
# does not perform. Both must pass.
#------------------------------------------------------------------------------
lint: lint-style lint-rtl lint-libs lint-scale lint-synth lint-synth-bb lint-slang

lint-rtl:
	@echo "== verilator --lint-only -Wall =="
	$(VERILATOR) $(VERILATOR_LINT_FLAGS) $(WAIVERS) $(RTL_SRCS)
	@echo "lint: clean"

# Elaborate the design exactly as Design Compiler will see it: `SYNTHESIS`
# defined, so every SVA block and every debug $display is compiled out. A
# design that lints clean in simulation mode and not in this one is a design
# that is about to fail on a machine this repository cannot run on.
# lint/waivers_synth.vlt lists the four signals whose only consumer is an
# assertion, with the assertion named; everything else this pass reports is
# real. See syn/README.md.
WAIVERS_SYNTH := $(ROOT)/lint/waivers_synth.vlt

lint-synth:
	@echo "== verilator --lint-only -Wall -DSYNTHESIS =="
	$(VERILATOR) $(VERILATOR_LINT_FLAGS) -DSYNTHESIS \
	  $(WAIVERS) $(WAIVERS_SYNTH) $(RTL_SRCS)
	@echo "lint-synth: clean"

# The same design again, with syn/blackbox/*.sv substituted for the behavioural
# array and the behavioural memory -- the exact source set dc_shell reads. Its
# job is to catch a stub whose port list has drifted from the module it stands
# in for, which is a hard error here and a dc_shell error two minutes into a run
# on a machine this repository cannot log into.
WAIVERS_BB := $(ROOT)/lint/waivers_blackbox.vlt
SYN_DIR    := $(ROOT)/syn
RTL_SRCS_BB := $(patsubst $(RTL_DIR)/mem/mem_model.sv,$(SYN_DIR)/blackbox/mem_model.sv,\
                 $(patsubst $(RTL_DIR)/lib/sram_1rw.sv,$(SYN_DIR)/blackbox/sram_1rw.sv,$(RTL_SRCS)))

lint-synth-bb:
	@echo "== verilator --lint-only -Wall -DSYNTHESIS, black boxes substituted =="
	$(VERILATOR) $(VERILATOR_LINT_FLAGS) -DSYNTHESIS \
	  $(WAIVERS) $(WAIVERS_SYNTH) $(WAIVERS_BB) $(RTL_SRCS_BB)
	@echo "lint-synth-bb: clean"

# Elaborate at mesh sizes other than the shipped one. See the script's header
# for what this does and does not prove; the short version is that it checks
# the parameters are parameters, not that the protocol still works at 64 tiles.
lint-scale:
	@echo "== verilator -Wall at other mesh sizes =="
	@$(SCRIPTS)/check_scale.sh $(VERILATOR) $(RTL_DIR) $(ROOT)/lint "$(RTL_SRCS)"

lint-style:
	@echo "== structural style checks =="
	@$(SCRIPTS)/check_style.sh $(RTL_DIR)

# Elaborating only from the system top leaves modules that nothing instantiates
# yet partially unchecked. Lint each library module as its own top as well, so a
# module is fully linted the day it is written rather than the day it is wired
# in.
LIB_MODULES := reset_sync rr_arbiter credit_counter fifo skid_buffer sram_1rw msg_hold
NOC_MODULES := route_compute crossbar input_unit vc_allocator switch_allocator router noc_top mem_model mshr_file l1_coh_fsm dir_coh_fsm l1_cache l2_bank tbe_file dir_ctrl tile_nic tile_top

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
	    $(WAIVERS) $(WAIVERS_PERFILE) $(RTL_DIR)/pkg/coh_pkg.sv $(RTL_DIR)/lib/$$m.sv \
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
	    $(WAIVERS) $(WAIVERS_PERFILE) $(RTL_SRCS) \
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
# Break one protocol arc at a time and require its named test to notice.
# `make mutate MUT=<name>` runs one; `make mutate RACE=R3` runs a race's set.
mutate:
	@$(PYTHON) $(SCRIPTS)/mutate.py \
	  $(if $(MUT),--only $(MUT),) $(if $(RACE),--race $(RACE),)

# The synthesis flow reads the file list from here rather than keeping its own,
# so the two cannot drift. One source of truth for "what the design is".
print-rtl-srcs:
	@for f in $(RTL_SRCS); do echo $$f; done

#------------------------------------------------------------------------------
# Diagrams. The SVGs are generated; the state machines are rendered from the
# same table the RTL is checked against, so a wrong one cannot be produced.
#------------------------------------------------------------------------------
diagrams:
	@$(PYTHON) $(SCRIPTS)/diagrams/gen_system.py
	@$(PYTHON) $(SCRIPTS)/diagrams/gen_uarch.py
	@$(PYTHON) $(SCRIPTS)/diagrams/gen_proto.py
	@$(PYTHON) $(SCRIPTS)/diagrams/gen_verif.py
	@$(PYTHON) $(SCRIPTS)/diagrams/gen_fsm.py

# The claim in docs/diagrams.md is that a figure is a function of the design,
# and the check for that claim is: regenerate, and the tree is unchanged. This
# target is that check. It fails if a committed figure is stale -- which is the
# one way a generated diagram can still lie.
diagrams-check: diagrams
	@if git -C $(ROOT) diff --quiet -- docs/img docs/diagrams.md; then \
	  echo "diagrams: up to date"; \
	else \
	  echo "diagrams: the working tree does not match what the generators"; \
	  echo "produce from the current design (run this on a clean tree --"; \
	  echo "an uncommitted prose edit to docs/diagrams.md counts too):"; \
	  git -C $(ROOT) diff --stat -- docs/img docs/diagrams.md; \
	  echo "run 'make diagrams' and commit the result"; \
	  exit 1; \
	fi

# The synthesis table in docs/synthesis.md is generated from the runs, not
# transcribed from them. Same rule as the diagrams: a number a human retyped is
# a number that can drift from the thing it describes.
syn-report:
	@$(PYTHON) $(ROOT)/syn/yosys/collect.py --inject $(ROOT)/docs/synthesis.md

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
	@echo "make diagrams             regenerate docs/img and the state machines"
	@echo "make diagrams-check       fail if a committed figure is stale"
	@echo "make lint-synth           elaborate with SYNTHESIS defined, as DC sees it"
	@echo "make lint-synth-bb        the same, with the syn/ black boxes substituted"
	@echo "make lint-scale           elaborate at 8, 16 and 64 tiles"
	@echo "make all                  lint + test"
