# PostEDA-Bench — Deploy and Evaluate

[← Back to the project overview](README.md)

**One prompt to deploy, one change to evaluate.**

## 1. Deploy with one prompt

Start Codex or Claude Code in the directory where you keep your projects (or in
your clone of this repository) and give it the prompt below. It installs every
prerequisite from [INSTALL.md](INSTALL.md), asks you before anything that
needs `sudo` or could affect the rest of your system, and runs the checks. It
ends with either "All set", after which you can start evaluating, or the steps
that are still up to you. To install by hand, follow [INSTALL.md](INSTALL.md).

```text
Deploy PostEDA-Bench (https://github.com/pengjas/posteda-bench) on this machine: install every prerequisite in its INSTALL.md so that I can start an evaluation as soon as you finish. Work safely:

1. Inspect before changing anything: OS and CPU architecture, free disk space, whether I can use sudo, and which components from INSTALL.md are already installed, at which versions (git, jq, bc, xauth, conda, Docker and whether it works for me without sudo, KLayout, Icarus Verilog, an OpenROAD-flow-scripts checkout and its Docker image, NVIDIA GPUs, vLLM). If the repository is not in the current directory, ask me where to clone it. Then read INSTALL.md: it has the exact versions, commands, and checks, and what to do in unusual setups.
2. Show me what is already there and what you will install. Ask me, in one message, what only I can decide: how I will run models (vLLM on this machine, with which model and GPU, and/or the OpenAI API) and any install location you are unsure of.
3. Install what is missing as INSTALL.md describes. Before running anything with sudo, show me the commands and ask (one request may cover a group of related commands); if sudo needs a password you cannot enter, give me the commands to run and wait. Also ask before adding me to the docker group, before accepting any license or terms of service, before editing shell startup files, before any download over 1 GB, and before using a GPU. Never remove, downgrade, or overwrite existing software, conda environments, or checkouts: install side by side, and ask me when something conflicts. Download only from the sources INSTALL.md names, never pipe a downloaded script into a shell, and never write an API key into a file or print it.
4. Set OPENROAD_FLOW_DIR and OR_IMAGE in eval/eval_ppa_config.sh, and for vLLM also MODEL, GPUS, and VLLM in scripts/serve_vllm.sh.
5. Run every check in INSTALL.md, and stop any model server you start for them. If a check fails, fix the cause within these rules, or tell me why you cannot.
6. End with one of:
   - "All set", only if every check passed, followed by the commands for my first evaluation;
   - "Not done yet", followed by each step still left to me, with the reason: a command to run (for example a sudo command you were not allowed to run, or logging in again for the docker group) or something to provide (for example OPENAI_API_KEY).
   Then list what you installed where, and every file you changed.
```

## 2. Evaluate with one change

1. **Start a model.** For a local model, set `MODEL` and `GPUS` at the top of
   [`scripts/serve_vllm.sh`](scripts/serve_vllm.sh) and run it; it prints the
   `VLLM_BASE_URL` and `VLLM_MODEL` for step 2. For the OpenAI API, run
   `export OPENAI_API_KEY=sk-...` and pick a `gpt_*` agent in step 2.
2. **Change the config and run.** In
   [`eval/eval_drc_config.sh`](eval/eval_drc_config.sh) or
   [`eval/eval_ppa_config.sh`](eval/eval_ppa_config.sh), set the agent, the
   model, and what to run (runs, task groups, result directory); the file
   explains each setting. Then, from your clone:
   ```bash
   conda activate posteda
   cd eval
   ./eval_drc_sr.sh      # or ./eval_ppa_sr.sh
   ```

Attempts that already have results in `RESULT_DIR` are skipped, so a stopped
evaluation resumes where it left off; use a new `RESULT_DIR` for every new
agent or model. To evaluate your own agent, set `AGENT_SCRIPT` to it;
[agents/README.md](agents/README.md) lists what it receives and what it must
leave behind.

## 3. Benchmark layout

| Benchmark | Task groups for `TASK_GROUPS` (tasks per level) | Tasks |
|---|---|---|
| `benchmark/drc_bench` | `drc_essential` (L1–L3: 20, 10, 10), `drc_reasoning` (L1–L3: 13, 10, 7) | 70 |
| `benchmark/ppa_bench` | `ppa_mono/area` (L1–L2: 5, 5), `ppa_mono/power` (L1–L2: 5, 5), `ppa_mono/perf` (L1–L3: 5, 5, 5), `ppa_multi` (L1–L2: 20, 20) | 75 |

A task is a folder `<group>/L<level>/q<n>/`. The agent works from `prompt.txt`
plus, for DRC, the layout `top_cell.gds`, and for PPA, the ORFS `config/`, the
RTL in `src/`, and the reference run's `logs/` and `reports/`. `info.json`
holds what the task is scored against: the initial DRC errors, or the PPA
targets. DRC agents never see it; PPA agents may read their targets in it, and
the harness restores the file before scoring.

## 4. How to read the results

Everything lands in `RESULT_DIR`.

**`final_summary_report.log`** is the report. Its headline numbers are means ±
standard deviations over the `NUM_RUNS` runs:

- **SR**, the success rate: the share of attempts that cleared every DRC error,
  or met every PPA target without changing what the RTL computes.
- **ERR**, the DRC error reduction rate: `(initial − final errors) / initial
  errors` per task, averaged.
- **VRR**, the PPA violation reduction rate: the share of the gap between the
  initial metric and its target that an attempt closed, averaged.
  Single-objective scores are clipped to [0, 1]. Multi-objective (`ppa_multi`)
  scores have no lower bound: breaking a constraint that was already met scores
  −(final − target) / (target − initial), so one such attempt can dominate the
  average (see `eval/ppa_score.py`).

The report gives these per level ("Benchmark"), per task group ("Top-Level"),
for the three single-objective PPA groups combined, and overall, followed by
tool-call, token, and cost statistics, the failure modes, and one line per
attempt.

**`run<N>/<group>/L<level>/q<n>/`** holds one attempt. Start with `status.txt`
(the outcome) and `agent_run.log` (everything the agent printed: model turns,
tool calls, errors). Next to them are the scores (`success.txt`, plus `err.txt`
or `vrr.txt`), `cost.json`, the scored `info.json`, and the agent's final
files: `modified.gds` and `report.lyrdb` for DRC; `config/`, `reports/`,
`logs/`, and the functional check's `equiv.log` for PPA.

| `status.txt` | Meaning |
|---|---|
| `success` | Every DRC error cleared, or every PPA target met with the RTL still equivalent |
| `fail (errors remain: N)` | DRC: the agent's last DRC check still found N errors |
| `fail (no output)` | DRC: the agent never ran a DRC check, often because it ran out of iterations first (see the end of `agent_run.log`) |
| `fail (targets not met)` | PPA: the final flow missed a target; `info.json` has the final metrics next to the targets |
| `fail (no reports generated)` | PPA: the agent's last OpenROAD run did not finish, so there are no final metrics |
| `fail (functional equivalence)` | PPA: the agent's RTL edits changed what the design computes (`equiv.log`) |
