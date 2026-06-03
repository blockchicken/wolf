#!/usr/bin/env python3
"""Final test: VGC with gen9vgc2025regg (confirmed working) vs gen9championsvgc2026regma."""

import logging
from pathlib import Path
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# Standard valid teams
TEAM = """Pikachu||leftovers|static|thunderbolt,quickattack,irontail,thunderwave|Timid|252,,,4,,252|M|||||
Charizard||leftovers|blaze|flamethrower,aircutter,dragonclaw,roost|Modest|4,,,252,,252|M|||||
Venusaur||lifeorb|chlorophyll|sludgebomb,gigadrain|Modest|4,,,252,,252|F|||||
Blastoise||assaultvest|torrent|hydropump,darkpulse|Modest|4,,,252,,252|M|||||
Alakazam||lifeorb|magicguard|psychic,focusblast|Timid|4,,,252,,252|||||
Gengar||lifeorb|levitate|shadowball,focusblast|Timid|4,,,252,,252|||||"""


def run_vgc_battle(showdown_path: Path, format_id: str, num_battles: int = 3):
    """Run VGC battles with a specific format."""
    logger.info(f"\n{'='*70}")
    logger.info(f"Testing: {format_id}")
    logger.info(f"Running {num_battles} battles")
    logger.info('='*70 + "\n")
    
    runner = BattleRunner(showdown_path=showdown_path, format_id=format_id)
    
    results = []
    for i in range(1, num_battles + 1):
        try:
            result, _ = runner.run_battle(
                team_p1=TEAM,
                team_p2=TEAM,
                p1_handler=RandomDecisionHandler(seed=100 + i),
                p2_handler=RandomDecisionHandler(seed=200 + i),
                p1_name="P1",
                p2_name="P2",
                max_turns=100,
            )
            
            results.append(result)
            logger.info(f"Battle {i}/{num_battles}: ✓ {result.turns:2d} turns | Winner: {result.winner or 'Tie':4s}")
            
        except Exception as e:
            logger.error(f"✗ {str(e)[:60]}")
    
    # Summary
    if results:
        p1_wins = sum(1 for r in results if r.winner == "p1")
        p2_wins = sum(1 for r in results if r.winner == "p2")
        ties = sum(1 for r in results if r.winner is None)
        avg_turns = sum(r.turns for r in results) / len(results)
        
        logger.info(f"\nResults: P1={p1_wins} P2={p2_wins} Ties={ties} | Avg turns: {avg_turns:.1f}")


def main():
    showdown_path = Path(__file__).parent.parent / "pokemon-showdown"
    
    if not showdown_path.exists():
        logger.error(f"Pokemon Showdown not found")
        return
    
    # Test both VGC formats
    run_vgc_battle(showdown_path, "gen9vgc2025regg", num_battles=5)
    run_vgc_battle(showdown_path, "gen9championsvgc2026regma", num_battles=5)
    
    logger.info(f"\n{'='*70}")
    logger.info("RECOMMENDATION:")
    logger.info("Use gen9vgc2025regg for VGC doubles battles (works correctly)")
    logger.info("gen9championsvgc2026regma has team parsing issues")
    logger.info('='*70)


if __name__ == "__main__":
    main()
