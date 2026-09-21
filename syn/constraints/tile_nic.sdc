#==============================================================================
# tile_nic.sdc
#
# Two different neighbours, two different budgets, and conflating them is how a
# NIC ends up over-constrained on one side and under-constrained on the other.
#
#   * The router side is a link, exactly as in router.sdc: both ends registered.
#   * The cache/directory side arrives through msg_hold, which is a pipeline
#     register on this boundary (decision D16 -- its hold count is a test hook,
#     but the register is there at hold=0 too). So that side is also
#     register-to-register, and is budgeted the same way.
#
# In other words the NIC has no unknown neighbours at all, which is the point:
# every message path in this design crosses a register.
#==============================================================================

set REG2REG_IN  [expr {$PERIOD * 0.25}]
set REG2REG_OUT [expr {$PERIOD * 0.15}]

set_input_delay  $REG2REG_IN  -clock clk \
  [remove_from_collection [all_inputs] [get_ports {clk rst_n} -quiet]]
set_output_delay $REG2REG_OUT -clock clk [all_outputs]
