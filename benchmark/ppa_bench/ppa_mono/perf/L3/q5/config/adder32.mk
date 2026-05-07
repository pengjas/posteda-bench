export PLATFORM               = asap7

export DESIGN_NAME            = adder32

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/adder32.sdc

export DIE_AREA               = 0 0 18 18
export CORE_AREA              = 1.08 1.08 16.92 16.92

export PLACE_DENSITY          = 0.40
