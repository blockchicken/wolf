#!/usr/bin/env python3
"""Process all downloaded battle logs and extract training dataset."""

import json
from pathlib import Path
from collections import defaultdict

from showdown_ai import load_showdown_log_json, TrainingDatasetBuilder, create_dataset_from_logs


def main():
    log_dir = Path("downloaded_logs/gen9championsvgc2026regma")
    
    if not log_dir.exists():
        print(f"No logs found at {log_dir}")
        return
    
    log_files = list(log_dir.glob("*.json"))
    print(f"Found {len(log_files)} log files")
    print(f"Processing {log_dir}...\n")
    
    # Extract all examples
    all_examples = create_dataset_from_logs(log_dir)
    
    print(f"\n{'='*60}")
    print(f"DATASET SUMMARY")
    print(f"{'='*60}")
    print(f"Total battles: {len(log_files)}")
    print(f"Total training examples: {len(all_examples)}")
    
    if all_examples:
        avg_examples_per_battle = len(all_examples) / len(log_files)
        print(f"Average examples per battle: {avg_examples_per_battle:.1f}")
        
        # Outcome distribution
        outcome_counts = defaultdict(int)
        outcome_weights = defaultdict(float)
        
        for ex in all_examples:
            outcome_counts[ex.outcome] += 1
            outcome_weights[ex.outcome] += ex.outcome_weight
        
        print(f"\nOutcome distribution:")
        for outcome in ["win", "loss", "tie"]:
            count = outcome_counts[outcome]
            weight = outcome_weights[outcome]
            pct = (count / len(all_examples) * 100) if all_examples else 0
            print(f"  {outcome}: {count} examples ({pct:.1f}%), total weight: {weight:.1f}")
        
        # Sample statistics
        print(f"\nAction availability:")
        avg_actions = sum(len(ex.available_actions) for ex in all_examples) / len(all_examples)
        print(f"  Average available actions per decision: {avg_actions:.1f}")
        
        avg_known_moves = sum(len(ex.my_known_moves) for ex in all_examples) / len(all_examples)
        print(f"  Average known moves per pokemon: {avg_known_moves:.2f}")
        
        print(f"\nExample details (first 3):")
        for i, ex in enumerate(all_examples[:3]):
            print(f"\n  Example {i+1}:")
            print(f"    Battle side: {ex.player_side}, Turn: {ex.turn}")
            print(f"    Active: {ex.my_active_pokemon} vs {ex.opponent_active_pokemon}")
            print(f"    Available: {len(ex.available_actions)} options")
            print(f"    Action taken: {ex.taken_action.action_type.value} {ex.taken_action.target}")
            print(f"    Outcome: {ex.outcome} (weight: {ex.outcome_weight})")
        
        # Save dataset
        output_file = Path("training_dataset.json")
        print(f"\n\nSaving dataset to {output_file}...")
        
        examples_dict = [ex.to_dict() for ex in all_examples]
        with open(output_file, 'w') as f:
            json.dump(examples_dict, f, indent=2)
        
        print(f"Saved {len(all_examples)} examples to {output_file}")


if __name__ == "__main__":
    main()
