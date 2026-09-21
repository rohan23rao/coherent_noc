#==============================================================================
# l1_cache.sdc
#
# Three classes of port, three budgets.
#
#   core_*   the core is not in this design, so its timing is unknown and it
#            keeps the conservative 40% default from common.sdc. This is also
#            the honest place to note that a real integration would pipeline
#            this interface: core_req_ready_o is combinational from array_free,
#            and a core that has to see it in the same cycle is buying the
#            cache's critical path. Look for it in timing_reg2out.rpt -- if it
#            IS the critical path, that is not a surprise, it is the cost of
#            a non-blocking cache with a combinational accept becoming a number.
#
#   vn*_     the network interface is one msg_hold register away on the way out
#            and one reassembly register away on the way in, so both directions
#            are register-to-register.
#
#   dbg_*    observation only, with no consumer on any timing path. False-pathed
#            so it cannot distort the number that matters.
#==============================================================================

set NET_IN  [expr {$PERIOD * 0.25}]
set NET_OUT [expr {$PERIOD * 0.15}]

foreach pat {vn0_ vn1_ vn2_} {
  sdc_in_opt  "${pat}*_i" $NET_IN
  sdc_out_opt "${pat}*_o" $NET_OUT
}

# The L1 issues on VN0 and accepts on VN1 by definition, so if either of these
# stops matching, the port list changed and this file did not.
sdc_in  "vn1_valid_i" $NET_IN
sdc_out "vn0_valid_o" $NET_OUT

sdc_false_to_opt "dbg_*_o"
