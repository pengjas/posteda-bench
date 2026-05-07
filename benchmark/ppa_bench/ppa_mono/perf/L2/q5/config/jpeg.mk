export PLATFORM               = asap7

export DESIGN_NAME            = jpeg_encoder
export DESIGN_NICKNAME        = jpeg

export VERILOG_FILES          = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NICKNAME)/*.v))
export VERILOG_INCLUDE_DIRS   = $(DESIGN_HOME)/src/$(DESIGN_NICKNAME)/include
export SDC_FILE               = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NICKNAME)/jpeg.sdc
export ABC_AREA               = 1
export ASAP7_USE_VT 		 = SLVT

export CORE_UTILIZATION       = 70
export CORE_ASPECT_RATIO      = 1
export CORE_MARGIN            = 2
export PLACE_DENSITY          = 0.9

export TNS_END_PERCENT        = 50
export EQUIVALENCE_CHECK     ?=   0
export REMOVE_CELLS_FOR_EQY   = TAPCELL*
export FLOW_VARIANT             = place_density_v4_3

