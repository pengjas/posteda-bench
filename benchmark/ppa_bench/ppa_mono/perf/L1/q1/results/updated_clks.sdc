###############################################################################
# Created by write_sdc
###############################################################################
current_design gcd
###############################################################################
# Timing Constraints
###############################################################################
create_clock -name core_clock -period 263.7876 [get_ports {clk}]
set_false_path\
    -from [list [get_ports {req_msg[0]}]\
           [get_ports {req_msg[10]}]\
           [get_ports {req_msg[11]}]\
           [get_ports {req_msg[12]}]\
           [get_ports {req_msg[13]}]\
           [get_ports {req_msg[14]}]\
           [get_ports {req_msg[15]}]\
           [get_ports {req_msg[16]}]\
           [get_ports {req_msg[17]}]\
           [get_ports {req_msg[18]}]\
           [get_ports {req_msg[19]}]\
           [get_ports {req_msg[1]}]\
           [get_ports {req_msg[20]}]\
           [get_ports {req_msg[21]}]\
           [get_ports {req_msg[22]}]\
           [get_ports {req_msg[23]}]\
           [get_ports {req_msg[24]}]\
           [get_ports {req_msg[25]}]\
           [get_ports {req_msg[26]}]\
           [get_ports {req_msg[27]}]\
           [get_ports {req_msg[28]}]\
           [get_ports {req_msg[29]}]\
           [get_ports {req_msg[2]}]\
           [get_ports {req_msg[30]}]\
           [get_ports {req_msg[31]}]\
           [get_ports {req_msg[3]}]\
           [get_ports {req_msg[4]}]\
           [get_ports {req_msg[5]}]\
           [get_ports {req_msg[6]}]\
           [get_ports {req_msg[7]}]\
           [get_ports {req_msg[8]}]\
           [get_ports {req_msg[9]}]\
           [get_ports {req_val}]\
           [get_ports {reset}]\
           [get_ports {resp_rdy}]]
set_false_path\
    -to [list [get_ports {req_rdy}]\
           [get_ports {resp_msg[0]}]\
           [get_ports {resp_msg[10]}]\
           [get_ports {resp_msg[11]}]\
           [get_ports {resp_msg[12]}]\
           [get_ports {resp_msg[13]}]\
           [get_ports {resp_msg[14]}]\
           [get_ports {resp_msg[15]}]\
           [get_ports {resp_msg[1]}]\
           [get_ports {resp_msg[2]}]\
           [get_ports {resp_msg[3]}]\
           [get_ports {resp_msg[4]}]\
           [get_ports {resp_msg[5]}]\
           [get_ports {resp_msg[6]}]\
           [get_ports {resp_msg[7]}]\
           [get_ports {resp_msg[8]}]\
           [get_ports {resp_msg[9]}]\
           [get_ports {resp_val}]]
###############################################################################
# Environment
###############################################################################
###############################################################################
# Design Rules
###############################################################################
