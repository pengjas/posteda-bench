#!/usr/bin/env python3
"""Backfill cost.json for historical PPA runs by parsing agent_run.log.

LangChain responses log lines like:
    usage_metadata={'input_tokens': 2981, 'output_tokens': 9, 'total_tokens': 3183, ...}
    'model_name': 'gemini-3-flash-preview'

This script sums the input_tokens / output_tokens across all LLM calls in one
log, auto-detects the model name, looks up the per-token price from
cost_tracker.TOKEN_COST, and writes cost.json next to the log.

Usage:
    # single attempt
    python3 backfill_cost_from_log.py <agent_run.log> [--model NAME] [--out PATH]

    # bulk: walk a result directory and backfill every agent_run.log
    python3 backfill_cost_from_log.py --walk <result_dir> [--model NAME]
"""

import argparse
import json
import os
import re
import sys

# Reuse the price table + cost schema from the runtime cost tracker so
# historical backfills stay consistent with live runs.
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, ".."))
sys.path.insert(0, _REPO_ROOT)
from agents.ppa.react import cost_tracker  # noqa: E402


USAGE_RE = re.compile(
    r"usage_metadata=\{[^}]*'input_tokens':\s*(\d+)[^}]*'output_tokens':\s*(\d+)"
    r"[^}]*'total_tokens':\s*(\d+)"
)
MODEL_RE = re.compile(r"'model_name':\s*'([^']+)'")


def parse_log(log_path):
    try:
        text = open(log_path, errors="replace").read()
    except OSError:
        return None
    in_tok = out_tok = tot_tok = 0
    for m in USAGE_RE.finditer(text):
        in_tok  += int(m.group(1))
        out_tok += int(m.group(2))
        tot_tok += int(m.group(3))
    m = MODEL_RE.search(text)
    model = m.group(1) if m else None
    return {"input_tokens": in_tok, "output_tokens": out_tok,
            "total_tokens": tot_tok, "model": model}


def write_cost(log_path, parsed, model_override=None, out_path=None):
    model = model_override or parsed.get("model") or "unknown"
    rates = cost_tracker.TOKEN_COST.get(model, {"input": 0.0, "output": 0.0})
    input_cost  = parsed["input_tokens"]  * rates["input"]
    output_cost = parsed["output_tokens"] * rates["output"]
    total_cost  = input_cost + output_cost
    data = {
        "model": model,
        "input_tokens":  parsed["input_tokens"],
        "output_tokens": parsed["output_tokens"],
        "total_tokens":  parsed["total_tokens"],
        "input_cost_usd":  round(input_cost,  6),
        "output_cost_usd": round(output_cost, 6),
        "total_cost_usd":  round(total_cost,  6),
        "backfilled": True,
    }
    path = out_path or os.path.join(os.path.dirname(log_path), "cost.json")
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    return path, data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log_path", nargs="?",
                    help="Path to agent_run.log (omit when using --walk)")
    ap.add_argument("--walk", metavar="DIR",
                    help="Walk DIR recursively and backfill every agent_run.log")
    ap.add_argument("--model", default=None,
                    help="Override model name (default: auto-detect from log)")
    ap.add_argument("--out", default=None,
                    help="Output path (single-file mode only)")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite an existing cost.json that is not marked backfilled")
    args = ap.parse_args()

    if args.walk:
        n_written = n_skipped = n_failed = 0
        for dp, _, fn in os.walk(args.walk):
            if "agent_run.log" not in fn:
                continue
            log = os.path.join(dp, "agent_run.log")
            existing = os.path.join(dp, "cost.json")
            if os.path.isfile(existing) and not args.force:
                try:
                    if not json.load(open(existing)).get("backfilled"):
                        n_skipped += 1
                        continue
                except Exception:
                    pass
            parsed = parse_log(log)
            if parsed is None:
                n_failed += 1
                continue
            try:
                write_cost(log, parsed, model_override=args.model)
                n_written += 1
            except Exception as e:
                print(f"  failed on {log}: {e}", file=sys.stderr)
                n_failed += 1
        print(f"backfilled {n_written}, skipped {n_skipped} (live cost.json exists), "
              f"failed {n_failed}")
        return

    if not args.log_path:
        ap.error("log_path is required unless --walk is used")
    parsed = parse_log(args.log_path)
    if parsed is None:
        sys.stderr.write(f"log not found: {args.log_path}\n")
        sys.exit(1)
    path, data = write_cost(args.log_path, parsed,
                             model_override=args.model, out_path=args.out)
    print(f"wrote {path}")
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
