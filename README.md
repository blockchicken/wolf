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

## Run Headless AI Battles

```python
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler

# Initialize runner pointing to pokemon-showdown checkout
runner = BattleRunner(
    showdown_path="/path/to/pokemon-showdown",
    format_id="gen9vgc2024regg",
    seed=(1234, 5678, 9012, 3456),  # optional
)

# Run a battle with two AI players
result, _ = runner.run_battle(
    team_p1=packed_team_1,
    team_p2=packed_team_2,
    p1_handler=RandomDecisionHandler(),
    p2_handler=RandomDecisionHandler(),
    p1_name="Alice",
    p2_name="Bob",
)

print(f"Winner: {result.winner}")  # "p1", "p2", or None for tie
print(f"Turns: {result.turns}")
```

**Key features:**
- AI decisions made from imperfect information (own team fully known, opponent team partially)
- Battle runs headlessly in subprocess—no GUI needed
- Easy to integrate trained models (implement `DecisionHandler` interface)
- Optional training data collection (request/action pairs per turn)

See [HEADLESS_BATTLE_RUNNER.md](HEADLESS_BATTLE_RUNNER.md) for complete integration guide.
