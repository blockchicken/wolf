"""Verify that Mega Evolution is triggered correctly in simulated battles.

Checks:
  1. Battles complete without errors
  2. '|-mega|' events appear in the log (Showdown confirms the mega happened)
  3. DECISION lines contain 'mega' (our handler correctly sent the command)
  4. No 'error' lines mentioning 'mega'
"""
import re
import tempfile
from pathlib import Path

from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler

SHOWDOWN_PATH = Path(r"c:\Users\Arie\pokemon-showdown")
FORMAT = "gen9championsvgc2026regma"

# Both teams are loaded with Mega Stones so every battle should trigger mega.
# Packed format: NAME|SPECIES|ITEM|ABILITY|MOVES|NATURE|EVS|GENDER|IVS|SHINY|LEVEL|
# Champions SPs: max 32/stat, 66 total. IV field left blank (always 31).
TEAM_A = (
    "Charizard||Charizardite Y|Blaze|Flamethrower,Air Slash,Dragon Claw,Protect"
    "|Timid|2/0/0/32/0/32||||50|"
    "]Gardevoir||Gardevoirite|Trace|Psychic,Hyper Voice,Shadow Ball,Protect"
    "|Timid|2/0/0/32/0/32||||50|"
    "]Incineroar||Incinerite|Intimidate|Fake Out,Flare Blitz,Darkest Lariat,Parting Shot"
    "|Careful|32/0/2/0/32/0||||50|"
    "]Garchomp||Garchompite|Rough Skin|Earthquake,Dragon Claw,Rock Slide,Protect"
    "|Jolly|2/32/0/0/0/32||||50|"
    "]Kingambit||Kinglerite|Supreme Overlord|Kowtow Cleave,Iron Head,Sucker Punch,Protect"
    "|Adamant|32/32/0/0/2/0||||50|"
    "]Whimsicott||Whimsicottite|Prankster|Tailwind,Moonblast,Encore,Protect"
    "|Timid|2/0/0/32/0/32||||50|"
)

TEAM_B = (
    "Alakazam||Alakazamite|Magic Guard|Psychic,Focus Blast,Shadow Ball,Dazzling Gleam"
    "|Timid|2/0/0/32/0/32||||50|"
    "]Kangaskhan||Kangaskhanite|Scrappy|Fake Out,Return,Sucker Punch,Protect"
    "|Adamant|32/32/0/0/2/0||||50|"
    "]Starmie||Starminite|Natural Cure|Surf,Ice Beam,Thunderbolt,Protect"
    "|Timid|2/0/0/32/0/32||||50|"
    "]Milotic||Milotite|Competitive|Surf,Ice Beam,Recover,Protect"
    "|Calm|32/0/2/0/32/0||||50|"
    "]Dragonite||Dragonite|Multiscale|Extreme Speed,Dragon Claw,Ice Punch,Protect"
    "|Adamant|2/32/0/0/0/32||||50|"
    "]Machamp||Machampite|Guts|Dynamic Punch,Heavy Slam,Bullet Punch,Protect"
    "|Adamant|32/32/0/0/2/0||||50|"
)

N_BATTLES = 3


def run_test():
    runner = BattleRunner(
        showdown_path=SHOWDOWN_PATH,
        format_id=FORMAT,
        seed=(12345, 12345, 12345, 12345),
    )

    total_mega_events  = 0
    total_mega_cmds    = 0
    total_mega_errors  = 0
    battles_completed  = 0

    for i in range(1, N_BATTLES + 1):
        log_path = Path(tempfile.mktemp(suffix=f"_battle{i}.log"))
        try:
            result, _ = runner.run_battle(
                team_p1=TEAM_A,
                team_p2=TEAM_B,
                p1_handler=RandomDecisionHandler(seed=100 + i),
                p2_handler=RandomDecisionHandler(seed=200 + i),
                p1_name="MegaTeamA",
                p2_name="MegaTeamB",
                max_turns=150,
                log_file=str(log_path),
            )
        except Exception as exc:
            print(f"Battle {i}: CRASHED — {exc}")
            continue

        log_text = log_path.read_text(encoding="utf-8")
        log_path.unlink(missing_ok=True)

        mega_events = log_text.count("|-mega|")
        mega_cmds   = len(re.findall(r"# DECISION.*\bmega\b", log_text))
        mega_errors = len(re.findall(r"\|error\|.*mega", log_text, re.IGNORECASE))

        total_mega_events += mega_events
        total_mega_cmds   += mega_cmds
        total_mega_errors += mega_errors
        battles_completed += 1

        winner = result.winner or "tie"
        status = "OK" if mega_events > 0 and mega_cmds > 0 and mega_errors == 0 else "WARN"
        print(
            f"[{status}] Battle {i}: {result.turns} turns | winner={winner} | "
            f"|-mega| events={mega_events} | 'mega' in decisions={mega_cmds} | "
            f"mega errors={mega_errors}"
        )

    print()
    print(f"Battles completed : {battles_completed}/{N_BATTLES}")
    print(f"Total |-mega| events  : {total_mega_events}")
    print(f"Total 'mega' decisions: {total_mega_cmds}")
    print(f"Total mega errors     : {total_mega_errors}")

    if battles_completed == N_BATTLES and total_mega_events > 0 and total_mega_cmds > 0 and total_mega_errors == 0:
        print("\nPASS — Mega Evolution is working correctly.")
    else:
        print("\nFAIL — see details above.")


if __name__ == "__main__":
    run_test()
