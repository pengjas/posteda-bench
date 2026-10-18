#!/usr/bin/env python3
"""Aggregate trajectory_stats.json files into a formatted summary block.

Usage:
    python3 summarize_trajectory_stats.py <root_dir> [<root_dir> ...]

Emits a short formatted report. Intended to be invoked from eval_ppa_sr.sh once
per reporting level (benchmark / top-level / overall / single-dimension).
"""

import json
import math
import os
import statistics as st
import sys
from collections import Counter


FLOAT_METRICS = [
    ("tool_call_error_rate",   "Tool-call error rate"),
    ("edit_failure_rate",      "edit_file failure rate"),
    ("redundant_call_rate",    "Redundant-call rate"),
    ("exploration_ratio",      "Exploration ratio (reads / edits)"),
    ("verification_discipline","Verification discipline ((flow+report)/edits)"),
    ("error_recovery_rate",    "Error-recovery rate"),
]

BINARY_METRICS = [
    ("iteration_exhausted",    "Hit iteration limit"),
    ("premature_exit",         "Premature-exit (ended without success, no exhaust)"),
    ("no_verification",        "No-verification (edits but never ran flow)"),
]


def _collect(roots):
    data = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, _, fn in os.walk(root):
            if "trajectory_stats.json" in fn:
                try:
                    data.append(json.load(open(os.path.join(dp, "trajectory_stats.json"))))
                except Exception:
                    pass
    return data


def _fmt_float(vals):
    vals = [v for v in vals if v is not None and isinstance(v, (int, float)) and not math.isnan(v)]
    if not vals:
        return "N/A"
    if len(vals) == 1:
        return f"{vals[0]:.3f} (n=1)"
    return f"{st.mean(vals):.3f} +/- {st.stdev(vals):.3f} (n={len(vals)})"


def _fmt_rate(vals):
    vals = [int(v) for v in vals if v in (0, 1)]
    if not vals:
        return "N/A"
    r = sum(vals) / len(vals)
    return f"{r:.3f}  ({sum(vals)}/{len(vals)})"


def _mean(vals):
    vals = [v for v in vals if v is not None and isinstance(v, (int, float)) and not math.isnan(v)]
    if not vals:
        return None
    return sum(vals) / len(vals)


def _split_by_success_block(data):
    """Emit a compact table contrasting float metrics for success vs fail attempts.

    Requires `success` field in each stats dict. Attempts without the field are
    skipped (older trajectory_stats.json written before the field was added).
    """
    succ = [d for d in data if d.get("success") == 1]
    fail = [d for d in data if d.get("success") == 0]
    have_success_field = sum(1 for d in data if "success" in d)
    if have_success_field == 0:
        print("  (success flag missing from trajectory_stats.json — "
              "regenerate with analyze_trajectory.py)")
        return
    print(f"  n_success={len(succ)}   n_fail={len(fail)}")
    print()
    header = f"  {'Metric':45s} {'success':>10s}  {'fail':>10s}  {'delta':>10s}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for key, label in FLOAT_METRICS:
        sm = _mean([d.get(key) for d in succ])
        fm = _mean([d.get(key) for d in fail])
        def _fmt(x):
            return "  N/A" if x is None else f"{x:10.3f}"
        delta = "  N/A" if (sm is None or fm is None) else f"{(sm - fm):+10.3f}"
        print(f"  {label:45s} {_fmt(sm)}  {_fmt(fm)}  {delta}")
    print()
    # Also break down binary flags by success/fail
    for key, label in BINARY_METRICS:
        sv = [int(d[key]) for d in succ if key in d]
        fv = [int(d[key]) for d in fail if key in d]
        sr = (sum(sv) / len(sv)) if sv else None
        fr = (sum(fv) / len(fv)) if fv else None
        def _pct(x):
            return "  N/A" if x is None else f"{x*100:9.1f}%"
        print(f"  {label:45s} {_pct(sr)}  {_pct(fr)}")


def summarize(data):
    if not data:
        print("  (no trajectory_stats.json found)")
        return
    print(f"  Attempts analyzed:           {len(data)}")
    print(f"  Mean tool calls / attempt:   "
          f"{_fmt_float([d.get('n_tool_calls') for d in data])}")
    print()
    print("  --- Reasoning / behavior metrics ---")
    for key, label in FLOAT_METRICS:
        print(f"  {label:45s} {_fmt_float([d.get(key) for d in data])}")
    print()
    print("  --- Binary flags (rate) ---")
    for key, label in BINARY_METRICS:
        print(f"  {label:45s} {_fmt_rate([d.get(key) for d in data])}")
    print()
    print("  --- Split by attempt outcome (success vs fail) ---")
    _split_by_success_block(data)
    print()
    print("  --- OpenROAD flow outcomes (per-attempt) ---")
    fc = Counter(d.get("flow_recovery", "unknown") for d in data)
    for k in ("no_flow", "no_fail", "recovered", "never_recovered", "unknown"):
        if fc.get(k):
            print(f"  flow_recovery={k:17s} {fc[k]}")
    tot_flow_calls = sum(d.get("n_flow_calls", 0) for d in data)
    tot_flow_fail  = sum(d.get("n_flow_fail", 0)  for d in data)
    if tot_flow_calls:
        print(f"  run_openroad_flow fail rate: "
              f"{tot_flow_fail}/{tot_flow_calls} "
              f"= {tot_flow_fail / tot_flow_calls:.3f}")
    print()
    print("  --- Tool-call composition (avg per attempt) ---")
    tools = Counter()
    for d in data:
        for tn, c in (d.get("per_tool_counts") or {}).items():
            tools[tn] += c
    if tools:
        for tn, total in sorted(tools.items(), key=lambda kv: -kv[1]):
            print(f"  {tn:24s} avg={total / len(data):.2f}  (total={total})")


def main():
    if len(sys.argv) < 2:
        sys.stderr.write("usage: summarize_trajectory_stats.py <root_dir> ...\n")
        sys.exit(1)
    data = _collect(sys.argv[1:])
    summarize(data)


if __name__ == "__main__":
    main()
