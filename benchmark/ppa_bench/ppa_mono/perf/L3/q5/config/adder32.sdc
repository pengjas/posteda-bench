current_design adder32

set clk_name  virtual_clk
set clk_period 500

create_clock -name $clk_name -period $clk_period

set clk_io_pct 0.2

set_input_delay  [expr $clk_period * $clk_io_pct] -clock $clk_name [all_inputs]
set_output_delay [expr $clk_period * $clk_io_pct] -clock $clk_name [all_outputs]
