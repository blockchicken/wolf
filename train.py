"""Train a BattlePolicy from downloaded VGC battle logs.

Usage::

    python train.py
    python train.py --logs downloaded_logs/gen9championsvgc2026regma --epochs 30

The script:
  1. Loads battle logs from the rating-bucketed log directory (recursive).
  2. Filters invalid logs (Zoroark, Transform, Imprison, incomplete).
  3. Extracts per-turn training examples (two per battle: one per perspective).
  4. Builds species/move vocabularies and saves them.
  5. Trains a BattlePolicy with behaviour-cloning weighted by battle outcome.
  6. Saves the best checkpoint (by validation loss).

Output files::

    checkpoints/vocab/species.json  — species vocabulary
    checkpoints/vocab/moves.json    — move vocabulary
    checkpoints/policy.pt           — trained model weights
"""

from __future__ import annotations

import argparse
import random
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader

from showdown_ai.features import encode_state, encode_action, build_vocab_from_examples
from showdown_ai.model import BattlePolicy
from showdown_ai.training_data import extract_examples_from_dir, TurnExample
from showdown_ai.vocab import BattleVocab


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

def _sf_to_tensors(sf) -> dict:
    return {
        "active_species":      torch.tensor(sf.active_species,      dtype=torch.long),
        "active_hp":           torch.tensor(sf.active_hp,           dtype=torch.float),
        "active_status":       torch.tensor(sf.active_status,       dtype=torch.long),
        "active_moves":        torch.tensor(sf.active_moves,        dtype=torch.long),
        "active_stat_stages":  torch.tensor(sf.active_stat_stages,  dtype=torch.float),
        "active_first_turn":   torch.tensor(sf.active_first_turn,   dtype=torch.float),
        "active_used_protect": torch.tensor(sf.active_used_protect, dtype=torch.float),
        "active_volatile":     torch.tensor(sf.active_volatile,     dtype=torch.float),
        "bench_species":       torch.tensor(sf.bench_species,       dtype=torch.long),
        "bench_hp":            torch.tensor(sf.bench_hp,            dtype=torch.float),
        "bench_fainted":       torch.tensor(sf.bench_fainted,       dtype=torch.float),
        "bench_status":        torch.tensor(sf.bench_status,        dtype=torch.long),
        "weather":     torch.tensor(sf.weather,      dtype=torch.long),
        "terrain":     torch.tensor(sf.terrain,      dtype=torch.long),
        "trick_room":  torch.tensor(sf.trick_room,   dtype=torch.float),
        "my_tailwind": torch.tensor(sf.my_tailwind,  dtype=torch.float),
        "opp_tailwind":torch.tensor(sf.opp_tailwind, dtype=torch.float),
        "turn":        torch.tensor(sf.turn,         dtype=torch.float),
        "my_side_conds":  torch.tensor(sf.my_side_conds,  dtype=torch.float),
        "opp_side_conds": torch.tensor(sf.opp_side_conds, dtype=torch.float),
    }


# Empirically derived from 2,807 high-rated (1500+) battles.
# Down-weight training examples where a player double-protects so the model
# doesn't over-learn that behaviour (real rate is ~7%).
_DOUBLE_PROTECT_TRAIN_WEIGHT = 0.07

_PROTECT_MOVE_NAMES = frozenset({
    "Protect", "Detect", "King's Shield", "Spiky Shield", "Baneful Bunker",
    "Obstruct", "Silk Trap", "Wide Guard", "Mat Block", "Quick Guard",
    "Max Guard", "Crafty Shield",
})


class BattleDataset(Dataset):
    def __init__(self, examples: list[TurnExample], vocab: BattleVocab) -> None:
        self._items: list[dict] = []
        oov = 0
        dp_downweighted = 0

        # Pre-build the set of protecting-move action indices for fast lookup.
        protect_idxs: set[int] = {
            idx for name in _PROTECT_MOVE_NAMES
            if (idx := vocab.action_idx("move", name)) is not None
        }

        for ex in examples:
            sf = encode_state(ex.state, vocab)
            tensors = _sf_to_tensors(sf)

            a_act = ex.actions.get("a")
            b_act = ex.actions.get("b")

            target_a = encode_action(a_act.kind, a_act.name, vocab) if a_act else None
            target_b = encode_action(b_act.kind, b_act.name, vocab) if b_act else None

            if target_a is None and target_b is None:
                oov += 1
                continue

            # Down-weight examples where a slot chose to protect after protecting
            # last turn (double-protect).  Index 0 = my slot A, 1 = my slot B.
            weight = ex.outcome_weight
            for slot_i, target in ((0, target_a), (1, target_b)):
                if (
                    target is not None
                    and target in protect_idxs
                    and sf.active_used_protect[slot_i] > 0.5
                ):
                    weight *= _DOUBLE_PROTECT_TRAIN_WEIGHT
                    dp_downweighted += 1
                    break  # one slot already adjusted; don't double-apply

            tensors["target_a"] = torch.tensor(target_a if target_a is not None else -1, dtype=torch.long)
            tensors["target_b"] = torch.tensor(target_b if target_b is not None else -1, dtype=torch.long)
            tensors["weight"]   = torch.tensor(weight, dtype=torch.float)
            self._items.append(tensors)

        if oov:
            print(f"  Dropped {oov} examples with out-of-vocabulary action targets.")
        if dp_downweighted:
            print(f"  Down-weighted {dp_downweighted} double-protect examples (×{_DOUBLE_PROTECT_TRAIN_WEIGHT}).")

    def __len__(self) -> int:
        return len(self._items)

    def __getitem__(self, idx: int) -> dict:
        return self._items[idx]


# ---------------------------------------------------------------------------
# Training helpers
# ---------------------------------------------------------------------------

def _batch_loss(
    model: BattlePolicy,
    batch: dict,
    device: torch.device,
) -> torch.Tensor:
    logits_a, logits_b = model(
        active_species=      batch["active_species"].to(device),
        active_hp=           batch["active_hp"].to(device),
        active_status=       batch["active_status"].to(device),
        active_moves=        batch["active_moves"].to(device),
        active_stat_stages=  batch["active_stat_stages"].to(device),
        active_first_turn=   batch["active_first_turn"].to(device),
        active_used_protect= batch["active_used_protect"].to(device),
        active_volatile=     batch["active_volatile"].to(device),
        bench_species=       batch["bench_species"].to(device),
        bench_hp=            batch["bench_hp"].to(device),
        bench_fainted=       batch["bench_fainted"].to(device),
        bench_status=        batch["bench_status"].to(device),
        weather=             batch["weather"].to(device),
        terrain=             batch["terrain"].to(device),
        trick_room=          batch["trick_room"].to(device),
        my_tailwind=         batch["my_tailwind"].to(device),
        opp_tailwind=        batch["opp_tailwind"].to(device),
        turn=                batch["turn"].to(device),
        my_side_conds=       batch["my_side_conds"].to(device),
        opp_side_conds=      batch["opp_side_conds"].to(device),
    )
    weight = batch["weight"].to(device)
    loss   = torch.tensor(0.0, device=device)
    count  = 0

    for logits, key in [(logits_a, "target_a"), (logits_b, "target_b")]:
        targets = batch[key].to(device)
        mask    = targets >= 0
        if mask.any():
            per_sample = F.cross_entropy(logits[mask], targets[mask], reduction="none")
            loss += (per_sample * weight[mask]).mean()
            count += 1

    return loss / max(count, 1)


# ---------------------------------------------------------------------------
# Main training function
# ---------------------------------------------------------------------------

def train(
    log_dir:    str | Path,
    model_out:  str | Path,
    vocab_dir:  str | Path,
    *,
    epochs:     int   = 30,
    batch_size: int   = 512,
    lr:         float = 1e-3,
    embed_dim:  int   = 32,
    hidden_dim: int   = 256,
    device_str: str   = "auto",
    seed:       int   = 42,
) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    if device_str == "auto":
        if torch.cuda.is_available():
            device = torch.device("cuda")
        elif torch.backends.mps.is_available():
            device = torch.device("mps")
        else:
            device = torch.device("cpu")
    else:
        device = torch.device(device_str)
    print(f"Device:  {device}")

    # --- Load examples ---
    print(f"\nLoading battle logs from {log_dir} …")
    examples = extract_examples_from_dir(log_dir, recursive=True, verbose=False)
    print(f"  {len(examples):,} training examples extracted.")

    if not examples:
        print("No training data — exiting.")
        return

    # --- Vocabulary ---
    print(f"\nBuilding vocabulary …")
    vocab = build_vocab_from_examples(examples)
    Path(vocab_dir).mkdir(parents=True, exist_ok=True)
    vocab.save(Path(vocab_dir))
    print(f"  {len(vocab.species)} species, {len(vocab.moves)} moves -> {vocab.n_actions()} total actions")

    # --- Dataset ---
    print(f"\nEncoding dataset …")
    dataset = BattleDataset(examples, vocab)
    print(f"  {len(dataset):,} usable examples.")

    if not dataset:
        print("Dataset is empty — exiting.")
        return

    n_val   = max(1, int(0.1 * len(dataset)))
    n_train = len(dataset) - n_val
    train_ds, val_ds = torch.utils.data.random_split(
        dataset, [n_train, n_val],
        generator=torch.Generator().manual_seed(seed),
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True,  drop_last=True)
    val_loader   = DataLoader(val_ds,   batch_size=batch_size, shuffle=False)
    print(f"  {n_train:,} train / {n_val:,} val")

    # --- Model ---
    model = BattlePolicy(
        n_species=len(vocab.species),
        n_moves=len(vocab.moves),
        n_actions=vocab.n_actions(),
        embed_dim=embed_dim,
        hidden_dim=hidden_dim,
    ).to(device)

    n_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"\nModel:   {n_params:,} parameters")

    optimiser = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=epochs)

    # --- Training loop ---
    print(f"\nTraining for {epochs} epochs …")
    print(f"{'Epoch':>6}  {'Train Loss':>10}  {'Val Loss':>10}  {'Best':>5}")
    print("-" * 38)

    best_val  = float("inf")
    model_out = Path(model_out)

    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        for batch in train_loader:
            optimiser.zero_grad()
            loss = _batch_loss(model, batch, device)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimiser.step()
            train_loss += loss.item()
        train_loss /= len(train_loader)
        scheduler.step()

        model.eval()
        val_loss = 0.0
        with torch.no_grad():
            for batch in val_loader:
                val_loss += _batch_loss(model, batch, device).item()
        val_loss /= max(len(val_loader), 1)

        is_best = val_loss < best_val
        if is_best:
            best_val = val_loss
            model.save(
                model_out,
                metadata={
                    "epoch":     epoch,
                    "val_loss":  val_loss,
                    "vocab_dir": str(vocab_dir),
                },
            )

        print(f"{epoch:>6}  {train_loss:>10.4f}  {val_loss:>10.4f}  {'*' if is_best else ''}")

    print(f"\nDone — best val loss {best_val:.4f} saved to {model_out}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="Train a BattlePolicy from downloaded VGC battle logs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--logs", default="downloaded_logs/gen9championsvgc2026regma",
        help="Root directory of downloaded logs (rating-bucket subfolders included automatically)",
    )
    parser.add_argument("--out",        default="checkpoints/policy.pt",  help="Output checkpoint path")
    parser.add_argument("--vocab",      default="checkpoints/vocab",       help="Vocabulary output directory")
    parser.add_argument("--epochs",     type=int,   default=30)
    parser.add_argument("--batch-size", type=int,   default=512)
    parser.add_argument("--lr",         type=float, default=1e-3)
    parser.add_argument("--embed-dim",  type=int,   default=32)
    parser.add_argument("--hidden-dim", type=int,   default=256)
    parser.add_argument("--device",     default="auto")
    parser.add_argument("--seed",       type=int,   default=42)
    args = parser.parse_args()

    train(
        log_dir=args.logs,
        model_out=args.out,
        vocab_dir=args.vocab,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        embed_dim=args.embed_dim,
        hidden_dim=args.hidden_dim,
        device_str=args.device,
        seed=args.seed,
    )


if __name__ == "__main__":
    _main()
