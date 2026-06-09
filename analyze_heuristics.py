"""Empirical heuristic analysis from high-rated battle logs.

Computes three metrics to inform inference-time decision heuristics:

  1. Double-protect rate:
       P(use protect again | used protect last turn, still on field, not under Encore)

  2. Voluntary switch rate:
       P(switch | normal action turn, at least one alive bench Pokemon available)

  3. First-turn Fake Out / First Impression rate:
       P(Fake Out/FI used on 1st turn | Pokemon is a known Fake-Out species and is on its 1st turn)
       Species list: Incineroar, Sneasler, Weavile, Lopunny(-Mega),
                     Kangaskhan(-Mega), Tinkaton, Raichu

Usage::

    python analyze_heuristics.py                  # 1500 bucket (default)
    python analyze_heuristics.py --bucket 1250
    python analyze_heuristics.py --bucket all
"""

from __future__ import annotations

import argparse
from pathlib import Path

from showdown_ai.logs import load_showdown_log_json
from showdown_ai.training_data import extract_examples

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PROTECT_MOVES: frozenset[str] = frozenset({
    "Protect", "Detect", "King's Shield", "Spiky Shield", "Baneful Bunker",
    "Obstruct", "Silk Trap", "Wide Guard", "Mat Block", "Quick Guard",
    "Max Guard", "Crafty Shield",
})

_FIRST_TURN_MOVES: frozenset[str] = frozenset({
    "Fake Out", "First Impression",
})

# Species with ~90% Fake Out / First Impression in their standard moveset.
_FAKE_OUT_SPECIES: frozenset[str] = frozenset({
    "Incineroar",
    "Sneasler",
    "Weavile",
    "Lopunny", "Lopunny-Mega",
    "Kangaskhan", "Kangaskhan-Mega",
    "Tinkaton",
    "Raichu",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_first_turn(state, slot_id: str) -> bool:
    return (
        slot_id in state.entry_turn
        and state.turn - state.entry_turn[slot_id] == 1
    )


def _has_viable_switch(state, side: str) -> bool:
    """True if there is at least one alive, benched Pokemon available to switch in."""
    active  = {state.active_species.get(f"{side}a"),
               state.active_species.get(f"{side}b")} - {None}
    fainted = {state.active_species.get(s)
               for s in state.fainted_slots if s.startswith(side)} - {None}
    return any(
        sp and sp not in active and sp not in fainted
        for sp in state.my_team
    )


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

def analyze(log_files: list[Path], verbose: bool = False) -> None:
    # ---- Metric 1: double protect ----
    dp_denom = 0
    dp_numer = 0

    # ---- Metric 2: voluntary switch rate (only when a switch is possible) ----
    sw_eligible = 0   # turns where a switch was actually available
    sw_switch   = 0
    sw_move     = 0

    # ---- Metric 3: Fake Out species on first turn ----
    fo_denom  = 0   # first turns where the active Pokemon is a Fake Out species
    fo_numer  = 0   # ...and they used a first-turn move
    fo_missed = 0   # ...and they used something else

    errors     = 0
    battles    = 0
    n_examples = 0

    for f in log_files:
        try:
            log     = load_showdown_log_json(f)
            ex_list = extract_examples(log)
        except Exception as e:
            errors += 1
            if verbose:
                print(f"  skip {f.name}: {e}")
            continue

        battles    += 1
        n_examples += len(ex_list)

        for ex in ex_list:
            state = ex.state
            side  = ex.side

            # Pre-compute switch availability once per example (same for both slots)
            switch_available = _has_viable_switch(state, side)

            for slot_suffix, action in ex.actions.items():
                slot_id = f"{side}{slot_suffix}"
                species = state.active_species.get(slot_id, "")

                # ---- Metric 1: double protect ----
                if slot_id in state.protect_last_turn:
                    vs = state.volatile_status.get(slot_id, set())
                    if "encore" not in vs:
                        dp_denom += 1
                        if action.kind == "move" and action.name in _PROTECT_MOVES:
                            dp_numer += 1

                # ---- Metric 2: voluntary switch rate ----
                if switch_available:
                    sw_eligible += 1
                    if action.kind == "switch":
                        sw_switch += 1
                    elif action.kind == "move":
                        sw_move += 1

                # ---- Metric 3: Fake Out species on first turn ----
                if species in _FAKE_OUT_SPECIES and _is_first_turn(state, slot_id):
                    fo_denom += 1
                    if action.kind == "move" and action.name in _FIRST_TURN_MOVES:
                        fo_numer += 1
                    else:
                        fo_missed += 1

    # ---------------------------------------------------------------------------
    # Report
    # ---------------------------------------------------------------------------
    print(f"\n{'='*62}")
    print(f"  BATTLE HEURISTICS ANALYSIS")
    print(f"{'='*62}")
    print(f"  Battles processed  : {battles:,}  ({errors} skipped)")
    print(f"  Total examples     : {n_examples:,}")

    print(f"\n--- 1. Double-Protect Rate ---")
    if dp_denom:
        pct = 100.0 * dp_numer / dp_denom
        print(f"  Opportunities (used protect last turn, on field, no Encore) : {dp_denom:,}")
        print(f"  Used protect again  : {dp_numer:,}  ({pct:.1f}%)")
        print(f"  Chose another move  : {dp_denom - dp_numer:,}  ({100-pct:.1f}%)")
        print(f"\n  => Recommended _DOUBLE_PROTECT_ALLOW_PROB = {pct/100:.2f}")
    else:
        print("  No data.")

    print(f"\n--- 2. Voluntary Switch Rate (turns where switch was possible) ---")
    if sw_eligible:
        pct_sw = 100.0 * sw_switch / sw_eligible
        pct_mv = 100.0 * sw_move   / sw_eligible
        other  = sw_eligible - sw_switch - sw_move
        print(f"  Eligible turns (alive bench Pokemon exists) : {sw_eligible:,}")
        print(f"  Move actions   : {sw_move:,}  ({pct_mv:.1f}%)")
        print(f"  Switch actions : {sw_switch:,}  ({pct_sw:.1f}%)")
        if other:
            print(f"  Other          : {other:,}")
    else:
        print("  No data.")

    print(f"\n--- 3. Fake Out / First Impression on First Turn ---")
    print(f"  Species: {', '.join(sorted(_FAKE_OUT_SPECIES))}")
    if fo_denom:
        pct_fo   = 100.0 * fo_numer  / fo_denom
        pct_miss = 100.0 * fo_missed / fo_denom
        print(f"  First-turn observations for these species : {fo_denom:,}")
        print(f"  Used Fake Out / First Impression : {fo_numer:,}  ({pct_fo:.1f}%)")
        print(f"  Used a different move            : {fo_missed:,}  ({pct_miss:.1f}%)")
        print(f"\n  => Recommended _FIRST_TURN_MOVE_PROB = {pct_fo/100:.2f}")
    else:
        print("  No data.")

    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze battle heuristics.")
    parser.add_argument(
        "--bucket",
        default="1500",
        choices=["1250", "1500", "all"],
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
    print("Analyzing... (this may take a minute or two)")

    analyze(log_files, verbose=args.verbose)


if __name__ == "__main__":
    main()
