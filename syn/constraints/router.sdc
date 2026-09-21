#==============================================================================
# router.sdc
#
# Every neighbour of a router is another router, and every one of this router's
# outputs is registered (stage ST writes an output register). So each link is a
# register-to-register path with a wire in between, not an unknown environment:
#
#   [upstream output reg] -> wire -> [this router's input] ...
#   ... [this router's output reg] -> wire -> [downstream input]
#
# The realistic split is therefore the upstream's clock-to-q plus the link wire
# on the way in, and the downstream flop's setup on the way out. 25% / 15% is
# deliberately a little pessimistic on the input side: the link wire in a 2x2
# mesh is short, but the clock-to-q of a 137-bit registered flit bus is not
# free.
#
# If this block misses timing, read timing_reg2reg.rpt first. The interesting
# path in a VC router is the allocator loop -- VA feeding SA feeding the
# crossbar select -- and it is entirely internal.
#
# Every override here is REQUIRED: these ports exist on a router by definition,
# so a pattern that matches nothing means the port list changed and this file
# did not.
#==============================================================================

set LINK_IN  [expr {$PERIOD * 0.25}]
set LINK_OUT [expr {$PERIOD * 0.15}]

foreach pat {flit_valid_i flit_i credit_valid_i credit_vc_i credit_tail_i} {
  sdc_in $pat $LINK_IN
}
foreach pat {flit_valid_o flit_o credit_valid_o credit_vc_o credit_tail_o} {
  sdc_out $pat $LINK_OUT
}
