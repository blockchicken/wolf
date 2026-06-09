"""Re-run 5 random vs random battles with clean log files."""
from pathlib import Path
import random
from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler
from showdown_ai.pikalytics import load_metagame, generate_team, team_to_packed

SHOWDOWN_PATH = Path(r"c:\Users\Arie\pokemon-showdown")
FORMAT = "gen9championsvgc2026regma"
PIKALYTICS_DIR = Path("data/pikalytics")
LOG_DIR = Path("battle_logs/random_vs_random")
LOG_DIR.mkdir(parents=True, exist_ok=True)

mg  = load_metagame(PIKALYTICS_DIR, FORMAT)
rng = random.Random(42)
team_a = team_to_packed(generate_team(mg, rng=rng))
team_b = team_to_packed(generate_team(mg, rng=rng))

runner = BattleRunner(showdown_path=SHOWDOWN_PATH, format_id=FORMAT)
print("Battle  Winner  Turns")
print("-" * 30)
for i in range(1, 6):
    log_path = LOG_DIR / f"battle_{i:02d}.log"
    p1 = RandomDecisionHandler(seed=100 + i)
    p2 = RandomDecisionHandler(seed=200 + i)
    result, _ = runner.run_battle(
        team_a, team_b, p1, p2, "TeamA", "TeamB",
        max_turns=200, log_file=str(log_path),
    )
    winner = result.winner or "tie"
    print(f"{i:>6}  {winner:>6}  {result.turns:>5}")
