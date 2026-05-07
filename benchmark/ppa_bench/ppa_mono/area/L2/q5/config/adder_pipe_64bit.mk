export PLATFORM               = asap7

export DESIGN_NAME            = adder_pipe_64bit

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/best_config.sdc

export DIE_AREA               = 0 0 25 25
export CORE_AREA              = 1.08 1.08 24 24

export PLACE_DENSITY          = 0.50


export ABC_AREA               = 1

export GPL_TIMING_DRIVEN      = 0
export GPL_ROUTABILITY_DRIVEN = 0

export TNS_END_PERCENT        = 50

export SKIP_GATE_CLONING      = 1
export SKIP_PIN_SWAP          = 1
export SKIP_LAST_GASP         = 1

export SKIP_CTS_REPAIR_TIMING = 1

export ROUTING_LAYER_ADJUSTMENT = 0.3

export ENABLE_DPO             = 1

export FLOW_VARIANT           = max_area
export ASAP7_USE_VT 		 = SLVT
