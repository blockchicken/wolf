#!/usr/bin/env python3
"""Debug script: Run a single battle with detailed logging."""

import json
import logging
from pathlib import Path
from showdown_ai.battle_runner import BattleRunner, DecisionHandler, BattleState
from typing import Dict, Any

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Simple teams for debugging
SIMPLE_TEAM_1 = """Pikachu||leftovers|static|thunderbolt,quickattack,irontail,thunderwave|Timid|252,,,4,,252|M|||||
Charizard||leftovers|blaze|flamethrower,aircutter,dragonclaw,roost|Modest|4,,,252,,252|M|||||"""

SIMPLE_TEAM_2 = """Dragonite||leftovers|multiscale|outrage,earthquake,extremespeed,dragondance|Adamant|4,252,,,,252|M|||||
Alakazam||leftovers|magicguard|psychic,focusblast,shadowball,dazzlinggleam|Timid|4,,,252,,252|M|||||"""


class DebugDecisionHandler(DecisionHandler):
    """Logs all decisions for debugging."""

    def __init__(self, side: str):
        self.side = side
        self.turn_count = 0

    def choose_action(
        self,
        request: Dict[str, Any],
        side: str,
        battle_state: BattleState,
    ) -> str:
        """Make a decision and log it."""
        self.turn_count += 1
        
        logger.info(f"\n{'='*70}")
        logger.info(f"{side} REQUEST #{self.turn_count}")
        logger.info(f"{'='*70}")
        
        # Log full request for debugging (truncated)
        request_str = json.dumps(request, indent=2)[:300]
        logger.info(f"Request (first 300 chars):\n{request_str}...")
        
        # Log request structure
        logger.info(f"Request keys: {list(request.keys())}")
        
        if request.get("wait"):
            logger.info("Battle waiting... (tie/end condition)")
            return "pass"
        
        if request.get("teamPreview"):
            logger.info("Team preview phase")
            max_size = request.get("maxTeamSize", 4)
            team_size = len(request.get("side", {}).get("pokemon", []))
            team_str = ",".join(str(i + 1) for i in range(min(max_size, team_size)))
            action = f"team {team_str}"
            logger.info(f"Action: {action}\n")
            return action
        
        # Normal turn - pick first legal move
        try:
            active = request.get("active", [])
            side_info = request.get("side", {})
            team = side_info.get("pokemon", [])
            
            logger.info(f"Active slots: {len(active)}")
            logger.info(f"Team size: {len(team)}")
            
            if active and len(active) > 0:
                slot_0 = active[0]
                moves = slot_0.get("moves", [])
                legal_moves = [m for m in moves if not m.get("disabled", False)]
                
                logger.info(f"Legal moves: {[m.get('move') for m in legal_moves]}")
                
                if legal_moves:
                    move_idx = moves.index(legal_moves[0]) + 1
                    action = f"move {move_idx}"
                    logger.info(f"Action: {action}\n")
                    return action
            
            logger.info(f"No legal moves, trying pass\n")
            return "pass"
            
        except Exception as e:
            logger.error(f"Error in choose_action: {e}", exc_info=True)
            return "pass"


def run_debug_battle():
    """Run a single debug battle."""
    showdown_path = Path(__file__).parent.parent / "pokemon-showdown"
    
    if not showdown_path.exists():
        print(f"Pokemon Showdown not found at {showdown_path}")
        return
    
    logger.info(f"Starting debug battle from {showdown_path}")
    logger.info(f"Team 1 (first 200 chars): {SIMPLE_TEAM_1[:200]}")
    logger.info(f"Team 2 (first 200 chars): {SIMPLE_TEAM_2[:200]}")
    
    runner = BattleRunner(
        showdown_path=showdown_path,
        format_id="gen9ou",  # Singles for simpler debugging
        seed=(1234, 5678, 9012, 3456)
    )
    
    p1_handler = DebugDecisionHandler("p1")
    p2_handler = DebugDecisionHandler("p2")
    
    logger.info("\n" + "="*70)
    logger.info("STARTING BATTLE")
    logger.info("="*70 + "\n")
    
    result, training_data = runner.run_battle(
        team_p1=SIMPLE_TEAM_1,
        team_p2=SIMPLE_TEAM_2,
        p1_handler=p1_handler,
        p2_handler=p2_handler,
        p1_name="DebugPlayer1",
        p2_name="DebugPlayer2",
        max_turns=100,
        collect_training_data=False,
    )
    
    logger.info("\n" + "="*70)
    logger.info("BATTLE RESULT")
    logger.info("="*70)
    logger.info(f"Winner: {result.winner}")
    logger.info(f"Loser: {result.loser}")
    logger.info(f"Turns: {result.turns}")
    logger.info(f"Total requests: P1={p1_handler.turn_count}, P2={p2_handler.turn_count}")
    logger.info("="*70 + "\n")


if __name__ == "__main__":
    run_debug_battle()
