###############################################################################
# Created by write_sdc
###############################################################################
current_design radix2_div
###############################################################################
# Timing Constraints
###############################################################################
create_clock -name core_clock -period 500.0000 [get_ports {clk}]
set_propagated_clock [get_clocks {core_clock}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {dividend[0]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {dividend[1]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {dividend[2]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {dividend[3]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {dividend[4]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {dividend[5]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {dividend[6]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {dividend[7]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {divisor[0]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {divisor[1]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {divisor[2]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {divisor[3]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {divisor[4]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {divisor[5]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {divisor[6]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {divisor[7]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {opn_valid}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {res_ready}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {rst}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {sign}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {res_valid}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[0]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[10]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[11]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[12]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[13]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[14]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[15]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[1]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[2]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[3]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[4]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[5]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[6]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[7]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[8]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {result[9]}]
###############################################################################
# Environment
###############################################################################
###############################################################################
# Design Rules
###############################################################################
