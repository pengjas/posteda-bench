
create_clock -name clk_virtual -period 500

set_input_delay -clock clk_virtual 0 [all_inputs]

set_output_delay -clock clk_virtual 0 [all_outputs]

set_driving_cell -lib_cell BUFx2_ASAP7_75t_R [all_inputs]

set_load 1 [all_outputs]
