# PostEDA-Bench — Deployment & Reproduction

[← Back to the project overview](README.md)

The original setup, deployment, and evaluation instructions are preserved below.

An anonymized benchmark and agent suite for evaluating LLM agents on
**post-EDA** physical-design tasks: DRC violation fixing on layout (GDS), and
PPA (period / power / area) optimization on a real OpenROAD flow.

This repository accompanies an anonymous double-blind submission. All
identifying information (paths, accounts, names) has been removed; reproduce by
following the steps below.

```
posteda-bench/
├── agents/                  # LLM agent baselines (DRC and PPA)
│   ├── drc/{react,tot,reflexion,proposer_critic,tools}/
│   └── ppa/{react,tot,reflexion,proposer_critic,orfs_agent,tools}/
├── benchmark/
│   ├── drc_bench/           # 70 DRC tasks (drc_essential ∪ drc_reasoning, L1–L3)
│   └── ppa_bench/           # 75 PPA tasks (ppa_mono/{area,power,perf}, ppa_multi)
├── eval/
│   ├── eval_drc_sr.sh       # DRC eval: success rate + error reduction
│   ├── eval_ppa_sr.sh       # PPA eval: success rate + violation reduction
│   ├── ppa_metric_collection.py
│   ├── FlowVariables.md     # OpenROAD flow-variable reference (used by the agent)
│   └── equiv/               # Post-agent testbench-based RTL functional checks
├── scripts/
│   └── serve_vllm.sh        # Launch a local vLLM server for the agents
└── requirements.txt         # Pinned Python dependencies (conda env `posteda`)
```

## Agent development and repository guide

Explore the [DRC baselines](agents/drc), [PPA baselines](agents/ppa), and the [ORFS agent guide](agents/ppa/orfs_agent/README.md). Study how different reasoning strategies handle tool use, iteration budgets, and the moment a promising edit meets an unforgiving report.

The repository includes benchmark inputs, reference artifacts, agent implementations, evaluation harnesses, and pinned Python dependencies. The sections below cover the full path from environment setup to evaluation.

| Start here | What you will find |
| --- | --- |
| [Deployment & reproduction](#1-prerequisites) | Toolchain setup, environment variables, evaluation commands, outputs, and a smoke test. |
| [DRC benchmark](benchmark/drc_bench) | Layout-repair tasks across essential and reasoning splits. |
| [PPA benchmark](benchmark/ppa_bench) | Single-objective and multi-objective optimization tasks. |
| [Agent suite](agents) | Baseline reasoning strategies and EDA tool interfaces. |
| [Evaluation harnesses](eval) | DRC/PPA scoring and the RTL sanity-check machinery. |
| [Dataset metadata](croissant.json) | Machine-readable dataset description in Croissant format. |

## 1. Prerequisites

### System packages
- Linux (tested on Ubuntu 22.04 / 24.04)
- `bash >= 5.1` (`wait -p`, used with `wait -n` by the eval scripts,
  requires Bash 5.1 or newer)
- Python **3.11.11** (the reference version; install it with the conda command
  below). Python 3.10 is unsupported because the pinned NumPy 2.4.1 requires
  Python 3.11 or newer.
- `git`, `wget`, `jq`, `bc`, and `xauth`; `tput`, `realpath`, and `basename`
  are preinstalled. Stock Ubuntu does not ship `jq` (nor `bc` on Ubuntu
  Server), and ORFS's `util/docker_shell` calls `xauth`, so install them:
  ```bash
  sudo apt-get update
  sudo apt-get install -y git wget jq bc xauth
  ```

### EDA toolchain
- **Docker Engine** — required for PPA tasks: the agent invokes ORFS through
  `flow/util/docker_shell`. Install Docker using the
  [official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/),
  then start the daemon and grant the account that will run the agents access:
  ```bash
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"
  ```
  Log out and log back in to apply the group membership, then verify Docker
  works **without `sudo`**:
  ```bash
  docker run --rm hello-world
  ```
  Complete this step before setting up ORFS below. A native OpenROAD
  installation alone does not satisfy the agent's Docker requirement.
- **KLayout 0.30.3** — required for DRC tasks (the reference version). Provides
  the `klayout` CLI, whose bundled Python supplies `pya` / `klayout.db` to the
  scripts the DRC tools generate. Do not use `apt-get install klayout`: Ubuntu's
  own package is older (0.26.2 on 22.04, 0.28.x on 24.04). Install the official
  package for your Ubuntu release (22 or 24) from klayout.org instead (the
  older klayout.de download links redirect there):
  ```bash
  UBUNTU_MAJOR=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
  wget "https://www.klayout.org/downloads/Ubuntu-${UBUNTU_MAJOR}/klayout_0.30.3-1_amd64.deb"
  sudo apt-get install -y ./klayout_0.30.3-1_amd64.deb
  klayout -v                           # should print: KLayout 0.30.3
  ```
- **OpenROAD-flow-scripts (ORFS)** — required for PPA tasks. Use the exact
  ORFS commit and Docker image that produced the benchmark's reference results:
  ```bash
  git clone https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts
  cd OpenROAD-flow-scripts
  git checkout a2bb042b6
  export OPENROAD_FLOW_DIR=$(pwd)/flow
  export OR_IMAGE=openroad/orfs:v3.0-4639-ga2bb042b6
  docker pull "$OR_IMAGE"
  ```
  Do not use the latest ORFS. From ORFS `26Q2` onward, `util/docker_shell`
  runs `make` inside the image's own flow directory, so every flow the agents
  launch fails. Other ORFS versions or images also produce different PPA
  numbers from the reference values in the tasks.

  No `--recursive` clone or `./build_openroad.sh` is needed:
  `util/docker_shell` runs OpenROAD and Yosys from the `$OR_IMAGE` Docker
  image. If `OR_IMAGE` is unset, it falls back to `openroad/orfs:latest`, which
  gives different results. Keep `OPENROAD_FLOW_DIR` and `OR_IMAGE` exported in
  every shell that runs the agents or eval scripts. The ASAP7 PDK ships with
  ORFS.

  Verify that the same wrapper used by the agents can launch both EDA tools:
  ```bash
  (
    cd "$OPENROAD_FLOW_DIR"
    util/docker_shell openroad -version
    util/docker_shell yosys -V
  )
  ```
  Both commands must succeed before running PPA evaluation. Once the Python
  environment is set up, the
  [PPA reference check](#ppa-reference-check-no-model-needed) in section 7
  confirms that re-running the flow on the unmodified `ppa_multi/L1/q1` design
  reproduces its reference metrics (effective period 319.69 ps, power
  0.00161 W).
- **Yosys** — included in the ORFS Docker image above; no host install needed.
- **Icarus Verilog** — required on the **host** for PPA functional checks.
  `eval/equiv/check_equiv.sh` invokes `iverilog` and `vvp` outside the ORFS
  container. Install and verify both executables:
  ```bash
  sudo apt-get update
  sudo apt-get install -y iverilog
  iverilog -V
  vvp -V
  ```
  Missing executables cause `FAIL_INFRA` on designs with supported testbenches.
  Ubuntu packages Icarus Verilog 11.0 on 22.04 and 12.0 on 24.04; both pass
  the functional checks.

### Get the code

Clone this repository outside the ORFS checkout and run the Python setup below
from its root:

```bash
cd ~    # or wherever you keep your checkouts
git clone https://github.com/pengjas/posteda-bench.git
cd posteda-bench
```

### Python environment

The agents and eval scripts run inside the `posteda` conda environment
(Python 3.11.11):

```bash
conda create -n posteda python=3.11.11
conda activate posteda
pip install -r requirements.txt
```

`requirements.txt` pins every package to the version used in the reference
environment. Three of them are optional: `pillow` is needed only by the
vision agents, `langchain-google-genai` only by the Gemini backend, and
`scikit-learn` only by `orfs_agent`'s GP surrogate — without it,
`gp_optimizer.py` falls back to Latin-hypercube sampling rather than
failing.

Note that the DRC tools never `import pya` / `klayout.db` in this
environment — those imports live inside scripts handed to the `klayout -b -r`
CLI, which runs them under KLayout's own bundled Python. Only the `klayout`
binary needs to be on `$PATH`.

## 2. Configure environment variables

Set these in every shell where you run the commands in this guide:

```bash
# Absolute path to your clone of this repository. The commands below use it.
export REPO_ROOT=/abs/path/to/posteda-bench

# ORFS setup from section 1 (PPA only)
export OPENROAD_FLOW_DIR=/abs/path/to/OpenROAD-flow-scripts/flow
export OR_IMAGE=openroad/orfs:v3.0-4639-ga2bb042b6

# Model access: you must set up your own models (see section 5).
# OpenAI-based agents (gpt_*.py):
export OPENAI_API_KEY=sk-...
# Agents that use your own OpenAI-compatible server, e.g. a local vLLM server
# started with scripts/serve_vllm.sh (section 5). VLLM_MODEL must match the
# served model name. orfs_agent.py ignores both variables; edit the constants
# in that file instead (section 5).
export VLLM_BASE_URL=http://localhost:9888/v1
export VLLM_MODEL=google/gemma-4-31B-it

# Optional: where to write evaluation outputs. Each harness has its own default
# ($REPO_ROOT/results/drc/react/v0 and $REPO_ROOT/results/ppa/orfs_agent/v0).
# Do not export one value for both: each harness writes final_summary_report.log
# at the top of RESULT_DIR_RAW, so the second run would overwrite the first.
# Set it per invocation instead:
#   RESULT_DIR_RAW=/abs/path/to/results/drc ./eval_drc_sr.sh
#   RESULT_DIR_RAW=/abs/path/to/results/ppa ./eval_ppa_sr.sh
```

The eval scripts locate the repository from their own location, so
`REPO_ROOT` only needs to point at your clone for the commands in this guide.
No path inside the repo encodes your username, home directory, or account
info.

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
  file**: `eval_drc_sr.sh` keeps it out of the agent's sandbox and copies it in
  only after the agent exits, to score the run.
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
(ORFS makefile + SDC), `src/` (RTL), and the reference run's `logs/` and
`reports/`. The reference `results/` databases (`.odb`, `.gds`) are not
shipped (`benchmark/**/results/` is gitignored); re-running the flow
regenerates them. The agent edits files in `config/` and `src/` inside its
sandbox copy and re-runs the OpenROAD flow.

## 4. Running the eval harnesses

Activate the environment first: `conda activate posteda`. Set up your model
before running either harness (section 5): both default agents
(`gemma_react.py` for DRC, `orfs_agent.py` for PPA) call a model server you
run yourself.

### DRC
```bash
cd "$REPO_ROOT/eval"
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
cd "$REPO_ROOT/eval"
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

After every PPA attempt, `eval/equiv/check_equiv.sh` runs testbench-based RTL
functional checks for designs with supported testbenches; detected functional
divergence is marked `FAIL_FUNCTIONAL` even when PPA targets are met.

## 5. LLM backends

This repository does not provide models or API access. You need to set up
your own models before running any agent:

- **OpenAI-based agents** (`gpt_*.py`) call the OpenAI API and read
  `OPENAI_API_KEY`.
- **All other agents** call an OpenAI-compatible server that you run
  yourself. You can serve any local model with [vLLM](https://docs.vllm.ai)
  (see below) and point the agents at it with `VLLM_BASE_URL` and
  `VLLM_MODEL`; the table below shows which setting each agent reads.
- **`orfs_agent.py`** (the default PPA agent) is configured by the constants
  at the top of the file (lines ≈55–80): `LLM_BACKEND` (`"vllm"` or
  `"openai"`), `VLLM_MODEL`, `VLLM_BASE_URL`, and `OPENAI_MODEL`.

### Serving a local model with vLLM

Install vLLM in its own environment (it pins its own PyTorch/CUDA stack), then
start the server with `scripts/serve_vllm.sh`:

```bash
# Default: google/gemma-4-31B-it on http://localhost:9888/v1
CUDA_VISIBLE_DEVICES=0 scripts/serve_vllm.sh
# Any other model, e.g. Qwen3.5 on two GPUs:
CUDA_VISIBLE_DEVICES=0,1 TP=2 scripts/serve_vllm.sh Qwen/Qwen3.5-27B
```

The server needs three settings, which the script passes for Gemma 4 and
Qwen3.5 models. For other model families set `TOOL_CALL_PARSER` and
`REASONING_PARSER`; `DRY_RUN=1` prints the command without running it.

- **Tool calling**: `--enable-auto-tool-choice --tool-call-parser <parser>`
  (`gemma4` for Gemma 4, `qwen3_coder` for Qwen3.5). Without it, every agent
  request fails with HTTP 400 (`"auto" tool choice requires
  --enable-auto-tool-choice and --tool-call-parser to be set`).
- **A reasoning parser**: `--reasoning-parser <parser>` (`gemma4` or `qwen3`).
  The agents request thinking (`enable_thinking`), and the parser returns it
  separately from the answer and the tool calls.
- **Enough context**: `--max-model-len 65536` or more. `orfs_agent`'s setup
  prompt alone is about 18k tokens. With a 32k window a thinking model can run
  out of context before it answers, and the agent then logs
  `ERROR: empty search space; aborting.`

The script prints the matching `VLLM_BASE_URL` and `VLLM_MODEL` exports. Set
`SERVED_MODEL_NAME` to serve a model under the name an agent expects (e.g.
`SERVED_MODEL_NAME=qwen3.5-9b` matches `orfs_agent.py`'s default constant).
The script binds to `127.0.0.1`; set `HOST=0.0.0.0` if the agents run on
another machine.

### How each agent finds its model

Every vLLM-backed agent defaults to `http://localhost:9888/v1`, the script's
default endpoint.

| Agent | Model name | Endpoint |
|---|---|---|
| `drc/react/gemma_react.py` (default DRC agent) | `VLLM_MODEL` (default `google/gemma-4-31B-it`) | `VLLM_BASE_URL` |
| `drc/reflexion/gemma_reflexion.py`, `drc/tot/gemma_tot.py` | `VLLM_MODEL` (default `google/gemma-4-31B-it`) | `VLLM_BASE_URL` |
| `drc/react/vision_gemma_react.py` | `qwen3.5-122b`, set in the file (needs a vision model) | `VLLM_BASE_URL` |
| `drc/proposer_critic/vllm_proposer_critic.py` | `qwen3.5-9b`, set in the file | `VLLM_BASE_URL` |
| `ppa/orfs_agent/orfs_agent.py` (default PPA agent) | constant `VLLM_MODEL` (`qwen3.5-9b`) | constant `VLLM_BASE_URL` |
| `ppa/react/gemma_react.py`, `ppa/reflexion/gemma_reflexion.py`, `ppa/tot/gemma_tot.py` | `VLLM_MODEL` (default `Qwen/Qwen3.5-27B`) | `VLLM_BASE_URL` |
| `ppa/proposer_critic/vllm_proposer_critic.py` | `qwen3.5-9b`, set in the file | `VLLM_BASE_URL` |
| `drc/reflexion/gpt_reflexion.py`, `drc/tot/gpt_tot.py` | `gpt-5-mini`, set in the file | OpenAI API (`OPENAI_API_KEY`) |
| `ppa/react/gpt_react.py`, `ppa/reflexion/gpt_reflexion.py`, `ppa/tot/gpt_tot.py` | `gpt-5`, set in the file | OpenAI API (`OPENAI_API_KEY`) |

Confirm your endpoint works before starting an evaluation, e.g.
`curl -s "$VLLM_BASE_URL/models"`. If the server is unreachable or times out,
some agents (including the default DRC agent `gemma_react.py`) keep retrying
instead of exiting. `gemma_react.py` stops with `AGENT RUN FAILED DUE TO ERROR`
when the server rejects its request, e.g. for an unknown model name or with
tool calling disabled.

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
  between initial metric and target, averaged across tasks. Single-objective
  scores are clipped to [0, 1]. Multi-objective (`ppa_multi`) scores have no
  lower bound: each violated metric's score is capped at 1 but can go
  negative, and breaking a constraint that was already met scores
  −(final − target) / (target − initial), which grows quickly when the
  initial slack is small. One such attempt can dominate the average: an
  attempt that improved the period but raised power from 0.00161 W to
  0.151 W against a 0.002 W limit scored VRR = −190.7 for that task (see
  `eval/ppa_score.py`).

## 7. Quick smoke-test

Run a single DRC task end-to-end without the harness. This runs
`gemma_react.py`, so first start your model server and set `VLLM_BASE_URL` and
`VLLM_MODEL` (section 5), and set `REPO_ROOT` (section 2):
```bash
conda activate posteda
cd /tmp && rm -rf sb && mkdir sb && cd sb
cp -L $REPO_ROOT/benchmark/drc_bench/drc_essential/L1/q1/* .
rm info.json    # scoring ground truth; the agent never sees it
export PYTHONPATH=$REPO_ROOT
export AGENT_RESULT_DIR=$(pwd)
python $REPO_ROOT/agents/drc/react/gemma_react.py \
    --drc_rpt_path ./prompt.txt --temperature 0 --iterations 8
```
You should see `AGENT RUN COMPLETE. TOTAL TOOL CALLS: <n>` followed by a
modified `report.lyrdb` and `modified.gds` in the working directory.

### PPA reference check (no model needed)

Re-run the OpenROAD flow on the unmodified `ppa_multi/L1/q1` design with the
agents' own flow tool, then score it with the harness's metric script. This
needs `OPENROAD_FLOW_DIR` and `OR_IMAGE` (section 2) and takes 1–3 minutes:
```bash
conda activate posteda
T=$REPO_ROOT/benchmark/ppa_bench/ppa_multi/L1/q1
cd "$(mktemp -d)"
cp -rL $T/config $T/src $T/info.json $REPO_ROOT/eval/ppa_metric_collection.py .
export PPA_SANDBOX_ROOT=$PWD PYTHONPATH=$REPO_ROOT
python -c "from agents.ppa.tools.run_openroad import run_openroad_flow as t; print(t.invoke({'target': 'finish'}))"
python ppa_metric_collection.py
jq '{final_effective_period, final_power, final_area}' info.json
```
The flow should report `SUCCESS: Flow completed for gcd`, and the metrics
should match the reference values: effective period 319.69 ps, power
0.00161 W, area 293.163 µm².

## 8. Notes for reviewers

- The eval scripts find the repository from their own location, so no paths
  inside the repo need editing. Only set `REPO_ROOT` (section 2) for the
  commands in this guide.
- The benchmark snapshots under `benchmark/ppa_bench/*/{logs,reports}`
  contain reference OpenROAD outputs; absolute filesystem paths inside those
  files have been replaced with `$ORFS_HOME` / `$REPO_ROOT` placeholders so
  they no longer leak the original author's environment.
- API keys are read from the environment only; nothing is hardcoded in the
  source.
