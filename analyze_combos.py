"""Analyze coordinated doubles plays from high-rated battle logs.

Metrics
-------
  A  Redirect + Setup
       One slot uses Follow Me/Rage Powder; partner uses a setup/field move.
       Frequency and battle-level win rate vs. overall baseline.

  B  Fake Out / First Impression pairing
       One slot uses Fake Out/FI; what does the partner do?

  C  Field condition timing
       Tailwind / Trick Room / Screens used when condition already active vs. not.
       Win rate comparison — shows whether redundant use correlates with loss.

Usage::

    python analyze_combos.py                  # 1500 bucket (default)
    python analyze_combos.py --bucket 1250
    python analyze_combos.py --bucket all
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

from showdown_ai.logs import load_showdown_log_json
from showdown_ai.training_data import extract_examples

# ---------------------------------------------------------------------------
# Move categories
# ---------------------------------------------------------------------------

_REDIRECT_MOVES: frozenset[str] = frozenset({"Follow Me", "Rage Powder"})

_STAT_SETUP_MOVES: frozenset[str] = frozenset({
    "Calm Mind", "Swords Dance", "Nasty Plot", "Dragon Dance", "Quiver Dance",
    "Iron Defense", "Amnesia", "Bulk Up", "Coil", "Agility", "Autotomize",
    "Hone Claws", "Work Up", "Clangorous Soul", "Shell Smash", "Geomancy",
    "Victory Dance", "Torch Song",
})

_FIELD_SETUP_MOVES: frozenset[str] = frozenset({
    "Tailwind", "Trick Room",
    "Reflect", "Light Screen", "Aurora Veil",
    "Rain Dance", "Sunny Day", "Sandstorm", "Snowscape", "Hail",
    "Electric Terrain", "Grassy Terrain", "Misty Terrain", "Psychic Terrain",
})

_PROTECT_MOVES: frozenset[str] = frozenset({
    "Protect", "Detect", "King's Shield", "Spiky Shield", "Baneful Bunker",
    "Obstruct", "Silk Trap", "Wide Guard", "Mat Block", "Quick Guard",
    "Max Guard", "Crafty Shield",
})

_FIRST_TURN_MOVES: frozenset[str] = frozenset({"Fake Out", "First Impression"})

# Union used for "redirect + any helpful move" check
_SETUP_AND_FIELD: frozenset[str] = _STAT_SETUP_MOVES | _FIELD_SETUP_MOVES


def _action_category(action) -> str:
    if action.kind == "switch":
        return "switch"
    n = action.name
    if n in _PROTECT_MOVES:
        return "protect"
    if n in _STAT_SETUP_MOVES:
        return "stat-boost setup"
    if n in _FIELD_SETUP_MOVES:
        return "field setup"
    if n in _REDIRECT_MOVES:
        return "redirect"
    if n in _FIRST_TURN_MOVES:
        return "fake-out"
    return "attack/other"


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(log_files: list[Path], verbose: bool = False) -> None:
    # A: Redirect + Setup
    # battle_key (battle_id:side) → outcome, collected only for battles where
    # the combo occurred at least once.
    redirect_setup_battles: dict[str, str] = {}
    all_battles:            dict[str, str] = {}
    redirect_setup_turn_count = 0
    redirect_partner_move: dict[str, int] = defaultdict(int)

    # B: Fake Out pairing
    fakeout_total = 0
    fakeout_partner_cat: dict[str, int] = defaultdict(int)

    # C: Field condition timing — {move_name: {"active": [wins,total], "inactive": [wins,total]}}
    _FIELD_TIMING_MOVES = ["Tailwind", "Trick Room", "Reflect", "Light Screen", "Aurora Veil"]
    field_timing: dict[str, dict[str, list[int]]] = {
        m: {"active": [0, 0], "inactive": [0, 0]} for m in _FIELD_TIMING_MOVES
    }

    errors  = 0
    battles = 0
    n_turns_both = 0

    for f in log_files:
        try:
            log     = load_showdown_log_json(f)
            ex_list = extract_examples(log)
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  skip {f.name}: {e}")
            continue

        battles += 1

        for ex in ex_list:
            battle_key = f"{ex.battle_id}:{ex.side}"
            all_battles[battle_key] = ex.outcome

            a_act = ex.actions.get("a")
            b_act = ex.actions.get("b")

            state = ex.state
            my_sc = state.side_conditions.get(ex.side, {})
            is_win = ex.outcome == "win"

            # ---- C: field condition timing (single-slot; no need for both slots) ----
            for act in filter(None, [a_act, b_act]):
                if act.kind != "move" or act.name not in _FIELD_TIMING_MOVES:
                    continue
                mn = act.name
                if mn == "Tailwind":
                    is_active = ex.side in state.tailwind
                elif mn == "Trick Room":
                    is_active = state.trick_room
                elif mn == "Reflect":
                    is_active = bool(my_sc.get("reflect"))
                elif mn == "Light Screen":
                    is_active = bool(my_sc.get("lightscreen"))
                elif mn == "Aurora Veil":
                    is_active = bool(my_sc.get("auroraveil"))
                else:
                    continue
                bucket = "active" if is_active else "inactive"
                field_timing[mn][bucket][0] += 1 if is_win else 0
                field_timing[mn][bucket][1] += 1

            # Need both slots for A and B
            if not (a_act and b_act):
                continue

            n_turns_both += 1

            # ---- A: Redirect + Setup ----
            for supporter, beneficiary in [(a_act, b_act), (b_act, a_act)]:
                if supporter.kind == "move" and supporter.name in _REDIRECT_MOVES:
                    if beneficiary.kind == "move" and beneficiary.name in _SETUP_AND_FIELD:
                        redirect_setup_turn_count += 1
                        redirect_partner_move[beneficiary.name] += 1
                        redirect_setup_battles[battle_key] = ex.outcome
                        break

            # ---- B: Fake Out pairing ----
            for act1, act2 in [(a_act, b_act), (b_act, a_act)]:
                if act1.kind == "move" and act1.name in _FIRST_TURN_MOVES:
                    fakeout_total += 1
                    fakeout_partner_cat[_action_category(act2)] += 1
                    break

    # ---------------------------------------------------------------------------
    # Report
    # ---------------------------------------------------------------------------
    print(f"\n{'='*64}")
    print(f"  COORDINATED DOUBLES PLAY ANALYSIS")
    print(f"{'='*64}")
    print(f"  Log files processed     : {battles:,}  ({errors} skipped)")
    print(f"  Unique battle-sides     : {len(all_battles):,}")
    print(f"  Turns with both slots   : {n_turns_both:,}")

    overall_wr = sum(1 for v in all_battles.values() if v == "win") / max(len(all_battles), 1)
    print(f"  Overall win rate        : {100*overall_wr:.1f}%")

    # A
    print(f"\n--- A. Redirect + Setup (Follow Me / Rage Powder → partner uses setup/field move) ---")
    rs_battles = len(redirect_setup_battles)
    rs_wins    = sum(1 for v in redirect_setup_battles.values() if v == "win")
    pct_battles = 100 * rs_battles / max(len(all_battles), 1)
    print(f"  Turn occurrences        : {redirect_setup_turn_count:,}")
    print(f"  Battle-sides that used it: {rs_battles:,}  ({pct_battles:.1f}% of all battles)")
    if rs_battles:
        print(f"  Win rate when used      : {100*rs_wins/rs_battles:.1f}%  (baseline {100*overall_wr:.1f}%)")
    if redirect_partner_move:
        print(f"  Top partner setup moves :")
        total = sum(redirect_partner_move.values())
        for move, cnt in sorted(redirect_partner_move.items(), key=lambda x: -x[1])[:10]:
            print(f"    {move:<22}: {cnt:4d}  ({100*cnt/total:.1f}%)")

    # B
    print(f"\n--- B. Fake Out / First Impression — partner's action ---")
    print(f"  Fake Out turns (both slots active): {fakeout_total:,}")
    if fakeout_total:
        print(f"  Partner category breakdown:")
        for cat, cnt in sorted(fakeout_partner_cat.items(), key=lambda x: -x[1]):
            print(f"    {cat:<26}: {cnt:4d}  ({100*cnt/fakeout_total:.1f}%)")

    # C
    print(f"\n--- C. Field Condition Timing — win rate when used while already active vs. not ---")
    any_data = False
    for move_name in _FIELD_TIMING_MOVES:
        buckets = field_timing[move_name]
        act_w, act_t = buckets["active"]
        ina_w, ina_t = buckets["inactive"]
        if act_t + ina_t == 0:
            continue
        any_data = True
        ina_str = f"Not active → {100*ina_w/ina_t:.1f}% win ({ina_t:,} uses)" if ina_t else "No data"
        act_str = f"Already active → {100*act_w/act_t:.1f}% win ({act_t:,} uses)" if act_t else "No data"
        print(f"  {move_name:<14}  {ina_str}   {act_str}")
    if not any_data:
        print("  No field-condition move uses found.")

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze coordinated doubles plays.")
    parser.add_argument(
        "--bucket", default="1500", choices=["1250", "1500", "all"],
        help="Rating bucket to analyze (default: 1500)",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    log_dir = Path("downloaded_logs/gen9championsvgc2026regma") / args.bucket
    if not log_dir.exists():
        print(f"Log directory not found: {log_dir}")
        return

    log_files = sorted(log_dir.glob("*.json"))
    print(f"Found {len(log_files):,} log files in {log_dir}")
    print("Analyzing coordinated plays... (may take a minute)")
    analyze(log_files, verbose=args.verbose)


if __name__ == "__main__":
    main()
