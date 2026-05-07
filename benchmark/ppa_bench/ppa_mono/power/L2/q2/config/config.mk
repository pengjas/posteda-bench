export PLATFORM               = asap7

export DESIGN_NAME            = sub_64bit

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

# Use CORE_UTILIZATION to let the tool size the die automatically
export CORE_UTILIZATION       = 20
export CORE_ASPECT_RATIO      = 1
export CORE_MARGIN            = 1

export PLACE_DENSITY          = 0.50

# Area optimization
export ABC_AREA               = 1
export ASAP7_USE_VT = LVT

# Fix all violating paths (may add more buffers, larger area)
export TNS_END_PERCENT        = 100
