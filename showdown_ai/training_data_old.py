"""Training dataset creation from parsed battle logs.

This module builds outcome-weighted training examples from showdown battle logs,
preparing data for imitation learning models.
"""

from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Tuple, Any
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
    """Build training examples from battle logs."""
    
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
        
        # Determine winner for outcome weighting
        winner = None
        for event in log.events:
            if event.kind == "win" and event.args:
                winner = event.args[0]
                break
        
        # Split events into per-player perspective views
        perspective_views = split_perspective_logs(log.events)
        
        # Process each player's perspective
        for player_idx, player_side in enumerate(["p1", "p2"]):
            perspective_examples = self._extract_examples_for_player(
                side=player_side,
                events=perspective_views[player_side],
                winner=winner,
                battle_id=log.battle_id,
            )
            examples.extend(perspective_examples)
        
        self.examples.extend(examples)
        return examples
    
    def _extract_examples_for_player(
        self,
        side: str,
        events: List[ParsedEvent],
        winner: Optional[str],
        battle_id: str,
    ) -> List[TrainingExample]:
        """Extract examples for one player's perspective."""
        examples = []
        tracker = StateTracker(side)
        
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
        
        # Process events
        current_decision_state: Optional[PerspectiveState] = None
        available_actions: List[Action] = []
        
        for event in events:
            tracker.consume(event)
            
            # When we see a "request" event, prepare to capture the decision
            if event.kind == "request" and event.args:
                owner = event.args[0] if event.args else ""
                if owner.startswith(side):
                    # Parse request to get available actions
                    if len(event.args) > 1:
                        try:
                            request_data = json.loads(event.args[1])
                            current_decision_state = tracker.state
                            available_actions = self._parse_available_actions(request_data, side)
                        except (json.JSONDecodeError, IndexError):
                            pass
            
            # When we see a "choice" event, that's the action that was taken
            elif event.kind == "choice" and event.args:
                owner = event.args[0] if event.args else ""
                if owner.startswith(side) and current_decision_state and available_actions:
                    # Parse the choice
                    taken_action = self._parse_choice(event.args[1] if len(event.args) > 1 else "", side)
                    
                    if taken_action:
                        example = self._build_example(
                            current_decision_state,
                            side,
                            available_actions,
                            taken_action,
                            outcome,
                            outcome_weight,
                        )
                        examples.append(example)
                    
                    current_decision_state = None
                    available_actions = []
            
            # When we see a "move" event, record it for next request
            elif event.kind == "move" and len(event.args) >= 2:
                actor = event.args[0]
                move_name = event.args[1]
                # Moves are already tracked in state.known_moves
        
        return examples
    
    def _parse_available_actions(self, request_data: Dict, side: str) -> List[Action]:
        """Parse available actions from a request event."""
        actions = []
        
        # Moves
        if "moves" in request_data:
            for move in request_data["moves"]:
                if isinstance(move, dict) and "move" in move:
                    actions.append(Action(ActionType.MOVE, move["move"]))
                elif isinstance(move, str):
                    actions.append(Action(ActionType.MOVE, move))
        
        # Switches
        if "switches" in request_data:
            for switch in request_data["switches"]:
                if isinstance(switch, dict) and "name" in switch:
                    actions.append(Action(ActionType.SWITCH, switch["name"]))
                elif isinstance(switch, str):
                    actions.append(Action(ActionType.SWITCH, switch))
        
        return actions
    
    def _parse_choice(self, choice_str: str, side: str) -> Optional[Action]:
        """Parse the action choice (e.g., 'move 0', 'switch pikachu')."""
        if not choice_str:
            return None
        
        parts = choice_str.strip().split()
        if not parts:
            return None
        
        action_type = parts[0]
        
        if action_type == "move" and len(parts) > 1:
            # Move choice: "move 0 terastallize" or "move 0"
            move_index = int(parts[1]) if parts[1].isdigit() else 0
            # We'd need the actual move name - for now store index
            return Action(ActionType.MOVE, f"move_{move_index}")
        
        elif action_type == "switch" and len(parts) > 1:
            pokemon = " ".join(parts[1:])
            return Action(ActionType.SWITCH, pokemon)
        
        return None
    
    def _build_example(
        self,
        state: PerspectiveState,
        player_side: str,
        available_actions: List[Action],
        taken_action: Action,
        outcome: str,
        outcome_weight: float,
    ) -> TrainingExample:
        """Build a TrainingExample from parsed state and actions."""
        
        # Extract observable state for this player
        # This is what the player can see on their screen
        
        # Get opponent side
        opponent_side = "p2" if player_side == "p1" else "p1"
        
        # Parse active pokemon and HP
        my_active = None
        my_active_hp = 1.0
        for slot, details in state.active.items():
            if player_side in slot:
                my_active = details.split(",")[0] if "," in details else details
                if slot in state.hp:
                    hp_str = state.hp[slot]
                    my_active_hp = self._parse_hp_percent(hp_str)
                break
        
        # Opponent active is not directly known, but we can infer from faint/switch events
        opponent_active = None
        opponent_active_hp = 1.0
        for slot, details in state.active.items():
            if opponent_side in slot:
                opponent_active = details.split(",")[0] if "," in details else "Unknown"
                if slot in state.hp:
                    hp_str = state.hp[slot]
                    opponent_active_hp = self._parse_hp_percent(hp_str)
                break
        
        # Build team lists
        my_team = [(poke, 1.0) for poke in state.fainted]  # Fainted = 0 HP
        opponent_team = [(poke, 1.0) for poke in state.fainted]
        
        # Known moves
        my_known = list(state.known_moves.get(f"{player_side}a", set()))
        opponent_known = list(state.known_moves.get(f"{opponent_side}a", set()))
        
        return TrainingExample(
            player_side=player_side,
            turn=state.turn,
            my_active_pokemon=my_active,
            my_active_hp_percent=my_active_hp,
            opponent_active_pokemon=opponent_active,
            opponent_active_hp_percent=opponent_active_hp,
            my_team=my_team,
            opponent_team=opponent_team,
            my_known_moves=my_known,
            opponent_known_moves=opponent_known,
            available_actions=available_actions,
            taken_action=taken_action,
            outcome=outcome,
            outcome_weight=outcome_weight,
        )
    
    def _parse_hp_percent(self, hp_str: str) -> float:
        """Parse HP string like '100/100' or '50%' to float 0-1."""
        if not hp_str or hp_str == "?":
            return 1.0
        
        if "%" in hp_str:
            try:
                return float(hp_str.replace("%", "")) / 100.0
            except ValueError:
                return 1.0
        
        if "/" in hp_str:
            try:
                current, max_hp = hp_str.split("/")
                return float(current) / float(max_hp)
            except (ValueError, ZeroDivisionError):
                return 1.0
        
        return 1.0
    
    def save_dataset(self, path: Path) -> None:
        """Save all examples to a JSON file."""
        data = [ex.to_dict() for ex in self.examples]
        path.write_text(json.dumps(data, indent=2))
    
    def load_dataset(self, path: Path) -> List[TrainingExample]:
        """Load examples from a JSON file."""
        data = json.loads(path.read_text())
        examples = []
        for d in data:
            d["action_type"] = ActionType(d["action_type"])
            ex = TrainingExample(**d)
            examples.append(ex)
        self.examples = examples
        return examples


def create_dataset_from_logs(log_dir: Path) -> TrainingDatasetBuilder:
    """Convenience function to load all logs from a directory and build dataset."""
    builder = TrainingDatasetBuilder()
    
    for log_file in log_dir.glob("*.json"):
        try:
            log = load_showdown_log_json(log_file)
            examples = builder.parse_log(log)
            print(f"✓ {log_file.name}: {len(examples)} examples")
        except Exception as e:
            print(f"✗ {log_file.name}: {e}")
    
    return builder
