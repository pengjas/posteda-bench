export PLATFORM               = asap7

export DESIGN_NAME            = float_multi

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

export CORE_UTILIZATION       = 50
export CORE_ASPECT_RATIO      = 1
export CORE_MARGIN            = 1

export PLACE_DENSITY          = 0.40

export ABC_AREA               = 1

export SKIP_GATE_CLONING      = 0
export TNS_END_PERCENT = 100
export FLOW_VARIANT           = max_area_v1
