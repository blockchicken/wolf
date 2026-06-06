"""Training data extraction from downloaded VGC doubles battle logs.

Design
------
Each JSON log from Showdown is processed into *two* sets of TurnExamples —
one from p1's perspective, one from p2's — doubling the effective dataset
size without requiring additional battles.

A TurnExample captures:
  - The full observable battle state at the START of a turn (before moves resolve)
  - The joint action taken by that side's two active slots during the turn
  - The battle outcome (win / loss / tie) for outcome-weighted training

Information asymmetry is enforced at feature time, not event time.  Both
perspectives consume the same public event stream (all HP as %, all revealed
moves), but when building model inputs from a p1 example the p2 slots are
treated as "opponent" (with only revealed moves), and vice-versa.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, List, Dict, Tuple

from .logs import BattleLog, ParsedEvent, load_showdown_log_json
from .state import StateTracker, PerspectiveState, _slot_id, _species


# ---------------------------------------------------------------------------
# Action types
# ---------------------------------------------------------------------------

@dataclass
class SlotAction:
    """What one active slot decided to do during a turn.

    For 'move': `name` is the move name; `target` is the target slot id (e.g.
    'p2a') when the target is deterministic from the log, otherwise None.

    For 'switch': `name` is the species switched in.
    """
    kind: str           # "move" or "switch"
    name: str
    target: Optional[str] = None   # target slot id for targeted moves


# ---------------------------------------------------------------------------
# Training example
# ---------------------------------------------------------------------------

@dataclass
class TurnExample:
    """One per-turn, per-side training example for VGC doubles.

    `state` is a snapshot taken at the |turn|N marker — i.e. the full
    observable battle state *before* any moves in turn N execute.

    `actions` maps slot suffix ('a' or 'b') to the SlotAction chosen for that
    slot this turn.  A slot is absent from the dict if it wasn't active or
    if its action could not be determined from the log (e.g. it flinched
    before acting — in that case the player's decision was made but the log
    only shows |cant|, not what move was intended).
    """
    battle_id: str
    side: str               # "p1" or "p2"
    turn: int

    state: PerspectiveState  # pre-turn observable state

    # slot suffix → action chosen for that slot
    actions: Dict[str, SlotAction]

    outcome: str            # "win" / "loss" / "tie"
    outcome_weight: float   # 1.0  /  0.5   /  0.25


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _winner_side(log: BattleLog) -> Optional[str]:
    """Map the |win| player name back to 'p1' or 'p2'."""
    for event in log.events:
        if event.kind == "win" and event.args:
            name = event.args[0]
            try:
                p1_name, p2_name = log.players
            except Exception:
                return None
            if name == p1_name:
                return "p1"
            if name == p2_name:
                return "p2"
    return None


def _outcome(side: str, winner: Optional[str]) -> Tuple[str, float]:
    if winner is None:
        return "tie", 0.25
    return ("win", 1.0) if winner == side else ("loss", 0.5)


def _group_turns(
    events: tuple[ParsedEvent, ...],
) -> List[Tuple[int, ParsedEvent, List[ParsedEvent]]]:
    """Split events into (turn_num, turn_event, body_events) triples.

    turn_event is the |turn|N ParsedEvent itself (needed to advance the
    tracker's turn counter before snapshotting).  body_events are everything
    that follows until the next |turn| marker.

    Group 0 contains events before the first |turn|, with a synthetic
    turn_event of None — these are team-preview and initial switch-ins and
    are consumed to seed the tracker but never produce TurnExamples.
    """
    groups: List[Tuple[int, Optional[ParsedEvent], List[ParsedEvent]]] = [
        (0, None, [])
    ]
    for event in events:
        if event.kind == "turn" and event.args:
            groups.append((int(event.args[0]), event, []))
        else:
            groups[-1][2].append(event)
    return groups


def _actions_for_side(
    body_events: List[ParsedEvent],
    side: str,
) -> Dict[str, SlotAction]:
    """Extract the first decisive action each of `side`'s slots took.

    In doubles VGC each slot chooses one action per turn.  We look for the
    first |move| or voluntary |switch| per slot.

    A |switch| that immediately follows a |move| for the same slot is a
    pivot-move follow-up (U-turn, Volt Switch, etc.) — we do NOT record it
    as a separate decision because the decision was already captured by the
    |move| event.
    """
    my_slots = {f"{side}a", f"{side}b"}
    actions: Dict[str, SlotAction] = {}
    slots_that_moved: set[str] = set()

    for event in body_events:
        if event.kind == "move" and len(event.args) >= 2:
            slot = _slot_id(event.args[0])
            if slot not in my_slots or slot in actions:
                continue
            # Target slot (for targeted moves) — may be absent for spread/self moves
            raw_target = event.args[2] if len(event.args) > 2 else None
            target_slot = (
                _slot_id(raw_target)
                if raw_target and ":" in raw_target
                else None
            )
            actions[slot[2]] = SlotAction(  # suffix "a" or "b"
                kind="move",
                name=event.args[1],
                target=target_slot,
            )
            slots_that_moved.add(slot)

        elif event.kind == "switch" and len(event.args) >= 2:
            slot = _slot_id(event.args[0])
            if slot not in my_slots or slot in actions or slot in slots_that_moved:
                # Already acted, or this is a pivot follow-up — skip
                continue
            actions[slot[2]] = SlotAction(
                kind="switch",
                name=_species(event.args[1]),
            )

    return actions


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_examples(log: BattleLog) -> List[TurnExample]:
    """Extract per-turn training examples from one battle log.

    Returns two groups of TurnExamples (one per side), so every downloaded
    log contributes training signal for both the winning and the losing player.
    """
    winner = _winner_side(log)
    examples: List[TurnExample] = []
    turn_groups = _group_turns(log.events)

    for side in ("p1", "p2"):
        outcome_str, outcome_w = _outcome(side, winner)
        tracker = StateTracker(side)

        for turn_num, turn_event, body_events in turn_groups:
            # Turn 0: pre-battle events (team preview, initial switch-ins).
            # Consume to seed the tracker; never emit a TurnExample.
            if turn_event is None:
                for event in body_events:
                    tracker.consume(event)
                continue

            # Advance the tracker's turn counter, THEN snapshot.
            # This ensures state.turn == turn_num at snapshot time.
            tracker.consume(turn_event)
            state_before = tracker.snapshot()

            # Consume the rest of the turn's events to advance HP, status, etc.
            for event in body_events:
                tracker.consume(event)

            # Extract what this side decided during the turn
            my_actions = _actions_for_side(body_events, side)

            # Only emit an example if at least one slot made a decision
            if not my_actions:
                continue

            examples.append(TurnExample(
                battle_id=log.battle_id,
                side=side,
                turn=turn_num,
                state=state_before,
                actions=my_actions,
                outcome=outcome_str,
                outcome_weight=outcome_w,
            ))

    return examples


def extract_examples_from_dir(
    log_dirs: "str | Path | list[str | Path]",
    *,
    recursive: bool = False,
    verbose: bool = False,
) -> List[TurnExample]:
    """Extract TurnExamples from battle log JSON files.

    Parameters
    ----------
    log_dirs:
        A single directory path or a list of directory paths.  Combine rating
        buckets by passing multiple paths::

            # 1500+ tier only
            extract_examples_from_dir("logs/1500")

            # 1250+ tier (mid + high)
            extract_examples_from_dir(["logs/1250", "logs/1500"])

            # all data
            extract_examples_from_dir("logs", recursive=True)

    recursive:
        If True, glob ``**/*.json`` so subdirectories (rating buckets) are
        included automatically.  Ignored when ``log_dirs`` is a list.
    verbose:
        Print a line for each excluded or unreadable file.
    """
    from .filters import validate_log

    if isinstance(log_dirs, (str, Path)):
        dirs = [Path(log_dirs)]
    else:
        dirs = [Path(d) for d in log_dirs]

    # Collect all JSON paths, deduplicating by absolute path (in case dirs overlap)
    seen_paths: set[Path] = set()
    json_paths: List[Path] = []
    for d in dirs:
        pattern = "**/*.json" if (recursive and isinstance(log_dirs, (str, Path))) else "*.json"
        for p in sorted(d.glob(pattern)):
            abs_p = p.resolve()
            if abs_p not in seen_paths:
                seen_paths.add(abs_p)
                json_paths.append(p)

    all_examples: List[TurnExample] = []

    for path in json_paths:
        try:
            log = load_showdown_log_json(path)
        except Exception as exc:
            if verbose:
                print(f"Parse error {path.name}: {exc}")
            continue

        valid, reason = validate_log(log)
        if not valid:
            if verbose:
                print(f"Excluded {path.name}: {reason}")
            continue

        all_examples.extend(extract_examples(log))

    return all_examples
