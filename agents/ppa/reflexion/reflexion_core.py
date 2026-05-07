"""Shared Reflexion orchestrator for PPA agents.

Implements the verbal-reinforcement trial-and-reflect loop from Shinn et al.
(arXiv:2303.11366) on top of the ReAct primitives in agents/ppa/react. Each
attempt is a full ReAct rollout that starts with FRESH conversational memory;
self-reflections accumulated from previous attempts are appended to the system
prompt as 'lessons from previous trials'.

Iteration accounting:
  - The user-visible --iterations budget is split evenly across
    --num_attempts trials (iters_per_attempt = iterations // num_attempts).
  - The Self-Reflection LLM call between trials is tool-less and does NOT
    consume any iterations.
  - Cumulative tool-call count and token cost are aggregated across trials so
    eval_ppa_sr.sh sees a single 'AGENT RUN COMPLETE. TOTAL TOOL CALLS: N'
    line and one cost.json, matching the React-agent contract.

Sandbox handling:
  - eval_ppa_sr.sh creates a fresh work_dir per task and copies the original
    task contents into it before invoking the agent, so trial 1 always
    starts clean. Before trial 1 the orchestrator snapshots the entire
    sandbox to $AGENT_RESULT_DIR/_reflexion_snapshot/; before every
    subsequent trial we wipe the sandbox and restore from that snapshot,
    so each trial sees the *original* task content rather than whatever
    the previous trial mangled. The snapshot is removed at the end of
    the run. This matches the canonical Reflexion semantics: the only
    thing carried across trials is the verbal reflection (long-term
    memory), not the environment state.
  - The snapshot lives outside the sandbox (under $AGENT_RESULT_DIR) so
    the agent's tool surface (which is rooted at $PPA_SANDBOX_ROOT) never
    sees it, and the eval script's existing artifact-copy logic runs
    against the final-trial sandbox state without any harness changes.
  - Inter-trial success evaluation uses the report_ppa tool, so the
    orchestrator interacts with the sandbox only through the published
    PPA tool surface; the snapshot/restore step operates one level up at
    the harness layer.
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

from agents.ppa.tools import report_ppa


# Cumulative tool-call count for the entire reflexion run (all attempts pooled).
# Mirrors the global counter used by the React agents so eval_ppa_sr.sh's
# `tail -n 1` grep against 'TOTAL TOOL CALLS:' picks up the right number.
tool_call_counter = {"count": 0}


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    ppa_violations: Annotated[list, add_messages]
    tool_call_count: NotRequired[int]


def _maybe_get_openai_conn_err():
    """Return openai.APIConnectionError if openai is importable, else a sentinel
    that won't match anything. Lets the call_llm retry transient vLLM blips
    without forcing openai as a hard dependency."""
    try:
        import openai
        return openai.APIConnectionError
    except Exception:
        class _Never(Exception):
            pass
        return _Never


def make_call_llm(llm_with_tools, cost_tracker_module):
    APIConnectionError = _maybe_get_openai_conn_err()

    def call_llm(state):
        print("\n--- CALL LLM: call_llm ---", flush=True)
        messages = state["messages"]

        recent_tool_count = 0
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                recent_tool_count = len(msg.tool_calls)
                break

        if recent_tool_count > 0:
            recent_tool_msgs = []
            for msg in reversed(messages):
                if isinstance(msg, ToolMessage):
                    recent_tool_msgs.insert(0, msg)
                    if len(recent_tool_msgs) >= recent_tool_count:
                        break
                elif isinstance(msg, AIMessage):
                    break
            if recent_tool_msgs:
                print(f"--- Received {len(recent_tool_msgs)} tool result(s) ---", flush=True)
                for tm in recent_tool_msgs:
                    print(f"  [{tm.name}]: {tm.content}", flush=True)
        else:
            print(messages[-1], flush=True)

        # Same retry schedule as gemma_react.py — only retries transient
        # connection-level failures (vLLM restart / port hiccup); other
        # errors propagate so the agent fails loudly on real bugs.
        backoff_schedule = [10, 30, 60, 90, 120]
        max_conn_retries = len(backoff_schedule) + 1

        try:
            response = None
            for conn_attempt in range(max_conn_retries):
                try:
                    response = llm_with_tools.invoke(
                        messages,
                        config={"request_timeout": 400.0},
                    )
                    break
                except APIConnectionError as conn_err:
                    if conn_attempt == max_conn_retries - 1:
                        raise
                    wait_s = backoff_schedule[conn_attempt]
                    print(
                        f"!!! LLM endpoint unreachable (attempt {conn_attempt + 1}/{max_conn_retries}), "
                        f"retrying in {wait_s}s: {conn_err}",
                        flush=True,
                    )
                    time.sleep(wait_s)

            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] LLM call successful!", flush=True)
            cost_tracker_module.record_langchain_usage(response)
            print("=================================RESPONSE==================================", flush=True)
            print(response, flush=True)
            print("=================================RESPONSE==================================", flush=True)
            return {"messages": [response]}

        except Exception as e:
            print(f"\n\n!!!!!!!!!!!!!!!!!!!!  ERROR: LLM CALL FAILED  !!!!!!!!!!!!!!!!!!!!", flush=True)
            print(f"Error Type: {type(e)}", flush=True)
            print(f"Error Details: {e}", flush=True)
            traceback.print_exc()
            print(f"!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!\n\n", flush=True)
            raise

    return call_llm


class BasicToolNode:
    """Mirrors react/BasicToolNode: runs the requested tools in isolated
    processes so a single hung tool can't take the whole agent down."""

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

            tool_timeout = int(os.environ.get("TOOL_TIMEOUT", 1800))
            timeout = (tool_timeout + 60) if tool_name == "run_openroad_flow" else 300

            manager = multiprocessing.Manager()
            return_dict = manager.dict()
            p = multiprocessing.Process(
                target=self._run_tool, args=(tool, tool_args, return_dict)
            )
            p.start()
            print(f"--- Waiting for '{tool_name}' (max {timeout}s)... ---", flush=True)
            p.join(timeout=timeout)

            if p.is_alive():
                print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!", flush=True)
                print(f"Tool '{tool_name}' exceeded {timeout} seconds and will be terminated.", flush=True)
                p.terminate()
                p.join()
                content = f"Error: Tool '{tool_name}' timed out after {timeout} seconds and was terminated."
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

        agent_state["messages"] = outputs
        current_count = inputs.get("tool_call_count", 0)
        new_count = current_count + len(message.tool_calls)
        agent_state["tool_call_count"] = new_count
        tool_call_counter["count"] = new_count
        return agent_state


def should_continue(state):
    last = state["messages"][-1]
    return "continue" if last.tool_calls else "end_flow"


def build_graph(llm_with_tools, tools, cost_tracker_module):
    call_llm = make_call_llm(llm_with_tools, cost_tracker_module)
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
    """The work_dir the eval script `cd`'d the agent into. Prefer
    $PPA_SANDBOX_ROOT (set by eval_ppa_sr.sh) and fall back to CWD so the
    orchestrator still works if invoked outside the harness."""
    return os.environ.get("PPA_SANDBOX_ROOT") or os.getcwd()


def _snapshot_dir():
    """Hidden snapshot directory we write to outside the sandbox so the
    agent's tools (which only see paths under PPA_SANDBOX_ROOT) can never
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


# Same per-tool budget BasicToolNode applies to non-flow tools (300s) — used
# as the hard timeout for the inter-trial evaluate_attempt() call so a wedged
# report_ppa tool can't block the whole reflexion run.
EVALUATE_TIMEOUT_SECONDS = 300


def _evaluate_attempt_isolated(return_dict):
    """Subprocess body for evaluate_attempt. Runs report_ppa in its own
    process so a hung tool can be killed cleanly. Mirrors BasicToolNode's
    isolation pattern."""
    try:
        report = report_ppa.invoke({})
        text = report if isinstance(report, str) else json.dumps(report, default=str)
        success = "ALL TARGETS MET" in (text or "")
        return_dict["result"] = (success, (text or ""))
    except Exception as e:
        return_dict["error"] = traceback.format_exc()
        return_dict["fallback"] = (False, f"Evaluator error: {e}")


def evaluate_attempt():
    """Return (success, summary_text). Reuses the report_ppa tool so the
    success criterion matches what the agent itself sees during a trial.

    The report_ppa call is wrapped in a multiprocessing.Process with the
    same kind of hard timeout BasicToolNode applies to Actor tool calls,
    so a hung tool can't block the whole reflexion run (the previous
    in-process invoke had no hard timeout)."""
    manager = multiprocessing.Manager()
    return_dict = manager.dict()
    p = multiprocessing.Process(
        target=_evaluate_attempt_isolated, args=(return_dict,)
    )
    p.start()
    print(
        f"--- [reflexion] Waiting for evaluate_attempt's report_ppa "
        f"(max {EVALUATE_TIMEOUT_SECONDS}s)... ---",
        flush=True,
    )
    p.join(timeout=EVALUATE_TIMEOUT_SECONDS)

    if p.is_alive():
        print(
            f"!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!\n"
            f"evaluate_attempt's report_ppa exceeded "
            f"{EVALUATE_TIMEOUT_SECONDS}s; terminating to avoid hanging the "
            f"whole reflexion run.",
            flush=True,
        )
        p.terminate()
        p.join()
        return False, (
            f"Evaluator timed out after {EVALUATE_TIMEOUT_SECONDS}s "
            f"running report_ppa."
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
    """One-paragraph compaction of the trial's tool-call timeline. Used as the
    Reflexion 'τ_t' input to the Self-Reflection LLM. We cap to the last 80
    events and truncate long payloads so the reflection prompt stays within
    a sane token budget on long trajectories."""
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
    "You are the Self-Reflection module of a Reflexion agent. The Actor agent "
    "just finished a trial of an OpenROAD PPA-optimization task and FAILED "
    "(at least one target metric not met). Read the trial summary and the "
    "final PPA evaluator report, then produce a concise, actionable critique "
    "that the next trial's Actor will read as 'lessons from the previous "
    "attempt'.\n"
    "\n"
    "Cover, in this order:\n"
    "  1. Diagnostic gaps: which logs/reports were not consulted but should "
    "have been to find the root cause of the violating metric(s).\n"
    "  2. What went wrong with the fix attempts: which parameter/file changes "
    "did NOT move the violating metric (or made it worse), and why.\n"
    "  3. Concrete next-trial strategy in priority order: which "
    "config/SDC/RTL change to try first, and what the expected effect is.\n"
    "\n"
    "Do NOT call tools. Output 8-15 lines of plain text. Be specific (cite "
    "file paths, parameter names, exact numeric gaps). Avoid generic advice."
)


def generate_reflection(llm, task_prompt, summary, eval_report, cost_tracker_module):
    """Run the tool-less Self-Reflection LLM call. Token usage is recorded but
    no tools are bound, so this step is invisible to iteration accounting."""
    sys_msg = SystemMessage(content=REFLECTION_SYSTEM_PROMPT)
    user_msg = HumanMessage(content=(
        f"=== Original task ===\n{task_prompt}\n\n"
        f"=== Actor trajectory summary ===\n{summary}\n\n"
        f"=== Final evaluator report ===\n{eval_report}\n\n"
        f"Write the lessons-learned reflection now."
    ))
    try:
        resp = llm.invoke([sys_msg, user_msg], config={"request_timeout": 400.0})
        cost_tracker_module.record_langchain_usage(resp)
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
    cost_tracker_module,
):
    """Drive the multi-attempt reflexion loop.

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

    graph = build_graph(llm_with_tools, tools, cost_tracker_module)

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
                "inputs — there is no leftover edit from previous attempts "
                "to revert."
            )
        else:
            attempt_system = system_prompt_content

        initial_state = {
            "messages": [
                SystemMessage(content=attempt_system),
                HumanMessage(content=task_prompt),
            ],
            "ppa_violations": [task_prompt],
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
        print(f"[reflexion] eval report:\n{last_eval_report}", flush=True)

        if success:
            print(f"[reflexion] all targets met — stopping early.", flush=True)
            break

        if attempt_idx + 1 < num_attempts:
            print(
                f"[reflexion] generating self-reflection for attempt {attempt_num} "
                f"(does not consume the iteration budget)...",
                flush=True,
            )
            summary = summarize_trajectory(attempt_msgs)
            reflection = generate_reflection(
                llm, task_prompt, summary, last_eval_report, cost_tracker_module
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
