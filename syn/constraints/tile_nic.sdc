#==============================================================================
# tile_nic.sdc
#
# Two different neighbours, two different budgets -- and conflating them is how
# a NIC ends up over-constrained on one side and under-constrained on the other.
#
#   * The router side is a link, exactly as in router.sdc: both ends registered.
#   * The cache and directory side arrives through msg_hold, which is a
#     pipeline register on this boundary. Decision D16 put it there for a test
#     hook, but the register is present at hold=0 too, so that side is also
#     register-to-register.
#
# In other words the NIC has no unknown neighbours at all. That is the point:
# every message path in this design crosses a register.
#==============================================================================

set REG2REG_IN  [expr {$PERIOD * 0.25}]
set REG2REG_OUT [expr {$PERIOD * 0.15}]

foreach pat {flit_valid_i flit_i credit_valid_i credit_vc_i credit_tail_i} {
  sdc_in $pat $REG2REG_IN
}
foreach pat {flit_valid_o flit_o credit_valid_o credit_vc_o credit_tail_o} {
  sdc_out $pat $REG2REG_OUT
}
foreach pat {l1_vn0_* l1_vn2_* dir_vn1_* dir_vn2_*} {
  sdc_in_opt  "${pat}_i" $REG2REG_IN
  sdc_out_opt "${pat}_o" $REG2REG_OUT
}
# The valid/msg/ready trio for each vnet, both directions, by suffix.
foreach pat {l1_vn1 l1_vn2 dir_vn0 dir_vn2} {
  sdc_out_opt "${pat}_valid_o" $REG2REG_OUT
  sdc_out_opt "${pat}_msg_o"   $REG2REG_OUT
  sdc_in_opt  "${pat}_ready_i" $REG2REG_IN
}
