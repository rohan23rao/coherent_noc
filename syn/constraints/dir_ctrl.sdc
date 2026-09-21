#==============================================================================
# dir_ctrl.sdc
#
# Same shape as l1_cache.sdc. The one addition is the memory channel: the
# memory controller is outside this block and its timing is unknown, so it keeps
# the conservative default rather than being given a register-to-register
# budget it has not earned.
#==============================================================================

set NET_IN  [expr {$PERIOD * 0.25}]
set NET_OUT [expr {$PERIOD * 0.15}]

foreach pat {vn0_ vn1_ vn2_} {
  set pi [get_ports ${pat}*_i -quiet]
  set po [get_ports ${pat}*_o -quiet]
  if {[sizeof_collection $pi] > 0} { set_input_delay  $NET_IN  -clock clk $pi }
  if {[sizeof_collection $po] > 0} { set_output_delay $NET_OUT -clock clk $po }
}

set dbg_o [get_ports dbg_*_o -quiet]
if {[sizeof_collection $dbg_o] > 0} { set_false_path -to $dbg_o }
