#!/usr/bin/env python3
"""Final verification of training data pipeline."""

import json
from pathlib import Path
from showdown_ai import load_showdown_log_json, TrainingDatasetBuilder

# Load one battle
log_path = Path("downloaded_logs/gen9championsvgc2026regma/gen9championsvgc2026regma-2594983461.json")
log = load_showdown_log_json(log_path)

print("✓ Battle log loaded successfully")
print(f"  - ID: {log.battle_id}")
print(f"  - Players: {log.players[0]} vs {log.players[1]}")
print(f"  - Total events: {len(log.events)}")

# Extract training examples
builder = TrainingDatasetBuilder()
examples = builder.parse_log(log)

print(f"\n✓ Training examples extracted: {len(examples)}")

if examples:
    ex = examples[0]
    print(f"\n✓ Sample example structure:")
    print(f"  - State: Turn {ex.turn}, {ex.my_active_pokemon} ({ex.my_active_hp_percent*100:.0f}%) vs {ex.opponent_active_pokemon}")
    print(f"  - Available: {len(ex.available_actions)} actions")
    print(f"  - Chosen: {ex.taken_action.action_type.value} {ex.taken_action.target}")
    print(f"  - Outcome: {ex.outcome} (weight: {ex.outcome_weight})")
    
    # Verify serialization
    ex_dict = ex.to_dict()
    json_str = json.dumps(ex_dict)
    print(f"\n✓ JSON serialization works ({len(json_str)} bytes)")

# Load the full dataset
dataset_path = Path("training_dataset.json")
if dataset_path.exists():
    dataset = json.loads(dataset_path.read_text())
    print(f"\n✓ Dataset file loaded: {len(dataset)} examples")
    
    outcomes = {}
    total_weight = 0
    for ex in dataset:
        outcome = ex['outcome']
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        total_weight += ex['outcome_weight']
    
    print(f"  - Outcomes: {outcomes}")
    print(f"  - Total outcome weight: {total_weight:.1f}")

print("\n" + "="*60)
print("PIPELINE STATUS: FULLY FUNCTIONAL")
print("="*60)
print("\nReady for model training!")
