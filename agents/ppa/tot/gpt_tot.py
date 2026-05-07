"""Tree-of-Thoughts PPA optimization agent (GPT-5).

Wraps the same tool surface as gpt_react.py with a ToT search
(Yao et al., arXiv:2305.10601):

  expand(node) -> sample K candidate next actions in parallel
              -> LLM judge scores each candidate 1..10
              -> push (parent, candidate, score) into a global priority queue
  step()      -> pop highest-scored (parent, candidate)
              -> if parent != current sandbox state, restore parent's snapshot
              -> execute the candidate's tool calls (counts against the budget)
              -> snapshot the resulting state as a new tree node
              -> expand the new node

Backtracking falls out for free: when the current path stalls, the next pop
naturally goes to the highest-scored un-executed candidate of any ancestor.

Iteration semantics:
  --iterations is the TOTAL tool-call budget across the whole tree (matches
  ReAct's 5 runs * 16 iters = 80 total when invoked with --iterations 80).
  --max_depth caps the depth of any single path (default 16, matching ReAct's
  per-run iteration cap). The K candidate sampling + 1 scoring LLM call per
  expansion are billed in tokens but do NOT consume the iteration budget;
  only tool calls do.

CLI surface and end-of-run log line ('AGENT RUN COMPLETE. TOTAL TOOL CALLS: N')
are kept identical to gpt_react.py so eval_ppa_sr.sh can swap agents without
modification, and so analyze_trajectory.py / summarize_trajectory_stats.py
emit Layer-1 metrics with no changes.
"""

import os
import argparse
import datetime

from langchain.chat_models import init_chat_model

from agents.ppa.tools import (
    read_file,
    list_files,
    edit_file,
    write_file,
    run_openroad_flow,
    report_ppa,
)
from agents.ppa.react import cost_tracker
from agents.ppa.tot.tot_core import (
    run_tot,
    tool_call_counter,
)


SYSTEM_PROMPT = """
You are an expert digital design engineer diagnosing and fixing PPA (Power, Performance, Area) violations in OpenROAD flow. Your goal is to find ROOT CAUSES and apply targeted fixes, not randomly tune parameters.

**Target Metrics (from task prompt):**
- `target_effective_period`: Clock period (ps)
- `target_power`: Total power (W)
- `target_area`: Die area (µm²)

**Diagnostic Workflow:**

**1. ROOT CAUSE ANALYSIS (Start Here)**
   - Use `report_ppa()` to identify which metrics violate targets
   - Examine logs and reports to understand WHY:
     - `read_file(command="cat logs/6_report.json")` - design statistics
     - `read_file(command="tail -100 logs/synth/6_1_report.log")` - synthesis details
     - `read_file(command="grep -i 'error\\|warning\\|critical' logs/...")` - issues
   - Analyze intermediate results to find bottlenecks
   - Form hypothesis about the root cause before making changes

**2. IDENTIFY SOLUTION**
   Multiple fix strategies available:
   - **Config parameters** (FlowVariables.md): Flow settings, density, buffers, etc.
   - **Timing constraints** (config/*.sdc): Clock period, I/O delays, false paths
   - **Design files** (src/*.v): RTL optimizations if design issues found
   - **Other configs**: Any file affecting the design flow

**3. VERIFY SOLUTION VIABILITY**
   - If changing config parameters: `read_file(command="grep -A 10 'PARAM_NAME' FlowVariables.md")`
   - Understand: What does it affect? Valid range? Side effects?
   - Confirm it addresses your diagnosed root cause

**4. APPLY FIX**
   - `edit_file(file_path="...", old_content="...", new_content="...")`
   - Make targeted changes based on root cause analysis

**5. VERIFY FIX**
   - `run_openroad_flow(target="finish")`
   - `report_ppa()` to check if violation is resolved
   - If unsuccessful, analyze why and try different approach

**CRITICAL RULES:**
- DO NOT blindly tune parameters hoping something works
- DO NOT skip root cause analysis
- DO investigate logs/reports before making changes
- DO verify parameter meanings if modifying FlowVariables
- DO consider all fix options (not just config parameters)

**Success:** actual_period <= target_effective_period AND actual_power <= target_power AND actual_area <= target_area

Think like a debug engineer: diagnose, hypothesize, fix, verify. Call tools one at a time.
"""


if __name__ == "__main__":
    separator = "=" * 80
    print(f"\n\n\n{separator}")
    print("PPA Tree-of-Thoughts Agent Run - New Conversation Initialized")
    print(f"Timestamp: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{separator}\n")

    parser = argparse.ArgumentParser(description="run the PPA Tree-of-Thoughts agent")
    parser.add_argument("--prompt_path", type=str, required=True,
                        help="Initial prompt/task description path")
    parser.add_argument("--iterations", type=int, default=80,
                        help="TOTAL tool-call budget across the whole tree "
                             "(default 80 = ReAct's 5 runs * 16 iters).")
    parser.add_argument("--temperature", type=float, default=0.7,
                        help="Temperature for the judge / non-sampling LLM calls.")
    parser.add_argument("--parallel_node", type=int, default=3,
                        help="K: candidates sampled at each tree expansion.")
    parser.add_argument("--max_depth", type=int, default=16,
                        help="Max depth for any single path through the tree.")
    parser.add_argument("--sample_temperature", type=float, default=0.9,
                        help="Temperature used ONLY when sampling the K candidates "
                             "(forced above 0 so candidates actually differ when "
                             "the eval harness passes --temperature 0).")
    args = parser.parse_args()

    with open(args.prompt_path, "r") as f:
        task_prompt = f.read()

    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY env var is not set")

    # Two LLM handles:
    #   judge_llm uses --temperature so the candidate scorer is closer to the
    #     eval-harness's intended determinism.
    #   sampler_llm forces a non-zero temperature so the K parallel samples in
    #     each expansion actually differ even when the eval passes
    #     --temperature 0.
    judge_llm = init_chat_model(
        model="gpt-5",
        temperature=args.temperature,
        model_provider="openai",
        timeout=400.0,
    )
    sample_temp = max(args.sample_temperature, args.temperature, 0.7)
    sampler_llm = init_chat_model(
        model="gpt-5",
        temperature=sample_temp,
        model_provider="openai",
        timeout=400.0,
    )

    tools = [
        read_file,
        list_files,
        edit_file,
        write_file,
        run_openroad_flow,
        report_ppa,
    ]
    sampler_llm_with_tools = sampler_llm.bind_tools(tools)

    tool_call_counter["count"] = 0
    cost_tracker.reset()

    status = "COMPLETE"
    total_tool_calls = 0
    try:
        status, total_tool_calls = run_tot(
            judge_llm=judge_llm,
            sampler_llm_with_tools=sampler_llm_with_tools,
            tools=tools,
            system_prompt_content=SYSTEM_PROMPT,
            task_prompt=task_prompt,
            iterations=args.iterations,
            parallel_node=args.parallel_node,
            max_depth=args.max_depth,
            cost_tracker_module=cost_tracker,
        )
    except Exception as e:
        print(f"\n{separator}")
        print("AGENT RUN FAILED DUE TO ERROR:")
        print(str(e))
        print(f"{separator}\n", flush=True)
        total_tool_calls = tool_call_counter["count"]

    finally:
        model_name = (
            getattr(judge_llm, "model_name", None)
            or getattr(judge_llm, "model", "unknown")
        )
        cost_data = cost_tracker.save_cost_json(model_name)
        print(f"\n{separator}")
        print(f"AGENT RUN {status}. TOTAL TOOL CALLS: {total_tool_calls}")
        print(
            f"TOKEN USAGE: input={cost_data['input_tokens']}, "
            f"output={cost_data['output_tokens']}, total={cost_data['total_tokens']}"
        )
        print(
            f"ESTIMATED COST: ${cost_data['total_cost_usd']:.6f} "
            f"(input=${cost_data['input_cost_usd']:.6f}, output=${cost_data['output_cost_usd']:.6f})"
        )
        print(f"{separator}\n", flush=True)
