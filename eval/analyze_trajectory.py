#!/usr/bin/env python3
"""Layer-1 trajectory analysis for PPA agent runs.

Parses agent_run.log and emits a per-attempt JSON of deterministic
reasoning/behavior metrics. The eval script invokes this once per attempt and
aggregates the results at bench / top-level / overall granularity.

Usage:
    python3 analyze_trajectory.py <agent_run.log> [--success 0|1] [--out trajectory_stats.json]

Metrics:
    n_tool_calls              total tool invocations
    per_tool_counts           breakdown by tool name
    n_errors                  tool returns whose payload starts with
                              ERROR:/FAILED:/"Error:" or matches wrapper-error
                              markers
    tool_call_error_rate      n_errors / n_tool_calls
    n_edit_failures           edit_file returns with "ERROR: old_content not found"
    edit_failure_rate         n_edit_failures / n_edit_file (NaN when no edits)
    n_redundant_calls         tool calls whose (tool, args) repeats an earlier
                              call in the same attempt
    redundant_call_rate       n_redundant_calls / n_tool_calls
    exploration_ratio         n_read_file / max(1, n_edit + n_write)
    verification_discipline   (n_run_openroad + n_report_ppa) /
                              max(1, n_edit + n_write)
    n_flow_calls / n_flow_success / n_flow_fail
    flow_recovery             "recovered" | "never_recovered" | "no_fail" | "no_flow"
    no_verification           1 if edits > 0 but no run_openroad_flow call
    iteration_exhausted       1 if "RECURSION LIMIT REACHED" appears
    premature_exit            1 if agent ended cleanly (no exhaust) but
                              success_flag == 0
    error_recovery_rate       of errored calls, fraction whose next call had a
                              different (tool, args) (NaN when no errors)

orfs_agent (LLM setup + GP loop) logs no TOOL CALL lines. Its flows are read
from optim_history.json next to the log: each GP candidate counts as one
run_openroad_flow call (SUCCESS when the flow produced metrics, FAILED
otherwise), and iteration_exhausted means every planned outer iteration
(OuterIters) ran.
"""

import argparse
import json
import math
import os
import re
import sys


TOOL_CALL_RE = re.compile(
    r"--- TOOL CALL: Invoking tool '(?P<tool>\w+)' with args: (?P<args>.*?) ---"
)
# Tool-return line format: two-space indent + "[tool_name]: "<quoted-payload>"
# Payload is truncated to the first line by the agent logger.
TOOL_RETURN_RE = re.compile(r'^\s*\[(?P<tool>\w+)\]:\s*(?P<payload>.*)$')


TOOLS_OF_INTEREST = {
    "read_file",
    "list_files",
    "edit_file",
    "write_file",
    "run_openroad_flow",
    "report_ppa",
}

# orfs_agent start-up banner, and the outer-iteration budget it prints with it.
ORFS_AGENT_MARKER = "ORFS-Agent run starting"
OUTER_ITERS_RE = re.compile(r"^OuterIters:\s*(\d+)", re.MULTILINE)


def _is_error_payload(payload: str) -> bool:
    """Return True if a tool-return payload looks like an error/failure."""
    p = payload.lstrip('"')
    lowers = [
        "ERROR:",
        "FAILED:",
        "Error:",
        "Command returned no output",
        "timed out after",
        "Tool '",            # wrapper: Error: Tool 'X' failed.
        "Unknown tool",
    ]
    return any(p.startswith(tag) or tag in p[:80] for tag in lowers)


def _is_edit_old_content_failure(payload: str) -> bool:
    return "old_content not found" in payload[:120]


def parse_log(log_path: str):
    """Return a list of dicts: [{tool, args, payload|None}, ...] in call order."""
    try:
        text = open(log_path, errors="replace").read()
    except OSError:
        return [], ""

    calls = []
    for m in TOOL_CALL_RE.finditer(text):
        calls.append({"tool": m.group("tool"), "args": m.group("args"), "payload": None})

    # Tool-return lines are emitted by call_llm() on the *next* agent turn,
    # in the same order the tools were called. Pair k-th return to k-th call.
    returns = []
    for line in text.splitlines():
        m = TOOL_RETURN_RE.match(line)
        if m and m.group("tool") in TOOLS_OF_INTEREST | {"error"}:
            # Skip the occasional "[run_openroad_flow]: ..." line that appears
            # inside a read_file output by looking at whether the whole line
            # starts with exactly 2 spaces (the logger format); Python's re
            # already anchors at start with \s*, so this is a best-effort check.
            returns.append({"tool": m.group("tool"), "payload": m.group("payload")})

    # Pair calls with returns. If we have more calls than returns (agent died
    # before its last return got echoed), the tail calls get payload=None.
    for i in range(min(len(calls), len(returns))):
        if returns[i]["tool"] == calls[i]["tool"]:
            calls[i]["payload"] = returns[i]["payload"]
        # If tools don't match, pairing is broken; bail on payload assignment
        # for the rest — counts still work, just error rates may be off.

    return calls, text


def parse_orfs_agent(log_path: str, text: str):
    """Return ([{tool, args, payload}, ...], iteration_exhausted) for orfs_agent.

    Every GP candidate is one OpenROAD flow, recorded in optim_history.json
    next to the log. A flow succeeded when it produced metrics; candidates whose
    flow failed (e.g. at synthesis) carry no metrics.
    """
    attempt_dir = os.path.dirname(os.path.abspath(log_path))
    try:
        with open(os.path.join(attempt_dir, "optim_history.json")) as f:
            history = json.load(f)
    except (OSError, ValueError):
        history = None

    if history is None:
        # The agent died before writing its history: count launched flows only.
        calls = [{"tool": "run_openroad_flow", "args": str(i), "payload": None}
                 for i in range(text.count("EXECUTING OPENROAD COMMAND:"))]
        n_iters_run = len(re.findall(r"^Iteration \d+/\d+", text, re.MULTILINE))
    else:
        calls = []
        for h in history:
            produced = any(v is not None for v in (h.get("metrics") or {}).values())
            if h.get("status") != "ok":
                payload = f"FAILED: flow {h.get('status')}"
            elif produced:
                payload = "SUCCESS: flow produced metrics"
            else:
                payload = "FAILED: flow produced no metrics"
            calls.append({"tool": "run_openroad_flow",
                          "args": json.dumps(h.get("vector")),
                          "payload": payload})
        n_iters_run = len({h.get("iter") for h in history})

    m = OUTER_ITERS_RE.search(text)
    iteration_exhausted = int(bool(m) and n_iters_run >= int(m.group(1)))
    return calls, iteration_exhausted


def compute(calls, text, success_flag, iteration_exhausted=None):
    from collections import Counter

    per_tool = Counter(c["tool"] for c in calls)
    n = len(calls)

    # Errors (by payload)
    n_errors = sum(1 for c in calls if c["payload"] and _is_error_payload(c["payload"]))

    # Edit-file specifics
    edits = [c for c in calls if c["tool"] == "edit_file"]
    n_edit_fail = sum(1 for c in edits if c["payload"] and _is_edit_old_content_failure(c["payload"]))

    # Redundant calls (same tool+args repeats earlier in this run)
    seen = set()
    n_redundant = 0
    for c in calls:
        key = (c["tool"], c["args"])
        if key in seen:
            n_redundant += 1
        else:
            seen.add(key)

    # Flow-specific
    flow_calls = [c for c in calls if c["tool"] == "run_openroad_flow"]
    flow_results = []
    for c in flow_calls:
        p = c["payload"] or ""
        p = p.lstrip('"')
        if p.startswith("SUCCESS"):
            flow_results.append("SUCCESS")
        elif p.startswith("FAILED"):
            flow_results.append("FAILED")
        else:
            flow_results.append("UNKNOWN")
    n_flow_success = flow_results.count("SUCCESS")
    n_flow_fail = flow_results.count("FAILED")

    if not flow_calls:
        flow_recovery = "no_flow"
    elif n_flow_fail == 0:
        flow_recovery = "no_fail"
    else:
        last_fail_idx = max(i for i, r in enumerate(flow_results) if r == "FAILED")
        later_success = any(
            r == "SUCCESS" for r in flow_results[last_fail_idx + 1:]
        )
        flow_recovery = "recovered" if later_success else "never_recovered"

    # Error-recovery: after an error, did the next call change (tool, args)?
    n_errs = 0
    n_recover_ok = 0
    for i, c in enumerate(calls[:-1]):
        if c["payload"] and _is_error_payload(c["payload"]):
            n_errs += 1
            next_c = calls[i + 1]
            if (next_c["tool"], next_c["args"]) != (c["tool"], c["args"]):
                n_recover_ok += 1

    # Ratios
    edits_writes = per_tool.get("edit_file", 0) + per_tool.get("write_file", 0)
    reads = per_tool.get("read_file", 0)
    n_flow = per_tool.get("run_openroad_flow", 0)
    n_report = per_tool.get("report_ppa", 0)

    if iteration_exhausted is None:
        iteration_exhausted = int("RECURSION LIMIT REACHED" in text)
    no_verification = int(edits_writes > 0 and n_flow == 0)
    premature_exit = int(
        not iteration_exhausted
        and success_flag == 0
        and "AGENT RUN COMPLETE" in text
    )

    def _rate(num, denom):
        return (num / denom) if denom else float("nan")

    return {
        "success": int(success_flag),
        "n_tool_calls": n,
        "per_tool_counts": dict(per_tool),
        "n_errors": n_errors,
        "tool_call_error_rate": _rate(n_errors, n),
        "n_edit_failures": n_edit_fail,
        "edit_failure_rate": _rate(n_edit_fail, len(edits)),
        "n_redundant_calls": n_redundant,
        "redundant_call_rate": _rate(n_redundant, n),
        "exploration_ratio": _rate(reads, max(1, edits_writes)),
        "verification_discipline": _rate(n_flow + n_report, max(1, edits_writes)),
        "n_flow_calls": len(flow_calls),
        "n_flow_success": n_flow_success,
        "n_flow_fail": n_flow_fail,
        "flow_recovery": flow_recovery,
        "no_verification": no_verification,
        "iteration_exhausted": iteration_exhausted,
        "premature_exit": premature_exit,
        "error_recovery_rate": _rate(n_recover_ok, n_errs),
    }


def _nan_to_null(obj):
    if isinstance(obj, dict):
        return {k: _nan_to_null(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_nan_to_null(v) for v in obj]
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log_path", help="Path to agent_run.log")
    ap.add_argument("--success", type=int, default=0,
                    help="Attempt success flag (0 or 1)")
    ap.add_argument("--out", default=None,
                    help="Output JSON path (default: trajectory_stats.json "
                         "next to the log file)")
    args = ap.parse_args()

    if not os.path.isfile(args.log_path):
        sys.stderr.write(f"log not found: {args.log_path}\n")
        sys.exit(1)

    calls, text = parse_log(args.log_path)
    iteration_exhausted = None
    if ORFS_AGENT_MARKER in text:
        calls, iteration_exhausted = parse_orfs_agent(args.log_path, text)
    stats = compute(calls, text, args.success, iteration_exhausted)
    out_path = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.log_path)),
        "trajectory_stats.json",
    )
    with open(out_path, "w") as f:
        json.dump(_nan_to_null(stats), f, indent=2)
    print(out_path)


if __name__ == "__main__":
    main()
