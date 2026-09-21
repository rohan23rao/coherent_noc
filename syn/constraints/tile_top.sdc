#==============================================================================
# tile_top.sdc
#
# At tile level the only real neighbours are the four mesh links and the core.
# Everything else is internal, which is why this level is worth synthesising:
# it is the first one where L1 -> msg_hold -> NIC -> router is a real path
# rather than an assumption held in two separate SDC files.
#==============================================================================

set LINK_IN  [expr {$PERIOD * 0.25}]
set LINK_OUT [expr {$PERIOD * 0.15}]

foreach pat {flit_valid_i flit_i credit_valid_i credit_vc_i credit_tail_i} {
  sdc_in $pat $LINK_IN
}
foreach pat {flit_valid_o flit_o credit_valid_o credit_vc_o credit_tail_o} {
  sdc_out $pat $LINK_OUT
}

# The msg_hold controls are static configuration, written once at reset in any
# real use. Timing them would constrain a path that never toggles in anger.
foreach pat {hold_vn0_i hold_vn1_i hold_vn2_i hold_vn2_dir_i} {
  sdc_false_from_opt $pat
}

sdc_false_to_opt   "dbg_*_o"
sdc_false_from_opt "dbg_*_i"
