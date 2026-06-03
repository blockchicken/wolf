"""Training dataset creation from parsed battle logs - REWRITTEN for JSON format.

This module builds outcome-weighted training examples from showdown battle logs,
preparing data for imitation learning models.
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple, Any, Set
from pathlib import Path
import json
from enum import Enum

from .logs import BattleLog, ParsedEvent, load_showdown_log_json, split_perspective_logs
from .state import StateTracker, PerspectiveState


class ActionType(Enum):
    """Type of action a player can take."""
    MOVE = "move"
    SWITCH = "switch"


@dataclass
class Action:
    """A single decision: either a move or a switch."""
    action_type: ActionType
    target: str  # move name or pokemon name


@dataclass
class TrainingExample:
    """Single (state, available_actions, taken_action, outcome) training example."""
    
    # State representation (what the player can see)
    player_side: str  # "p1" or "p2"
    turn: int
    
    # Active pokemon state (what player can observe)
    my_active_pokemon: Optional[str]  # "pikachu" or similar
    my_active_hp_percent: float  # 0.0 to 1.0
    
    # Opponent state (what player can observe)
    opponent_active_pokemon: Optional[str]  # Known type/species or "Unknown"
    opponent_active_hp_percent: float
    
    # Pokemon on field
    my_team: List[Tuple[str, float]]  # [(name, hp%), ...]
    opponent_team: List[Tuple[str, float]]
    
    # Known moves (what we've seen them use)
    my_known_moves: List[str]
    opponent_known_moves: List[str]
    
    # Available actions at this decision point
    available_actions: List[Action]
    
    # Action actually taken
    taken_action: Action
    
    # Outcome (for weighting)
    outcome: str  # "win", "loss", "tie"
    outcome_weight: float  # 1.0 for win, 0.5 for loss, 0.25 for tie
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "player_side": self.player_side,
            "turn": self.turn,
            "my_active_pokemon": self.my_active_pokemon,
            "my_active_hp_percent": self.my_active_hp_percent,
            "opponent_active_pokemon": self.opponent_active_pokemon,
            "opponent_active_hp_percent": self.opponent_active_hp_percent,
            "my_team": self.my_team,
            "opponent_team": self.opponent_team,
            "my_known_moves": self.my_known_moves,
            "opponent_known_moves": self.opponent_known_moves,
            "available_actions": [
                {"action_type": a.action_type.value, "target": a.target}
                for a in self.available_actions
            ],
            "taken_action": {
                "action_type": self.taken_action.action_type.value,
                "target": self.taken_action.target,
            },
            "outcome": self.outcome,
            "outcome_weight": self.outcome_weight,
        }


class TrainingDatasetBuilder:
    """Build training examples from battle logs.
    
    Key insight: In the JSON format, actions are shown as |move| and |switch| events.
    We need to:
    1. Track state progression through events
    2. Identify each decision point (when a player makes an action)
    3. Build available actions from known pokemon and moves
    4. Record the action that was actually taken
    """
    
    def __init__(self):
        self.examples: List[TrainingExample] = []
    
    def parse_log(self, log: BattleLog) -> List[TrainingExample]:
        """Extract training examples from a single battle log.
        
        Args:
            log: Parsed battle log
            
        Returns:
            List of training examples for both players
        """
        examples = []
        
        # Determine winner for outcome weighting. Showdown's |win| uses the
        # player's display name, not the side string 'p1'/'p2'. Map the name
        # back to the corresponding side if possible.
        winner = None
        for event in log.events:
            if event.kind == "win" and event.args:
                winner_name = event.args[0]
                # If BattleLog has players, map name -> side
                try:
                    p1_name, p2_name = log.players
                except Exception:
                    p1_name = p2_name = None

                if p1_name and winner_name == p1_name:
                    winner = "p1"
                elif p2_name and winner_name == p2_name:
                    winner = "p2"
                elif winner_name in ("p1", "p2"):
                    # In some logs the side string might already be present.
                    winner = winner_name
                else:
                    winner = None
                break
        
        # Extract all moves and switches, organized by turn and player
        turns_data = self._extract_turns_data(log.events)
        
        # Process each player's perspective
        for player_side in ["p1", "p2"]:
            perspective_examples = self._extract_examples_for_player(
                side=player_side,
                log_events=log.events,
                turns_data=turns_data,
                winner=winner,
                battle_id=log.battle_id,
            )
            examples.extend(perspective_examples)
        
        self.examples.extend(examples)
        return examples
    
    def _extract_turns_data(self, events: Tuple[ParsedEvent, ...]) -> Dict[int, Dict]:
        """Extract structured data about each turn.
        
        Returns:
            {
                turn_number: {
                    "p1": {"move": move_name, "target": target} OR {"switch": pokemon},
                    "p2": {...},
                }
            }
        """
        turns = {}
        current_turn = 0
        
        for event in events:
            if event.kind == "turn" and event.args:
                current_turn = int(event.args[0])
                turns[current_turn] = {"p1": None, "p2": None}
            
            elif event.kind == "move" and len(event.args) >= 2:
                actor = event.args[0]  # "p1a: Pikachu" or "p2b: Dragonite"
                move_name = event.args[1]
                
                side = actor[:2]  # "p1" or "p2"
                if current_turn in turns and side in ["p1", "p2"]:
                    if turns[current_turn][side] is None:
                        turns[current_turn][side] = {"action_type": "move", "target": move_name}
            
            elif event.kind == "switch" and len(event.args) >= 2:
                actor = event.args[0]  # "p1a: Pikachu"
                pokemon_spec = event.args[1]  # "Pikachu, L50" or similar
                pokemon_name = pokemon_spec.split(',')[0] if ',' in pokemon_spec else pokemon_spec
                
                side = actor[:2]  # "p1" or "p2"
                if current_turn in turns and side in ["p1", "p2"]:
                    if turns[current_turn][side] is None:
                        turns[current_turn][side] = {"action_type": "switch", "target": pokemon_name}
        
        return turns
    
    def _extract_examples_for_player(
        self,
        side: str,
        log_events: Tuple[ParsedEvent, ...],
        turns_data: Dict[int, Dict],
        winner: Optional[str],
        battle_id: str,
    ) -> List[TrainingExample]:
        """Extract examples for one player's perspective."""
        examples = []
        
        # Determine outcome for this player
        if winner is None:
            outcome = "tie"
            outcome_weight = 0.25
        elif winner == side:
            outcome = "win"
            outcome_weight = 1.0
        else:
            outcome = "loss"
            outcome_weight = 0.5
        
        # Track state as we go through events
        team_pokemon: List[str] = []  # All pokemon on this player's team
        opponent_team_pokemon: List[str] = []
        
        active_pokemon = {
            side: None,
            "opponent": None,
        }
        active_hp = {
            side: 1.0,
            "opponent": 1.0,
        }
        
        known_moves: Dict[str, Set[str]] = {}  # {pokemon_name: {moves}}
        opponent_known_moves: Dict[str, Set[str]] = {}
        fainted: Set[str] = set()  # Fainted pokemon
        opponent_fainted: Set[str] = set()
        
        current_turn = 0
        
        # First pass: collect all team pokemon
        for event in log_events:
            if event.kind == "poke" and len(event.args) >= 2:
                poke_side = event.args[0]
                pokemon_spec = event.args[1]
                pokemon_name = pokemon_spec.split(',')[0]
                
                if poke_side == side:
                    team_pokemon.append(pokemon_name)
                else:
                    opponent_team_pokemon.append(pokemon_name)
        
        # Second pass: extract examples for each action taken
        for i, event in enumerate(log_events):
            # Track turn number
            if event.kind == "turn" and event.args:
                current_turn = int(event.args[0])
            
            # Update active pokemon on switch
            elif event.kind == "switch" and len(event.args) >= 2:
                actor = event.args[0]
                actor_side = actor[:2]
                pokemon_spec = event.args[1]
                pokemon_name = pokemon_spec.split(',')[0]
                hp_str = event.args[2] if len(event.args) > 2 else "100/100"
                hp_frac = self._parse_hp(hp_str)
                
                if actor_side == side:
                    active_pokemon[side] = pokemon_name
                    active_hp[side] = hp_frac
                else:
                    active_pokemon["opponent"] = pokemon_name
                    active_hp["opponent"] = hp_frac
            
            # Update HP on damage
            elif event.kind == "-damage" and len(event.args) >= 2:
                actor = event.args[0]
                actor_side = actor[:2]
                hp_str = event.args[1] if len(event.args) > 1 else "?/?"
                hp_frac = self._parse_hp(hp_str)
                
                if actor_side == side:
                    active_hp[side] = hp_frac
                else:
                    active_hp["opponent"] = hp_frac
            
            # Track fainted pokemon
            elif event.kind == "faint" and len(event.args) >= 1:
                actor = event.args[0]
                actor_side = actor[:2]
                pokemon_name = actor.split(': ')[1] if ': ' in actor else "Unknown"
                
                if actor_side == side:
                    fainted.add(pokemon_name)
                else:
                    opponent_fainted.add(pokemon_name)
            
            # Track known moves and create examples
            elif event.kind == "move" and len(event.args) >= 2:
                actor = event.args[0]
                actor_side = actor[:2]
                move_name = event.args[1]
                pokemon_name = actor.split(': ')[1] if ': ' in actor else "Unknown"
                
                # Track moves
                if actor_side == side:
                    if pokemon_name not in known_moves:
                        known_moves[pokemon_name] = set()
                    known_moves[pokemon_name].add(move_name)
                else:
                    if pokemon_name not in opponent_known_moves:
                        opponent_known_moves[pokemon_name] = set()
                    opponent_known_moves[pokemon_name].add(move_name)
                    continue  # Don't create examples for opponent moves
                
                # Create example when our pokemon uses a move
                if pokemon_name == active_pokemon[side]:
                    available = self._build_available_actions(
                        active_pokemon[side],
                        team_pokemon,
                        fainted,
                        known_moves.get(active_pokemon[side], set()),
                    )
                    
                    taken_action = Action(ActionType.MOVE, move_name)
                    
                    example = TrainingExample(
                        player_side=side,
                        turn=current_turn,
                        my_active_pokemon=active_pokemon[side],
                        my_active_hp_percent=active_hp[side],
                        opponent_active_pokemon=active_pokemon["opponent"],
                        opponent_active_hp_percent=active_hp["opponent"],
                        my_team=[(p, 1.0) for p in team_pokemon],
                        opponent_team=[(p, 1.0) for p in opponent_team_pokemon],
                        my_known_moves=list(known_moves.get(active_pokemon[side], set())),
                        opponent_known_moves=list(opponent_known_moves.get(active_pokemon["opponent"] or "", set())),
                        available_actions=available,
                        taken_action=taken_action,
                        outcome=outcome,
                        outcome_weight=outcome_weight,
                    )
                    examples.append(example)
        
        return examples
    
    def _build_available_actions(
        self,
        active_pokemon: Optional[str],
        team_pokemon: List[str],
        fainted_pokemon: Set[str],
        pokemon_moves: Set[str],
    ) -> List[Action]:
        """Build available actions based on known state.
        
        Args:
            active_pokemon: Currently active pokemon
            team_pokemon: All team pokemon
            fainted_pokemon: Fainted pokemon
            pokemon_moves: Known moves for active pokemon
            
        Returns:
            List of available actions (moves + switches)
        """
        actions = []
        
        # Add known moves for active pokemon
        for move in pokemon_moves:
            actions.append(Action(ActionType.MOVE, move))
        
        # Add available switches (non-fainted, not currently active)
        for pokemon in team_pokemon:
            if pokemon != active_pokemon and pokemon not in fainted_pokemon:
                actions.append(Action(ActionType.SWITCH, pokemon))
        
        # If no actions available (shouldn't happen), add a default
        if not actions and active_pokemon:
            actions.append(Action(ActionType.MOVE, "Unknown"))
        
        return actions
    
    def _build_state_snapshot(
        self,
        side: str,
        events: Tuple[ParsedEvent, ...],
        team_info: Dict[str, Tuple[str, float]],
        opponent_team_info: Dict[str, Tuple[str, float]],
        known_moves: Dict[str, Set[str]],
        opponent_known_moves: Dict[str, Set[str]],
        current_turn: int,
    ) -> Dict[str, Any]:
        """Build a state snapshot from events up to a point."""
        # Track active pokemon and HP
        active_pokemon = {
            side: None,
            "opponent": None,
        }
        active_hp = {
            side: 1.0,
            "opponent": 1.0,
        }
        
        # Trace through events to find current state
        for event in events:
            if event.kind == "switch" and len(event.args) >= 2:
                actor_side = event.args[0][:2]
                pokemon_spec = event.args[1]
                pokemon_name = pokemon_spec.split(',')[0]
                hp_str = event.args[2] if len(event.args) > 2 else "100/100"
                hp_frac = self._parse_hp(hp_str)
                
                if actor_side == side:
                    active_pokemon[side] = pokemon_name
                    active_hp[side] = hp_frac
                else:
                    active_pokemon["opponent"] = pokemon_name
                    active_hp["opponent"] = hp_frac
            
            elif event.kind == "-damage" and len(event.args) >= 2:
                actor = event.args[0]
                actor_side = actor[:2]
                hp_str = event.args[2] if len(event.args) > 2 else "?/?"
                hp_frac = self._parse_hp(hp_str)
                
                if actor_side == side:
                    active_hp[side] = hp_frac
                else:
                    active_hp["opponent"] = hp_frac
        
        return {
            "active": active_pokemon[side],
            "active_hp": active_hp[side],
            "opponent_active": active_pokemon["opponent"],
            "opponent_active_hp": active_hp["opponent"],
            "team": list(team_info.values()),
            "opponent_team": list(opponent_team_info.values()),
            "known_moves": {k: list(v) for k, v in known_moves.items()},
            "opponent_known_moves": {k: list(v) for k, v in opponent_known_moves.items()},
        }
    
    def _parse_hp(self, hp_str: str) -> float:
        """Parse HP string like '50/100', '50%', or '?' to fraction 0-1."""
        if not hp_str or hp_str == "?" or hp_str == "?/?":
            return 0.5  # Unknown, assume 50%
        
        if hp_str.endswith("%"):
            try:
                return float(hp_str[:-1]) / 100.0
            except:
                return 0.5
        
        if "/" in hp_str:
            try:
                parts = hp_str.split("/")
                current = float(parts[0])
                maximum = float(parts[1])
                return current / maximum if maximum > 0 else 0.5
            except:
                return 0.5
        
        return 0.5


def create_dataset_from_logs(log_dir: Path | str) -> List[TrainingExample]:
    """Process all logs in a directory and create a training dataset.
    
    Args:
        log_dir: Directory containing battle JSON files
        
    Returns:
        List of all training examples from all battles
    """
    log_dir = Path(log_dir)
    builder = TrainingDatasetBuilder()
    
    for log_file in log_dir.glob("*.json"):
        try:
            log = load_showdown_log_json(log_file)
            examples = builder.parse_log(log)
        except Exception as e:
            print(f"Error processing {log_file.name}: {e}")
    
    return builder.examples
