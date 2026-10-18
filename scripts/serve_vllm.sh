#!/usr/bin/env bash
# Serve a local model with vLLM as the OpenAI-compatible endpoint the agents call.
#
# Usage:
#   scripts/serve_vllm.sh [MODEL]            # default: google/gemma-4-31B-it
#
# Environment overrides:
#   PORT=9888                  agents default to http://localhost:9888/v1
#   HOST=127.0.0.1             use 0.0.0.0 if the agents run on another machine
#   MAX_MODEL_LEN=65536        orfs_agent's setup prompt alone is ~18k tokens and
#                              the agents run with thinking enabled
#   TP=1                       tensor-parallel size (number of GPUs)
#   GPU_MEMORY_UTILIZATION=0.90
#   SERVED_MODEL_NAME=MODEL    the name agents must send (VLLM_MODEL)
#   TOOL_CALL_PARSER / REASONING_PARSER
#                              override the parsers chosen from the model name
#   DRY_RUN=1                  print the vllm command instead of running it
# Select GPUs with CUDA_VISIBLE_DEVICES. Install vLLM in its own environment
# (it pins its own PyTorch/CUDA stack), not in the `posteda` environment.
set -euo pipefail

MODEL="${1:-google/gemma-4-31B-it}"
PORT="${PORT:-9888}"
HOST="${HOST:-127.0.0.1}"
MAX_MODEL_LEN="${MAX_MODEL_LEN:-65536}"
TP="${TP:-1}"
GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-$MODEL}"

# The agents need structured tool calls and request thinking
# (chat_template_kwargs.enable_thinking), so both parsers matter.
case "${MODEL,,}" in
    *gemma-4*|*gemma4*)
        TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-gemma4}"
        REASONING_PARSER="${REASONING_PARSER:-gemma4}"
        ;;
    *qwen3.5*|*qwen3_5*)
        TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-qwen3_coder}"
        REASONING_PARSER="${REASONING_PARSER:-qwen3}"
        ;;
esac
if [ -z "${TOOL_CALL_PARSER:-}" ]; then
    echo "error: no default tool-call parser for '$MODEL'." >&2
    echo "Set TOOL_CALL_PARSER (and REASONING_PARSER for a thinking model); see 'vllm serve --help=all'." >&2
    exit 2
fi

cmd=(vllm serve "$MODEL"
     --host "$HOST" --port "$PORT"
     --served-model-name "$SERVED_MODEL_NAME"
     --max-model-len "$MAX_MODEL_LEN"
     --tensor-parallel-size "$TP"
     --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
     --enable-auto-tool-choice --tool-call-parser "$TOOL_CALL_PARSER")
if [ -n "${REASONING_PARSER:-}" ]; then
    cmd+=(--reasoning-parser "$REASONING_PARSER")
fi

echo "Point the agents at this server:" >&2
echo "  export VLLM_BASE_URL=http://localhost:$PORT/v1 VLLM_MODEL=$SERVED_MODEL_NAME" >&2
if [ "${DRY_RUN:-0}" = 1 ]; then
    printf '%q ' "${cmd[@]}"
    echo
    exit 0
fi
exec "${cmd[@]}"
