#!/usr/bin/env python3
"""Deep dive: What's happening in gen9championsvgc2026regma."""

import json
import logging
from pathlib import Path
from showdown_ai.battle_runner import BattleRunner, DecisionHandler, BattleState
from typing import Dict, Any

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Try with the exact teams from your earlier batl logs
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


class DetailedDebugHandler(DecisionHandler):
    """Detailed debug to understand format requirements."""

    def __init__(self, side: str):
        self.side = side
        self.request_count = 0

    def choose_action(self, request: Dict[str, Any], side: str, battle_state: BattleState) -> str:
        """Log everything about the request."""
        self.request_count += 1
        
        logger.info(f"\n{'='*70}")
        logger.info(f"REQUEST #{self.request_count} from {side}")
        logger.info('='*70)
        
        # Log entire structure (compact)
        request_summary = {
            "keys": list(request.keys()),
            "teamPreview": request.get("teamPreview"),
            "active_count": len(request.get("active", [])),
            "forceSwitch": request.get("forceSwitch"),
            "wait": request.get("wait"),
            "side_pokemon_count": len(request.get("side", {}).get("pokemon", [])),
        }
        logger.info(f"Structure: {json.dumps(request_summary, indent=2)}")
        
        # If active, show details
        if request.get("active"):
            logger.info("Active pokemon moves:")
            for i, slot in enumerate(request["active"]):
                moves = slot.get("moves", [])
                logger.info(f"  Slot {i}: {len(moves)} moves - {[m.get('move') for m in moves[:2]]}")
        
        # Log side/team info
        side_info = request.get("side", {})
        pokemon = side_info.get("pokemon", [])
        logger.info(f"Team info:")
        for i, poke in enumerate(pokemon[:3]):
            active = poke.get("active", False)
            cond = poke.get("condition", "?/?")
            logger.info(f"  {i+1}: {poke.get('details', 'Unknown')} - active={active}, hp={cond}")
        
        # Make a decision
        if request.get("teamPreview"):
            max_size = request.get("maxTeamSize", 4)
            team_size = len(pokemon)
            team_str = ",".join(str(i + 1) for i in range(min(max_size, team_size)))
            action = f"team {team_str}"
            logger.info(f"→ Action: {action}")
            return action
        
        elif request.get("wait"):
            logger.info("→ Action: pass (battle waiting)")
            return "pass"
        
        else:
            # Regular turn
            active = request.get("active", [])
            choices = []
            
            for slot_idx in range(len(active)):
                if slot_idx < len(active):
                    moves = active[slot_idx].get("moves", [])
                    legal = [m for m in moves if not m.get("disabled")]
                    if legal:
                        choice = f"move {moves.index(legal[0]) + 1}"
                    else:
                        choice = "pass"
                else:
                    choice = "pass"
                choices.append(choice)
            
            action = ", ".join(choices) if choices else "default"
            logger.info(f"→ Action: {action}")
            return action


def main():
    showdown_path = Path(__file__).parent.parent / "pokemon-showdown"
    
    if not showdown_path.exists():
        print(f"Pokemon Showdown not found at {showdown_path}")
        return
    
    logger.info("\n" + "="*70)
    logger.info("DEEP DIVE: gen9championsvgc2026regma")
    logger.info("="*70)
    
    runner = BattleRunner(
        showdown_path=showdown_path,
        format_id="gen9championsvgc2026regma",
    )
    
    p1_handler = DetailedDebugHandler("p1")
    p2_handler = DetailedDebugHandler("p2")
    
    result, _ = runner.run_battle(
        team_p1=TEAM_1,
        team_p2=TEAM_2,
        p1_handler=p1_handler,
        p2_handler=p2_handler,
        p1_name="Player1",
        p2_name="Player2",
        max_turns=50,
    )
    
    logger.info("\n" + "="*70)
    logger.info("RESULT")
    logger.info("="*70)
    logger.info(f"Winner: {result.winner}")
    logger.info(f"Turns: {result.turns}")
    logger.info(f"Total requests: P1={p1_handler.request_count}, P2={p2_handler.request_count}")
    logger.info("="*70)


if __name__ == "__main__":
    main()
