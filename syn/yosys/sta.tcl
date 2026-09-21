#==============================================================================
# syn/yosys/sta.tcl -- real static timing on the mapped netlist, with OpenSTA.
#
# This exists because abc's `stime` is not static timing analysis. abc walks the
# mapped netlist with the Liberty's NLDM tables and no constraints at all: no
# clock, no input or output budget, no setup check against a real flop. It
# answers "how long is the longest chain of gates", which is a different and
# much friendlier question than "does this design close".
#
# The constraints below are the same ones syn/constraints/*.sdc argue for the
# Design Compiler flow, in the same proportions, so the two flows are asking
# the same question of the same design:
#
#   links (flit_*, credit_*)   register-to-register, 25% in / 15% out
#   coherence messages (vn*_)  also register-to-register, through msg_hold
#   core_* and everything else  40% each way -- an unknown neighbour
#   dbg_*                       false path, observation only
#
# Environment: STA_LIB, STA_NETLIST, STA_TOP, STA_PERIOD_NS, STA_OUT.
#==============================================================================

proc envdef {name default} {
  global env
  return [expr {[info exists env($name)] ? $env($name) : $default}]
}

set LIB     $env(STA_LIB)
set NETLIST $env(STA_NETLIST)
set TOP     $env(STA_TOP)
set PERIOD  $env(STA_PERIOD_NS)
set OUT     $env(STA_OUT)
set DRIVER  [envdef STA_DRIVER ""]
set LOAD_PF [envdef STA_LOAD_PF 0.001]

read_liberty $LIB
# AFTER read_liberty: the units default to the library's (picoseconds, for
# ASAP7), and setting them earlier is silently ignored.
set_cmd_units -time ns -capacitance pf -resistance kohm -voltage v
# The SRAM black boxes, declared with pin directions and no timing at all.
# syn/yosys/blackbox_lib.py generates it; decision D25 says why it carries no
# timing arcs rather than invented ones.
if {[info exists env(STA_BB_LIB)] && [file exists $env(STA_BB_LIB)]} {
  read_liberty $env(STA_BB_LIB)
}
read_verilog $NETLIST
link_design $TOP

create_clock -name clk -period $PERIOD [get_ports clk]
# Pre-layout: the clock is ideal and uncertainty stands in for the tree that
# does not exist yet. Same 5% + a floor as the DC flow.
set_clock_uncertainty [expr {$PERIOD * 0.05 + 0.005}] [get_clocks clk]

# Reset is asynchronously asserted and synchronously deasserted above this
# block, so the port-to-async-pin path is genuinely untimed.
foreach rp {arst_n rst_n} {
  if {[llength [get_ports -quiet $rp]] > 0} { set_false_path -from [get_ports $rp] }
}

# After flattening, ports are bit-blasted -- `flit_o[10]`, not `flit_o` -- so
# direction cannot be recovered from the name's suffix. Ask the tool instead.
proc data_inputs {} {
  set r {}
  foreach p [all_inputs] {
    set n [get_full_name $p]
    if {[regexp {^(clk|rst_n|arst_n)(\[.*\])?$} $n]} { continue }
    lappend r $p
  }
  return $r
}
proc data_outputs {} { return [all_outputs] }

set DEF_IN  [expr {$PERIOD * 0.40}]
set DEF_OUT [expr {$PERIOD * 0.40}]
set REG_IN  [expr {$PERIOD * 0.25}]
set REG_OUT [expr {$PERIOD * 0.15}]

foreach p [data_inputs]  { set_input_delay  $DEF_IN  -clock clk $p }
foreach p [data_outputs] { set_output_delay $DEF_OUT -clock clk $p }

# Ports whose neighbour IS known get the register-to-register budget.
foreach pat {flit_valid_i* flit_i* credit_valid_i* credit_vc_i* credit_tail_i*
             vn0_*_i vn1_*_i vn2_*_i l1_vn*_i dir_vn*_i} {
  foreach p [get_ports -quiet $pat] { set_input_delay $REG_IN -clock clk $p }
}
foreach pat {flit_valid_o* flit_o* credit_valid_o* credit_vc_o* credit_tail_o*
             vn0_*_o vn1_*_o vn2_*_o l1_vn*_o dir_vn*_o} {
  foreach p [get_ports -quiet $pat] { set_output_delay $REG_OUT -clock clk $p }
}

# Debug and static configuration are not timing paths.
foreach pat {dbg_*_o cycle_count_o rst_n_o} {
  foreach p [get_ports -quiet $pat] { set_false_path -to $p }
}
foreach pat {dbg_*_i hold_vn0_i* hold_vn1_i* hold_vn2_i* hold_vn2_dir_i*} {
  foreach p [get_ports -quiet $pat] { set_false_path -from $p }
}

if {$DRIVER ne ""} {
  set_driving_cell -lib_cell $DRIVER [data_inputs]
}
set_load $LOAD_PF [data_outputs]

# The array boundary.
#
# A black box with no timing arcs has no setup check on its inputs and no
# arrival at its outputs, so every path that ends at the address logic or
# starts at the read data would be unconstrained -- and unconstrained is
# silent. Budgeting half the period on each side is not a model of the array;
# it is what makes those paths appear in the report at all. The array's own
# access time is the one number this flow cannot give you: it needs a memory
# compiler. Same position as syn/constraints/common.sdc.
set bb [get_cells -quiet -hier -filter "ref_name =~ sram_1rw_*"]
if {[llength $bb] > 0} {
  foreach c $bb {
    foreach p [get_pins -quiet -of_objects $c] {
      if {[get_property $p direction] eq "input"} {
        set_max_delay [expr {$PERIOD * 0.50}] -to $p
      } else {
        set_max_delay [expr {$PERIOD * 0.50}] -from $p
      }
    }
  }
  puts "STA: [llength $bb] SRAM black box(es) budgeted at 50% of the period."
}

# OpenSTA has no `redirect`, so everything goes to stdout and the driver keeps
# the whole transcript as sta.rpt. One stream, one file, nothing to keep in
# step -- and the unconstrained report is in it, which is the one to read first.
puts "==== report_checks -path_delay max ===="
report_checks -path_delay max -group_count 5 -digits 4 \
              -fields {slew capacitance fanout}
puts "==== report_checks -path_delay min ===="
report_checks -path_delay min -group_count 2 -digits 4
puts "==== unconstrained ===="
report_checks -unconstrained -digits 4
puts "==== summary ===="
report_tns -digits 4
report_wns -digits 4
report_worst_slack -max -digits 4
report_units

# The driver parses `worst slack max` out of the transcript above, which is
# already in the units set here. `sta::worst_slack_cmd` returns SECONDS, and
# formatting that as if it were nanoseconds prints -0.0000 for every design --
# which reads as "closes with zero slack" and is how a 1.15 ns critical path
# got reported as meeting a 0.12 ns target.
puts "STA_PERIOD_NS $PERIOD"
exit 0
