#!/usr/bin/env python3
"""Test script: Run random AI vs random AI battles headlessly.

Before running:
1. Clone and build Pokemon Showdown:
   git clone https://github.com/smogon/pokemon-showdown.git
   cd pokemon-showdown
   npm install
   ./build  (or 'node build' on Windows)

2. Update SHOWDOWN_PATH below to point to your pokemon-showdown directory

Usage:
    python test_headless_battles.py
"""

import json
from pathlib import Path
from typing import Tuple
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler, BattleResult


# ============================================================================
# CONFIGURATION
# ============================================================================

# Update this to your pokemon-showdown checkout directory
SHOWDOWN_PATH = Path(__file__).parent.parent / "pokemon-showdown"

# Example teams (packed format from Showdown)
# These are real VGC 2024 Regulation G teams
TEAM_1 = """Incineroar||assaultvest|intimidate|flamecharge,darkestlariat,stoneedge,closecombat|Careful|252,252,,,,4|M|||||
Garchomp||choiceband|roughskin|stoneedge,earthquake,outrage,firefang|Jolly|4,252,,,,252|M|||||
Milotic||assaultvest|competitive|recover,icebeam,surf,toxic|Calm|252,,,4,252,|F|||||
Gardevoir||lifeorb|synchronize|psychic,shadowball,hypervoice,focusblast|Timid|4,,,252,,252|F|||||
Corviknight||leftovers|pressure|roost,tailwind,bodypress,steelbeam|Impish|252,,,,,4|F|||||
Rotom-Wash||leftovers|levitate|hydropump,voltswitch|Quiet|252,,,,252,|||||"""

TEAM_2 = """Ceruledge||lifeorb|flash fire|psychocut,shadowclaw,x-scissor,closecombat|Quiet|252,252,,,,4|M|||||
Charizard||airballoon|blaze|solarbeam,flamethrower,earthquake,stoneedge|Modest|4,,,252,,252|M|||||
Pikachu||lifeorb|static|thunderbolt,quickattack,wildcharge,surf|Timid|4,,,252,,252|M|||||
Archaludon||assaultvest|heatproof|flashcannon,shadowball,earthpower,thunderbolt|Modest|4,,,252,,252|||||
Slowbro-Galar||assaultvest|regenerator|psychic,shellsidearm,scald,recover|Calm|252,,,4,252,|M|||||
Dragonite||choicescarf|multiscale|outrage,earthquake,extremespeed,dragondance|Adamant|4,252,,,,252|M|||||"""


def run_test_battle(
    showdown_path: Path,
    team_1: str,
    team_2: str,
    num_battles: int = 3,
) -> None:
    """Run multiple test battles and report results."""

    print(f"\n{'='*70}")
    print(f"Pokemon Showdown Headless Battle Test")
    print(f"{'='*70}")
    print(f"Showdown Path: {showdown_path}")
    print(f"Format: gen9vgc2024regg (Doubles)")
    print(f"Number of battles: {num_battles}")
    print(f"{'='*70}\n")

    runner = BattleRunner(
        showdown_path=showdown_path,
        format_id="gen9vgc2024regg",
    )

    results: list[BattleResult] = []

    for battle_num in range(1, num_battles + 1):
        print(f"Battle {battle_num}/{num_battles}...", end=" ", flush=True)

        try:
            # Create random handlers with different seeds
            p1_handler = RandomDecisionHandler(seed=100 + battle_num)
            p2_handler = RandomDecisionHandler(seed=200 + battle_num)

            # Run battle
            result, training_data = runner.run_battle(
                team_p1=team_1,
                team_p2=team_2,
                p1_handler=p1_handler,
                p2_handler=p2_handler,
                p1_name="RandomPlayer1",
                p2_name="RandomPlayer2",
                max_turns=50,
                collect_training_data=False,  # Set to True to collect (state, action) pairs
            )

            results.append(result)
            print(
                f"✓ {result.turns} turns | "
                f"Winner: {result.winner or 'Tie'}"
            )

        except Exception as e:
            print(f"✗ Error: {e}")

    # Summary
    print(f"\n{'='*70}")
    print(f"RESULTS SUMMARY")
    print(f"{'='*70}")

    p1_wins = sum(1 for r in results if r.winner == "p1")
    p2_wins = sum(1 for r in results if r.winner == "p2")
    ties = sum(1 for r in results if r.winner is None)
    avg_turns = sum(r.turns for r in results) / len(results) if results else 0

    print(f"P1 Wins: {p1_wins}")
    print(f"P2 Wins: {p2_wins}")
    print(f"Ties: {ties}")
    print(f"Average Turns: {avg_turns:.1f}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    if not SHOWDOWN_PATH.exists():
        print(f"\n❌ ERROR: Pokemon Showdown not found at {SHOWDOWN_PATH}")
        print("\nTo set up:")
        print("  1. Clone: git clone https://github.com/smogon/pokemon-showdown.git")
        print("  2. Navigate: cd pokemon-showdown")
        print("  3. Install: npm install")
        print("  4. Build: ./build (or 'node build' on Windows)")
        print(f"\n  Then update SHOWDOWN_PATH in this script to point to that directory")
        exit(1)

    run_test_battle(
        showdown_path=SHOWDOWN_PATH,
        team_1=TEAM_1,
        team_2=TEAM_2,
        num_battles=3,
    )
