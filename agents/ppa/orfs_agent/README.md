# orfs_agent — ORFS-Agent adapted for posteda_bench PPA

LLM-driven Bayesian-optimization-style agent. Discovers the search space once
via an LLM call, then runs a 6-iteration × 3-parallel-candidates GP loop. Each
candidate runs a real OpenROAD flow via `agents.ppa.tools.run_openroad_flow`.

## Files

| file | purpose |
|---|---|
| `orfs_agent.py` | CLI entry; orchestrates outer loop |
| `search_space_discovery.py` | one LLM call: prompt + config + FlowVariables → tunable params |
| `gp_optimizer.py` | sklearn GP + LHS + UCB + diverse top-k |
| `parallel_executor.py` | spawns N candidate ORFS runs concurrently in per-candidate work_dirs |
| `param_apply.py` | writes a candidate vector into the sandbox's .mk / .sdc files |
| `metrics.py` | extracts period/power/area; computes SR + VRR (mirrors `ppa_score.py`) |
| `prompts.py` | LLM system + user prompt for the setup phase |

## What's different from upstream ORFS-Agent

| upstream | here |
|---|---|
| Hardcoded designs (aes/ibex/jpeg × asap7/sky130hd) | LLM-discovered per task |
| `opt_config.json` + per-design baselines maintained by humans | LLM reads FlowVariables.md + the task's own config |
| Objectives = ECP / DWL / COMBO | Real targets parsed from `info.json` (area / power / period / multi); GP trained on negative VRR |
| 6 outer × 50 parallel ORFS runs (~300/run) | 6 outer × 3 parallel (18/run) |
| Driver = bash + `make INT_PARAM=…` against ORFS tree | Per-candidate work_dir copy of the benchmark sandbox; uses `run_openroad_flow` tool which handles `_temp_runs/<uuid>` isolation |
| LLM in every iteration to choose GP meta-params | LLM only at setup; GP uses fixed Matern + UCB + diversity |

## Running

The agent conforms to the same CLI contract as `agents/ppa/react/gpt_react.py`:

```
python -m agents.ppa.orfs_agent.orfs_agent \
    --prompt_path ./prompt.txt \
    --temperature 0.0 \
    --iterations 18
```

It reads:
- `$PPA_SANDBOX_ROOT` (or CWD) — the per-task sandbox
- `$AGENT_RESULT_DIR` — where to drop `cost.json`, `optim_history.json`, `orfs_summary.json`, `search_space.json`
- `$TOOL_TIMEOUT` (default 1800s) — per-candidate ORFS timeout
- `$ORFS_PARALLEL` (default 3) — candidates per iteration
- `$ORFS_OUTER_ITERS` (default 6) — outer iterations
- `$ORFS_MODEL` (default `gpt-5`) — OpenAI model
- `$OPENAI_API_KEY` — required when `LLM_BACKEND="openai"`

It writes (back into the sandbox, so the eval harness verifier picks them up):
- `config/`, `src/`, `reports/`, `logs/`, `results/` from the BEST candidate
- (info.json is left for `eval/ppa_metric_collection.py` to update)

## Plugging into eval_ppa_sr.sh

Edit `eval/eval_ppa_sr.sh` so that `AGENT_SCRIPT` points at this file (under
the repo root):

```bash
AGENT_SCRIPT="${REPO_ROOT}/agents/ppa/orfs_agent/orfs_agent.py"
```

Everything else (sandbox creation, info.json scoring, SR/VRR aggregation,
trajectory analysis) stays the same — the agent already conforms to the
contract: it prints
`AGENT RUN COMPLETE. TOTAL TOOL CALLS: <n>` and writes `cost.json`.

## Dependencies

```
pip install scikit-learn   # for GP surrogate; without it, falls back to LHS-only
```

(`numpy`, `langchain`, `langchain_openai` already present in the project's env.)

## SR / VRR / other metrics tracked

For each candidate the agent computes:
- `period`, `power`, `area` — from `reports/6_finish.rpt` + `logs/6_report.json`
- `success` — all targeted dimensions ≤ target (matches `ppa_score.py`)
- `vrr` — Violation Reduction Rate (matches `ppa_score.py`)
- `objective` — `-vrr` (lower better; failed flows penalty 1e6)

Per-iteration: `iter_SR` (max success across candidates), `iter_max_VRR`.
Cumulative: `best_objective`, `best_success`, `best_vrr`, `n_candidates_run`,
`successes_total`. Persisted to `optim_history.json` + `orfs_summary.json`.
```
