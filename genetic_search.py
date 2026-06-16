"""Genetic algorithm team optimizer for Champions VGC.

Evolves a population of teams using best-of-3 battles against fresh random
meta-representative opponents as the fitness function.  Mutation rate decays
via simulated annealing so early generations explore broadly and later ones
refine the best candidates.

Usage::

    python genetic_search.py                                         # 16 teams, 20 gens, 5 rounds, AI
    python genetic_search.py --pop 48 --pop-end 16 --rounds 5 --rounds-end 16
    python genetic_search.py --pop 8 --gens 10 --rounds 3
    python genetic_search.py --handler random
    python genetic_search.py --seed 42 --out results/run1.json

At the end the top N teams are printed with fitness, record, and packed strings.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
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
MODEL_PATH     = Path("checkpoints/policy.pt")
VOCAB_DIR      = Path("checkpoints/vocab")
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
    # Regional forms (Ninetales-Alola and Ninetales treated as same species)
    "-Alola", "-Galar", "-Hisui", "-Paldea",
    # Gender forms (Basculegion-M / -F, Indeedee-M / -F)
    "-M", "-F",
    # Ogerpon masks
    "-Wellspring", "-Hearthflame", "-Cornerstone",
    # Terapagos
    "-Terastal", "-Stellar",
    # Misc time/form variants
    "-Dawn", "-Dusk", "-Midnight", "-Midday",
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


_NON_MOVES = frozenset({"Other", "None", "Nothing", ""})


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
        pool = [(it, w) for it, w in stat.items if it not in blocked]
        if not pool:
            pool = list(stat.items)       # fall back if everything is blocked
        # Mega Stone holders should always receive their Mega Stone if available.
        mega_pool = [(it, w) for it, w in pool if it in _MEGA_STONES]
        if mega_pool:
            item = mega_pool[0][0]
        else:
            names, wts = zip(*pool)
            item = _weighted_pick(list(names), list(wts), rng) or ""
    if stat.abilities:
        names, wts = zip(*stat.abilities)
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
    pool = [it for it, _ in stat.items if it != spec.item and it not in used_items]
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

    n            = len(population)
    total_series = n * n_rounds
    done         = 0

    for i, ind in enumerate(population):
        for _ in range(n_rounds):
            # Generate a fresh random opponent from the current meta
            opp_team   = generate_team(metagame, rng=rng)
            opp_team   = _ensure_item_clause(opp_team, metagame, rng)
            opp_packed = team_to_packed(opp_team)

            t0       = time.time()
            wa, wb, wt = _battle_best_of_3(
                ind.packed(), opp_packed, runner, make_p1, make_p2
            )

            if wa > wb:
                ind.series_wins   += 1
                result_str = f"WIN  {wa}-{wb}-{wt}"
            elif wb > wa:
                ind.series_losses += 1
                result_str = f"LOSS {wa}-{wb}-{wt}"
            else:
                ind.series_ties   += 1
                result_str = f"TIE  {wa}-{wb}-{wt}"

            done += 1
            print(
                f"  [{done:>3}/{total_series}] "
                f"Team {i+1:>2} ({', '.join(ind.species_list()[:2])}) "
                f"vs random  →  {result_str}  ({time.time()-t0:.1f}s)",
                flush=True,
            )


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
    """Anneal population size from pop_start down to ~pop_end over n_gens.

    Uses the same exponential decay as mutation prob; reaches ~pop_end + 5% of
    the range at the final generation (exp(-3) ≈ 0.05).
    """
    return max(1, round(_anneal(gen, n_gens, float(pop_start), float(pop_end))))


def _rounds_schedule(gen: int, n_gens: int, rounds_start: int, rounds_end: int) -> int:
    """Anneal rounds per team from rounds_start up to ~rounds_end over n_gens.

    When rounds_start < rounds_end the _anneal formula naturally grows (the
    v0 - v_min term is negative), so no separate function is needed.
    """
    return max(1, round(_anneal(gen, n_gens, float(rounds_start), float(rounds_end))))


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
    topn:          int   = 4,
    mutation_t0:   float = 0.35,
    mutation_tmin: float = 0.05,
    rng: Optional[random.Random] = None,
) -> list[Individual]:
    if rng is None:
        rng = random.Random()

    pop_annealed    = pop_start != pop_end
    rounds_annealed = rounds_start != rounds_end

    def _pop_g(gen: int) -> int:
        return _pop_schedule(gen, generations, pop_start, pop_end)

    def _rounds_g(gen: int) -> int:
        return _rounds_schedule(gen, generations, rounds_start, rounds_end)

    max_series = _pop_g(1) * _rounds_g(1) * 3   # rough upper bound for gen 1

    print(f"\n{'='*66}")
    print(f"  GENETIC TEAM SEARCH  —  Champions VGC")
    print(f"{'='*66}")
    if pop_annealed:
        print(f"  Population  : {pop_start} -> {pop_end} teams  (annealed)")
    else:
        print(f"  Population  : {pop_start} teams")
    print(f"  Generations : {generations}")
    if rounds_annealed:
        print(f"  Rounds      : {rounds_start} -> {rounds_end} per team per gen  (annealed)")
    else:
        print(f"  Rounds      : {rounds_start} per team per gen")
    print(f"  Top-N keep  : {topn}")
    print(f"  Mutation    : {mutation_t0:.2f} -> {mutation_tmin:.2f}  (simulated annealing)")
    print(f"  Max series  : ~{max_series} in generation 1  (BO3 series)")
    print(f"{'='*66}")

    # Initialize population at pop_start size
    print(f"\nInitializing {pop_start} random teams ...")
    population: list[Individual] = []
    for idx in range(pop_start):
        team = generate_team(metagame, rng=rng)
        team = _ensure_item_clause(team, metagame, rng)
        ind  = Individual(team=team)
        print(f"  Team {idx+1:>2}: {', '.join(ind.species_list())}")
        population.append(ind)

    total_start = time.time()

    for gen in range(1, generations + 1):
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

        print(f"\n{'─'*66}")
        print(
            f"  Generation {gen}/{generations}  |  "
            f"pop={pop_g}  |  rounds={rounds_g}  |  "
            f"mutation={mu:.3f}  |  {pop_g * rounds_g} series"
        )
        print(f"{'─'*66}")

        _evaluate_generation(
            population, metagame, runner, make_p1, make_p2, rounds_g, rng
        )
        population.sort(key=lambda x: -x.fitness)

        gen_time = time.time() - gen_start
        _log_generation(gen, generations, population, gen_time, mu, shift, rounds_g)

        if gen == generations:
            break

        # Build next generation sized to the next scheduled population count.
        pop_next = _pop_g(gen + 1)
        next_gen: list[Individual] = []

        # Top-N elites carry over unchanged (fitness reset for next round).
        n_elite = min(topn, pop_next)
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
    parser.add_argument("--pop",         type=int,   default=16,
                        help="Starting population size (number of teams)")
    parser.add_argument("--pop-end",    type=int,   default=None,
                        help="Ending population size for annealing (default: --pop, no annealing)")
    parser.add_argument("--gens",       type=int,   default=20,
                        help="Number of generations")
    parser.add_argument("--rounds",     type=int,   default=5,
                        help="Starting number of random meta opponents per team per generation")
    parser.add_argument("--rounds-end", type=int,   default=None,
                        help="Ending rounds for annealing (default: --rounds, no annealing)")
    parser.add_argument("--topn",       type=int,   default=4,
                        help="Top-N teams carried over unchanged each generation")
    parser.add_argument("--t0",         type=float, default=0.35,
                        help="Initial mutation probability per slot per type")
    parser.add_argument("--tmin",       type=float, default=0.05,
                        help="Final mutation probability after annealing")
    parser.add_argument("--seed",      type=int,   default=None,
                        help="RNG seed for reproducibility")
    parser.add_argument("--handler",   choices=["model", "random"], default="model",
                        help="Decision handler used in battles")
    parser.add_argument("--top",       type=int,   default=10,
                        help="Number of teams to print in the final report")
    parser.add_argument("--out",       default=None,
                        help="Optional JSON file to save the top teams")
    args = parser.parse_args()

    rng = random.Random(args.seed)

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
        topn=args.topn,
        mutation_t0=args.t0,
        mutation_tmin=args.tmin,
        rng=rng,
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
    main()
