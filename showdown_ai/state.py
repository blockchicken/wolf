from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

from .logs import ParsedEvent


@dataclass
class PerspectiveState:
    side: str
    turn: int = 0
    active: dict[str, str] = field(default_factory=dict)
    hp: dict[str, str] = field(default_factory=dict)
    known_moves: dict[str, set[str]] = field(default_factory=dict)
    fainted: set[str] = field(default_factory=set)
    winner: str | None = None


class StateTracker:
    """Incrementally derive model-ready state from showdown protocol events."""

    def __init__(self, side: str) -> None:
        if side not in {"p1", "p2"}:
            raise ValueError("side must be 'p1' or 'p2'")
        self.state = PerspectiveState(side=side)
        self.timeline: List[PerspectiveState] = []

    def consume(self, event: ParsedEvent) -> PerspectiveState:
        s = self.state

        if event.kind == "turn" and event.args:
            s.turn = int(event.args[0])
        elif event.kind == "switch" and event.args:
            slot = event.args[0]
            details = event.args[1] if len(event.args) > 1 else "Unknown"
            hp = event.args[2] if len(event.args) > 2 else "?"
            s.active[slot.split(":")[0]] = details
            s.hp[slot] = hp
        elif event.kind == "move" and len(event.args) >= 2:
            actor = event.args[0]
            move_name = event.args[1]
            s.known_moves.setdefault(actor, set()).add(move_name)
        elif event.kind == "-damage" and len(event.args) >= 2:
            s.hp[event.args[0]] = event.args[1]
        elif event.kind == "faint" and event.args:
            s.fainted.add(event.args[0])
        elif event.kind == "win" and event.args:
            s.winner = event.args[0]

        snapshot = PerspectiveState(
            side=s.side,
            turn=s.turn,
            active=dict(s.active),
            hp=dict(s.hp),
            known_moves={k: set(v) for k, v in s.known_moves.items()},
            fainted=set(s.fainted),
            winner=s.winner,
        )
        self.timeline.append(snapshot)
        return snapshot

    def consume_all(self, events: list[ParsedEvent]) -> list[PerspectiveState]:
        return [self.consume(event) for event in events]
