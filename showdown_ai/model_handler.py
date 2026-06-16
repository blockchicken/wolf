"""ModelDecisionHandler — uses a trained BattlePolicy in live battles.

The handler maintains its own StateTracker so it can observe the full event
stream (opponent HP, revealed moves, field conditions) instead of relying only
on the partial information in the request JSON.

To enable event feeding the BattleRunner calls ``consume_event(line)`` on each
handler before routing sideupdate requests.  The DecisionHandler base class
provides a no-op default; only this class overrides it.
"""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any, Dict, Optional

import torch

from .battle_runner import DecisionHandler, BattleState
from .features import (
    encode_state,
    StateFeatures,
    N_ACTIVE, N_BENCH, N_MOVES_PER_SLOT,
    N_WEATHER, N_TERRAIN, N_STATUS,
    N_STAT_STAGES, N_VOLATILE, N_SIDE_CONDS,
    _WEATHER_IDX, _TERRAIN_IDX, _STATUS_IDX,
)
from .logs import _parse_line
from .model import BattlePolicy
from .state import StateTracker
from .vocab import BattleVocab

logger = logging.getLogger(__name__)

# Target categories that require an explicit slot number in doubles
_NEEDS_TARGET = {"normal", "adjacentFoe", "any"}

# Protecting moves (display names as they appear in request JSON)
_PROTECT_MOVE_NAMES: frozenset[str] = frozenset({
    "Protect", "Detect", "King's Shield", "Spiky Shield", "Baneful Bunker",
    "Obstruct", "Silk Trap", "Wide Guard", "Mat Block", "Quick Guard",
    "Max Guard", "Crafty Shield",
})

# Empirically derived from 2,807 high-rated (1500+) battles:
# real players double-protect only 6.9% of the time.
_DOUBLE_PROTECT_ALLOW_PROB = 0.07

# Moves that only work on the first turn a Pokemon is on the field.
_FIRST_TURN_ONLY_MOVES: frozenset[str] = frozenset({
    "Fake Out", "First Impression",
})

# Empirically derived from 2,807 high-rated (1500+) battles using a hardcoded
# species list (Incineroar, Sneasler, Weavile, Lopunny, Kangaskhan, Tinkaton,
# Raichu and their Megas): 55.1% of first turns use Fake Out / First Impression.
_FIRST_TURN_MOVE_PROB = 0.55

# Field-condition moves that are wasted (or self-cancelling for screens) when
# already active on my side.  Trick Room is intentionally excluded — re-using
# it removes the effect, which is sometimes a valid counter to the opponent's TR.
_REDUNDANT_WHEN_ACTIVE: dict[str, str] = {
    "reflect":     "Reflect",
    "lightscreen": "Light Screen",
    "auroraveil":  "Aurora Veil",
}


def _sf_to_tensors(sf: StateFeatures, device: torch.device) -> dict[str, torch.Tensor]:
    """Convert a StateFeatures struct to a 1-item batch of tensors."""
    def _t(arr, dtype):
        return torch.tensor(arr, dtype=dtype).unsqueeze(0).to(device)

    return {
        "active_species":      _t(sf.active_species,      torch.long),
        "active_hp":           _t(sf.active_hp,           torch.float),
        "active_status":       _t(sf.active_status,       torch.long),
        "active_moves":        _t(sf.active_moves,        torch.long),
        "active_stat_stages":  _t(sf.active_stat_stages,  torch.float),
        "active_first_turn":   _t(sf.active_first_turn,   torch.float),
        "active_used_protect": _t(sf.active_used_protect, torch.float),
        "active_volatile":     _t(sf.active_volatile,     torch.float),
        "bench_species":       _t(sf.bench_species,       torch.long),
        "bench_hp":            _t(sf.bench_hp,            torch.float),
        "bench_fainted":       _t(sf.bench_fainted,       torch.float),
        "bench_status":        _t(sf.bench_status,        torch.long),
        "weather":     torch.tensor([sf.weather],      dtype=torch.long).to(device),
        "terrain":     torch.tensor([sf.terrain],      dtype=torch.long).to(device),
        "trick_room":  torch.tensor([sf.trick_room],   dtype=torch.float).to(device),
        "my_tailwind": torch.tensor([sf.my_tailwind],  dtype=torch.float).to(device),
        "opp_tailwind":torch.tensor([sf.opp_tailwind], dtype=torch.float).to(device),
        "turn":        torch.tensor([sf.turn],         dtype=torch.float).to(device),
        "my_side_conds":  _t(sf.my_side_conds,  torch.float),
        "opp_side_conds": _t(sf.opp_side_conds, torch.float),
    }


def _species_from_details(details: str) -> str:
    """'Incineroar, L50, M' → 'Incineroar'"""
    return details.split(",")[0].strip()


def _action_to_showdown(
    kind: str,
    name: str,
    slot_idx: int,
    request: Dict[str, Any],
    is_doubles: bool,
    can_mega: bool = False,
) -> str:
    """Convert a (kind, name) model choice into a Showdown action string.

    Falls back gracefully when the chosen action isn't found in the request.
    Appends 'mega' only when can_mega=True (caller controls the always-mega policy).
    """
    mega_suffix = " mega" if can_mega else ""

    if kind == "move":
        active = request.get("active", [])
        if slot_idx < len(active):
            moves = active[slot_idx].get("moves", [])
            for i, m in enumerate(moves):
                if m.get("move") == name and not m.get("disabled"):
                    pos = i + 1
                    target = m.get("target", "")
                    if is_doubles and target in _NEEDS_TARGET:
                        return f"move {pos} 1{mega_suffix}"
                    if is_doubles and target == "adjacentAlly":
                        ally = 2 if slot_idx == 0 else 1
                        return f"move {pos} -{ally}{mega_suffix}"
                    return f"move {pos}{mega_suffix}"
        # Fallback: first non-disabled move
        active = request.get("active", [])
        if slot_idx < len(active):
            moves = active[slot_idx].get("moves", [])
            for i, m in enumerate(moves):
                if not m.get("disabled"):
                    pos = i + 1
                    target = m.get("target", "")
                    if is_doubles and target in _NEEDS_TARGET:
                        return f"move {pos} 1{mega_suffix}"
                    return f"move {pos}{mega_suffix}"
        return f"move 1{mega_suffix}"

    elif kind == "switch":
        pokemon = request.get("side", {}).get("pokemon", [])
        for i, p in enumerate(pokemon):
            sp = _species_from_details(p.get("details", ""))
            if sp == name and not p.get("active"):
                cond = p.get("condition", "")
                if not (cond == "0" or cond.startswith("0 ")):
                    return f"switch {i + 1}"
        # Fallback: first available switch
        for i, p in enumerate(pokemon):
            if p.get("active"):
                continue
            cond = p.get("condition", "")
            if not (cond == "0" or cond.startswith("0 ")):
                return f"switch {i + 1}"
        return "move 1"

    return "move 1"


class ModelDecisionHandler(DecisionHandler):
    """Decision handler backed by a trained BattlePolicy.

    Parameters
    ----------
    model:
        Trained BattlePolicy (or path to a ``.pt`` checkpoint).
    vocab:
        BattleVocab (or path to a vocab directory).
    side:
        The side this handler controls (``"p1"`` or ``"p2"``).
    device:
        PyTorch device string (``"cpu"``, ``"cuda"``, ``"mps"``).
    """

    def __init__(
        self,
        model:  "BattlePolicy | str | Path",
        vocab:  "BattleVocab | str | Path",
        side:   str,
        device: str = "cpu",
    ) -> None:
        if isinstance(model, (str, Path)):
            model = BattlePolicy.load(Path(model), device=device)
        if isinstance(vocab, (str, Path)):
            vocab = BattleVocab.load(Path(vocab))

        self.model:   BattlePolicy = model
        self.vocab:   BattleVocab  = vocab
        self.side:    str          = side
        self.device:  torch.device = torch.device(device)

        self.model.to(self.device)
        self.model.eval()

        self._tracker  = StateTracker(side)
        self._mega_used = False
        self._banned_switch_slots: set = set()  # 1-based team slots Showdown rejected

    # ------------------------------------------------------------------
    # Event hook (called by BattleRunner for every raw line)

    def consume_event(self, line: str) -> None:
        # |-mega|p1a: Charizard|Charizardite Y  → our side mega'd
        if "|-mega|" in line and f"|{self.side}" in line:
            self._mega_used = True
        event = _parse_line(line)
        if event.kind not in {"blank", "text"}:
            self._tracker.apply(event)   # apply in-place; no deepcopy needed here

    def handle_error(self, error_line: str, last_request: Dict[str, Any], last_action: str) -> None:
        if "can only switch in once" in error_line:
            import re
            m = re.search(r"slot (\d+)", error_line)
            if m:
                self._banned_switch_slots.add(int(m.group(1)))

    # ------------------------------------------------------------------
    # Core decision

    def choose_action(
        self,
        request:      Dict[str, Any],
        side:         str,
        battle_state: BattleState,
    ) -> str:
        if request.get("wait"):
            return "pass"

        if request.get("teamPreview"):
            team_size = len(request.get("side", {}).get("pokemon", []))
            max_size  = (
                request.get("maxChosenTeamSize")
                or request.get("maxTeamSize")
                or team_size
            )
            chosen = min(max_size, team_size)
            return "team " + ",".join(str(i + 1) for i in range(chosen))

        force_switch = request.get("forceSwitch", [])
        if isinstance(force_switch, list) and any(force_switch):
            return self._handle_force_switch(request, force_switch)

        return self._handle_normal_turn(request, battle_state)

    # ------------------------------------------------------------------
    # Internals

    def _score_actions(self, request: Dict[str, Any]) -> tuple[torch.Tensor, torch.Tensor]:
        """Run model forward pass.  Returns (logits_a, logits_b) (1, n_actions)."""
        # Read live state directly — encode_state is read-only so no copy needed.
        sf = encode_state(self._tracker._s, self.vocab)
        batch = _sf_to_tensors(sf, self.device)
        with torch.no_grad():
            logits_a, logits_b = self.model(**batch)
        return logits_a[0], logits_b[0]

    def _available_actions(
        self,
        slot_idx: int,
        request: Dict[str, Any],
        include_switches: bool = True,
        already_chosen_switches: Optional[set] = None,
    ) -> list[tuple[str, str]]:
        """Return list of available (kind, name) pairs for a slot."""
        actions: list[tuple[str, str]] = []

        active = request.get("active", [])
        if slot_idx < len(active):
            for m in active[slot_idx].get("moves", []):
                if not m.get("disabled"):
                    actions.append(("move", m["move"]))

        # Showdown sets trapped/maybeTrapped in active[i] when the Pokemon cannot switch.
        slot_data = active[slot_idx] if slot_idx < len(active) else {}
        is_trapped = slot_data.get("trapped") or slot_data.get("maybeTrapped")

        if include_switches and not is_trapped:
            pokemon = request.get("side", {}).get("pokemon", [])
            for team_slot_0, p in enumerate(pokemon):
                if p.get("active"):
                    continue
                slot_1based = team_slot_0 + 1
                if slot_1based in self._banned_switch_slots:
                    continue
                if already_chosen_switches and slot_1based in already_chosen_switches:
                    continue
                cond = p.get("condition", "")
                if cond == "0" or cond.startswith("0 "):
                    continue
                sp = _species_from_details(p.get("details", ""))
                actions.append(("switch", sp))

        slot_id  = f"{self.side}{'ab'[slot_idx]}" if slot_idx < 2 else None
        st       = self._tracker._s

        if slot_id:
            is_first_turn = (
                slot_id in st.entry_turn
                and st.turn - st.entry_turn[slot_id] == 1
            )

            # Strip first-turn-only moves when it's not their first turn.
            # Showdown should already mark these disabled, but belt-and-suspenders.
            if not is_first_turn:
                filtered = [(k, n) for k, n in actions if not (k == "move" and n in _FIRST_TURN_ONLY_MOVES)]
                if filtered:
                    actions = filtered

            # 70% of the time when the first-turn move is available on the first
            # turn, narrow the choice to that move to boost its usage.
            if is_first_turn:
                ft_available = [(k, n) for k, n in actions if k == "move" and n in _FIRST_TURN_ONLY_MOVES]
                if ft_available and random.random() < _FIRST_TURN_MOVE_PROB:
                    actions = ft_available

            # Strip protecting moves when this slot protected last turn,
            # unless Encore is forcing the move (in which case we must use it).
            if slot_id in st.protect_last_turn:
                vs = st.volatile_status.get(slot_id, set())
                if "encore" not in vs and random.random() >= _DOUBLE_PROTECT_ALLOW_PROB:
                    filtered = [(k, n) for k, n in actions if not (k == "move" and n in _PROTECT_MOVE_NAMES)]
                    if filtered:
                        actions = filtered

            # Mask field-condition moves that are already active on my side.
            my_sc = st.side_conditions.get(self.side, {})
            if self.side in st.tailwind:
                filtered = [(k, n) for k, n in actions if not (k == "move" and n == "Tailwind")]
                if filtered:
                    actions = filtered
            for cond_key, move_name in _REDUNDANT_WHEN_ACTIVE.items():
                if my_sc.get(cond_key):
                    filtered = [(k, n) for k, n in actions if not (k == "move" and n == move_name)]
                    if filtered:
                        actions = filtered

        return actions

    def _best_action(
        self,
        logits: torch.Tensor,
        available: list[tuple[str, str]],
    ) -> tuple[str, str]:
        """Return the (kind, name) with the highest logit among available actions."""
        best_score = float("-inf")
        best: tuple[str, str] = available[0] if available else ("move", "")

        for kind, name in available:
            idx = self.vocab.action_idx(kind, name)
            if idx is None:
                continue
            score = logits[idx].item()
            if score > best_score:
                best_score = score
                best = (kind, name)

        return best

    def _handle_normal_turn(
        self, request: Dict[str, Any], battle_state: BattleState
    ) -> str:
        is_doubles = battle_state.is_doubles
        active     = request.get("active", [])
        logits_a, logits_b = self._score_actions(request)
        heads = [logits_a, logits_b]

        # active[i] corresponds to the i-th active: true Pokemon in side.pokemon.
        # A Pokemon can be active: true but fainted (condition "0 fnt") when it
        # died mid-turn and hasn't been replaced yet.  Sending choices for that
        # slot causes "more choices than unfainted Pokémon" errors.
        pokemon = request.get("side", {}).get("pokemon", [])
        active_pokemon = [p for p in pokemon if p.get("active")]

        choices: list[str] = []
        mega_granted = False            # at most one mega per action command
        chosen_switches: set[int] = set()  # prevent duplicate switch targets

        for slot_idx in range(len(active)):
            # Skip the slot if the corresponding Pokemon has fainted.
            if slot_idx < len(active_pokemon):
                cond = active_pokemon[slot_idx].get("condition", "")
                if cond == "0" or cond.startswith("0 "):
                    continue

            slot_data = active[slot_idx] if slot_idx < len(active) else {}
            if not slot_data or not slot_data.get("moves"):
                continue

            logits    = heads[slot_idx] if slot_idx < len(heads) else heads[-1]
            available = self._available_actions(slot_idx, request, already_chosen_switches=chosen_switches)
            if not available:
                # All moves appear disabled in the stale request.  Pick a random
                # move index so successive retries probe different options rather
                # than always colliding on "move 1".
                import random
                move_count = len(active[slot_idx].get("moves", []))
                choices.append(f"move {random.randint(1, max(1, move_count))}")
                continue

            kind, name = self._best_action(logits, available)
            can_mega = (
                not self._mega_used
                and not mega_granted
                and bool(slot_data.get("canMegaEvo"))
            )
            if can_mega:
                mega_granted = True
            action_str = _action_to_showdown(kind, name, slot_idx, request, is_doubles, can_mega=can_mega)
            # Record switch targets so the other slot doesn't pick the same Pokémon
            parts = action_str.split()
            if parts[0] == "switch" and len(parts) >= 2:
                try:
                    chosen_switches.add(int(parts[1]))
                except ValueError:
                    pass
            choices.append(action_str)

        return ", ".join(choices) if choices else "move 1"

    def _handle_force_switch(
        self, request: Dict[str, Any], force_switch: list
    ) -> str:
        logits_a, logits_b = self._score_actions(request)
        heads = [logits_a, logits_b]

        already_chosen: set[int] = set()
        choices: list[str] = []

        for slot_idx, must_switch in enumerate(force_switch):
            if not must_switch:
                continue

            logits    = heads[slot_idx] if slot_idx < len(heads) else heads[-1]
            pokemon   = request.get("side", {}).get("pokemon", [])
            available = [
                ("switch", _species_from_details(p.get("details", "")))
                for i, p in enumerate(pokemon)
                if not p.get("active")
                and (i + 1) not in already_chosen
                and (i + 1) not in self._banned_switch_slots
                and not (p.get("condition", "").startswith("0 ") or p.get("condition") == "0")
            ]

            if not available:
                # Last resort: ignore the once-per-battle ban if it's the only option.
                # Sending "pass" here creates an infinite loop when Showdown requires a switch.
                fallback = [
                    ("switch", _species_from_details(p.get("details", "")))
                    for i, p in enumerate(pokemon)
                    if not p.get("active")
                    and (i + 1) not in already_chosen
                    and not (p.get("condition", "").startswith("0 ") or p.get("condition") == "0")
                ]
                available = fallback if fallback else available

            if not available:
                choices.append("pass")
                continue

            kind, name = self._best_action(logits, available)
            action_str = _action_to_showdown(kind, name, slot_idx, request, False)
            # Record which team slot we chose so we don't double-pick it
            parts = action_str.split()
            if len(parts) == 2 and parts[0] == "switch":
                already_chosen.add(int(parts[1]))
            choices.append(action_str)

        return ", ".join(choices) if choices else "pass"
