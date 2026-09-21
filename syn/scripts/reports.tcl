#==============================================================================
# syn/scripts/reports.tcl -- the report set, and only the report set.
#
# Kept separate from the flow so it can be re-sourced on a saved .ddc without
# re-running compile, and so the list of what is measured is reviewable in one
# short file.
#
# The five that matter, in the order to read them:
#   qor          one screen: worst slack, total negative slack, area, cells
#   timing       the critical path, with the cells on it
#   area         where the area went, by hierarchy
#   constraint   whether anything is unconstrained or violating
#   check_timing unclocked registers, missing input delays -- read this FIRST,
#                because a beautiful timing report on an unconstrained design
#                means nothing
#==============================================================================

report_qor                                  > $OUT/qor.rpt
report_timing -max_paths 10 -nworst 3 \
              -significant_digits 4 \
              -path_type full_clock_expanded > $OUT/timing.rpt
report_area -hierarchy                      > $OUT/area.rpt
report_constraint -all_violators            > $OUT/constraint.rpt
report_power -analysis_effort low           > $OUT/power.rpt
report_reference -hierarchy                 > $OUT/reference.rpt
report_clock_gating                         > $OUT/clock_gating.rpt

# The critical path split by start/end point class, which is what actually
# tells you whether the protocol logic or the network is the limiter.
report_timing -from [all_inputs]  -max_paths 3 > $OUT/timing_in2reg.rpt
report_timing -to   [all_outputs] -max_paths 3 > $OUT/timing_reg2out.rpt
report_timing -from [all_registers -clock_pins] \
              -to   [all_registers -data_pins] \
              -max_paths 5                     > $OUT/timing_reg2reg.rpt
