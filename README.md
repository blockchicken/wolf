# showdown-ai

A lightweight Python framework for:

1. Running **headless Pokemon Showdown doubles battles** as a black box (team1 + team2 -> winner).
2. Parsing uploaded Showdown logs into structured events.
3. Building two independent perspective datasets (one for each player) from a single omniscient battle log.
4. **Extracting training data** for imitation learning models (Tier 1: outcome-weighted learning).

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e . --no-build-isolation
pytest
```

## Extract Training Data for Model Training

```python
from pathlib import Path
from showdown_ai import create_dataset_from_logs

# Process all logs in a directory
log_dir = Path("downloaded_logs/gen9championsvgc2026regma")
examples = create_dataset_from_logs(log_dir)

# Each example has: state, available_actions, taken_action, outcome
# Examples are weighted: 1.0 (win), 0.5 (loss), 0.25 (tie)
print(f"Extracted {len(examples)} training examples")
```

See [TRAINING_DATA_PIPELINE.md](TRAINING_DATA_PIPELINE.md) for complete details on data extraction and model training.

## Parse a Showdown JSON log

```python
from showdown_ai import load_showdown_log_json, split_perspective_logs, StateTracker

battle = load_showdown_log_json("gen9vgc2024regg-2194787570.json")
views = split_perspective_logs(battle.events)

p1_tracker = StateTracker("p1")
p1_timeline = p1_tracker.consume_all(views["p1"])
print(p1_timeline[-1].winner)
```

## Run one headless battle

```python
from showdown_ai import ShowdownBattleRunner, RandomLegalAgent

runner = ShowdownBattleRunner(pokemon_showdown_repo="/path/to/pokemon-showdown")
result = runner.run_single_battle(
    team_p1="PACKED_TEAM_STRING_1",
    team_p2="PACKED_TEAM_STRING_2",
    agent_p1=RandomLegalAgent(),
    agent_p2=RandomLegalAgent(),
    seed=7,
    formatid="gen9vgc2024regg",
)
print(result.winner, result.turns)
```

### Notes

- The Node worker expects a built Showdown checkout (`dist/sim` present).
- Policies act on each side's private `|request|` stream, so decisions are made from imperfect information.
- Perspective splitting keeps public battle protocol events and drops spectator/chat noise.
