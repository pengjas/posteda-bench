export PLATFORM               = asap7

export DESIGN_NICKNAME        = ibex
export DESIGN_NAME            = ibex_core

export VERILOG_FILES = $(sort $(wildcard $(DESIGN_HOME)/src/$(DESIGN_NICKNAME)/*.sv)) \
    $(DESIGN_HOME)/src/$(DESIGN_NICKNAME)/syn/rtl/prim_clock_gating.v

export VERILOG_INCLUDE_DIRS = \
    $(DESIGN_HOME)/src/$(DESIGN_NICKNAME)/vendor/lowrisc_ip/prim/rtl/

export SYNTH_HDL_FRONTEND = slang
export ASAP7_USE_VT = SLVT

ifeq ($(FLOW_VARIANT),pos_slack)
export SDC_FILE              = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NICKNAME)/constraint_pos_slack.sdc
else
export SDC_FILE              = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NICKNAME)/ibex.sdc
endif

export CORE_UTILIZATION       =  50
export CORE_ASPECT_RATIO      = 1
export CORE_MARGIN            = 2
export PLACE_DENSITY_LB_ADDON  = 0.20

export ENABLE_DPO = 0

export TNS_END_PERCENT        = 100
export FLOW_VARIANT             = best_config_v1

