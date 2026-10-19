#!/usr/bin/env bash
# Serve a model with vLLM as the OpenAI-compatible endpoint the agents call.
#
# Usage: scripts/serve_vllm.sh [MODEL] [VLLM_SERVE_OPTIONS...]
#   scripts/serve_vllm.sh                                     # google/gemma-4-31B-it
#   scripts/serve_vllm.sh Qwen/Qwen3.5-27B --tensor-parallel-size 2
#
# The server listens on http://localhost:9888/v1 and serves MODEL under its own
# name; set VLLM_BASE_URL and VLLM_MODEL in eval/eval_drc_config.sh or
# eval/eval_ppa_config.sh to match. Extra options go to `vllm serve` and
# override the ones below, e.g. --port, --served-model-name, or --host 0.0.0.0
# when the agents run on another machine. Select GPUs with CUDA_VISIBLE_DEVICES.
# Install vLLM in its own environment, not in `posteda`.
set -euo pipefail

MODEL="${1:-google/gemma-4-31B-it}"

# The agents need tool calls and request thinking, so vLLM must parse both.
# For other model families pass --tool-call-parser (and --reasoning-parser).
case "${MODEL,,}" in
    *gemma-4*|*gemma4*)  PARSERS=(--tool-call-parser gemma4 --reasoning-parser gemma4) ;;
    *qwen3.5*|*qwen3_5*) PARSERS=(--tool-call-parser qwen3_coder --reasoning-parser qwen3) ;;
    *)                   PARSERS=() ;;
esac

# 64k context: orfs_agent's setup prompt alone is ~18k tokens, and the agents
# think before they answer.
exec vllm serve "$MODEL" --host 127.0.0.1 --port 9888 --max-model-len 65536 \
    --gpu-memory-utilization 0.90 --enable-auto-tool-choice "${PARSERS[@]}" "${@:2}"
