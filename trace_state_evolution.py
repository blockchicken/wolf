#!/usr/bin/env python3
"""Trace state evolution through a battle to understand parsing.

Shows how game state changes through each turn, and what training examples
were extracted from each decision point.
"""

import json
from pathlib import Path

from showdown_ai import load_showdown_log_json, TrainingDatasetBuilder


def trace_state_evolution(battle_id: str = "gen9championsvgc2026regma-2594983461"):
    """Show how state changes through the battle, highlighting decisions."""
    
    log_path = Path(f"downloaded_logs/gen9championsvgc2026regma/{battle_id}.json")
    if not log_path.exists():
        print(f"Battle not found: {log_path}")
        return
    
    # Load data
    with open(log_path) as f:
        raw_data = json.load(f)
    
    log_lines = raw_data['log'].splitlines()
    log = load_showdown_log_json(log_path)
    builder = TrainingDatasetBuilder()
    examples = builder.parse_log(log)
    
    # Map examples by turn for quick lookup
    examples_by_turn = {}
    for ex in examples:
        key = (ex.turn, ex.player_side)
        if key not in examples_by_turn:
            examples_by_turn[key] = []
        examples_by_turn[key].append(ex)
    
    print("=" * 80)
    print(f"STATE EVOLUTION TRACE: {battle_id}")
    print("=" * 80)
    
    # Track state as we go
    state = {
        'turn': 0,
        'p1_active': None,
        'p2_active': None,
        'p1_team': {},
        'p2_team': {},
        'p1_hp': {},
        'p2_hp': {},
        'p1_fainted': set(),
        'p2_fainted': set(),
    }
    
    print(f"\nTeam Composition:")
    print(f"  P1: {raw_data.get('players')[0] if raw_data.get('players') else '?'}")
    print(f"  P2: {raw_data.get('players')[1] if raw_data.get('players') else '?'}")
    
    # Initial setup
    p1_pokemon = []
    p2_pokemon = []
    for line in log_lines:
        if "|poke|p1|" in line:
            parts = line.split('|')
            name = parts[3].split(',')[0]
            p1_pokemon.append(name)
            state['p1_team'][name] = 1.0
        elif "|poke|p2|" in line:
            parts = line.split('|')
            name = parts[3].split(',')[0]
            p2_pokemon.append(name)
            state['p2_team'][name] = 1.0
    
    print(f"\n  P1 team: {', '.join(p1_pokemon)}")
    print(f"  P2 team: {', '.join(p2_pokemon)}")
    
    # Process each turn
    print(f"\n" + "-" * 80)
    print("TURN-BY-TURN STATE CHANGES:")
    print("-" * 80)
    
    for line_idx, line in enumerate(log_lines):
        if not line.startswith('|'):
            continue
        
        parts = line.split('|')
        if len(parts) < 2:
            continue
        
        event_type = parts[1]
        
        # Track turns
        if event_type == "turn":
            state['turn'] = int(parts[2])
            
            # Show turn separator
            print(f"\n>>> TURN {state['turn']} <<<")
            print(f"    P1: {state['p1_active']:12s} ({state['p1_hp'].get(state['p1_active'], 1.0)*100:3.0f}%) | "
                  f"P2: {state['p2_active']:12s} ({state['p2_hp'].get(state['p2_active'], 1.0)*100:3.0f}%)")
            
            # Check for extracted examples at this turn
            for player_side in ['p1', 'p2']:
                key = (state['turn'], player_side)
                if key in examples_by_turn:
                    for ex in examples_by_turn[key]:
                        print(f"    EXAMPLE: {player_side} chose "
                              f"{ex.taken_action.action_type.value} {ex.taken_action.target} "
                              f"({len(ex.available_actions)} options available)")
        
        # Track active pokemon
        elif event_type == "switch":
            side = parts[2][:2]
            name = parts[3].split(',')[0]
            hp_str = parts[4] if len(parts) > 4 else "100/100"
            hp = parse_hp(hp_str)
            
            if side == "p1":
                state['p1_active'] = name
                state['p1_hp'][name] = hp
                print(f"    P1 switch in: {name} ({hp*100:.0f}%)")
            else:
                state['p2_active'] = name
                state['p2_hp'][name] = hp
                print(f"    P2 switch in: {name} ({hp*100:.0f}%)")
        
        # Track damage
        elif event_type == "-damage":
            side = parts[2][:2]
            hp_str = parts[3]
            hp = parse_hp(hp_str)
            
            if side == "p1" and state['p1_active']:
                state['p1_hp'][state['p1_active']] = hp
            elif side == "p2" and state['p2_active']:
                state['p2_hp'][state['p2_active']] = hp
        
        # Track faints
        elif event_type == "faint":
            side = parts[2][:2]
            name = parts[2].split(': ')[1] if ': ' in parts[2] else "Unknown"
            
            if side == "p1":
                state['p1_fainted'].add(name)
                print(f"    P1 fainted: {name}")
            else:
                state['p2_fainted'].add(name)
                print(f"    P2 fainted: {name}")
        
        # Track moves
        elif event_type == "move":
            actor = parts[2]
            move = parts[3]
            side = actor[:2]
            
            pokemon = actor.split(': ')[1] if ': ' in actor else "Unknown"
            print(f"    {side.upper()} used {move} ({pokemon})")
    
    # Final result
    for line in log_lines:
        if "|win|" in line:
            winner = line.split('|')[2]
            print(f"\n>>> BATTLE RESULT <<<")
            print(f"    Winner: {winner}")
            break


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
    log_dir = Path("downloaded_logs/gen9championsvgc2026regma")
    battles = sorted([f.stem for f in log_dir.glob("*.json")])
    
    if not battles:
        print("No battle logs found")
        exit(1)
    
    # Trace first battle
    trace_state_evolution(battles[0])
    
    print("\n" + "=" * 80)
    print("✓ State evolution trace complete")
    print("=" * 80)
