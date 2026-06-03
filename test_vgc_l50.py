#!/usr/bin/env python3
"""Test gen9championsvgc2026regma with L50 regulation teams."""

import logging
from pathlib import Path
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

# VGC 2026 Reg M-A regulation teams (L50)
# Format: Pokémon||Item|Ability|Moves|Nature|EV-HP,EV-Atk,EV-SpA,EV-SpD,EV-Spe|Gender|IVs|...
TEAM_REG = """Incineroar, L50||assaultvest|intimidate|flamecharge,darkestlariat,stoneedge,closecombat|Careful|252,252,,,,4|M|||||
Garchomp, L50||choiceband|roughskin|stoneedge,earthquake,outrage,firefang|Jolly|4,252,,,,252|M|||||
Milotic, L50||assaultvest|competitive|recover,icebeam,surf,toxic|Calm|252,,,4,252,|F|||||
Gardevoir, L50||lifeorb|synchronize|psychic,shadowball,hypervoice,focusblast|Timid|4,,,252,,252|F|||||
Corviknight, L50||leftovers|pressure|roost,tailwind,bodypress,steelbeam|Impish|252,,,,,4|F|||||
Rotom-Wash, L50||leftovers|levitate|hydropump,voltswitch|Quiet|252,,,,252,|||||"""

def test_vgc_with_levels():
    """Test with L50 teams."""
    showdown_path = Path(__file__).parent.parent / "pokemon-showdown"
    
    if not showdown_path.exists():
        logger.error("Pokemon Showdown not found")
        return
    
    logger.info("\n" + "="*70)
    logger.info("Testing gen9championsvgc2026regma with L50 teams")
    logger.info("="*70 + "\n")
    
    runner = BattleRunner(showdown_path=showdown_path, format_id="gen9championsvgc2026regma")
    
    for i in range(1, 4):
        try:
            result, _ = runner.run_battle(
                team_p1=TEAM_REG,
                team_p2=TEAM_REG,
                p1_handler=RandomDecisionHandler(seed=100 + i),
                p2_handler=RandomDecisionHandler(seed=200 + i),
                p1_name="P1",
                p2_name="P2",
                max_turns=100,
            )
            
            logger.info(f"Battle {i}/3: ✓ {result.turns:2d} turns | Winner: {result.winner or 'Tie'}")
            
        except Exception as e:
            logger.error(f"✗ {str(e)[:80]}")

if __name__ == "__main__":
    test_vgc_with_levels()
