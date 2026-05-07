set clk_name clk
set clk_port_name clk
set clk_period 295
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]

create_clock -name $clk_name -period $clk_period $clk_port
set_false_path -to [all_outputs]
set_false_path -from [all_inputs -no_clocks]


