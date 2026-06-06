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

    def consume(self, event: ParsedEvent) -> PerspectiveState:
        """Apply one event and return a snapshot of the updated state."""
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

    def _apply(self, event: ParsedEvent) -> None:
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
            s.turn = int(a[0])

        # Switch-ins and phazing (drag / baton pass / etc.)
        elif k in {"switch", "drag"} and len(a) >= 2:
            slot = _slot_id(a[0])
            sp   = _species(a[1])
            hp   = a[2].split()[0] if len(a) > 2 else "100/100"
            s.active_species[slot] = sp
            s.active_hp[slot]      = hp
            s.active_status.pop(slot, None)   # status resets on switch
            s.fainted_slots.discard(slot)

        # Forme changes (mega evolution, etc.)
        elif k == "detailschange" and len(a) >= 2:
            slot = _slot_id(a[0])
            s.active_species[slot] = _species(a[1])

        # Illusion breaks (|replace| reveals the true species after Zoroark drops its disguise)
        elif k == "replace" and len(a) >= 2:
            slot = _slot_id(a[0])
            s.active_species[slot] = _species(a[1])

        # Move used — track revealed moves
        elif k == "move" and len(a) >= 2:
            actor = a[0]
            move  = a[1]
            s.revealed_moves.setdefault(actor, set()).add(move)

        # Damage and healing
        elif k == "-damage" and len(a) >= 2:
            slot = _slot_id(a[0])
            s.active_hp[slot] = a[1].split()[0]

        elif k == "-heal" and len(a) >= 2:
            slot = _slot_id(a[0])
            s.active_hp[slot] = a[1].split()[0]

        # Status conditions
        elif k == "-status" and len(a) >= 2:
            slot = _slot_id(a[0])
            s.active_status[slot] = a[1]

        elif k == "-curestatus" and a:
            slot = _slot_id(a[0])
            s.active_status.pop(slot, None)

        # Faints
        elif k == "faint" and a:
            slot = _slot_id(a[0])
            s.fainted_slots.add(slot)
            s.active_hp[slot] = "0 fnt"

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

        # Side conditions (tailwind, reflect, light screen, …)
        elif k == "-sidestart" and len(a) >= 2:
            side_id = a[0].split(":")[0].strip()
            if "tailwind" in a[1].lower():
                s.tailwind.add(side_id)

        elif k == "-sideend" and len(a) >= 2:
            side_id = a[0].split(":")[0].strip()
            if "tailwind" in a[1].lower():
                s.tailwind.discard(side_id)

        # Winner
        elif k == "win" and a:
            s.winner = a[0]
