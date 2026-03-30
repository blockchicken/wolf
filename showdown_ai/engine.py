from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
from typing import Any

from .agents import Agent


@dataclass(frozen=True)
class BattleResult:
    winner: str
    turns: int
    raw_result: dict[str, Any]


class ShowdownBattleRunner:
    """Black-box one-battle executor backed by a node worker process."""

    def __init__(self, pokemon_showdown_repo: str | Path, worker_script: str | Path | None = None) -> None:
        self.pokemon_showdown_repo = Path(pokemon_showdown_repo)
        self.worker_script = Path(worker_script or "scripts/showdown_worker.js")

    def run_single_battle(
        self,
        *,
        team_p1: str,
        team_p2: str,
        agent_p1: Agent,
        agent_p2: Agent,
        seed: int | None = None,
        formatid: str = "gen9vgc2024regg",
    ) -> BattleResult:
        """Run one battle and return winner + turns.

        Each side is controlled independently from its own private request stream.
        """

        request = {
            "team_p1": team_p1,
            "team_p2": team_p2,
            "policy_p1": agent_p1.policy_name,
            "policy_p2": agent_p2.policy_name,
            "seed": seed,
            "formatid": formatid,
            "repo": str(self.pokemon_showdown_repo),
        }

        proc = subprocess.run(
            ["node", str(self.worker_script)],
            input=json.dumps(request),
            text=True,
            capture_output=True,
            check=True,
        )
        payload = json.loads(proc.stdout)
        return BattleResult(
            winner=payload["winner"],
            turns=int(payload.get("turns", 0)),
            raw_result=payload,
        )
