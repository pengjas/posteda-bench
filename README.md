# PostEDA-Bench

An anonymized benchmark and agent suite for evaluating LLM agents on
**post-EDA** physical-design tasks: DRC violation fixing on layout (GDS), and
PPA (period / power / area) optimization on a real OpenROAD flow.

This repository accompanies an anonymous double-blind submission. All
identifying information (paths, accounts, names) has been removed; reproduce by
following the steps below.

```
posteda_bench/
├── agents/                  # LLM agent baselines (DRC and PPA)
│   ├── drc/{react,tot,reflexion,proposer_critic,tools}/
│   └── ppa/{react,tot,reflexion,proposer_critic,orfs_agent,tools}/
├── benchmark/
│   ├── drc_bench/           # 70 DRC tasks (drc_essential ∪ drc_reasoning, L1–L3)
│   └── ppa_bench/           # 75 PPA tasks (ppa_mono/{area,power,perf}, ppa_multi)
└── eval/
    ├── eval_drc_sr.sh       # DRC eval: success rate + error reduction
    ├── eval_ppa_sr.sh       # PPA eval: success rate + violation reduction
    ├── ppa_metric_collection.py
    ├── FlowVariables.md     # OpenROAD flow-variable reference (used by the agent)
    └── equiv/               # Post-agent RTL functional-equivalence sanity check
```

## 1. Prerequisites

### System packages
- Linux (tested on Ubuntu 22.04 / 24.04)
- `bash >= 4.3` (the eval scripts use `wait -n -p`)
- `python >= 3.10`
- `jq`, `bc`, `tput`, `realpath`, `basename` (standard on most distros)

### EDA toolchain
- **KLayout** ≥ 0.28 — required for DRC tasks. Provides the `klayout` CLI and
  the `pya` / `klayout.db` Python bindings.
  ```bash
  sudo apt-get install klayout         # or build from source
  pip install klayout                  # python bindings
  ```
- **OpenROAD-flow-scripts (ORFS)** — required for PPA tasks. Clone and build
  according to upstream instructions, then expose the flow directory:
  ```bash
  git clone --recursive https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts
  cd OpenROAD-flow-scripts && ./build_openroad.sh
  export OPENROAD_FLOW_DIR=$(pwd)/flow
  ```
  ASAP7 PDK is required (shipped with ORFS).
- **Yosys** ≥ 0.30 (already pulled in by the ORFS build).

### Python packages
```bash
pip install -r requirements.txt
```
where `requirements.txt` contains at minimum:
```
langchain
langchain-openai
langgraph
openai
numpy
scikit-learn          # for orfs_agent's GP surrogate
Pillow                # only needed for vision agents
```
Installing `klayout`'s Python module via pip exposes `pya` / `klayout.db`
inside Python; equivalent to using KLayout's bundled python.

## 2. Configure environment variables

The two eval scripts auto-resolve the repo root from their own location;
everything else is overridable via env. Minimum required:

```bash
# Path to your OpenROAD-flow-scripts/flow checkout (PPA only)
export OPENROAD_FLOW_DIR=/abs/path/to/OpenROAD-flow-scripts/flow

# Required if your agent backend is OpenAI:
export OPENAI_API_KEY=sk-...

# Optional: where to write evaluation outputs (defaults under repo/results/)
export RESULT_DIR_RAW=/abs/path/to/results

# Optional: vLLM endpoint when LLM_BACKEND=="vllm" inside the agent
# (override URL / model in the agent file directly if needed)
```

No path inside the repo encodes your username, home directory, or
account info. All references resolve relative to `REPO_ROOT` (the directory
containing this README) or to standard environment variables.

## 3. Benchmark layout

### DRC benchmark (`benchmark/drc_bench/`)
Two splits × three difficulty levels = **70 tasks**
(`drc_essential`: L1×20, L2×10, L3×10; `drc_reasoning`: L1×13, L2×10, L3×7):

| split | description |
|---|---|
| `drc_essential` | clean rule-violation patterns; tests rule recall |
| `drc_reasoning` | composite errors that need multi-step reasoning |

Per-task files (e.g. `drc_bench/drc_essential/L1/q1/`):
- `top_cell.gds` — input layout
- `prompt.txt` — natural-language description of the violation(s)
- `info.json` — *ground truth metadata used by the harness only* (number of
  initial errors, error types, iteration budget). **The agent never sees this
  file** — `eval_drc_sr.sh` reads it directly to score the run.
- `6_drc_count.rpt` — pre-computed DRC report on the input GDS
- `drc_error_collection.py` / `create_errors.py` — scripts used to construct
  the task (provided for transparency; not invoked at eval time)

### PPA benchmark (`benchmark/ppa_bench/`)
Two regimes × difficulty levels = **75 tasks**
(`ppa_mono/{area,power}`: 10 each; `ppa_mono/perf`: 15; `ppa_multi`: 40):

| regime | objectives |
|---|---|
| `ppa_mono/area`, `ppa_mono/power`, `ppa_mono/perf` | single-objective tasks |
| `ppa_multi`                                        | joint period+power tasks |

Per-task files include `prompt.txt`, `info.json` (targets only), `config/`
(ORFS makefile + SDC), `src/` (RTL), and a complete reference run under
`logs/`, `reports/`, `results/`. The agent edits files in `config/` and `src/`
inside its sandbox copy and re-runs the OpenROAD flow.

## 4. Running the eval harnesses

### DRC
```bash
cd posteda_bench/eval
./eval_drc_sr.sh
```
The script (defaults shown):
- runs every task in `benchmark/drc_bench/{drc_essential,drc_reasoning}` for
  `NUM_RUNS=5` independent attempts
- launches up to `MAX_PARALLEL_AGENTS=5` agents in parallel
- writes per-attempt sandboxes, `cost.json`, `agent_run.log`, and an
  aggregated `final_summary_report.log` under `$RESULT_DIR_RAW`

To swap the agent under test, point `AGENT_SCRIPT` at a different file:
```bash
AGENT_SCRIPT=$REPO_ROOT/agents/drc/reflexion/gpt_reflexion.py ./eval_drc_sr.sh
```
Agents currently shipped:
- `agents/drc/react/{gemma_react.py, vision_gemma_react.py}` — vLLM-backed
  ReAct (vision and non-vision variants)
- `agents/drc/reflexion/{gemma_reflexion.py, gpt_reflexion.py}` — Reflexion
- `agents/drc/tot/{gemma_tot.py, gpt_tot.py}` — Tree-of-Thoughts
- `agents/drc/proposer_critic/vllm_proposer_critic.py`

### PPA
```bash
cd posteda_bench/eval
./eval_ppa_sr.sh
```
Defaults:
- `NUM_RUNS=5`, `MAX_PARALLEL_AGENTS=2` (PPA runs are heavier than DRC)
- `ITERATIONS=16`, `TOOL_TIMEOUT=1800` per OpenROAD invocation
- writes per-attempt sandboxes plus an aggregated summary

The default agent is `agents/ppa/orfs_agent/orfs_agent.py` (LLM-driven setup
followed by a GP optimization loop). Other available baselines:
- `agents/ppa/react/{gpt_react.py, gemma_react.py}`
- `agents/ppa/reflexion/{gpt_reflexion.py, gemma_reflexion.py}`
- `agents/ppa/tot/{gpt_tot.py, gemma_tot.py}`
- `agents/ppa/proposer_critic/vllm_proposer_critic.py`

After every PPA attempt, `eval/equiv/check_equiv.sh` runs an RTL
functional-equivalence sanity check; agents that meet PPA targets but break
the design's functional behavior are marked `FAIL_FUNCTIONAL`.

## 5. LLM backends

Each agent file selects its backend near the top:

| Backend | Where set | Required env |
|---|---|---|
| OpenAI (`gpt-5`, `gpt-5-mini`) | `LLM_BACKEND="openai"` | `OPENAI_API_KEY` |
| vLLM (e.g. `qwen3.5-9b`) | `LLM_BACKEND="vllm"` | local vLLM server at `VLLM_BASE_URL` |
| Google GenAI (Gemini) | (commented branch) | `GOOGLE_API_KEY` |

Edit the constants at the top of the chosen agent file (e.g.
`agents/ppa/orfs_agent/orfs_agent.py`, lines ≈55–80) to switch providers,
models, base URLs, or parallelism.

## 6. Outputs and metrics

Each run of `eval_drc_sr.sh` / `eval_ppa_sr.sh` writes into
`$RESULT_DIR_RAW/run<N>/<benchmark_path>/q<i>/`:
- `agent_run.log` — full agent stdout/stderr (tool calls, LLM turns, errors)
- `success.txt`, `err.txt` (DRC) / `vrr.txt` (PPA) — per-attempt scoring
- `cost.json` — input/output/reasoning tokens, USD cost
- `attempt_metrics.txt` — extracted reasoning-process metrics

Aggregated, multi-run statistics (mean ± std for SR / ERR / VRR / tokens /
cost / failure-mode breakdown) land in
`$RESULT_DIR_RAW/final_summary_report.log`.

Headline metrics:
- **SR (Success Rate)**: fraction of attempts where every initial error was
  cleared (DRC) or every PPA target was met (PPA).
- **ERR (Error Reduction Rate)** — DRC: `(init_errors − final_errors) / init_errors`,
  averaged across tasks.
- **VRR (Violation Reduction Rate)** — PPA: closed fraction of the gap
  between initial metric and target, averaged across tasks.

## 7. Quick smoke-test

Run a single DRC task end-to-end without the harness:
```bash
cd /tmp && rm -rf sb && mkdir sb && cd sb
cp -L $REPO_ROOT/benchmark/drc_bench/drc_essential/L1/q1/* .
export PYTHONPATH=$REPO_ROOT
export AGENT_RESULT_DIR=$(pwd)
python $REPO_ROOT/agents/drc/react/gemma_react.py \
    --drc_rpt_path ./prompt.txt --temperature 0 --iterations 8
```
You should see `AGENT RUN COMPLETE. TOTAL TOOL CALLS: <n>` followed by a
modified `report.lyrdb` and `modified.gds` in the working directory.

## 8. Notes for reviewers

- All paths inside the repo resolve to `$REPO_ROOT` (auto-detected from each
  script's own location). No reviewer-side editing of paths is needed.
- The benchmark snapshots under `benchmark/ppa_bench/*/{logs,reports,results}`
  contain reference OpenROAD outputs; absolute filesystem paths inside those
  files have been replaced with `$ORFS_HOME` / `$REPO_ROOT` placeholders so
  they no longer leak the original author's environment.
- API keys are read from the environment only; nothing is hardcoded in the
  source.
