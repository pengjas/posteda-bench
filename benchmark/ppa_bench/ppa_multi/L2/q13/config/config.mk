export PLATFORM               = asap7

export DESIGN_NAME            = float_multi

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

# Use CORE_UTILIZATION to let the tool size the die automatically
export CORE_UTILIZATION = 20
export CORE_ASPECT_RATIO      = 1
export CORE_MARGIN            = 1

export PLACE_DENSITY = 0.3

# Area optimization
export ABC_AREA = 1

# Allow gate cloning for timing (may increase area)
export SKIP_GATE_CLONING = 0
export ASAP7_USE_VT = RVT
export GPL_ROUTABILITY_DRIVEN = 0
