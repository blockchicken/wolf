"""Run N battles and report timing.  Supports serial and parallel modes.

Usage:
    python stress_test.py                        # 100 battles, 1 worker
    python stress_test.py --workers 4            # 100 battles across 4 workers
    python stress_test.py --battles 50 --handler random
    python stress_test.py --profile --battles 10 # cProfile dump → profile.pstats
"""

from __future__ import annotations

import argparse
import multiprocessing
import random
import time
from dataclasses import dataclass
from pathlib import Path

SHOWDOWN_PATH  = Path(r"c:\Users\Arie\pokemon-showdown")
FORMAT         = "gen9championsvgc2026regma"
MODEL_PATH     = Path("checkpoints/policy.pt")
VOCAB_DIR      = Path("checkpoints/vocab")
PIKALYTICS_DIR = Path("data/pikalytics")


# ---------------------------------------------------------------------------
# Per-battle worker (runs in its own process when --workers > 1)
# ---------------------------------------------------------------------------

@dataclass
class BattleRecord:
    battle_num: int
    winner: str
    turns: int
    elapsed: float


# Worker-process globals — loaded once per process by _worker_init().
_worker_metagame = None
_worker_runner   = None
_worker_model    = None
_worker_vocab    = None
_worker_handler  = None


def _worker_init(handler_kind: str) -> None:
    """Called once per Pool worker process to load heavy resources."""
    global _worker_metagame, _worker_runner, _worker_model, _worker_vocab, _worker_handler

    from showdown_ai.battle_runner import BattleRunner
    from showdown_ai.pikalytics import load_metagame

    _worker_metagame = load_metagame(PIKALYTICS_DIR, FORMAT)
    _worker_runner   = BattleRunner(SHOWDOWN_PATH, format_id=FORMAT)
    _worker_handler  = handler_kind

    if handler_kind == "ai":
        from showdown_ai.model_handler import ModelDecisionHandler
        # Load model + vocab once and reuse handles across battles.
        from showdown_ai.model import BattlePolicy
        from showdown_ai.vocab import BattleVocab
        _worker_model = BattlePolicy.load(MODEL_PATH)
        _worker_vocab = BattleVocab.load(VOCAB_DIR)


def _run_one_battle(args: tuple) -> BattleRecord:
    """Run a single battle using the process-local resources loaded by _worker_init.

    Retries up to 3× when the Node subprocess crashes (turns=0, no winner),
    which can happen transiently under parallel load on Windows.
    """
    battle_num, seed = args

    from showdown_ai.battle_runner import RandomDecisionHandler
    from showdown_ai.pikalytics import generate_team, team_to_packed

    rng     = random.Random(seed)
    team_p1 = team_to_packed(generate_team(_worker_metagame, rng=rng))
    team_p2 = team_to_packed(generate_team(_worker_metagame, rng=rng))

    for attempt in range(3):
        if _worker_handler == "ai":
            from showdown_ai.model_handler import ModelDecisionHandler
            p1 = ModelDecisionHandler(_worker_model, _worker_vocab, side="p1")
            p2 = ModelDecisionHandler(_worker_model, _worker_vocab, side="p2")
        else:
            p1 = RandomDecisionHandler(seed=rng.randint(0, 2**31))
            p2 = RandomDecisionHandler(seed=rng.randint(0, 2**31))

        t0 = time.perf_counter()
        result, _ = _worker_runner.run_battle(team_p1, team_p2, p1, p2)
        elapsed = time.perf_counter() - t0

        if result.turns == 0 and result.winner is None and attempt < 2:
            time.sleep(0.5 * (attempt + 1))
            continue
        break

    return BattleRecord(
        battle_num=battle_num,
        winner=result.winner or "tie",
        turns=result.turns,
        elapsed=elapsed,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--battles",  type=int, default=100)
    parser.add_argument("--workers",  type=int, default=1,
                        help="parallel worker processes (default 1 = serial)")
    parser.add_argument("--handler",  choices=["ai", "random"], default="ai")
    parser.add_argument("--seed",     type=int, default=0)
    parser.add_argument("--profile",  action="store_true",
                        help="write cProfile data to profile.pstats (serial only)")
    args = parser.parse_args()

    if args.workers > 1 and args.profile:
        print("--profile is only supported in serial mode (--workers 1)")
        return

    # Each battle gets a unique seed derived from the master seed
    master_rng  = random.Random(args.seed)
    battle_args = [
        (i + 1, master_rng.randint(0, 2**32 - 1))
        for i in range(args.battles)
    ]

    wins   = {"p1": 0, "p2": 0, "tie": 0}
    times: list[float] = []
    mode = f"{args.workers} workers" if args.workers > 1 else "serial"
    print(f"Running {args.battles} battles ({args.handler} vs {args.handler}, {mode}) ...\n")

    total_start = time.perf_counter()

    def _handle_record(rec: BattleRecord) -> None:
        wins[rec.winner] = wins.get(rec.winner, 0) + 1
        times.append(rec.elapsed)
        print(
            f"[{rec.battle_num:>3}/{args.battles}] winner={rec.winner:<4}  "
            f"turns={rec.turns:<3}  {rec.elapsed:.2f}s  "
            f"(p1 {wins['p1']} / p2 {wins['p2']} / tie {wins['tie']})"
        )

    if args.workers <= 1:
        # Serial: init once in this process.
        _worker_init(args.handler)
        if args.profile:
            import cProfile
            pr = cProfile.Profile()
            pr.enable()
        for a in battle_args:
            _handle_record(_run_one_battle(a))
        if args.profile:
            pr.disable()
            pr.dump_stats("profile.pstats")
            print("Profile written to profile.pstats")
    else:
        # Parallel: each worker process calls _worker_init once on startup.
        with multiprocessing.Pool(
            processes=args.workers,
            initializer=_worker_init,
            initargs=(args.handler,),
        ) as pool:
            for rec in pool.imap_unordered(_run_one_battle, battle_args):
                _handle_record(rec)

    total     = time.perf_counter() - total_start
    wall_per  = total / args.battles
    avg       = sum(times) / len(times)

    print(f"\n{'='*60}")
    print(f"  Battles      : {args.battles}  ({mode})")
    print(f"  Wall time    : {total:.1f}s  ({wall_per:.2f}s per battle)")
    print(f"  Per-battle   : avg {avg:.2f}s  min {min(times):.2f}s  max {max(times):.2f}s")
    if args.workers > 1:
        serial_est = avg * args.battles
        print(f"  Serial est.  : {serial_est:.1f}s  ->  {serial_est/total:.1f}x speedup")
    print(f"  Results      : p1={wins['p1']}  p2={wins['p2']}  tie={wins.get('tie', 0)}")
    print(f"  P1 win rate  : {100*wins['p1']/args.battles:.1f}%")
    print(f"{'='*60}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
