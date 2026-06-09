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

    def consume_event(self, line: str) -> None:
        """Optional hook: receives every raw simulator event line.

        Override in subclasses that maintain their own state tracker
        (e.g. ModelDecisionHandler).  The default is a no-op.
        """

    def handle_error(self, error_line: str, last_request: Dict[str, Any], last_action: str) -> None:
        """Called when Showdown rejects our last action before the retry.

        Handlers that track banned switch slots (Champions 'once per battle'
        switch rule) should override this to update their internal state.
        """

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
        self._mega_used = False         # True once our side has mega-evolved
        self._side: Optional[str] = None
        self._banned_switch_slots: set = set()  # 1-based team slots Showdown rejected

    def consume_event(self, line: str) -> None:
        # |-mega|p1a: Charizard|Charizardite Y  → our side mega'd; mark it used
        if "|-mega|" in line and self._side and f"|{self._side}" in line:
            self._mega_used = True

    def handle_error(self, error_line: str, last_request: Dict[str, Any], last_action: str) -> None:
        # "The Pokémon in slot 4 can only switch in once" — Champions format rule.
        # Parse the slot number and ban it so we don't retry the same switch.
        if "can only switch in once" in error_line:
            import re
            m = re.search(r"slot (\d+)", error_line)
            if m:
                self._banned_switch_slots.add(int(m.group(1)))

    def choose_action(
        self,
        request: Dict[str, Any],
        side: str,
        battle_state: "BattleState",
    ) -> str:
        """Choose a random legal action."""
        self._side = side

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
            mega_granted = False       # at most one mega per action command
            chosen_switches: set = set()  # prevent duplicate switch targets

            # active[i] corresponds to the i-th active: true Pokemon in side.pokemon.
            # A Pokemon can be active: true but fainted (condition "0 fnt") when it
            # died mid-turn and hasn't been replaced yet.  Sending choices for that
            # slot causes "more choices than unfainted Pokémon" errors.
            side_pokemon = request.get("side", {}).get("pokemon", [])
            active_pokemon = [p for p in side_pokemon if p.get("active")]

            for slot_idx in range(len(active)):
                # Skip the slot if the corresponding Pokemon has fainted.
                if slot_idx < len(active_pokemon):
                    cond = active_pokemon[slot_idx].get("condition", "")
                    if cond == "0" or cond.startswith("0 "):
                        continue

                slot_data = active[slot_idx] if slot_idx < len(active) else None
                # Skip null/empty slots — Pokémon has fainted and not yet replaced.
                if not slot_data or not slot_data.get("moves"):
                    continue
                choice = self._random_move_or_default(request, slot_idx, is_doubles, mega_granted, chosen_switches)
                if " mega" in choice:
                    mega_granted = True
                parts = choice.split()
                if parts[0] == "switch" and len(parts) >= 2:
                    try:
                        chosen_switches.add(int(parts[1]))
                    except ValueError:
                        pass
                choices.append(choice)
            return ", ".join(choices) if choices else "default"

    # Move target fields that require an explicit target in doubles.
    # Everything else (self, spread, side, field) is auto-resolved by Showdown.
    _NEEDS_TARGET = {"normal", "adjacentFoe", "adjacentAlly", "any"}

    def _random_move_or_default(
        self,
        request: Dict[str, Any],
        slot_idx: int,
        is_doubles: bool = False,
        mega_already_granted: bool = False,
        already_chosen_switches: Optional[set] = None,
    ) -> str:
        """Choose a random legal move, with target selection for doubles.

        Appends 'mega' on the first eligible slot when canMegaEvo is set,
        subject to one-mega-per-command and one-mega-per-battle constraints.
        """
        active = request.get("active", [])
        if slot_idx >= len(active):
            return "pass"

        slot_data = active[slot_idx]
        moves = slot_data.get("moves", [])
        legal_moves = [m for m in moves if not m.get("disabled")]

        if not legal_moves:
            # All moves appear disabled in the stale request (e.g. Encore changed
            # the legal set after the request was sent).  Probe a random move so
            # successive retries don't always collide on the same rejected choice.
            all_moves = slot_data.get("moves", [])
            if not all_moves:
                return "pass"
            legal_moves = all_moves  # try any; Showdown will correct us

        move = self.rng.choice(legal_moves)
        # Use position in the FULL moves list (1-based), not position in the filtered
        # legal_moves subset.  The two differ when some early moves are disabled
        # (e.g. Protect is move 4 but index 0 in legal_moves, so we'd send "move 1").
        move_idx = moves.index(move) + 1
        can_mega = (
            not self._mega_used
            and not mega_already_granted
            and bool(slot_data.get("canMegaEvo"))
        )
        mega_suffix = " mega" if can_mega else ""

        if not is_doubles:
            return f"move {move_idx}{mega_suffix}"

        # In doubles, single-target moves need an explicit target slot.
        # Only add a target when the move explicitly declares it needs one.
        # Defaulting to "normal" when the field is absent causes errors for
        # auto-targeting moves (randomNormal, allAdjacent, etc.).
        move_target = move.get("target")
        if move_target not in self._NEEDS_TARGET:
            return f"move {move_idx}{mega_suffix}"  # spread / self / side move, no target needed

        if move_target == "adjacentAlly":
            # Target the other active slot (ally), numbered relative to your side
            ally_slot = 2 if slot_idx == 0 else 1
            return f"move {move_idx} -{ally_slot}{mega_suffix}"
        else:
            # Target a random opponent slot (1 or 2)
            target_slot = self.rng.randint(1, 2)
            return f"move {move_idx} {target_slot}{mega_suffix}"

    def _random_switch(
        self,
        request: Dict[str, Any],
        slot_idx: int,
        exclude_slots: Optional[set] = None,
        already_chosen_switches: Optional[set] = None,
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
            if team_slot_1based in self._banned_switch_slots:
                return False
            if already_chosen_switches and team_slot_1based in already_chosen_switches:
                return False
            cond = p.get("condition", "")
            # Fainted Pokémon have condition "0 fnt"; alive ones are "HP/maxHP [status]"
            return not (cond == "0" or cond.startswith("0 "))

        available = [
            (i + 1, p)
            for i, p in enumerate(pokemon)
            if _is_available(i + 1, p)
        ]

        if not available:
            # Last resort: ignore once-per-battle ban if it's the only option,
            # to avoid an infinite "pass" loop when Showdown requires a switch.
            available = [
                (i + 1, p)
                for i, p in enumerate(pokemon)
                if not p.get("active")
                and not (exclude_slots and (i + 1) in exclude_slots)
                and not (p.get("condition", "") == "0" or p.get("condition", "").startswith("0 "))
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
    def _apply_error_to_request(error_line: str, request: Dict[str, Any]) -> None:
        """Mutate the cached request based on the error so retries pick differently.

        Called before _fix_action and choose_action so the handler sees an
        updated view of what's actually legal.
        """
        import re
        # "X's Move is disabled" — e.g. "Basculegion's Aqua Jet is disabled".
        # Mark that move as disabled so choose_action skips it on the next retry.
        m = re.search(r"'s (.+?) is disabled", error_line, re.IGNORECASE)
        if m and request:
            disabled_name = m.group(1).strip().lower()
            for active_slot in request.get("active", []):
                for move in active_slot.get("moves", []):
                    if move.get("move", "").lower() == disabled_name:
                        move["disabled"] = True

                # If ALL moves in this slot are now disabled, the stale request
                # no longer reflects the true game state (e.g. Encore changed which
                # move is legal).  Re-enable all moves except the rejected one so
                # the handler can probe other options rather than looping on "move 1".
                slot_moves = active_slot.get("moves", [])
                if slot_moves and all(mv.get("disabled") for mv in slot_moves):
                    for mv in slot_moves:
                        if mv.get("move", "").lower() != disabled_name:
                            mv["disabled"] = False

    @staticmethod
    def _fix_action(error_line: str, last_action: str) -> str:
        """Attempt a targeted fix for a known Showdown error.

        Returns the corrected action, or the original action unchanged if no
        fix can be applied (which causes the handler to generate a fresh choice).
        """
        error_msg = error_line  # e.g. "|error|[Invalid choice] Can't move: ..."

        # "more choices than unfainted Pokémon" — we sent 2 choices but only 1
        # Pokémon is alive.  Drop all but the last non-empty choice; we use the
        # last rather than the first because the first slot may be the fainted one.
        if "more choices than unfainted" in error_msg:
            choices = [c.strip() for c in last_action.split(",") if c.strip()]
            if len(choices) > 1:
                return choices[-1]

        # "can't choose a target for X" — a self-targeting move (e.g. Protect) was
        # given an explicit slot target.  Strip targets from all move choices.
        if "can't choose a target for" in error_msg.lower():
            fixed_parts = []
            changed = False
            for part in last_action.split(","):
                part = part.strip()
                tokens = part.split()
                # "move N T" where T is a numeric slot — drop the slot
                if tokens and tokens[0] == "move" and len(tokens) == 3:
                    try:
                        int(tokens[2].lstrip("-"))
                        part = f"move {tokens[1]}"
                        changed = True
                    except ValueError:
                        pass
                fixed_parts.append(part)
            if changed:
                return ", ".join(fixed_parts)

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
        log_file: Optional[str] = None,
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
            log_file: Optional path to write a full battle transcript (raw protocol +
                      decisions). Useful for debugging. Pass a filename like
                      "battle_001.log" or an absolute path.

        Returns:
            Tuple of (BattleResult, training_data_pairs or None)
        """
        self.battle_state = BattleState(p1_name=p1_name, p2_name=p2_name, is_doubles=self.is_doubles)
        training_data = [] if collect_training_data else None

        log_fh = open(log_file, "w", encoding="utf-8") if log_file else None

        def _log(line: str) -> None:
            if log_fh:
                log_fh.write(line + "\n")
                log_fh.flush()

        try:
            # Start subprocess
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
            start_cmd = {"formatid": self.format_id}
            if self.seed:
                start_cmd["seed"] = list(self.seed)

            _log(f"=== BATTLE START: {p1_name} vs {p2_name} | format: {self.format_id} ===\n")

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

                    # |split|SIDE precedes two lines with the same event from
                    # different perspectives: first is the accurate (private) view,
                    # second is the opponent's approximate view (e.g. HP as %).
                    # Consume both but only log the accurate (first) line.
                    if line.startswith("|split|"):
                        accurate = self.process.stdout.readline().rstrip("\n\r")
                        _log(accurate)
                        self.process.stdout.readline()  # discard opponent's view
                        continue

                    # Log every raw line from the simulator
                    _log(line)

                    # Notify handlers so state-tracking subclasses stay current
                    for h in handlers.values():
                        h.consume_event(line)

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
                        _log(side_line.rstrip("\n\r"))

                        request_line = self.process.stdout.readline()
                        if not request_line:
                            break
                        request_line = request_line.strip()
                        _log(request_line)

                        if request_line.startswith("|error|"):
                            logger.warning(f"Retrying after error: {request_line}")
                            _log(f"# ERROR — retrying for {side}")
                            cached = last_requests.get(side)
                            last_action = last_actions.get(side, "pass")
                            if cached and side in handlers:
                                self._apply_error_to_request(request_line, cached)
                                handlers[side].handle_error(request_line, cached, last_action)
                                fixed = self._fix_action(request_line, last_action)
                                if fixed != last_action:
                                    action = fixed
                                else:
                                    action = handlers[side].choose_action(
                                        cached, side, self.battle_state
                                    )
                                last_actions[side] = action
                                _log(f"# RETRY >{side} {action}")
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

                        if request.get("wait"):
                            _log(f"# {side} is waiting (no action needed)")
                            continue

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
                        _log(f"# DECISION >{side} {action}")
                        self.process.stdin.write(f">{side} {action}\n")
                        self.process.stdin.flush()

                    elif line.startswith("|win|"):
                        winner = line.split("|win|")[1].strip()
                        _log(f"\n=== WINNER: {winner} after {turns} turns ===")
                        break

                    elif line.startswith("|tie|"):
                        winner = None
                        _log(f"\n=== TIE after {turns} turns ===")
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
            if log_fh:
                log_fh.close()
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
