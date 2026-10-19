# DRC evaluation settings, read by eval_drc_sr.sh.
#
# Edit the values below, then run from this directory:
#   ./eval_drc_sr.sh                  # uses this file
#   ./eval_drc_sr.sh my_config.sh     # or a copy of it, e.g. one per experiment
#
# $REPO_ROOT is the root of this repository. Keep OPENAI_API_KEY in your shell,
# not in this file.

# --- Agent ---------------------------------------------------------------------
# The agent script to evaluate:
#   agents/drc/react/gemma_react.py                      ReAct              vLLM
#   agents/drc/react/vision_gemma_react.py               ReAct + images     vLLM (vision model)
#   agents/drc/reflexion/gemma_reflexion.py              Reflexion          vLLM
#   agents/drc/reflexion/gpt_reflexion.py                Reflexion          OpenAI
#   agents/drc/tot/gemma_tot.py                          Tree-of-Thoughts   vLLM
#   agents/drc/tot/gpt_tot.py                            Tree-of-Thoughts   OpenAI
#   agents/drc/proposer_critic/vllm_proposer_critic.py   Proposer-critic    vLLM
# Your own agent works too if it accepts --drc_rpt_path, --temperature and
# --iterations (DEPLOYMENT.md, section 4).
AGENT_SCRIPT="$REPO_ROOT/agents/drc/react/gemma_react.py"

# Extra command-line options for the agent; empty keeps the agent's defaults.
#   Reflexion:         AGENT_ARGS=(--num_attempts 2)
#   Tree-of-Thoughts:  AGENT_ARGS=(--parallel_node 5 --max_depth 16 --sample_temperature 0.9)
AGENT_ARGS=()

# --- Model ---------------------------------------------------------------------
# vLLM agents call this OpenAI-compatible endpoint with this model name, which
# must match the served name (scripts/serve_vllm.sh serves MODEL on port 9888).
VLLM_BASE_URL=http://localhost:9888/v1
VLLM_MODEL=google/gemma-4-31B-it
# OpenAI agents (gpt_*.py) use this model.
OPENAI_MODEL=gpt-5-mini

# --- Run -----------------------------------------------------------------------
TEMPERATURE=0
# Passed to the agent as --iterations: the step limit of a ReAct or
# proposer-critic attempt, split across the Reflexion trials, and the tool-call
# budget of the whole Tree-of-Thoughts search.
ITERATIONS=8
NUM_RUNS=5              # independent runs; the summary reports mean +/- std
MAX_PARALLEL_AGENTS=5   # attempts running at the same time

# Task groups to evaluate, relative to benchmark/drc_bench. The summary has a
# "Top-Level" section for each one. A single level such as drc_essential/L1
# works too.
TASK_GROUPS=(drc_essential drc_reasoning)

# Results directory. Attempts that already have results here are skipped, so an
# interrupted evaluation resumes where it stopped: use a new directory for each
# new agent or model.
RESULT_DIR="$REPO_ROOT/results/drc/react/v0"
