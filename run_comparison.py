"""Compare ModelDecisionHandler vs RandomDecisionHandler over 5 battles each.

Generates two teams from Pikalytics data, then runs:
  - 5 battles: AI model (p1) vs AI model (p2)
  - 5 battles: Random (p1) vs Random (p2)

All battle logs are saved to battle_logs/ for review.
"""
from __future__ import annotations

import random
from pathlib import Path

from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler
from showdown_ai.model_handler import ModelDecisionHandler
from showdown_ai.pikalytics import load_metagame, generate_team, team_to_packed

# --- Config ---
SHOWDOWN_PATH  = Path(r"c:\Users\Arie\pokemon-showdown")
FORMAT         = "gen9championsvgc2026regma"
MODEL_PATH     = Path("checkpoints/policy.pt")
VOCAB_DIR      = Path("checkpoints/vocab")
PIKALYTICS_DIR = Path("data/pikalytics")
LOG_DIR        = Path("battle_logs")
N_BATTLES      = 5


def make_teams(seed: int = 1) -> tuple[str, str]:
    mg  = load_metagame(PIKALYTICS_DIR, FORMAT)
    rng = random.Random(seed)
    team_a = team_to_packed(generate_team(mg, rng=rng))
    team_b = team_to_packed(generate_team(mg, rng=rng))
    return team_a, team_b


def show_team(packed: str, label: str) -> None:
    sets = packed.split("]")
    print(f"\n{label}:")
    for s in sets:
        parts = s.split("|")
        name  = parts[0] or parts[1]
        item  = parts[2]
        moves = parts[4].replace(",", " / ")
        print(f"  {name} @ {item}  [{moves}]")


def run_series(
    runner: BattleRunner,
    team_a: str,
    team_b: str,
    p1_handler_factory,
    p2_handler_factory,
    label: str,
    log_subdir: Path,
) -> list[dict]:
    log_subdir.mkdir(parents=True, exist_ok=True)
    results = []

    print(f"\n{'='*60}")
    print(f"{label}")
    print(f"{'='*60}")
    print(f"{'Battle':>7}  {'Winner':>12}  {'Turns':>6}  {'Log'}")
    print("-" * 60)

    for i in range(1, N_BATTLES + 1):
        log_path = log_subdir / f"battle_{i:02d}.log"
        p1 = p1_handler_factory(seed=100 + i)
        p2 = p2_handler_factory(seed=200 + i)

        result, _ = runner.run_battle(
            team_p1=team_a,
            team_p2=team_b,
            p1_handler=p1,
            p2_handler=p2,
            p1_name="TeamA",
            p2_name="TeamB",
            max_turns=200,
            log_file=str(log_path),
        )

        winner = result.winner or "tie"
        print(f"{i:>7}  {winner:>12}  {result.turns:>6}  {log_path.name}")
        results.append({"battle": i, "winner": winner, "turns": result.turns})

    wins_a = sum(1 for r in results if r["winner"] == "p1")
    wins_b = sum(1 for r in results if r["winner"] == "p2")
    ties   = sum(1 for r in results if r["winner"] == "tie")
    avg    = sum(r["turns"] for r in results) / len(results)
    print(f"\n  TeamA wins: {wins_a}  TeamB wins: {wins_b}  Ties: {ties}  Avg turns: {avg:.1f}")
    return results


def main() -> None:
    LOG_DIR.mkdir(exist_ok=True)

    print("Generating teams from Pikalytics metagame data (seed=42)...")
    team_a, team_b = make_teams(seed=42)
    show_team(team_a, "Team A")
    show_team(team_b, "Team B")

    runner = BattleRunner(showdown_path=SHOWDOWN_PATH, format_id=FORMAT)

    # --- AI vs AI ---
    def make_model_handler(side):
        def factory(seed=None):  # seed unused — model is deterministic
            return ModelDecisionHandler(
                model=MODEL_PATH,
                vocab=VOCAB_DIR,
                side=side,
            )
        return factory

    run_series(
        runner, team_a, team_b,
        p1_handler_factory=make_model_handler("p1"),
        p2_handler_factory=make_model_handler("p2"),
        label="AI MODEL vs AI MODEL",
        log_subdir=LOG_DIR / "ai_vs_ai",
    )

    # --- Random vs Random ---
    run_series(
        runner, team_a, team_b,
        p1_handler_factory=lambda seed: RandomDecisionHandler(seed=seed),
        p2_handler_factory=lambda seed: RandomDecisionHandler(seed=seed),
        label="RANDOM vs RANDOM",
        log_subdir=LOG_DIR / "random_vs_random",
    )

    print(f"\nAll logs saved to: {LOG_DIR.resolve()}")


if __name__ == "__main__":
    main()
