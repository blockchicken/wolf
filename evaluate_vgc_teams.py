#!/usr/bin/env python3
"""
End-to-end VGC team evaluation using the headless battle system.

This demonstrates:
1. Creating team compositions
2. Running battles with RandomDecisionHandler
3. Collecting battle results
4. Extracting training data if needed
5. Ranking teams by performance
"""

import json
from pathlib import Path
from collections import defaultdict
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler

# Example team compositions for evaluation
TEAMS = {
    "balanced": """Charizard||leftovers|blaze|flamethrower,aircutter,dragonclaw,roost|Modest|4,,,252,,252|M|||||
Venusaur||lifeorb|chlorophyll|sludgebomb,gigadrain|Modest|4,,,252,,252|F|||||
Blastoise||assaultvest|torrent|hydropump,darkpulse|Modest|4,,,252,,252|M|||||
Alakazam||lifeorb|magicguard|psychic,focusblast|Timid|4,,,252,,252|||||
Gengar||lifeorb|levitate|shadowball,focusblast|Timid|4,,,252,,252|||||
Machamp||choiceband|guts|dynamicpunch,stoneedge|Adamant|4,252,,,,252|M|||||""",
    
    "speed-focused": """Alakazam||lifeorb|magicguard|psychic,focusblast,shadowball|Timid|4,,,252,,252|||||
Gengar||lifeorb|levitate|shadowball,focusblast|Timid|4,,,252,,252|||||
Pikachu||choiceband|static|thunderbolt,quickattack,wildcharge,extremespeed|Jolly|4,252,,,,252|M|||||
Arcanine||choiceband|intimidate|closecombat|Adamant|4,252,,,,252|M|||||
Archeops||choiceband|defeatist|stoneedge,earthquake,aquajet|Adamant|4,252,,,,252|||||
Cinderace||choiceband|libero|pyroball,aquajet,zipcannon|Adamant|4,252,,,,252|M|||||""",
    
    "tanky": """Blastoise||assaultvest|torrent|hydropump,darkpulse,icebeam,recover|Modest|252,,,252,,4|M|||||
Venusaur||assaultvest|chlorophyll|sludgebomb,gigadrain,synthesis|Calm|252,,,4,252,|F|||||
Machamp||assaultvest|guts|dynamicpunch,stoneedge,defend|Adamant|252,252,,,,4|M|||||
Lapras||leftovers|waterabsorb|surf,icebeam,earthquake,recover|Modest|252,,,252,,4|||||
Raichu||assaultvest|static|thunderbolt,focusblast|Calm|252,,,4,252,|M|||||
Charizard||assaultvest|blaze|flamethrower,roost|Modest|252,,,252,,4|M|||||""",
}


def evaluate_team(showdown_path: Path, team_name: str, team: str, num_battles: int = 10):
    """Evaluate a team composition."""
    runner = BattleRunner(showdown_path=showdown_path, format_id="gen9vgc2025regg")
    
    results = {
        "wins": 0,
        "losses": 0,
        "ties": 0,
        "total_turns": 0,
        "battles": [],
    }
    
    for i in range(num_battles):
        try:
            result, _ = runner.run_battle(
                team_p1=team,
                team_p2=team,  # Battle against itself for now
                p1_handler=RandomDecisionHandler(seed=1000 + i),
                p2_handler=RandomDecisionHandler(seed=2000 + i),
                p1_name="Team",
                p2_name="Opponent",
                max_turns=100,
            )
            
            # Count wins (we're always p1)
            if result.winner == "p1":
                results["wins"] += 1
            elif result.winner == "p2":
                results["losses"] += 1
            else:
                results["ties"] += 1
            
            results["total_turns"] += result.turns
            results["battles"].append({
                "winner": result.winner or "tie",
                "turns": result.turns,
            })
            
        except Exception as e:
            print(f"Error in battle: {e}")
    
    return results


def main():
    showdown_path = Path(__file__).parent.parent / "pokemon-showdown"
    
    if not showdown_path.exists():
        print(f"Pokemon Showdown not found at {showdown_path}")
        return
    
    print("\n" + "="*80)
    print("VGC TEAM EVALUATION")
    print("="*80 + "\n")
    
    team_stats = {}
    
    for team_name, team in TEAMS.items():
        print(f"Evaluating '{team_name}' team ({len(TEAMS[team_name].split('|||||')) // 2} Pokémon)...")
        
        results = evaluate_team(showdown_path, team_name, team, num_battles=10)
        team_stats[team_name] = results
        
        win_rate = results["wins"] / (results["wins"] + results["losses"]) if (results["wins"] + results["losses"]) > 0 else 0
        avg_turns = results["total_turns"] / 10 if 10 > 0 else 0
        
        print(f"  Wins: {results['wins']} | Losses: {results['losses']} | Ties: {results['ties']}")
        print(f"  Win rate: {win_rate*100:.1f}% | Avg turns: {avg_turns:.1f}\n")
    
    # Rankings
    print("="*80)
    print("RANKINGS (by win rate)")
    print("="*80 + "\n")
    
    rankings = sorted(
        team_stats.items(),
        key=lambda x: (x[1]["wins"] / (x[1]["wins"] + x[1]["losses"]) if (x[1]["wins"] + x[1]["losses"]) > 0 else 0),
        reverse=True
    )
    
    for rank, (team_name, stats) in enumerate(rankings, 1):
        win_rate = stats["wins"] / (stats["wins"] + stats["losses"]) if (stats["wins"] + stats["losses"]) > 0 else 0
        avg_turns = stats["total_turns"] / 10
        print(f"{rank}. {team_name:20} | Win rate: {win_rate*100:5.1f}% | Avg turns: {avg_turns:4.1f}")
    
    print("\n" + "="*80)
    print("✅ Team evaluation complete!")
    print("="*80)


if __name__ == "__main__":
    main()
