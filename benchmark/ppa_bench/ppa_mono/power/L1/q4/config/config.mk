export PLATFORM               = asap7

export DESIGN_NAME            = multi_pipe_8bit

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

# Use CORE_UTILIZATION to let the tool size the die automatically
# Lower utilization = larger die area
export CORE_UTILIZATION       = 10
export CORE_ASPECT_RATIO      = 1
export CORE_MARGIN            = 1

export PLACE_DENSITY          = 0.10

# Area optimization
export ABC_AREA               = 1
