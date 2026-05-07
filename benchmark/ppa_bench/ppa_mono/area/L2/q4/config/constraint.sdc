current_design sub_64bit

set clk_name virtual_clock
set clk_period 500
set clk_io_pct 0.2

create_clock -name $clk_name -period $clk_period

set_input_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_inputs]
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
