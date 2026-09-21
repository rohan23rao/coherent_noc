#==============================================================================
# syn/scripts/dryrun.tcl -- run the synthesis flow with Design Compiler stubbed.
#
# This machine has no Synopsys tools, so syn/scripts/synth.tcl and the SDC
# files cannot be executed for real. What they CAN be is executed against a set
# of no-op stubs that record what was called -- which exercises everything in
# them that is my logic rather than the tool's:
#
#   * every variable resolves, in every branch
#   * the file list substitution puts the black boxes in and leaves the rest
#   * the per-top elaboration parameters are found and formatted
#   * the right block SDC is selected, and common.sdc runs before it
#   * every `get_ports` pattern in every SDC matches a port that EXISTS -- the
#     port list is extracted from the elaborated RTL by scripts/ports.py, so a
#     constraint aimed at a misspelled port is caught here rather than silently
#     constraining nothing on the real run
#   * no SDC leaves a port with no input or output delay
#
# What it cannot check: anything about the tool. `compile_ultra` might reject
# an option, a library might not have `INVX2_RVT`, `report_clock_gating` might
# need a licence. Those are found on the first real run, and syn/README.md says
# so. The point of this file is that they are the ONLY things found then.
#
# Run it with:  make -C syn dryrun
#==============================================================================

set DRY_ROOT [file normalize [file dirname [file dirname [info script]]]]
rename exit   exit_real
rename source source_real
set ::calls {}
set ::warnings {}

proc note {args} { lappend ::calls [join $args " "] }
proc warn {msg}  { lappend ::warnings $msg }

#------------------------------------------------------------------------------
# The port list of the block being constrained, from the elaborated RTL.
#------------------------------------------------------------------------------
set ports {}
if {[info exists env(DRY_PORTS)] && [file exists $env(DRY_PORTS)]} {
  set fh [open $env(DRY_PORTS) r]
  foreach p [split [string trim [read $fh]] "\n"] {
    if {[string trim $p] ne ""} { lappend ports [string trim $p] }
  }
  close $fh
}
set ::ALL_PORTS $ports

#------------------------------------------------------------------------------
# Collections. A collection is just a Tcl list here; that is enough for the
# `sizeof_collection > 0` guards the SDC files are built out of.
#------------------------------------------------------------------------------
proc get_ports {args} {
  set pats {}
  foreach a $args {
    if {[string match "-*" $a]} { continue }
    foreach p $a { lappend pats $p }
  }
  set hits {}
  foreach pat $pats {
    foreach p $::ALL_PORTS { if {[string match $pat $p]} { lappend hits $p } }
  }
  set hits [lsort -unique $hits]
  if {[llength $hits] == 0 && ![string match "*-quiet*" $args]} {
    warn "get_ports {$pats} matched nothing, and was not -quiet"
  }
  return $hits
}
# This project's naming convention is strict -- every port ends _i or _o, apart
# from clk and the reset -- so direction is recoverable from the name alone.
# check_style.sh enforces that, which is what makes it safe to rely on here.
proc all_inputs {}  {
  set r {}
  foreach p $::ALL_PORTS { if {![string match "*_o" $p]} { lappend r $p } }
  return $r
}
proc all_outputs {} {
  set r {}
  foreach p $::ALL_PORTS { if {[string match "*_o" $p]} { lappend r $p } }
  return $r
}
proc all_registers {args} { return {reg_stub} }
proc get_clocks {args}    { return {clk} }
proc get_cells {args}     { return {} }
proc get_pins {args}      { return {} }
proc current_design {args} { note current_design $args; return {design} }
proc sizeof_collection {c} { return [llength $c] }
proc remove_from_collection {a b} {
  set r {}
  foreach x $a { if {[lsearch -exact $b $x] < 0} { lappend r $x } }
  return $r
}
proc load_of {args} { return 0.01 }

#------------------------------------------------------------------------------
# Constraint commands. These record which ports they touched, so the dry run
# can say afterwards whether anything was left unconstrained.
#------------------------------------------------------------------------------
set ::have_in  {}
set ::have_out {}
set ::false_in {}
set ::false_out {}

proc set_input_delay {args} {
  note set_input_delay
  foreach p [lindex $args end] { lappend ::have_in $p }
}
proc set_output_delay {args} {
  note set_output_delay
  foreach p [lindex $args end] { lappend ::have_out $p }
}
proc set_false_path {args} {
  note set_false_path $args
  set i [lsearch -exact $args "-from"]
  if {$i >= 0} { foreach p [lindex $args [expr {$i + 1}]] { lappend ::false_in $p } }
  set i [lsearch -exact $args "-to"]
  if {$i >= 0} { foreach p [lindex $args [expr {$i + 1}]] { lappend ::false_out $p } }
}

foreach cmd {create_clock set_clock_uncertainty set_clock_transition
             set_clock_latency set_dont_touch_network set_ideal_network
             set_driving_cell set_load set_max_fanout set_max_transition
             set_max_area set_max_delay set_dont_touch set_app_var
             define_design_lib link check_timing write_sdc check_design
             compile_ultra compile report_qor report_timing report_area
             report_constraint report_power report_reference
             report_clock_gating write group_path set_operating_conditions
             set_wire_load_model set_wire_load_mode set_fix_hold} {
  proc $cmd {args} "note $cmd \$args"
}

proc analyze {args} {
  set f [lindex $args end]
  note analyze $f
  if {![file exists $f]} { warn "analyze: file does not exist: $f" }
  return 1
}
proc elaborate {args} { note elaborate $args; return 1 }
proc redirect {args} { uplevel 1 [lindex $args end] }
proc exit {args} { return }

# Design Compiler's `source` takes -echo and -verbose; Tcl's does not.
proc source {args} {
  set f [lindex $args end]
  uplevel 1 [list source_real $f]
}

# pdk.tcl insists on a real kit, which is the right behaviour for the real
# flow and useless here. Give it a fake one so its glob and error paths are
# exercised rather than skipped.
# Make both flavours the saed32 branch can pick, so the default (lvt/tt) and
# the pessimistic override (rvt/ss) are both reachable from a dry run.
foreach {vt corner file} {
  lvt tt saed32lvt_tt0p85v25c.db
  rvt ss saed32rvt_ss0p75v125c.db
} {
  set fake [file join [pwd] .dryrun_pdk lib stdcell_$vt db_nldm]
  file mkdir $fake
  close [open [file join $fake $file] w]
}
set env(SAED32_ROOT) [file join [pwd] .dryrun_pdk]
set env(SYN_PDK) saed32

#------------------------------------------------------------------------------
# Go.
#------------------------------------------------------------------------------
source $DRY_ROOT/scripts/synth.tcl

#------------------------------------------------------------------------------
# What the flow left unconstrained. An unconstrained port is the failure this
# whole file exists to find, because on the real tool it is silent.
#------------------------------------------------------------------------------
set unconstrained {}
foreach p $::ALL_PORTS {
  if {$p in {clk rst_n arst_n}} { continue }
  if {[string match "*_o" $p]} {
    if {$p ni $::have_out && $p ni $::false_out} { lappend unconstrained "out $p" }
  } else {
    if {$p ni $::have_in && $p ni $::false_in} { lappend unconstrained "in  $p" }
  }
}

puts "\n---- dry run ----"
puts "commands issued : [llength $::calls]"
puts "analyze calls   : [llength [lsearch -all -inline $::calls "analyze *"]]"
puts "ports           : [llength $::ALL_PORTS]"
if {[llength $::warnings]} {
  puts "WARNINGS:"
  foreach w $::warnings { puts "  $w" }
}
if {[llength $unconstrained]} {
  puts "UNCONSTRAINED PORTS:"
  foreach u $unconstrained { puts "  $u" }
}
if {[llength $::warnings] || [llength $unconstrained]} {
  puts "dryrun: FAIL"
  exit_real 1
}
puts "dryrun: clean"
