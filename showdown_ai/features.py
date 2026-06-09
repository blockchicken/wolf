"""Convert PerspectiveState snapshots into fixed-size integer/float arrays.

Each state is represented as a StateFeatures struct containing:
  - Integer arrays (for embedding lookups): species IDs, move IDs, status IDs, etc.
  - Float arrays: HP fractions, binary flags, normalized turn count.

The layout treats the four active slots in a fixed order:
    [my_a, my_b, opp_a, opp_b]

Bench slots are also fixed-size (N_BENCH_PER_SIDE * 2) and padded with zeros.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from .state import PerspectiveState, _slot_id
from .vocab import BattleVocab

# ---------------------------------------------------------------------------
# Categorical constants
# ---------------------------------------------------------------------------

_WEATHER_IDX: dict[str, int] = {
    "sunnyday": 1, "desolateland": 1,
    "raindance": 2, "primordialsea": 2,
    "sandstorm": 3,
    "snow": 4, "hail": 4,
}
N_WEATHER = 5   # 0 = none/unknown

_TERRAIN_IDX: dict[str, int] = {
    "electricterrain": 1,
    "grassyterrain":   2,
    "mistyterrain":    3,
    "psychicterrain":  4,
}
N_TERRAIN = 5   # 0 = none

_STATUS_IDX: dict[str, int] = {
    "brn": 1, "par": 2, "psn": 3, "tox": 4, "slp": 5, "frz": 6,
}
N_STATUS = 7    # 0 = no status

N_ACTIVE         = 4    # 2 mine + 2 opponent
N_MOVES_PER_SLOT = 4    # max revealed moves we encode per slot
N_BENCH_PER_SIDE = 2    # VGC: 4 total – 2 active = 2 bench
N_BENCH          = N_BENCH_PER_SIDE * 2   # 4 total bench slots in feature vector

# Stat stage axes encoded per active slot (normalized to [-1, 1])
STAT_NAMES   = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"]
N_STAT_STAGES = len(STAT_NAMES)   # 7

# Volatile status flags per active slot (binary)
VOLATILE_STATUS_LIST = ["confusion", "taunt", "encore", "leech_seed", "substitute"]
N_VOLATILE = len(VOLATILE_STATUS_LIST)   # 5

# Per-side hazard/screen features (reflect, lightscreen, auroraveil, stealthrock, spikes, toxicspikes)
N_SIDE_CONDS = 6


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _parse_hp(hp_str: str) -> float:
    """'87/100' → 0.87  |  '0 fnt' or '' → 0.0  |  unknown → 1.0"""
    if not hp_str or hp_str.startswith("0 ") or hp_str == "0":
        return 0.0
    try:
        cur, mx = hp_str.split("/")
        return float(cur) / float(mx)
    except Exception:
        return 1.0


def _moves_for_slot(state: PerspectiveState, slot: str) -> list[str]:
    """Return unique move names revealed for this slot (from revealed_moves)."""
    result: list[str] = []
    for actor_key, moves in state.revealed_moves.items():
        if _slot_id(actor_key) == slot:
            result.extend(m for m in moves if m not in result)
    return result


def _bench_species(state: PerspectiveState, side: str) -> list[str]:
    """Infer bench species for a given side.

    Strategy: start from the team preview list (``my_team`` / ``opp_team``),
    remove currently active species, remove currently fainted.

    Limitation: the preview list may include Pokémon left home in team selection
    (VGC teams show 6, bring 4).  We include them anyway because we cannot tell
    from the log which 2 stayed home; having false bench members is better than
    missing real ones.
    """
    team = state.my_team if side == state.side else state.opp_team
    current_active = {
        state.active_species.get(f"{side}a"),
        state.active_species.get(f"{side}b"),
    }
    fainted_species = {
        state.active_species.get(s)
        for s in state.fainted_slots
        if s.startswith(side)
    }
    seen: set[str] = set()
    bench: list[str] = []
    for sp in team:
        if sp and sp not in current_active and sp not in fainted_species and sp not in seen:
            bench.append(sp)
            seen.add(sp)
    return bench


def _fill_side_conds(conds: dict[str, int], out: np.ndarray) -> None:
    """Fill a N_SIDE_CONDS float array from a side_conditions dict."""
    out[0] = float(conds.get("reflect", 0) > 0)
    out[1] = float(conds.get("lightscreen", 0) > 0)
    out[2] = float(conds.get("auroraveil", 0) > 0)
    out[3] = float(conds.get("stealthrock", 0) > 0)
    out[4] = conds.get("spikes", 0) / 3.0
    out[5] = conds.get("toxicspikes", 0) / 2.0


# ---------------------------------------------------------------------------
# StateFeatures dataclass
# ---------------------------------------------------------------------------

@dataclass
class StateFeatures:
    """Fixed-size numeric representation of a PerspectiveState.

    Active slot order: [my_a, my_b, opp_a, opp_b].
    Bench slot order:  [my_bench_0, my_bench_1, opp_bench_0, opp_bench_1].
    """
    # Active slots
    active_species:     np.ndarray   # (N_ACTIVE,)               int
    active_hp:          np.ndarray   # (N_ACTIVE,)               float  [0, 1]
    active_status:      np.ndarray   # (N_ACTIVE,)               int    [0, N_STATUS)
    active_moves:       np.ndarray   # (N_ACTIVE, N_MOVES_PER_SLOT) int
    active_stat_stages: np.ndarray   # (N_ACTIVE, N_STAT_STAGES) float  [-1, 1]
    active_first_turn:  np.ndarray   # (N_ACTIVE,)               float  {0, 1}
    active_used_protect: np.ndarray  # (N_ACTIVE,)               float  {0, 1}
    active_volatile:    np.ndarray   # (N_ACTIVE, N_VOLATILE)    float  {0, 1}

    # Bench slots (padded to N_BENCH)
    bench_species:  np.ndarray   # (N_BENCH,)         int
    bench_hp:       np.ndarray   # (N_BENCH,)         float  (1.0 = unknown / assumed full)
    bench_fainted:  np.ndarray   # (N_BENCH,)         float  {0.0, 1.0}
    bench_status:   np.ndarray   # (N_BENCH,)         int    [0, N_STATUS)

    # Field conditions
    weather:       int             # [0, N_WEATHER)
    terrain:       int             # [0, N_TERRAIN)
    trick_room:    float           # {0.0, 1.0}
    my_tailwind:   float           # {0.0, 1.0}
    opp_tailwind:  float           # {0.0, 1.0}
    turn:          float           # log1p(turn) / 5, clamped to [0, 1]

    # Per-side hazards/screens
    my_side_conds:  np.ndarray   # (N_SIDE_CONDS,)    float  [0, 1]
    opp_side_conds: np.ndarray   # (N_SIDE_CONDS,)    float  [0, 1]


# ---------------------------------------------------------------------------
# Encoder
# ---------------------------------------------------------------------------

def encode_state(state: PerspectiveState, vocab: BattleVocab) -> StateFeatures:
    """Convert a PerspectiveState into a StateFeatures struct."""
    my  = state.side
    opp = "p2" if my == "p1" else "p1"
    slot_order = [f"{my}a", f"{my}b", f"{opp}a", f"{opp}b"]

    # --- Active slots ---
    active_species      = np.zeros(N_ACTIVE, dtype=np.int64)
    active_hp           = np.ones(N_ACTIVE,  dtype=np.float32)
    active_status       = np.zeros(N_ACTIVE, dtype=np.int64)
    active_moves        = np.zeros((N_ACTIVE, N_MOVES_PER_SLOT), dtype=np.int64)
    active_stat_stages  = np.zeros((N_ACTIVE, N_STAT_STAGES), dtype=np.float32)
    active_first_turn   = np.zeros(N_ACTIVE, dtype=np.float32)
    active_used_protect = np.zeros(N_ACTIVE, dtype=np.float32)
    active_volatile     = np.zeros((N_ACTIVE, N_VOLATILE), dtype=np.float32)

    for i, slot in enumerate(slot_order):
        sp = state.active_species.get(slot, "")
        if sp:
            active_species[i] = vocab.species.get(sp)
        active_hp[i] = _parse_hp(state.active_hp.get(slot, "100/100"))
        status = state.active_status.get(slot, "")
        active_status[i] = _STATUS_IDX.get(status, 0)
        for j, mv in enumerate(_moves_for_slot(state, slot)[:N_MOVES_PER_SLOT]):
            active_moves[i, j] = vocab.moves.get(mv)

        # Stat stages — normalized to [-1, 1]
        stages = state.stat_stages.get(slot, {})
        for j, stat in enumerate(STAT_NAMES):
            active_stat_stages[i, j] = stages.get(stat, 0) / 6.0

        # First-turn flag: Pokemon entered exactly one turn ago
        if slot in state.entry_turn:
            active_first_turn[i] = float(state.turn - state.entry_turn[slot] == 1)

        # Used protect last turn
        active_used_protect[i] = float(slot in state.protect_last_turn)

        # Volatile status flags
        vs = state.volatile_status.get(slot, set())
        for j, v in enumerate(VOLATILE_STATUS_LIST):
            active_volatile[i, j] = float(v in vs)

    # --- Bench ---
    my_bench  = _bench_species(state, my)[:N_BENCH_PER_SIDE]
    opp_bench = _bench_species(state, opp)[:N_BENCH_PER_SIDE]

    bench_species = np.zeros(N_BENCH, dtype=np.int64)
    bench_hp      = np.ones(N_BENCH,  dtype=np.float32)
    bench_fainted = np.zeros(N_BENCH, dtype=np.float32)
    bench_status  = np.zeros(N_BENCH, dtype=np.int64)

    fainted_species = {state.active_species.get(s) for s in state.fainted_slots}

    for i, sp in enumerate(my_bench):
        bench_species[i] = vocab.species.get(sp)
        if sp in fainted_species:
            bench_fainted[i] = 1.0
            bench_hp[i] = 0.0
        bench_status[i] = _STATUS_IDX.get(state.bench_status.get(sp, ""), 0)

    for i, sp in enumerate(opp_bench):
        bench_species[N_BENCH_PER_SIDE + i] = vocab.species.get(sp)
        if sp in fainted_species:
            bench_fainted[N_BENCH_PER_SIDE + i] = 1.0
            bench_hp[N_BENCH_PER_SIDE + i] = 0.0
        bench_status[N_BENCH_PER_SIDE + i] = _STATUS_IDX.get(state.bench_status.get(sp, ""), 0)

    # --- Field ---
    weather_str = (state.weather or "").lower()
    terrain_str = (state.terrain or "").lower()

    my_side_conds  = np.zeros(N_SIDE_CONDS, dtype=np.float32)
    opp_side_conds = np.zeros(N_SIDE_CONDS, dtype=np.float32)
    _fill_side_conds(state.side_conditions.get(my, {}),  my_side_conds)
    _fill_side_conds(state.side_conditions.get(opp, {}), opp_side_conds)

    return StateFeatures(
        active_species=active_species,
        active_hp=active_hp,
        active_status=active_status,
        active_moves=active_moves,
        active_stat_stages=active_stat_stages,
        active_first_turn=active_first_turn,
        active_used_protect=active_used_protect,
        active_volatile=active_volatile,
        bench_species=bench_species,
        bench_hp=bench_hp,
        bench_fainted=bench_fainted,
        bench_status=bench_status,
        weather=_WEATHER_IDX.get(weather_str, 0),
        terrain=_TERRAIN_IDX.get(terrain_str, 0),
        trick_room=float(state.trick_room),
        my_tailwind=float(my in state.tailwind),
        opp_tailwind=float(opp in state.tailwind),
        turn=float(min(np.log1p(state.turn) / 5.0, 1.0)),
        my_side_conds=my_side_conds,
        opp_side_conds=opp_side_conds,
    )


def encode_action(kind: str, name: str, vocab: BattleVocab) -> Optional[int]:
    """Map (kind, name) to its integer index in the joint action space.

    Returns None if the entity is not in the vocab (OOV).
    """
    return vocab.action_idx(kind, name)


def build_vocab_from_examples(examples: list) -> BattleVocab:
    """Build a BattleVocab from a list of TurnExample objects."""
    vocab = BattleVocab()
    for ex in examples:
        s = ex.state
        for sp in s.my_team + s.opp_team:
            vocab.species.add(sp)
        for sp in s.active_species.values():
            vocab.species.add(sp)
        for moves in s.revealed_moves.values():
            for m in moves:
                vocab.moves.add(m)
        for action in ex.actions.values():
            if action.kind == "move":
                vocab.moves.add(action.name)
            else:
                vocab.species.add(action.name)
    return vocab
