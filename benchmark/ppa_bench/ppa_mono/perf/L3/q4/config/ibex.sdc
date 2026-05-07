set clk_name core_clock
set clk_port_name clk_i
set clk_period 810
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]

create_clock -name $clk_name -period $clk_period $clk_port

set non_clock_inputs [all_inputs -no_clocks]

set_false_path -to [all_outputs]
set_false_path -from [all_inputs -no_clocks]

