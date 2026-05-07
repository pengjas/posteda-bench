"""Shared Tree-of-Thoughts orchestrator for PPA agents.

Implements ToT (Yao et al., arXiv:2305.10601) on top of the same tool surface
the ReAct agents use. At each tree node the agent samples PARALLEL_NODE
candidate next actions in parallel, an LLM judge scores them, the
highest-scored candidate is executed, and the resulting state becomes a new
node. All un-executed candidates from every previously expanded node remain
in a global priority queue, so when the current path stalls the search
naturally backtracks to the highest-scored alternative from an ancestor —
this is the "fall back to a previous node" behavior.

Iteration accounting:
  - --iterations is the TOTAL tool-call budget across the whole tree
    (ReAct's 5 runs x 16 iters = 80 total maps to ToT's --iterations 80).
  - --max_depth is the deepest any single path may go (default 16, matching
    ReAct's per-run iteration cap).
  - --parallel_node is the number of candidates sampled at each expansion.
  - Per-step LLM calls (K candidate samples + 1 scoring call) are billed in
    tokens but do NOT consume iteration budget. Only tool calls do.

Sandbox handling:
  - Each tree node owns a snapshot of the sandbox at that state, stored under
    $AGENT_RESULT_DIR/_tot_snapshots/<node_id>/. When the search picks a
    candidate from a non-current ancestor (i.e. backtrack), the sandbox is
    wiped and restored from that ancestor's snapshot before executing.
  - Snapshots live outside the agent-visible sandbox so the tools (rooted at
    PPA_SANDBOX_ROOT) never see them.
  - At end of run the sandbox is restored to the best node, ranked as:
      1. success node first; else
      2. paths that successfully called run_openroad_flow at least once
         (so ./reports/6_finish.rpt and ./logs/6_report.json exist in the
         chosen snapshot); else
      3. deeper paths over shallower paths; else
      4. higher judge score (action_score / value).
    This avoids the failure mode where a shallow high-scored ancestor is
    picked even though a deeper sibling actually produced the OpenROAD
    reports the eval needs to score the run. eval_ppa_sr.sh's artifact-
    copy step then picks up the final answer state.
"""

import os
import re
import json
import shutil
import time
import heapq
import uuid
import traceback
import multiprocessing
from concurrent.futures import ThreadPoolExecutor, as_completed

from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage

from agents.ppa.tools import report_ppa


# Cumulative tool-call count for the entire ToT run. Mirrors the React agents'
# global counter so eval_ppa_sr.sh's tail-grep against 'TOTAL TOOL CALLS:'
# picks up the right number even when the run is killed mid-search.
tool_call_counter = {"count": 0}


# The PPA verification tool: it's the only tool that writes reports/6_finish.rpt
# and logs/6_report.json — the artifacts eval_ppa_sr.sh checks for to decide
# whether the run produced any output, and the inputs report_ppa() reads to
# decide whether ALL TARGETS MET. The final-node selection prefers paths that
# called this tool successfully so the chosen snapshot actually contains
# scorable artifacts.
VERIFICATION_TOOL_NAME = "run_openroad_flow"


def _is_successful_verification_call(tool_call_name, tool_message_content):
    """A verification call counts only if the tool didn't error — execute_tool_call
    prefixes failure ToolMessages with 'Error: Tool '."""
    if tool_call_name != VERIFICATION_TOOL_NAME:
        return False
    content = tool_message_content if isinstance(tool_message_content, str) else ""
    return not content.startswith("Error: Tool ")


def _maybe_get_openai_conn_err():
    """Return openai.APIConnectionError if openai is importable, else a
    sentinel that won't match anything. Lets the LLM-call retry transient
    vLLM blips without forcing openai as a hard dependency."""
    try:
        import openai
        return openai.APIConnectionError
    except Exception:
        class _Never(Exception):
            pass
        return _Never


# ---------- Sandbox snapshot helpers ----------

def _sandbox_root():
    return os.environ.get("PPA_SANDBOX_ROOT") or os.getcwd()


def _snapshots_root():
    result_dir = os.environ.get("AGENT_RESULT_DIR", ".")
    return os.path.join(result_dir, "_tot_snapshots")


def _wipe_sandbox(sandbox_root):
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


def snapshot_sandbox(node_id):
    """Copy the current sandbox tree to $AGENT_RESULT_DIR/_tot_snapshots/<node_id>/.
    Returns the snapshot path or None on failure."""
    src = _sandbox_root()
    snap_root = _snapshots_root()
    os.makedirs(snap_root, exist_ok=True)
    dst = os.path.join(snap_root, node_id)
    if os.path.isdir(dst):
        shutil.rmtree(dst, ignore_errors=True)
    if not os.path.isdir(src):
        print(f"[tot] WARNING: sandbox {src} does not exist; cannot snapshot.", flush=True)
        return None
    try:
        shutil.copytree(src, dst, symlinks=False)
        return dst
    except Exception as e:
        print(f"[tot] warn: snapshot failed ({src} -> {dst}): {e}", flush=True)
        return None


def restore_sandbox(snapshot_dir):
    """Wipe the sandbox and refill it from snapshot_dir. Used when backtracking
    so the next tool call sees the ancestor's state, not the current path's."""
    sandbox_root = _sandbox_root()
    if not snapshot_dir or not os.path.isdir(snapshot_dir):
        print(f"[tot] WARNING: snapshot dir {snapshot_dir} not found; skipping restore.", flush=True)
        return False
    _wipe_sandbox(sandbox_root)
    for entry in os.listdir(snapshot_dir):
        src = os.path.join(snapshot_dir, entry)
        dst = os.path.join(sandbox_root, entry)
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst, symlinks=False)
            elif os.path.isfile(src):
                shutil.copy2(src, dst)
        except Exception as e:
            print(f"[tot] warn: restore copy {src} -> {dst}: {e}", flush=True)
    return True


def cleanup_snapshots():
    """Remove the entire snapshot tree at end of run."""
    snap_root = _snapshots_root()
    try:
        if os.path.isdir(snap_root):
            shutil.rmtree(snap_root, ignore_errors=True)
    except Exception:
        pass


# ---------- Tool execution (process-isolated, mirrors React/Reflexion) ----------

def _run_tool_isolated(tool, args, return_dict):
    try:
        result = tool.invoke(args)
        return_dict["result"] = json.dumps(result)
    except Exception:
        return_dict["error"] = traceback.format_exc()


def execute_tool_call(tool_call, tools_by_name):
    """Run a single tool call in an isolated process (so a hung tool can't take
    the whole agent down). Returns a ToolMessage in either success or error form."""
    tool_name = tool_call["name"]
    tool_args = tool_call.get("args", {})
    print(f"\n--- TOOL CALL: Invoking tool '{tool_name}' with args: {tool_args} ---", flush=True)

    tool = tools_by_name.get(tool_name)
    if tool is None:
        print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: UNKNOWN TOOL  !!!!!!!!!!!!!!!!!!!!", flush=True)
        return ToolMessage(
            content=f"Error: Unknown tool '{tool_name}'.",
            name=tool_name,
            tool_call_id=tool_call.get("id", ""),
        )

    tool_timeout = int(os.environ.get("TOOL_TIMEOUT", 1800))
    timeout = (tool_timeout + 60) if tool_name == "run_openroad_flow" else 300

    manager = multiprocessing.Manager()
    return_dict = manager.dict()
    p = multiprocessing.Process(target=_run_tool_isolated, args=(tool, tool_args, return_dict))
    p.start()
    print(f"--- Waiting for '{tool_name}' (max {timeout}s)... ---", flush=True)
    p.join(timeout=timeout)

    if p.is_alive():
        print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TIMEOUT  !!!!!!!!!!!!!!!!!!!!", flush=True)
        print(f"Tool '{tool_name}' exceeded {timeout} seconds and will be terminated.", flush=True)
        p.terminate()
        p.join()
        content = f"Error: Tool '{tool_name}' timed out after {timeout} seconds and was terminated."
    elif "error" in return_dict:
        print(f"!!!!!!!!!!!!!!!!!!!!  ERROR: TOOL FAILED  !!!!!!!!!!!!!!!!!!!!", flush=True)
        print(f"Tool '{tool_name}' failed with error:\n{return_dict['error']}", flush=True)
        content = f"Error: Tool '{tool_name}' failed. Exception Traceback:\n{return_dict['error']}"
    else:
        print(f"--- Tool '{tool_name}' finished successfully. ---", flush=True)
        content = return_dict.get("result", "{}")

    return ToolMessage(content=content, name=tool_name, tool_call_id=tool_call.get("id", ""))


# ---------- LLM helpers ----------

def _llm_invoke_with_retry(llm, messages, label="llm", max_retries=6):
    """Invoke an LLM with the same backoff schedule the React agents use for
    transient connection errors. Other errors propagate so real bugs are loud."""
    APIConnectionError = _maybe_get_openai_conn_err()
    backoff = [10, 30, 60, 90, 120]
    last_exc = None
    for attempt in range(max_retries):
        try:
            return llm.invoke(messages, config={"request_timeout": 400.0})
        except APIConnectionError as e:
            last_exc = e
            if attempt == max_retries - 1:
                raise
            wait = backoff[min(attempt, len(backoff) - 1)]
            print(
                f"!!! {label} LLM unreachable (attempt {attempt + 1}/{max_retries}), "
                f"retrying in {wait}s: {e}",
                flush=True,
            )
            time.sleep(wait)
    if last_exc is not None:
        raise last_exc


def sample_k_candidates(sampler_llm_with_tools, messages, k, cost_tracker_module):
    """Generate K candidate next actions in parallel.

    The sampler LLM is configured with a non-zero temperature in the entry
    point so the K samples actually differ even when the eval harness passes
    --temperature 0 (which would otherwise produce K identical candidates and
    defeat the whole point of ToT).
    """
    if k <= 0:
        return []

    def _one_call(idx):
        try:
            resp = _llm_invoke_with_retry(
                sampler_llm_with_tools, messages, label=f"sample[{idx}]"
            )
            cost_tracker_module.record_langchain_usage(resp)
            print(
                f"=================================RESPONSE (cand {idx})==================================",
                flush=True,
            )
            print(resp, flush=True)
            print(
                f"=================================RESPONSE (cand {idx})==================================",
                flush=True,
            )
            return resp
        except Exception as e:
            print(f"[tot] candidate {idx} sampling failed: {e}", flush=True)
            traceback.print_exc()
            return None

    candidates = []
    if k == 1:
        resp = _one_call(0)
        if resp is not None:
            candidates.append(resp)
        return candidates

    with ThreadPoolExecutor(max_workers=k) as ex:
        futures = {ex.submit(_one_call, i): i for i in range(k)}
        for fut in as_completed(futures):
            resp = fut.result()
            if resp is not None:
                candidates.append(resp)
    return candidates


SCORE_SYSTEM_PROMPT = (
    "You are the Evaluator module of a Tree-of-Thoughts agent. The Actor "
    "agent is solving an OpenROAD PPA-optimization task and has proposed "
    "several candidate next actions from the same state. Read the recent "
    "conversation context and each candidate's reasoning + tool call, then "
    "rate each candidate from 1 (clearly bad: irrelevant, redundant, will "
    "not progress the task) to 10 (clearly the most promising next step "
    "toward meeting all PPA targets).\n\n"
    "Reward candidates that diagnose root causes, target the right violating "
    "metric, and avoid wasted/redundant tool calls. Penalize candidates that "
    "blindly tune parameters without diagnosis or that repeat actions "
    "already taken in the recent context. A candidate that proposes "
    "run_openroad_flow when no useful change has been made since the last "
    "run is wasteful; score it low.\n\n"
    "Output STRICT JSON only, of the form: "
    '{"scores":[<int1>,<int2>,...]} '
    "with one integer per candidate, in the same order as listed."
)


def _render_msg_for_judge(m):
    if isinstance(m, SystemMessage):
        return "SYSTEM: <task instructions omitted>"
    if isinstance(m, HumanMessage):
        text = m.content if isinstance(m.content, str) else str(m.content)
        if len(text) > 600:
            text = text[:600] + "...<truncated>"
        return f"USER: {text}"
    if isinstance(m, AIMessage):
        thought = m.content if isinstance(m.content, str) else ""
        if len(thought) > 300:
            thought = thought[:300] + "...<truncated>"
        tcs = []
        if getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                args_str = json.dumps(tc.get("args", {}), default=str)
                if len(args_str) > 240:
                    args_str = args_str[:240] + "...<truncated>"
                tcs.append(f"  TOOL_CALL {tc.get('name')}({args_str})")
        body = f"ASSISTANT: {thought}"
        if tcs:
            body += "\n" + "\n".join(tcs)
        return body
    if isinstance(m, ToolMessage):
        content = m.content if isinstance(m.content, str) else str(m.content)
        if len(content) > 400:
            content = content[:400] + "...<truncated>"
        return f"TOOL[{m.name}]: {content}"
    return str(m)


def _parse_scores(text, k):
    """Best-effort extraction of K integer scores from a judge response.
    Falls back to a uniform middle score if parsing fails so the search keeps
    moving rather than stalling on a malformed judge output."""
    if not isinstance(text, str):
        text = str(text)
    # 1) try to find a JSON object with "scores"
    m = re.search(r'\{[^{}]*"scores"\s*:\s*\[[^\]]*\][^{}]*\}', text, re.DOTALL)
    if m:
        try:
            data = json.loads(m.group(0))
            scores = data.get("scores", [])
            if isinstance(scores, list) and len(scores) >= k:
                return [float(scores[i]) for i in range(k)]
        except Exception:
            pass
    # 2) try to find a bare list
    m = re.search(r'\[\s*([\d.,\s]+)\s*\]', text)
    if m:
        nums = re.findall(r'\d+(?:\.\d+)?', m.group(1))
        if len(nums) >= k:
            return [float(nums[i]) for i in range(k)]
    # 3) fall back to first K numbers in the response
    nums = re.findall(r'\d+(?:\.\d+)?', text)
    if len(nums) >= k:
        return [float(nums[i]) for i in range(k)]
    return [5.0] * k


def score_candidates(judge_llm, parent_messages, candidates, cost_tracker_module):
    """LLM judge scores each candidate 1..10. Returns list[float] of len(candidates)."""
    if not candidates:
        return []
    if len(candidates) == 1:
        return [5.0]

    ctx_msgs = parent_messages[-8:]
    ctx_str = "\n".join(_render_msg_for_judge(m) for m in ctx_msgs)

    cand_strs = []
    for i, c in enumerate(candidates):
        thought = c.content if isinstance(c.content, str) else ""
        if len(thought) > 400:
            thought = thought[:400] + "...<truncated>"
        tcs = []
        if getattr(c, "tool_calls", None):
            for tc in c.tool_calls:
                args_str = json.dumps(tc.get("args", {}), default=str)
                if len(args_str) > 300:
                    args_str = args_str[:300] + "...<truncated>"
                tcs.append(f"  - TOOL_CALL {tc.get('name')}({args_str})")
        action_block = "\n".join(tcs) if tcs else "  (no tool call — final answer)"
        cand_strs.append(
            f"=== Candidate {i + 1} ===\nReasoning: {thought}\nActions:\n{action_block}"
        )

    user_msg = HumanMessage(content=(
        f"=== Conversation context (recent) ===\n{ctx_str}\n\n"
        + "\n".join(cand_strs)
        + f"\n\nScore the {len(candidates)} candidates now."
    ))
    sys_msg = SystemMessage(content=SCORE_SYSTEM_PROMPT)

    try:
        resp = _llm_invoke_with_retry(judge_llm, [sys_msg, user_msg], label="judge")
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
        scores = _parse_scores(text or "", len(candidates))
        print(f"[tot] judge scores: {scores}", flush=True)
        return scores
    except Exception as e:
        print(f"[tot] judge call failed; using uniform scores: {e}", flush=True)
        traceback.print_exc()
        return [5.0] * len(candidates)


def _candidate_dedup_key(candidate):
    """Stable key for in-expansion dedup: a tuple over (tool_name, sorted-arg
    JSON) for each tool_call. Returns None for candidates with no tool_calls
    so we never collapse multiple final-answer terminals together (they're
    rare and harmless to keep)."""
    tcs = getattr(candidate, "tool_calls", None) or []
    if not tcs:
        return None
    return tuple(
        (tc.get("name"), json.dumps(tc.get("args", {}), sort_keys=True, default=str))
        for tc in tcs
    )


def _dedupe_candidates(candidates):
    """Drop candidates whose (tool_name, args) tuple exact-matches an
    earlier-kept candidate in the same expansion. Without this, K parallel
    samples that converge on identical tool calls each occupy a frontier
    slot at the same score, depth, and parent — under any tie-break they
    pop sequentially and burn the budget on near-duplicate work. Order is
    preserved so the as_completed-driven completion order still chooses
    the fastest sample on ties."""
    seen = set()
    out = []
    for c in candidates:
        key = _candidate_dedup_key(c)
        if key is None:
            out.append(c)
            continue
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


# ---------- Tree node ----------

class TotNode:
    __slots__ = ("id", "messages", "parent_id", "depth", "snapshot_dir",
                 "terminal", "value", "success", "path_has_verification")

    def __init__(self, id, messages, parent_id, depth, snapshot_dir=None,
                 terminal=False, value=0.0, success=False,
                 path_has_verification=False):
        self.id = id
        self.messages = messages
        self.parent_id = parent_id
        self.depth = depth
        self.snapshot_dir = snapshot_dir
        self.terminal = terminal
        self.value = value
        self.success = success
        # True if this node OR any ancestor executed a successful
        # run_openroad_flow call. Drives final-node selection so the chosen
        # snapshot actually contains reports/6_finish.rpt and logs/6_report.json.
        self.path_has_verification = path_has_verification


def evaluate_state():
    """Use report_ppa to determine if all PPA targets are met. Returns
    (success: bool, summary: str). Mirrors reflexion's evaluator so the
    success criterion stays consistent with the Actor's own view."""
    try:
        report = report_ppa.invoke({})
    except Exception as e:
        return False, f"Evaluator error: {e}"
    success = "ALL TARGETS MET" in (report or "")
    return success, (report or "")


# ---------- Main search loop ----------

def run_tot(
    judge_llm,
    sampler_llm_with_tools,
    tools,
    system_prompt_content,
    task_prompt,
    iterations,
    parallel_node,
    max_depth,
    cost_tracker_module,
):
    """Drive the ToT search.

    Args:
        judge_llm: plain LLM (no tools bound) used for the candidate scorer.
        sampler_llm_with_tools: tool-bound LLM, configured with non-zero
            temperature in the entry point so K parallel samples actually
            differ.
        tools: list of LangChain tools (read_file, edit_file, run_openroad_flow, ...).
        system_prompt_content: system prompt for the Actor LLM.
        task_prompt: user prompt for the Actor LLM (typically prompt.txt).
        iterations: TOTAL tool-call budget across the whole tree.
        parallel_node: K, candidates sampled per expansion.
        max_depth: max depth any single path may go.
        cost_tracker_module: cost_tracker or vllm_cost_tracker (interface-compat).

    Returns:
        (status_str, total_tool_calls).
    """
    result_dir = os.environ.get("AGENT_RESULT_DIR", ".")
    tools_by_name = {t.name: t for t in tools}

    total_budget = max(1, int(iterations))
    K = max(1, int(parallel_node))
    max_depth = max(1, int(max_depth))

    print(
        f"[tot] iterations(total budget)={total_budget}, parallel_node={K}, "
        f"max_depth={max_depth}",
        flush=True,
    )

    # Drop any stale snapshot tree from a prior run.
    cleanup_snapshots()
    os.makedirs(_snapshots_root(), exist_ok=True)

    # Root node: the initial sandbox state with the system+user prompt.
    sys_msg = SystemMessage(content=system_prompt_content)
    initial_messages = [sys_msg, HumanMessage(content=task_prompt)]
    root_id = "root"
    root_snap = snapshot_sandbox(root_id)
    root = TotNode(
        id=root_id,
        messages=initial_messages,
        parent_id=None,
        depth=0,
        snapshot_dir=root_snap,
        terminal=False,
        value=5.0,
        success=False,
    )
    nodes = {root.id: root}

    # Frontier: min-heap on -score so popping gives the highest-scored
    # (parent_node_id, candidate) pair across the whole tree. Among ties on
    # score, prefer the candidate whose parent sits deepest in the tree
    # (-parent_depth wins on a min-heap) so freshly-expanded deep nodes
    # advance before we drain old equally-scored siblings near the root —
    # this avoids the failure mode where K identical 10-scored siblings of
    # an early node burn the whole budget on near-duplicate actions. The
    # counter still tie-breaks any remaining ties so ordering stays
    # deterministic.
    frontier = []
    counter = [0]

    def _push_candidate(parent_id, candidate, score):
        counter[0] += 1
        parent = nodes.get(parent_id)
        parent_depth = parent.depth if parent is not None else 0
        heapq.heappush(
            frontier,
            (-float(score), -int(parent_depth), counter[0], parent_id, candidate),
        )

    def _expand(parent):
        """Sample K candidates from parent and push them to the frontier."""
        if parent.terminal or parent.depth >= max_depth:
            return
        candidates = sample_k_candidates(
            sampler_llm_with_tools, parent.messages, K, cost_tracker_module
        )
        if not candidates:
            print(f"[tot] expand: no candidates produced for node {parent.id}", flush=True)
            return
        sampled = len(candidates)
        candidates = _dedupe_candidates(candidates)
        dropped = sampled - len(candidates)
        if dropped:
            print(
                f"[tot] dedup: dropped {dropped}/{sampled} duplicate candidate(s) "
                f"at node {parent.id} (identical tool_call args)",
                flush=True,
            )
        if not candidates:
            return
        if len(candidates) > 1:
            scores = score_candidates(
                judge_llm, parent.messages, candidates, cost_tracker_module
            )
        else:
            scores = [5.0]
        for c, s in zip(candidates, scores):
            _push_candidate(parent.id, c, s)
        print(
            f"[tot] expanded node {parent.id} (depth {parent.depth}): "
            f"{len(candidates)} candidates pushed, scores={scores}",
            flush=True,
        )

    # Track which node's snapshot is currently materialized in the sandbox
    # so we only restore when the next action's parent differs.
    current_sandbox_node_id = root.id

    def _ensure_sandbox_at(node):
        nonlocal current_sandbox_node_id
        if node.id == current_sandbox_node_id:
            return
        print(
            f"[tot] backtrack: restoring sandbox {current_sandbox_node_id} -> {node.id}",
            flush=True,
        )
        restore_sandbox(node.snapshot_dir)
        current_sandbox_node_id = node.id

    # Seed the frontier with K candidates from the root.
    _expand(root)

    tool_calls_used = 0
    success = False
    last_eval_report = ""
    success_node = None

    while tool_calls_used < total_budget and frontier:
        neg_score, _neg_depth, _ctr, parent_id, candidate = heapq.heappop(frontier)
        action_score = -neg_score

        parent = nodes.get(parent_id)
        if parent is None or parent.depth >= max_depth:
            continue

        sep = "=" * 80
        print(
            f"\n{sep}\n[tot] EXECUTING: parent={parent_id} (depth {parent.depth}), "
            f"action_score={action_score:.2f}, "
            f"budget_left={total_budget - tool_calls_used}\n{sep}",
            flush=True,
        )

        tool_calls = list(getattr(candidate, "tool_calls", None) or [])
        new_msgs = list(parent.messages) + [candidate]

        if not tool_calls:
            # Actor declared done at this state. Evaluate against PPA targets;
            # mark terminal either way and continue searching.
            _ensure_sandbox_at(parent)
            success_now, eval_report = evaluate_state()
            last_eval_report = eval_report
            term_node = TotNode(
                id=str(uuid.uuid4())[:8],
                messages=new_msgs,
                parent_id=parent.id,
                depth=parent.depth,  # no tool consumed -> no depth advance
                snapshot_dir=parent.snapshot_dir,
                terminal=True,
                value=10.0 if success_now else 1.0,
                success=success_now,
                path_has_verification=parent.path_has_verification,
            )
            nodes[term_node.id] = term_node
            if success_now:
                success = True
                success_node = term_node
                print(f"[tot] terminal node — ALL TARGETS MET. Stopping.", flush=True)
                break
            print(f"[tot] terminal node (no tool call), success=False; continuing search", flush=True)
            continue

        # Don't go past the budget cap mid-AIMessage. If a single AIMessage
        # carries more tool calls than we have left, we execute as many as
        # possible and stop.
        remaining = total_budget - tool_calls_used
        if len(tool_calls) > remaining:
            print(
                f"[tot] budget cap: trimming {len(tool_calls)} tool calls -> {remaining}",
                flush=True,
            )
            tool_calls = tool_calls[:remaining]
            if not tool_calls:
                break
            # Rewrite the AIMessage in our trajectory to match what we actually executed.
            try:
                candidate = AIMessage(
                    content=candidate.content,
                    tool_calls=tool_calls,
                )
                new_msgs = list(parent.messages) + [candidate]
            except Exception:
                # If we can't reconstruct, leave the original AIMessage as-is;
                # downstream logging will still be coherent because we only
                # produced ToolMessages for the executed subset.
                pass

        # Backtrack the sandbox to parent state before running the action.
        _ensure_sandbox_at(parent)

        tool_msgs = []
        for tc in tool_calls:
            tm = execute_tool_call(tc, tools_by_name)
            tool_msgs.append(tm)
            tool_calls_used += 1
            tool_call_counter["count"] = tool_calls_used
        new_msgs.extend(tool_msgs)

        # Snapshot the post-action state and create the child node. The
        # sandbox is now at the child's state; record that so we don't
        # waste a restore on the next pop.
        new_node_id = str(uuid.uuid4())[:8]
        new_snap = snapshot_sandbox(new_node_id)
        current_sandbox_node_id = new_node_id

        success_now, eval_report = evaluate_state()
        last_eval_report = eval_report

        # Did this step include a successful run_openroad_flow call? If yes,
        # this node's snapshot has reports/6_finish.rpt and logs/6_report.json
        # on disk (whether targets met or not). Used by the final-node
        # selector below.
        verified_now = any(
            _is_successful_verification_call(tc.get("name"), tm.content)
            for tc, tm in zip(tool_calls, tool_msgs)
        )
        path_verified = parent.path_has_verification or verified_now

        new_depth = parent.depth + 1
        new_terminal = success_now or new_depth >= max_depth

        new_node = TotNode(
            id=new_node_id,
            messages=new_msgs,
            parent_id=parent.id,
            depth=new_depth,
            snapshot_dir=new_snap,
            terminal=new_terminal,
            value=10.0 if success_now else action_score,
            success=success_now,
            path_has_verification=path_verified,
        )
        nodes[new_node.id] = new_node
        print(
            f"[tot] node {new_node.id} created at depth {new_depth} "
            f"(terminal={new_terminal}, success={success_now}); tool_calls_used={tool_calls_used}/{total_budget}",
            flush=True,
        )

        if success_now:
            success = True
            success_node = new_node
            print(f"[tot] ALL TARGETS MET at node {new_node.id}. Stopping.", flush=True)
            break

        if not new_terminal and tool_calls_used < total_budget:
            _expand(new_node)

    # Pick the "final answer" node, ranked by tuple (success, path-has-
    # verification, depth, value). Lexicographic comparison means:
    #   1. success node first (evaluate_state saw "ALL TARGETS MET"); else
    #   2. any path that called run_openroad_flow successfully (so the chosen
    #      snapshot has reports/6_finish.rpt and logs/6_report.json for
    #      eval_ppa_sr.sh to score); else
    #   3. deeper paths (more reasoning / actions actually applied); else
    #   4. higher judge score.
    # This fixes the failure mode where many ties at value=10.0 caused
    # `max(..., key=value)` to pick the earliest, shallowest sibling — which
    # often had no run_openroad_flow on its path, yielding "fail (no reports
    # generated)" in the eval even though deeper siblings did produce reports.
    final_node = success_node
    if final_node is None:
        non_root = [n for n in nodes.values() if n.id != root.id]
        if non_root:
            final_node = max(
                non_root,
                key=lambda n: (
                    int(n.success),
                    int(n.path_has_verification),
                    n.depth,
                    n.value,
                ),
            )
        else:
            final_node = root

    if final_node.snapshot_dir and current_sandbox_node_id != final_node.id:
        print(
            f"[tot] restoring sandbox to final node {final_node.id} "
            f"(success={final_node.success}, value={final_node.value:.2f})",
            flush=True,
        )
        restore_sandbox(final_node.snapshot_dir)

    # Persist the tree summary so it can be inspected after the run.
    try:
        tree_dump = []
        for n in nodes.values():
            tree_dump.append({
                "id": n.id,
                "parent_id": n.parent_id,
                "depth": n.depth,
                "terminal": n.terminal,
                "success": n.success,
                "value": n.value,
                "path_has_verification": n.path_has_verification,
            })
        with open(os.path.join(result_dir, "tot_tree.json"), "w") as f:
            json.dump({
                "nodes": tree_dump,
                "tool_calls_used": tool_calls_used,
                "total_budget": total_budget,
                "max_depth": max_depth,
                "parallel_node": K,
                "final_success": bool(success),
                "final_node_id": final_node.id if final_node is not None else None,
                "final_eval_report": last_eval_report,
            }, f, indent=2)
    except Exception as e:
        print(f"[tot] warn: could not write tot_tree.json: {e}", flush=True)

    cleanup_snapshots()

    status = "COMPLETE"
    return status, tool_calls_used
