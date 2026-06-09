"""Vocabulary management for species names, move names, and actions."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class Vocab:
    """String → int mapping.  Index 0 is reserved as PAD / UNK."""

    PAD = 0

    def __init__(self) -> None:
        self._tok2idx: dict[str, int] = {}
        self._idx2tok: list[str] = ["<pad>"]

    def add(self, token: str) -> int:
        if token not in self._tok2idx:
            idx = len(self._idx2tok)
            self._tok2idx[token] = idx
            self._idx2tok.append(token)
        return self._tok2idx[token]

    def get(self, token: str) -> int:
        return self._tok2idx.get(token, self.PAD)

    def __len__(self) -> int:
        return len(self._idx2tok)

    def __contains__(self, token: str) -> bool:
        return token in self._tok2idx

    @property
    def tokens(self) -> list[str]:
        return list(self._idx2tok)

    def save(self, path: Path) -> None:
        Path(path).write_text(json.dumps(self._idx2tok), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Vocab":
        v = cls()
        for tok in json.loads(Path(path).read_text(encoding="utf-8"))[1:]:
            v.add(tok)
        return v


class BattleVocab:
    """Combined vocabularies for state encoding and action output.

    Action index layout (joint action space):
        [0 … len(moves)-1]                  → move actions  (by move name)
        [len(moves) … len(moves)+len(species)-1] → switch actions (by species name)

    Index 0 in each sub-vocab is PAD and is never a valid action.
    """

    def __init__(self) -> None:
        self.species = Vocab()
        self.moves   = Vocab()

    # ------------------------------------------------------------------
    # Action index helpers

    def move_action_idx(self, name: str) -> Optional[int]:
        """Return the action-space index for 'use move <name>', or None if OOV."""
        idx = self.moves.get(name)
        return idx if idx != Vocab.PAD else None

    def switch_action_idx(self, name: str) -> Optional[int]:
        """Return the action-space index for 'switch to <species>', or None if OOV."""
        idx = self.species.get(name)
        return (len(self.moves) + idx) if idx != Vocab.PAD else None

    def action_idx(self, kind: str, name: str) -> Optional[int]:
        if kind == "move":
            return self.move_action_idx(name)
        if kind == "switch":
            return self.switch_action_idx(name)
        return None

    def n_actions(self) -> int:
        return len(self.moves) + len(self.species)

    # ------------------------------------------------------------------

    def save(self, dir_path: Path) -> None:
        dir_path = Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)
        self.species.save(dir_path / "species.json")
        self.moves.save(dir_path / "moves.json")

    @classmethod
    def load(cls, dir_path: Path) -> "BattleVocab":
        bv = cls()
        bv.species = Vocab.load(Path(dir_path) / "species.json")
        bv.moves   = Vocab.load(Path(dir_path) / "moves.json")
        return bv
