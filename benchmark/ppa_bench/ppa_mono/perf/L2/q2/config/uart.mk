export PLATFORM               = asap7
export CORNER                 = TC

export DESIGN_NAME            = uart

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NICKNAME)/*.v))
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NICKNAME)/uart.sdc
export SYNTH_BLACKBOXES = dummy

export PLACE_DENSITY          = 0.50
export DIE_AREA               = 0 0 17 17
export CORE_AREA              = 1.08 1.08 16 16
export TNS_END_PERCENT        = 100
export REMOVE_CELLS_FOR_EQY   = TAPCELL*
export SKIP_GATE_CLONING      = 1
export VERILOG_TOP_PARAMS     = DATA_WIDTH 8
export SYNTH_HDL_FRONTEND     = slang
export ASAP7_USE_VT = SLVT
export FLOW_VARIANT = skip_gate_cloning_v5