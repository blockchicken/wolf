#!/usr/bin/env python3
"""Compare extracted training examples with raw battle log events.

This tool lets you drill into specific turns and verify what was parsed.
"""

import json
from pathlib import Path
from collections import defaultdict

from showdown_ai import load_showdown_log_json, TrainingDatasetBuilder


def compare_turn(battle_id: str, example_index: int = 0):
    """Show detailed comparison of a training example vs the actual log events."""
    
    log_path = Path(f"downloaded_logs/gen9championsvgc2026regma/{battle_id}.json")
    if not log_path.exists():
        print(f"Battle not found: {log_path}")
        return
    
    # Load and extract examples
    log = load_showdown_log_json(log_path)
    builder = TrainingDatasetBuilder()
    examples = builder.parse_log(log)
    
    if example_index >= len(examples):
        print(f"Example {example_index} not found (only {len(examples)} examples)")
        return
    
    example = examples[example_index]
    
    print("=" * 70)
    print(f"TURN COMPARISON: Example #{example_index + 1}")
    print("=" * 70)
    
    print(f"\nEXTRACTED TRAINING EXAMPLE:")
    print(f"  Turn: {example.turn}")
    print(f"  Side: {example.player_side}")
    print(f"  My active: {example.my_active_pokemon} ({example.my_active_hp_percent*100:.1f}%)")
    print(f"  Opponent: {example.opponent_active_pokemon} ({example.opponent_active_hp_percent*100:.1f}%)")
    print(f"  Known my moves: {example.my_known_moves}")
    print(f"  Available actions: {len(example.available_actions)}")
    for i, action in enumerate(example.available_actions, 1):
        print(f"    {i}. {action.action_type.value}: {action.target}")
    print(f"  ✓ CHOSEN: {example.taken_action.action_type.value} {example.taken_action.target}")
    print(f"  Battle outcome: {example.outcome} (weight: {example.outcome_weight})")
    
    # Now show what was in the raw log around this turn
    print(f"\nRAW LOG EVENTS AROUND THIS DECISION:")
    
    with open(log_path) as f:
        raw_data = json.load(f)
    
    log_lines = raw_data['log'].splitlines()
    
    # Find turn marker and show nearby events
    turn_marker = f"|turn|{example.turn}"
    turn_line_idx = None
    
    for i, line in enumerate(log_lines):
        if line == turn_marker:
            turn_line_idx = i
            break
    
    if turn_line_idx is None:
        print(f"  (Could not find turn marker in log)")
        return
    
    print(f"\n  Events from {max(0, turn_line_idx-2)} to {min(len(log_lines), turn_line_idx+15)}:")
    
    for i in range(max(0, turn_line_idx-2), min(len(log_lines), turn_line_idx+15)):
        line = log_lines[i]
        marker = ">>>" if i == turn_line_idx else "   "
        
        # Highlight important lines
        if "|move|" in line:
            marker = "MOV"
        elif "|switch|" in line:
            marker = "SWI"
        elif "|-damage|" in line:
            marker = "DMG"
        elif "|turn|" in line:
            marker = "TRN"
        
        print(f"  {marker} [{i:3d}] {line[:80]}")
    
    # Find and highlight the actual action taken
    print(f"\n  LOOKING FOR ACTION TAKEN IN LOG...")
    
    if example.taken_action.action_type.value == "move":
        action_pattern = f"|move|{example.player_side}"
        search_text = f"{example.taken_action.target}"
    else:
        action_pattern = f"|switch|{example.player_side}"
        search_text = f"{example.taken_action.target}"
    
    found = False
    for i in range(turn_line_idx, min(len(log_lines), turn_line_idx+10)):
        if action_pattern in log_lines[i] and search_text in log_lines[i]:
            print(f"    ✓ Found at line {i}: {log_lines[i]}")
            found = True
            break
    
    if not found:
        print(f"    ✗ Could not find exact action in log")
    
    print("\n" + "=" * 70)


def list_all_examples(battle_id: str = "gen9championsvgc2026regma-2594983461"):
    """List all examples from a battle with summaries."""
    
    log_path = Path(f"downloaded_logs/gen9championsvgc2026regma/{battle_id}.json")
    if not log_path.exists():
        print(f"Battle not found: {log_path}")
        return
    
    log = load_showdown_log_json(log_path)
    builder = TrainingDatasetBuilder()
    examples = builder.parse_log(log)
    
    print(f"\nALL EXAMPLES FROM {battle_id}:")
    print("=" * 70)
    
    for i, ex in enumerate(examples):
        print(f"\n[{i}] Turn {ex.turn:2d} | {ex.player_side} | {ex.my_active_pokemon:12s} "
              f"({ex.my_active_hp_percent*100:3.0f}%) vs {ex.opponent_active_pokemon:12s} "
              f"({ex.opponent_active_hp_percent*100:3.0f}%) | "
              f"{len(ex.available_actions):2d} opts | "
              f"→ {ex.taken_action.target:12s} | "
              f"{ex.outcome} ({ex.outcome_weight})")


def show_dataset_summary():
    """Show summary statistics across all battles."""
    
    log_dir = Path("downloaded_logs/gen9championsvgc2026regma")
    battles = list(log_dir.glob("*.json"))
    
    print("\n" + "=" * 70)
    print("FULL DATASET SUMMARY")
    print("=" * 70)
    
    total_examples = 0
    all_stats = {
        'avg_actions': [],
        'avg_known_moves': [],
        'outcomes': defaultdict(int),
        'turns': [],
    }
    
    for battle_file in sorted(battles):
        log = load_showdown_log_json(battle_file)
        builder = TrainingDatasetBuilder()
        examples = builder.parse_log(log)
        
        total_examples += len(examples)
        
        for ex in examples:
            all_stats['avg_actions'].append(len(ex.available_actions))
            all_stats['avg_known_moves'].append(len(ex.my_known_moves))
            all_stats['outcomes'][ex.outcome] += 1
            all_stats['turns'].append(ex.turn)
        
        if examples:
            avg_actions = sum(len(e.available_actions) for e in examples) / len(examples)
            print(f"  {battle_file.stem[:40]:40s} | {len(examples):2d} ex | "
                  f"avg {avg_actions:.1f} actions")
    
    print(f"\n{'TOTALS':40s} | {total_examples:2d} ex")
    
    if all_stats['avg_actions']:
        print(f"\nGLOBAL STATISTICS:")
        print(f"  Average available actions: {sum(all_stats['avg_actions'])/len(all_stats['avg_actions']):.2f}")
        print(f"  Average known moves: {sum(all_stats['avg_known_moves'])/len(all_stats['avg_known_moves']):.2f}")
        print(f"  Outcome distribution: {dict(all_stats['outcomes'])}")
        print(f"  Turn range: {min(all_stats['turns'])} - {max(all_stats['turns'])}")


if __name__ == "__main__":
    log_dir = Path("downloaded_logs/gen9championsvgc2026regma")
    battles = sorted([f.stem for f in log_dir.glob("*.json")])
    
    if not battles:
        print("No battle logs found")
        exit(1)
    
    print("\nPOKEMON SHOWDOWN TRAINING DATA COMPARISON TOOL")
    print("=" * 70)
    
    battle_id = battles[0]
    
    # Show all examples
    list_all_examples(battle_id)
    
    # Compare first example
    print("\n")
    compare_turn(battle_id, example_index=0)
    
    # Show dataset summary
    show_dataset_summary()
    
    print("\n" + "=" * 70)
    print("USAGE:")
    print("  compare_turn('battle-id', example_index=0) - Show specific example")
    print("  list_all_examples('battle-id') - List all examples")
    print("  show_dataset_summary() - Statistics across all battles")
