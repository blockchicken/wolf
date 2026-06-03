#!/usr/bin/env python3
"""Test script: Run random AI vs random AI singles battles."""

from pathlib import Path
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler

# Standard OU teams
TEAM_1 = """Pikachu||leftovers|static|thunderbolt,quickattack,irontail,thunderwave|Timid|252,,,4,,252|M|||||
Charizard||leftovers|blaze|flamethrower,aircutter,dragonclaw,roost|Modest|4,,,252,,252|M|||||
Venusaur||lifeorb|chlorophyll|sludgebomb,gigadrain,hiddenpowerfire,synthesis|Modest|4,,,252,,252|F|||||
Blastoise||assaultvest|torrent|hydropump,darkpulse,flashcannon|Modest|4,,,252,,252|M|||||
Alakazam||lifeorb|magicguard|psychic,focusblast|Timid|4,,,252,,252|||||
Gengar||lifeorb|levitate|shadowball,focusblast,hiddenpowerfire|Timid|4,,,252,,252|||||"""

TEAM_2 = """Dragonite||leftovers|multiscale|outrage,earthquake,extremespeed,dragondance|Adamant|4,252,,,,252|M|||||
Alakazam||lifeorb|magicguard|psychic,focusblast,shadowball,dazzlinggleam|Timid|4,,,252,,252|M|||||
Mamoswine||assaultvest|thickfat|earthquake,stoneedge,iciclecrash,knockoff|Adamant|252,252,,,,4|M|||||
Gyarados||leftovers|intimidate|waterfall,earthquake,stoneedge,dragondance|Adamant|252,252,,,,4|M|||||
Salamence||leftovers|intimidate|outrage,earthquake,stoneedge,dragondance|Adamant|252,252,,,,4|M|||||
Landorus||leftovers|intimidate|earthquake,stoneedge|Adamant|252,252,,,,4|M|||||"""


def main():
    showdown_path = Path(__file__).parent.parent / "pokemon-showdown"
    
    if not showdown_path.exists():
        print(f"Pokemon Showdown not found at {showdown_path}")
        return
    
    print("\n" + "="*70)
    print("Pokemon Showdown Headless Battle Test (SINGLES)")
    print("="*70)
    print(f"Format: gen9ou")
    print(f"Number of battles: 5")
    print("="*70 + "\n")
    
    runner = BattleRunner(
        showdown_path=showdown_path,
        format_id="gen9ou",
    )
    
    results = []
    
    for i in range(1, 6):
        print(f"Battle {i}/5...", end=" ", flush=True)
        
        p1_handler = RandomDecisionHandler(seed=100 + i)
        p2_handler = RandomDecisionHandler(seed=200 + i)
        
        try:
            result, _ = runner.run_battle(
                team_p1=TEAM_1,
                team_p2=TEAM_2,
                p1_handler=p1_handler,
                p2_handler=p2_handler,
                p1_name="RandomPlayer1",
                p2_name="RandomPlayer2",
                max_turns=100,
            )
            
            results.append(result)
            print(f"✓ {result.turns} turns | Winner: {result.winner or 'Tie'}")
            
        except Exception as e:
            print(f"✗ Error: {e}")
    
    print("\n" + "="*70)
    print("RESULTS SUMMARY")
    print("="*70)
    
    p1_wins = sum(1 for r in results if r.winner == "p1")
    p2_wins = sum(1 for r in results if r.winner == "p2")
    ties = sum(1 for r in results if r.winner is None)
    avg_turns = sum(r.turns for r in results) / len(results) if results else 0
    
    print(f"P1 Wins: {p1_wins}")
    print(f"P2 Wins: {p2_wins}")
    print(f"Ties: {ties}")
    print(f"Average Turns: {avg_turns:.1f}")
    print("="*70 + "\n")


if __name__ == "__main__":
    main()
