#!/usr/bin/env python3
"""Test various formats to find working VGC setup."""

import logging
from pathlib import Path
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Test teams
TEAM = """Pikachu||leftovers|static|thunderbolt,quickattack,irontail,thunderwave|Timid|252,,,4,,252|M|||||
Charizard||leftovers|blaze|flamethrower,aircutter,dragonclaw,roost|Modest|4,,,252,,252|M|||||
Venusaur||lifeorb|chlorophyll|sludgebomb,gigadrain,hiddenpowerfire,synthesis|Modest|4,,,252,,252|F|||||
Blastoise||assaultvest|torrent|hydropump,darkpulse,flashcannon|Modest|4,,,252,,252|M|||||
Alakazam||lifeorb|magicguard|psychic,focusblast|Timid|4,,,252,,252|||||
Gengar||lifeorb|levitate|shadowball,focusblast,hiddenpowerfire|Timid|4,,,252,,252|||||"""

FORMATS = [
    "gen9ou",
    "gen9doubles",
    "gen9vgc2024regg",
    "gen9vgc2025regg",
    "gen9championsvgc2026regma",
]


def test_format(showdown_path: Path, format_id: str):
    """Test a single format."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Testing: {format_id}")
    logger.info('='*60)
    
    try:
        runner = BattleRunner(showdown_path=showdown_path, format_id=format_id)
        
        result, _ = runner.run_battle(
            team_p1=TEAM,
            team_p2=TEAM,
            p1_handler=RandomDecisionHandler(seed=123),
            p2_handler=RandomDecisionHandler(seed=456),
            p1_name="P1",
            p2_name="P2",
            max_turns=10,
        )
        
        logger.info(f"✓ Format works!")
        logger.info(f"  Turns: {result.turns}")
        logger.info(f"  Winner: {result.winner or 'Tie'}")
        return True
        
    except Exception as e:
        logger.error(f"✗ Format failed: {str(e)[:100]}")
        return False


def main():
    showdown_path = Path(__file__).parent.parent / "pokemon-showdown"
    
    if not showdown_path.exists():
        print(f"Pokemon Showdown not found at {showdown_path}")
        return
    
    results = {}
    for fmt in FORMATS:
        results[fmt] = test_format(showdown_path, fmt)
    
    logger.info(f"\n{'='*60}")
    logger.info("SUMMARY")
    logger.info('='*60)
    for fmt, works in results.items():
        status = "✓" if works else "✗"
        logger.info(f"{status} {fmt}")


if __name__ == "__main__":
    main()
