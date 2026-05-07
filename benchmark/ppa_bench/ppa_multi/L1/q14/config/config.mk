export PLATFORM               = asap7

export DESIGN_NAME            = adder_pipe_64bit

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

# Die and core area - sized for 64-bit pipelined adder with registers
export DIE_AREA               = 0 0 25 25
export CORE_AREA              = 1.08 1.08 23.92 23.92

# Placement density - moderate density for good timing
export PLACE_DENSITY = 0.3

# ============================================================================
# Timing Optimization Variables
# ============================================================================

# Synthesis: Use speed optimization (not area)
export ABC_AREA = 1

# Placement: Enable timing-driven and routability-driven placement
export GPL_TIMING_DRIVEN      = 1
export GPL_ROUTABILITY_DRIVEN = 1

# Timing repair: Fix all violating endpoints
export TNS_END_PERCENT        = 100

# Enable all timing optimization transforms
export SKIP_GATE_CLONING      = 0
export SKIP_PIN_SWAP          = 0
export SKIP_LAST_GASP         = 0

# CTS and timing repair iterations
export SKIP_CTS_REPAIR_TIMING = 0

# Routing layer adjustment for better timing
export ROUTING_LAYER_ADJUSTMENT = 0.3

# Enable detailed placement optimization
export ENABLE_DPO             = 1
export ASAP7_USE_VT = RVT
export RECOVER_POWER = 0
