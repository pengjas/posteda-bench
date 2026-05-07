#!/usr/bin/env python3
"""Latency-sweep comparator for cycle-stamped equivalence logs.

Each log line: "<index> <value_field1> <value_field2> ..."
where <index> is a monotonic cycle number. Sweeps latency offset L in
[0, sweep] and checks if orig[t] == mod[t-L] for all t in the common window.

Pass if any L yields zero mismatches over the overlap region.
"""
import argparse
import sys


def load(path):
    """Return list of (index, payload_str)."""
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 1)
            if not parts:
                continue
            try:
                idx = int(parts[0])
            except ValueError:
                continue
            payload = parts[1] if len(parts) > 1 else ""
            out.append((idx, payload))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("orig")
    ap.add_argument("mod")
    ap.add_argument("--sweep", type=int, default=16,
                    help="max latency offset to try")
    ap.add_argument("--warmup", type=int, default=8,
                    help="skip first N cycles after reset")
    args = ap.parse_args()

    orig = load(args.orig)
    mod = load(args.mod)
    if not orig or not mod:
        print("FAIL: empty log")
        return 1

    # Build lookup: cycle_index -> payload
    orig_map = dict(orig)
    mod_map = dict(mod)
    orig_cycles = sorted(orig_map.keys())
    mod_cycles = sorted(mod_map.keys())
    start = max(orig_cycles[0], mod_cycles[0]) + args.warmup
    end = min(orig_cycles[-1], mod_cycles[-1])
    if end <= start:
        print("FAIL: no overlap window")
        return 1

    # Mod[t] should match orig[t - L] (mod is delayed by L extra cycles)
    # Also try negative L (mod faster) in case agent reduced pipeline depth.
    best_L = None
    best_miss = None
    for L in range(-args.sweep, args.sweep + 1):
        miss = 0
        total = 0
        for t in range(start, end + 1):
            if t not in mod_map or (t - L) not in orig_map:
                continue
            total += 1
            if mod_map[t] != orig_map[t - L]:
                miss += 1
        if total == 0:
            continue
        if best_miss is None or miss < best_miss:
            best_miss = miss
            best_L = L
        if miss == 0:
            print(f"PASS: equivalent with latency offset L={L} ({total} samples)")
            return 0

    print(f"FAIL: no latency offset worked; best was L={best_L} "
          f"with {best_miss} mismatches")
    return 1


if __name__ == "__main__":
    sys.exit(main())
