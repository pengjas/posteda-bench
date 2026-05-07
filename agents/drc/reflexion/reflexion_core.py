"""Shared Reflexion orchestrator for DRC agents.

Implements the verbal-reinforcement trial-and-reflect loop from Shinn et al.
(arXiv:2303.11366) on top of the ReAct primitives in agents/drc/react. Each
attempt is a full ReAct rollout that starts with FRESH conversational memory;
self-reflections accumulated from previous attempts are appended to the system
prompt as 'lessons from previous trials'.

Iteration accounting:
  - The user-visible --iterations budget is split evenly across
    --num_attempts trials (iters_per_attempt = iterations // num_attempts).
  - The Self-Reflection LLM call between trials is tool-less and does NOT
    consume any iterations.
  - Cumulative tool-call count and token cost are aggregated across trials so
    eval_drc_sr.sh sees a single 'AGENT RUN COMPLETE. TOTAL TOOL CALLS: N'
    line and one cost.json. The eval-script's per-tool grep
    (`^--- TOOL CALL: Invoking tool 'X'`) likewise picks up calls from every
    trial, so the Tool Usage Breakdown table is the cumulative across all
    reflexion trials of one task.

Sandbox handling:
  - eval_drc_sr.sh creates a fresh work_dir per task and copies the task
    contents into it before invoking the agent, so trial 1 always starts
    clean. Before trial 1 the orchestrator snapshots the entire sandbox
    to $AGENT_RESULT_DIR/_reflexion_snapshot/; before every subsequent
    trial we wipe the sandbox and restore from that snapshot, so each
    trial sees the *original* task content rather than whatever the
    previous trial mangled. The snapshot is removed at the end of the run.
    This matches the canonical Reflexion semantics: the only thing carried
    across trials is the verbal reflection (long-term memory), not the
    environment state.
  - The snapshot lives under $AGENT_RESULT_DIR (one level above the agent's
    CWD), so DRC tools that operate on relative paths inside the sandbox
    never see it.
  - Inter-trial success evaluation uses the query_drc_report tool (also
    what the Actor itself uses), so the orchestrator interacts with the
    sandbox only through the published DRC tool surface; the snapshot/
    restore step operates one level up at the harness layer.
"""

import os
import json
import shutil
import time
import traceback
import multiprocessing
from typing import Annotated, TypedDict
from typing_extensions import NotRequired

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.errors import GraphRecursionError

from agents.drc.tools import query_drc_report


# Cumulative tool-call count for the entire reflexion run (all attempts pooled).
# Mirrors the global counter used by the React agents so eval_drc_sr.sh's
# `tail -n 1` grep against 'TOTAL TOOL CALLS:' picks up the right number.
tool_call_counter = {"count": 0}


# Tool timeout (s) used by every DRC tool — query_drc_report and query_with_pts
# both spawn klayout subprocesses that occasionally take a few minutes on dense
# layouts. Matches the value hardcoded in gpt_react.py / gemma_react.py.
TOOL_TIMEOUT_SECONDS = 1200


# Filenames the eval script expects in the sandbox CWD after the agent ends.
# We use modified.gds for inter-trial DRC re-checks because the React agents
# already write there and eval_drc_sr.sh's `mv ./modified.gds ...` cementing
# the convention.
MODIFIED_GDS_PATH = "./modified.gds"


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    drc_errors: Annotated[list, add_messages]
    output_gds: NotRequired[str]
    query_change_args: NotRequired[dict]
    tool_call_count: NotRequired[int]


def make_call_llm(llm_with_tools, cost_tracker_module):
    """Factory for the LangGraph 'call_agent' node, parameterized by which
    cost-tracker callable is in use (cost_tracking.record_usage for OpenAI,
    vllm_cost_tracker.record_langchain_usage for vLLM)."""

    def call_llm(state):
        print("\n--- CALL LLM: call_llm ---", flush=True)
        print(state["messages"][-1], flush=True)
        try:
            response = llm_with_tools.invoke(
                state["messages"],
                config={"request_timeout": 400.0},
            )
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] LLM call successful!", flush=True)
            cost_tracker_module(response)
            print("=================================RESPONSE==================================", flush=True)
            print(response, flush=True)
            print("=================================RESPONSE==================================", flush=True)
            return {"messages": [response]}
        except Exception as e:
            print(f"\n\n!!!!!!!!!!!!!!!!!!!!  ERROR: UNKNOWN  !!!!!!!!!!!!!!!!!!!!", flush=True)
            print(f"Unexpected error during LLM call:", flush=True)
            print(f"Error Type: {type(e)}", flush=True)
            print(f"Error Details: {e}", flush=True)
            traceback.print_exc()
            print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n\n", flush=True)
            raise

    return call_llm


class BasicToolNode:
    """Runs the requested DRC tools in isolated processes (timeout-safe).

    Preserves the query_with_pts <-> change_size guard from the React agents:
    if the agent calls change_size with args that disagree with the most
    recent query_with_pts call, the tool result is replaced with a helpful
    error so the agent can re-query before modifying.
    """

    def __init__(self, tools):
        self.tools_by_name = {t.name: t for t in tools}

    def _run_tool(self, tool, args, return_dict):
        try:
            result = tool.invoke(args)
            return_dict["result"] = json.dumps(result)
        except Exception:
            return_dict["error"] = traceback.format_exc()

    def __call__(self, inputs):
        if messages := inputs.get("messages", []):
            message = messages[-1]
        else:
            raise ValueError("No message found in input")

        outputs = []
        query_change_args = None
        agent_state = {}

        for tool_call in message.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            print(f"\n--- TOOL CALL: Invoking tool '{tool_name}' with args: {tool_args} ---", flush=True)

            tool = self.tools_by_name.get(tool_name)
            if tool is None:
                print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: UNKNOWN TOOL  !!!!!!!!!!!!!!!!!!!!", flush=True)
                outputs.append(ToolMessage(
                    content=f"Error: Unknown tool '{tool_name}'.",
                    name=tool_name,
                    tool_call_id=tool_call["id"],
                ))
                continue

            manager = multiprocessing.Manager()
            return_dict = manager.dict()
            p = multiprocessing.Process(
                target=self._run_tool, args=(tool, tool_args, return_dict)
            )
            p.start()
            print(f"--- Waiting for '{tool_name}' (max {TOOL_TIMEOUT_SECONDS}s)... ---", flush=True)
            p.join(timeout=TOOL_TIMEOUT_SECONDS)

            if p.is_alive():
                print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!", flush=True)
                print(f"Tool '{tool_name}' exceeded {TOOL_TIMEOUT_SECONDS} seconds and will be terminated.", flush=True)
                p.terminate()
                p.join()
                content = f"Error: Tool '{tool_name}' timed out after {TOOL_TIMEOUT_SECONDS} seconds and was terminated."
            else:
                if "error" in return_dict:
                    print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TOOL FAILED  !!!!!!!!!!!!!!!!!!!!", flush=True)
                    print(f"Tool '{tool_name}' failed with error:\n{return_dict['error']}", flush=True)
                    content = f"Error: Tool '{tool_name}' failed. Exception Traceback:\n{return_dict['error']}"
                else:
                    print(f"--- Tool '{tool_name}' finished successfully. ---", flush=True)
                    content = return_dict.get("result", "{}")

            outputs.append(ToolMessage(
                content=content,
                name=tool_name,
                tool_call_id=tool_call["id"],
            ))

            # Same query_change_args guard as the React agents.
            if tool_name == "change_size":
                prev = inputs.get("query_change_args", {}) or {}
                for key, value in prev.items():
                    if value != tool_args.get(key):
                        return {
                            "messages": ToolMessage(
                                content=(
                                    "The change you want to make is not aligned with the last query. "
                                    "Please make sure the changed entity is the one that was queried in the last step."
                                ),
                                name="error",
                            )
                        }
            elif tool_name == "query_with_pts":
                query_change_args = tool_args

        agent_state["messages"] = outputs
        if query_change_args:
            agent_state["query_change_args"] = query_change_args

        current_count = inputs.get("tool_call_count", 0)
        new_count = current_count + len(message.tool_calls)
        agent_state["tool_call_count"] = new_count
        tool_call_counter["count"] = new_count
        return agent_state


def should_continue(state):
    last = state["messages"][-1]
    return "continue" if last.tool_calls else "end_flow"


def build_graph(llm_with_tools, tools, cost_recorder):
    call_llm = make_call_llm(llm_with_tools, cost_recorder)
    tool_node = BasicToolNode(tools)
    workflow = StateGraph(AgentState)
    workflow.add_node("call_agent", call_llm)
    workflow.add_node("call_tools", tool_node)
    workflow.add_edge("call_tools", "call_agent")
    workflow.add_conditional_edges(
        "call_agent",
        should_continue,
        {"continue": "call_tools", "end_flow": END},
    )
    workflow.set_entry_point("call_agent")
    workflow.set_finish_point("call_agent")
    return workflow.compile()


def _sandbox_root():
    """The work_dir the eval script `cd`'d the agent into. DRC eval doesn't
    export an env var like PPA does, so we just use CWD."""
    return os.getcwd()


def _snapshot_dir():
    """Hidden snapshot directory we write to outside the sandbox so the
    DRC tools (which operate on paths relative to CWD) can never
    accidentally read or modify it."""
    result_dir = os.environ.get("AGENT_RESULT_DIR", ".")
    return os.path.join(result_dir, "_reflexion_snapshot")


def _wipe_sandbox(sandbox_root):
    """Remove everything inside `sandbox_root` (but keep the directory
    itself). Symlinks are unlinked without following so we never delete
    files outside the sandbox."""
    if not os.path.isdir(sandbox_root):
        return
    for entry in os.listdir(sandbox_root):
        path = os.path.join(sandbox_root, entry)
        if os.path.islink(path):
            try:
                os.unlink(path)
            except OSError:
                pass
        elif os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            try:
                os.remove(path)
            except OSError:
                pass


def snapshot_sandbox():
    """Copy the entire current sandbox tree to $AGENT_RESULT_DIR/_reflexion_snapshot/.
    Called once before trial 1 so we can restore-to-original between trials."""
    src = _sandbox_root()
    dst = _snapshot_dir()
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    if not os.path.isdir(src):
        print(f"[reflexion] WARNING: sandbox {src} does not exist; cannot snapshot.", flush=True)
        return False
    try:
        shutil.copytree(src, dst, symlinks=False)
        print(f"[reflexion] snapshotted sandbox -> {dst}", flush=True)
        return True
    except Exception as e:
        print(f"[reflexion] warn: snapshot failed ({src} -> {dst}): {e}", flush=True)
        return False


def restore_sandbox():
    """Wipe the sandbox and replace its contents with the snapshot taken
    before trial 1. Used between reflexion trials to give each trial the
    original task state."""
    sandbox_root = _sandbox_root()
    snap = _snapshot_dir()
    if not os.path.isdir(snap):
        print(
            f"[reflexion] WARNING: snapshot dir {snap} not found; skipping "
            f"restore. Trial 2+ will inherit trial 1's sandbox state.",
            flush=True,
        )
        return False
    print(f"[reflexion] restoring sandbox from {snap} -> {sandbox_root}", flush=True)
    _wipe_sandbox(sandbox_root)
    for entry in os.listdir(snap):
        src = os.path.join(snap, entry)
        dst = os.path.join(sandbox_root, entry)
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst, symlinks=False)
            elif os.path.isfile(src):
                shutil.copy2(src, dst)
        except Exception as e:
            print(f"[reflexion] warn: restore copy {src} -> {dst}: {e}", flush=True)
    return True


def cleanup_snapshot():
    """Remove the snapshot directory at end of run so it doesn't pollute
    $AGENT_RESULT_DIR. Failures are non-fatal."""
    snap = _snapshot_dir()
    try:
        if os.path.isdir(snap):
            shutil.rmtree(snap, ignore_errors=True)
    except Exception:
        pass


def _evaluate_attempt_isolated(return_dict):
    """Subprocess body for evaluate_attempt. Runs the inter-trial
    query_drc_report call in its own process so a klayout child that hangs
    on a corrupted modified.gds can be killed cleanly. Mirrors the
    isolation pattern in BasicToolNode."""
    try:
        if not os.path.isfile(MODIFIED_GDS_PATH):
            return_dict["result"] = (False, (
                f"No {MODIFIED_GDS_PATH} produced — the agent never wrote a "
                f"modified GDS, so the trial cannot have cleared any DRC errors."
            ))
            return
        report = query_drc_report.invoke({
            "gds_file_path": MODIFIED_GDS_PATH,
            "rerun_drc_check": True,
        })
        text = report if isinstance(report, str) else json.dumps(report, default=str)
        success = "Congrats! No DRC errors found" in (text or "")
        return_dict["result"] = (success, (text or ""))
    except Exception as e:
        return_dict["error"] = traceback.format_exc()
        return_dict["fallback"] = (False, f"Evaluator error invoking query_drc_report: {e}")


def evaluate_attempt():
    """Inter-trial DRC success check.

    Convention from eval_drc_sr.sh: the agent is expected to leave a
    ./modified.gds in the sandbox CWD. We re-run query_drc_report against
    it (same tool the Actor uses for self-verification) and read the
    klayout-printed message. 'Congrats! No DRC errors found' is the exact
    string emitted by the embedded klayout script when total_errors_count
    == 0, so it's a reliable success signal.

    The query_drc_report call is wrapped in multiprocessing.Process with the
    same TOOL_TIMEOUT_SECONDS budget BasicToolNode uses for the Actor: a
    klayout child that hangs on a corrupted modified.gds gets terminated
    cleanly instead of taking the whole reflexion run down with it (the
    direct in-process invoke previously had no hard timeout — a hung
    klayout would block the whole agent until eval_drc_sr.sh was killed
    externally).

    Returns (success: bool, summary_text: str).
    """
    manager = multiprocessing.Manager()
    return_dict = manager.dict()
    p = multiprocessing.Process(
        target=_evaluate_attempt_isolated, args=(return_dict,)
    )
    p.start()
    print(
        f"--- [reflexion] Waiting for evaluate_attempt's query_drc_report "
        f"(max {TOOL_TIMEOUT_SECONDS}s)... ---",
        flush=True,
    )
    p.join(timeout=TOOL_TIMEOUT_SECONDS)

    if p.is_alive():
        print(
            f"!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!\n"
            f"evaluate_attempt's query_drc_report exceeded "
            f"{TOOL_TIMEOUT_SECONDS}s; terminating to avoid hanging the "
            f"whole reflexion run.",
            flush=True,
        )
        p.terminate()
        p.join()
        return False, (
            f"Evaluator timed out after {TOOL_TIMEOUT_SECONDS}s running "
            f"query_drc_report on {MODIFIED_GDS_PATH}."
        )

    if "result" in return_dict:
        return return_dict["result"]
    if "fallback" in return_dict:
        print(
            f"[reflexion] evaluate_attempt errored in subprocess:\n"
            f"{return_dict.get('error', '<no traceback>')}",
            flush=True,
        )
        return return_dict["fallback"]
    return False, "Evaluator subprocess returned no result (crashed silently)"


def summarize_trajectory(messages):
    """Compact one-paragraph summary of the trial's tool-call timeline. Used
    as the Reflexion 'τ_t' input to the Self-Reflection LLM. We cap to the
    last 80 events and truncate long payloads so the reflection prompt stays
    within a sane token budget on long trajectories."""
    lines = []
    n_calls = 0
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                n_calls += 1
                args_repr = json.dumps(tc.get("args", {}), default=str)
                if len(args_repr) > 240:
                    args_repr = args_repr[:240] + "...<truncated>"
                lines.append(f"  - {tc.get('name')}({args_repr})")
        elif isinstance(m, ToolMessage):
            content = (m.content or "").strip().split("\n", 1)[0]
            if len(content) > 280:
                content = content[:280] + "...<truncated>"
            lines.append(f"     -> [{m.name}] {content}")
    head = f"Total tool calls: {n_calls}"
    return head + "\n" + "\n".join(lines[-80:])


REFLECTION_SYSTEM_PROMPT = (
    "You are the Self-Reflection module of a Reflexion agent for DRC "
    "(Design Rule Checking) error fixing. The Actor agent just finished a "
    "trial of fixing a layout's DRC violations and FAILED (errors remain "
    "or no modified GDS was produced). Read the trial summary and the "
    "final DRC report, then produce a concise, actionable critique that "
    "the next trial's Actor will read as 'lessons from the previous attempt'.\n"
    "\n"
    "Cover, in this order:\n"
    "  1. Diagnostic gaps: which query_with_pts levels / query_drc_report "
    "categories were skipped that would have revealed the actual cause of "
    "the violation (spacing? overlap? minimum-area?).\n"
    "  2. Why the fix attempts failed: which move_cell / change_shape / "
    "add_shape calls did NOT clear the violation (or introduced new ones), "
    "and the geometric reason why.\n"
    "  3. Concrete next-trial strategy in priority order: what to query "
    "first, what spatial change to make, exact direction/distance/layer "
    "to use. The sandbox is reset to the original task input before the "
    "next trial — there is NO leftover modified.gds from this attempt to "
    "revert; do not include 'revert previous edit' steps.\n"
    "\n"
    "Do NOT call tools. Output 8-15 lines of plain text. Be specific (cite "
    "cell names, layer names, coordinates, error categories). Avoid generic "
    "advice."
)


def generate_reflection(llm, task_prompt, summary, eval_report, cost_recorder):
    """Run the tool-less Self-Reflection LLM call. Token usage is recorded but
    no tools are bound, so this step is invisible to iteration accounting."""
    sys_msg = SystemMessage(content=REFLECTION_SYSTEM_PROMPT)
    user_msg = HumanMessage(content=(
        f"=== Original DRC error report (task) ===\n{task_prompt}\n\n"
        f"=== Actor trajectory summary ===\n{summary}\n\n"
        f"=== Final query_drc_report after the trial ===\n{eval_report}\n\n"
        f"Write the lessons-learned reflection now."
    ))
    try:
        resp = llm.invoke([sys_msg, user_msg], config={"request_timeout": 400.0})
        cost_recorder(resp)
        text = resp.content if hasattr(resp, "content") else str(resp)
        if isinstance(text, list):
            parts = []
            for p in text:
                if isinstance(p, dict):
                    parts.append(p.get("text", ""))
                else:
                    parts.append(str(p))
            text = " ".join(parts)
        return (text or "").strip()
    except Exception as e:
        traceback.print_exc()
        return f"(reflection generation failed: {e})"


def run_reflexion(
    llm,
    llm_with_tools,
    tools,
    system_prompt_content,
    task_prompt,
    iterations,
    num_attempts,
    cost_recorder,
):
    """Drive the multi-attempt reflexion loop.

    Args:
      llm:               base LLM (no tools bound) — used for the tool-less
                         self-reflection LLM call.
      llm_with_tools:    the tool-bound LLM used by the ReAct rollout.
      tools:             list of langchain @tool functions to bind into the
                         ToolNode (must match the names the LLM sees).
      cost_recorder:     callable(response) -> None. For DRC OpenAI models
                         this is `cost_tracking.record_usage`; for vLLM it's
                         `vllm_cost_tracker.record_langchain_usage`.

    Returns (status_str, total_tool_calls). status_str is "COMPLETE" if every
    attempt finished cleanly, "RECURSION LIMIT REACHED" if any attempt
    exhausted its per-attempt iteration budget without ending. The eval
    script's tool-count parser greps for this on the LAST matching line.
    """
    result_dir = os.environ.get("AGENT_RESULT_DIR", ".")

    num_attempts = max(1, int(num_attempts))
    iters_per_attempt = max(1, int(iterations) // num_attempts)
    print(
        f"[reflexion] iterations={iterations}, num_attempts={num_attempts}, "
        f"iters_per_attempt={iters_per_attempt}",
        flush=True,
    )

    # Snapshot the eval-prepared sandbox once, so every trial after trial 1
    # can be restored to the original task state without help from the eval
    # script. No-op (with warning) if num_attempts == 1.
    if num_attempts > 1:
        snapshot_sandbox()

    graph = build_graph(llm_with_tools, tools, cost_recorder)

    reflections = []
    cumulative_tool_count = 0
    recursion_limit_reached = False
    success = False
    last_eval_report = ""
    attempts_run = 0

    for attempt_idx in range(num_attempts):
        attempt_num = attempt_idx + 1
        attempts_run = attempt_num
        sep = "=" * 80
        print(f"\n{sep}", flush=True)
        print(
            f"REFLEXION ATTEMPT {attempt_num}/{num_attempts} "
            f"(budget={iters_per_attempt} iters)",
            flush=True,
        )
        print(f"{sep}", flush=True)

        if attempt_idx > 0:
            restore_sandbox()

        if reflections:
            reflection_block = "\n\n".join(
                f"--- Reflection from attempt {i + 1} ---\n{r}"
                for i, r in enumerate(reflections)
            )
            attempt_system = (
                system_prompt_content.rstrip()
                + "\n\n=== LESSONS FROM PREVIOUS ATTEMPTS ===\n"
                + reflection_block
                + "\n=== END LESSONS ===\n"
                + "Apply these lessons. Do NOT repeat the strategies that "
                "failed above; start from the highest-priority next-trial "
                "strategy. The sandbox has been reset to the original task "
                "input — there is no leftover modified.gds from previous "
                "attempts to revert."
            )
        else:
            attempt_system = system_prompt_content

        initial_state = {
            "messages": [
                SystemMessage(content=attempt_system),
                HumanMessage(content=task_prompt),
            ],
            "drc_errors": [task_prompt],
            "tool_call_count": cumulative_tool_count,
        }

        recursion_limit = iters_per_attempt * 2 + 1

        # Drive the graph via stream() instead of invoke() so we can
        # accumulate the trajectory message-by-message. invoke() raises
        # GraphRecursionError without returning the partial state, which
        # made the post-attempt summarize_trajectory() see zero events
        # (the most common failure mode of Reflexion is exactly this case)
        # and produced reflections that wrongly claimed "the Actor performed
        # zero tool calls" when in reality the actor made a full N-step run
        # before the recursion cap fired.
        attempt_msgs = list(initial_state["messages"])

        def _ingest_state_update(update):
            """Apply one node's state delta to our local trajectory mirror."""
            nonlocal cumulative_tool_count
            if not isinstance(update, dict):
                return
            new_msgs = update.get("messages")
            if new_msgs is not None:
                if isinstance(new_msgs, list):
                    attempt_msgs.extend(new_msgs)
                else:
                    attempt_msgs.append(new_msgs)
            new_count = update.get("tool_call_count")
            if new_count is not None:
                cumulative_tool_count = new_count

        try:
            for chunk in graph.stream(
                initial_state,
                {"recursion_limit": recursion_limit},
                stream_mode="updates",
            ):
                # chunk is {node_name: state_update_dict}
                if isinstance(chunk, dict):
                    for _node_name, node_update in chunk.items():
                        _ingest_state_update(node_update)
        except GraphRecursionError as e:
            recursion_limit_reached = True
            cumulative_tool_count = tool_call_counter["count"]
            print(f"\n--- ATTEMPT {attempt_num} HIT RECURSION LIMIT ---\n{e}", flush=True)
        except Exception as e:
            cumulative_tool_count = tool_call_counter["count"]
            print(f"\n--- ATTEMPT {attempt_num} ERRORED: {e} ---", flush=True)
            traceback.print_exc()

        success, last_eval_report = evaluate_attempt()
        print(f"\n[reflexion] attempt {attempt_num} success={success}", flush=True)
        # Eval reports for DRC can be very long; cap stdout dump for log readability.
        eval_dump = last_eval_report if len(last_eval_report) < 4000 else (
            last_eval_report[:4000] + "\n...<eval_report truncated>..."
        )
        print(f"[reflexion] eval report:\n{eval_dump}", flush=True)

        if success:
            print(f"[reflexion] all DRC errors cleared — stopping early.", flush=True)
            break

        if attempt_idx + 1 < num_attempts:
            print(
                f"[reflexion] generating self-reflection for attempt {attempt_num} "
                f"(does not consume the iteration budget)...",
                flush=True,
            )
            summary = summarize_trajectory(attempt_msgs)
            reflection = generate_reflection(
                llm, task_prompt, summary, last_eval_report, cost_recorder
            )
            reflections.append(reflection)
            print(f"\n--- REFLECTION (attempt {attempt_num}) ---", flush=True)
            print(reflection, flush=True)
            print(f"--- END REFLECTION ---\n", flush=True)

    try:
        with open(os.path.join(result_dir, "reflections.json"), "w") as f:
            json.dump(
                {
                    "reflections": reflections,
                    "attempts_run": attempts_run,
                    "num_attempts_configured": num_attempts,
                    "iterations_per_attempt": iters_per_attempt,
                    "iterations_total": iterations,
                    "final_success": bool(success),
                    "total_tool_calls": cumulative_tool_count,
                },
                f,
                indent=2,
            )
    except Exception as e:
        print(f"[reflexion] warning: could not write reflections.json: {e}", flush=True)

    cleanup_snapshot()

    status = "RECURSION LIMIT REACHED" if recursion_limit_reached else "COMPLETE"
    return status, cumulative_tool_count
