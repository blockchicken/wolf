#!/usr/bin/env python3
"""Detailed validation and inspection of training data extraction.

Shows what was parsed, verifies state transitions, and validates outcomes.
"""

import json
from pathlib import Path
from collections import defaultdict

from showdown_ai import load_showdown_log_json, TrainingDatasetBuilder


def inspect_battle(battle_id: str = "gen9championsvgc2026regma-2594983461"):
    """Inspect a specific battle's extraction in detail."""
    
    log_path = Path(f"downloaded_logs/gen9championsvgc2026regma/{battle_id}.json")
    if not log_path.exists():
        print(f"Battle not found: {log_path}")
        return
    
    # Load raw JSON to see structure
    with open(log_path) as f:
        raw_data = json.load(f)
    
    print("=" * 70)
    print(f"BATTLE INSPECTION: {battle_id}")
    print("=" * 70)
    
    print(f"\nRAW JSON STRUCTURE:")
    print(f"  Players: {raw_data.get('players')}")
    print(f"  Format: {raw_data.get('format')}")
    print(f"  Uploadtime: {raw_data.get('uploadtime')}")
    
    log_lines = raw_data['log'].splitlines()
    print(f"  Log lines: {len(log_lines)}")
    
    # Identify key event types
    event_counts = defaultdict(int)
    for line in log_lines:
        if '|' in line:
            event_type = line.split('|')[1]
            event_counts[event_type] += 1
    
    print(f"\nKEY EVENTS IN LOG:")
    for event_type in ['poke', 'switch', 'move', 'faint', '-damage', 'turn', 'win']:
        if event_type in event_counts:
            print(f"  {event_type}: {event_counts[event_type]}")
    
    # Parse the log
    log = load_showdown_log_json(log_path)
    print(f"\nPARSED LOG:")
    print(f"  Battle ID: {log.battle_id}")
    print(f"  Format: {log.format_name}")
    print(f"  Players: {log.players}")
    print(f"  Total events after filtering: {len(log.events)}")
    
    # Extract examples
    builder = TrainingDatasetBuilder()
    examples = builder.parse_log(log)
    
    print(f"\nEXTRACTED TRAINING EXAMPLES:")
    print(f"  Total: {len(examples)}")
    
    if examples:
        # Group by outcome
        by_outcome = defaultdict(list)
        for ex in examples:
            by_outcome[ex.outcome].append(ex)
        
        print(f"  By outcome:")
        for outcome in sorted(by_outcome.keys()):
            exs = by_outcome[outcome]
            total_weight = sum(e.outcome_weight for e in exs)
            print(f"    {outcome}: {len(exs)} examples (total weight: {total_weight:.1f})")
        
        # Show details of first few examples
        print(f"\n  FIRST 5 EXAMPLES:")
        for i, ex in enumerate(examples[:5], 1):
            print(f"\n    Example {i}:")
            print(f"      Turn: {ex.turn}, Side: {ex.player_side}")
            print(f"      My pokemon: {ex.my_active_pokemon} ({ex.my_active_hp_percent*100:.0f}%)")
            print(f"      Opponent: {ex.opponent_active_pokemon} ({ex.opponent_active_hp_percent*100:.0f}%)")
            print(f"      My team: {len(ex.my_team)} pokemon")
            print(f"      Opponent team: {len(ex.opponent_team)} pokemon")
            print(f"      Known my moves: {ex.my_known_moves}")
            print(f"      Available actions: {len(ex.available_actions)} total")
            
            # Show first few available actions
            print(f"        - Options:")
            for action in ex.available_actions[:4]:
                print(f"          * {action.action_type.value}: {action.target}")
            if len(ex.available_actions) > 4:
                print(f"          ... and {len(ex.available_actions) - 4} more")
            
            print(f"      Chosen: {ex.taken_action.action_type.value} {ex.taken_action.target}")
            print(f"      Outcome: {ex.outcome} (weight: {ex.outcome_weight})")


def validate_state_consistency(battle_id: str = "gen9championsvgc2026regma-2594983461"):
    """Validate that extracted state is consistent with the battle log."""
    
    log_path = Path(f"downloaded_logs/gen9championsvgc2026regma/{battle_id}.json")
    if not log_path.exists():
        print(f"Battle not found: {log_path}")
        return
    
    print("\n" + "=" * 70)
    print(f"STATE CONSISTENCY CHECK: {battle_id}")
    print("=" * 70)
    
    # Load raw to verify key events
    with open(log_path) as f:
        raw_data = json.load(f)
    
    log_lines = raw_data['log'].splitlines()
    
    # Track actual state from raw log
    team_p1 = {}  # {name: hp_pct}
    team_p2 = {}
    active_p1 = None
    active_p2 = None
    fainted_p1 = set()
    fainted_p2 = set()
    
    print("\nTRACKING STATE THROUGH LOG:")
    
    for i, line in enumerate(log_lines):
        if not line.startswith('|'):
            continue
        
        parts = line.split('|')
        if len(parts) < 2:
            continue
        
        event_type = parts[1]
        
        # Track teams
        if event_type == "poke":
            side = parts[2]
            pokemon_spec = parts[3]
            name = pokemon_spec.split(',')[0]
            
            if side == "p1":
                team_p1[name] = 1.0
            else:
                team_p2[name] = 1.0
        
        # Track active
        elif event_type == "switch":
            actor = parts[2]
            pokemon_spec = parts[3]
            name = pokemon_spec.split(',')[0]
            hp_str = parts[4] if len(parts) > 4 else "100/100"
            hp_frac = parse_hp(hp_str)
            
            if actor.startswith("p1"):
                active_p1 = name
                team_p1[name] = hp_frac
            else:
                active_p2 = name
                team_p2[name] = hp_frac
        
        # Track damage
        elif event_type == "-damage":
            actor = parts[2]
            hp_str = parts[3]
            hp_frac = parse_hp(hp_str)
            
            if actor.startswith("p1"):
                if active_p1:
                    team_p1[active_p1] = hp_frac
            else:
                if active_p2:
                    team_p2[active_p2] = hp_frac
        
        # Track faints
        elif event_type == "faint":
            actor = parts[2]
            name = actor.split(': ')[1] if ': ' in actor else "Unknown"
            
            if actor.startswith("p1"):
                fainted_p1.add(name)
            else:
                fainted_p2.add(name)
    
    print(f"\nRaw state from log:")
    print(f"  P1 team: {list(team_p1.keys())}")
    print(f"  P2 team: {list(team_p2.keys())}")
    print(f"  P1 fainted: {fainted_p1}")
    print(f"  P2 fainted: {fainted_p2}")
    
    # Now extract and compare
    log = load_showdown_log_json(log_path)
    builder = TrainingDatasetBuilder()
    examples = builder.parse_log(log)
    
    if not examples:
        print("\n✗ No examples extracted - cannot validate")
        return
    
    print(f"\nExtracted state from examples:")
    
    # Look at first and last examples
    first_ex = examples[0]
    print(f"  First example:")
    print(f"    My team: {[name for name, _ in first_ex.my_team]}")
    print(f"    Opponent team: {[name for name, _ in first_ex.opponent_team]}")
    
    # Validate some basic properties
    print(f"\n✓ VALIDATION CHECKS:")
    
    all_valid = True
    
    # Check 1: Team sizes match
    if len(first_ex.my_team) == len(team_p1):
        print(f"  ✓ P1 team size matches ({len(team_p1)})")
    else:
        print(f"  ✗ P1 team size mismatch: extracted {len(first_ex.my_team)} vs raw {len(team_p1)}")
        all_valid = False
    
    # Check 2: HP values in valid range
    hp_valid = all(0 <= e.my_active_hp_percent <= 1.0 for e in examples)
    if hp_valid:
        print(f"  ✓ All HP percentages in valid range [0, 1]")
    else:
        print(f"  ✗ Some HP percentages out of range")
        all_valid = False
    
    # Check 3: Actions are sensible
    actions_valid = all(len(e.available_actions) > 0 for e in examples)
    if actions_valid:
        print(f"  ✓ All examples have available actions")
    else:
        print(f"  ✗ Some examples have no available actions")
        all_valid = False
    
    # Check 4: Taken actions are in available
    taken_in_available = all(
        any(a.action_type == e.taken_action.action_type and a.target == e.taken_action.target
            for a in e.available_actions)
        for e in examples
    )
    if taken_in_available:
        print(f"  ✓ All taken actions are in available actions")
    else:
        print(f"  ✗ Some taken actions not in available actions list")
        all_valid = False
    
    # Check 5: Outcome weights are correct
    weight_valid = all(
        (e.outcome == "win" and e.outcome_weight == 1.0) or
        (e.outcome == "loss" and e.outcome_weight == 0.5) or
        (e.outcome == "tie" and e.outcome_weight == 0.25)
        for e in examples
    )
    if weight_valid:
        print(f"  ✓ All outcome weights are correct")
    else:
        print(f"  ✗ Some outcome weights are incorrect")
        all_valid = False
    
    if all_valid:
        print(f"\n✓ ALL VALIDATION CHECKS PASSED")
    else:
        print(f"\n✗ SOME VALIDATION CHECKS FAILED")


def parse_hp(hp_str: str) -> float:
    """Parse HP string like '50/100', '50%', or '?' to fraction 0-1."""
    if not hp_str or hp_str == "?" or hp_str == "?/?":
        return 0.5
    
    if hp_str.endswith("%"):
        try:
            return float(hp_str[:-1]) / 100.0
        except:
            return 0.5
    
    if "/" in hp_str:
        try:
            parts = hp_str.split("/")
            current = float(parts[0])
            maximum = float(parts[1])
            return current / maximum if maximum > 0 else 0.5
        except:
            return 0.5
    
    return 0.5


if __name__ == "__main__":
    print("POKEMON SHOWDOWN TRAINING DATA VALIDATOR\n")
    
    # Get available battles
    log_dir = Path("downloaded_logs/gen9championsvgc2026regma")
    if not log_dir.exists():
        print(f"No logs found at {log_dir}")
        exit(1)
    
    battles = sorted([f.stem for f in log_dir.glob("*.json")])
    
    if not battles:
        print("No battle files found")
        exit(1)
    
    print(f"Found {len(battles)} battle logs")
    print(f"Using first battle for inspection: {battles[0]}\n")
    
    # Inspect first battle
    inspect_battle(battles[0])
    
    # Validate state consistency
    validate_state_consistency(battles[0])
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"✓ Validation complete")
    print(f"✓ {len(battles)} battle logs available")
    print(f"✓ Run with specific battle ID to inspect different battles")
