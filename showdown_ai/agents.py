from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Agent(Protocol):
    """Agent metadata used by the battle runner.

    Runtime decisioning currently happens inside the node worker from each side's
    private `|request|` payload. Python agents select which policy family to use.
    """

    @property
    def policy_name(self) -> str:
        ...


@dataclass(frozen=True)
class RandomLegalAgent:
    """Random policy over legal choices (computed from private requests)."""

    name: str = "random"

    @property
    def policy_name(self) -> str:
        return "random"
