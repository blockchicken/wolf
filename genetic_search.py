"""Genetic algorithm team optimizer for Champions VGC.

Evolves a population of teams using best-of-3 battles against fresh random
meta-representative opponents as the fitness function.  Mutation rate decays
via simulated annealing so early generations explore broadly and later ones
refine the best candidates.

Usage::

    python genetic_search.py                                         # 16 teams, 20 gens, 5 rounds, AI
    python genetic_search.py --pop 48 --pop-end 16 --rounds 5 --rounds-end 16
    python genetic_search.py --gen0-teams 1000 --pop-end 16 --rounds 5 --rounds-end 16
    python genetic_search.py --pop 8 --gens 10 --rounds 3
    python genetic_search.py --handler random
    python genetic_search.py --seed 42 --out results/run1.json

When --gen0-teams is set, a Gen-0 viability cull runs first: N random teams
each play --gen0-rounds BO3 series against fresh random opponents, and only
the teams that sweep all rounds become the GA's initial population (--pop is
ignored in that case — the survivor count becomes the starting population).

At the end the top N teams are printed with fitness, record, and packed strings.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import multiprocessing
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from showdown_ai.battle_runner import BattleRunner, RandomDecisionHandler
from showdown_ai.model import BattlePolicy
from showdown_ai.model_handler import ModelDecisionHandler
from showdown_ai.pikalytics import (
    Metagame, TeamSpec,
    generate_team, load_metagame, team_to_packed,
)
from showdown_ai.vocab import BattleVocab

# ---------------------------------------------------------------------------
# Paths / format
# ---------------------------------------------------------------------------

SHOWDOWN_PATH  = Path(r"c:\Users\Arie\pokemon-showdown")
FORMAT         = "gen9championsvgc2026regma"
MODEL_PATH     = Path("checkpoints/policy_1500_ppo_v13.pt")
VOCAB_DIR      = Path("checkpoints/vocab_1500_ppo_v13")
PIKALYTICS_DIR = Path("data/pikalytics")

# Champions SP constraints (0–32 per stat, 66 total)
SP_TOTAL = 66
SP_MAX   = 32

# Stat indices within HP/Atk/Def/SpA/SpD/Spe
_ATK_IDX = 1
_SPA_IDX = 3

# ---------------------------------------------------------------------------
# Species-clause normalisation
# ---------------------------------------------------------------------------

# Checked longest-first so "Charizard-Mega-X" → "Charizard", not "Charizard-Mega".
_FORM_SUFFIXES: tuple[str, ...] = tuple(sorted((
    # Mega evolutions
    "-Mega-X", "-Mega-Y", "-Mega",
    # Regional forms
    "-Alola", "-Galar", "-Hisui", "-Paldea",
    # Tauros Paldean sub-forms (end in -Combat/-Blaze/-Aqua, not -Paldea)
    "-Paldea-Combat", "-Paldea-Blaze", "-Paldea-Aqua",
    # Gender forms (Basculegion-M/F, Indeedee-M/F)
    "-M", "-F",
    # Ogerpon masks
    "-Wellspring", "-Hearthflame", "-Cornerstone",
    # Terapagos
    "-Terastal", "-Stellar",
    # Time/cycle forms (Lycanroc, Necrozma partial overlap handled by ordering)
    "-Dawn", "-Dusk", "-Midnight", "-Midday",
    # Rotom appliance formes (all share Dex #479)
    "-Heat", "-Wash", "-Frost", "-Fan", "-Mow",
    # Urshifu (Dex #892)
    "-Rapid-Strike", "-Single-Strike",
    # Necrozma fusions (must precede "-Dawn" / "-Dusk")
    "-Dusk-Mane", "-Dawn-Wings", "-Ultra",
    # Forces of Nature therian formes (Tornadus/Thundurus/Landorus/Enamorus)
    "-Therian",
    # Kyurem formes (Dex #646)
    "-Black", "-White",
    # Calyrex riders (Dex #898)
    "-Ice", "-Shadow",
    # Giratina (Dex #487)
    "-Origin",
    # Shaymin (Dex #492)
    "-Sky",
    # Palafin (Dex #964)
    "-Hero",
    # Toxtricity (Dex #849)
    "-Low-Key",
    # Zygarde (Dex #718)
    "-Complete",
    # Eiscue (Dex #875)
    "-Noice",
), key=len, reverse=True))


def _base_species(name: str) -> str:
    """Canonical base species for species-clause duplicate detection.

    Strips Mega, regional-form, gender, and mask-form suffixes so that
    e.g. Kangaskhan-Mega, Ninetales-Alola, and Basculegion-F all reduce
    to their base names and count as the same species slot.
    """
    for suf in _FORM_SUFFIXES:
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


# ---------------------------------------------------------------------------
# Item-clause: Mega Stone list
# ---------------------------------------------------------------------------

# Pokemon holding a Mega Stone must not have their item mutated — losing the
# stone drops them to a lower tier entirely.
_MEGA_STONES: frozenset[str] = frozenset({
    "Abomasite", "Absolite", "Aerodactylite", "Aggronite", "Alakazite",
    "Altarianite", "Ampharosite", "Audinite", "Banettite", "Beedrillite",
    "Blastoisinite", "Blazikenite", "Cameruptite",
    "Charizardite X", "Charizardite Y",
    "Diancite", "Galladite", "Garchompite", "Gardevoirite", "Gengarite",
    "Glalitite", "Gyaradosite", "Heracronite", "Houndoominite",
    "Kangaskhanite", "Latiasite", "Latiosite", "Lopunnite", "Lucarionite",
    "Manectite", "Mawilite", "Medichamite", "Metagrossite", "Pidgeotite",
    "Pinsirite", "Sablenite", "Salamencite", "Scizorite", "Sharpedonite",
    "Slowbronite", "Steelixite", "Tyranitarite", "Venusaurite",
})

# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _parse_evs(ev_str: str) -> list[int]:
    parts = ev_str.strip().split("/")
    evs = [int(x) for x in parts]
    while len(evs) < 6:
        evs.append(0)
    return evs[:6]


def _evs_to_str(evs: list[int]) -> str:
    return "/".join(str(x) for x in evs)


def _weighted_pick(items: list, weights: list[float], rng: random.Random):
    total = sum(weights)
    if total <= 0 or not items:
        return rng.choice(items) if items else None
    r = rng.uniform(0, total)
    acc = 0.0
    for item, w in zip(items, weights):
        acc += w
        if acc >= r:
            return item
    return items[-1]


def _weighted_sample_no_replace(
    items: list, weights: list[float], k: int, rng: random.Random
) -> list:
    rem_items   = list(items)
    rem_weights = list(weights)
    result = []
    for _ in range(min(k, len(rem_items))):
        total = sum(rem_weights)
        if total <= 0:
            result.extend(rng.sample(rem_items, min(k - len(result), len(rem_items))))
            break
        r, acc, idx = rng.uniform(0, total), 0.0, len(rem_items) - 1
        for i, w in enumerate(rem_weights):
            acc += w
            if acc >= r:
                idx = i
                break
        result.append(rem_items[idx])
        rem_items.pop(idx)
        rem_weights.pop(idx)
    return result


_PIKALYTICS_PLACEHOLDERS = frozenset({"Other", "None", "Nothing", ""})
_NON_MOVES = _PIKALYTICS_PLACEHOLDERS  # alias kept for existing move-filtering references


def _build_spec(
    species: str,
    metagame: Metagame,
    rng: random.Random,
    forbidden_items: Optional[set[str]] = None,
) -> TeamSpec:
    """Build a TeamSpec from Pikalytics metagame data for a given species."""
    stat    = metagame.pokemon[species]
    blocked = forbidden_items or set()

    item, ability = "", ""
    if stat.items:
        pool = [(it, w) for it, w in stat.items
                if it not in _PIKALYTICS_PLACEHOLDERS and it not in blocked]
        if not pool:
            # fall back ignoring only the item-clause block, not placeholders
            pool = [(it, w) for it, w in stat.items if it not in _PIKALYTICS_PLACEHOLDERS]
        # Mega Stone holders should always receive their Mega Stone if available.
        mega_pool = [(it, w) for it, w in pool if it in _MEGA_STONES]
        if mega_pool:
            item = mega_pool[0][0]
        elif pool:
            names, wts = zip(*pool)
            item = _weighted_pick(list(names), list(wts), rng) or ""
    if stat.abilities:
        ab_pool = [(ab, w) for ab, w in stat.abilities if ab not in _PIKALYTICS_PLACEHOLDERS]
        if ab_pool:
            names, wts = zip(*ab_pool)
            ability = _weighted_pick(list(names), list(wts), rng) or ""

    move_pool = [(m, w) for m, w in stat.moves[:8] if w >= 2.0 and m not in _NON_MOVES]
    if len(move_pool) < 4:
        move_pool = [(m, w) for m, w in stat.moves if m not in _NON_MOVES][:max(4, len(move_pool))]
    if move_pool:
        mv_names, mv_wts = zip(*move_pool)
        moves = _weighted_sample_no_replace(list(mv_names), list(mv_wts), 4, rng)
    else:
        moves = []
    while len(moves) < 4:
        moves.append("")

    if stat.spreads:
        _, _sp_evs, sp_pcts = zip(*stat.spreads)
        idx    = _weighted_pick(list(range(len(stat.spreads))), list(sp_pcts), rng)
        nature = stat.spreads[idx][0]
        ev_str = stat.spreads[idx][1]
    else:
        nature, ev_str = "Timid", "0/0/0/0/0/32"

    return TeamSpec(species=species, item=item, ability=ability,
                    moves=moves, nature=nature, ev_str=ev_str)


def _ensure_item_clause(
    team: list[TeamSpec], metagame: Metagame, rng: random.Random
) -> list[TeamSpec]:
    """Re-roll any duplicate items so each slot holds a unique item.

    When the conflicting slot's item pool is exhausted, tries to reassign the
    *prior* holder instead, so the current slot can keep the disputed item.
    Mega Stone holders are never reassigned.
    """
    team = list(team)
    seen: dict[str, int] = {}   # item → first slot index
    for i, spec in enumerate(team):
        if not spec.item or spec.item not in seen:
            if spec.item:
                seen[spec.item] = i
            continue
        # Conflict at slot i: try to give i a different item from its pool.
        in_use = {ts.item for j, ts in enumerate(team) if j != i and ts.item}
        stat_i = metagame.pokemon.get(spec.species)
        pool_i = [it for it, _ in stat_i.items if it not in in_use] if stat_i else []
        if pool_i:
            spec      = copy.copy(spec)
            spec.item = rng.choice(pool_i)
            team[i]   = spec
            seen[spec.item] = i
        else:
            # Pool exhausted for slot i — reassign the prior holder (slot j)
            # so slot i can keep the disputed item.  Skip if j holds a Mega Stone.
            j = seen[spec.item]
            if team[j].item not in _MEGA_STONES:
                in_use_j = {ts.item for k, ts in enumerate(team) if k != j and ts.item}
                stat_j   = metagame.pokemon.get(team[j].species)
                pool_j   = [it for it, _ in stat_j.items if it not in in_use_j] if stat_j else []
                if pool_j:
                    new_j       = copy.copy(team[j])
                    new_j.item  = rng.choice(pool_j)
                    team[j]     = new_j
                    seen[new_j.item] = j
                    seen[spec.item]  = i   # slot i now uniquely holds the item
    return team


def _sample_random_spec(
    metagame: Metagame,
    used_bases: set[str],
    rng: random.Random,
    forbidden_items: Optional[set[str]] = None,
) -> Optional[TeamSpec]:
    available = [
        (n, metagame.pokemon[n].usage)
        for n in metagame.pokemon
        if _base_species(n) not in used_bases
    ]
    if not available:
        return None
    names, wts = zip(*available)
    species = _weighted_pick(list(names), list(wts), rng)
    return _build_spec(species, metagame, rng, forbidden_items=forbidden_items)


# ---------------------------------------------------------------------------
# Individual
# ---------------------------------------------------------------------------

@dataclass
class Individual:
    team: list[TeamSpec]   # exactly 6
    series_wins:   int = 0
    series_losses: int = 0
    series_ties:   int = 0

    @property
    def series_played(self) -> int:
        return self.series_wins + self.series_losses + self.series_ties

    @property
    def fitness(self) -> float:
        """Win rate; a tied series counts as 0.5."""
        if self.series_played == 0:
            return 0.0
        return (self.series_wins + 0.5 * self.series_ties) / self.series_played

    def packed(self) -> str:
        return team_to_packed(self.team)

    def reset_fitness(self) -> None:
        self.series_wins = self.series_losses = self.series_ties = 0

    def species_list(self) -> list[str]:
        return [ts.species for ts in self.team]

    def to_dict(self) -> dict:
        return {
            "fitness": self.fitness,
            "record":  f"{self.series_wins}W-{self.series_losses}L-{self.series_ties}T",
            "species": self.species_list(),
            "packed":  self.packed(),
            "team": [
                {"species": ts.species, "item": ts.item, "ability": ts.ability,
                 "moves": ts.moves, "nature": ts.nature, "ev_str": ts.ev_str}
                for ts in self.team
            ],
        }


# ---------------------------------------------------------------------------
# Mutation operators
# ---------------------------------------------------------------------------

def _mutate_spread(spec: TeamSpec, rng: random.Random, max_shift: int = 8) -> TeamSpec:
    """Shift SP points between two stats.

    Constraint: never moves points INTO Atk if SpA > 0, and vice versa,
    preventing nonsensical mixed-attacker spreads.
    """
    evs = _parse_evs(spec.ev_str)

    donors = [i for i, v in enumerate(evs) if v > 0]
    if not donors:
        return spec
    donor = rng.choice(donors)

    # Eligible recipients: must have room, and must not cross the Atk/SpA wall
    recvs = [
        i for i, v in enumerate(evs)
        if i != donor
        and v < SP_MAX
        and not (i == _ATK_IDX and evs[_SPA_IDX] > 0)
        and not (i == _SPA_IDX and evs[_ATK_IDX] > 0)
    ]
    if not recvs:
        return spec

    recv  = rng.choice(recvs)
    shift = rng.randint(1, min(evs[donor], SP_MAX - evs[recv], max_shift))
    evs[donor] -= shift
    evs[recv]  += shift

    spec        = copy.copy(spec)
    spec.ev_str = _evs_to_str(evs)
    return spec


def _mutate_spread_distribution(
    spec: TeamSpec, metagame: Metagame, rng: random.Random
) -> TeamSpec:
    """Pick a different popular spread+nature combo from Pikalytics data.

    Nature and stat distribution are coupled, so we swap the whole combination
    rather than changing the nature alone.  Only considers spreads that differ
    from the current one so the mutation always makes a real change.
    """
    stat = metagame.pokemon.get(spec.species)
    if not stat or not stat.spreads:
        return spec
    current = (spec.nature, spec.ev_str)
    alts    = [(n, ev, pct) for n, ev, pct in stat.spreads if (n, ev) != current]
    if not alts:
        return spec
    _, _, pcts = zip(*alts)
    idx         = _weighted_pick(list(range(len(alts))), list(pcts), rng)
    spec        = copy.copy(spec)
    spec.nature = alts[idx][0]
    spec.ev_str = alts[idx][1]
    return spec


def _mutate_move(spec: TeamSpec, metagame: Metagame, rng: random.Random) -> TeamSpec:
    """Replace one of the four moves with a different move from this species' pool."""
    stat = metagame.pokemon.get(spec.species)
    if not stat or not stat.moves:
        return spec
    pool = [m for m, _ in stat.moves if m not in _NON_MOVES and m not in spec.moves]
    if not pool:
        return spec
    new_moves                   = list(spec.moves)
    new_moves[rng.randint(0, 3)] = rng.choice(pool)
    spec                        = copy.copy(spec)
    spec.moves                  = new_moves
    return spec


def _mutate_item(
    spec: TeamSpec,
    metagame: Metagame,
    rng: random.Random,
    used_items: set[str],
) -> TeamSpec:
    """Replace the held item, respecting Mega Stone lock and item clause.

    Pokemon holding a Mega Stone are never mutated — losing the stone would
    remove their Mega Evolution entirely.
    """
    if spec.item in _MEGA_STONES:
        return spec                          # Mega Stone lock: never change
    stat = metagame.pokemon.get(spec.species)
    if not stat or not stat.items:
        return spec
    pool = [it for it, _ in stat.items
            if it not in _PIKALYTICS_PLACEHOLDERS and it != spec.item and it not in used_items]
    if not pool:
        return spec
    spec       = copy.copy(spec)
    spec.item  = rng.choice(pool)
    return spec


def _mutate_species(
    team: list[TeamSpec],
    slot_idx: int,
    metagame: Metagame,
    rng: random.Random,
    used_bases: set[str],
    used_items: set[str],
) -> list[TeamSpec]:
    """Replace one Pokemon with a metagame-sampled one.

    The replacement respects both species clause (no duplicate base species)
    and item clause (the generated Pokemon gets an item not already on the team).
    """
    new_spec = _sample_random_spec(
        metagame, used_bases, rng, forbidden_items=used_items
    )
    if new_spec is None:
        return team
    team           = list(team)
    team[slot_idx] = new_spec
    # _build_spec falls back to all items when the preferred pool is exhausted,
    # so run a clause-repair pass to eliminate any accidental duplicate.
    return _ensure_item_clause(team, metagame, rng)


def _mutate_individual(
    ind: Individual,
    metagame: Metagame,
    mutation_prob: float,
    rng: random.Random,
    max_shift: int = 8,
) -> Individual:
    """Apply independent per-type mutations to each Pokemon slot.

    Each mutation type is checked separately, so a single slot can receive
    both a spread tweak and a move swap in the same generation.
    Relative frequencies reflect how impactful each change is:
      - spread / move : checked at full mutation_prob  (equal, high impact)
      - item          : checked at 0.5 × mutation_prob (less frequent)
      - distribution  : checked at 0.3 × mutation_prob (whole spread swap, rare)
      - species       : checked at 0.15 × mutation_prob (biggest change, rarest)
    """
    team = list(ind.team)

    for i in range(len(team)):
        # Precompute items and bases used by OTHER slots (refreshed per slot since
        # earlier slots in this loop may have already mutated)
        used_items = {ts.item for j, ts in enumerate(team) if j != i and ts.item}
        used_bases = {_base_species(ts.species) for j, ts in enumerate(team) if j != i}

        if rng.random() < mutation_prob:
            team[i] = _mutate_spread(team[i], rng, max_shift=max_shift)

        if rng.random() < mutation_prob:
            team[i] = _mutate_move(team[i], metagame, rng)

        if rng.random() < mutation_prob * 0.5:
            team[i] = _mutate_item(team[i], metagame, rng, used_items)

        if rng.random() < mutation_prob * 0.3:
            team[i] = _mutate_spread_distribution(team[i], metagame, rng)

        if rng.random() < mutation_prob * 0.15:
            team = _mutate_species(team, i, metagame, rng, used_bases, used_items)

    # Final safety pass: _build_spec can produce a duplicate item when all valid
    # items for a replacement species are already taken elsewhere on the team.
    team = _ensure_item_clause(team, metagame, rng)
    return Individual(team=team)


# ---------------------------------------------------------------------------
# Crossover
# ---------------------------------------------------------------------------

def _crossover_teams(
    parent_a: list[TeamSpec],
    parent_b: list[TeamSpec],
    metagame: Metagame,
    rng: random.Random,
) -> list[TeamSpec]:
    """Uniform crossover at the team-slot level.

    For each of 6 slots, randomly choose from parent A or a shuffled parent B.
    Duplicate base-species are resolved by trying the other parent first, then
    falling back to a metagame sample.  Item clause is enforced via a final
    post-processing pass.
    """
    b_order    = rng.sample(range(len(parent_b)), len(parent_b))
    child:      list[TeamSpec] = []
    used_bases: set[str]       = set()

    for i in range(6):
        a_slot = parent_a[i] if i < len(parent_a) else None
        b_slot = parent_b[b_order[i]] if i < len(b_order) else None
        order  = ([a_slot, b_slot] if rng.random() < 0.5 else [b_slot, a_slot])

        chosen = None
        for candidate in order:
            if candidate is None:
                continue
            base = _base_species(candidate.species)
            if base not in used_bases:
                chosen = copy.deepcopy(candidate)
                used_bases.add(base)
                break

        if chosen is None:
            chosen = _sample_random_spec(metagame, used_bases, rng)
            if chosen:
                used_bases.add(_base_species(chosen.species))

        if chosen is not None:
            child.append(chosen)

    while len(child) < 6:
        extra = _sample_random_spec(metagame, used_bases, rng)
        if extra is None:
            break
        child.append(extra)
        used_bases.add(_base_species(extra.species))

    return _ensure_item_clause(child[:6], metagame, rng)


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

def _tournament_select(
    population: list[Individual], rng: random.Random, k: int = 3
) -> Individual:
    contestants = rng.sample(population, min(k, len(population)))
    return max(contestants, key=lambda x: x.fitness)


# ---------------------------------------------------------------------------
# Battle / evaluation
# ---------------------------------------------------------------------------

def _battle_best_of_3(
    packed_a: str,
    packed_b: str,
    runner: BattleRunner,
    make_p1,
    make_p2,
) -> tuple[int, int, int]:
    """Play up to 3 games; stop as soon as one side reaches 2 wins.

    Returns (wins_a, wins_b, ties).
    """
    wins_a = wins_b = ties = 0
    for _ in range(3):
        if wins_a >= 2 or wins_b >= 2:
            break
        try:
            result, _ = runner.run_battle(
                team_p1=packed_a,
                team_p2=packed_b,
                p1_handler=make_p1(),
                p2_handler=make_p2(),
                max_turns=200,
            )
            if result.winner == "p1":
                wins_a += 1
            elif result.winner == "p2":
                wins_b += 1
            else:
                ties += 1
        except Exception as exc:
            print(f"  [battle error] {exc}")
            ties += 1
    return wins_a, wins_b, ties


def _evaluate_generation(
    population: list[Individual],
    metagame: Metagame,
    runner: BattleRunner,
    make_p1,
    make_p2,
    n_rounds: int,
    rng: random.Random,
) -> None:
    """Evaluate each team against n_rounds fresh random meta teams (BO3 each).

    Teams in the population never battle each other — this measures absolute
    strength against the metagame rather than relative population strength.
    """
    for ind in population:
        ind.reset_fitness()

    n = len(population)

    for i, ind in enumerate(population):
        team_start = time.time()
        for _ in range(n_rounds):
            opp_team   = generate_team(metagame, rng=rng)
            opp_team   = _ensure_item_clause(opp_team, metagame, rng)
            opp_packed = team_to_packed(opp_team)
            wa, wb, wt = _battle_best_of_3(
                ind.packed(), opp_packed, runner, make_p1, make_p2
            )
            if wa > wb:
                ind.series_wins   += 1
            elif wb > wa:
                ind.series_losses += 1
            else:
                ind.series_ties   += 1

        record_str = f"{ind.series_wins:>2}W-{ind.series_losses:>2}L-{ind.series_ties:>2}T"
        print(
            f"  [{i+1:>2}/{n}] {', '.join(ind.species_list())}  "
            f"->  {record_str}  fit={ind.fitness:.3f}  ({time.time()-team_start:.0f}s)",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Gen-0 viability cull (optional pre-experiment phase, runs in parallel)
# ---------------------------------------------------------------------------

# Worker-process globals — loaded once per process by _gen0_worker_init().
_gen0_metagame = None
_gen0_runner   = None
_gen0_model    = None
_gen0_vocab    = None
_gen0_handler  = None


def _gen0_worker_init(handler_kind: str) -> None:
    """Per-process resource loader for the parallel Gen-0 cull."""
    global _gen0_metagame, _gen0_runner, _gen0_model, _gen0_vocab, _gen0_handler

    _gen0_metagame = load_metagame(PIKALYTICS_DIR, FORMAT)
    _gen0_runner   = BattleRunner(SHOWDOWN_PATH, format_id=FORMAT)
    _gen0_handler  = handler_kind

    if handler_kind == "model":
        _gen0_model = BattlePolicy.load(MODEL_PATH)
        _gen0_vocab = BattleVocab.load(VOCAB_DIR)


def _gen0_make_handlers():
    if _gen0_handler == "model":
        def make_p1():
            return ModelDecisionHandler(model=_gen0_model, vocab=_gen0_vocab, side="p1")
        def make_p2():
            return ModelDecisionHandler(model=_gen0_model, vocab=_gen0_vocab, side="p2")
    else:
        ctr = [random.randint(0, 2**31)]
        def make_p1():
            ctr[0] += 1
            return RandomDecisionHandler(seed=ctr[0])
        def make_p2():
            ctr[0] += 1
            return RandomDecisionHandler(seed=ctr[0])
    return make_p1, make_p2


def _gen0_run_one_team(args: tuple) -> tuple[list[TeamSpec], int]:
    """Generate one random team, play n_rounds BO3 series, return (team, series_won)."""
    seed, n_rounds = args
    rng    = random.Random(seed)
    team   = generate_team(_gen0_metagame, rng=rng)
    team   = _ensure_item_clause(team, _gen0_metagame, rng)
    packed = team_to_packed(team)

    make_p1, make_p2 = _gen0_make_handlers()

    series_won = 0
    for _ in range(n_rounds):
        opp_team   = generate_team(_gen0_metagame, rng=rng)
        opp_team   = _ensure_item_clause(opp_team, _gen0_metagame, rng)
        opp_packed = team_to_packed(opp_team)
        wa, wb, _wt = _battle_best_of_3(packed, opp_packed, _gen0_runner, make_p1, make_p2)
        if wa > wb:
            series_won += 1

    return team, series_won


def run_gen0_cull(
    n_teams: int,
    n_rounds: int,
    min_wins: int,
    handler_kind: str,
    workers: int,
    rng: random.Random,
) -> list[Individual]:
    """Generate n_teams random teams, play n_rounds BO3 series each against fresh
    random opponents, and keep only teams with series_won >= min_wins.

    Survivors become the GA's initial population — no mutation or breeding is
    applied here, this is a pure cull of pre-experiment random teams down to a
    "viable" starting pool.
    """
    print(f"\n{'='*66}")
    print(f"  GEN-0 VIABILITY CULL")
    print(f"{'='*66}")
    print(f"  Teams       : {n_teams}")
    print(f"  Rounds      : {n_rounds}  (BO3 series, fresh random opponents)")
    print(f"  Min wins    : {min_wins}/{n_rounds} to survive")
    print(f"  Workers     : {workers}")
    print(f"{'='*66}\n")

    team_args = [(rng.randint(0, 2**32 - 1), n_rounds) for _ in range(n_teams)]
    survivors: list[Individual] = []
    done = 0
    t0   = time.time()

    def _record(team: list[TeamSpec], wins: int) -> None:
        nonlocal done
        done += 1
        if wins >= min_wins:
            survivors.append(Individual(team=team))
        if done % 25 == 0 or done == n_teams:
            print(
                f"  [{done:>4}/{n_teams}] survivors so far: {len(survivors)}  "
                f"({time.time()-t0:.0f}s)",
                flush=True,
            )

    if workers <= 1:
        _gen0_worker_init(handler_kind)
        for a in team_args:
            team, wins = _gen0_run_one_team(a)
            _record(team, wins)
    else:
        with multiprocessing.Pool(
            processes=workers, initializer=_gen0_worker_init, initargs=(handler_kind,)
        ) as pool:
            for team, wins in pool.imap_unordered(_gen0_run_one_team, team_args):
                _record(team, wins)

    elapsed = time.time() - t0
    print(
        f"\n  Gen-0 complete in {elapsed:.0f}s  ->  {len(survivors)}/{n_teams} teams survived "
        f"({100*len(survivors)/n_teams:.1f}%)"
    )
    print(f"{'='*66}")
    return survivors


# ---------------------------------------------------------------------------
# Simulated annealing schedule
# ---------------------------------------------------------------------------

def _anneal(gen: int, n_gens: int, v0: float, v_min: float) -> float:
    """Exponential decay from v0 → v_min over n_gens generations."""
    if n_gens <= 1:
        return v_min
    return v_min + (v0 - v_min) * math.exp(-3.0 * (gen - 1) / (n_gens - 1))


def _mutation_prob(gen: int, n_gens: int, t0: float, t_min: float) -> float:
    return _anneal(gen, n_gens, t0, t_min)


def _max_spread_shift(gen: int, n_gens: int, s0: int = 24, s_min: int = 2) -> int:
    """Anneal the maximum SP points that can shift in one mutation: s0 → s_min."""
    return max(s_min, round(_anneal(gen, n_gens, float(s0), float(s_min))))


def _pop_schedule(gen: int, n_gens: int, pop_start: int, pop_end: int) -> int:
    """Linearly interpolate population size from pop_start to pop_end over n_gens.

    Linear (not exponential) so it lands exactly on pop_end at the final
    generation regardless of how large pop_start is — important because
    pop_start may come from the data-driven Gen-0 cull rather than a preset.
    """
    if n_gens <= 1:
        return pop_end
    frac = (gen - 1) / (n_gens - 1)
    return max(1, round(pop_start + (pop_end - pop_start) * frac))


def _rounds_schedule(gen: int, n_gens: int, rounds_start: int, rounds_end: int) -> int:
    """Linearly interpolate rounds per team from rounds_start to rounds_end over n_gens.

    Linear (not exponential) so it lands exactly on rounds_end at the final
    generation — important when rounds are increasing (rounds_start < rounds_end).
    """
    if n_gens <= 1:
        return rounds_end
    frac = (gen - 1) / (n_gens - 1)
    return max(1, round(rounds_start + (rounds_end - rounds_start) * frac))


def _topn_count(pop_size: int, topn_pct: float) -> int:
    """Elites to carry over: topn_pct of pop_size, floored at 2 (need at least
    two distinct survivors so the elite pool isn't a single clone) and capped
    at pop_size.
    """
    n = max(2, round(pop_size * topn_pct))
    return min(pop_size, n)


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _format_team(team: list[TeamSpec], indent: str = "    ") -> str:
    lines = []
    for spec in team:
        moves_str = " / ".join(m for m in spec.moves if m)
        lines.append(
            f"{indent}{spec.species:<22} @ {spec.item:<20} "
            f"[{moves_str}]  {spec.nature}  {spec.ev_str}"
        )
    return "\n".join(lines)


def _log_generation(
    gen: int,
    n_gens: int,
    population: list[Individual],
    gen_time: float,
    mu: float,
    max_shift: int = 8,
    n_rounds: int = 0,
) -> None:
    fitnesses  = [ind.fitness for ind in population]
    best       = population[0]
    avg        = sum(fitnesses) / len(fitnesses)
    bar        = "=" * 66
    mins, secs = divmod(int(gen_time), 60)
    print(f"\n{bar}")
    print(
        f"  Generation {gen}/{n_gens}  |  "
        f"Time: {mins}m {secs}s  |  "
        f"Pop: {len(population)}  |  "
        f"Rounds: {n_rounds}  |  "
        f"Mutation: {mu:.3f}  |  "
        f"Max SP shift: {max_shift}"
    )
    print(
        f"  Best:  {best.fitness:.3f}  "
        f"({best.series_wins}W-{best.series_losses}L-{best.series_ties}T)  "
        f"Avg: {avg:.3f}  Worst: {fitnesses[-1]:.3f}"
    )
    print(f"  Best team: {', '.join(best.species_list())}")
    top5 = "  |  ".join(
        f"{ind.species_list()[0]}({ind.fitness:.2f})" for ind in population[:5]
    )
    print(f"  Top 5:  {top5}")
    print(f"{bar}", flush=True)


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def _write_checkpoint(
    path: Path,
    gen: int,
    total_gens: int,
    original_pop_start: int,
    pop_end: int,
    rounds_start: int,
    rounds_end: int,
    elapsed_s: float,
    rng: random.Random,
    population: list[Individual],
) -> None:
    """Write GA state to disk so a crashed run can be resumed.

    gen == 0 means the checkpoint was written after the Gen-0 cull (before
    any GA generation ran).  The file is overwritten each time.
    """
    v, istate, gnext = rng.getstate()
    data = {
        "gen": gen,
        "total_gens": total_gens,
        "elapsed_s": round(elapsed_s),
        "original_pop_start": original_pop_start,
        "pop_end": pop_end,
        "rounds_start": rounds_start,
        "rounds_end": rounds_end,
        "rng_state": [v, list(istate), gnext],
        "population": [
            {
                "team": [
                    {"species": ts.species, "item": ts.item, "ability": ts.ability,
                     "moves": ts.moves, "nature": ts.nature, "ev_str": ts.ev_str}
                    for ts in ind.team
                ],
                "series_wins":   ind.series_wins,
                "series_losses": ind.series_losses,
                "series_ties":   ind.series_ties,
            }
            for ind in population
        ],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    label = "gen-0 survivors" if gen == 0 else f"gen {gen}/{total_gens}"
    print(f"  [checkpoint] {label} → {path}  ({len(population)} teams)", flush=True)


def load_checkpoint(path: Path) -> tuple[dict, list[Individual]]:
    """Load a checkpoint written by _write_checkpoint.

    Returns (meta, population).  meta keys: gen, total_gens, elapsed_s,
    original_pop_start, pop_end, rounds_start, rounds_end, rng_state.
    gen == 0 means the population is Gen-0 survivors ready for gen 1.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    population = [
        Individual(
            team=[
                TeamSpec(
                    species=m["species"], item=m["item"], ability=m["ability"],
                    moves=m["moves"], nature=m["nature"], ev_str=m["ev_str"],
                )
                for m in entry["team"]
            ],
            series_wins=entry["series_wins"],
            series_losses=entry["series_losses"],
            series_ties=entry["series_ties"],
        )
        for entry in raw["population"]
    ]
    return raw, population


# ---------------------------------------------------------------------------
# GA loop
# ---------------------------------------------------------------------------

def run_ga(
    metagame:       Metagame,
    runner:         BattleRunner,
    make_p1,
    make_p2,
    *,
    pop_start:     int   = 16,
    pop_end:       int   = 16,
    generations:   int   = 20,
    rounds_start:  int   = 5,
    rounds_end:    int   = 5,
    topn_pct:      float = 0.15,
    mutation_t0:   float = 0.35,
    mutation_tmin: float = 0.05,
    rng: Optional[random.Random] = None,
    initial_population: Optional[list[Individual]] = None,
    # Resume / checkpoint support
    start_gen:          int   = 1,
    original_pop_start: Optional[int]  = None,
    checkpoint_path:    Optional[Path] = None,
    resume_elapsed:     float = 0.0,
) -> list[Individual]:
    if rng is None:
        rng = random.Random()

    # For annealing, use the original pop_start from the full run so the
    # decay curves stay on the same trajectory after a resume.
    annealing_pop_start = original_pop_start if original_pop_start is not None else pop_start

    # A Gen-0-cull-supplied population overrides pop_start on fresh starts only.
    # On a resume (start_gen > 1) the checkpoint carries the correct annealing base.
    if initial_population is not None and start_gen == 1:
        pop_start = len(initial_population)
        if original_pop_start is None:
            annealing_pop_start = pop_start

    pop_annealed    = annealing_pop_start != pop_end
    rounds_annealed = rounds_start != rounds_end

    def _pop_g(gen: int) -> int:
        return _pop_schedule(gen, generations, annealing_pop_start, pop_end)

    def _rounds_g(gen: int) -> int:
        return _rounds_schedule(gen, generations, rounds_start, rounds_end)

    max_series = _pop_g(1) * _rounds_g(1) * 3   # rough upper bound for gen 1

    print(f"\n{'='*66}")
    print(f"  GENETIC TEAM SEARCH  —  Champions VGC")
    print(f"{'='*66}")
    if pop_annealed:
        print(f"  Population  : {annealing_pop_start} -> {pop_end} teams  (linear anneal)")
    else:
        print(f"  Population  : {annealing_pop_start} teams")
    print(f"  Generations : {generations}")
    if rounds_annealed:
        print(f"  Rounds      : {rounds_start} -> {rounds_end} per team per gen  (annealed)")
    else:
        print(f"  Rounds      : {rounds_start} per team per gen")
    print(f"  Top-N keep  : {topn_pct*100:.0f}% of current pop (min 2)")
    print(f"  Mutation    : {mutation_t0:.2f} -> {mutation_tmin:.2f}  (simulated annealing)")
    print(f"  Max series  : ~{max_series} in generation 1  (BO3 series)")
    print(f"{'='*66}")

    if start_gen > 1:
        # Resuming mid-run: initial_population is the sorted result of gen (start_gen-1).
        # Reconstruct the gen start_gen population via crossover+mutation using the
        # restored RNG state so the search continues naturally from the checkpoint.
        assert initial_population is not None, "resume requires a checkpoint population"
        prev_pop   = list(initial_population)
        prev_gen   = start_gen - 1
        mu_prev    = _mutation_prob(prev_gen, generations, mutation_t0, mutation_tmin)
        shift_prev = _max_spread_shift(prev_gen, generations)
        pop_this   = _pop_g(start_gen)
        n_elite    = _topn_count(pop_this, topn_pct)

        print(f"\nResuming GA from generation {start_gen}/{generations}  "
              f"({len(prev_pop)} checkpoint teams → rebuilding {pop_this}).")

        population: list[Individual] = []
        for e in prev_pop[:n_elite]:
            elite = copy.deepcopy(e)
            elite.reset_fitness()
            population.append(elite)
        while len(population) < pop_this:
            pa    = _tournament_select(prev_pop, rng)
            pb    = _tournament_select(prev_pop, rng)
            child = Individual(team=_crossover_teams(pa.team, pb.team, metagame, rng))
            child = _mutate_individual(child, metagame, mu_prev, rng, max_shift=shift_prev)
            population.append(child)
        population = population[:pop_this]

    elif initial_population is not None:
        print(f"\nUsing {pop_start} Gen-0 survivors as the initial population.")
        population = list(initial_population)
    else:
        print(f"\nInitializing {pop_start} random teams ...")
        population = []
        for idx in range(pop_start):
            team = generate_team(metagame, rng=rng)
            team = _ensure_item_clause(team, metagame, rng)
            ind  = Individual(team=team)
            print(f"  Team {idx+1:>2}: {', '.join(ind.species_list())}")
            population.append(ind)

    total_start = time.time()

    for gen in range(start_gen, generations + 1):
        gen_start = time.time()
        mu        = _mutation_prob(gen, generations, mutation_t0, mutation_tmin)
        pop_g     = _pop_g(gen)
        rounds_g  = _rounds_g(gen)

        # Trim population to this generation's target size.
        # On gen > 1 the population is already sorted by fitness from the
        # previous round, so we keep the strongest teams.
        if len(population) > pop_g:
            population = population[:pop_g]

        shift = _max_spread_shift(gen, generations)

        print(f"\n{'-'*66}")
        print(
            f"  Generation {gen}/{generations}  |  "
            f"pop={pop_g}  |  rounds={rounds_g}  |  "
            f"mutation={mu:.3f}  |  {pop_g * rounds_g} series"
        )
        print(f"{'-'*66}")

        _evaluate_generation(
            population, metagame, runner, make_p1, make_p2, rounds_g, rng
        )
        population.sort(key=lambda x: -x.fitness)

        gen_time = time.time() - gen_start
        _log_generation(gen, generations, population, gen_time, mu, shift, rounds_g)

        if checkpoint_path is not None:
            elapsed = resume_elapsed + (time.time() - total_start)
            _write_checkpoint(
                checkpoint_path, gen, generations,
                annealing_pop_start, pop_end,
                rounds_start, rounds_end,
                elapsed, rng, population,
            )

        if gen == generations:
            break

        # Build next generation sized to the next scheduled population count.
        pop_next = _pop_g(gen + 1)
        next_gen: list[Individual] = []

        # Top-N% elites carry over unchanged (fitness reset for next round).
        n_elite = _topn_count(pop_next, topn_pct)
        for e in population[:n_elite]:
            elite = copy.deepcopy(e)
            elite.reset_fitness()
            next_gen.append(elite)

        # Fill remainder via crossover + mutation.
        while len(next_gen) < pop_next:
            pa    = _tournament_select(population, rng)
            pb    = _tournament_select(population, rng)
            child = Individual(
                team=_crossover_teams(pa.team, pb.team, metagame, rng)
            )
            child = _mutate_individual(child, metagame, mu, rng, max_shift=shift)
            next_gen.append(child)

        population = next_gen[:pop_next]

    total_time = time.time() - total_start
    mins, secs = divmod(int(total_time), 60)
    print(f"\n{'='*66}")
    print(f"  Search complete in {mins}m {secs}s")
    print(f"{'='*66}")

    return population


# ---------------------------------------------------------------------------
# Final report
# ---------------------------------------------------------------------------

def print_top_teams(top: list[Individual], n: int = 10) -> None:
    n = min(n, len(top))
    print(f"\n{'='*66}")
    print(f"  TOP {n} TEAMS")
    print(f"{'='*66}")
    for rank, ind in enumerate(top[:n], 1):
        print(
            f"\n#{rank}  Fitness: {ind.fitness:.3f}  "
            f"({ind.series_wins}W-{ind.series_losses}L-{ind.series_ties}T)"
        )
        print(f"  {', '.join(ind.species_list())}")
        print(_format_team(ind.team))
        print(f"  Packed: {ind.packed()}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genetic algorithm team optimizer for Champions VGC.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--pop",          type=int,   default=16,
                        help="Starting population size (number of teams); ignored if --gen0-teams > 0")
    parser.add_argument("--pop-end",      type=int,   default=None,
                        help="Ending population size for linear annealing (default: --pop / Gen-0 survivor count, no annealing)")
    parser.add_argument("--gens",         type=int,   default=20,
                        help="Number of generations")
    parser.add_argument("--rounds",       type=int,   default=5,
                        help="Starting number of random meta opponents per team per generation")
    parser.add_argument("--rounds-end",   type=int,   default=None,
                        help="Ending rounds for annealing (default: --rounds, no annealing)")
    parser.add_argument("--topn-pct",     type=float, default=0.15,
                        help="Fraction of current population carried over unchanged each generation (floored at 2)")
    parser.add_argument("--t0",           type=float, default=0.35,
                        help="Initial mutation probability per slot per type")
    parser.add_argument("--tmin",         type=float, default=0.05,
                        help="Final mutation probability after annealing")
    parser.add_argument("--gen0-teams",   type=int,   default=0,
                        help="If >0, run a Gen-0 viability cull first: generate this many random "
                             "teams, play --gen0-rounds BO3 series each, and keep only teams that "
                             "sweep all rounds as the GA's initial population (overrides --pop)")
    parser.add_argument("--gen0-rounds",  type=int,   default=3,
                        help="BO3 series per team during the Gen-0 cull")
    parser.add_argument("--gen0-min-wins", type=int,  default=None,
                        help="Minimum series wins to survive Gen-0 (default: --gen0-rounds, i.e. a full sweep)")
    parser.add_argument("--gen0-workers", type=int,   default=4,
                        help="Parallel worker processes for the Gen-0 cull")
    parser.add_argument("--seed",         type=int,   default=None,
                        help="RNG seed for reproducibility")
    parser.add_argument("--handler",      choices=["model", "random"], default="model",
                        help="Decision handler used in battles")
    parser.add_argument("--top",          type=int,   default=10,
                        help="Number of teams to print in the final report")
    parser.add_argument("--out",          default=None,
                        help="Optional JSON file to save the top teams")
    parser.add_argument("--checkpoint",   default=None,
                        help="Path to write a checkpoint JSON after each generation "
                             "(e.g. results/run3_ckpt.json). Overwritten each gen so "
                             "you always have the latest state.")
    parser.add_argument("--resume",       default=None,
                        help="Resume from a checkpoint file written by --checkpoint. "
                             "Skips Gen-0 and picks up from the saved generation.")
    args = parser.parse_args()

    rng = random.Random(args.seed)

    # ---- Resume from checkpoint (must happen before gen-0 cull) ----
    start_gen          = 1
    resume_elapsed     = 0.0
    original_pop_start = None
    checkpoint_initial_population = None

    if args.resume:
        ckpt_path = Path(args.resume)
        if not ckpt_path.exists():
            print(f"ERROR: checkpoint file not found: {ckpt_path}")
            return
        meta, checkpoint_initial_population = load_checkpoint(ckpt_path)
        if not checkpoint_initial_population:
            print("ERROR: checkpoint has no population.")
            return
        resumed_gen        = meta["gen"]
        start_gen          = resumed_gen + 1
        resume_elapsed     = float(meta.get("elapsed_s", 0))
        original_pop_start = meta["original_pop_start"]
        v, istate, gnext   = meta["rng_state"]
        rng.setstate((v, tuple(istate), gnext))
        print(
            f"\nLoaded checkpoint: gen {resumed_gen}/{meta['total_gens']} completed  "
            f"({len(checkpoint_initial_population)} teams, "
            f"{resume_elapsed/3600:.1f}h elapsed)"
        )
        if start_gen > meta["total_gens"]:
            print("Checkpoint is already at the final generation — printing results.")
            print_top_teams(checkpoint_initial_population, n=args.top)
            return
        if meta["total_gens"] != args.gens:
            print(
                f"  NOTE: using checkpoint's total_gens={meta['total_gens']} "
                f"(ignoring --gens={args.gens})"
            )
            args.gens = meta["total_gens"]
    # ----------------------------------------------------------------

    print(f"Loading metagame from {PIKALYTICS_DIR} …")
    metagame = load_metagame(PIKALYTICS_DIR, FORMAT)
    print(f"  {len(metagame.pokemon)} Pokémon in pool.")

    if args.handler == "model":
        print(f"Loading model from {MODEL_PATH} …")
        model = BattlePolicy.load(MODEL_PATH)
        vocab = BattleVocab.load(VOCAB_DIR)
        def make_p1():
            return ModelDecisionHandler(model=model, vocab=vocab, side="p1")
        def make_p2():
            return ModelDecisionHandler(model=model, vocab=vocab, side="p2")
    else:
        _ctr = [1]
        def make_p1():
            _ctr[0] += 1
            return RandomDecisionHandler(seed=_ctr[0])
        def make_p2():
            _ctr[0] += 1
            return RandomDecisionHandler(seed=_ctr[0])

    runner = BattleRunner(showdown_path=SHOWDOWN_PATH, format_id=FORMAT)

    initial_population = checkpoint_initial_population  # None unless --resume
    checkpoint_path    = Path(args.checkpoint) if args.checkpoint else None

    if args.resume:
        pass  # population already loaded from checkpoint; skip gen-0 cull entirely
    elif args.gen0_teams > 0:
        min_wins = args.gen0_min_wins if args.gen0_min_wins is not None else args.gen0_rounds
        initial_population = run_gen0_cull(
            n_teams=args.gen0_teams,
            n_rounds=args.gen0_rounds,
            min_wins=min_wins,
            handler_kind=args.handler,
            workers=args.gen0_workers,
            rng=rng,
        )
        if not initial_population:
            print("\nNo teams survived the Gen-0 cull. Try more --gen0-teams or a lower --gen0-min-wins.")
            return
        # Checkpoint the gen-0 survivors so a crash before gen 1 can be resumed.
        if checkpoint_path is not None:
            pop_end_val    = args.pop_end if args.pop_end is not None else len(initial_population)
            rounds_end_val = args.rounds_end if args.rounds_end is not None else args.rounds
            _write_checkpoint(
                checkpoint_path, 0, args.gens,
                len(initial_population), pop_end_val,
                args.rounds, rounds_end_val,
                0.0, rng, initial_population,
            )

    final_pop = run_ga(
        metagame=metagame,
        runner=runner,
        make_p1=make_p1,
        make_p2=make_p2,
        pop_start=args.pop,
        pop_end=args.pop_end if args.pop_end is not None else args.pop,
        generations=args.gens,
        rounds_start=args.rounds,
        rounds_end=args.rounds_end if args.rounds_end is not None else args.rounds,
        topn_pct=args.topn_pct,
        mutation_t0=args.t0,
        mutation_tmin=args.tmin,
        rng=rng,
        initial_population=initial_population,
        start_gen=start_gen,
        original_pop_start=original_pop_start,
        checkpoint_path=checkpoint_path,
        resume_elapsed=resume_elapsed,
    )

    print_top_teams(final_pop, n=args.top)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps([ind.to_dict() for ind in final_pop[:args.top]], indent=2),
            encoding="utf-8",
        )
        print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
