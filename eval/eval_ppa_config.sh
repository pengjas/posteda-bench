# PPA evaluation settings, read by eval_ppa_sr.sh.
#
# Edit the values below, then run from this directory:
#   ./eval_ppa_sr.sh                  # uses this file
#   ./eval_ppa_sr.sh my_config.sh     # or a copy of it, e.g. one per experiment
#
# OpenAI agents read their API key from OPENAI_API_KEY in your shell; keep the
# key out of this file.

# Your posteda-bench checkout. Empty: the checkout that holds eval_ppa_sr.sh.
# Relative paths below (AGENT_SCRIPT, RESULT_DIR) start from here.
REPO_ROOT=

# --- OpenROAD toolchain (DEPLOYMENT.md, section 1) -----------------------------
# Your ORFS checkout's flow directory, and the Docker image its
# util/docker_shell runs. Other images give different PPA numbers.
OPENROAD_FLOW_DIR=$HOME/OpenROAD-flow-scripts/flow
OR_IMAGE=openroad/orfs:v3.0-4639-ga2bb042b6

# --- Agent ---------------------------------------------------------------------
# The agent script to evaluate:
#   agents/ppa/orfs_agent/orfs_agent.py                  LLM setup + GP search   vLLM or OpenAI
#   agents/ppa/react/vllm_react.py                       ReAct                   vLLM
#   agents/ppa/react/gpt_react.py                        ReAct                   OpenAI
#   agents/ppa/reflexion/vllm_reflexion.py               Reflexion               vLLM
#   agents/ppa/reflexion/gpt_reflexion.py                Reflexion               OpenAI
#   agents/ppa/tot/vllm_tot.py                           Tree-of-Thoughts        vLLM
#   agents/ppa/tot/gpt_tot.py                            Tree-of-Thoughts        OpenAI
#   agents/ppa/proposer_critic/vllm_proposer_critic.py   Proposer-critic         vLLM
# Your own agent works too if it accepts --prompt_path, --temperature and
# --iterations (DEPLOYMENT.md, section 4).
AGENT_SCRIPT=agents/ppa/orfs_agent/orfs_agent.py

# orfs_agent.py only. It asks the LLM once which flow parameters to tune, then
# searches them with a Gaussian process: ORFS_OUTER_ITERATIONS rounds of
# ORFS_PARALLEL_RUNS OpenROAD flows (18 flows by default), stopping early once
# every target is met. It ignores ITERATIONS below.
ORFS_LLM_BACKEND=vllm      # vllm: VLLM_MODEL at VLLM_BASE_URL; openai: OPENAI_MODEL
ORFS_OUTER_ITERATIONS=6
ORFS_PARALLEL_RUNS=3       # flows running at the same time within one attempt

# Extra command-line options for the other agents; empty keeps their defaults.
#   Reflexion:         AGENT_ARGS=(--num_attempts 2)
#   Tree-of-Thoughts:  AGENT_ARGS=(--parallel_node 5 --max_depth 16 --sample_temperature 0.9)
AGENT_ARGS=()

# --- Model ---------------------------------------------------------------------
# vLLM agents call this OpenAI-compatible endpoint with this model name, which
# must match the served name; scripts/serve_vllm.sh prints both when it starts.
VLLM_BASE_URL=http://localhost:9888/v1
VLLM_MODEL=qwen3.5-9b
# OpenAI agents (gpt_*.py, and orfs_agent with ORFS_LLM_BACKEND=openai) use this model.
OPENAI_MODEL=gpt-5

# --- Run -----------------------------------------------------------------------
TEMPERATURE=0
# Passed to the agent as --iterations, with the same meaning as in
# eval_drc_config.sh (orfs_agent ignores it).
ITERATIONS=16
TOOL_TIMEOUT=1800       # seconds allowed for one OpenROAD flow
NUM_RUNS=5              # independent runs; the summary reports mean +/- std
# Attempts running at the same time. Each one runs OpenROAD: with orfs_agent,
# MAX_PARALLEL_AGENTS x ORFS_PARALLEL_RUNS flows run at once.
MAX_PARALLEL_AGENTS=2

# Task groups to evaluate, relative to benchmark/ppa_bench. The summary has a
# "Top-Level" section for each one, plus a single-dimension section that
# combines ppa_mono/area, ppa_mono/power and ppa_mono/perf, so list those three
# rather than ppa_mono. A single level such as ppa_multi/L1 works too.
TASK_GROUPS=(ppa_multi ppa_mono/area ppa_mono/power ppa_mono/perf)

# Results directory. Attempts that already have results here are skipped, so an
# interrupted evaluation resumes where it stopped: use a new directory for each
# new agent or model.
RESULT_DIR=results/ppa/orfs_agent/v0

# 1: run no agents, only rebuild the summary from the results in RESULT_DIR
# (e.g. after adding a metric). Attempts without cost.json or
# trajectory_stats.json get them rebuilt from their agent_run.log.
REAGGREGATE_ONLY=0
