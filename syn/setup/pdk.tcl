#==============================================================================
# syn/setup/pdk.tcl -- resolve one of three PDKs into Design Compiler variables.
#
# No path in this repository points at anyone's filesystem. Each PDK is located
# through one environment variable, and if that variable is not set the script
# says which one and stops. That is deliberate: a synthesis script with a
# hard-coded /home/someone/pdk in it is a script that runs on exactly one
# machine, and this one has to run on a machine the author cannot log into.
#
# Set exactly one of:
#   SAED32_ROOT   Synopsys 32/28nm educational kit
#   ASAP7_ROOT    ASAP7 7nm predictive PDK
#   SKY130_ROOT   SkyWater 130nm open PDK (sky130_fd_sc_hd)
#
# and pass SYN_PDK=saed32|asap7|sky130.
#==============================================================================

proc env_or_die {name why} {
  global env
  if {![info exists env($name)]} {
    error "\n\n  \$$name is not set.\n  $why\n"
  }
  return $env($name)
}

proc first_glob {desc args} {
  foreach pat $args {
    set hits [glob -nocomplain $pat]
    if {[llength $hits] > 0} { return [lsort $hits] }
  }
  error "\n\n  Could not find $desc.\n  Tried:\n    [join $args "\n    "]\n\
         \n  Edit syn/setup/pdk.tcl if your kit is laid out differently --\
         that is the only file that should need to change.\n"
}

set PDK [expr {[info exists env(SYN_PDK)] ? $env(SYN_PDK) : "saed32"}]

switch -- $PDK {
  saed32 {
    set root [env_or_die SAED32_ROOT \
      "Point it at the SAED32 kit, the directory containing lib/stdcell_*/."]
    # Typical layout: $root/lib/stdcell_rvt/db_nldm/saed32rvt_ss0p75v125c.db
    set libs [first_glob "a SAED32 slow-corner .db" \
      "$root/lib/stdcell_rvt/db_nldm/saed32rvt_ss*.db" \
      "$root/lib/stdcell_rvt/db_ccs/saed32rvt_ss*.db" \
      "$root/stdcell_rvt/db_nldm/saed32rvt_ss*.db"]
    set target_library [list [lindex $libs 0]]
    # 32nm educational: a sensible default period for a control block.
    set ::SYN_DEFAULT_PERIOD 2.0
    set ::SYN_DRIVING_CELL   "INVX2_RVT"
    set ::SYN_DRIVING_PIN    "Y"
    set ::SYN_LOAD_CELL      "INVX1_RVT"
    set ::SYN_LOAD_PIN       "A"
  }
  asap7 {
    set root [env_or_die ASAP7_ROOT \
      "Point it at the ASAP7 PDK root, the directory containing asap7sc7p5t_*/."]
    set libs [first_glob "an ASAP7 7.5-track .db" \
      "$root/asap7sc7p5t_*/LIB/NLDM/*SS*.db" \
      "$root/lib/asap7sc7p5t_*SS*.db"]
    set target_library $libs
    set ::SYN_DEFAULT_PERIOD 0.5
    set ::SYN_DRIVING_CELL   "INVx2_ASAP7_75t_R"
    set ::SYN_DRIVING_PIN    "Y"
    set ::SYN_LOAD_CELL      "INVx1_ASAP7_75t_R"
    set ::SYN_LOAD_PIN       "A"
  }
  sky130 {
    set root [env_or_die SKY130_ROOT \
      "Point it at the sky130A PDK root, or at the directory holding\
       sky130_fd_sc_hd .db files. ASCII .lib must be compiled to .db with\
       Library Compiler first -- see syn/README.md."]
    set libs [first_glob "a sky130_fd_sc_hd slow-corner .db" \
      "$root/**/sky130_fd_sc_hd__ss_*.db" \
      "$root/sky130_fd_sc_hd__ss_*.db"]
    set target_library [list [lindex $libs 0]]
    set ::SYN_DEFAULT_PERIOD 10.0
    set ::SYN_DRIVING_CELL   "sky130_fd_sc_hd__inv_2"
    set ::SYN_DRIVING_PIN    "Y"
    set ::SYN_LOAD_CELL      "sky130_fd_sc_hd__inv_1"
    set ::SYN_LOAD_PIN       "A"
  }
  default {
    error "SYN_PDK=$PDK is not one of: saed32, asap7, sky130"
  }
}

set link_library   [concat "*" $target_library]
set symbol_library {}

puts "PDK        : $PDK"
puts "target_lib : $target_library"
