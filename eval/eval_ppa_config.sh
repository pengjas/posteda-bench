# PPA evaluation settings, read by eval_ppa_sr.sh.
#
# Edit the values below, then run from this directory:
#   ./eval_ppa_sr.sh                  # uses this file
#   ./eval_ppa_sr.sh my_config.sh     # or a copy of it, e.g. one per experiment
#
# $REPO_ROOT is the root of this repository. Keep OPENAI_API_KEY in your shell,
# not in this file.

# --- Agent ---------------------------------------------------------------------
# The agent script to evaluate:
#   agents/ppa/orfs_agent/orfs_agent.py                  LLM setup + GP search   vLLM or OpenAI
#   agents/ppa/react/gemma_react.py                      ReAct                   vLLM
#   agents/ppa/react/gpt_react.py                        ReAct                   OpenAI
#   agents/ppa/reflexion/gemma_reflexion.py              Reflexion               vLLM
#   agents/ppa/reflexion/gpt_reflexion.py                Reflexion               OpenAI
#   agents/ppa/tot/gemma_tot.py                          Tree-of-Thoughts        vLLM
#   agents/ppa/tot/gpt_tot.py                            Tree-of-Thoughts        OpenAI
#   agents/ppa/proposer_critic/vllm_proposer_critic.py   Proposer-critic         vLLM
# Your own agent works too if it accepts --prompt_path, --temperature and
# --iterations (DEPLOYMENT.md, section 4).
AGENT_SCRIPT="$REPO_ROOT/agents/ppa/orfs_agent/orfs_agent.py"

# Extra command-line options for the agent; empty keeps the agent's defaults.
#   orfs_agent:        AGENT_ARGS=(--llm_backend openai --outer_iterations 6 --parallel_runs 3)
#   Reflexion:         AGENT_ARGS=(--num_attempts 2)
#   Tree-of-Thoughts:  AGENT_ARGS=(--parallel_node 5 --max_depth 16 --sample_temperature 0.9)
AGENT_ARGS=()

# --- Model ---------------------------------------------------------------------
# vLLM agents call this OpenAI-compatible endpoint with this model name, which
# must match the served name (scripts/serve_vllm.sh serves MODEL on port 9888).
VLLM_BASE_URL=http://localhost:9888/v1
VLLM_MODEL=qwen3.5-9b
# OpenAI agents (gpt_*.py, and orfs_agent with --llm_backend openai) use this model.
OPENAI_MODEL=gpt-5

# --- Run -----------------------------------------------------------------------
TEMPERATURE=0
# Passed to the agent as --iterations, with the same meaning as in
# eval_drc_config.sh. orfs_agent ignores it: it runs outer_iterations x
# parallel_runs OpenROAD flows (6 x 3 by default).
ITERATIONS=16
TOOL_TIMEOUT=1800       # seconds allowed for one OpenROAD flow
NUM_RUNS=5              # independent runs; the summary reports mean +/- std
MAX_PARALLEL_AGENTS=2   # attempts running at the same time; each runs OpenROAD

# Task groups to evaluate, relative to benchmark/ppa_bench. The summary has a
# "Top-Level" section for each one, plus a single-dimension section that
# combines ppa_mono/area, ppa_mono/power and ppa_mono/perf, so list those three
# rather than ppa_mono. A single level such as ppa_multi/L1 works too.
TASK_GROUPS=(ppa_multi ppa_mono/area ppa_mono/power ppa_mono/perf)

# Results directory. Attempts that already have results here are skipped, so an
# interrupted evaluation resumes where it stopped: use a new directory for each
# new agent or model.
RESULT_DIR="$REPO_ROOT/results/ppa/orfs_agent/v0"

# 1: run no agents, only rebuild the summary from the results in RESULT_DIR
# (e.g. after adding a metric). Attempts without cost.json or
# trajectory_stats.json get them rebuilt from their agent_run.log.
REAGGREGATE_ONLY=0

# --- OpenROAD toolchain (DEPLOYMENT.md, section 1) -----------------------------
# The ORFS flow directory and the Docker image its util/docker_shell runs. Both
# default to what your shell exports. Without OR_IMAGE, ORFS uses
# openroad/orfs:latest, which does not reproduce the reference results; the
# reference image is openroad/orfs:v3.0-4639-ga2bb042b6.
OPENROAD_FLOW_DIR="${OPENROAD_FLOW_DIR:-$HOME/OpenROAD-flow-scripts/flow}"
OR_IMAGE="${OR_IMAGE:-}"
