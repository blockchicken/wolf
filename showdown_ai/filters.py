"""Filters for excluding battle logs that would corrupt training data.

Excluded categories
-------------------
illusion
    Zoroark, Zorua, and their Hisuian forms have the Illusion ability: they
    enter disguised as a different Pokémon and reveal their true species only
    when damaged.  In the public omniscient log the disguised name is shown
    for all moves until the |replace| event fires.  Any model trained on such
    a battle would learn incorrect species-move associations.

    All Zoroark/Zorua variants are caught at the team-preview |poke| stage
    where Showdown always lists the TRUE species regardless of Illusion.

transform
    Ditto (via Imposter ability) and any Pokémon using the Transform move copy
    the target's entire moveset.  Subsequent |move| events attribute the
    TARGET's moves to the Ditto/transformer, corrupting every move→species
    pairing downstream.  Ditto is caught from |poke| events (Imposter triggers
    automatically on switch-in, never appearing as a Transform move), and the
    Transform move is caught from |move| events.

imprison
    Imprison prevents the opponent from using any move the Imprison user knows.
    Because the opponent cannot see which moves are imprisoned, the observable
    state does not explain why certain moves become unavailable.  Training on
    such turns would teach the model that moves are randomly unusable.

incomplete
    Battles that end (forfeit, disconnect, timer loss) before at least one
    |move| event occurs inside turn 1 provide near-zero training signal and
    may reflect unusual player behaviour unrelated to game decisions.
"""

from __future__ import annotations

from typing import Optional

from .logs import BattleLog


# ---------------------------------------------------------------------------
# Species / move sets that trigger exclusion
# ---------------------------------------------------------------------------

# Prefixes covering all regional forms:
#   Zoroark, Zoroark-Hisui, Zorua, Zorua-Hisui
_ILLUSION_PREFIXES: frozenset[str] = frozenset({"Zoroark", "Zorua"})

# Exact species name (Imposter triggers without producing a |move| event)
_IMPOSTER_SPECIES: frozenset[str] = frozenset({"Ditto"})

# Moves that corrupt the training signal regardless of user
_EXCLUDED_MOVES: frozenset[str] = frozenset({"Transform", "Imprison"})


# ---------------------------------------------------------------------------
# Internal checkers
# ---------------------------------------------------------------------------

def _species_from_poke(arg: str) -> str:
    """'Zoroark-Hisui, L50, M' → 'Zoroark-Hisui'"""
    return arg.split(",")[0].strip()


def _team_species(log: BattleLog) -> list[str]:
    """All species listed in team-preview |poke| events (both teams)."""
    result = []
    for event in log.events:
        if event.kind == "poke" and len(event.args) >= 2:
            result.append(_species_from_poke(event.args[1]))
    return result


def _moves_used(log: BattleLog) -> list[str]:
    """All move names that appear in |move| events during the battle."""
    result = []
    for event in log.events:
        if event.kind == "move" and len(event.args) >= 2:
            result.append(event.args[1])
    return result


def _has_complete_first_turn(log: BattleLog) -> bool:
    """True iff turn 1 started AND at least one move was executed inside it.

    A |turn|1 without any subsequent |move| events indicates the battle ended
    (forfeit / disconnect / timer) before either player's choice resolved.
    """
    in_turn_1_or_later = False
    for event in log.events:
        if event.kind == "turn" and event.args:
            if int(event.args[0]) >= 1:
                in_turn_1_or_later = True
        elif event.kind == "move" and in_turn_1_or_later:
            return True  # at least one move executed in turn 1+
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

ExclusionReason = Optional[str]  # None means "valid"


def validate_log(log: BattleLog) -> tuple[bool, ExclusionReason]:
    """Check whether a battle log is suitable for training.

    Returns
    -------
    (True, None)
        The log is clean and should be included.
    (False, reason)
        The log should be excluded.  `reason` is one of:
        ``"illusion"``, ``"transform"``, ``"imprison"``, ``"incomplete"``.
    """
    species_list = _team_species(log)

    # --- Illusion users ---
    for sp in species_list:
        if any(sp.startswith(prefix) for prefix in _ILLUSION_PREFIXES):
            return False, "illusion"

    # --- Transform / Imposter users ---
    for sp in species_list:
        if sp in _IMPOSTER_SPECIES:
            return False, "transform"

    # --- Problematic moves ---
    for move in _moves_used(log):
        if move == "Transform":
            return False, "transform"
        if move == "Imprison":
            return False, "imprison"

    # --- Incomplete battle ---
    if not _has_complete_first_turn(log):
        return False, "incomplete"

    return True, None


def filter_logs(
    logs: list[BattleLog],
    *,
    verbose: bool = False,
) -> list[BattleLog]:
    """Return only logs that pass all filters.

    Parameters
    ----------
    logs:
        Battle logs to filter.
    verbose:
        If True, print one line per excluded log showing the reason.
    """
    kept = []
    for log in logs:
        valid, reason = validate_log(log)
        if valid:
            kept.append(log)
        elif verbose:
            print(f"Excluded {log.battle_id}: {reason}")
    return kept
