from showdown_ai import StateTracker, load_showdown_log_json, split_perspective_logs


def test_can_parse_uploaded_log():
    battle = load_showdown_log_json("gen9vgc2024regg-2194787570.json")
    assert battle.format_name == "[Gen 9] VGC 2024 Reg G"
    assert len(battle.events) > 100


def test_split_perspectives_and_replay_state():
    battle = load_showdown_log_json("gen9vgc2024regg-2194787570.json")
    views = split_perspective_logs(battle.events)
    assert views["p1"]
    assert views["p2"]

    p1 = StateTracker("p1").consume_all(views["p1"])
    p2 = StateTracker("p2").consume_all(views["p2"])

    assert p1[-1].winner in battle.players
    assert p2[-1].winner == p1[-1].winner
