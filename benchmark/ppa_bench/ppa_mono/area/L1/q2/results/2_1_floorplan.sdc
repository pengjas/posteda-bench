###############################################################################
# Created by write_sdc
###############################################################################
current_design multi_pipe_8bit
###############################################################################
# Timing Constraints
###############################################################################
create_clock -name core_clock -period 500.0000 [get_ports {clk}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_a[0]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_a[1]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_a[2]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_a[3]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_a[4]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_a[5]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_a[6]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_a[7]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_b[0]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_b[1]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_b[2]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_b[3]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_b[4]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_b[5]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_b[6]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_b[7]}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_en_in}]
set_input_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {rst_n}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_en_out}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[0]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[10]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[11]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[12]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[13]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[14]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[15]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[1]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[2]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[3]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[4]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[5]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[6]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[7]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[8]}]
set_output_delay 100.0000 -clock [get_clocks {core_clock}] -add_delay [get_ports {mul_out[9]}]
###############################################################################
# Environment
###############################################################################
###############################################################################
# Design Rules
###############################################################################
