"""BattlePolicy: neural network for VGC doubles decision-making.

Architecture
------------
1. Embedding lookup for species and moves.
2. Per-slot feature vector: [species_emb | hp | status_onehot | move_embs |
                              stat_stages | first_turn | used_protect | volatile_flags].
3. Bench feature vector: [species_emb | hp | fainted | status_onehot].
4. Field feature vector: [weather_onehot | terrain_onehot | trick_room | tailwinds | turn |
                           my_side_conds | opp_side_conds].
5. Flat concatenation → 2-layer MLP state encoder → 256-dim state embedding.
6. Two output heads (one per active slot: A and B), each a linear map to n_actions logits.
   The joint action space is [move_0…move_N | switch_0…switch_M].

Training uses cross-entropy loss on the observed (state, action) pairs from
real battle logs, weighted by battle outcome (win=1.0, loss=0.5, tie=0.25).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
import torch.nn.functional as F

from .features import (
    N_ACTIVE, N_BENCH, N_MOVES_PER_SLOT,
    N_WEATHER, N_TERRAIN, N_STATUS,
    N_STAT_STAGES, N_VOLATILE, N_SIDE_CONDS,
)


class BattlePolicy(nn.Module):
    """Policy network for VGC doubles.

    Parameters
    ----------
    n_species:
        Size of the species vocabulary (including PAD at index 0).
    n_moves:
        Size of the move vocabulary (including PAD at index 0).
    n_actions:
        Total action-space size = len(move_vocab) + len(species_vocab).
    embed_dim:
        Embedding dimension for species and move tokens.
    hidden_dim:
        Width of the MLP hidden layers.
    """

    def __init__(
        self,
        n_species:  int,
        n_moves:    int,
        n_actions:  int,
        embed_dim:  int = 32,
        hidden_dim: int = 256,
    ) -> None:
        super().__init__()
        self.embed_dim  = embed_dim
        self.hidden_dim = hidden_dim
        self.n_actions  = n_actions

        self.species_emb = nn.Embedding(n_species, embed_dim, padding_idx=0)
        self.move_emb    = nn.Embedding(n_moves,   embed_dim, padding_idx=0)

        # Per active-slot feature size:
        # species_emb(D) + hp(1) + status_onehot(N_STATUS) + N_MOVES_PER_SLOT*move_emb(D)
        #   + stat_stages(N_STAT_STAGES) + first_turn(1) + used_protect(1) + volatile(N_VOLATILE)
        slot_feat = (
            embed_dim + 1 + N_STATUS
            + N_MOVES_PER_SLOT * embed_dim
            + N_STAT_STAGES + 2 + N_VOLATILE
        )

        # Per bench-slot feature size: species_emb(D) + hp(1) + fainted(1) + status_onehot(N_STATUS)
        bench_feat = embed_dim + 2 + N_STATUS

        # Field: weather_onehot + terrain_onehot + trick_room + my_tailwind + opp_tailwind + turn
        #        + my_side_conds + opp_side_conds
        field_feat = N_WEATHER + N_TERRAIN + 4 + 2 * N_SIDE_CONDS

        flat_size = N_ACTIVE * slot_feat + N_BENCH * bench_feat + field_feat

        self.encoder = nn.Sequential(
            nn.Linear(flat_size, hidden_dim),
            nn.ReLU(),
            nn.Linear(hidden_dim, hidden_dim),
            nn.ReLU(),
        )

        # Independent output heads for slot A and slot B
        self.head_a = nn.Linear(hidden_dim, n_actions)
        self.head_b = nn.Linear(hidden_dim, n_actions)

    # ------------------------------------------------------------------

    def forward(
        self,
        active_species:      torch.Tensor,   # (B, N_ACTIVE)
        active_hp:           torch.Tensor,   # (B, N_ACTIVE)                float
        active_status:       torch.Tensor,   # (B, N_ACTIVE)                int
        active_moves:        torch.Tensor,   # (B, N_ACTIVE, N_MOVES_PER_SLOT)
        active_stat_stages:  torch.Tensor,   # (B, N_ACTIVE, N_STAT_STAGES) float
        active_first_turn:   torch.Tensor,   # (B, N_ACTIVE)                float
        active_used_protect: torch.Tensor,   # (B, N_ACTIVE)                float
        active_volatile:     torch.Tensor,   # (B, N_ACTIVE, N_VOLATILE)    float
        bench_species:       torch.Tensor,   # (B, N_BENCH)
        bench_hp:            torch.Tensor,   # (B, N_BENCH)                 float
        bench_fainted:       torch.Tensor,   # (B, N_BENCH)                 float
        bench_status:        torch.Tensor,   # (B, N_BENCH)                 int
        weather:             torch.Tensor,   # (B,)                         int
        terrain:             torch.Tensor,   # (B,)                         int
        trick_room:          torch.Tensor,   # (B,)                         float
        my_tailwind:         torch.Tensor,   # (B,)                         float
        opp_tailwind:        torch.Tensor,   # (B,)                         float
        turn:                torch.Tensor,   # (B,)                         float
        my_side_conds:       torch.Tensor,   # (B, N_SIDE_CONDS)            float
        opp_side_conds:      torch.Tensor,   # (B, N_SIDE_CONDS)            float
    ) -> tuple[torch.Tensor, torch.Tensor]:
        """Return ``(logits_a, logits_b)`` each of shape ``(B, n_actions)``."""
        B = active_species.shape[0]

        # Active slots
        sp_emb    = self.species_emb(active_species)            # (B, 4, D)
        mv_emb    = self.move_emb(active_moves)                 # (B, 4, N_MOVES, D)
        mv_flat   = mv_emb.view(B, N_ACTIVE, -1)               # (B, 4, N_MOVES*D)
        status_oh = F.one_hot(active_status, N_STATUS).float()  # (B, 4, N_STATUS)
        hp_exp    = active_hp.unsqueeze(-1)                     # (B, 4, 1)
        ft_exp    = active_first_turn.unsqueeze(-1)             # (B, 4, 1)
        up_exp    = active_used_protect.unsqueeze(-1)           # (B, 4, 1)

        slot_feat  = torch.cat(
            [sp_emb, hp_exp, status_oh, mv_flat, active_stat_stages, ft_exp, up_exp, active_volatile],
            dim=-1,
        )
        slot_flat  = slot_feat.view(B, -1)

        # Bench slots
        bsp_emb    = self.species_emb(bench_species)            # (B, 4, D)
        bhp_exp    = bench_hp.unsqueeze(-1)                     # (B, 4, 1)
        bft_exp    = bench_fainted.unsqueeze(-1)                # (B, 4, 1)
        bst_oh     = F.one_hot(bench_status, N_STATUS).float()  # (B, 4, N_STATUS)
        bench_feat = torch.cat([bsp_emb, bhp_exp, bft_exp, bst_oh], dim=-1)
        bench_flat = bench_feat.view(B, -1)

        # Field
        weather_oh  = F.one_hot(weather,  N_WEATHER).float()   # (B, N_WEATHER)
        terrain_oh  = F.one_hot(terrain,  N_TERRAIN).float()   # (B, N_TERRAIN)
        field = torch.cat([
            weather_oh, terrain_oh,
            trick_room.unsqueeze(-1),
            my_tailwind.unsqueeze(-1),
            opp_tailwind.unsqueeze(-1),
            turn.unsqueeze(-1),
            my_side_conds,
            opp_side_conds,
        ], dim=-1)

        x         = torch.cat([slot_flat, bench_flat, field], dim=-1)
        state_emb = self.encoder(x)

        return self.head_a(state_emb), self.head_b(state_emb)

    # ------------------------------------------------------------------
    # Persistence

    def save(self, path: Path, metadata: Optional[dict] = None) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "config": {
                "n_species":  self.species_emb.num_embeddings,
                "n_moves":    self.move_emb.num_embeddings,
                "n_actions":  self.n_actions,
                "embed_dim":  self.embed_dim,
                "hidden_dim": self.hidden_dim,
            },
            "state_dict": self.state_dict(),
        }
        if metadata:
            payload["metadata"] = metadata
        torch.save(payload, path)

    @classmethod
    def load(cls, path: Path, device: str = "cpu") -> "BattlePolicy":
        payload = torch.load(path, map_location=device, weights_only=False)
        model   = cls(**payload["config"])
        model.load_state_dict(payload["state_dict"])
        model.eval()
        return model
