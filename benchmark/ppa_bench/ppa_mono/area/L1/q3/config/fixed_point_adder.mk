export PLATFORM               = asap7

export DESIGN_NAME            = fixed_point_adder

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

export DIE_AREA               = 0 0 50 50
export CORE_AREA              = 1.08 1.08 35 35

export PLACE_DENSITY          = 0.50


export ABC_AREA               = 0
export FLOW_VARIANT           = max_area
