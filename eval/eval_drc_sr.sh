#!/bin/bash

# ===================================================================================
# LLM Agent DRC Fixing Capability Evaluation Script - Success Rate Version
# ===================================================================================
#
# Description:
# This script evaluates the DRC fixing ability using Success Rate (SR) instead of pass@k.
# It supports multiple runs and calculates mean and standard deviation for SR and ERR.
# SR and ERR are calculated at benchmark, top-level, and overall levels.
#
# Key metrics:
# - SR (Success Rate): number of successful tasks / total tasks
# - ERR (Error Reduction Rate): average error reduction across tasks
#
# Dependencies:
# - bash (v4.3+ for 'wait -n -p'), python3, jq, bc, tput, realpath, basename
#
# ===================================================================================

# ===================================================================================
# CONFIGURATION - Modify these variables directly
# ===================================================================================

# Maximum number of parallel agent scripts to run
# MAX_PARALLEL_AGENTS=1
MAX_PARALLEL_AGENTS=5

# Temperature setting for LLM
TEMPERATURE=0

# Number of runs for calculating mean and std deviation
NUM_RUNS=5

# Maximum iterations passed to the agent (formerly read from each task's info.json)
ITERATIONS=8

# Resolve repo root from this script's location: <repo>/eval/eval_drc_sr.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Result directory (where outputs will be saved). Override via env.
result_dir_raw="${RESULT_DIR_RAW:-$REPO_ROOT/results/drc/react/v0}"

# Base path of your benchmarks (for calculating relative paths)
benchmark_base_dir="${BENCHMARK_BASE_DIR:-$REPO_ROOT/benchmark/drc_bench}"

# List of top-level directories to evaluate
FOLDER_PATHS=(
    "$REPO_ROOT/benchmark/drc_bench/drc_essential"
    "$REPO_ROOT/benchmark/drc_bench/drc_reasoning"
)

# Path to the agent script. Default points at the ReAct DRC agent shipped in
# this repo. Swap to any other agent (e.g. agents/drc/reflexion/gpt_reflexion.py
# or agents/drc/tot/gpt_tot.py) by exporting AGENT_SCRIPT.
AGENT_SCRIPT="${AGENT_SCRIPT:-$REPO_ROOT/agents/drc/react/gemma_react.py}"

# Path to the DRC verifier script (optional; eval can run without it).
VERIFIER_SCRIPT="${VERIFIER_SCRIPT:-$REPO_ROOT/eval/drc_error_collection_eval.py}"
# ===================================================================================

# --- Helper Functions ---

# Helper function for displaying a tqdm-like progress bar
progress_bar() {
    local current=$1
    local total=$2
    if [ "$total" -eq 0 ]; then
        total=1
        current=0
    fi
    if [ "$current" -gt "$total" ]; then
        current=$total
    fi
    local bar_width=40

    local progress=$((current * 100 / total))
    local filled_width=$((bar_width * progress / 100))

    local filled_bar=$(printf "%${filled_width}s" | tr ' ' '#')
    local bar_and_padding=$(printf "%s" "$filled_bar")
    printf -v bar_and_padding "%-${bar_width}s" "$bar_and_padding"
    bar_and_padding=${bar_and_padding// /-}

    printf "[%s] %3d%% (%d/%d)" "$bar_and_padding" "$progress" "$current" "$total"
}

# Helper function to redraw the entire parallel display
update_parallel_display() {
    tput rc
    tput ed

    local label="Overall Attempts:"
    printf "%-28s %s\n" "$label" "$(progress_bar $grand_current_attempt_num $grand_total_attempts)"

    echo "--- Currently Running (Max: $MAX_PARALLEL_AGENTS) ---"

    local job_lines=()
    for pid in "${!running_jobs[@]}"; do
        job_lines+=("  - ${running_jobs[$pid]} (PID: $pid)")
    done

    local lines_printed=0
    for line in "${job_lines[@]}"; do
        printf "%-80s\n" "$line"
        ((lines_printed++))
    done

    while [ "$lines_printed" -lt "$MAX_PARALLEL_AGENTS" ]; do
        tput el
        echo ""
        ((lines_printed++))
    done
}

# Calculate ERR (Error Reduction Rate)
calculate_err() {
    local init_errors=$1
    local final_errors=$2
    local has_report=$3

    if [ "$has_report" == "0" ]; then
        echo "0.0"
        return
    fi

    if [ "$init_errors" -eq 0 ]; then
        if [ "$final_errors" -eq 0 ]; then
            echo "1.0"
        else
            echo "0.0"
        fi
        return
    fi

    if [ "$final_errors" -gt "$init_errors" ]; then
        echo "0.0"
        return
    fi

    echo "scale=4; ($init_errors - $final_errors) / $init_errors" | bc
}

# Calculate mean of an array
calculate_mean() {
    local -a values=("$@")
    local n=${#values[@]}
    if [ "$n" -eq 0 ]; then
        echo "0.0"
        return
    fi
    local sum=0
    for v in "${values[@]}"; do
        sum=$(echo "$sum + $v" | bc -l)
    done
    echo "scale=4; $sum / $n" | bc
}

# Calculate standard deviation of an array
calculate_std() {
    local -a values=("$@")
    local n=${#values[@]}
    if [ "$n" -le 1 ]; then
        echo "0.0"
        return
    fi

    # Calculate mean
    local sum=0
    for v in "${values[@]}"; do
        sum=$(echo "$sum + $v" | bc -l)
    done
    local mean=$(echo "scale=10; $sum / $n" | bc)

    # Calculate variance
    local var_sum=0
    for v in "${values[@]}"; do
        local diff=$(echo "scale=10; $v - $mean" | bc)
        var_sum=$(echo "$var_sum + ($diff * $diff)" | bc -l)
    done
    local variance=$(echo "scale=10; $var_sum / $n" | bc)

    # Standard deviation
    echo "scale=4; sqrt($variance)" | bc
}

# Parse a single agent_run.log and emit a key=value metrics file.
# Keys: terminal_state, tc_<tool>, tc_total, tc_observation, tc_action,
#       tool_timeouts, tool_failures, llm_errors, llm_turns,
#       tokens_input, tokens_output, tokens_reasoning, tokens_total.
extract_attempt_metrics() {
    local log="$1"
    local out="$2"

    if [ ! -f "$log" ]; then
        : > "$out"
        return
    fi

    local tc_qpts tc_cshape tc_ashape tc_mcell tc_qdrc
    tc_qpts=$(grep -cE "^--- TOOL CALL: Invoking tool 'query_with_pts'" "$log")
    tc_cshape=$(grep -cE "^--- TOOL CALL: Invoking tool 'change_shape'" "$log")
    tc_ashape=$(grep -cE "^--- TOOL CALL: Invoking tool 'add_shape'" "$log")
    tc_mcell=$(grep -cE "^--- TOOL CALL: Invoking tool 'move_cell'" "$log")
    tc_qdrc=$(grep -cE "^--- TOOL CALL: Invoking tool 'query_drc_report'" "$log")

    local tc_total=$((tc_qpts + tc_cshape + tc_ashape + tc_mcell + tc_qdrc))
    local tc_obs=$((tc_qpts + tc_qdrc))
    local tc_act=$((tc_cshape + tc_ashape + tc_mcell))

    local timeouts failures llm_errs llm_turns
    timeouts=$(grep -c 'ERROR: TIMEOUT' "$log")
    failures=$(grep -c 'ERROR: TOOL FAILED' "$log")
    llm_errs=$(grep -c 'ERROR: UNKNOWN  !!!!' "$log")
    llm_turns=$(grep -cE "^--- CALL LLM: call_llm ---" "$log")

    local input_tok output_tok reason_tok
    input_tok=$(grep -oE "'prompt_token_count': [0-9]+" "$log" | awk -F': ' '{s+=$2} END {print s+0}')
    output_tok=$(grep -oE "'candidates_token_count': [0-9]+" "$log" | awk -F': ' '{s+=$2} END {print s+0}')
    reason_tok=$(grep -oE "'thoughts_token_count': [0-9]+" "$log" | awk -F': ' '{s+=$2} END {print s+0}')
    local total_tok=$((input_tok + output_tok + reason_tok))

    local terminal="unknown"
    if grep -qE "^AGENT RUN COMPLETE\. TOTAL TOOL CALLS:" "$log"; then
        terminal="complete"
    elif grep -qE "^AGENT RUN RECURSION LIMIT REACHED\. TOTAL TOOL CALLS:" "$log"; then
        terminal="recursion_limit"
    elif grep -qE "^AGENT RUN FAILED DUE TO ERROR:" "$log"; then
        terminal="agent_exception"
    fi

    {
        echo "terminal_state=$terminal"
        echo "tc_query_with_pts=$tc_qpts"
        echo "tc_change_shape=$tc_cshape"
        echo "tc_add_shape=$tc_ashape"
        echo "tc_move_cell=$tc_mcell"
        echo "tc_query_drc_report=$tc_qdrc"
        echo "tc_total=$tc_total"
        echo "tc_observation=$tc_obs"
        echo "tc_action=$tc_act"
        echo "tool_timeouts=$timeouts"
        echo "tool_failures=$failures"
        echo "llm_errors=$llm_errs"
        echo "llm_turns=$llm_turns"
        echo "tokens_input=$input_tok"
        echo "tokens_output=$output_tok"
        echo "tokens_reasoning=$reason_tok"
        echo "tokens_total=$total_tok"
    } > "$out"
}

# Sandboxed function to run a single attempt
run_single_attempt() {
    local task_path_abs="$1"
    local relative_task_path="$2"
    local run_id="$3"
    local current_result_dir="$4"
    local current_attempt_log="$5"
    local original_script_dir="$6"
    local agent_script_path="$7"
    local verifier_script_path="$8"
    local temperature="$9"
    local num_iterations="${10}"

    # --- Define result file paths ---
    local success_file="$current_result_dir/success.txt"
    local err_file="$current_result_dir/err.txt"
    local status_file="$current_result_dir/status.txt"
    local tool_count_file="$current_result_dir/tool_count.txt"
    local error_types_file="$current_result_dir/error_types.txt"

    # --- Create sandbox ---
    local work_dir="$current_result_dir/work_sandbox"
    mkdir -p "$work_dir"

    # --- Copy verifier script to sandbox ---
    cp "$verifier_script_path" "$work_dir/" 2>/dev/null
    local local_verifier="$work_dir/$(basename "$verifier_script_path")"

    # --- Copy all task inputs into the sandbox ---
    cp -L "$task_path_abs"/* "$work_dir/" 2>/dev/null

    # --- Read task info ---
    local init_errors=0
    local error_types=""
    if [ -f "$work_dir/info.json" ]; then
        init_errors=$(jq -r '.init_errors_num // 0' "$work_dir/info.json" 2>/dev/null)
        error_types=$(jq -r '.init_error_types // {} | keys | join(",")' "$work_dir/info.json" 2>/dev/null)
        if [ -z "$error_types" ] || [ "$error_types" == "null" ]; then
            error_types=$(jq -r '.error_type // [] | join(",")' "$work_dir/info.json" 2>/dev/null)
        fi
        echo "$error_types" > "$error_types_file"
    fi

    # --- CD into the sandbox ---
    cd "$work_dir" || {
        echo "0" > "$success_file"
        echo "0.0" > "$err_file"
        echo "0" > "$tool_count_file"
        echo "$relative_task_path/run$run_id: fail (sandbox cd failed)" > "$status_file"
        rm -rf "$work_dir"
        return 1
    }

    # Cleanup old outputs
    rm -f ./report.lyrdb ./modified.gds ./*.png

    local attempt_status="fail (unknown)"

    # --- Run the agent ---
    export AGENT_RESULT_DIR="$current_result_dir"
    export PYTHONPATH="$REPO_ROOT:$PYTHONPATH"
    export PYTHONUNBUFFERED=1
    python "$agent_script_path" \
        --drc_rpt_path ./prompt.txt \
        --temperature "$temperature" \
        --iterations "$num_iterations" \
        &> "$current_attempt_log"

    # --- Extract Tool Call Count ---
    local tool_count_line=$(grep -E 'AGENT RUN (COMPLETE|RECURSION LIMIT REACHED)\. TOTAL TOOL CALLS:' "$current_attempt_log" | tail -n 1)
    local tool_count=0

    if [ -n "$tool_count_line" ]; then
        tool_count=$(echo "$tool_count_line" | awk '{print $NF}')
        if ! [[ "$tool_count" =~ ^[0-9]+$ ]]; then
            tool_count=0
        fi
    fi
    echo "$tool_count" > "$tool_count_file"

    # --- Extract fine-grained reasoning-process metrics ---
    extract_attempt_metrics "$current_attempt_log" "$current_result_dir/attempt_metrics.txt"

    # --- Check Results ---
    local has_report="0"
    local final_errors="$init_errors"
    local verifier_log="$current_result_dir/verifier.log"

    if [ ! -f "./report.lyrdb" ]; then
        echo "0" > "$success_file"
        echo "0.0" > "$err_file"
        attempt_status="fail (no output)"
        has_report="0"
    else
        has_report="1"

        klayout -b -r "$local_verifier" &> "$verifier_log"

        if [ -f "./info.json" ]; then
            local final_errors_from_json=$(jq -r '.final_errors_num // null' info.json 2>/dev/null)
            if [[ -n "$final_errors_from_json" && "$final_errors_from_json" != "null" ]]; then
                final_errors="$final_errors_from_json"
            fi
        fi

        if [ "$final_errors" -eq 0 ]; then
            echo "1" > "$success_file"
            attempt_status="success"
        else
            echo "0" > "$success_file"
            attempt_status="fail (errors remain: $final_errors)"
        fi

        local err_value=$(calculate_err "$init_errors" "$final_errors" "$has_report")
        echo "$err_value" > "$err_file"
    fi

    # --- Move/Copy artifacts ---
    cp ./info.json "$current_result_dir/" 2>/dev/null
    mv ./modified.gds ./report.lyrdb ./*.png "$current_result_dir/" 2>/dev/null

    # --- Write the final status ---
    echo "$relative_task_path/run$run_id: $attempt_status" > "$status_file"

    # --- Cleanup sandbox ---
    cd "$original_script_dir"
    rm -rf "$work_dir"

    return 0
}

# Export functions for subshells
export -f run_single_attempt
export -f calculate_err
export -f calculate_mean
export -f calculate_std
export -f extract_attempt_metrics

# --- Main Logic ---

result_dir_abs=$(eval echo "$result_dir_raw")
echo "=============================================================="
echo "DRC Agent Evaluation - Success Rate Version"
echo "=============================================================="
echo "Result directory: $result_dir_abs"
echo "Temperature: $TEMPERATURE"
echo "Iterations: $ITERATIONS"
echo "Number of runs: $NUM_RUNS"
echo "Agent script: $AGENT_SCRIPT"
echo "Max parallel agents: $MAX_PARALLEL_AGENTS"
echo ""
mkdir -p "$result_dir_abs"
final_summary_log="$result_dir_abs/final_summary_report.log"

original_script_dir=$(pwd)

# Pre-calculate the grand total number of attempts
grand_total_tasks=0
echo "Calculating total number of tasks..."
all_task_paths=()
for dir in "${FOLDER_PATHS[@]}"; do
    if [ -d "$dir" ]; then
        while IFS= read -r task; do
            if [ -n "$task" ]; then
                all_task_paths+=("$task")
            fi
        done < <(find "$dir" -type d -name "q*")
    fi
done
IFS=$'\n' all_task_paths=($(sort -V <<<"${all_task_paths[*]}"))
unset IFS
grand_total_tasks=${#all_task_paths[@]}

grand_total_attempts=$((grand_total_tasks * NUM_RUNS))
grand_current_attempt_num=0
echo "Found $grand_total_tasks tasks, for a total of $grand_total_attempts attempts across $NUM_RUNS runs."
echo ""

# Export shared variables
export AGENT_SCRIPT VERIFIER_SCRIPT TEMPERATURE ITERATIONS REPO_ROOT

# Arrays to store per-run results for each level.
# Each key -> space-separated per-run value list.
declare -A all_bench_sr_values
declare -A all_bench_err_values
declare -A all_bench_tc_all_values
declare -A all_bench_tc_success_values
declare -A all_bench_tc_positive_values

declare -A all_toplevel_sr_values
declare -A all_toplevel_err_values
declare -A all_toplevel_tc_all_values
declare -A all_toplevel_tc_success_values
declare -A all_toplevel_tc_positive_values

declare -a all_overall_sr=()
declare -a all_overall_err=()
declare -a all_overall_tc_all=()
declare -a all_overall_tc_success=()
declare -a all_overall_tc_positive=()

# Failure statistics by error type (cumulative across runs)
declare -A error_type_task_count
declare -A error_type_failures

# Individual attempt results (status strings from each task)
declare -a individual_results=()

# ----- Cost (USD) and cost-tracker token accumulators -----
# Keyed by "scope" where scope is benchmark-key / top-key / "__overall__"
declare -A scope_cost_sum            # sum of total_cost_usd
declare -A scope_cost_in_tokens      # sum of input_tokens (from cost.json)
declare -A scope_cost_out_tokens     # sum of output_tokens (from cost.json)
declare -A scope_cost_total_tokens   # sum of total_tokens (from cost.json)
declare -A scope_cost_attempts       # attempts that had a cost.json
declare -A scope_model_seen          # model name -> "1"   (for reporting)

# ----- Reasoning-process accumulators -----
# Terminal-state counts per scope (key: scope -> "complete=N recursion_limit=N agent_exception=N unknown=N")
declare -A overall_term_count
declare -A toplevel_term_count
# Per-tool call totals (key: scope -> "tool=name sum=N" via scope_tool keys)
# We flatten as "scope|tool" -> cumulative sum across runs
declare -A tool_sum
declare -A tool_attempt_count
# Scope-level cumulative sums for tokens, tool reliability
declare -A scope_tok_input
declare -A scope_tok_output
declare -A scope_tok_reasoning
declare -A scope_tool_timeouts
declare -A scope_tool_failures
declare -A scope_llm_errors
declare -A scope_attempts

# Failure mode categorization (overall, accumulated across runs)
# Buckets: success, no_output_recursion, no_output_complete, no_output_other,
#          errors_remain_small (<=10), errors_remain_large (>10), tool_blocked
declare -A failure_mode_count

# Enhanced per-task lines (for individual report with tool breakdown + tokens)
declare -a individual_detail=()

# ===================================================================
# MAIN LOOP - Run evaluation NUM_RUNS times
# ===================================================================

for run_id in $(seq 1 "$NUM_RUNS"); do
    echo "=============================================================="
    echo "Starting Run $run_id of $NUM_RUNS"
    echo "=============================================================="

    run_result_dir="$result_dir_abs/run$run_id"
    mkdir -p "$run_result_dir"

    # Set up the live display area
    tput sc
    echo ""
    echo "--- Currently Running (Max: $MAX_PARALLEL_AGENTS) ---"
    for i in $(seq 1 "$MAX_PARALLEL_AGENTS"); do
        echo ""
    done
    tput sc

    declare -A running_jobs

    for task_path in "${all_task_paths[@]}"; do
        task_path_abs=$(realpath "$task_path")
        relative_task_path=$(realpath --relative-to="$benchmark_base_dir" "$task_path")

        current_result_dir="$run_result_dir/$relative_task_path"
        mkdir -p "$current_result_dir"

        current_attempt_log="$current_result_dir/agent_run.log"

        # Skip tasks that already completed (have success.txt), to allow resuming
        if [ -f "$current_result_dir/success.txt" ]; then
            echo "Skipping already completed: $relative_task_path (run$run_id)"
            ((grand_current_attempt_num++))
            continue
        fi

        # Clean up any orphaned work_sandbox from a previous interrupted run
        if [ -d "$current_result_dir/work_sandbox" ]; then
            echo "Cleaning up orphaned sandbox: $current_result_dir/work_sandbox"
            rm -rf "$current_result_dir/work_sandbox"
        fi

        run_single_attempt \
            "$task_path_abs" \
            "$relative_task_path" \
            "$run_id" \
            "$current_result_dir" \
            "$current_attempt_log" \
            "$original_script_dir" \
            "$AGENT_SCRIPT" \
            "$VERIFIER_SCRIPT" \
            "$TEMPERATURE" \
            "$ITERATIONS" &

        pid=$!
        running_jobs["$pid"]="$relative_task_path (run$run_id)"

        update_parallel_display

        job_count=$(jobs -p | wc -l)
        while [ "$job_count" -ge "$MAX_PARALLEL_AGENTS" ]; do
            wait -n -p finished_pid
            unset running_jobs["$finished_pid"]
            ((grand_current_attempt_num++))
            job_count=$(jobs -p | wc -l)
            update_parallel_display
        done
    done

    # Wait for remaining jobs
    while [ "$(jobs -p | wc -l)" -gt 0 ]; do
        wait -n -p finished_pid
        unset running_jobs["$finished_pid"]
        ((grand_current_attempt_num++))
        update_parallel_display
    done

    tput rc
    tput ed
    echo "Run $run_id complete. Aggregating results..."

    # ===================================================================
    # Aggregate results for this run - hierarchical structure
    # (SR, ERR, and tool-call metrics: all / success-only / ERR>0)
    # ===================================================================

    overall_success_count=0
    overall_total_err=0
    overall_task_count=0
    overall_tc_all_sum=0;      overall_tc_all_count=0
    overall_tc_success_sum=0;  overall_tc_success_count=0
    overall_tc_positive_sum=0; overall_tc_positive_count=0

    for top_level_dir in "${FOLDER_PATHS[@]}"; do
        if [ ! -d "$top_level_dir" ]; then
            continue
        fi
        top_key=$(realpath --relative-to="$benchmark_base_dir" "$top_level_dir")

        toplevel_success_count=0
        toplevel_total_err=0
        toplevel_task_count=0
        top_tc_all_sum=0;      top_tc_all_count=0
        top_tc_success_sum=0;  top_tc_success_count=0
        top_tc_positive_sum=0; top_tc_positive_count=0

        # Find all benchmark directories (parent dirs of q* tasks)
        benchmark_dirs=$(find "$top_level_dir" -type d -name "q*" -printf '%h\n' | sort -u)

        for bench_dir in $benchmark_dirs; do
            bench_key=$(realpath --relative-to="$benchmark_base_dir" "$bench_dir")

            bench_success_count=0
            bench_total_err=0
            bench_task_count=0
            bench_tc_all_sum=0;      bench_tc_all_count=0
            bench_tc_success_sum=0;  bench_tc_success_count=0
            bench_tc_positive_sum=0; bench_tc_positive_count=0

            task_folders=$(find "$bench_dir" -maxdepth 1 -type d -name "q*" | sort -V)

            for task_path in $task_folders; do
                relative_task_path=$(realpath --relative-to="$benchmark_base_dir" "$task_path")
                current_result_dir="$run_result_dir/$relative_task_path"

                success_val=0
                err_val=0
                tool_count=0

                [ -f "$current_result_dir/success.txt" ] && success_val=$(cat "$current_result_dir/success.txt")
                [ -f "$current_result_dir/err.txt" ] && err_val=$(cat "$current_result_dir/err.txt")
                [ -f "$current_result_dir/tool_count.txt" ] && tool_count=$(cat "$current_result_dir/tool_count.txt")
                status_line=""
                if [ -f "$current_result_dir/status.txt" ]; then
                    status_line=$(cat "$current_result_dir/status.txt")
                    individual_results+=("run$run_id: $status_line")
                fi

                # Lazy-extract reasoning metrics if missing (e.g., for resumed runs)
                metrics_file="$current_result_dir/attempt_metrics.txt"
                if [ ! -f "$metrics_file" ] && [ -f "$current_result_dir/agent_run.log" ]; then
                    extract_attempt_metrics "$current_result_dir/agent_run.log" "$metrics_file"
                fi

                # Load metrics into variables (sourcing is safe — file has only key=int)
                term_state="unknown"
                m_qpts=0; m_cshape=0; m_ashape=0; m_mcell=0; m_qdrc=0
                m_obs=0; m_act=0
                m_timeouts=0; m_failures=0; m_llm_errs=0; m_turns=0
                m_tin=0; m_tout=0; m_treason=0
                if [ -f "$metrics_file" ]; then
                    while IFS='=' read -r k v; do
                        case "$k" in
                            terminal_state) term_state="$v" ;;
                            tc_query_with_pts) m_qpts="$v" ;;
                            tc_change_shape) m_cshape="$v" ;;
                            tc_add_shape) m_ashape="$v" ;;
                            tc_move_cell) m_mcell="$v" ;;
                            tc_query_drc_report) m_qdrc="$v" ;;
                            tc_observation) m_obs="$v" ;;
                            tc_action) m_act="$v" ;;
                            tool_timeouts) m_timeouts="$v" ;;
                            tool_failures) m_failures="$v" ;;
                            llm_errors) m_llm_errs="$v" ;;
                            llm_turns) m_turns="$v" ;;
                            tokens_input) m_tin="$v" ;;
                            tokens_output) m_tout="$v" ;;
                            tokens_reasoning) m_treason="$v" ;;
                        esac
                    done < "$metrics_file"
                fi

                # Read cost.json if present (produced by the agent's cost_tracking.write_cost_json)
                attempt_cost="0"
                attempt_in_tok=0
                attempt_out_tok=0
                attempt_total_tok=0
                cost_file="$current_result_dir/cost.json"
                if [ -f "$cost_file" ]; then
                    attempt_cost=$(jq -r '.total_cost_usd // 0' "$cost_file" 2>/dev/null)
                    attempt_in_tok=$(jq -r '.input_tokens // 0' "$cost_file" 2>/dev/null)
                    attempt_out_tok=$(jq -r '.output_tokens // 0' "$cost_file" 2>/dev/null)
                    attempt_total_tok=$(jq -r '.total_tokens // 0' "$cost_file" 2>/dev/null)
                    attempt_model=$(jq -r '.model // "unknown"' "$cost_file" 2>/dev/null)
                    [ -n "$attempt_model" ] && scope_model_seen["$attempt_model"]="1"
                    have_cost=1
                else
                    have_cost=0
                fi

                # Per-tool cumulative sums at each scope (bench / top / overall)
                for scope_key in "$bench_key" "$top_key" "__overall__"; do
                    tool_sum["$scope_key|query_with_pts"]=$((${tool_sum["$scope_key|query_with_pts"]:-0} + m_qpts))
                    tool_sum["$scope_key|change_shape"]=$((${tool_sum["$scope_key|change_shape"]:-0} + m_cshape))
                    tool_sum["$scope_key|add_shape"]=$((${tool_sum["$scope_key|add_shape"]:-0} + m_ashape))
                    tool_sum["$scope_key|move_cell"]=$((${tool_sum["$scope_key|move_cell"]:-0} + m_mcell))
                    tool_sum["$scope_key|query_drc_report"]=$((${tool_sum["$scope_key|query_drc_report"]:-0} + m_qdrc))
                    tool_sum["$scope_key|observation"]=$((${tool_sum["$scope_key|observation"]:-0} + m_obs))
                    tool_sum["$scope_key|action"]=$((${tool_sum["$scope_key|action"]:-0} + m_act))
                    scope_tok_input["$scope_key"]=$((${scope_tok_input["$scope_key"]:-0} + m_tin))
                    scope_tok_output["$scope_key"]=$((${scope_tok_output["$scope_key"]:-0} + m_tout))
                    scope_tok_reasoning["$scope_key"]=$((${scope_tok_reasoning["$scope_key"]:-0} + m_treason))
                    scope_tool_timeouts["$scope_key"]=$((${scope_tool_timeouts["$scope_key"]:-0} + m_timeouts))
                    scope_tool_failures["$scope_key"]=$((${scope_tool_failures["$scope_key"]:-0} + m_failures))
                    scope_llm_errors["$scope_key"]=$((${scope_llm_errors["$scope_key"]:-0} + m_llm_errs))
                    scope_attempts["$scope_key"]=$((${scope_attempts["$scope_key"]:-0} + 1))
                    # Cost tracking accumulation
                    if [ "$have_cost" -eq 1 ]; then
                        scope_cost_sum["$scope_key"]=$(echo "${scope_cost_sum["$scope_key"]:-0} + $attempt_cost" | bc)
                        scope_cost_in_tokens["$scope_key"]=$((${scope_cost_in_tokens["$scope_key"]:-0} + attempt_in_tok))
                        scope_cost_out_tokens["$scope_key"]=$((${scope_cost_out_tokens["$scope_key"]:-0} + attempt_out_tok))
                        scope_cost_total_tokens["$scope_key"]=$((${scope_cost_total_tokens["$scope_key"]:-0} + attempt_total_tok))
                        scope_cost_attempts["$scope_key"]=$((${scope_cost_attempts["$scope_key"]:-0} + 1))
                    fi
                done

                # Terminal-state distribution per top-level and overall
                for scope_key in "$top_key" "__overall__"; do
                    combined_key="$scope_key|$term_state"
                    overall_term_count["$combined_key"]=$((${overall_term_count["$combined_key"]:-0} + 1))
                done

                # Failure mode categorization (overall)
                fm="unknown"
                if [ "$success_val" == "1" ]; then
                    fm="success"
                elif [[ "$status_line" == *"no output"* ]]; then
                    if [ "$term_state" == "recursion_limit" ]; then
                        fm="no_output_recursion"
                    elif [ "$term_state" == "complete" ]; then
                        fm="no_output_complete"
                    elif [ "$term_state" == "agent_exception" ]; then
                        fm="no_output_exception"
                    else
                        fm="no_output_other"
                    fi
                elif [[ "$status_line" == *"errors remain"* ]]; then
                    residual=$(echo "$status_line" | sed -nE 's/.*errors remain: ([0-9]+).*/\1/p')
                    residual=${residual:-0}
                    if [ "$residual" -le 10 ]; then
                        fm="errors_remain_small"
                    else
                        fm="errors_remain_large"
                    fi
                fi
                # Flag tool-blocked overlay (any failure that also had tool timeouts/failures)
                if [ "$success_val" != "1" ] && { [ "$m_timeouts" -gt 0 ] || [ "$m_failures" -gt 0 ]; }; then
                    failure_mode_count["tool_blocked"]=$((${failure_mode_count["tool_blocked"]:-0} + 1))
                fi
                failure_mode_count["$fm"]=$((${failure_mode_count["$fm"]:-0} + 1))

                # Enhanced per-task line (includes cost if available)
                cost_display="-"
                [ "$have_cost" -eq 1 ] && cost_display="\$$attempt_cost"
                individual_detail+=("$(printf "run%s  %-45s  %-20s  term=%-15s  tc=%-3s  q=%s c=%s a=%s m=%s v=%s  tok(i/o/r)=%s/%s/%s  cost=%s" \
                    "$run_id" "$relative_task_path" \
                    "$(echo "$status_line" | awk -F': ' '{print $NF}' | cut -c1-20)" \
                    "$term_state" "$tool_count" \
                    "$m_qpts" "$m_cshape" "$m_ashape" "$m_mcell" "$m_qdrc" \
                    "$m_tin" "$m_tout" "$m_treason" \
                    "$cost_display")")

                # Error-type failure tracking
                local_error_types=""
                if [ -f "$current_result_dir/error_types.txt" ]; then
                    local_error_types=$(cat "$current_result_dir/error_types.txt")
                elif [ -f "$task_path/info.json" ]; then
                    local_error_types=$(jq -r '.init_error_types // {} | keys | join(",")' "$task_path/info.json" 2>/dev/null)
                fi
                if [ "$run_id" -eq 1 ] && [ -n "$local_error_types" ]; then
                    IFS=',' read -ra et_array <<< "$local_error_types"
                    for et in "${et_array[@]}"; do
                        [ -n "$et" ] && error_type_task_count["$et"]=$((${error_type_task_count["$et"]:-0} + 1))
                    done
                fi
                if [ "$success_val" != "1" ] && [ -n "$local_error_types" ]; then
                    IFS=',' read -ra et_array <<< "$local_error_types"
                    for et in "${et_array[@]}"; do
                        [ -n "$et" ] && error_type_failures["$et"]=$((${error_type_failures["$et"]:-0} + 1))
                    done
                fi

                if [ "$success_val" == "1" ]; then
                    ((bench_success_count++))
                fi
                bench_total_err=$(echo "$bench_total_err + $err_val" | bc -l)
                ((bench_task_count++))

                # Tool call metrics
                bench_tc_all_sum=$(echo "$bench_tc_all_sum + $tool_count" | bc)
                ((bench_tc_all_count++))
                if [ "$success_val" == "1" ]; then
                    bench_tc_success_sum=$(echo "$bench_tc_success_sum + $tool_count" | bc)
                    ((bench_tc_success_count++))
                fi
                if (( $(echo "$err_val > 0" | bc -l) )); then
                    bench_tc_positive_sum=$(echo "$bench_tc_positive_sum + $tool_count" | bc)
                    ((bench_tc_positive_count++))
                fi
            done

            # Per-benchmark metrics for this run
            if [ "$bench_task_count" -gt 0 ]; then
                bench_sr=$(echo "scale=4; $bench_success_count / $bench_task_count" | bc)
                bench_avg_err=$(echo "scale=4; $bench_total_err / $bench_task_count" | bc)
            else
                bench_sr="0.0"; bench_avg_err="0.0"
            fi
            [ "$bench_tc_all_count" -gt 0 ]     && bench_tc_all_avg=$(echo "scale=2; $bench_tc_all_sum / $bench_tc_all_count" | bc)         || bench_tc_all_avg="0.00"
            [ "$bench_tc_success_count" -gt 0 ] && bench_tc_success_avg=$(echo "scale=2; $bench_tc_success_sum / $bench_tc_success_count" | bc) || bench_tc_success_avg="0.00"
            [ "$bench_tc_positive_count" -gt 0 ] && bench_tc_positive_avg=$(echo "scale=2; $bench_tc_positive_sum / $bench_tc_positive_count" | bc) || bench_tc_positive_avg="0.00"

            all_bench_sr_values["$bench_key"]="${all_bench_sr_values[$bench_key]} $bench_sr"
            all_bench_err_values["$bench_key"]="${all_bench_err_values[$bench_key]} $bench_avg_err"
            all_bench_tc_all_values["$bench_key"]="${all_bench_tc_all_values[$bench_key]} $bench_tc_all_avg"
            all_bench_tc_success_values["$bench_key"]="${all_bench_tc_success_values[$bench_key]} $bench_tc_success_avg"
            all_bench_tc_positive_values["$bench_key"]="${all_bench_tc_positive_values[$bench_key]} $bench_tc_positive_avg"

            # Accumulate to top-level
            toplevel_success_count=$((toplevel_success_count + bench_success_count))
            toplevel_total_err=$(echo "$toplevel_total_err + $bench_total_err" | bc -l)
            toplevel_task_count=$((toplevel_task_count + bench_task_count))
            top_tc_all_sum=$(echo "$top_tc_all_sum + $bench_tc_all_sum" | bc)
            top_tc_all_count=$((top_tc_all_count + bench_tc_all_count))
            top_tc_success_sum=$(echo "$top_tc_success_sum + $bench_tc_success_sum" | bc)
            top_tc_success_count=$((top_tc_success_count + bench_tc_success_count))
            top_tc_positive_sum=$(echo "$top_tc_positive_sum + $bench_tc_positive_sum" | bc)
            top_tc_positive_count=$((top_tc_positive_count + bench_tc_positive_count))
        done

        # Per top-level metrics for this run
        if [ "$toplevel_task_count" -gt 0 ]; then
            toplevel_sr=$(echo "scale=4; $toplevel_success_count / $toplevel_task_count" | bc)
            toplevel_avg_err=$(echo "scale=4; $toplevel_total_err / $toplevel_task_count" | bc)
        else
            toplevel_sr="0.0"; toplevel_avg_err="0.0"
        fi
        [ "$top_tc_all_count" -gt 0 ]     && top_tc_all_avg=$(echo "scale=2; $top_tc_all_sum / $top_tc_all_count" | bc)         || top_tc_all_avg="0.00"
        [ "$top_tc_success_count" -gt 0 ] && top_tc_success_avg=$(echo "scale=2; $top_tc_success_sum / $top_tc_success_count" | bc) || top_tc_success_avg="0.00"
        [ "$top_tc_positive_count" -gt 0 ] && top_tc_positive_avg=$(echo "scale=2; $top_tc_positive_sum / $top_tc_positive_count" | bc) || top_tc_positive_avg="0.00"

        all_toplevel_sr_values["$top_key"]="${all_toplevel_sr_values[$top_key]} $toplevel_sr"
        all_toplevel_err_values["$top_key"]="${all_toplevel_err_values[$top_key]} $toplevel_avg_err"
        all_toplevel_tc_all_values["$top_key"]="${all_toplevel_tc_all_values[$top_key]} $top_tc_all_avg"
        all_toplevel_tc_success_values["$top_key"]="${all_toplevel_tc_success_values[$top_key]} $top_tc_success_avg"
        all_toplevel_tc_positive_values["$top_key"]="${all_toplevel_tc_positive_values[$top_key]} $top_tc_positive_avg"

        overall_success_count=$((overall_success_count + toplevel_success_count))
        overall_total_err=$(echo "$overall_total_err + $toplevel_total_err" | bc -l)
        overall_task_count=$((overall_task_count + toplevel_task_count))
        overall_tc_all_sum=$(echo "$overall_tc_all_sum + $top_tc_all_sum" | bc)
        overall_tc_all_count=$((overall_tc_all_count + top_tc_all_count))
        overall_tc_success_sum=$(echo "$overall_tc_success_sum + $top_tc_success_sum" | bc)
        overall_tc_success_count=$((overall_tc_success_count + top_tc_success_count))
        overall_tc_positive_sum=$(echo "$overall_tc_positive_sum + $top_tc_positive_sum" | bc)
        overall_tc_positive_count=$((overall_tc_positive_count + top_tc_positive_count))
    done

    # Overall metrics for this run
    if [ "$overall_task_count" -gt 0 ]; then
        overall_sr=$(echo "scale=4; $overall_success_count / $overall_task_count" | bc)
        overall_avg_err=$(echo "scale=4; $overall_total_err / $overall_task_count" | bc)
    else
        overall_sr="0.0"; overall_avg_err="0.0"
    fi
    [ "$overall_tc_all_count" -gt 0 ]     && overall_tc_all=$(echo "scale=2; $overall_tc_all_sum / $overall_tc_all_count" | bc)         || overall_tc_all="0.00"
    [ "$overall_tc_success_count" -gt 0 ] && overall_tc_success=$(echo "scale=2; $overall_tc_success_sum / $overall_tc_success_count" | bc) || overall_tc_success="0.00"
    [ "$overall_tc_positive_count" -gt 0 ] && overall_tc_positive=$(echo "scale=2; $overall_tc_positive_sum / $overall_tc_positive_count" | bc) || overall_tc_positive="0.00"

    all_overall_sr+=("$overall_sr")
    all_overall_err+=("$overall_avg_err")
    all_overall_tc_all+=("$overall_tc_all")
    all_overall_tc_success+=("$overall_tc_success")
    all_overall_tc_positive+=("$overall_tc_positive")

    echo "Run $run_id: Overall SR = $overall_sr ($overall_success_count/$overall_task_count), Avg ERR = $overall_avg_err, TC(all) = $overall_tc_all"
    echo ""
done

# ===================================================================
# Final Summary with Mean and Standard Deviation at all levels
# ===================================================================

{
    echo "=============================================================="
    echo "DRC Evaluation - Success Rate Summary"
    echo "=============================================================="
    echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Result directory: $result_dir_abs"
    echo "Agent script: $AGENT_SCRIPT"
    echo "Temperature: $TEMPERATURE"
    echo "Iterations: $ITERATIONS"
    echo "Number of runs: $NUM_RUNS"
    echo "Total tasks per run: $grand_total_tasks"
    echo ""
    echo "Folder paths:"
    for fp in "${FOLDER_PATHS[@]}"; do
        echo "  - $fp"
    done
    echo ""

    # Per-run overall results
    echo "=============================================================="
    echo "Per-Run Overall Results"
    echo "=============================================================="
    printf "%-6s %-12s %-12s %-12s %-14s %-12s\n" "Run" "SR" "ERR" "TC(all)" "TC(success)" "TC(ERR>0)"
    echo "--------------------------------------------------------------"
    for i in $(seq 0 $((NUM_RUNS - 1))); do
        run_num=$((i + 1))
        printf "%-6s %-12s %-12s %-12s %-14s %-12s\n" \
            "Run$run_num" \
            "${all_overall_sr[$i]}" \
            "${all_overall_err[$i]}" \
            "${all_overall_tc_all[$i]}" \
            "${all_overall_tc_success[$i]}" \
            "${all_overall_tc_positive[$i]}"
    done
    echo ""

    # Benchmark-level statistics
    echo "=============================================================="
    echo "Benchmark-Level Statistics (Mean +/- Std across $NUM_RUNS runs)"
    echo "=============================================================="
    for bench_key in $(echo "${!all_bench_sr_values[@]}" | tr ' ' '\n' | sort); do
        sr_arr=(${all_bench_sr_values["$bench_key"]})
        err_arr=(${all_bench_err_values["$bench_key"]})
        tc_all_arr=(${all_bench_tc_all_values["$bench_key"]})
        tc_success_arr=(${all_bench_tc_success_values["$bench_key"]})
        tc_positive_arr=(${all_bench_tc_positive_values["$bench_key"]})

        mean_sr=$(calculate_mean "${sr_arr[@]}")
        std_sr=$(calculate_std "${sr_arr[@]}")
        mean_err=$(calculate_mean "${err_arr[@]}")
        std_err=$(calculate_std "${err_arr[@]}")
        mean_tc_all=$(calculate_mean "${tc_all_arr[@]}")
        std_tc_all=$(calculate_std "${tc_all_arr[@]}")
        mean_tc_success=$(calculate_mean "${tc_success_arr[@]}")
        std_tc_success=$(calculate_std "${tc_success_arr[@]}")
        mean_tc_positive=$(calculate_mean "${tc_positive_arr[@]}")
        std_tc_positive=$(calculate_std "${tc_positive_arr[@]}")

        echo "Benchmark: $bench_key"
        echo "  SR:           $mean_sr +/- $std_sr"
        echo "  ERR:          $mean_err +/- $std_err"
        echo "  TC (all):     $mean_tc_all +/- $std_tc_all"
        echo "  TC (success): $mean_tc_success +/- $std_tc_success"
        echo "  TC (ERR>0):   $mean_tc_positive +/- $std_tc_positive"
        echo ""
    done

    # Top-level statistics
    echo "=============================================================="
    echo "Top-Level Statistics (Mean +/- Std across $NUM_RUNS runs)"
    echo "=============================================================="
    for top_key in $(echo "${!all_toplevel_sr_values[@]}" | tr ' ' '\n' | sort); do
        sr_arr=(${all_toplevel_sr_values["$top_key"]})
        err_arr=(${all_toplevel_err_values["$top_key"]})
        tc_all_arr=(${all_toplevel_tc_all_values["$top_key"]})
        tc_success_arr=(${all_toplevel_tc_success_values["$top_key"]})
        tc_positive_arr=(${all_toplevel_tc_positive_values["$top_key"]})

        mean_sr=$(calculate_mean "${sr_arr[@]}")
        std_sr=$(calculate_std "${sr_arr[@]}")
        mean_err=$(calculate_mean "${err_arr[@]}")
        std_err=$(calculate_std "${err_arr[@]}")
        mean_tc_all=$(calculate_mean "${tc_all_arr[@]}")
        std_tc_all=$(calculate_std "${tc_all_arr[@]}")
        mean_tc_success=$(calculate_mean "${tc_success_arr[@]}")
        std_tc_success=$(calculate_std "${tc_success_arr[@]}")
        mean_tc_positive=$(calculate_mean "${tc_positive_arr[@]}")
        std_tc_positive=$(calculate_std "${tc_positive_arr[@]}")

        echo "Top-Level: $top_key"
        echo "  SR:           $mean_sr +/- $std_sr"
        echo "  ERR:          $mean_err +/- $std_err"
        echo "  TC (all):     $mean_tc_all +/- $std_tc_all"
        echo "  TC (success): $mean_tc_success +/- $std_tc_success"
        echo "  TC (ERR>0):   $mean_tc_positive +/- $std_tc_positive"
        echo ""
    done

    # Overall aggregate statistics
    echo "=============================================================="
    echo "Overall Aggregate Statistics (Mean +/- Std across $NUM_RUNS runs)"
    echo "=============================================================="
    mean_sr=$(calculate_mean "${all_overall_sr[@]}")
    std_sr=$(calculate_std "${all_overall_sr[@]}")
    mean_err=$(calculate_mean "${all_overall_err[@]}")
    std_err=$(calculate_std "${all_overall_err[@]}")
    mean_tc_all=$(calculate_mean "${all_overall_tc_all[@]}")
    std_tc_all=$(calculate_std "${all_overall_tc_all[@]}")
    mean_tc_success=$(calculate_mean "${all_overall_tc_success[@]}")
    std_tc_success=$(calculate_std "${all_overall_tc_success[@]}")
    mean_tc_positive=$(calculate_mean "${all_overall_tc_positive[@]}")
    std_tc_positive=$(calculate_std "${all_overall_tc_positive[@]}")

    echo "Success Rate (SR):"
    echo "  Mean: $mean_sr"
    echo "  Std:  $std_sr"
    echo ""
    echo "Error Reduction Rate (ERR):"
    echo "  Mean: $mean_err"
    echo "  Std:  $std_err"
    echo ""
    echo "Tool Calls (all attempts):"
    echo "  Mean: $mean_tc_all"
    echo "  Std:  $std_tc_all"
    echo ""
    echo "Tool Calls (success only):"
    echo "  Mean: $mean_tc_success"
    echo "  Std:  $std_tc_success"
    echo ""
    echo "Tool Calls (ERR > 0):"
    echo "  Mean: $mean_tc_positive"
    echo "  Std:  $std_tc_positive"
    echo ""

    # ==============================================================
    # Reasoning-process metrics: tool-usage breakdown per scope
    # ==============================================================
    echo "=============================================================="
    echo "Tool Usage Breakdown (avg calls per attempt, across all runs)"
    echo "=============================================================="
    printf "%-25s %-10s %-10s %-10s %-10s %-10s | %-10s %-10s %-8s\n" \
        "Scope" "q_pts" "c_shape" "a_shape" "m_cell" "q_drc" "obs" "action" "a/o"
    echo "----------------------------------------------------------------------------------------------------"
    scope_print_order=()
    for top_key in $(echo "${!all_toplevel_sr_values[@]}" | tr ' ' '\n' | sort); do
        scope_print_order+=("$top_key")
    done
    for bench_key in $(echo "${!all_bench_sr_values[@]}" | tr ' ' '\n' | sort); do
        scope_print_order+=("$bench_key")
    done
    scope_print_order+=("__overall__")

    for scope in "${scope_print_order[@]}"; do
        attempts=${scope_attempts["$scope"]:-0}
        [ "$attempts" -eq 0 ] && continue
        qpts=$(echo "scale=2; ${tool_sum["$scope|query_with_pts"]:-0} / $attempts" | bc)
        cshape=$(echo "scale=2; ${tool_sum["$scope|change_shape"]:-0} / $attempts" | bc)
        ashape=$(echo "scale=2; ${tool_sum["$scope|add_shape"]:-0} / $attempts" | bc)
        mcell=$(echo "scale=2; ${tool_sum["$scope|move_cell"]:-0} / $attempts" | bc)
        qdrc=$(echo "scale=2; ${tool_sum["$scope|query_drc_report"]:-0} / $attempts" | bc)
        obs=$(echo "scale=2; ${tool_sum["$scope|observation"]:-0} / $attempts" | bc)
        act=$(echo "scale=2; ${tool_sum["$scope|action"]:-0} / $attempts" | bc)
        obs_total=${tool_sum["$scope|observation"]:-0}
        act_total=${tool_sum["$scope|action"]:-0}
        if [ "$obs_total" -gt 0 ]; then
            ratio=$(echo "scale=2; $act_total / $obs_total" | bc)
        else
            ratio="N/A"
        fi
        label="$scope"
        [ "$scope" == "__overall__" ] && label="OVERALL"
        printf "%-25s %-10s %-10s %-10s %-10s %-10s | %-10s %-10s %-8s\n" \
            "$label" "$qpts" "$cshape" "$ashape" "$mcell" "$qdrc" "$obs" "$act" "$ratio"
    done
    echo ""

    # ==============================================================
    # Terminal state distribution (agent's final exit state)
    # ==============================================================
    echo "=============================================================="
    echo "Terminal State Distribution (per scope, across all $NUM_RUNS runs)"
    echo "=============================================================="
    printf "%-25s %-18s %-18s %-18s %-10s\n" \
        "Scope" "complete" "recursion_limit" "agent_exception" "unknown"
    echo "----------------------------------------------------------------------------------------------------"
    term_scopes=()
    for top_key in $(echo "${!all_toplevel_sr_values[@]}" | tr ' ' '\n' | sort); do
        term_scopes+=("$top_key")
    done
    term_scopes+=("__overall__")

    for scope in "${term_scopes[@]}"; do
        total=${scope_attempts["$scope"]:-0}
        [ "$total" -eq 0 ] && continue
        c_complete=${overall_term_count["$scope|complete"]:-0}
        c_recursion=${overall_term_count["$scope|recursion_limit"]:-0}
        c_exception=${overall_term_count["$scope|agent_exception"]:-0}
        c_unknown=${overall_term_count["$scope|unknown"]:-0}
        pct() { echo "scale=1; $1 * 100 / $total" | bc; }
        label="$scope"
        [ "$scope" == "__overall__" ] && label="OVERALL"
        printf "%-25s %-4d (%-5s%%) %-4d (%-5s%%) %-4d (%-5s%%) %-4d (%-5s%%)\n" \
            "$label" \
            "$c_complete" "$(pct $c_complete)" \
            "$c_recursion" "$(pct $c_recursion)" \
            "$c_exception" "$(pct $c_exception)" \
            "$c_unknown" "$(pct $c_unknown)"
    done
    echo ""

    # ==============================================================
    # Tool reliability (timeouts, tool exceptions, LLM errors)
    # ==============================================================
    echo "=============================================================="
    echo "Tool Reliability (counts of mid-run errors per scope)"
    echo "=============================================================="
    printf "%-25s %-12s %-12s %-12s %-10s\n" "Scope" "Timeouts" "ToolFailed" "LLMErrors" "Attempts"
    echo "----------------------------------------------------------------------------------------------------"
    for scope in "${term_scopes[@]}"; do
        total=${scope_attempts["$scope"]:-0}
        [ "$total" -eq 0 ] && continue
        t=${scope_tool_timeouts["$scope"]:-0}
        f=${scope_tool_failures["$scope"]:-0}
        le=${scope_llm_errors["$scope"]:-0}
        label="$scope"
        [ "$scope" == "__overall__" ] && label="OVERALL"
        printf "%-25s %-12d %-12d %-12d %-10d\n" "$label" "$t" "$f" "$le" "$total"
    done
    echo ""

    # ==============================================================
    # Token usage (averages per attempt)
    # ==============================================================
    echo "=============================================================="
    echo "Token Usage (avg per attempt, across all runs)"
    echo "=============================================================="
    printf "%-25s %-14s %-14s %-14s %-10s\n" \
        "Scope" "Input(avg)" "Output(avg)" "Reasoning(avg)" "R/O ratio"
    echo "----------------------------------------------------------------------------------------------------"
    for scope in "${term_scopes[@]}"; do
        total=${scope_attempts["$scope"]:-0}
        [ "$total" -eq 0 ] && continue
        tin=${scope_tok_input["$scope"]:-0}
        tout=${scope_tok_output["$scope"]:-0}
        tre=${scope_tok_reasoning["$scope"]:-0}
        avg_in=$(echo "scale=1; $tin / $total" | bc)
        avg_out=$(echo "scale=1; $tout / $total" | bc)
        avg_re=$(echo "scale=1; $tre / $total" | bc)
        if [ "$tout" -gt 0 ]; then
            ratio=$(echo "scale=2; $tre / $tout" | bc)
        else
            ratio="N/A"
        fi
        label="$scope"
        [ "$scope" == "__overall__" ] && label="OVERALL"
        printf "%-25s %-14s %-14s %-14s %-10s\n" "$label" "$avg_in" "$avg_out" "$avg_re" "$ratio"
    done
    echo ""

    # ==============================================================
    # API cost tracking (from per-attempt cost.json written by agents)
    # ==============================================================
    echo "=============================================================="
    echo "API Cost Tracking (from cost.json written by each agent)"
    echo "=============================================================="
    # List model names actually seen (if any)
    models_seen_list=""
    for m in "${!scope_model_seen[@]}"; do
        models_seen_list="$models_seen_list $m"
    done
    if [ -n "$models_seen_list" ]; then
        echo "Models observed:$models_seen_list"
    fi

    total_with_cost=${scope_cost_attempts["__overall__"]:-0}
    total_without_cost=$((${scope_attempts["__overall__"]:-0} - total_with_cost))
    echo "Attempts with cost.json: $total_with_cost"
    if [ "$total_without_cost" -gt 0 ]; then
        echo "Attempts missing cost.json: $total_without_cost (likely pre-dating cost tracking or agent crashed before finally)"
    fi
    echo ""
    printf "%-25s %-14s %-14s %-14s %-14s %-14s\n" \
        "Scope" "TotalCost" "AvgCost/run" "AvgInTok/run" "AvgOutTok/run" "Attempts(w/cost)"
    echo "----------------------------------------------------------------------------------------------------"
    for scope in "${scope_print_order[@]}"; do
        n_cost=${scope_cost_attempts["$scope"]:-0}
        [ "$n_cost" -eq 0 ] && continue
        tot=${scope_cost_sum["$scope"]:-0}
        in_tok=${scope_cost_in_tokens["$scope"]:-0}
        out_tok=${scope_cost_out_tokens["$scope"]:-0}
        avg_cost=$(echo "scale=6; $tot / $n_cost" | bc)
        avg_in=$((in_tok / n_cost))
        avg_out=$((out_tok / n_cost))
        label="$scope"
        [ "$scope" == "__overall__" ] && label="OVERALL"
        printf "%-25s \$%-13s \$%-13s %-14d %-14d %-14d\n" \
            "$label" "$tot" "$avg_cost" "$avg_in" "$avg_out" "$n_cost"
    done
    echo ""

    # ==============================================================
    # Failure mode categorization (across all runs)
    # ==============================================================
    echo "=============================================================="
    echo "Failure Mode Categorization (across all $NUM_RUNS runs)"
    echo "=============================================================="
    total_attempts_all_runs=$((grand_total_tasks * NUM_RUNS))
    echo "Total attempts: $total_attempts_all_runs"
    echo ""
    printf "%-28s %-10s %-10s\n" "Mode" "Count" "Share"
    echo "--------------------------------------------------"
    fm_order=(success no_output_recursion no_output_complete no_output_exception no_output_other errors_remain_small errors_remain_large unknown tool_blocked)
    for fm in "${fm_order[@]}"; do
        cnt=${failure_mode_count["$fm"]:-0}
        if [ "$total_attempts_all_runs" -gt 0 ]; then
            share=$(echo "scale=3; $cnt / $total_attempts_all_runs" | bc)
        else
            share="0.000"
        fi
        printf "%-28s %-10d %-10s\n" "$fm" "$cnt" "$share"
    done
    echo ""
    echo "Legend:"
    echo "  success              : DRC errors cleared"
    echo "  no_output_recursion  : agent hit iteration limit without producing a GDS"
    echo "  no_output_complete   : agent stopped (complete) without producing a GDS"
    echo "  no_output_exception  : agent crashed before producing a GDS"
    echo "  errors_remain_small  : modified GDS, residual DRC errors <= 10"
    echo "  errors_remain_large  : modified GDS, residual DRC errors > 10 (often made it worse)"
    echo "  tool_blocked         : overlay count — any failure where a tool timed out or raised"
    echo ""

    # Failure statistics by error type
    echo "=============================================================="
    echo "Failure Statistics by Error Type (across all $NUM_RUNS runs)"
    echo "=============================================================="
    echo "Note: Task count is per task (not multiplied by runs)."
    echo "      Failures are summed across all runs."
    echo ""
    printf "%-25s %-12s %-14s %-12s\n" "Error Type" "Failures" "Tasks*Runs" "Failure Rate"
    echo "--------------------------------------------------------------"
    for et in $(echo "${!error_type_task_count[@]}" | tr ' ' '\n' | sort); do
        failures=${error_type_failures["$et"]:-0}
        task_count=${error_type_task_count["$et"]}
        total_attempts=$((task_count * NUM_RUNS))
        if [ "$total_attempts" -gt 0 ]; then
            rate=$(echo "scale=4; $failures / $total_attempts" | bc)
        else
            rate="0.0000"
        fi
        printf "%-25s %-12d %-14d %s\n" "$et" "$failures" "$total_attempts" "$rate"
    done
    echo ""

    # Individual attempt success/failure report (short form)
    echo "=============================================================="
    echo "Individual Attempt Success/Failure Report"
    echo "=============================================================="
    printf "%s\n" "${individual_results[@]}" | sort
    echo ""

    # Enhanced per-task detail: terminal state, tool breakdown, tokens
    echo "=============================================================="
    echo "Per-Attempt Detail (terminal state, tool calls by name, tokens)"
    echo "=============================================================="
    echo "Columns: q=query_with_pts, c=change_shape, a=add_shape, m=move_cell, v=query_drc_report"
    echo "         tok(i/o/r) = input / output / reasoning tokens"
    echo ""
    printf "%s\n" "${individual_detail[@]}" | sort
    echo "=============================================================="
} | tee "$final_summary_log"

echo ""
echo "Evaluation complete. Results saved to $final_summary_log"
