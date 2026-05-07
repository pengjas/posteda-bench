#!/bin/bash

# ===================================================================================
# LLM Agent PPA Fixing Capability Evaluation Script - Success Rate Version
# ===================================================================================
#
# Description:
# This script evaluates the PPA optimization ability using Success Rate (SR) instead of pass@k.
# It supports multiple runs and calculates mean and standard deviation for SR and VRR.
# SR and VRR are calculated at benchmark, top-level, and overall levels.
#
# Key metrics:
# - SR (Success Rate): number of successful tasks / total tasks
# - VRR (Violation Reduction Rate): average violation reduction across tasks
#
# Dependencies:
# - bash (v4.3+ for 'wait -n -p'), python3, jq, bc, tput, realpath, basename
#
# ===================================================================================

# ===================================================================================
# CONFIGURATION - Modify these variables directly
# ===================================================================================

# Re-aggregation mode: set REAGGREGATE_ONLY=1 in the environment (or here) to
# skip Stage 1 (parallel agent launching) and only re-run Stage 2 (aggregation
# + final summary) against an existing $RESULT_DIR_RAW tree. Useful when a new
# metric was added after the original run. Attempts that lack cost.json /
# trajectory_stats.json are auto-backfilled from agent_run.log.
: "${REAGGREGATE_ONLY:=0}"

# Maximum number of parallel agent scripts to run
MAX_PARALLEL_AGENTS=2
# MAX_PARALLEL_AGENTS=5

# Temperature setting for LLM
TEMPERATURE=0

# Number of runs for calculating mean and std deviation
NUM_RUNS=5
# Iterations per run (applied to every task, overrides info.json)
ITERATIONS=16

# Tool timeout in seconds
TOOL_TIMEOUT=1800

# Resolve repo root from this script's location: <repo>/eval/eval_ppa_sr.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Result directory (where outputs will be saved). Override by exporting RESULT_DIR_RAW.
RESULT_DIR_RAW="${RESULT_DIR_RAW:-$REPO_ROOT/results/ppa/orfs_agent/v0}"

# Base path of your benchmarks (for calculating relative paths)
BENCHMARK_BASE_DIR="${BENCHMARK_BASE_DIR:-$REPO_ROOT/benchmark/ppa_bench}"

# List of top-level directories to evaluate
FOLDER_PATHS=(
    "$REPO_ROOT/benchmark/ppa_bench/ppa_multi"
    "$REPO_ROOT/benchmark/ppa_bench/ppa_mono/area"
    "$REPO_ROOT/benchmark/ppa_bench/ppa_mono/power"
    "$REPO_ROOT/benchmark/ppa_bench/ppa_mono/perf"
)

# Path to the PPA agent script
AGENT_SCRIPT="${AGENT_SCRIPT:-$REPO_ROOT/agents/ppa/orfs_agent/orfs_agent.py}"

# Path to the PPA verifier script
VERIFIER_SCRIPT="${VERIFIER_SCRIPT:-$REPO_ROOT/eval/ppa_metric_collection.py}"

# Path to the PPA scorer (success + VRR from info.json). Optional; eval works
# without it if absent.
SCORER_SCRIPT="${SCORER_SCRIPT:-$REPO_ROOT/eval/ppa_score.py}"

# OpenROAD flow scripts location. Reviewer must set this env var, or symlink
# OpenROAD-flow-scripts/flow under their HOME.
OPENROAD_FLOW_DIR="${OPENROAD_FLOW_DIR:-$HOME/OpenROAD-flow-scripts/flow}"
export OPENROAD_FLOW_DIR

# FlowVariables documentation
FLOW_VARIABLES_DOC="${FLOW_VARIABLES_DOC:-$REPO_ROOT/eval/FlowVariables.md}"

# Post-agent RTL functional-equivalence sanity check (event-match + latency-sweep).
# Pipeline-depth and handshake-timing changes PASS; only functional divergence
# (wrong logic, broken FSM, etc.) FAILs.
EQUIV_CHECK_SCRIPT="${EQUIV_CHECK_SCRIPT:-$REPO_ROOT/eval/equiv/check_equiv.sh}"

# Optional trajectory analyzer + summarizer. Skipped silently if absent.
TRAJ_ANALYZER="${TRAJ_ANALYZER:-$REPO_ROOT/eval/analyze_trajectory.py}"
TRAJ_SUMMARIZER="${TRAJ_SUMMARIZER:-$REPO_ROOT/eval/summarize_trajectory_stats.py}"

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

# Score a task's info.json: returns "<success 0|1> <vrr>" via ppa_score.py
score_task() {
    local info_json_path=$1
    python3 "$SCORER_SCRIPT" "$info_json_path" 2>/dev/null || echo "0 0.0"
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
    local vrr_file="$current_result_dir/vrr.txt"
    local status_file="$current_result_dir/status.txt"
    local tool_count_file="$current_result_dir/tool_count.txt"
    local ppa_type_file="$current_result_dir/ppa_type.txt"
    local equiv_status_file="$current_result_dir/equiv_status.txt"
    local equiv_log="$current_result_dir/equiv.log"

    # --- Create sandbox ---
    local work_dir="$current_result_dir/work_sandbox"
    mkdir -p "$work_dir"

    # --- Copy verifier script to sandbox ---
    cp "$verifier_script_path" "$work_dir/" 2>/dev/null
    local local_verifier="$work_dir/$(basename "$verifier_script_path")"

    # --- Copy all task inputs into the sandbox ---
    for subdir in config src result results report reports log logs object objects; do
        if [ -d "$task_path_abs/$subdir" ]; then
            cp -rL "$task_path_abs/$subdir" "$work_dir/" 2>/dev/null
        fi
    done
    cp -L "$task_path_abs"/*.txt "$work_dir/" 2>/dev/null
    cp -L "$task_path_abs"/*.json "$work_dir/" 2>/dev/null

    # --- Snapshot original src/ for post-agent equivalence check ---
    # _orig_src is outside the agent's sandbox-rooted view; stored under the
    # result dir so the equiv check can diff it against the sandboxed ./src.
    if [ -d "$work_dir/src" ]; then
        cp -rL "$work_dir/src" "$current_result_dir/_orig_src" 2>/dev/null
    fi

    # --- Copy FlowVariables.md documentation ---
    if [ -f "$FLOW_VARIABLES_DOC" ]; then
        cp "$FLOW_VARIABLES_DOC" "$work_dir/" 2>/dev/null
    fi

    # --- Determine PPA type ---
    local ppa_type=""
    if [ -f "$work_dir/info.json" ]; then
        local json_ppa_type=$(jq -r '.ppa_type // null' "$work_dir/info.json" 2>/dev/null)
        if [ "$json_ppa_type" != "null" ] && [ -n "$json_ppa_type" ]; then
            ppa_type="$json_ppa_type"
        else
            local t_area=$(jq -r '.target_area // null' "$work_dir/info.json" 2>/dev/null)
            local t_power=$(jq -r '.target_power // null' "$work_dir/info.json" 2>/dev/null)
            local t_period=$(jq -r '.target_effective_period // null' "$work_dir/info.json" 2>/dev/null)

            local t_count=0
            [ "$t_area" != "null" ] && [ -n "$t_area" ] && ((t_count++))
            [ "$t_power" != "null" ] && [ -n "$t_power" ] && ((t_count++))
            [ "$t_period" != "null" ] && [ -n "$t_period" ] && ((t_count++))

            if [ "$t_count" -gt 1 ]; then
                ppa_type="multi_dimension"
            elif [ "$t_area" != "null" ] && [ -n "$t_area" ]; then
                ppa_type="area"
            elif [ "$t_power" != "null" ] && [ -n "$t_power" ]; then
                ppa_type="power"
            elif [ "$t_period" != "null" ] && [ -n "$t_period" ]; then
                ppa_type="performance"
            fi
        fi
    fi

    if [ -z "$ppa_type" ]; then
        if [[ "$task_path_abs" == *"/multi_dimension/"* ]]; then
            ppa_type="multi_dimension"
        elif [[ "$task_path_abs" == *"/area/"* ]]; then
            ppa_type="area"
        elif [[ "$task_path_abs" == *"/power/"* ]]; then
            ppa_type="power"
        elif [[ "$task_path_abs" == *"/performance/"* ]]; then
            ppa_type="performance"
        else
            ppa_type="unknown"
        fi
    fi

    echo "$ppa_type" > "$ppa_type_file"

    # --- CD into the sandbox ---
    cd "$work_dir" || {
        echo "0" > "$success_file"
        echo "0.0" > "$vrr_file"
        echo "0" > "$tool_count_file"
        echo "$relative_task_path/run$run_id: fail (sandbox cd failed)" > "$status_file"
        rm -rf "$work_dir"
        return 1
    }

    local attempt_status="fail (unknown)"

    # --- Run the agent ---
    export PPA_SANDBOX_ROOT="$work_dir"
    export AGENT_RESULT_DIR="$current_result_dir"
    export PYTHONPATH="$REPO_ROOT:$PYTHONPATH"

    python "$agent_script_path" \
        --prompt_path ./prompt.txt \
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

    # --- Check Results ---
    local has_result="0"
    local verifier_log="$current_result_dir/verifier.log"

    # --- RTL functional-equivalence sanity check ---
    # Runs only when we have a snapshot of the original src and the agent's
    # (possibly modified) ./src. DESIGN_NAME is parsed from config/*.mk.
    # If the design has no TB (e.g. ibex_core with external RTL), we get SKIP
    # (exit 0) and treat it as PASS.
    local equiv_status="SKIP"
    if [ -d "$current_result_dir/_orig_src" ] && [ -d "./src" ]; then
        local design_name=""
        if ls ./config/*.mk >/dev/null 2>&1; then
            design_name=$(grep -h "^export DESIGN_NAME" ./config/*.mk 2>/dev/null \
                          | head -1 | awk -F= '{print $2}' | tr -d ' \t')
        fi
        if [ -n "$design_name" ] && [ -x "$EQUIV_CHECK_SCRIPT" ]; then
            bash "$EQUIV_CHECK_SCRIPT" \
                "$current_result_dir/_orig_src" "./src" "$design_name" \
                &> "$equiv_log"
            local equiv_rc=$?
            if [ "$equiv_rc" -eq 0 ]; then
                equiv_status="PASS"
            elif [ "$equiv_rc" -eq 1 ]; then
                equiv_status="FAIL_FUNCTIONAL"
            else
                equiv_status="FAIL_INFRA"   # build error / usage / missing input
            fi
        fi
    fi
    echo "$equiv_status" > "$equiv_status_file"

    # Short-circuit on functional-equivalence failure: the PPA numbers are
    # meaningless if the agent broke the design. Skip OpenROAD scoring entirely.
    if [ "$equiv_status" = "FAIL_FUNCTIONAL" ]; then
        echo "0" > "$success_file"
        echo "0.0" > "$vrr_file"
        attempt_status="fail (functional equivalence)"
        cp ./info.json "$current_result_dir/" 2>/dev/null
        cp -r ./reports "$current_result_dir/" 2>/dev/null
        cp -r ./results "$current_result_dir/" 2>/dev/null
        cp -r ./logs "$current_result_dir/" 2>/dev/null
        cp -r ./config "$current_result_dir/" 2>/dev/null
        echo "$relative_task_path/run$run_id: $attempt_status" > "$status_file"
        cd "$original_script_dir"
        rm -rf "$work_dir"
        return 0
    fi

    if [ -f "./reports/6_finish.rpt" ] || [ -f "./logs/6_report.json" ]; then
        has_result="1"

        python "$local_verifier" &> "$verifier_log" || true

        if [ -f "./info.json" ]; then
            local score_line=$(score_task "./info.json")
            local success_flag=$(echo "$score_line" | awk '{print $1}')
            local vrr=$(echo "$score_line" | awk '{print $2}')

            if [ "$success_flag" == "1" ]; then
                echo "1" > "$success_file"
                attempt_status="success"
            else
                echo "0" > "$success_file"
                attempt_status="fail (targets not met)"
            fi

            echo "$vrr" > "$vrr_file"
        else
            echo "0" > "$success_file"
            echo "0.0" > "$vrr_file"
            attempt_status="fail (info.json not updated)"
        fi
    else
        echo "0" > "$success_file"
        echo "0.0" > "$vrr_file"
        attempt_status="fail (no reports generated)"
    fi

    # --- Move/Copy artifacts ---
    cp ./info.json "$current_result_dir/" 2>/dev/null
    cp -r ./reports "$current_result_dir/" 2>/dev/null
    cp -r ./results "$current_result_dir/" 2>/dev/null
    cp -r ./logs "$current_result_dir/" 2>/dev/null
    cp -r ./config "$current_result_dir/" 2>/dev/null

    # --- Write the final status ---
    echo "$relative_task_path/run$run_id: $attempt_status" > "$status_file"

    # --- Layer-1 trajectory analysis ---
    # Parse agent_run.log into trajectory_stats.json so the aggregator can
    # surface reasoning/behavior metrics alongside SR/VRR. Non-fatal on failure.
    local final_success=0
    [ -f "$success_file" ] && final_success=$(cat "$success_file")
    if [ -f "$current_attempt_log" ] && [ -f "$TRAJ_ANALYZER" ]; then
        python3 "$TRAJ_ANALYZER" "$current_attempt_log" \
            --success "$final_success" \
            --out "$current_result_dir/trajectory_stats.json" \
            > /dev/null 2>> "$current_result_dir/trajectory_analyzer.log" || true
    fi

    # --- Cleanup sandbox ---
    cd "$original_script_dir"
    rm -rf "$work_dir"

    return 0
}

# Export functions for subshells
export -f run_single_attempt
export -f score_task
export -f calculate_mean
export -f calculate_std
export SCORER_SCRIPT TRAJ_ANALYZER TRAJ_SUMMARIZER

# --- Main Logic ---

# Record eval start time so cleanup only touches artifacts from this session
EVAL_START_TS=$(date +%s)

# User-scoped cleanup of docker containers + temp dirs left by hard-killed runs.
# Safe on shared hosts:
#   - Containers: only openroad/orfs:latest ancestor, status=exited,
#                 and CreatedAt >= EVAL_START_TS (this session only).
#   - Temp dirs:  only -user $USER AND -newermt EVAL_START_TS.
# Will NOT touch other users' containers or their _temp_runs/ entries,
# and will NOT touch ORFS containers running before this eval started.
cleanup_docker_and_tmp() {
    echo ""
    echo "--- Post-eval cleanup (user-scoped) ---"

    if command -v docker >/dev/null 2>&1; then
        local cleaned=0
        while read -r cid created; do
            [ -z "$cid" ] && continue
            local created_ts
            created_ts=$(date -d "$created" +%s 2>/dev/null || echo 0)
            if [ "$created_ts" -ge "$EVAL_START_TS" ]; then
                docker rm -f "$cid" >/dev/null 2>&1 && ((cleaned++))
            fi
        done < <(docker ps -a \
                     --filter ancestor=openroad/orfs:latest \
                     --filter status=exited \
                     --format '{{.ID}} {{.CreatedAt}}' 2>/dev/null)
        echo "  docker: removed $cleaned exited ORFS container(s) from this session"
    fi

    if [ -d "$OPENROAD_FLOW_DIR/_temp_runs" ]; then
        local removed=0
        while IFS= read -r d; do
            [ -z "$d" ] && continue
            rm -rf "$d" && ((removed++))
        done < <(find "$OPENROAD_FLOW_DIR/_temp_runs" -mindepth 1 -maxdepth 1 -type d \
                      -user "$USER" -newermt "@$EVAL_START_TS" 2>/dev/null)
        echo "  _temp_runs: removed $removed leftover sandbox dir(s)"
    fi
}
trap cleanup_docker_and_tmp EXIT

result_dir_abs=$(eval echo "$RESULT_DIR_RAW")
echo "=============================================================="
echo "PPA Agent Evaluation - Success Rate Version"
echo "=============================================================="
echo "Result directory: $result_dir_abs"
echo "Temperature: $TEMPERATURE"
echo "Number of runs: $NUM_RUNS"
echo "Iterations: $ITERATIONS"
echo "Tool timeout: ${TOOL_TIMEOUT}s"
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
export AGENT_SCRIPT VERIFIER_SCRIPT TEMPERATURE ITERATIONS TOOL_TIMEOUT OPENROAD_FLOW_DIR FLOW_VARIABLES_DOC REPO_ROOT

# Arrays to store per-run results for each level
# 5-tuple values per run: sr,vrr,tc_all,tc_success,tc_positive
declare -A all_bench_results      # bench_path -> "sr1,vrr1,tcA1,tcS1,tcP1 ..."
declare -A all_toplevel_results   # toplevel_path -> "sr1,vrr1,tcA1,tcS1,tcP1 ..."

declare -a all_overall_sr=()
declare -a all_overall_vrr=()
declare -a all_overall_tc_all=()
declare -a all_overall_tc_success=()
declare -a all_overall_tc_positive=()

# Arrays for combined single_dimension statistics (area + power + performance)
declare -a all_single_dim_sr=()
declare -a all_single_dim_vrr=()
declare -a all_single_dim_tc_all=()
declare -a all_single_dim_tc_success=()
declare -a all_single_dim_tc_positive=()

# Cost / token totals (in USD and raw token counts) pooled across every
# attempt in every run. Each agent writes cost.json into its result dir; the
# eval script sums those numbers at bench / top-level / overall / single-dim
# granularity so the final report can show total $ spent and per-attempt
# averages. Single-dim totals are additionally carried as a combined view.
declare -A bench_cost_total          # bench_dir -> sum of total_cost_usd (string, bc-safe)
declare -A bench_input_tokens_total  # bench_dir -> sum input_tokens
declare -A bench_output_tokens_total # bench_dir -> sum output_tokens
declare -A bench_cost_attempts       # bench_dir -> count of attempts with cost.json
declare -A toplevel_cost_total
declare -A toplevel_input_tokens_total
declare -A toplevel_output_tokens_total
declare -A toplevel_cost_attempts
overall_cost_total="0.0";       overall_input_tokens_total=0;   overall_output_tokens_total=0;   overall_cost_attempts=0
sd_cost_total="0.0";            sd_input_tokens_total=0;        sd_output_tokens_total=0;        sd_cost_attempts=0

# Failure stats aggregated across all runs
declare -A ppa_type_failures
declare -A ppa_type_task_count

# Equivalence-check outcomes pooled across all runs.
declare -A equiv_outcome_count   # PASS|SKIP|FAIL_FUNCTIONAL|FAIL_INFRA -> count

# Per-attempt record for the final individual report
declare -a individual_results=()

# ===================================================================
# MAIN LOOP - Run evaluation NUM_RUNS times
# ===================================================================

for run_id in $(seq 1 "$NUM_RUNS"); do
    echo "=============================================================="
    echo "Starting Run $run_id of $NUM_RUNS"
    echo "=============================================================="

    run_result_dir="$result_dir_abs/run$run_id"
    mkdir -p "$run_result_dir"

    if [ "$REAGGREGATE_ONLY" = "1" ]; then
        echo "REAGGREGATE_ONLY=1: skipping parallel agent launch for run $run_id."
        # Backfill cost.json for attempts that predate cost tracking, and
        # (re)generate trajectory_stats.json for attempts that lack it.
        if [ -d "$run_result_dir" ]; then
            python3 "$(dirname "$TRAJ_ANALYZER")/backfill_cost_from_log.py" \
                --walk "$run_result_dir" 2>&1 | sed 's/^/  [cost] /'
            while IFS= read -r log; do
                [ -z "$log" ] && continue
                attempt_dir=$(dirname "$log")
                if [ ! -f "$attempt_dir/trajectory_stats.json" ]; then
                    succ=0
                    [ -f "$attempt_dir/success.txt" ] && succ=$(cat "$attempt_dir/success.txt")
                    python3 "$TRAJ_ANALYZER" "$log" \
                        --success "$succ" \
                        --out "$attempt_dir/trajectory_stats.json" \
                        > /dev/null 2>&1 || true
                fi
            done < <(find "$run_result_dir" -name agent_run.log)
        fi
    else
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
            relative_task_path=$(realpath --relative-to="$BENCHMARK_BASE_DIR" "$task_path")

            task_iterations="$ITERATIONS"

            current_result_dir="$run_result_dir/$relative_task_path"
            mkdir -p "$current_result_dir"

            current_attempt_log="$current_result_dir/agent_run.log"

            # Skip-if-already-scored: if success.txt is present we already have a
            # result for this attempt (any prior run that produced one). To force
            # a retry, delete the attempt's result dir before re-running the eval.
            if [ -f "$current_result_dir/success.txt" ]; then
                ((grand_current_attempt_num++))
                update_parallel_display
                continue
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
                "$task_iterations" &

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
    fi
    echo "Run $run_id complete. Aggregating results..."

    # ===================================================================
    # Aggregate results for this run - hierarchical structure
    # ===================================================================

    # Run-overall accumulators
    overall_success_count=0
    overall_total_vrr=0
    overall_task_count=0
    overall_tc_all_sum=0;      overall_tc_all_n=0
    overall_tc_succ_sum=0;     overall_tc_succ_n=0
    overall_tc_pos_sum=0;      overall_tc_pos_n=0

    # Combined single_dimension accumulators (area+power+performance)
    single_dim_success_count=0
    single_dim_total_vrr=0
    single_dim_task_count=0
    sd_tc_all_sum=0;     sd_tc_all_n=0
    sd_tc_succ_sum=0;    sd_tc_succ_n=0
    sd_tc_pos_sum=0;     sd_tc_pos_n=0

    for top_level_dir in "${FOLDER_PATHS[@]}"; do
        if [ ! -d "$top_level_dir" ]; then
            continue
        fi

        toplevel_success_count=0
        toplevel_total_vrr=0
        toplevel_task_count=0
        tl_tc_all_sum=0;   tl_tc_all_n=0
        tl_tc_succ_sum=0;  tl_tc_succ_n=0
        tl_tc_pos_sum=0;   tl_tc_pos_n=0

        benchmark_dirs=$(find "$top_level_dir" -type d -name "q*" -printf '%h\n' | sort -u)

        for bench_dir in $benchmark_dirs; do
            bench_success_count=0
            bench_total_vrr=0
            bench_task_count=0
            b_tc_all_sum=0;   b_tc_all_n=0
            b_tc_succ_sum=0;  b_tc_succ_n=0
            b_tc_pos_sum=0;   b_tc_pos_n=0

            task_folders=$(find "$bench_dir" -maxdepth 1 -type d -name "q*" | sort -V)

            for task_path in $task_folders; do
                relative_task_path=$(realpath --relative-to="$BENCHMARK_BASE_DIR" "$task_path")
                current_result_dir="$run_result_dir/$relative_task_path"
                success_file="$current_result_dir/success.txt"
                vrr_file="$current_result_dir/vrr.txt"
                tool_count_file="$current_result_dir/tool_count.txt"
                ppa_type_file="$current_result_dir/ppa_type.txt"

                success_val=0
                vrr_val="0.0"
                tool_count=0
                ppa_type="unknown"
                equiv_val="UNKNOWN"
                equiv_status_file="$current_result_dir/equiv_status.txt"
                cost_file="$current_result_dir/cost.json"

                [ -f "$success_file" ] && success_val=$(cat "$success_file")
                [ -f "$vrr_file" ] && vrr_val=$(cat "$vrr_file")
                if [ -f "$tool_count_file" ]; then
                    tool_count=$(cat "$tool_count_file")
                    [[ "$tool_count" =~ ^[0-9]+$ ]] || tool_count=0
                fi
                [ -f "$ppa_type_file" ] && ppa_type=$(cat "$ppa_type_file")
                [ -f "$equiv_status_file" ] && equiv_val=$(cat "$equiv_status_file")
                equiv_outcome_count["$equiv_val"]=$((${equiv_outcome_count["$equiv_val"]:-0} + 1))

                # --- Cost / token accumulation (bench + toplevel + overall + single-dim) ---
                # All buckets are pooled across every run; we roll up here so
                # each attempt is counted exactly once at every level it belongs to.
                if [ -f "$cost_file" ]; then
                    attempt_cost=$(jq -r '.total_cost_usd // 0' "$cost_file" 2>/dev/null)
                    attempt_in_tok=$(jq -r '.input_tokens // 0' "$cost_file" 2>/dev/null)
                    attempt_out_tok=$(jq -r '.output_tokens // 0' "$cost_file" 2>/dev/null)
                    [[ "$attempt_in_tok"  =~ ^[0-9]+$ ]] || attempt_in_tok=0
                    [[ "$attempt_out_tok" =~ ^[0-9]+$ ]] || attempt_out_tok=0
                    bench_cost_total["$bench_dir"]=$(echo "${bench_cost_total[$bench_dir]:-0} + $attempt_cost" | bc -l)
                    bench_input_tokens_total["$bench_dir"]=$((${bench_input_tokens_total[$bench_dir]:-0} + attempt_in_tok))
                    bench_output_tokens_total["$bench_dir"]=$((${bench_output_tokens_total[$bench_dir]:-0} + attempt_out_tok))
                    bench_cost_attempts["$bench_dir"]=$((${bench_cost_attempts[$bench_dir]:-0} + 1))

                    toplevel_cost_total["$top_level_dir"]=$(echo "${toplevel_cost_total[$top_level_dir]:-0} + $attempt_cost" | bc -l)
                    toplevel_input_tokens_total["$top_level_dir"]=$((${toplevel_input_tokens_total[$top_level_dir]:-0} + attempt_in_tok))
                    toplevel_output_tokens_total["$top_level_dir"]=$((${toplevel_output_tokens_total[$top_level_dir]:-0} + attempt_out_tok))
                    toplevel_cost_attempts["$top_level_dir"]=$((${toplevel_cost_attempts[$top_level_dir]:-0} + 1))

                    overall_cost_total=$(echo "$overall_cost_total + $attempt_cost" | bc -l)
                    overall_input_tokens_total=$((overall_input_tokens_total + attempt_in_tok))
                    overall_output_tokens_total=$((overall_output_tokens_total + attempt_out_tok))
                    ((overall_cost_attempts++))

                    if [[ "$top_level_dir" == *"/single_dimension/openroad/area"*      || \
                          "$top_level_dir" == *"/single_dimension/openroad/power"*     || \
                          "$top_level_dir" == *"/single_dimension/openroad/performance"* ]]; then
                        sd_cost_total=$(echo "$sd_cost_total + $attempt_cost" | bc -l)
                        sd_input_tokens_total=$((sd_input_tokens_total + attempt_in_tok))
                        sd_output_tokens_total=$((sd_output_tokens_total + attempt_out_tok))
                        ((sd_cost_attempts++))
                    fi
                fi

                # Per-attempt line
                if [ "$success_val" == "1" ]; then
                    individual_results+=("run$run_id/$relative_task_path: success (vrr=$vrr_val, tools=$tool_count, equiv=$equiv_val)")
                    ((bench_success_count++))
                else
                    individual_results+=("run$run_id/$relative_task_path: fail (vrr=$vrr_val, tools=$tool_count, equiv=$equiv_val)")
                fi

                # PPA-type failure stats (pooled across runs)
                ppa_type_task_count["$ppa_type"]=$((${ppa_type_task_count["$ppa_type"]:-0} + 1))
                if [ "$success_val" != "1" ]; then
                    ppa_type_failures["$ppa_type"]=$((${ppa_type_failures["$ppa_type"]:-0} + 1))
                fi

                bench_total_vrr=$(echo "$bench_total_vrr + $vrr_val" | bc -l)
                ((bench_task_count++))

                # Tool calls (all)
                b_tc_all_sum=$((b_tc_all_sum + tool_count)); ((b_tc_all_n++))
                # Tool calls (success only)
                if [ "$success_val" == "1" ]; then
                    b_tc_succ_sum=$((b_tc_succ_sum + tool_count)); ((b_tc_succ_n++))
                fi
                # Tool calls (vrr > 0)
                if (( $(echo "$vrr_val > 0" | bc -l) )); then
                    b_tc_pos_sum=$((b_tc_pos_sum + tool_count)); ((b_tc_pos_n++))
                fi
            done

            # Bench-level per-run stats
            if [ "$bench_task_count" -gt 0 ]; then
                bench_sr=$(echo "scale=4; $bench_success_count / $bench_task_count" | bc)
                bench_avg_vrr=$(echo "scale=4; $bench_total_vrr / $bench_task_count" | bc)
            else
                bench_sr="0.0"; bench_avg_vrr="0.0"
            fi
            bench_avg_tc_all=$([ "$b_tc_all_n" -gt 0 ] && echo "scale=2; $b_tc_all_sum / $b_tc_all_n" | bc || echo "NA")
            bench_avg_tc_succ=$([ "$b_tc_succ_n" -gt 0 ] && echo "scale=2; $b_tc_succ_sum / $b_tc_succ_n" | bc || echo "NA")
            bench_avg_tc_pos=$([ "$b_tc_pos_n" -gt 0 ] && echo "scale=2; $b_tc_pos_sum / $b_tc_pos_n" | bc || echo "NA")

            all_bench_results["$bench_dir"]="${all_bench_results[$bench_dir]} ${bench_sr},${bench_avg_vrr},${bench_avg_tc_all},${bench_avg_tc_succ},${bench_avg_tc_pos}"

            # Accumulate to top-level
            toplevel_success_count=$((toplevel_success_count + bench_success_count))
            toplevel_total_vrr=$(echo "$toplevel_total_vrr + $bench_total_vrr" | bc -l)
            toplevel_task_count=$((toplevel_task_count + bench_task_count))
            tl_tc_all_sum=$((tl_tc_all_sum + b_tc_all_sum));   tl_tc_all_n=$((tl_tc_all_n + b_tc_all_n))
            tl_tc_succ_sum=$((tl_tc_succ_sum + b_tc_succ_sum)); tl_tc_succ_n=$((tl_tc_succ_n + b_tc_succ_n))
            tl_tc_pos_sum=$((tl_tc_pos_sum + b_tc_pos_sum));   tl_tc_pos_n=$((tl_tc_pos_n + b_tc_pos_n))
        done

        # Top-level per-run stats
        if [ "$toplevel_task_count" -gt 0 ]; then
            toplevel_sr=$(echo "scale=4; $toplevel_success_count / $toplevel_task_count" | bc)
            toplevel_avg_vrr=$(echo "scale=4; $toplevel_total_vrr / $toplevel_task_count" | bc)
        else
            toplevel_sr="0.0"; toplevel_avg_vrr="0.0"
        fi
        toplevel_avg_tc_all=$([ "$tl_tc_all_n" -gt 0 ] && echo "scale=2; $tl_tc_all_sum / $tl_tc_all_n" | bc || echo "NA")
        toplevel_avg_tc_succ=$([ "$tl_tc_succ_n" -gt 0 ] && echo "scale=2; $tl_tc_succ_sum / $tl_tc_succ_n" | bc || echo "NA")
        toplevel_avg_tc_pos=$([ "$tl_tc_pos_n" -gt 0 ] && echo "scale=2; $tl_tc_pos_sum / $tl_tc_pos_n" | bc || echo "NA")

        all_toplevel_results["$top_level_dir"]="${all_toplevel_results[$top_level_dir]} ${toplevel_sr},${toplevel_avg_vrr},${toplevel_avg_tc_all},${toplevel_avg_tc_succ},${toplevel_avg_tc_pos}"

        # Accumulate to overall
        overall_success_count=$((overall_success_count + toplevel_success_count))
        overall_total_vrr=$(echo "$overall_total_vrr + $toplevel_total_vrr" | bc -l)
        overall_task_count=$((overall_task_count + toplevel_task_count))
        overall_tc_all_sum=$((overall_tc_all_sum + tl_tc_all_sum));   overall_tc_all_n=$((overall_tc_all_n + tl_tc_all_n))
        overall_tc_succ_sum=$((overall_tc_succ_sum + tl_tc_succ_sum)); overall_tc_succ_n=$((overall_tc_succ_n + tl_tc_succ_n))
        overall_tc_pos_sum=$((overall_tc_pos_sum + tl_tc_pos_sum));   overall_tc_pos_n=$((overall_tc_pos_n + tl_tc_pos_n))

        # Combined single-dimension (area+power+performance)
        if [[ "$top_level_dir" == *"/single_dimension/openroad/area"* ]] || \
           [[ "$top_level_dir" == *"/single_dimension/openroad/power"* ]] || \
           [[ "$top_level_dir" == *"/single_dimension/openroad/performance"* ]]; then
            single_dim_success_count=$((single_dim_success_count + toplevel_success_count))
            single_dim_total_vrr=$(echo "$single_dim_total_vrr + $toplevel_total_vrr" | bc -l)
            single_dim_task_count=$((single_dim_task_count + toplevel_task_count))
            sd_tc_all_sum=$((sd_tc_all_sum + tl_tc_all_sum));   sd_tc_all_n=$((sd_tc_all_n + tl_tc_all_n))
            sd_tc_succ_sum=$((sd_tc_succ_sum + tl_tc_succ_sum)); sd_tc_succ_n=$((sd_tc_succ_n + tl_tc_succ_n))
            sd_tc_pos_sum=$((sd_tc_pos_sum + tl_tc_pos_sum));   sd_tc_pos_n=$((sd_tc_pos_n + tl_tc_pos_n))
        fi
    done

    # Per-run single-dim stats
    if [ "$single_dim_task_count" -gt 0 ]; then
        single_dim_sr=$(echo "scale=4; $single_dim_success_count / $single_dim_task_count" | bc)
        single_dim_avg_vrr=$(echo "scale=4; $single_dim_total_vrr / $single_dim_task_count" | bc)
    else
        single_dim_sr="0.0"; single_dim_avg_vrr="0.0"
    fi
    sd_avg_tc_all=$([ "$sd_tc_all_n" -gt 0 ] && echo "scale=2; $sd_tc_all_sum / $sd_tc_all_n" | bc || echo "NA")
    sd_avg_tc_succ=$([ "$sd_tc_succ_n" -gt 0 ] && echo "scale=2; $sd_tc_succ_sum / $sd_tc_succ_n" | bc || echo "NA")
    sd_avg_tc_pos=$([ "$sd_tc_pos_n" -gt 0 ] && echo "scale=2; $sd_tc_pos_sum / $sd_tc_pos_n" | bc || echo "NA")
    all_single_dim_sr+=("$single_dim_sr")
    all_single_dim_vrr+=("$single_dim_avg_vrr")
    all_single_dim_tc_all+=("$sd_avg_tc_all")
    all_single_dim_tc_success+=("$sd_avg_tc_succ")
    all_single_dim_tc_positive+=("$sd_avg_tc_pos")

    # Per-run overall stats
    if [ "$overall_task_count" -gt 0 ]; then
        overall_sr=$(echo "scale=4; $overall_success_count / $overall_task_count" | bc)
        overall_avg_vrr=$(echo "scale=4; $overall_total_vrr / $overall_task_count" | bc)
    else
        overall_sr="0.0"; overall_avg_vrr="0.0"
    fi
    overall_avg_tc_all=$([ "$overall_tc_all_n" -gt 0 ] && echo "scale=2; $overall_tc_all_sum / $overall_tc_all_n" | bc || echo "NA")
    overall_avg_tc_succ=$([ "$overall_tc_succ_n" -gt 0 ] && echo "scale=2; $overall_tc_succ_sum / $overall_tc_succ_n" | bc || echo "NA")
    overall_avg_tc_pos=$([ "$overall_tc_pos_n" -gt 0 ] && echo "scale=2; $overall_tc_pos_sum / $overall_tc_pos_n" | bc || echo "NA")
    all_overall_sr+=("$overall_sr")
    all_overall_vrr+=("$overall_avg_vrr")
    all_overall_tc_all+=("$overall_avg_tc_all")
    all_overall_tc_success+=("$overall_avg_tc_succ")
    all_overall_tc_positive+=("$overall_avg_tc_pos")

    echo "Run $run_id: Overall    SR=$overall_sr ($overall_success_count/$overall_task_count), VRR=$overall_avg_vrr, TC(all)=$overall_avg_tc_all, TC(success)=$overall_avg_tc_succ, TC(vrr>0)=$overall_avg_tc_pos"
    echo "Run $run_id: Single-Dim SR=$single_dim_sr ($single_dim_success_count/$single_dim_task_count), VRR=$single_dim_avg_vrr, TC(all)=$sd_avg_tc_all, TC(success)=$sd_avg_tc_succ, TC(vrr>0)=$sd_avg_tc_pos"
    echo ""
done

# ===================================================================
# Final Summary with Mean and Standard Deviation at all levels
# ===================================================================

emit_cost_block() {
    # $1 = total_cost_usd (bc-safe string), $2 = input_tokens, $3 = output_tokens,
    # $4 = attempts count.
    local total_cost="$1" in_tok="$2" out_tok="$3" n="$4"
    if [ -z "$n" ] || [ "$n" -eq 0 ]; then
        echo "  API Cost:              N/A (no cost.json written)"
        return
    fi
    local avg_cost avg_in avg_out
    avg_cost=$(echo "scale=6; $total_cost / $n" | bc)
    avg_in=$((in_tok / n))
    avg_out=$((out_tok / n))
    printf "  API Cost (total):      \$%s over %d attempts\n" "$total_cost" "$n"
    printf "  API Cost (per run):    \$%s\n" "$avg_cost"
    printf "  Tokens (per run):      input=%d, output=%d\n" "$avg_in" "$avg_out"
    printf "  Tokens (total):        input=%d, output=%d\n" "$in_tok" "$out_tok"
}

emit_level_stats() {
    # $1 = label; remaining args: tuples "sr,vrr,tcA,tcS,tcP" for each run.
    # TC fields may be "NA" when a run had no samples for that subset
    # (e.g. zero successes in that benchmark). NA entries are skipped from
    # the across-runs mean/std instead of being folded in as 0.0.
    local label="$1"; shift
    local sr_values=() vrr_values=() tca_values=() tcs_values=() tcp_values=()
    local tuple v
    for tuple in "$@"; do
        [ -z "$tuple" ] && continue
        sr_values+=("$(echo "$tuple"  | cut -d',' -f1)")
        vrr_values+=("$(echo "$tuple" | cut -d',' -f2)")
        v="$(echo "$tuple" | cut -d',' -f3)"; [ "$v" != "NA" ] && [ -n "$v" ] && tca_values+=("$v")
        v="$(echo "$tuple" | cut -d',' -f4)"; [ "$v" != "NA" ] && [ -n "$v" ] && tcs_values+=("$v")
        v="$(echo "$tuple" | cut -d',' -f5)"; [ "$v" != "NA" ] && [ -n "$v" ] && tcp_values+=("$v")
    done

    local m_sr s_sr m_vrr s_vrr
    m_sr=$(calculate_mean "${sr_values[@]}");   s_sr=$(calculate_std "${sr_values[@]}")
    m_vrr=$(calculate_mean "${vrr_values[@]}"); s_vrr=$(calculate_std "${vrr_values[@]}")

    fmt_tc() {
        local -a values=("$@")
        local n=${#values[@]}
        if [ "$n" -eq 0 ]; then
            echo "N/A (no samples)"
        else
            local m s
            m=$(calculate_mean "${values[@]}")
            s=$(calculate_std "${values[@]}")
            echo "$m +/- $s (n=$n)"
        fi
    }

    echo "$label"
    echo "  SR:                    $m_sr +/- $s_sr"
    echo "  VRR:                   $m_vrr +/- $s_vrr"
    echo "  Tool Calls (all):      $(fmt_tc "${tca_values[@]}")"
    echo "  Tool Calls (success):  $(fmt_tc "${tcs_values[@]}")"
    echo "  Tool Calls (vrr>0):    $(fmt_tc "${tcp_values[@]}")"
    echo ""
}

{
    echo "=============================================================="
    echo "PPA Evaluation - Success Rate Summary"
    echo "=============================================================="
    echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Result directory: $result_dir_abs"
    echo "Agent script: $AGENT_SCRIPT"
    echo "Temperature: $TEMPERATURE"
    echo "Number of runs: $NUM_RUNS"
    echo "Iterations: $ITERATIONS"
    echo "Total tasks per run: $grand_total_tasks"
    echo ""
    echo "Folder paths:"
    for fp in "${FOLDER_PATHS[@]}"; do
        echo "  - $fp"
    done
    echo ""

    # Per-run overall + single-dim results
    echo "=============================================================="
    echo "Per-Run Overall Results"
    echo "=============================================================="
    for i in $(seq 0 $((NUM_RUNS - 1))); do
        run_num=$((i + 1))
        echo "Run $run_num: Overall    SR=${all_overall_sr[$i]}, VRR=${all_overall_vrr[$i]}, TC(all)=${all_overall_tc_all[$i]}, TC(success)=${all_overall_tc_success[$i]}, TC(vrr>0)=${all_overall_tc_positive[$i]}"
        echo "Run $run_num: Single-Dim SR=${all_single_dim_sr[$i]}, VRR=${all_single_dim_vrr[$i]}, TC(all)=${all_single_dim_tc_all[$i]}, TC(success)=${all_single_dim_tc_success[$i]}, TC(vrr>0)=${all_single_dim_tc_positive[$i]}"
    done
    echo ""

    # Benchmark-level statistics
    echo "=============================================================="
    echo "Benchmark-Level Statistics (Mean +/- Std across $NUM_RUNS runs)"
    echo "=============================================================="
    for bench_dir in $(echo "${!all_bench_results[@]}" | tr ' ' '\n' | sort); do
        read -r -a tuples <<< "${all_bench_results[$bench_dir]}"
        emit_level_stats "Benchmark: $bench_dir" "${tuples[@]}"
        emit_cost_block "${bench_cost_total[$bench_dir]:-0.0}" \
                        "${bench_input_tokens_total[$bench_dir]:-0}" \
                        "${bench_output_tokens_total[$bench_dir]:-0}" \
                        "${bench_cost_attempts[$bench_dir]:-0}"
        echo ""
    done

    # Top-level statistics
    echo "=============================================================="
    echo "Top-Level Statistics (Mean +/- Std across $NUM_RUNS runs)"
    echo "=============================================================="
    for top_level_dir in "${FOLDER_PATHS[@]}"; do
        if [ -n "${all_toplevel_results[$top_level_dir]}" ]; then
            read -r -a tuples <<< "${all_toplevel_results[$top_level_dir]}"
            emit_level_stats "Top-Level: $top_level_dir" "${tuples[@]}"
            emit_cost_block "${toplevel_cost_total[$top_level_dir]:-0.0}" \
                            "${toplevel_input_tokens_total[$top_level_dir]:-0}" \
                            "${toplevel_output_tokens_total[$top_level_dir]:-0}" \
                            "${toplevel_cost_attempts[$top_level_dir]:-0}"
            echo ""
        fi
    done

    # Combined single_dimension statistics
    echo "=============================================================="
    echo "Combined Single-Dimension Statistics (area + power + performance)"
    echo "(Mean +/- Std across $NUM_RUNS runs)"
    echo "=============================================================="
    sd_tuples=()
    for i in $(seq 0 $((NUM_RUNS - 1))); do
        sd_tuples+=("${all_single_dim_sr[$i]},${all_single_dim_vrr[$i]},${all_single_dim_tc_all[$i]},${all_single_dim_tc_success[$i]},${all_single_dim_tc_positive[$i]}")
    done
    emit_level_stats "Single-Dimension (combined)" "${sd_tuples[@]}"
    emit_cost_block "$sd_cost_total" "$sd_input_tokens_total" "$sd_output_tokens_total" "$sd_cost_attempts"
    echo ""

    # Overall aggregate statistics
    echo "=============================================================="
    echo "Overall Aggregate Statistics (Mean +/- Std across $NUM_RUNS runs)"
    echo "=============================================================="
    ov_tuples=()
    for i in $(seq 0 $((NUM_RUNS - 1))); do
        ov_tuples+=("${all_overall_sr[$i]},${all_overall_vrr[$i]},${all_overall_tc_all[$i]},${all_overall_tc_success[$i]},${all_overall_tc_positive[$i]}")
    done
    emit_level_stats "Overall" "${ov_tuples[@]}"
    emit_cost_block "$overall_cost_total" "$overall_input_tokens_total" "$overall_output_tokens_total" "$overall_cost_attempts"
    echo ""

    # Failure stats by PPA type (pooled across runs)
    echo "=============================================================="
    echo "Failure Statistics by PPA Type (pooled across all runs)"
    echo "=============================================================="
    printf "%-20s %-11s %-14s %s\n" "PPA Type" "Failures" "Total Tasks" "Failure Rate"
    echo "--------------------------------------------------------------"
    for pt in "${!ppa_type_task_count[@]}"; do
        failures=${ppa_type_failures["$pt"]:-0}
        total=${ppa_type_task_count["$pt"]}
        if [ "$total" -gt 0 ]; then
            rate=$(echo "scale=4; $failures / $total" | bc)
        else
            rate="0.0000"
        fi
        printf "%-20s %-11d %-14d %s\n" "$pt" "$failures" "$total" "$rate"
    done | sort -k1
    echo "=============================================================="
    echo ""

    # Layer-1 trajectory metrics (pooled across all runs + attempts)
    # Reasoning / behavior signals parsed from agent_run.log by
    # analyze_trajectory.py; one trajectory_stats.json per attempt.
    echo "=============================================================="
    echo "Layer-1 Trajectory Metrics — Overall (pooled)"
    echo "=============================================================="
    python3 "$TRAJ_SUMMARIZER" "$result_dir_abs" 2>/dev/null || echo "  (summarizer failed)"
    echo ""

    echo "=============================================================="
    echo "Layer-1 Trajectory Metrics — per Top-Level"
    echo "=============================================================="
    for top_level_dir in "${FOLDER_PATHS[@]}"; do
        [ -d "$top_level_dir" ] || continue
        tl_rel=$(realpath --relative-to="$BENCHMARK_BASE_DIR" "$top_level_dir")
        # glob of result-tree paths across all runs
        tl_roots=()
        for run_dir in "$result_dir_abs"/run*/; do
            [ -d "$run_dir$tl_rel" ] && tl_roots+=("$run_dir$tl_rel")
        done
        echo ""
        echo "Top-Level: $top_level_dir"
        if [ ${#tl_roots[@]} -gt 0 ]; then
            python3 "$TRAJ_SUMMARIZER" "${tl_roots[@]}" 2>/dev/null || echo "  (summarizer failed)"
        else
            echo "  (no attempts)"
        fi
    done
    echo ""

    echo "=============================================================="
    echo "Layer-1 Trajectory Metrics — Combined Single-Dimension"
    echo "=============================================================="
    sd_roots=()
    for top_level_dir in "${FOLDER_PATHS[@]}"; do
        case "$top_level_dir" in
            */single_dimension/openroad/area*|\
            */single_dimension/openroad/power*|\
            */single_dimension/openroad/performance*)
                tl_rel=$(realpath --relative-to="$BENCHMARK_BASE_DIR" "$top_level_dir")
                for run_dir in "$result_dir_abs"/run*/; do
                    [ -d "$run_dir$tl_rel" ] && sd_roots+=("$run_dir$tl_rel")
                done
                ;;
        esac
    done
    if [ ${#sd_roots[@]} -gt 0 ]; then
        python3 "$TRAJ_SUMMARIZER" "${sd_roots[@]}" 2>/dev/null || echo "  (summarizer failed)"
    else
        echo "  (no single-dimension attempts)"
    fi
    echo ""

    # Equivalence-check outcomes (pooled across all runs)
    echo "=============================================================="
    echo "RTL Functional-Equivalence Check Outcomes (pooled)"
    echo "=============================================================="
    printf "%-20s %s\n" "Outcome" "Count"
    echo "--------------------------------------------------------------"
    for k in PASS SKIP FAIL_FUNCTIONAL FAIL_INFRA UNKNOWN; do
        printf "%-20s %d\n" "$k" "${equiv_outcome_count[$k]:-0}"
    done
    echo "=============================================================="
    echo ""

    # Individual attempt report (sorted)
    echo "=============================================================="
    echo "Individual Attempt Success/Failure Report"
    echo "=============================================================="
    printf "%s\n" "${individual_results[@]}" | sort
    echo "=============================================================="
} | tee "$final_summary_log"

echo ""
echo "Evaluation complete. Results saved to $final_summary_log"
