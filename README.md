<p align="center">
  <img src="posteda.png" alt="PostEDA-Bench — a bridge from AI reasoning to chip-design closure" width="900">
</p>

<h1 align="center">The chip-design gauntlet for AI agents.</h1>

<p align="center">
  <strong>145 challenges. Two unforgiving arenas. One mission: close the design.</strong>
</p>

<p align="center">
  <a href="benchmark"><img src="https://img.shields.io/badge/Tasks-145-ff922b?style=for-the-badge" alt="145 benchmark tasks"></a>
  <a href="benchmark/drc_bench"><img src="https://img.shields.io/badge/DRC-70_Tasks-4dabf7?style=for-the-badge" alt="70 DRC tasks"></a>
  <a href="benchmark/ppa_bench"><img src="https://img.shields.io/badge/PPA-75_Tasks-94d82d?style=for-the-badge" alt="75 PPA tasks"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-CC_BY_4.0-ffd43b?style=for-the-badge" alt="License: CC BY 4.0"></a>
</p>

<p align="center">
  <a href="#the-challenge"><strong>The Challenge</strong></a> ·
  <a href="benchmark"><strong>Explore the Benchmark</strong></a> ·
  <a href="agents"><strong>Meet the Agents</strong></a> ·
  <a href="DEPLOYMENT.md"><strong>Run It Yourself</strong></a>
</p>

---

**The tools have run. The reports are in. The design still needs a hero.**

A layout violates manufacturing rules. A timing target refuses to budge. A power constraint leaves almost no room to maneuver. Welcome to **post-EDA design closure**, where an agent must inspect the evidence, choose an intervention, change the design, and face the tools again.

**PostEDA-Bench puts that engineering pressure at the center of agent evaluation.** It brings together real GDS layouts, RTL, tool reports, and executable evaluation flows to test whether LLM agents can repair design-rule violations and optimize power, performance, and area.

This repository accompanies **“PostEDA-Bench: A Benchmark for LLM Agents on Post-EDA Design Closure Tasks.”**

## The challenge

**Two arenas. Plenty of ways to get humbled.**

| Arena | The mission | The scale |
| --- | --- | --- |
| 🛠️ **DRC-Bench** | Inspect and repair design-rule violations in GDS layouts with KLayout and the ASAP7 rule deck. | **70 tasks**: 40 essential + 30 reasoning; L1–L3. |
| ⚡ **PPA-Bench** | Tune flow configurations, timing constraints, or RTL, then run OpenROAD to chase demanding optimization targets. | **75 tasks**: 35 single-objective + 40 multi-objective. |

DRC moves from essential rule-violation patterns to composite errors that demand multiple reasoning steps. PPA turns up the pressure across area, power, and performance, then combines period and power constraints in the multi-objective track.

**A taste of the pressure:** [one PPA task](benchmark/ppa_bench/ppa_multi/L1/q1/prompt.txt) asks an agent to cut effective period from **319.69 ps to at most 241 ps** while keeping power at or below **0.002 W**. That is roughly a **25% period reduction**, with a power ceiling to defend. This is the task target; the agent still has to earn the result.

## Why this benchmark hits hard

- **The artifacts are the arena.** Agents work with layouts, source code, configurations, and tool reports. Every intervention has consequences in the design flow.
- **The loop is the challenge.** Inspect, reason, edit, run, and reassess. Each new report can force a new plan.
- **The tools deliver the verdict.** Evaluation measures success rate, DRC error reduction, and PPA violation reduction, with logs and token-cost records to inspect what happened.
- **Correctness has teeth.** For designs with supported testbenches, the PPA harness includes an RTL functional-equivalence sanity check; detected functional divergence is marked `FAIL_FUNCTIONAL`.
- **The baselines are ready to battle.** Compare ReAct, Reflexion, Tree-of-Thoughts, proposer–critic, and an ORFS agent that pairs LLM-driven search-space discovery with Gaussian-process optimization.

## Bring your strongest agent

Explore the [DRC baselines](agents/drc), [PPA baselines](agents/ppa), and the [ORFS agent guide](agents/ppa/orfs_agent/README.md). Study how different reasoning strategies handle tool use, iteration budgets, and the moment a promising edit meets an unforgiving report.

The repository includes benchmark inputs, reference artifacts, agent implementations, evaluation harnesses, and pinned Python dependencies. The full path from environment setup to evaluation lives in the **[Deployment & Reproduction Guide](DEPLOYMENT.md)**.

| Start here | What you will find |
| --- | --- |
| [Deployment & reproduction](DEPLOYMENT.md) | Toolchain setup, environment variables, evaluation commands, outputs, and a smoke test. |
| [DRC benchmark](benchmark/drc_bench) | Layout-repair tasks across essential and reasoning splits. |
| [PPA benchmark](benchmark/ppa_bench) | Single-objective and multi-objective optimization tasks. |
| [Agent suite](agents) | Baseline reasoning strategies and EDA tool interfaces. |
| [Evaluation harnesses](eval) | DRC/PPA scoring and the RTL sanity-check machinery. |
| [Dataset metadata](croissant.json) | Machine-readable dataset description in Croissant format. |

---

<p align="center">
  <strong>Build the agent. Take the challenge. Show what it can close.</strong><br>
  Star the repository, explore the tasks, and bring your next chip-design idea to PostEDA-Bench.
</p>

<p align="center">
  Released under <a href="LICENSE">CC BY 4.0</a>.
</p>
