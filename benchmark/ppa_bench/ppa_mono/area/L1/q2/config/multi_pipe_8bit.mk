export PLATFORM               = asap7

export DESIGN_NAME            = multi_pipe_8bit

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

export CORE_UTILIZATION       = 10
export CORE_ASPECT_RATIO      = 1
export CORE_MARGIN            = 1

export PLACE_DENSITY          = 0.10

export ABC_AREA               = 1
export FLOW_VARIANT           = max_area
