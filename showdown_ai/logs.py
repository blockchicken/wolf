from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ParsedEvent:
    """Tokenized showdown log event."""

    kind: str
    args: tuple[str, ...]
    raw: str


@dataclass(frozen=True)
class BattleLog:
    battle_id: str
    format_name: str
    players: tuple[str, str]
    events: tuple[ParsedEvent, ...]


def _parse_line(line: str) -> ParsedEvent:
    if not line:
        return ParsedEvent(kind="blank", args=(), raw=line)
    if not line.startswith("|"):
        return ParsedEvent(kind="text", args=(line,), raw=line)

    parts = line.split("|")
    # First element is always empty because showdown protocol lines start with '|'.
    kind = parts[1] if len(parts) > 1 and parts[1] else "blank"
    args = tuple(parts[2:])
    return ParsedEvent(kind=kind, args=args, raw=line)


def parse_protocol(log_blob: str) -> tuple[ParsedEvent, ...]:
    return tuple(_parse_line(line) for line in log_blob.splitlines())


def load_showdown_log_json(path: str | Path) -> BattleLog:
    payload = json.loads(Path(path).read_text())
    players = tuple(payload.get("players", []))
    if len(players) != 2:
        # Fallback for uncommon payload shapes.
        p1 = payload.get("p1", "p1")
        p2 = payload.get("p2", "p2")
        players = (p1, p2)

    return BattleLog(
        battle_id=payload["id"],
        format_name=payload["format"],
        players=(players[0], players[1]),
        events=parse_protocol(payload["log"]),
    )


def split_perspective_logs(events: Iterable[ParsedEvent]) -> dict[str, list[ParsedEvent]]:
    """Build two limited-information training views from one omniscient log.

    This keeps all public battle events and strips spectator/chat noise. Opponent
    private-choice lines are not emitted (for uploaded ladder logs they are usually
    absent already, but this method is robust if present).
    """

    keep_kinds = {
        "player",
        "teamsize",
        "gen",
        "tier",
        "rule",
        "clearpoke",
        "poke",
        "teampreview",
        "start",
        "switch",
        "move",
        "-damage",
        "-heal",
        "-status",
        "-curestatus",
        "-boost",
        "-unboost",
        "-ability",
        "-item",
        "-enditem",
        "-weather",
        "-fieldstart",
        "-fieldend",
        "-sidestart",
        "-sideend",
        "-activate",
        "-immune",
        "-resisted",
        "-supereffective",
        "faint",
        "cant",
        "turn",
        "upkeep",
        "win",
        "tie",
        "detailschange",
        "replace",
        "drag",
        "request",
        "choice",
    }

    views: dict[str, list[ParsedEvent]] = {"p1": [], "p2": []}

    for event in events:
        if event.kind not in keep_kinds:
            continue

        # Safety filter if private request/choice events are included.
        if event.kind in {"request", "choice"}:
            owner = event.args[0] if event.args else ""
            for side in ("p1", "p2"):
                if owner.startswith(side):
                    views[side].append(event)
            continue

        views["p1"].append(event)
        views["p2"].append(event)

    return views
