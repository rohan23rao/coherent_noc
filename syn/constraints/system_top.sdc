#==============================================================================
# system_top.sdc
#
# The whole design. Every link is internal here, so the only ports that carry
# real traffic are the four core interfaces. Expect this run to be slow and the
# area to be dominated by whatever SYN_SRAM is set to -- it is worth doing once,
# to see the four tiles' critical paths side by side and confirm they are the
# same path, but the block runs are where the QoR conversation happens.
#==============================================================================

foreach pat {hold_vn0_i hold_vn1_i hold_vn2_i hold_vn2_dir_i} {
  set p [get_ports $pat* -quiet]
  if {[sizeof_collection $p] > 0} { set_false_path -from $p }
}
set dbg_o [get_ports dbg_*_o -quiet]
if {[sizeof_collection $dbg_o] > 0} { set_false_path -to $dbg_o }
set dbg_i [get_ports dbg_*_i -quiet]
if {[sizeof_collection $dbg_i] > 0} { set_false_path -from $dbg_i }

# cycle_count_o is a free-running counter brought out for the testbench. It is
# not part of the design's function and must not set the critical path.
set cc [get_ports cycle_count_o* -quiet]
if {[sizeof_collection $cc] > 0} { set_false_path -to $cc }
