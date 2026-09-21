#==============================================================================
# dir_ctrl.sdc
#
# Same shape as l1_cache.sdc. Two differences worth stating.
#
#   * The memory channel's other end is a memory controller that is not in this
#     design, so it keeps the conservative default rather than being given a
#     register-to-register budget it has not earned.
#   * The directory never sends on VN0 and never receives on VN1, so those
#     patterns are optional here and required in l1_cache.sdc. That asymmetry
#     is the protocol, not an oversight.
#==============================================================================

set NET_IN  [expr {$PERIOD * 0.25}]
set NET_OUT [expr {$PERIOD * 0.15}]

foreach pat {vn0_ vn1_ vn2_} {
  sdc_in_opt  "${pat}*_i" $NET_IN
  sdc_out_opt "${pat}*_o" $NET_OUT
}
sdc_in  "vn0_valid_i" $NET_IN
sdc_out "vn1_valid_o" $NET_OUT

sdc_false_to_opt "dbg_*_o"
