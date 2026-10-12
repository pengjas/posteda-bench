#!/usr/bin/env python3
"""Compute PPA success flag and VRR (Violation Reduction Rate) from info.json.

Usage:
    python3 ppa_score.py <path/to/info.json>

Output (one line, space-separated):
    <success 0|1> <vrr float>

Success:
    All target metrics satisfied (final <= target).

VRR definition (matches spec in eval_ppa_sr.sh):

Single target metric:
    score = (final - init) / (target - init)      clipped to [0, 1]

Multiple target metrics:
    Type A  := init >  target   (violated, must improve)
    Type B  := init <= target   (constraint, must not regress)

    Rule 1: if type-A set is non-empty AND every type-A metric has
            final >= init (no improvement at all), VRR = 0.
    Otherwise, score each metric:
        Type A: s = (final - init) / (target - init)
                s is capped above at 1.0; lower bound is unbounded
                (can be negative if final is worse than init).
        Type B: if final <= target:          s = 1
                else:                        s = -(final - target) / (target - init)
                (degrades below 0 proportionally to how far past target we went)

    VRR = mean(s) over all target metrics (equal weights 1/N).
"""

import json
import sys


METRICS = [
    (
        "target_effective_period",
        ("initial_effective_period", "init_effective_period"),
        ("final_effective_period", "final_period_min"),
    ),
    (
        "target_power",
        ("initial_power", "init_power"),
        ("final_power", "final_total_power"),
    ),
    (
        "target_area",
        ("initial_area", "init_area"),
        ("final_area", "final_die_area"),
    ),
]


def _pick(info, keys):
    for k in keys:
        v = info.get(k)
        if v is not None:
            return float(v)
    return None


def compute(info):
    targets = []
    for tkey, ikeys, fkeys in METRICS:
        t = info.get(tkey)
        if t is None:
            continue
        t = float(t)
        i = _pick(info, ikeys)
        f = _pick(info, fkeys)
        targets.append((tkey, t, i, f))

    if not targets:
        return 0, 0.0

    success = 1 if all(f is not None and f <= t for _, t, _, f in targets) else 0

    if any(i is None or f is None for _, t, i, f in targets):
        return success, 0.0

    if len(targets) == 1:
        _, t, i, f = targets[0]
        if t == i:
            return success, (1.0 if f <= t else 0.0)
        r = (f - i) / (t - i)
        return success, max(0.0, min(1.0, r))

    # Multi-metric case: partition into Type A (violated) and Type B (constraint).
    type_a = [(t, i, f) for _, t, i, f in targets if i > t]

    # Rule 1: if every Type A metric failed to improve, VRR = 0.
    if type_a and all(f >= i for t, i, f in type_a):
        return success, 0.0

    scores = []
    for _, t, i, f in targets:
        if i > t:
            # Type A: cap above at 1, allow negative
            denom = t - i  # negative
            s = (f - i) / denom
            if s > 1.0:
                s = 1.0
        else:
            # Type B
            if f <= t:
                s = 1.0
            else:
                denom = t - i  # non-negative
                if denom == 0:
                    s = 0.0
                else:
                    s = -(f - t) / denom
        scores.append(s)

    return success, sum(scores) / len(scores)


def main():
    if len(sys.argv) != 2:
        print("0 0.0")
        sys.exit(1)
    try:
        with open(sys.argv[1]) as fh:
            info = json.load(fh)
    except Exception:
        print("0 0.0")
        sys.exit(0)

    success, vrr = compute(info)
    print(f"{success} {vrr:.6f}")


if __name__ == "__main__":
    main()
