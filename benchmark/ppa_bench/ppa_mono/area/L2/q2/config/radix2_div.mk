export PLATFORM               = asap7

export DESIGN_NAME            = radix2_div

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

export CORE_UTILIZATION       = 20
export CORE_ASPECT_RATIO      = 1
export CORE_MARGIN            = 1

export PLACE_DENSITY          = 0.1

export ABC_AREA               = 0

export FLOW_VARIANT           = class2_max
