#==============================================================================
# system_top.sdc
#
# The whole design. Every link is internal here, so the only ports carrying
# real traffic are the four core interfaces, which keep the conservative
# default. Expect this run to be slow and the area to be dominated by whatever
# SYN_SRAM is set to. It is worth doing once, to see the four tiles' critical
# paths side by side and confirm they are the same path -- the block runs are
# where the QoR conversation happens.
#==============================================================================

foreach pat {hold_vn0_i hold_vn1_i hold_vn2_i hold_vn2_dir_i} {
  sdc_false_from_opt $pat
}
sdc_false_to_opt   "dbg_*_o"
sdc_false_from_opt "dbg_*_i"

# cycle_count_o and rst_n_o are brought out for the testbench. Neither is part
# of the design's function, and neither may set the critical path.
sdc_false_to_opt "cycle_count_o"
sdc_false_to_opt "rst_n_o"
