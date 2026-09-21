#==============================================================================
# l1_cache.sdc
#
# Three classes of port, three budgets.
#
#   core_*    the core is not in this design, so its timing is unknown and gets
#             the conservative 40%. This is also the honest place to note that a
#             real integration would pipeline this interface: core_req_ready_o
#             is combinational from array_free, and a core that has to see it in
#             the same cycle is buying the cache's critical path.
#
#   vn*_      the network interface is one msg_hold register away on the way out
#             and one reassembly register away on the way in, so both directions
#             are register-to-register.
#
#   dbg_*     debug observation only. It has no consumer on the clock path and
#             is given a relaxed budget so it cannot become the critical path
#             and distort the number that matters.
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
if {[sizeof_collection $dbg_o] > 0} {
  set_output_delay [expr {$PERIOD * 0.05}] -clock clk $dbg_o
  set_false_path -to $dbg_o
  puts "SDC: dbg_* outputs false-pathed -- observation only, no consumer."
}
