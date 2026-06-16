"""Battle state tracking for VGC doubles from one player's perspective."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import List, Optional

from .logs import ParsedEvent


# ---------------------------------------------------------------------------
# Slot helpers
# ---------------------------------------------------------------------------

def _slot_id(actor: str) -> str:
    """Extract the slot identifier from an actor string.

    'p1a: Incineroar' → 'p1a'
    'p2b: Garchomp'   → 'p2b'
    """
    return actor.split(":")[0].strip()


def _slot_side(slot: str) -> str:
    """'p1a' → 'p1',  'p2b' → 'p2'"""
    return slot[:2]


def _slot_suffix(slot: str) -> str:
    """'p1a' → 'a',  'p2b' → 'b'"""
    return slot[2]


def _species(details: str) -> str:
    """'Pelipper, L50, M' → 'Pelipper'"""
    return details.split(",")[0].strip()


# ---------------------------------------------------------------------------
# Constants for derived state
# ---------------------------------------------------------------------------

# Moves that activate the "used protect" flag.
_PROTECT_MOVES: frozenset[str] = frozenset({
    "protect", "detect", "kingsshield", "spikyshield", "banefulbunker",
    "obstruct", "silktrap", "wideguard", "matblock", "quickguard",
    "maxguard", "craftyshield",
})

# Volatile status patterns: canonical name → substrings to match in -start/-end effect strings.
_VOLATILE_PATTERNS: dict[str, list[str]] = {
    "confusion":  ["confusion"],
    "taunt":      ["taunt"],
    "encore":     ["encore"],
    "leech_seed": ["leech seed"],
    "substitute": ["substitute"],
}


# ---------------------------------------------------------------------------
# State dataclass
# ---------------------------------------------------------------------------

@dataclass
class PerspectiveState:
    """VGC doubles battle state from one player's perspective.

    All HP values are stored as the string Showdown emits — e.g. '87/100' or
    '0 fnt'.  The battle logs use percentages for all Pokémon (the replay /
    omniscient stream), so there is no private/public distinction to enforce
    at this layer; information asymmetry is applied at the feature-extraction
    step in training_data.py.
    """

    side: str   # "p1" or "p2"
    turn: int = 0

    # Teams announced during team-preview (species names only, up to 6)
    my_team: List[str] = field(default_factory=list)
    opp_team: List[str] = field(default_factory=list)

    # Active slot state.  Keys are slot IDs: "p1a", "p1b", "p2a", "p2b".
    # A slot is absent from the dict if no Pokémon has ever been in it.
    active_species: dict[str, str] = field(default_factory=dict)  # slot → species
    active_hp:      dict[str, str] = field(default_factory=dict)  # slot → "87/100"
    active_status:  dict[str, str] = field(default_factory=dict)  # slot → "par"/"brn"/…

    # Moves revealed through use.  Key = full actor string ("p1a: Pelipper").
    revealed_moves: dict[str, set[str]] = field(default_factory=dict)

    # Slots that have fainted this battle (slot ID like "p1b")
    fainted_slots: set[str] = field(default_factory=set)

    # Field conditions
    weather:    Optional[str] = None   # "raindance", "sunnyday", "sandstorm", "snow"
    terrain:    Optional[str] = None   # "electricterrain", "grassyterrain", etc.
    trick_room: bool = False
    tailwind:   set[str] = field(default_factory=set)  # sides: {"p1"}, {"p2"}

    # --- New fields ---

    # Stat stage changes per active slot.  slot → {atk: 2, def: -1, …}
    stat_stages: dict[str, dict[str, int]] = field(default_factory=dict)

    # Volatile status flags per active slot.  slot → {confusion, taunt, …}
    volatile_status: dict[str, set[str]] = field(default_factory=dict)

    # Per-side hazards/screens.  side → {reflect: 1, spikes: 2, …}
    side_conditions: dict[str, dict[str, int]] = field(default_factory=dict)

    # Turn on which a Pokemon entered the field (for first-turn detection).
    # Game-start Pokemon enter at turn 0 (before |turn|1|).
    entry_turn: dict[str, int] = field(default_factory=dict)  # slot → turn number

    # Which slots used a protecting move last turn / this turn.
    protect_last_turn: set[str] = field(default_factory=set)
    protect_this_turn: set[str] = field(default_factory=set)

    # Status for Pokemon on the bench, keyed by species name.
    bench_status: dict[str, str] = field(default_factory=dict)

    winner: Optional[str] = None  # player name from |win| event

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------

    def my_slots(self) -> tuple[str, str]:
        return (f"{self.side}a", f"{self.side}b")

    def opp_side(self) -> str:
        return "p2" if self.side == "p1" else "p1"

    def opp_slots(self) -> tuple[str, str]:
        opp = self.opp_side()
        return (f"{opp}a", f"{opp}b")

    def active_my_side(self) -> dict[str, str]:
        """slot → species for my active (non-fainted) Pokémon."""
        return {
            s: self.active_species[s]
            for s in self.my_slots()
            if s in self.active_species and s not in self.fainted_slots
        }

    def active_opp_side(self) -> dict[str, str]:
        return {
            s: self.active_species[s]
            for s in self.opp_slots()
            if s in self.active_species and s not in self.fainted_slots
        }


# ---------------------------------------------------------------------------
# StateTracker
# ---------------------------------------------------------------------------

class StateTracker:
    """Incrementally derives a PerspectiveState from Showdown protocol events.

    Usage::

        tracker = StateTracker("p1")
        states = tracker.consume_all(events)   # list of snapshots, one per event
        final  = states[-1]                    # state after last event

    Or, for turn-boundary snapshots::

        tracker.consume(turn_event)
        snap = tracker.snapshot()              # deep copy of current state
        for event in turn_body_events:
            tracker.consume(event)
    """

    def __init__(self, side: str) -> None:
        if side not in {"p1", "p2"}:
            raise ValueError("side must be 'p1' or 'p2'")
        self.side = side
        self._s = PerspectiveState(side=side)

    # ------------------------------------------------------------------

    def apply(self, event: ParsedEvent) -> None:
        """Apply one event in-place without snapshotting (fast path for inference)."""
        self._apply(event)

    def consume(self, event: ParsedEvent) -> PerspectiveState:
        """Apply one event and return a snapshot of the updated state.

        Used by the training-data pipeline where every post-event state is needed.
        For live inference use ``apply()`` instead to avoid the deepcopy overhead.
        """
        self._apply(event)
        return self.snapshot()

    def consume_all(self, events: list[ParsedEvent]) -> list[PerspectiveState]:
        return [self.consume(e) for e in events]

    def snapshot(self) -> PerspectiveState:
        """Deep copy of the current state — use at turn boundaries."""
        return deepcopy(self._s)

    # ------------------------------------------------------------------
    # Internal event application
    # ------------------------------------------------------------------

    def _apply(self, event: ParsedEvent) -> None:  # noqa: C901 (complex but systematic)
        s = self._s
        k = event.kind
        a = event.args

        # Team preview
        if k == "poke" and len(a) >= 2:
            poke_side = a[0]
            sp = _species(a[1])
            if poke_side == self.side:
                s.my_team.append(sp)
            else:
                s.opp_team.append(sp)

        elif k == "turn" and a:
            # Roll protect tracking: what happened last turn is now "last turn".
            s.protect_last_turn = set(s.protect_this_turn)
            s.protect_this_turn = set()
            s.turn = int(a[0])

        # Switch-ins and phazing (drag / baton pass / etc.)
        elif k in {"switch", "drag"} and len(a) >= 2:
            slot   = _slot_id(a[0])
            sp     = _species(a[1])
            hp     = a[2].split()[0] if len(a) > 2 else "100/100"

            # Carry outgoing Pokemon's persistent status to bench_status.
            old_sp = s.active_species.get(slot)
            if old_sp:
                old_status = s.active_status.get(slot, "")
                if old_status:
                    s.bench_status[old_sp] = old_status
                else:
                    s.bench_status.pop(old_sp, None)

            s.active_species[slot] = sp
            s.active_hp[slot]      = hp

            # Restore status from bench (persists through switch).
            bench_st = s.bench_status.get(sp, "")
            if bench_st:
                s.active_status[slot] = bench_st
            else:
                s.active_status.pop(slot, None)

            # Clear per-slot volatile state that doesn't persist through switches.
            s.stat_stages.pop(slot, None)
            s.volatile_status.pop(slot, None)
            s.protect_this_turn.discard(slot)

            # Record which turn this Pokemon entered (for first-turn flag).
            s.entry_turn[slot] = s.turn

            s.fainted_slots.discard(slot)

        # Forme changes (mega evolution, etc.)
        elif k == "detailschange" and len(a) >= 2:
            slot = _slot_id(a[0])
            s.active_species[slot] = _species(a[1])

        # Illusion breaks (|replace| reveals the true species after Zoroark drops its disguise)
        elif k == "replace" and len(a) >= 2:
            slot = _slot_id(a[0])
            s.active_species[slot] = _species(a[1])

        # Move used — track revealed moves and protecting moves
        elif k == "move" and len(a) >= 2:
            actor = a[0]
            move  = a[1]
            s.revealed_moves.setdefault(actor, set()).add(move)
            if move.lower().replace(" ", "").replace("-", "") in _PROTECT_MOVES:
                slot = _slot_id(actor)
                s.protect_this_turn.add(slot)

        # Damage and healing
        elif k == "-damage" and len(a) >= 2:
            slot = _slot_id(a[0])
            s.active_hp[slot] = a[1].split()[0]

        elif k == "-heal" and len(a) >= 2:
            slot = _slot_id(a[0])
            s.active_hp[slot] = a[1].split()[0]

        # Non-volatile status conditions
        elif k == "-status" and len(a) >= 2:
            slot   = _slot_id(a[0])
            status = a[1]
            s.active_status[slot] = status
            sp = s.active_species.get(slot)
            if sp:
                s.bench_status[sp] = status

        elif k == "-curestatus" and a:
            slot = _slot_id(a[0])
            s.active_status.pop(slot, None)
            sp = s.active_species.get(slot)
            if sp:
                s.bench_status.pop(sp, None)

        # Faints — clear per-slot volatile state
        elif k == "faint" and a:
            slot = _slot_id(a[0])
            s.fainted_slots.add(slot)
            s.active_hp[slot] = "0 fnt"
            s.stat_stages.pop(slot, None)
            s.volatile_status.pop(slot, None)
            s.protect_this_turn.discard(slot)

        # Weather
        elif k == "-weather" and a:
            raw = a[0].lower()
            s.weather = None if raw == "none" else raw

        # Field effects (terrain, trick room)
        elif k == "-fieldstart" and a:
            name = a[0].lower().replace("move: ", "").replace(" ", "")
            if "trickroom" in name:
                s.trick_room = True
            else:
                s.terrain = name

        elif k == "-fieldend" and a:
            name = a[0].lower().replace("move: ", "").replace(" ", "")
            if "trickroom" in name:
                s.trick_room = False
            else:
                s.terrain = None

        # Side conditions (tailwind, screens, hazards)
        elif k == "-sidestart" and len(a) >= 2:
            side_id   = a[0].split(":")[0].strip()
            cond_raw  = a[1].lower()
            cond_clean = cond_raw.replace("move: ", "").replace(" ", "")
            sc = s.side_conditions.setdefault(side_id, {})

            if "tailwind" in cond_clean:
                s.tailwind.add(side_id)
            elif "reflect" in cond_clean:
                sc["reflect"] = 1
            elif "lightscreen" in cond_clean:
                sc["lightscreen"] = 1
            elif "auroraveil" in cond_clean:
                sc["auroraveil"] = 1
            elif "stealthrock" in cond_clean:
                sc["stealthrock"] = 1
            elif "toxicspikes" in cond_clean:
                sc["toxicspikes"] = min(2, sc.get("toxicspikes", 0) + 1)
            elif "spikes" in cond_clean:
                sc["spikes"] = min(3, sc.get("spikes", 0) + 1)
            elif "stickyweb" in cond_clean:
                sc["stickyweb"] = 1

        elif k == "-sideend" and len(a) >= 2:
            side_id   = a[0].split(":")[0].strip()
            cond_raw  = a[1].lower()
            cond_clean = cond_raw.replace("move: ", "").replace(" ", "")
            sc = s.side_conditions.get(side_id, {})

            if "tailwind" in cond_clean:
                s.tailwind.discard(side_id)
            elif "reflect" in cond_clean:
                sc.pop("reflect", None)
            elif "lightscreen" in cond_clean:
                sc.pop("lightscreen", None)
            elif "auroraveil" in cond_clean:
                sc.pop("auroraveil", None)
            elif "stealthrock" in cond_clean:
                sc.pop("stealthrock", None)
            elif "toxicspikes" in cond_clean:
                sc.pop("toxicspikes", None)
            elif "spikes" in cond_clean:
                sc.pop("spikes", None)
            elif "stickyweb" in cond_clean:
                sc.pop("stickyweb", None)

        # Stat boosts
        elif k == "-boost" and len(a) >= 3:
            slot   = _slot_id(a[0])
            stat   = a[1]
            amount = int(a[2])
            stages = s.stat_stages.setdefault(slot, {})
            stages[stat] = max(-6, min(6, stages.get(stat, 0) + amount))

        elif k == "-unboost" and len(a) >= 3:
            slot   = _slot_id(a[0])
            stat   = a[1]
            amount = int(a[2])
            stages = s.stat_stages.setdefault(slot, {})
            stages[stat] = max(-6, min(6, stages.get(stat, 0) - amount))

        elif k == "-setboost" and len(a) >= 3:
            slot   = _slot_id(a[0])
            stat   = a[1]
            amount = int(a[2])
            stages = s.stat_stages.setdefault(slot, {})
            stages[stat] = max(-6, min(6, amount))

        elif k == "-clearboost" and a:
            slot = _slot_id(a[0])
            s.stat_stages.pop(slot, None)

        elif k == "-clearallboost":
            s.stat_stages.clear()

        elif k == "-invertboost" and a:
            slot = _slot_id(a[0])
            stages = s.stat_stages.get(slot)
            if stages:
                for stat in stages:
                    stages[stat] = -stages[stat]

        # Volatile statuses
        elif k == "-start" and len(a) >= 2:
            slot   = _slot_id(a[0])
            effect = a[1].lower()
            for canonical, patterns in _VOLATILE_PATTERNS.items():
                if any(p in effect for p in patterns):
                    s.volatile_status.setdefault(slot, set()).add(canonical)
                    break

        elif k == "-end" and len(a) >= 2:
            slot   = _slot_id(a[0])
            effect = a[1].lower()
            vs = s.volatile_status.get(slot)
            if vs:
                for canonical, patterns in _VOLATILE_PATTERNS.items():
                    if any(p in effect for p in patterns):
                        vs.discard(canonical)
                        break

        # Winner
        elif k == "win" and a:
            s.winner = a[0]
