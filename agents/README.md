# Agents

The baseline agents. Choose one with `AGENT_SCRIPT` in `eval/eval_drc_config.sh`
or `eval/eval_ppa_config.sh`.

| Framework | DRC (`agents/drc/`) | PPA (`agents/ppa/`) |
|---|---|---|
| ReAct | `react/vllm_react.py`; `react/vision_vllm_react.py` also sees layout images (needs a vision model) | `react/vllm_react.py`, `react/gpt_react.py` |
| Reflexion | `reflexion/vllm_reflexion.py`, `reflexion/gpt_reflexion.py` | `reflexion/vllm_reflexion.py`, `reflexion/gpt_reflexion.py` |
| Tree-of-Thoughts | `tot/vllm_tot.py`, `tot/gpt_tot.py` | `tot/vllm_tot.py`, `tot/gpt_tot.py` |
| Proposer-critic | `proposer_critic/vllm_proposer_critic.py` | `proposer_critic/vllm_proposer_critic.py` |
| LLM setup + GP search | – | `orfs_agent/orfs_agent.py` ([guide](ppa/orfs_agent/README.md)) |

The tools they call are in `drc/tools/` (query the layout, edit shapes and
cells, run DRC with KLayout) and `ppa/tools/` (read and edit files, run the
OpenROAD flow, report PPA).

## Models

`vllm_*` agents and `orfs_agent.py` call an OpenAI-compatible server at
`VLLM_BASE_URL` and request `VLLM_MODEL`. `gpt_*` agents call the OpenAI API
with `OPENAI_MODEL` and `OPENAI_API_KEY`, as does `orfs_agent.py` with
`ORFS_LLM_BACKEND=openai`. The eval configs set these variables; an agent run
by hand falls back to these models:

| Agent | Default model |
|---|---|
| `drc/react/vllm_react.py`, `drc/reflexion/vllm_reflexion.py`, `drc/tot/vllm_tot.py` | `google/gemma-4-31B-it` |
| `drc/react/vision_vllm_react.py` | `qwen3.5-122b` |
| `drc/proposer_critic/vllm_proposer_critic.py`, `ppa/proposer_critic/vllm_proposer_critic.py`, `ppa/orfs_agent/orfs_agent.py` | `qwen3.5-9b` |
| `ppa/react/vllm_react.py`, `ppa/reflexion/vllm_reflexion.py`, `ppa/tot/vllm_tot.py` | `Qwen/Qwen3.5-27B` |
| `drc/reflexion/gpt_reflexion.py`, `drc/tot/gpt_tot.py` | `gpt-5-mini` |
| `ppa/react/gpt_react.py`, `ppa/reflexion/gpt_reflexion.py`, `ppa/tot/gpt_tot.py`, and `orfs_agent.py` on the OpenAI backend | `gpt-5` |

`VLLM_BASE_URL` defaults to `http://localhost:9888/v1`, where
`scripts/serve_vllm.sh` serves.

## Writing your own agent

Set `AGENT_SCRIPT` to your script. For every attempt, the harness copies the
task into a fresh sandbox, runs the agent there, and scores what it leaves
behind:

- **Command line**: `--drc_rpt_path ./prompt.txt` (DRC) or
  `--prompt_path ./prompt.txt` (PPA), then
  `--temperature <TEMPERATURE> --iterations <ITERATIONS>` and the config's
  `AGENT_ARGS`.
- **Environment**: `AGENT_RESULT_DIR` (the attempt's result directory),
  `VLLM_BASE_URL`, `VLLM_MODEL`, and `OPENAI_MODEL`; for PPA also
  `PPA_SANDBOX_ROOT` (the sandbox), `TOOL_TIMEOUT`, `OPENROAD_FLOW_DIR`, and
  `OR_IMAGE`. `PYTHONPATH` includes the repository, so `agents.*` imports
  work.
- **Scored output**: DRC scores the final `./report.lyrdb`, which the
  `query_drc_report` tool writes, and keeps `./modified.gds`. PPA scores
  `./reports/6_finish.rpt` and `./logs/6_report.json` from the last OpenROAD
  run and checks `./src` against the original RTL. A DRC agent never sees
  `info.json`; a PPA agent may read its targets there, and the harness
  restores the file before scoring.
- **Statistics** (optional): a last stdout line
  `AGENT RUN COMPLETE. TOTAL TOOL CALLS: <n>` feeds the tool-call statistics,
  and `cost.json` in `AGENT_RESULT_DIR` the token and cost statistics.
