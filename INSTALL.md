# Installing PostEDA-Bench

[← Back to the deployment guide](DEPLOYMENT.md)

The full setup reference. The deployment prompt in [DEPLOYMENT.md](DEPLOYMENT.md)
has a coding agent work through this file for you; you can also follow it by
hand.

## What you need

| Component | Needed for | Version |
|---|---|---|
| Linux on x86_64, `bash` 5.1 or newer | everything | tested on Ubuntu 22.04 and 24.04 |
| `git`, `wget`, `jq`, `bc`, `xauth` | everything | any |
| conda environment `posteda` | everything | Python 3.11.11 with `requirements.txt` |
| KLayout | DRC | 0.30.3 |
| Docker Engine, usable without `sudo` | PPA | any recent |
| OpenROAD-flow-scripts (ORFS) and its Docker image | PPA | commit `a2bb042b6`, image `openroad/orfs:v3.0-4639-ga2bb042b6` |
| Icarus Verilog (`iverilog`, `vvp`) | PPA | 11 or 12 |
| vLLM in its own environment, NVIDIA GPU | local models | one that supports your model |
| OpenAI API key | `gpt_*` agents | – |

An evaluation needs one of the two model backends; set up the one you will use.

## 1. System packages

```bash
sudo apt-get update
sudo apt-get install -y git wget jq bc xauth
```

Stock Ubuntu lacks `jq` (and `bc` on Ubuntu Server), and ORFS's
`util/docker_shell` calls `xauth` even without a display. `tput`, `realpath`,
and `basename` are preinstalled.

## 2. Docker Engine (PPA)

Install Docker Engine with the
[official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/),
then start it and let your account use it:

```bash
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"    # membership of the docker group is root-equivalent
```

Log out and back in, then check that Docker works without `sudo`:
`docker run --rm hello-world`. A native OpenROAD installation does not replace
Docker: the agents run ORFS through `util/docker_shell`.

## 3. KLayout 0.30.3 (DRC)

Ubuntu's own `klayout` package is too old (0.26.2 on 22.04, 0.28.x on 24.04).
Install the official package for your release from klayout.org:

```bash
UBUNTU_MAJOR=$(. /etc/os-release && echo "${VERSION_ID%%.*}")
wget "https://www.klayout.org/downloads/Ubuntu-${UBUNTU_MAJOR}/klayout_0.30.3-1_amd64.deb"
sudo apt-get install -y ./klayout_0.30.3-1_amd64.deb
klayout -v                                   # KLayout 0.30.3
```

The DRC tools hand their scripts to the `klayout` command, which runs them
under KLayout's bundled Python, so only `klayout` itself must be on `PATH`.

## 4. OpenROAD-flow-scripts (PPA)

Use the ORFS commit and Docker image that produced the reference results:

```bash
git clone https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts
cd OpenROAD-flow-scripts
git checkout a2bb042b6
docker pull openroad/orfs:v3.0-4639-ga2bb042b6
```

No `--recursive` clone or build is needed: `util/docker_shell` runs OpenROAD
and Yosys from the image. Do not use a newer ORFS: from `26Q2` on,
`util/docker_shell` runs `make` inside the image's own flow directory, so every
flow the agents launch fails. Other versions or images also give PPA numbers
that differ from the tasks' reference values.

## 5. Icarus Verilog (PPA)

The functional checks after each PPA attempt run on the host:

```bash
sudo apt-get install -y iverilog
iverilog -V && vvp -V
```

Without them, designs with testbenches end as `FAIL_INFRA`. Ubuntu's 11.0
(22.04) and 12.0 (24.04) both work.

## 6. The repository and its Python environment

Clone the repository outside the ORFS checkout and create the `posteda`
environment:

```bash
git clone https://github.com/pengjas/posteda-bench.git
cd posteda-bench
conda create -n posteda python=3.11.11
conda activate posteda
pip install -r requirements.txt
```

Python 3.10 does not work: the pinned NumPy 2.4.1 needs 3.11. Three of the
pinned packages are optional: `pillow` (vision agents), `langchain-google-genai`
(Gemini backend), and `scikit-learn` (`orfs_agent`'s GP surrogate; without it,
`orfs_agent` falls back to Latin-hypercube sampling).

## 7. Point the configs at your toolchain

In `eval/eval_ppa_config.sh`, set `OPENROAD_FLOW_DIR` to the `flow` directory
of your ORFS checkout (default `$HOME/OpenROAD-flow-scripts/flow`), and keep
`OR_IMAGE=openroad/orfs:v3.0-4639-ga2bb042b6`.

## 8. A model backend

- **Local models:** install vLLM in its own environment, since it pins its own
  PyTorch and CUDA (e.g. `conda create -n vllm python=3.12`, then
  `pip install vllm` inside it). At the top of `scripts/serve_vllm.sh`, set
  `MODEL` (a Hugging Face name or a local checkpoint path), `GPUS`, and `VLLM`
  (the path of that environment's `vllm` command).
- **OpenAI API:** `export OPENAI_API_KEY=sk-...` in your shell; keep the key out
  of files.

## Check the installation

Run these from the root of your clone, with `posteda` active. All but the last
need no model.

| Check | Command | Expected |
|---|---|---|
| KLayout | `klayout -v` | `KLayout 0.30.3` |
| Docker without `sudo` | `docker run --rm hello-world` | `Hello from Docker!` |
| ORFS through Docker | `(cd <ORFS>/flow && export OR_IMAGE=openroad/orfs:v3.0-4639-ga2bb042b6 && util/docker_shell openroad -version && util/docker_shell yosys -V)` | both versions |
| Icarus Verilog | `iverilog -V && vvp -V` | both versions |
| Python packages | `python -c "import langchain_openai, langgraph, numpy"` | no error |

**DRC tools.** Re-run DRC on an unmodified layout, which has one error:
```bash
REPO_ROOT=$PWD
cd "$(mktemp -d)"
cp -L $REPO_ROOT/benchmark/drc_bench/drc_essential/L1/q1/* .
export PYTHONPATH=$REPO_ROOT AGENT_RESULT_DIR=$PWD
python -c "from agents.drc.tools.query_drc_report import query_drc_report as q; q.invoke({'gds_file_path': './top_cell.gds', 'rerun_drc_check': True})" > /dev/null
cp $REPO_ROOT/eval/drc_error_collection_eval.py . && klayout -b -r drc_error_collection_eval.py > /dev/null
jq -c '{init_errors_num, final_errors_num}' info.json    # {"init_errors_num":1,"final_errors_num":1}
```

**PPA flow.** Re-run the OpenROAD flow on an unmodified design (1–3 minutes):
```bash
REPO_ROOT=$PWD
T=$REPO_ROOT/benchmark/ppa_bench/ppa_multi/L1/q1
export OPENROAD_FLOW_DIR=/abs/path/to/OpenROAD-flow-scripts/flow
export OR_IMAGE=openroad/orfs:v3.0-4639-ga2bb042b6
cd "$(mktemp -d)"
cp -rL $T/config $T/src $T/info.json $REPO_ROOT/eval/ppa_metric_collection.py .
export PPA_SANDBOX_ROOT=$PWD PYTHONPATH=$REPO_ROOT
python -c "from agents.ppa.tools.run_openroad import run_openroad_flow as t; print(t.invoke({'target': 'finish'}))"
python ppa_metric_collection.py > /dev/null
jq -c '{final_effective_period, final_power, final_area}' info.json
```
The flow reports `SUCCESS: Flow completed for gcd`, and the metrics match the
reference: effective period 319.69 ps, power 0.00161 W, area 293.163 µm².

**Model.** With the server from `scripts/serve_vllm.sh` running, use the two
values it printed and run one DRC task end to end:
```bash
REPO_ROOT=$PWD
export VLLM_BASE_URL=http://localhost:9888/v1 VLLM_MODEL=google/gemma-4-31B-it
curl -s $VLLM_BASE_URL/models
cd "$(mktemp -d)"
cp -L $REPO_ROOT/benchmark/drc_bench/drc_essential/L1/q1/* . && rm info.json
export PYTHONPATH=$REPO_ROOT AGENT_RESULT_DIR=$PWD
python $REPO_ROOT/agents/drc/react/vllm_react.py --drc_rpt_path ./prompt.txt --temperature 0 --iterations 8
```
It ends with `AGENT RUN COMPLETE. TOTAL TOOL CALLS: <n>`, leaving
`report.lyrdb` and `modified.gds` behind. If the server is unreachable, the
agent keeps retrying; if it rejects the request (unknown model name, tool
calling disabled), the agent stops with `AGENT RUN FAILED DUE TO ERROR`. For
the OpenAI API, export `OPENAI_API_KEY` instead and run
`agents/drc/reflexion/gpt_reflexion.py` the same way.

## Special situations

| Situation | What to do |
|---|---|
| No `sudo` | An administrator has to install the system packages, Docker, KLayout, and Icarus Verilog, and Docker must already work for your account. Conda, the repository, ORFS, and vLLM install in your home directory. |
| A Linux distribution other than Ubuntu | Install the same versions with its package manager; klayout.org has KLayout packages for several distributions. Untested. |
| macOS, Windows, or ARM | Use an x86_64 Linux machine or virtual machine; the tested setup is x86_64 Ubuntu, and WSL2 is untested. |
| Another KLayout version is installed | Other projects may rely on it, so ask before replacing it. The DRC tools need 0.30.3 as the `klayout` on `PATH`. |
| An ORFS checkout exists at another commit | Leave it; clone a second copy at `a2bb042b6` and point `OPENROAD_FLOW_DIR` at that one. |
| A `posteda` conda environment already exists | Compare it with `requirements.txt`; rather than changing it, create an environment under another name. |
| No conda | Install Miniconda in your home directory from repo.anaconda.com. If `conda create` stops at Anaconda's Terms of Service, read and accept them with `conda tos accept`, or create the environment from conda-forge (`-c conda-forge --override-channels`). |
| Docker says `permission denied` | Your account is not in the `docker` group yet, or has not logged in again since; until then, `sg docker -c '<command>'` works. Rootless Docker and Podman are untested. |
| `docker pull` fails | Check the Docker daemon's proxy settings, or Docker Hub's rate limit (`docker login` raises it). |
| Little disk space | The ORFS image takes 6 GB, the ORFS checkout 2 GB, Miniconda with `posteda` 1.5 GB, a vLLM environment 16 GB, and a model checkpoint 19 GB (Qwen3.5-9B) to 59 GB (Gemma 4 31B). |
| No NVIDIA GPU | Use the OpenAI API, or serve the model on another machine (`HOST=0.0.0.0` in its `scripts/serve_vllm.sh`) and point `VLLM_BASE_URL` at it. |
| A shared GPU machine | Serve on a GPU that `nvidia-smi` shows idle, or lower `GPU_MEMORY_UTILIZATION` to share one (if vLLM then refuses to start, also set `EXTRA_ARGS=(--max-num-seqs 256)`). Keep `HOST=127.0.0.1` unless agents connect from elsewhere, and stop the server when done. |
