#!/usr/bin/env python3
"""Test gen9championsvgc2026regma with properly formatted Champions teams."""

import logging
from pathlib import Path
from pokepaste_parser import parse_pokepaste
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Real Champions Pokepaste format
# Champions stat points: max 32 per stat, 66 total per Pokémon (replaces traditional EVs)
# Champions items: Mega Stones replace most hold items (e.g. Starmite, Charizardite Y)
CHAMPIONS_TEAM_1 = """Starmie @ Starminite
Ability: Natural Cure
Level: 50
EVs: 2 HP / 32 Atk / 32 Spe
Adamant Nature
- Agility
- Aqua Jet
- Bulk Up
- Flip Turn

Incineroar @ Incinerite
Ability: Intimidate
Level: 50
EVs: 32 HP / 2 Atk / 32 SpD
Careful Nature
- Fake Out
- Darkest Lariat
- Close Combat
- Flare Blitz

Dragonite @ Dragonite
Ability: Multiscale
Level: 50
EVs: 2 HP / 32 Atk / 32 Spe
Adamant Nature
- Outrage
- Earthquake
- Extreme Speed
- Superpower

Corviknight @ Corviknite
Ability: Pressure
Level: 50
EVs: 32 HP / 2 Def / 32 SpD
Impish Nature
- Roost
- Tailwind
- Body Press
- Steel Beam

Milotic @ Milotite
Ability: Competitive
Level: 50
EVs: 32 HP / 2 SpA / 32 SpD
Calm Nature
- Recover
- Ice Beam
- Surf
- Muddy Water

Gardevoir @ Gardevoirite
Ability: Trace
Level: 50
EVs: 2 HP / 32 SpA / 32 Spe
Timid Nature
- Psychic
- Shadow Ball
- Hyper Voice
- Focus Blast"""

CHAMPIONS_TEAM_2 = """Charizard @ Charizardite Y
Ability: Blaze
Level: 50
EVs: 2 HP / 32 SpA / 32 Spe
Timid Nature
- Flamethrower
- Air Cutter
- Dragon Claw
- Roost

Pikachu @ Pikachuite
Ability: Static
Level: 50
EVs: 2 HP / 32 SpA / 32 Spe
Timid Nature
- Thunderbolt
- Quick Attack
- Wild Charge
- Volt Switch

Alakazam @ Alakazamite
Ability: Magic Guard
Level: 50
EVs: 2 HP / 32 SpA / 32 Spe
Timid Nature
- Psychic
- Focus Blast
- Shadow Ball
- Dazzling Gleam

Machamp @ Machampite
Ability: Guts
Level: 50
EVs: 32 HP / 32 Atk / 2 Spe
Adamant Nature
- Dynamic Punch
- Heavy Slam
- Bullet Punch
- Close Combat

Lapras @ Laprasite
Ability: Water Absorb
Level: 50
EVs: 32 HP / 2 SpA / 32 SpD
Calm Nature
- Surf
- Ice Beam
- Earthquake
- Freeze-Dry

Raichu @ Raichuite
Ability: Static
Level: 50
EVs: 2 HP / 32 SpA / 32 Spe
Timid Nature
- Thunderbolt
- Focus Blast
- Nasty Plot
- Volt Switch"""


def test_champions_format():
    """Test Champions format teams with gen9championsvgc2026regma."""
    showdown_path = Path(__file__).parent.parent / "pokemon-showdown"
    
    if not showdown_path.exists():
        logger.error(f"Pokemon Showdown not found")
        return
    
    # Parse teams
    team1_pokemon = parse_pokepaste(CHAMPIONS_TEAM_1)
    team2_pokemon = parse_pokepaste(CHAMPIONS_TEAM_2)
    
    # Pokémon sets must be joined with ] — Showdown's Teams.unpack() requires this
    team1_str = "]".join(p.to_packed() for p in team1_pokemon)
    team2_str = "]".join(p.to_packed() for p in team2_pokemon)
    
    logger.info("\n" + "="*80)
    logger.info("Testing gen9championsvgc2026regma with Champions Format Teams")
    logger.info("="*80)
    logger.info(f"\nTeam 1 ({len(team1_pokemon)} Pokémon):")
    for p in team1_pokemon:
        logger.info(f"  - {p.name}, L{p.level}")
    
    logger.info(f"\nTeam 2 ({len(team2_pokemon)} Pokémon):")
    for p in team2_pokemon:
        logger.info(f"  - {p.name}, L{p.level}")
    
    logger.info("\n" + "="*80)
    logger.info("Running 5 battles...")
    logger.info("="*80 + "\n")
    
    runner = BattleRunner(showdown_path=showdown_path, format_id="gen9championsvgc2026regma")
    
    results = []
    for i in range(1, 6):
        try:
            result, _ = runner.run_battle(
                team_p1=team1_str,
                team_p2=team2_str,
                p1_handler=RandomDecisionHandler(seed=100 + i),
                p2_handler=RandomDecisionHandler(seed=200 + i),
                p1_name="Team1",
                p2_name="Team2",
                max_turns=100,
            )
            
            results.append(result)
            winner = result.winner.upper() if result.winner else "TIE"
            logger.info(f"Battle {i}/5: ✓ {result.turns:2d} turns | Winner: {winner}")
            
        except Exception as e:
            logger.error(f"Battle {i}/5: ✗ {str(e)[:80]}")
    
    # Summary
    if results:
        p1_wins = sum(1 for r in results if r.winner == "p1")
        p2_wins = sum(1 for r in results if r.winner == "p2")
        ties = sum(1 for r in results if r.winner is None)
        avg_turns = sum(r.turns for r in results) / len(results)
        
        logger.info("\n" + "="*80)
        logger.info("RESULTS")
        logger.info("="*80)
        logger.info(f"Team 1 wins: {p1_wins}")
        logger.info(f"Team 2 wins: {p2_wins}")
        logger.info(f"Ties: {ties}")
        logger.info(f"Average turns: {avg_turns:.1f}")
        
        if avg_turns > 2 and ties == 0:
            logger.info("\n✅ SUCCESS! Champions format works with proper teams!")
        else:
            logger.info("\n❌ Still having issues - format may need adjustment")
        
        logger.info("="*80)


if __name__ == "__main__":
    test_champions_format()
