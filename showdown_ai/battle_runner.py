"""Headless Pokemon Showdown battle runner for AI vs AI battles.

This module spawns the Pokemon Showdown battle simulator in a subprocess
and manages the communication loop for AI decision-making.
"""

import subprocess
import json
import logging
from pathlib import Path
from typing import Optional, Callable, Dict, Any, Tuple
from dataclasses import dataclass
from abc import ABC, abstractmethod


logger = logging.getLogger(__name__)


@dataclass
class BattleResult:
    """Result of a completed battle."""
    winner: Optional[str]  # "p1", "p2", or None for tie
    loser: Optional[str]
    turns: int
    seed: Optional[list] = None
    p1_name: str = "p1"
    p2_name: str = "p2"
    battle_id: Optional[str] = None
    p1_team: Optional[list] = None
    p2_team: Optional[list] = None


class DecisionHandler(ABC):
    """Abstract base for decision-making strategy."""

    @abstractmethod
    def choose_action(
        self,
        request: Dict[str, Any],
        side: str,
        battle_state: "BattleState",
    ) -> str:
        """Make a decision given the request JSON.

        Args:
            request: The |request| JSON from simulator
            side: "p1" or "p2"
            battle_state: Current BattleState object

        Returns:
            Action string like "move 1", "switch 2", "move 1 -1, switch 2" (doubles)
        """
        pass


class RandomDecisionHandler(DecisionHandler):
    """Simple random decision handler for testing."""

    def __init__(self, seed: int = 42):
        import random
        self.rng = random.Random(seed)

    def choose_action(
        self,
        request: Dict[str, Any],
        side: str,
        battle_state: "BattleState",
    ) -> str:
        """Choose a random legal action."""
        if request.get("wait"):
            return "pass"

        if request.get("teamPreview"):
            # maxChosenTeamSize is used by Champions; maxTeamSize by older formats
            team_size = len(request.get("side", {}).get("pokemon", []))
            max_size = (
                request.get("maxChosenTeamSize")
                or request.get("maxTeamSize")
                or team_size
            )
            team_str = ",".join(str(i + 1) for i in range(min(max_size, team_size)))
            return f"team {team_str}"

        force_switch = request.get("forceSwitch", [])
        active = request.get("active", [])
        # Use format-level doubles flag: active count alone is unreliable when
        # one Pokémon has fainted (leaving len(active)==1 in a doubles battle).
        is_doubles = battle_state.is_doubles or len(active) > 1

        if isinstance(force_switch, list) and any(force_switch):
            # Force-switch request: only send choices for slots that must switch.
            # Slots with must_switch=False are still alive but don't get a choice
            # here — sending one would trigger "more choices than unfainted Pokémon".
            # Track already-chosen team slots so we don't switch in the same Pokémon twice.
            already_chosen: set = set()
            choices = []
            for slot_idx, must_switch in enumerate(force_switch):
                if must_switch:
                    choice = self._random_switch(request, slot_idx, already_chosen)
                    # Extract the slot number from "switch N" and record it
                    parts = choice.split()
                    if len(parts) == 2 and parts[0] == "switch":
                        already_chosen.add(int(parts[1]))
                    choices.append(choice)
            return ", ".join(choices) if choices else "pass"
        else:
            choices = []
            for slot_idx in range(len(active)):
                slot_data = active[slot_idx] if slot_idx < len(active) else None
                # Skip null/empty slots — Pokémon has fainted and not yet replaced.
                # An empty dict {} or missing "moves" key both indicate no active Pokémon.
                if not slot_data or not slot_data.get("moves"):
                    continue
                choice = self._random_move_or_default(request, slot_idx, is_doubles)
                choices.append(choice)
            return ", ".join(choices) if choices else "default"

    # Move target fields that require an explicit target in doubles.
    # Everything else (self, spread, side, field) is auto-resolved by Showdown.
    _NEEDS_TARGET = {"normal", "adjacentFoe", "adjacentAlly", "any"}

    def _random_move_or_default(
        self, request: Dict[str, Any], slot_idx: int, is_doubles: bool = False
    ) -> str:
        """Choose a random legal move, with target selection for doubles."""
        active = request.get("active", [])
        if slot_idx >= len(active):
            return "pass"

        moves = active[slot_idx].get("moves", [])
        legal_moves = [m for m in moves if not m.get("disabled")]

        if not legal_moves:
            return "pass"

        move = self.rng.choice(legal_moves)
        move_idx = legal_moves.index(move) + 1

        if not is_doubles:
            return f"move {move_idx}"

        # In doubles, single-target moves need an explicit target slot.
        # Only add a target when the move explicitly declares it needs one.
        # Defaulting to "normal" when the field is absent causes errors for
        # auto-targeting moves (randomNormal, allAdjacent, etc.).
        move_target = move.get("target")
        if move_target not in self._NEEDS_TARGET:
            return f"move {move_idx}"  # spread / self / side move, no target needed

        if move_target == "adjacentAlly":
            # Target the other active slot (ally), numbered relative to your side
            ally_slot = 2 if slot_idx == 0 else 1
            return f"move {move_idx} -{ally_slot}"
        else:
            # Target a random opponent slot (1 or 2)
            target_slot = self.rng.randint(1, 2)
            return f"move {move_idx} {target_slot}"

    def _random_switch(
        self,
        request: Dict[str, Any],
        slot_idx: int,
        exclude_slots: Optional[set] = None,
    ) -> str:
        """Choose a random available switch target.

        Args:
            exclude_slots: 1-based team slot numbers already chosen in this
                           round (prevents switching in the same Pokémon twice).
        """
        side = request.get("side", {})
        pokemon = side.get("pokemon", [])

        def _is_available(team_slot_1based: int, p: Dict[str, Any]) -> bool:
            if p.get("active"):
                return False
            if exclude_slots and team_slot_1based in exclude_slots:
                return False
            cond = p.get("condition", "")
            # Fainted Pokémon have condition "0 fnt"; alive ones are "HP/maxHP [status]"
            return not (cond == "0" or cond.startswith("0 "))

        available = [
            (i + 1, p)
            for i, p in enumerate(pokemon)
            if _is_available(i + 1, p)
        ]

        if available:
            slot, _ = self.rng.choice(available)
            return f"switch {slot}"
        return "pass"


@dataclass
class BattleState:
    """Tracks current battle state from event stream."""
    turn: int = 0
    p1_name: str = "p1"
    p2_name: str = "p2"
    is_doubles: bool = False  # True for VGC/doubles formats
    p1_team: Dict[str, Any] = None
    p2_team: Dict[str, Any] = None
    events: list = None

    def __post_init__(self):
        if self.p1_team is None:
            self.p1_team = {}
        if self.p2_team is None:
            self.p2_team = {}
        if self.events is None:
            self.events = []


class BattleRunner:
    """Manages a headless Pokemon Showdown battle between two AIs."""

    def __init__(
        self,
        showdown_path: str | Path,
        format_id: str = "gen9ou",
        seed: Optional[Tuple[int, int, int, int]] = None,
    ):
        """Initialize the battle runner.

        Args:
            showdown_path: Path to pokemon-showdown directory (parent of ./dist/)
            format_id: Format ID like "gen9ou", "gen9vgc2024regg", "gen9doubles"
            seed: Optional 4-tuple of ints for reproducible RNG
        """
        self.showdown_path = Path(showdown_path)
        self.format_id = format_id
        self.seed = seed
        # Detect doubles/VGC formats so handlers know to include move targets
        fid = format_id.lower()
        self.is_doubles = any(kw in fid for kw in ("doubles", "vgc", "triples"))

        # Verify showdown exists
        dist_sim = self.showdown_path / "dist" / "sim"
        if not dist_sim.exists():
            raise FileNotFoundError(
                f"Pokemon Showdown dist/sim not found at {dist_sim}. "
                f"Clone and build: https://github.com/smogon/pokemon-showdown"
            )

        self.process: Optional[subprocess.Popen] = None
        self.battle_state = BattleState(is_doubles=self.is_doubles)

    @staticmethod
    def _fix_action(error_line: str, last_action: str) -> str:
        """Attempt a targeted fix for a known Showdown error.

        Returns the corrected action, or the original action unchanged if no
        fix can be applied (which causes the handler to generate a fresh choice).
        """
        error_msg = error_line  # e.g. "|error|[Invalid choice] Can't move: ..."

        # "more choices than unfainted Pokémon" — we sent 2 choices but only 1
        # Pokémon is alive.  Drop all but the first choice.
        if "more choices than unfainted" in error_msg:
            choices = [c.strip() for c in last_action.split(",")]
            if len(choices) > 1:
                return choices[0]

        # "X needs a target" — a single-target move was sent without a slot.
        # Add " 1" (target opponent slot 1) to each un-targeted move choice.
        if "needs a target" in error_msg:
            fixed_parts = []
            for part in last_action.split(","):
                part = part.strip()
                tokens = part.split()
                if tokens and tokens[0] == "move" and len(tokens) == 2:
                    part = part + " 1"  # default to opponent slot 1
                fixed_parts.append(part)
            fixed = ", ".join(fixed_parts)
            if fixed != last_action:
                return fixed

        # "X is disabled" / "unavailable" — move can't be used (e.g. Fake Out after
        # turn 1).  Signal that the handler should re-roll a different move.
        if "is disabled" in error_msg or "Unavailable choice" in error_msg:
            return last_action  # unchanged → forces fresh handler call below

        return last_action  # unchanged → handler will generate a new action

    @staticmethod
    def _normalize_packed_team(team: str) -> str:
        """Convert newline-delimited packed teams to ]-delimited format.

        Showdown's Teams.unpack() uses ] as the Pokémon set separator.
        Newline-delimited teams (common in Python code) only load the first Pokémon.
        """
        team = team.strip()
        if ']' in team:
            return team  # already in ] format
        lines = [line.strip() for line in team.split('\n') if line.strip()]
        return ']'.join(lines)

    def run_battle(
        self,
        team_p1: str,  # packed team format
        team_p2: str,
        p1_handler: DecisionHandler,
        p2_handler: DecisionHandler,
        p1_name: str = "p1",
        p2_name: str = "p2",
        max_turns: int = 1000,
        collect_training_data: bool = False,
    ) -> Tuple[BattleResult, Optional[list]]:
        """Run a complete battle.

        Args:
            team_p1: Packed team format for player 1
            team_p2: Packed team format for player 2
            p1_handler: DecisionHandler for player 1
            p2_handler: DecisionHandler for player 2
            p1_name: Display name for player 1
            p2_name: Display name for player 2
            max_turns: Maximum turns before timeout
            collect_training_data: If True, return list of (request, action) pairs

        Returns:
            Tuple of (BattleResult, training_data_pairs or None)
        """
        self.battle_state = BattleState(p1_name=p1_name, p2_name=p2_name, is_doubles=self.is_doubles)
        training_data = [] if collect_training_data else None

        try:
            # Start subprocess
            # On Windows, the pokemon-showdown script needs to be run with node explicitly
            showdown_script = self.showdown_path / "pokemon-showdown"
            cmd = ["node", str(showdown_script), "simulate-battle"]
            
            self.process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            # Normalize teams: Showdown requires ] as the Pokémon set separator
            norm_p1 = self._normalize_packed_team(team_p1)
            norm_p2 = self._normalize_packed_team(team_p2)

            # Send battle start
            start_cmd = {
                "formatid": self.format_id,
            }
            if self.seed:
                start_cmd["seed"] = list(self.seed)

            self.process.stdin.write(f">start {json.dumps(start_cmd)}\n")
            self.process.stdin.write(
                f">player p1 {json.dumps({'name': p1_name, 'team': norm_p1})}\n"
            )
            self.process.stdin.write(
                f">player p2 {json.dumps({'name': p2_name, 'team': norm_p2})}\n"
            )
            self.process.stdin.flush()

            # Battle loop — respond to each player's request immediately.
            winner = None
            turns = 0
            handlers = {"p1": p1_handler, "p2": p2_handler}
            # Track the last request/action per side so we can retry after an error.
            # When Showdown rejects a choice it sends |error| and then WAITS for
            # a new response — it does NOT resend the request automatically.
            last_requests: Dict[str, Any] = {}
            last_actions: Dict[str, str] = {}

            while True:
                try:
                    line = self.process.stdout.readline()
                    if not line:
                        break

                    line = line.rstrip("\n\r")
                    if not line:
                        continue

                    if line.startswith("|turn|"):
                        try:
                            turns = int(line.split("|turn|")[1])
                        except (IndexError, ValueError):
                            pass

                    elif line.startswith("sideupdate"):
                        side_line = self.process.stdout.readline()
                        if not side_line:
                            break
                        side = side_line.strip()

                        request_line = self.process.stdout.readline()
                        if not request_line:
                            break
                        request_line = request_line.strip()

                        if request_line.startswith("|error|"):
                            # Showdown rejected our last choice and is waiting for a
                            # new valid response — it does NOT resend the request.
                            # Apply targeted fixes based on the error message, then retry
                            # with the cached last request for this side.
                            logger.warning(f"Retrying after error: {request_line}")
                            cached = last_requests.get(side)
                            last_action = last_actions.get(side, "pass")
                            if cached and side in handlers:
                                fixed = self._fix_action(request_line, last_action)
                                if fixed != last_action:
                                    action = fixed
                                else:
                                    action = handlers[side].choose_action(
                                        cached, side, self.battle_state
                                    )
                                last_actions[side] = action
                                self.process.stdin.write(f">{side} {action}\n")
                                self.process.stdin.flush()
                            continue

                        if request_line.startswith("|request|"):
                            request_json_str = request_line[9:]
                        else:
                            request_json_str = request_line

                        if not request_json_str:
                            logger.warning("Empty request JSON")
                            continue

                        try:
                            request = json.loads(request_json_str)
                        except json.JSONDecodeError as e:
                            logger.error(f"Failed to parse request JSON: {e}")
                            logger.error(f"  JSON was: {request_json_str[:200]}")
                            continue

                        if side not in handlers:
                            continue

                        # wait=true means this side has no decision to make
                        if request.get("wait"):
                            continue

                        # Cache so we can retry immediately if Showdown rejects the choice
                        last_requests[side] = request

                        if turns >= max_turns:
                            logger.warning(f"Battle exceeded {max_turns} turns, terminating")
                            return (
                                self._build_result(None, turns, p1_name, p2_name),
                                training_data,
                            )

                        action = handlers[side].choose_action(
                            request, side, self.battle_state
                        )

                        if collect_training_data:
                            training_data.append((side, request, action))

                        last_actions[side] = action
                        self.process.stdin.write(f">{side} {action}\n")
                        self.process.stdin.flush()

                    elif line.startswith("|win|"):
                        winner = line.split("|win|")[1].strip()
                        break

                    elif line.startswith("|tie|"):
                        winner = None
                        break

                except Exception as e:
                    logger.error(f"Error in battle loop: {e}", exc_info=True)
                    break

            # Determine result
            result = self._build_result(winner, turns, p1_name, p2_name)
            return result, training_data

        except Exception as e:
            logger.error(f"Battle error: {e}", exc_info=True)
            raise

        finally:
            if self.process:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()

    def _build_result(
        self,
        winner_name: Optional[str],
        turns: int,
        p1_name: str,
        p2_name: str,
    ) -> BattleResult:
        """Build a BattleResult from battle outcome."""
        if winner_name is None:
            winner = None
            loser = None
        elif winner_name == p1_name:
            winner = "p1"
            loser = "p2"
        elif winner_name == p2_name:
            winner = "p2"
            loser = "p1"
        else:
            # Could be a username we don't recognize
            winner = None
            loser = None
            logger.warning(f"Unknown winner name: {winner_name}")

        return BattleResult(
            winner=winner,
            loser=loser,
            turns=turns,
            p1_name=p1_name,
            p2_name=p2_name,
        )
