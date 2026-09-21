#==============================================================================
# syn/setup/pdk.tcl -- resolve one of three PDKs into Design Compiler variables.
#
# No path in this repository points at anyone's filesystem. Each PDK is located
# through environment variables, and if none of them resolves, the script says
# which ones it tried and stops. That is deliberate: a synthesis script with a
# hard-coded /home/someone/pdk in it runs on exactly one machine, and this one
# has to run on a machine the author cannot log into.
#
# Set one of these roots:
#   SAED32_ROOT   Synopsys 32/28nm educational kit
#   ASAP7_ROOT    ASAP7 7nm predictive PDK
#   SKY130_ROOT   SkyWater 130nm open PDK (sky130_fd_sc_hd)
#
# and pass SYN_PDK=saed32|asap7|sky130.
#
# Overrides, for when the search does not match your kit's layout:
#   SYN_DB            an explicit .db (or several); skips the search entirely
#   SYN_VT            saed32 only: lvt | rvt | hvt          (default lvt)
#   SYN_CORNER        saed32 only: tt | ss                  (default tt)
#   SYN_DRIVE_CELL    library cell for set_driving_cell
#   SYN_WLM           wire load model name, e.g. 16000; empty disables it
#
# The saed32 defaults are LVT at the typical corner with a NAND2X2_LVT driver,
# because that is the combination the UW-Madison course kits ship and use. It
# is also the OPTIMISTIC choice: typical-corner numbers are what a course flow
# reports, not what sign-off would. Set SYN_CORNER=ss for the pessimistic one,
# and expect the frequency to drop substantially.
#==============================================================================

proc env_or {name default} {
  global env
  return [expr {[info exists env($name)] ? $env($name) : $default}]
}

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
         \n  Either set \$SYN_DB to the .db you want, or edit\
         syn/setup/pdk.tcl -- it is the only file that should need to\
         change for a differently laid out kit.\n"
}

set PDK [env_or SYN_PDK "saed32"]

# An explicit .db wins over everything. This is the escape hatch for a kit
# whose directory layout the globs below do not predict.
set explicit [env_or SYN_DB ""]

switch -- $PDK {
  saed32 {
    set vt     [string tolower [env_or SYN_VT "lvt"]]
    set corner [string tolower [env_or SYN_CORNER "tt"]]
    if {$explicit ne ""} {
      set libs $explicit
    } else {
      set root [env_or_die SAED32_ROOT \
        "Point it at the SAED32 kit -- the directory containing lib/stdcell_*/."]
      set libs [first_glob "a SAED32 $vt $corner .db" \
        "$root/lib/stdcell_$vt/db_nldm/saed32${vt}_${corner}*.db" \
        "$root/lib/stdcell_$vt/db_ccs/saed32${vt}_${corner}*.db" \
        "$root/stdcell_$vt/db_nldm/saed32${vt}_${corner}*.db" \
        "$root/**/saed32${vt}_${corner}*.db"]
    }
    set target_library [list [lindex $libs 0]]
    set ::SYN_DEFAULT_PERIOD 2.0
    set ::SYN_DRIVING_CELL [env_or SYN_DRIVE_CELL \
                              "NAND2X2_[string toupper $vt]"]
    set ::SYN_DRIVING_PIN  "Y"
    set ::SYN_LOAD_PF      0.1
    # Pre-layout at 32nm with no wire load model reports interconnect as zero,
    # which flatters every path. 16000 is the model the course flows use.
    set ::SYN_WLM [env_or SYN_WLM "16000"]
  }
  asap7 {
    if {$explicit ne ""} {
      set libs $explicit
    } else {
      set root [env_or_die ASAP7_ROOT \
        "Point it at the ASAP7 PDK root -- the directory containing asap7sc7p5t_*/."]
      set libs [first_glob "an ASAP7 7.5-track .db" \
        "$root/asap7sc7p5t_*/LIB/NLDM/*SS*.db" \
        "$root/lib/asap7sc7p5t_*SS*.db" \
        "$root/**/asap7sc7p5t_*.db"]
    }
    set target_library $libs
    set ::SYN_DEFAULT_PERIOD 0.5
    set ::SYN_DRIVING_CELL [env_or SYN_DRIVE_CELL "INVx2_ASAP7_75t_R"]
    set ::SYN_DRIVING_PIN  "Y"
    set ::SYN_LOAD_PF      0.001
    set ::SYN_WLM [env_or SYN_WLM ""]
  }
  sky130 {
    if {$explicit ne ""} {
      set libs $explicit
    } else {
      set root [env_or_die SKY130_ROOT \
        "Point it at the directory holding sky130_fd_sc_hd .db files. ASCII\
         .lib must be compiled to .db with Library Compiler first -- see\
         syn/README.md."]
      set libs [first_glob "a sky130_fd_sc_hd .db" \
        "$root/**/sky130_fd_sc_hd__ss_*.db" \
        "$root/sky130_fd_sc_hd__ss_*.db" \
        "$root/**/sky130_fd_sc_hd__*.db"]
    }
    set target_library [list [lindex $libs 0]]
    set ::SYN_DEFAULT_PERIOD 10.0
    set ::SYN_DRIVING_CELL [env_or SYN_DRIVE_CELL "sky130_fd_sc_hd__inv_2"]
    set ::SYN_DRIVING_PIN  "Y"
    set ::SYN_LOAD_PF      0.05
    set ::SYN_WLM [env_or SYN_WLM ""]
  }
  default {
    error "SYN_PDK=$PDK is not one of: saed32, asap7, sky130"
  }
}

set link_library   [concat "*" $target_library]
set symbol_library {}

# The library's own name, which set_wire_load_model needs and which is not
# always the file name.
set ::SYN_LIB_NAME [file rootname [file tail [lindex $target_library 0]]]

puts "PDK        : $PDK"
puts "target_lib : $target_library"
puts "driver     : $::SYN_DRIVING_CELL"
puts "wire load  : [expr {$::SYN_WLM eq "" ? "(none)" : $::SYN_WLM}]"
