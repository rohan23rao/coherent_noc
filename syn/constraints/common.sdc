#==============================================================================
# syn/constraints/common.sdc -- the constraints every block shares.
#
# The principle this file is written to: an unconstrained path is worse than a
# violating one, because a violating path is reported and an unconstrained path
# is silent. check_timing runs right after this and its output is the first
# report to read.
#
# $PERIOD, $SYN_DRIVING_CELL, $SYN_LOAD_CELL and friends come from
# syn/setup/pdk.tcl. Nothing here names a technology.
#==============================================================================


#------------------------------------------------------------------------------
# Helpers, and the reason they exist.
#
# A block SDC that overrides the default budget for a class of ports is making
# a claim about what is on the other side of them. If its pattern matches
# nothing -- a renamed port, a typo -- the claim is silently replaced by the
# conservative default and nobody ever finds out: the design still times, just
# against a budget that was never intended. So a required override that matches
# nothing is an error.
#
# The _opt forms exist for patterns that are legitimately absent on some blocks
# (`dbg_*` on the router, `hold_*` below tile level). They say "if this exists,
# constrain it this way", which is a different and weaker claim, deliberately.
#
# `make -C syn dryrun` exercises every one of these against the block's real
# port list, with no tool and no licence -- see syn/scripts/dryrun.tcl.
#------------------------------------------------------------------------------
proc sdc_where {} {
  return [expr {[info exists ::TOP] ? $::TOP : "this block"}]
}

proc sdc_in {pattern delay} {
  set p [get_ports $pattern -quiet]
  if {[sizeof_collection $p] == 0} {
    error "SDC: input pattern '$pattern' matched no port on [sdc_where]"
  }
  set_input_delay $delay -clock clk $p
}
proc sdc_out {pattern delay} {
  set p [get_ports $pattern -quiet]
  if {[sizeof_collection $p] == 0} {
    error "SDC: output pattern '$pattern' matched no port on [sdc_where]"
  }
  set_output_delay $delay -clock clk $p
}
proc sdc_in_opt {pattern delay} {
  set p [get_ports $pattern -quiet]
  if {[sizeof_collection $p] > 0} { set_input_delay $delay -clock clk $p }
}
proc sdc_out_opt {pattern delay} {
  set p [get_ports $pattern -quiet]
  if {[sizeof_collection $p] > 0} { set_output_delay $delay -clock clk $p }
}
proc sdc_false_from_opt {pattern} {
  set p [get_ports $pattern -quiet]
  if {[sizeof_collection $p] > 0} { set_false_path -from $p }
}
proc sdc_false_to_opt {pattern} {
  set p [get_ports $pattern -quiet]
  if {[sizeof_collection $p] > 0} { set_false_path -to $p }
}

set CLK_PORT clk

create_clock -name clk -period $PERIOD [get_ports $CLK_PORT]

# Pre-CTS: the clock is ideal, and uncertainty stands in for the skew and jitter
# the clock tree will add. 5% of the period plus a fixed margin is the usual
# pre-layout guess; it is a guess, and it is why the number to quote from this
# run is the critical PATH, not the slack.
set_clock_uncertainty [expr {$PERIOD * 0.05 + 0.02}] [get_clocks clk]
set_clock_transition  [expr {$PERIOD * 0.02}]        [get_clocks clk]
set_clock_latency     [expr {$PERIOD * 0.10}]        [get_clocks clk]
set_dont_touch_network [get_clocks clk]

#------------------------------------------------------------------------------
# Reset.
#
# Every flop in the design takes an asynchronous active-low reset. At the top
# that reset is `arst_n`, which passes through reset_sync -- asynchronously
# asserted, synchronously deasserted -- before it reaches anything. At block
# level the port is `rst_n` and the synchroniser is above it.
#
# The path from the reset PORT to a flop's async pin is genuinely untimed: the
# synchroniser is what makes deassertion safe, and timing the assertion edge
# would be timing something that is allowed to be asynchronous. Recovery and
# removal at the synchroniser's own flops are what matter, and those are
# internal paths that stay timed.
#------------------------------------------------------------------------------
foreach rp {arst_n rst_n} {
  set p [get_ports $rp -quiet]
  if {[sizeof_collection $p] > 0} {
    set_false_path -from $p
    set_ideal_network -no_propagate $p
  }
}

#------------------------------------------------------------------------------
# Default I/O budget, for any port a block-specific SDC does not override.
#
# 40% of the period each way is the conservative "I do not know what is on the
# other side" number. Every block-level SDC in this directory overrides it for
# the ports whose neighbour IS known, with the reason written down -- because a
# blanket 40% on a port that is driven by a register one wire away is 40% of
# the period thrown into a hole.
#------------------------------------------------------------------------------
set IO_DEFAULT [expr {$PERIOD * 0.40}]

set data_inputs  [remove_from_collection [all_inputs] \
                    [get_ports [list $CLK_PORT arst_n rst_n] -quiet]]
set_input_delay  $IO_DEFAULT -clock clk $data_inputs
set_output_delay $IO_DEFAULT -clock clk [all_outputs]

set_driving_cell -lib_cell $::SYN_DRIVING_CELL -pin $::SYN_DRIVING_PIN \
                 $data_inputs
set_load [load_of [lindex $target_library 0]/$::SYN_LOAD_CELL/$::SYN_LOAD_PIN] \
         [all_outputs]

#------------------------------------------------------------------------------
# Design rules. Left deliberately tight: a design that only meets timing with
# 40-load nets is a design that will not meet it after placement.
#------------------------------------------------------------------------------
set_max_fanout    16 [current_design]
set_max_transition [expr {$PERIOD * 0.15}] [current_design]
set_max_area 0

#------------------------------------------------------------------------------
# The SRAM black boxes.
#
# With SYN_SRAM=blackbox the arrays have no timing arcs at all, so every path
# into and out of them would be unconstrained -- silently. Budgeting half the
# period on each side is not a model of the array; it is a placeholder that
# makes those paths appear in the reports, so the logic feeding the address and
# the logic consuming the read data are both timed.
#
# What this run therefore does NOT tell you: the array access time. That comes
# from the foundry memory compiler's .lib, and until it is dropped in, the
# number to quote from here is the control-logic critical path.
#------------------------------------------------------------------------------
set bb [get_cells -hier -filter "ref_name =~ sram_1rw*" -quiet]
if {[sizeof_collection $bb] > 0} {
  set bb_in  [get_pins -of_objects $bb -filter "direction == in"  -quiet]
  set bb_out [get_pins -of_objects $bb -filter "direction == out" -quiet]
  set_max_delay [expr {$PERIOD * 0.50}] -to   $bb_in
  set_max_delay [expr {$PERIOD * 0.50}] -from $bb_out
  set_dont_touch $bb
  puts "SDC: [sizeof_collection $bb] SRAM black box(es) budgeted at 50% of the period each side."
}
