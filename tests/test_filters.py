"""Tests for battle log filters."""

import json
import tempfile
from pathlib import Path

import pytest

from showdown_ai import validate_log, filter_logs, load_showdown_log_json
from showdown_ai.filters import _has_complete_first_turn
from showdown_ai.logs import parse_protocol, BattleLog


# ---------------------------------------------------------------------------
# Helpers to build synthetic battle logs for testing
# ---------------------------------------------------------------------------

def _make_log(log_lines: str, *, battle_id: str = "test-1") -> BattleLog:
    """Build a BattleLog from a newline-separated protocol string."""
    payload = {
        "id": battle_id,
        "format": "[Gen 9 Champions] VGC 2026 Reg M-A",
        "players": ["PlayerA", "PlayerB"],
        "log": log_lines,
    }
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(payload, f)
        tmp = Path(f.name)
    log = load_showdown_log_json(tmp)
    tmp.unlink()
    return log


_BASE_PREVIEW = """\
|player|p1|PlayerA||1200
|player|p2|PlayerB||1100
|gen|9
|tier|[Gen 9 Champions] VGC 2026 Reg M-A
|clearpoke
|poke|p1|Incineroar, L50, M|
|poke|p1|Garchomp, L50, F|
|poke|p1|Pelipper, L50, F|
|poke|p1|Milotic, L50, M|
|poke|p1|Corviknight, L50, F|
|poke|p1|Gardevoir, L50, M|
|poke|p2|Charizard, L50, F|
|poke|p2|Pikachu, L50, M|
|poke|p2|Alakazam, L50, F|
|poke|p2|Machamp, L50, F|
|poke|p2|Lapras, L50, M|
|poke|p2|Raichu, L50, M|
|teampreview|4
|teamsize|p1|4
|teamsize|p2|4
|start
|switch|p1a: Incineroar|Incineroar, L50, M|100/100
|switch|p1b: Garchomp|Garchomp, L50, F|100/100
|switch|p2a: Charizard|Charizard, L50, F|100/100
|switch|p2b: Pikachu|Pikachu, L50, M|100/100"""

_TURN1_MOVE = """\
|turn|1
|move|p1a: Incineroar|Fake Out|p2a: Charizard
|-damage|p2a: Charizard|87/100
|move|p1b: Garchomp|Earthquake|p2a: Charizard
|-damage|p2a: Charizard|42/100
|move|p2a: Charizard|Flamethrower|p1b: Garchomp
|-damage|p1b: Garchomp|68/100
|move|p2b: Pikachu|Thunderbolt|p1a: Incineroar
|-damage|p1a: Incineroar|79/100
|upkeep
|win|PlayerA"""

_VALID_LOG = f"{_BASE_PREVIEW}\n{_TURN1_MOVE}"


# ---------------------------------------------------------------------------
# Valid log
# ---------------------------------------------------------------------------

def test_valid_log_passes():
    log = _make_log(_VALID_LOG)
    valid, reason = validate_log(log)
    assert valid is True
    assert reason is None


# ---------------------------------------------------------------------------
# Illusion (Zoroark / Zorua and Hisuian forms)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("species", [
    "Zoroark",
    "Zoroark-Hisui",
    "Zorua",
    "Zorua-Hisui",
])
def test_illusion_user_excluded(species):
    log_str = _BASE_PREVIEW.replace(
        "|poke|p2|Lapras, L50, M|",
        f"|poke|p2|{species}, L50, M|",
    ) + f"\n{_TURN1_MOVE}"
    log = _make_log(log_str)
    valid, reason = validate_log(log)
    assert valid is False
    assert reason == "illusion"


# ---------------------------------------------------------------------------
# Transform / Imposter (Ditto)
# ---------------------------------------------------------------------------

def test_ditto_excluded():
    log_str = _BASE_PREVIEW.replace(
        "|poke|p2|Lapras, L50, M|",
        "|poke|p2|Ditto, L50|",
    ) + f"\n{_TURN1_MOVE}"
    log = _make_log(log_str)
    valid, reason = validate_log(log)
    assert valid is False
    assert reason == "transform"


def test_transform_move_excluded():
    # Mew (or any Pokémon) uses the Transform move
    log_str = (
        _BASE_PREVIEW
        + "\n|turn|1"
        + "\n|move|p1a: Incineroar|Transform|p2a: Charizard"
        + "\n|win|PlayerB"
    )
    log = _make_log(log_str)
    valid, reason = validate_log(log)
    assert valid is False
    assert reason == "transform"


# ---------------------------------------------------------------------------
# Imprison
# ---------------------------------------------------------------------------

def test_imprison_excluded():
    log_str = (
        _BASE_PREVIEW
        + "\n|turn|1"
        + "\n|move|p1a: Incineroar|Imprison|p1a: Incineroar"
        + "\n|win|PlayerB"
    )
    log = _make_log(log_str)
    valid, reason = validate_log(log)
    assert valid is False
    assert reason == "imprison"


# ---------------------------------------------------------------------------
# Incomplete battles (forfeit / disconnect before any moves)
# ---------------------------------------------------------------------------

def test_forfeit_before_any_turn_excluded():
    # Battle ends with |win| before |turn|1 appears
    log_str = _BASE_PREVIEW + "\n|win|PlayerB"
    log = _make_log(log_str)
    valid, reason = validate_log(log)
    assert valid is False
    assert reason == "incomplete"


def test_forfeit_after_turn1_but_no_moves_excluded():
    # |turn|1 appears but no |move| events — player forfeited immediately
    log_str = _BASE_PREVIEW + "\n|turn|1\n|win|PlayerB"
    log = _make_log(log_str)
    valid, reason = validate_log(log)
    assert valid is False
    assert reason == "incomplete"


def test_forfeit_after_moves_is_valid():
    # Turn 1 had moves before the forfeit — this is valid training data
    log_str = (
        _BASE_PREVIEW
        + "\n|turn|1"
        + "\n|move|p1a: Incineroar|Fake Out|p2a: Charizard"
        + "\n|-damage|p2a: Charizard|87/100"
        + "\n|win|PlayerA"   # forfeit after one move, still valid
    )
    log = _make_log(log_str)
    valid, reason = validate_log(log)
    assert valid is True


# ---------------------------------------------------------------------------
# filter_logs helper
# ---------------------------------------------------------------------------

def test_filter_logs_removes_invalid():
    valid_log   = _make_log(_VALID_LOG, battle_id="valid-1")
    illusion_log = _make_log(
        _BASE_PREVIEW.replace("|poke|p2|Lapras, L50, M|", "|poke|p2|Zoroark, L50, M|")
        + f"\n{_TURN1_MOVE}",
        battle_id="illusion-1",
    )
    incomplete_log = _make_log(
        _BASE_PREVIEW + "\n|win|PlayerB",
        battle_id="incomplete-1",
    )

    kept = filter_logs([valid_log, illusion_log, incomplete_log])
    assert len(kept) == 1
    assert kept[0].battle_id == "valid-1"


def test_filter_logs_verbose(capsys):
    illusion_log = _make_log(
        _BASE_PREVIEW.replace("|poke|p2|Lapras, L50, M|", "|poke|p2|Zorua-Hisui, L50|")
        + f"\n{_TURN1_MOVE}",
        battle_id="illusion-verbose",
    )
    filter_logs([illusion_log], verbose=True)
    captured = capsys.readouterr()
    assert "illusion-verbose" in captured.out
    assert "illusion" in captured.out


# ---------------------------------------------------------------------------
# Real Champions log should pass (confirm no false-positives on real data)
# ---------------------------------------------------------------------------

def test_real_champions_log_passes():
    real_log_path = (
        Path(__file__).parent.parent
        / "downloaded_logs"
        / "gen9championsvgc2026regma"
        / "gen9championsvgc2026regma-2594986055.json"
    )
    log = load_showdown_log_json(real_log_path)
    valid, reason = validate_log(log)
    assert valid is True, f"Real log unexpectedly excluded: {reason}"
