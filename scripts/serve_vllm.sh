#!/usr/bin/env bash
# Serve a model with vLLM as the OpenAI-compatible endpoint the agents call.
#
# Edit the settings below, then run:   scripts/serve_vllm.sh
# On start it prints the VLLM_BASE_URL and VLLM_MODEL to set in
# eval/eval_drc_config.sh and eval/eval_ppa_config.sh. Install vLLM in its own
# environment, not in `posteda`.
set -euo pipefail

# ================================= Settings =================================
# A Hugging Face model name, or the path of a local checkpoint directory (the
# folder that holds config.json and the weights).
MODEL=google/gemma-4-31B-it
# The name agents request (VLLM_MODEL in the eval configs). Empty: MODEL itself.
# Worth setting for a local path, e.g. SERVED_MODEL_NAME=gemma-4-31b.
SERVED_MODEL_NAME=
# Where Hugging Face models are downloaded and looked up, e.g. a shared model
# directory. Empty: Hugging Face's default cache.
HF_HUB_CACHE=
# GPU ids, comma-separated (e.g. 0,1); the model is split across all of them.
GPUS=0
PORT=9888
# 127.0.0.1 accepts local agents only; 0.0.0.0 also agents on other machines.
HOST=127.0.0.1
# orfs_agent's setup prompt alone is ~18k tokens, and the agents think before
# they answer.
MAX_MODEL_LEN=65536
GPU_MEMORY_UTILIZATION=0.90
# The agents need tool calls and request thinking, so vLLM must parse both.
# Empty: chosen from the model for Gemma 4 (gemma4, gemma4) and Qwen3.5
# (qwen3_coder, qwen3). Set them for other model families.
TOOL_CALL_PARSER=
REASONING_PARSER=
# The vllm command. A full path such as ~/miniconda3/envs/vllm/bin/vllm works
# without activating that environment.
VLLM=vllm
# Any other `vllm serve` options, e.g. EXTRA_ARGS=(--max-num-seqs 256), which
# a lower GPU_MEMORY_UTILIZATION can require.
EXTRA_ARGS=()
# ============================================================================

if [ "$#" -gt 0 ]; then
    echo "serve_vllm.sh takes no arguments: edit the settings at the top of $0" >&2
    exit 2
fi
if ! command -v "$VLLM" > /dev/null; then
    echo "vllm not found ($VLLM): activate your vLLM environment or set VLLM in $0" >&2
    exit 2
fi
if [ -z "$GPUS" ]; then
    echo "Set GPUS in $0" >&2
    exit 2
fi

# The model family picks the parsers: from the model name, and for a local
# checkpoint also from the model_type in its config.json.
family="${MODEL,,}"
if [ -d "$MODEL" ]; then
    if [ ! -f "$MODEL/config.json" ]; then
        echo "No config.json in $MODEL: MODEL must be a checkpoint directory" >&2
        exit 2
    fi
    model_type=$(grep -o '"model_type": *"[^"]*"' "$MODEL/config.json" || true)
    family+=" ${model_type,,}"
elif [[ "$MODEL" = /* || "$MODEL" = ./* || "$MODEL" = ../* ]]; then
    echo "MODEL is not a directory: $MODEL" >&2
    exit 2
fi
case "$family" in
    *gemma-4*|*gemma4*)
        TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-gemma4}"
        REASONING_PARSER="${REASONING_PARSER:-gemma4}"
        ;;
    *qwen3.5*|*qwen3_5*)
        TOOL_CALL_PARSER="${TOOL_CALL_PARSER:-qwen3_coder}"
        REASONING_PARSER="${REASONING_PARSER:-qwen3}"
        ;;
esac
if [ -z "$TOOL_CALL_PARSER" ]; then
    echo "No default parsers for $MODEL: set TOOL_CALL_PARSER (and REASONING_PARSER for a thinking model) in $0" >&2
    exit 2
fi

IFS=, read -ra gpu_ids <<< "$GPUS"
export CUDA_VISIBLE_DEVICES="$GPUS"
if [ -n "$HF_HUB_CACHE" ]; then
    export HF_HUB_CACHE
fi

cmd=("$VLLM" serve "$MODEL" --host "$HOST" --port "$PORT"
     --tensor-parallel-size "${#gpu_ids[@]}" --max-model-len "$MAX_MODEL_LEN"
     --gpu-memory-utilization "$GPU_MEMORY_UTILIZATION"
     --enable-auto-tool-choice --tool-call-parser "$TOOL_CALL_PARSER")
if [ -n "$REASONING_PARSER" ]; then
    cmd+=(--reasoning-parser "$REASONING_PARSER")
fi
if [ -n "$SERVED_MODEL_NAME" ]; then
    cmd+=(--served-model-name "$SERVED_MODEL_NAME")
fi
cmd+=("${EXTRA_ARGS[@]}")

case "$HOST" in
    127.0.0.1|0.0.0.0|localhost) url_host=localhost ;;
    *)                           url_host=$HOST ;;
esac
echo "Set in eval/eval_drc_config.sh and eval/eval_ppa_config.sh:" >&2
echo "  VLLM_BASE_URL=http://$url_host:$PORT/v1" >&2
echo "  VLLM_MODEL=${SERVED_MODEL_NAME:-$MODEL}" >&2
echo "Starting on GPU $GPUS: ${cmd[*]}" >&2
exec "${cmd[@]}"
