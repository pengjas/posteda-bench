create_clock -name core_clock -period 210 [get_ports clk]
set_false_path -to [all_outputs]
set_false_path -from [all_inputs -no_clocks]