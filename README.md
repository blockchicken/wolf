# wolf — Pokemon Champions VGC Team Optimizer

A genetic algorithm that evolves Pokemon teams for the Champions VGC format, using a PPO-trained neural network as the battle agent. Teams compete in headless pokemon-showdown battles; the fittest survive, mutate, and breed across generations.

## How it works

1. **Metagame pool** — species, movesets, items, and spreads are sampled from Pikalytics usage statistics for the Champions Regulation MA format.
2. **Genetic search** — a population of teams plays best-of-3 series against fresh random-meta opponents each generation. Team size and round count anneal over generations (large population → fast culling early; small population → thorough evaluation late).
3. **Battle agent** — the neural network (`policy_1500_ppo_v13.pt`) acts as the decision-maker for both sides during evaluation. It was trained via PPO on Champions format self-play.
4. **Output** — the top surviving teams are written to a JSON results file with their win/loss records and packed team strings you can paste directly into Pokemon Showdown.

## Prerequisites

**Node.js + pokemon-showdown**

The battle engine runs as a subprocess. Clone and install the [pokemon-showdown](https://github.com/smogon/pokemon-showdown) server:

```bash
git clone https://github.com/smogon/pokemon-showdown.git
cd pokemon-showdown
npm install
```

**Python environment**

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e . --no-build-isolation
```

Dependencies: `torch`, `numpy` (see `pyproject.toml`).

## Running the genetic search

Edit the `SHOWDOWN_PATH` constant at the top of `genetic_search.py` to point to your pokemon-showdown checkout, then:

```bash
# Default run: 20 generations, annealing from 16 teams/5 rounds to 8 teams/16 rounds
python genetic_search.py

# Custom annealing schedule
python genetic_search.py --pop 48 --pop-end 16 --rounds 5 --rounds-end 16 --gens 20

# With a Gen-0 viability cull (runs N teams, keeps only those that sweep all BO3s)
python genetic_search.py --gen0-teams 1000 --pop-end 16 --rounds 5 --rounds-end 16

# Use random decisions instead of the neural network (faster, less accurate)
python genetic_search.py --handler random

# Save results and set a seed for reproducibility
python genetic_search.py --seed 42 --out results/run1.json
```

### Key flags

| Flag | Default | Description |
|---|---|---|
| `--pop` | 16 | Starting population size |
| `--pop-end` | 8 | Population size in the final generation |
| `--rounds` | 5 | BO3 series per team in generation 1 |
| `--rounds-end` | 16 | BO3 series per team in the final generation |
| `--gens` | 20 | Number of generations |
| `--gen0-teams` | — | If set, runs a Gen-0 cull with this many random teams |
| `--gen0-rounds` | 3 | BO3 series each Gen-0 team must sweep to survive |
| `--handler` | `ai` | Decision handler: `ai` (neural network) or `random` |
| `--seed` | — | Random seed for reproducibility |
| `--out` | — | Path to write results JSON (default: prints to stdout) |

Results at the end show each team's win/loss record, fitness score, and a packed team string ready to import into Showdown.

## Analyzing results

```bash
# Summarize a results file
python analyze_heuristics.py
```

## Training your own model

If you want to train from scratch on your own battle logs:

```bash
# Logs should be in downloaded_logs/<format>/<bucket>/
python train.py --logs downloaded_logs/gen9championsvgc2026regma --epochs 30
```

This performs behaviour cloning weighted by battle outcome. Checkpoints are saved to `checkpoints/`.

## Project structure

```
showdown_ai/
  battle_runner.py   — headless subprocess wrapper around pokemon-showdown
  agents.py          — DecisionHandler interface and RandomDecisionHandler
  model.py           — BattlePolicy transformer architecture
  model_handler.py   — ModelDecisionHandler (wraps a trained checkpoint)
  pikalytics.py      — metagame data loading and team generation
  features.py        — state → tensor encoding
  state.py           — per-turn perspective state tracker
  engine.py          — lower-level battle engine
  logs.py            — Showdown log parser
  training_data.py   — log → training example extraction
  vocab.py           — species/move vocabulary

genetic_search.py    — main GA loop
train.py             — imitation learning training script
analyze_heuristics.py — empirical analysis of battle heuristics

data/pikalytics/     — metagame usage stats (Champions Reg. MA)
checkpoints/
  policy_1500_ppo_v13.pt     — trained model weights
  vocab_1500_ppo_v13/        — species and move vocabularies
scripts/
  showdown_worker.js — Node.js worker used by battle_runner
```

## Format

This project targets **Pokemon Champions VGC — Regulation MA** (doubles, Gen 9). The metagame data and model were trained on that format. To adapt to a different format you would need new Pikalytics data and a retrained model.
