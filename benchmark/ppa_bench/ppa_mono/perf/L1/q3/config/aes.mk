
export PLATFORM               = asap7

export DESIGN_NAME            = aes_cipher_top
export DESIGN_NICKNAME        = aes

export VERILOG_FILES = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NICKNAME)/*.v))
export SDC_FILE      = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NICKNAME)/aes.sdc

export ABC_AREA                 = 0
export ASAP7_USE_VT 		 = SLVT

export FLOW_VARIANT             = best_config
export CORE_UTILIZATION         = 50
export CORE_ASPECT_RATIO        = 1
export CORE_MARGIN              = 2
export PLACE_DENSITY            = 0.5
export TNS_END_PERCENT          = 100
export EQUIVALENCE_CHECK       ?=   0
export REMOVE_CELLS_FOR_EQY     = TAPCELL*
