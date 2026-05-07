export PLATFORM               = asap7

export DESIGN_NAME            = adder_pipe_64bit

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/constraint.sdc

export DIE_AREA               = 0 0 25 25
export CORE_AREA              = 1.08 1.08 23.92 23.92

export PLACE_DENSITY          = 0.50

export FLOW_VARIANT           = max_power


export RECOVER_POWER          = 0

export ABC_AREA               = 0

export SKIP_VT_SWAP           = 1
export SKIP_CRIT_VT_SWAP      = 1

export ASAP7_USE_VT           = SLVT

export GPL_TIMING_DRIVEN      = 1
export GPL_ROUTABILITY_DRIVEN = 1

export TNS_END_PERCENT        = 100

export SKIP_GATE_CLONING      = 0
export SKIP_PIN_SWAP          = 0
export SKIP_LAST_GASP         = 0

export SKIP_CTS_REPAIR_TIMING = 0

export ROUTING_LAYER_ADJUSTMENT = 0.3

export ENABLE_DPO             = 1
