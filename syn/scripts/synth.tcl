#==============================================================================
# syn/scripts/synth.tcl -- the Design Compiler flow for this design.
#
# Driven entirely by environment variables so that it is the same script on
# every machine and for every block:
#
#   SYN_TOP      module to synthesise      (router, tile_nic, l1_cache,
#                                           dir_ctrl, tile_top, system_top)
#   SYN_PDK      saed32 | asap7 | sky130   (default saed32)
#   SYN_PERIOD   clock period in library time units (default: per PDK)
#   SYN_SRAM     blackbox | flops          (default blackbox)
#   SYN_EFFORT   ultra | high              (default ultra)
#   SYN_FILELIST path to the RTL file list (written by syn/Makefile)
#   SYN_OUT      output directory          (default syn/out/$SYN_TOP.$SYN_PDK)
#
# Run it as:   dc_shell -f syn/scripts/synth.tcl | tee $SYN_OUT/synth.log
# or, easier:  make -C syn router PDK=saed32 PERIOD=2.0
#==============================================================================

proc envdef {name default} {
  global env
  return [expr {[info exists env($name)] ? $env($name) : $default}]
}

set TOP      [envdef SYN_TOP      "router"]
set SRAM     [envdef SYN_SRAM     "blackbox"]
set EFFORT   [envdef SYN_EFFORT   "ultra"]
set FILELIST [envdef SYN_FILELIST "filelist.f"]
set SYN_ROOT [file normalize [file dirname [file dirname [info script]]]]

source -echo -verbose $SYN_ROOT/setup/pdk.tcl

set PERIOD [envdef SYN_PERIOD $::SYN_DEFAULT_PERIOD]
set OUT    [envdef SYN_OUT "$SYN_ROOT/out/$TOP.$PDK"]
file mkdir $OUT

puts "TOP        : $TOP"
puts "PERIOD     : $PERIOD"
puts "SRAM       : $SRAM"

#------------------------------------------------------------------------------
# Sources.
#
# The file list comes from sim/Makefile (`make -C sim print-rtl-srcs`), so the
# thing that is synthesised is by construction the thing that is simulated and
# linted. Two substitutions are made, and both are stated in the reports:
#
#   * sram_1rw   -- a behavioural array. With SYN_SRAM=blackbox it is replaced
#                   by an empty module so DC leaves it unresolved, which is
#                   what a real flow does before the memory compiler's macro
#                   and .lib are dropped in. With SYN_SRAM=flops the real
#                   behavioural model is synthesised into flip-flops, which is
#                   honest but enormous -- useful only for the tag arrays.
#   * mem_model  -- the testbench memory behind each directory bank. It is not
#                   part of the design and is always a black box here.
#------------------------------------------------------------------------------
set rtl {}
set fh [open $FILELIST r]
foreach line [split [read $fh] "\n"] {
  set line [string trim $line]
  if {$line eq "" || [string match "#*" $line]} { continue }
  set base [file tail $line]
  if {$base eq "mem_model.sv"} {
    lappend rtl "$SYN_ROOT/blackbox/mem_model.sv"
  } elseif {$base eq "sram_1rw.sv" && $SRAM eq "blackbox"} {
    lappend rtl "$SYN_ROOT/blackbox/sram_1rw.sv"
  } else {
    lappend rtl $line
  }
}
close $fh

#------------------------------------------------------------------------------
# Elaboration. SYNTHESIS is defined, which compiles out every SVA block and
# every debug $display. `make lint-synth` elaborates the same configuration
# with Verilator, so a front-end problem is found before dc_shell is started.
#------------------------------------------------------------------------------
define_design_lib WORK -path $OUT/work
set_app_var hdlin_sverilog_std 2017

foreach f $rtl {
  if {![analyze -format sverilog -define {SYNTHESIS} -work WORK $f]} {
    error "analyze failed on $f"
  }
}

# Per-top elaboration parameters. A leaf that takes its position as a parameter
# has to be told one, and the corner instance is the interesting one: at
# MY_X=1,MY_Y=1 route_compute's edge cases fold away, so synthesising the
# middle-of-the-mesh case (0,0) is the pessimistic and therefore correct choice.
array set TOP_PARAMS {
  router     {MY_X 0 MY_Y 0}
  tile_nic   {TILE_ID 0}
  tile_top   {TILE_ID 0}
  l1_cache   {}
  dir_ctrl   {BANK_ID 0 ENABLE_E 1}
  system_top {}
}
set params {}
if {[info exists TOP_PARAMS($TOP)]} {
  foreach {k v} $TOP_PARAMS($TOP) { lappend params "$k=$v" }
}
if {[llength $params]} {
  elaborate $TOP -work WORK -parameters [join $params ","]
} else {
  elaborate $TOP -work WORK
}
current_design $TOP
link

#------------------------------------------------------------------------------
# Structural checks BEFORE compile. check_design after compile tells you about
# the netlist; check_design before it tells you about the RTL, which is the
# thing that can still be fixed.
#------------------------------------------------------------------------------
redirect $OUT/check_design.rpt { check_design -summary }
redirect -append $OUT/check_design.rpt { check_design }

#------------------------------------------------------------------------------
# Constraints.
#------------------------------------------------------------------------------
source -echo -verbose $SYN_ROOT/constraints/common.sdc
if {[file exists $SYN_ROOT/constraints/$TOP.sdc]} {
  source -echo -verbose $SYN_ROOT/constraints/$TOP.sdc
} else {
  puts "NOTE: no block-specific SDC for $TOP; common.sdc only."
}
redirect $OUT/check_timing.rpt { check_timing }
write_sdc $OUT/$TOP.sdc

#------------------------------------------------------------------------------
# Compile. compile_ultra needs a DC Ultra licence; SYN_EFFORT=high falls back
# to plain compile so the flow still runs without one.
#------------------------------------------------------------------------------
set_app_var compile_seqmap_propagate_constants false
if {$EFFORT eq "ultra"} {
  compile_ultra -no_autoungroup
} else {
  compile -map_effort high -area_effort high
}

source -echo $SYN_ROOT/scripts/reports.tcl

write -format verilog -hierarchy -output $OUT/$TOP.mapped.v
write -format ddc     -hierarchy -output $OUT/$TOP.ddc

puts "\n=========================================================="
puts "  $TOP on $PDK at period $PERIOD -- reports in $OUT"
puts "=========================================================="
exit 0
