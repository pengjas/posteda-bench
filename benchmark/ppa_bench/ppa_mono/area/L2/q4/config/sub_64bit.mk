export PLATFORM               = asap7

export DESIGN_NAME            = sub_64bit

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

export CORE_UTILIZATION       = 20
export CORE_ASPECT_RATIO      = 1
export CORE_MARGIN            = 1

export PLACE_DENSITY          = 0.40

export ABC_AREA               = 0

export TNS_END_PERCENT        = 5
export FLOW_VARIANT           = min_area
