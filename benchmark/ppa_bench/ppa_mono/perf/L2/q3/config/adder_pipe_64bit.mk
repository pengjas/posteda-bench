export PLATFORM               = asap7

export DESIGN_NAME            = adder_pipe_64bit

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NAME)/adder_pipe_64bit.sdc

export DIE_AREA               = 0 0 25 25
export CORE_AREA              = 1.08 1.08 23.92 23.92

export PLACE_DENSITY          = 0.1


export ABC_AREA               = 1

export GPL_TIMING_DRIVEN      = 0
export GPL_ROUTABILITY_DRIVEN = 0

export TNS_END_PERCENT        = 10

export SKIP_GATE_CLONING      = 1
export SKIP_PIN_SWAP          = 1
export SKIP_LAST_GASP         = 1

export SKIP_CTS_REPAIR_TIMING = 1

export ROUTING_LAYER_ADJUSTMENT = 0.3

export ENABLE_DPO             = 0

export FLOW_VARIANT           = class2
export ASAP7_USE_VT 		 = SLVT
