from pathlib import Path
from showdown_ai import (
    StateTracker,
    load_showdown_log_json,
    split_perspective_logs,
    extract_examples,
)

LOG_DIR = Path(__file__).parent.parent / "downloaded_logs"
CHAMP_LOG = LOG_DIR / "gen9championsvgc2026regma" / "gen9championsvgc2026regma-2594986055.json"


def test_can_parse_champions_log():
    battle = load_showdown_log_json(CHAMP_LOG)
    assert battle.format_name == "[Gen 9 Champions] VGC 2026 Reg M-A"
    assert len(battle.events) > 50
    assert len(battle.players) == 2


def test_split_perspectives_and_replay_state():
    battle = load_showdown_log_json(CHAMP_LOG)
    views = split_perspective_logs(battle.events)

    assert views["p1"]
    assert views["p2"]
    # Both perspectives receive the same public event stream
    assert len(views["p1"]) == len(views["p2"])

    p1_states = StateTracker("p1").consume_all(views["p1"])
    p2_states = StateTracker("p2").consume_all(views["p2"])

    assert p1_states[-1].winner in battle.players
    assert p2_states[-1].winner == p1_states[-1].winner


def test_state_tracker_tracks_both_active_slots():
    """Both p1a and p1b are tracked after doubles switch-ins."""
    battle = load_showdown_log_json(CHAMP_LOG)
    tracker = StateTracker("p1")
    for event in battle.events:
        tracker.consume(event)
    state = tracker.snapshot()

    # Champions VGC is doubles — both slots should have been filled
    slots_seen = set(state.active_species.keys())
    assert "p1a" in slots_seen
    assert "p1b" in slots_seen
    assert "p2a" in slots_seen
    assert "p2b" in slots_seen


def test_state_tracker_records_team_preview():
    battle = load_showdown_log_json(CHAMP_LOG)
    tracker = StateTracker("p1")
    for event in battle.events:
        tracker.consume(event)
    state = tracker.snapshot()

    # VGC brings 4 of 6 — team preview lists 6 but teamsize limits to 4
    assert len(state.my_team) > 0
    assert len(state.opp_team) > 0


def test_extract_examples_produces_two_perspectives():
    """Each log yields examples for both p1 and p2."""
    battle = load_showdown_log_json(CHAMP_LOG)
    examples = extract_examples(battle)

    sides = {ex.side for ex in examples}
    assert "p1" in sides
    assert "p2" in sides


def test_extract_examples_doubles_actions():
    """At least some turns should have actions for both active slots (a and b)."""
    battle = load_showdown_log_json(CHAMP_LOG)
    examples = extract_examples(battle)

    # Find examples where both slots acted
    both_slots = [ex for ex in examples if "a" in ex.actions and "b" in ex.actions]
    assert len(both_slots) > 0, "Expected at least one turn with two slot actions"


def test_extract_examples_outcome_weighting():
    """Winners get weight 1.0, losers 0.5, ties 0.25."""
    battle = load_showdown_log_json(CHAMP_LOG)
    examples = extract_examples(battle)

    weights = {ex.outcome: ex.outcome_weight for ex in examples}
    if "win" in weights:
        assert weights["win"] == 1.0
    if "loss" in weights:
        assert weights["loss"] == 0.5
    if "tie" in weights:
        assert weights["tie"] == 0.25


def test_extract_examples_state_has_both_teams():
    """Pre-turn state should carry both teams from team preview."""
    battle = load_showdown_log_json(CHAMP_LOG)
    examples = extract_examples(battle)

    for ex in examples[:5]:  # spot-check early examples
        assert len(ex.state.my_team) > 0
        assert len(ex.state.opp_team) > 0


def test_extract_examples_actions_are_moves_or_switches():
    battle = load_showdown_log_json(CHAMP_LOG)
    examples = extract_examples(battle)

    for ex in examples:
        for suffix, action in ex.actions.items():
            assert suffix in ("a", "b")
            assert action.kind in ("move", "switch")
            assert action.name  # never empty


def test_state_tracks_weather():
    """Rain from Pelipper's Drizzle should be recorded in state."""
    battle = load_showdown_log_json(CHAMP_LOG)
    tracker = StateTracker("p1")
    for event in battle.events:
        tracker.consume(event)
        s = tracker.snapshot()
        if s.weather is not None:
            break  # weather was set at some point
    # This log has Pelipper with Drizzle, so rain appears
    all_states = StateTracker("p1").consume_all(list(battle.events))
    weathers_seen = {s.weather for s in all_states if s.weather}
    assert weathers_seen, "Expected weather to be tracked during the battle"
