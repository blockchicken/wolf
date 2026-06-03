#!/usr/bin/env python3
"""Debug script: Run a single VGC battle with detailed logging."""

import json
import logging
from pathlib import Path
from showdown_ai.battle_runner import BattleRunner, DecisionHandler, BattleState
from typing import Dict, Any

logging.basicConfig(
    level=logging.DEBUG,
    format='%(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Real VGC 2026 Reg M-A teams
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


class VGCDebugHandler(DecisionHandler):
    """Debug handler specifically for VGC doubles."""

    def __init__(self, side: str):
        self.side = side
        self.turn_count = 0

    def choose_action(self, request: Dict[str, Any], side: str, battle_state: BattleState) -> str:
        """Make a decision and log it."""
        self.turn_count += 1
        
        logger.info(f"\n{'='*70}")
        logger.info(f"{side} REQUEST #{self.turn_count}")
        logger.info(f"{'='*70}")
        
        # Log request structure
        logger.info(f"Request keys: {list(request.keys())}")
        
        if request.get("wait"):
            logger.info("⏸️  Battle waiting")
            return "pass"
        
        if request.get("teamPreview"):
            logger.info("🎯 Team preview phase")
            # Log team info
            side_info = request.get("side", {})
            pokemon = side_info.get("pokemon", [])
            logger.info(f"   Team size: {len(pokemon)}")
            for i, poke in enumerate(pokemon[:3]):
                logger.info(f"     {i+1}: {poke.get('details', 'Unknown')}")
            
            max_size = request.get("maxTeamSize", 4)
            team_size = len(pokemon)
            team_str = ",".join(str(i + 1) for i in range(min(max_size, team_size)))
            action = f"team {team_str}"
            logger.info(f"✓ Action: {action}\n")
            return action
        
        # Normal turn
        active = request.get("active", [])
        force_switch = request.get("forceSwitch", [])
        side_info = request.get("side", {})
        
        logger.info(f"Active slots: {len(active)}")
        logger.info(f"Force switch: {force_switch}")
        
        if len(active) > 0:
            for i, slot in enumerate(active):
                moves = slot.get("moves", [])
                legal = [m for m in moves if not m.get("disabled")]
                logger.info(f"  Slot {i}: {len(legal)} legal moves: {[m.get('move') for m in legal[:2]]}")
        
        # Generate action
        choices = []
        
        if isinstance(force_switch, list) and len(force_switch) > 0:
            # Doubles with forced switches
            logger.info("💪 Forced switch scenario")
            for slot_idx, must_switch in enumerate(force_switch):
                logger.info(f"  Slot {slot_idx}: must_switch={must_switch}")
                
                if must_switch:
                    # Must switch - find available pokemon
                    pokemon = side_info.get("pokemon", [])
                    for i, poke in enumerate(pokemon):
                        if not poke.get("active") and poke.get("condition", "0/1") != "0/1":
                            choice = f"switch {i + 1}"
                            choices.append(choice)
                            logger.info(f"    → switch {i + 1}")
                            break
                    else:
                        choices.append("pass")
                        logger.info(f"    → pass (no valid switches)")
                else:
                    # Can use move or switch
                    if slot_idx < len(active):
                        moves = active[slot_idx].get("moves", [])
                        legal = [m for m in moves if not m.get("disabled")]
                        
                        if legal:
                            # Pick first legal move with target
                            move = legal[0]
                            move_idx = moves.index(move) + 1
                            
                            # For doubles, determine target
                            # Default: -1 for ally, +1 for opponent
                            target = ""
                            move_target = move.get("target", "normal")
                            
                            if move_target in ["adjacentAlly", "allySide"]:
                                target = " -1"
                            elif move_target in ["normal", "allAdjacent", "foeSide"]:
                                target = " 1"  # Default to opponent
                            
                            choice = f"move {move_idx}{target}"
                            choices.append(choice)
                            logger.info(f"    → move {move_idx} (target: {move_target})")
                        else:
                            choices.append("pass")
                            logger.info(f"    → pass (no legal moves)")
                    else:
                        choices.append("pass")
                        logger.info(f"    → pass (slot out of range)")
        else:
            # Regular doubles turn - all active pokemon can act
            logger.info("⚔️  Regular turn (both pokemon act)")
            for slot_idx in range(len(active)):
                moves = active[slot_idx].get("moves", [])
                legal = [m for m in moves if not m.get("disabled")]
                
                if legal:
                    move = legal[0]
                    move_idx = moves.index(move) + 1
                    
                    # Determine target for doubles
                    move_target = move.get("target", "normal")
                    target = ""
                    
                    if move_target in ["adjacentAlly", "allySide"]:
                        target = " -1"
                    elif move_target in ["normal", "allAdjacent", "foeSide"]:
                        target = " 1"
                    
                    choice = f"move {move_idx}{target}"
                    choices.append(choice)
                    logger.info(f"  Slot {slot_idx}: move {move_idx} (target: {move_target})")
                else:
                    choices.append("pass")
                    logger.info(f"  Slot {slot_idx}: pass (no legal moves)")
        
        action = ", ".join(choices)
        logger.info(f"✓ Final action: {action}\n")
        return action


def main():
    showdown_path = Path(__file__).parent.parent / "pokemon-showdown"
    
    if not showdown_path.exists():
        print(f"Pokemon Showdown not found at {showdown_path}")
        return
    
    logger.info("\n" + "="*70)
    logger.info("VGC 2026 Reg M-A Doubles Battle Debug")
    logger.info("="*70 + "\n")
    
    runner = BattleRunner(
        showdown_path=showdown_path,
        format_id="gen9championsvgc2026regma",
    )
    
    p1_handler = VGCDebugHandler("p1")
    p2_handler = VGCDebugHandler("p2")
    
    result, _ = runner.run_battle(
        team_p1=TEAM_1,
        team_p2=TEAM_2,
        p1_handler=p1_handler,
        p2_handler=p2_handler,
        p1_name="VGC_Player1",
        p2_name="VGC_Player2",
        max_turns=50,
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
    main()
