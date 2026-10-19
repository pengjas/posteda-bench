# PostEDA-Bench — Deployment & Reproduction

[← Back to the project overview](README.md)

The original setup, deployment, and evaluation instructions are preserved below.

A benchmark and agent suite for evaluating LLM agents on **post-EDA**
physical-design tasks: DRC violation fixing on layout (GDS), and PPA
(period / power / area) optimization on a real OpenROAD flow.

Machine-specific paths and account details have been removed from the
repository, so nothing needs editing for your machine; reproduce the results by
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
│   ├── eval_drc_config.sh   # DRC eval settings: agent, model, runs, tasks
│   ├── eval_drc_sr.sh       # DRC eval: success rate + error reduction
│   ├── eval_ppa_config.sh   # PPA eval settings: agent, model, runs, tasks
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
| [Deployment & reproduction](#1-prerequisites) | Toolchain setup, configuration files, evaluation commands, outputs, and a smoke test. |
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
  gives different results. The eval harness takes both values from
  `eval/eval_ppa_config.sh` (section 2); the exports above serve the commands
  in this guide that run the flow by hand. The ASAP7 PDK ships with ORFS.

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

## 2. Configure your setup

Everything is configured in three files, so nothing needs exporting before a
run:

| File | What you set there |
|---|---|
| `scripts/serve_vllm.sh` | The model to serve (a Hugging Face name or a local checkpoint path), its GPUs, and its port (section 5) |
| `eval/eval_drc_config.sh` | The DRC evaluation: agent, model endpoint and name, runs, tasks, result directory (section 4) |
| `eval/eval_ppa_config.sh` | The same for PPA, plus the ORFS toolchain from section 1: `OPENROAD_FLOW_DIR` (your checkout's `flow` directory) and `OR_IMAGE` |

Both eval config files also hold `REPO_ROOT`, your clone of this repository.
Left empty, it is the clone the eval script runs from, so it needs setting only
to evaluate a different checkout. No path inside the repo encodes your
username, home directory, or account info.

The one exception is the API key of the OpenAI-based agents (`gpt_*.py`),
which stays in your shell so that it never lands in a file you might commit:
```bash
export OPENAI_API_KEY=sk-...
```

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

Each harness takes all of its settings from one config file next to it:
`eval/eval_drc_config.sh` for `eval_drc_sr.sh`, and `eval/eval_ppa_config.sh`
for `eval_ppa_sr.sh`. To run an evaluation:

1. Start your model server (section 5), e.g. `scripts/serve_vllm.sh`.
2. Edit the config file: choose the agent, set `VLLM_BASE_URL` and
   `VLLM_MODEL` to your server's endpoint and served model name (or
   `OPENAI_MODEL` for an OpenAI agent), and adjust the runs, task groups, and
   result directory.
3. Run the harness from the `eval` directory of your clone:
   ```bash
   conda activate posteda
   cd eval
   ./eval_drc_sr.sh      # DRC
   ./eval_ppa_sr.sh      # PPA
   ```

To keep several setups side by side, copy a config file and pass the copy:
`./eval_drc_sr.sh my_drc_config.sh`.

| Setting | DRC default | PPA default | Meaning |
|---|---|---|---|
| `REPO_ROOT` | empty | empty | Your clone of this repository; empty means the one holding the eval script. Relative `AGENT_SCRIPT` and `RESULT_DIR` paths start here |
| `AGENT_SCRIPT` | `agents/drc/react/vllm_react.py` | `agents/ppa/orfs_agent/orfs_agent.py` | Agent to evaluate; the shipped agents are listed below and in the file |
| `AGENT_ARGS` | none | none | Extra agent options, e.g. `(--num_attempts 2)` for Reflexion |
| `ORFS_LLM_BACKEND`, `ORFS_OUTER_ITERATIONS`, `ORFS_PARALLEL_RUNS` | – | `vllm`, `6`, `3` | `orfs_agent` only; see [Configuring orfs_agent](#configuring-orfs_agent) |
| `VLLM_BASE_URL` | `http://localhost:9888/v1` | `http://localhost:9888/v1` | Endpoint the vLLM agents call |
| `VLLM_MODEL` | `google/gemma-4-31B-it` | `qwen3.5-9b` | Model the vLLM agents request; must match the served name |
| `OPENAI_MODEL` | `gpt-5-mini` | `gpt-5` | Model the OpenAI agents request |
| `TEMPERATURE` | `0` | `0` | LLM temperature |
| `ITERATIONS` | `8` | `16` | Budget passed to the agent as `--iterations` |
| `TOOL_TIMEOUT` | – | `1800` | Seconds allowed for one OpenROAD flow |
| `NUM_RUNS` | `5` | `5` | Independent runs; the summary reports mean ± std |
| `MAX_PARALLEL_AGENTS` | `5` | `2` | Attempts running at the same time (PPA attempts are heavier) |
| `TASK_GROUPS` | `drc_essential drc_reasoning` | `ppa_multi ppa_mono/area ppa_mono/power ppa_mono/perf` | Task groups, relative to `benchmark/drc_bench` or `benchmark/ppa_bench`; a level such as `drc_essential/L1` also works |
| `RESULT_DIR` | `results/drc/react/v0` | `results/ppa/orfs_agent/v0` | Output directory |
| `REAGGREGATE_ONLY` | – | `0` | `1` rebuilds the summary from the results in `RESULT_DIR` without running any agent |
| `OPENROAD_FLOW_DIR` | – | `$HOME/OpenROAD-flow-scripts/flow` | Your ORFS checkout's `flow` directory (section 1) |
| `OR_IMAGE` | – | `openroad/orfs:v3.0-4639-ga2bb042b6` | ORFS Docker image; other images give different PPA numbers |

Attempts that already have results in `RESULT_DIR` are skipped, so an
interrupted evaluation resumes where it stopped. Use a new `RESULT_DIR` for
each new agent or model; otherwise its earlier results are reused.

`ITERATIONS` is a different budget for each framework: a ReAct or
proposer-critic attempt stops after that many steps, Reflexion splits it
across its trials, Tree-of-Thoughts spends it as the tool-call budget of the
whole search, and `orfs_agent` ignores it (it runs `ORFS_OUTER_ITERATIONS` ×
`ORFS_PARALLEL_RUNS` flows, 6 × 3 by default).

### DRC
`eval_drc_sr.sh` runs every task in `TASK_GROUPS` for `NUM_RUNS` independent
attempts, up to `MAX_PARALLEL_AGENTS` at a time, and writes per-attempt
sandboxes, `cost.json`, `agent_run.log`, and an aggregated
`final_summary_report.log` under `RESULT_DIR`.

Agents currently shipped:
- `agents/drc/react/{vllm_react.py, vision_vllm_react.py}` — vLLM-backed
  ReAct (non-vision and vision variants)
- `agents/drc/reflexion/{vllm_reflexion.py, gpt_reflexion.py}` — Reflexion
- `agents/drc/tot/{vllm_tot.py, gpt_tot.py}` — Tree-of-Thoughts
- `agents/drc/proposer_critic/vllm_proposer_critic.py`

### PPA
`eval_ppa_sr.sh` works the same way, with each attempt running real OpenROAD
flows. The default agent is `agents/ppa/orfs_agent/orfs_agent.py` (LLM-driven
setup followed by a GP optimization loop). Other available baselines:
- `agents/ppa/react/{gpt_react.py, vllm_react.py}`
- `agents/ppa/reflexion/{gpt_reflexion.py, vllm_reflexion.py}`
- `agents/ppa/tot/{gpt_tot.py, vllm_tot.py}`
- `agents/ppa/proposer_critic/vllm_proposer_critic.py`

After every PPA attempt, `eval/equiv/check_equiv.sh` runs testbench-based RTL
functional checks for designs with supported testbenches; detected functional
divergence is marked `FAIL_FUNCTIONAL` even when PPA targets are met.

### Configuring orfs_agent
`orfs_agent.py` makes one LLM call, which reads the task prompt, the task's
ORFS config files, the names of its RTL files, and `eval/FlowVariables.md`, and
picks the flow parameters to tune with their ranges. It then searches those parameters with a Gaussian
process: `ORFS_OUTER_ITERATIONS` rounds of `ORFS_PARALLEL_RUNS` OpenROAD flows
each, stopping early once every target is met, and keeps the best flow's
outputs for scoring. Its settings are in `eval/eval_ppa_config.sh`:

| Setting | Default | Meaning |
|---|---|---|
| `ORFS_LLM_BACKEND` | `vllm` | `vllm` calls `VLLM_MODEL` at `VLLM_BASE_URL`; `openai` calls `OPENAI_MODEL` (needs `OPENAI_API_KEY`) |
| `ORFS_OUTER_ITERATIONS` | `6` | Search rounds |
| `ORFS_PARALLEL_RUNS` | `3` | Flows per round, run at the same time |

It ignores `ITERATIONS`: an attempt runs at most `ORFS_OUTER_ITERATIONS` ×
`ORFS_PARALLEL_RUNS` flows (18 by default), each limited to `TOOL_TIMEOUT`
seconds, and `MAX_PARALLEL_AGENTS` × `ORFS_PARALLEL_RUNS` flows run at once (6
by default). Besides the usual outputs, each attempt's result directory gets
`search_space.json` (the chosen parameters), `optim_history.json` (every
flow), and `orfs_summary.json` (the best one). See the
[ORFS agent guide](agents/ppa/orfs_agent/README.md) for the details.

### Evaluating your own agent
Set `AGENT_SCRIPT` to your script. The harness copies each task into a fresh
sandbox, runs the agent there, and scores what it leaves behind:

- **Command line**: `--drc_rpt_path ./prompt.txt` (DRC) or
  `--prompt_path ./prompt.txt` (PPA), then
  `--temperature <TEMPERATURE> --iterations <ITERATIONS>` and `AGENT_ARGS`.
- **Environment**: `AGENT_RESULT_DIR` (the attempt's result directory),
  `VLLM_BASE_URL`, `VLLM_MODEL`, and `OPENAI_MODEL`; for PPA also
  `PPA_SANDBOX_ROOT` (the sandbox), `TOOL_TIMEOUT`, `OPENROAD_FLOW_DIR`, and
  `OR_IMAGE`.
- **Scored output**: DRC scores the final `./report.lyrdb`, which the
  `query_drc_report` tool writes, and keeps `./modified.gds`. PPA scores
  `./reports/6_finish.rpt` and `./logs/6_report.json` from the last OpenROAD
  run and checks `./src` against the original RTL. A DRC agent never sees
  `info.json`; a PPA agent may read it, and the harness restores it before
  scoring.
- **Statistics**: a last stdout line
  `AGENT RUN COMPLETE. TOTAL TOOL CALLS: <n>` feeds the tool-call statistics,
  and `cost.json` in `AGENT_RESULT_DIR` the token and cost statistics.

## 5. LLM backends

This repository does not provide models or API access. You need to set up
your own models before running any agent:

- **OpenAI-based agents** (`gpt_*.py`) call the OpenAI API with the model in
  `OPENAI_MODEL` and read `OPENAI_API_KEY`.
- **All other agents** call an OpenAI-compatible server that you run
  yourself. You can serve any local model with [vLLM](https://docs.vllm.ai)
  (see below); the agents call `VLLM_BASE_URL` and request `VLLM_MODEL`.
- **`orfs_agent.py`** (the default PPA agent) uses the vLLM settings; with
  `ORFS_LLM_BACKEND=openai` in `eval/eval_ppa_config.sh` it calls the OpenAI
  API with `OPENAI_MODEL` instead.

The eval config files (section 4) set `VLLM_BASE_URL`, `VLLM_MODEL`, and
`OPENAI_MODEL` for whichever agent they run.

### Serving a local model with vLLM

Install vLLM in its own environment (it pins its own PyTorch/CUDA stack).
Then edit the settings at the top of `scripts/serve_vllm.sh` and start it:

```bash
scripts/serve_vllm.sh
```

| Setting | Default | Meaning |
|---|---|---|
| `MODEL` | `google/gemma-4-31B-it` | A Hugging Face model name, or the path of a local checkpoint directory (the folder holding `config.json` and the weights) |
| `SERVED_MODEL_NAME` | empty: `MODEL` | The name the agents request; worth setting for a local path |
| `HF_HUB_CACHE` | empty: Hugging Face's default | Where Hugging Face models are downloaded and looked up, e.g. a shared model directory |
| `GPUS` | `0` | GPU ids, comma-separated; the model is split across all of them |
| `PORT`, `HOST` | `9888`, `127.0.0.1` | Where the server listens; `HOST=0.0.0.0` lets agents on other machines connect |
| `MAX_MODEL_LEN` | `65536` | Context window (see below) |
| `GPU_MEMORY_UTILIZATION` | `0.90` | Share of each GPU's memory vLLM may use. With less, vLLM may ask for fewer concurrent sequences, e.g. `EXTRA_ARGS=(--max-num-seqs 256)` |
| `TOOL_CALL_PARSER`, `REASONING_PARSER` | empty: chosen from the model | vLLM's parsers; set them for models other than Gemma 4 and Qwen3.5 |
| `VLLM` | `vllm` | The vllm command, e.g. its full path inside your vLLM environment |
| `EXTRA_ARGS` | none | Any other `vllm serve` options |

When it starts, the script prints the `VLLM_BASE_URL` and `VLLM_MODEL` lines
to copy into the eval config files.

The server needs three settings, which the script passes:

- **Tool calling**: `--enable-auto-tool-choice --tool-call-parser <parser>`
  (`gemma4` for Gemma 4, `qwen3_coder` for Qwen3.5; for a local checkpoint
  the script reads the family from `model_type` in its `config.json`). Without
  it, every agent request fails with HTTP 400 (`"auto" tool choice requires
  --enable-auto-tool-choice and --tool-call-parser to be set`).
- **A reasoning parser**: `--reasoning-parser <parser>` (`gemma4` or `qwen3`).
  The agents request thinking (`enable_thinking`), and the parser returns it
  separately from the answer and the tool calls.
- **Enough context**: `MAX_MODEL_LEN=65536` or more. `orfs_agent`'s setup
  prompt alone is about 18k tokens. With a 32k window a thinking model can run
  out of context before it answers, and the agent then logs
  `ERROR: empty search space; aborting.`

### How each agent finds its model

Every agent reads its model from the environment, which the eval configs set.
Run on its own (e.g. the smoke test in section 7), an agent falls back to the
defaults below when a variable is unset. `VLLM_BASE_URL` defaults to
`http://localhost:9888/v1`, the script's endpoint.

| Agent | Model setting | Default when unset |
|---|---|---|
| `drc/react/vllm_react.py` (default DRC agent), `drc/reflexion/vllm_reflexion.py`, `drc/tot/vllm_tot.py` | `VLLM_MODEL` | `google/gemma-4-31B-it` |
| `drc/react/vision_vllm_react.py` (needs a vision model) | `VLLM_MODEL` | `qwen3.5-122b` |
| `drc/proposer_critic/vllm_proposer_critic.py`, `ppa/proposer_critic/vllm_proposer_critic.py` | `VLLM_MODEL` | `qwen3.5-9b` |
| `ppa/orfs_agent/orfs_agent.py` (default PPA agent) | `VLLM_MODEL`, or `OPENAI_MODEL` with `ORFS_LLM_BACKEND=openai` | `qwen3.5-9b` / `gpt-5` |
| `ppa/react/vllm_react.py`, `ppa/reflexion/vllm_reflexion.py`, `ppa/tot/vllm_tot.py` | `VLLM_MODEL` | `Qwen/Qwen3.5-27B` |
| `drc/reflexion/gpt_reflexion.py`, `drc/tot/gpt_tot.py` | `OPENAI_MODEL` | `gpt-5-mini` |
| `ppa/react/gpt_react.py`, `ppa/reflexion/gpt_reflexion.py`, `ppa/tot/gpt_tot.py` | `OPENAI_MODEL` | `gpt-5` |

Confirm your endpoint works before starting an evaluation, e.g.
`curl -s http://localhost:9888/v1/models`. If the server is unreachable or
times out, some agents (including the default DRC agent `vllm_react.py`) keep
retrying instead of exiting. `vllm_react.py` stops with
`AGENT RUN FAILED DUE TO ERROR` when the server rejects its request, e.g. for
an unknown model name or with tool calling disabled.

## 6. Outputs and metrics

Each run of `eval_drc_sr.sh` / `eval_ppa_sr.sh` writes into
`RESULT_DIR/run<N>/<benchmark_path>/q<i>/` (`RESULT_DIR` from the config file):
- `agent_run.log` — full agent stdout/stderr (tool calls, LLM turns, errors)
- `success.txt`, `err.txt` (DRC) / `vrr.txt` (PPA) — per-attempt scoring
- `cost.json` — input/output/reasoning tokens, USD cost
- `attempt_metrics.txt` — extracted reasoning-process metrics

Aggregated, multi-run statistics (mean ± std for SR / ERR / VRR / tokens /
cost / failure-mode breakdown) land in
`RESULT_DIR/final_summary_report.log`.

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
`vllm_react.py`, so first start your model server (section 5). Without the
harness, the commands set the repository path and model settings themselves;
run them from the root of your clone:
```bash
conda activate posteda
REPO_ROOT=$PWD
export VLLM_BASE_URL=http://localhost:9888/v1 VLLM_MODEL=google/gemma-4-31B-it
cd /tmp && rm -rf sb && mkdir sb && cd sb
cp -L $REPO_ROOT/benchmark/drc_bench/drc_essential/L1/q1/* .
rm info.json    # scoring ground truth; the agent never sees it
export PYTHONPATH=$REPO_ROOT
export AGENT_RESULT_DIR=$(pwd)
python $REPO_ROOT/agents/drc/react/vllm_react.py \
    --drc_rpt_path ./prompt.txt --temperature 0 --iterations 8
```
You should see `AGENT RUN COMPLETE. TOTAL TOOL CALLS: <n>` followed by a
modified `report.lyrdb` and `modified.gds` in the working directory.

### PPA reference check (no model needed)

Re-run the OpenROAD flow on the unmodified `ppa_multi/L1/q1` design with the
agents' own flow tool, then score it with the harness's metric script. The
flow tool reads the ORFS settings from section 1 from the environment, so
export them; run the commands from the root of your clone. It takes 1–3
minutes:
```bash
conda activate posteda
REPO_ROOT=$PWD
export OPENROAD_FLOW_DIR=/abs/path/to/OpenROAD-flow-scripts/flow
export OR_IMAGE=openroad/orfs:v3.0-4639-ga2bb042b6
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

- The eval scripts find the repository from their own location (or from
  `REPO_ROOT` in their config file), so no paths inside the repo need editing.
- The benchmark snapshots under `benchmark/ppa_bench/*/{logs,reports}`
  contain reference OpenROAD outputs; absolute filesystem paths inside those
  files have been replaced with `$ORFS_HOME` / `$REPO_ROOT` placeholders so
  they no longer leak the original author's environment.
- API keys are read from the environment only; nothing is hardcoded in the
  source.
