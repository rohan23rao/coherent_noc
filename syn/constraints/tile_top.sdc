#==============================================================================
# tile_top.sdc
#
# At tile level the only real neighbours are the four mesh links and the core.
# Everything else is internal, which is the point of synthesising this level:
# it is the first level where the L1-to-NIC-to-router path is a real path and
# not an assumption in two separate SDC files.
#==============================================================================

set LINK_IN  [expr {$PERIOD * 0.25}]
set LINK_OUT [expr {$PERIOD * 0.15}]

foreach pat {flit_valid_i flit_i credit_valid_i credit_vc_i credit_tail_i} {
  set p [get_ports $pat* -quiet]
  if {[sizeof_collection $p] > 0} { set_input_delay $LINK_IN -clock clk $p }
}
foreach pat {flit_valid_o flit_o credit_valid_o credit_vc_o credit_tail_o} {
  set p [get_ports $pat* -quiet]
  if {[sizeof_collection $p] > 0} { set_output_delay $LINK_OUT -clock clk $p }
}

# The msg_hold controls are static configuration, written once at reset in any
# real use. Timing them against the clock would constrain a path that never
# toggles in anger.
foreach pat {hold_vn0_i hold_vn1_i hold_vn2_i hold_vn2_dir_i} {
  set p [get_ports $pat* -quiet]
  if {[sizeof_collection $p] > 0} { set_false_path -from $p }
}

set dbg_o [get_ports dbg_*_o -quiet]
if {[sizeof_collection $dbg_o] > 0} { set_false_path -to $dbg_o }
set dbg_i [get_ports dbg_*_i -quiet]
if {[sizeof_collection $dbg_i] > 0} { set_false_path -from $dbg_i }
