"""Unit tests for genetic_search.py key components.

Run with:  pytest tests/test_genetic_search.py -v

Each section covers one subsystem.  Stochastic tests run N_TRIALS iterations
and assert invariants hold on EVERY iteration — a single violation is a bug.
"""
from __future__ import annotations

import random
import pytest
from showdown_ai.pikalytics import Metagame, PokemonStat, TeamSpec

from genetic_search import (
    SP_MAX, SP_TOTAL,
    _ATK_IDX, _SPA_IDX, _MEGA_STONES,
    _anneal, _base_species, _build_spec, _crossover_teams,
    _ensure_item_clause, _evs_to_str, _max_spread_shift,
    _mutate_individual, _mutate_item, _mutate_move,
    _mutate_species, _mutate_spread, _mutate_spread_distribution,
    _mutation_prob, _parse_evs, Individual,
)

N_TRIALS = 500   # iterations for stochastic invariant checks


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _ps(name: str, items: list[str], moves: list[str],
        spreads: list[tuple]) -> PokemonStat:
    return PokemonStat(
        name=name, usage=50.0, winrate=50.0,
        moves=[(m, 20.0) for m in moves],
        items=[(it, 50.0) for it in items],
        abilities=[("Ability", 100.0)],
        teammates={},
        spreads=spreads,
    )


@pytest.fixture
def rng() -> random.Random:
    return random.Random(0)


@pytest.fixture
def mg() -> Metagame:
    """Mock Metagame: each species has 5 distinct items so item-clause tests
    never exhaust the available pool even on a 6-slot team."""
    m = Metagame(format_id="test", rating="0", date="2026-01", fetched_at="")
    m.pokemon = {
        # Physical (Atk invested, SpA = 0)
        "Incineroar": _ps(
            "Incineroar",
            items=["Safety Goggles", "Sitrus Berry", "Assault Vest", "Lum Berry", "Leftovers"],
            moves=["Fake Out", "Knock Off", "Flare Blitz", "Parting Shot", "Will-O-Wisp"],
            spreads=[
                ("Careful", "4/0/0/0/28/0", 50.0),
                ("Adamant", "0/28/4/0/0/4", 50.0),
            ],
        ),
        # Special (SpA invested, Atk = 0)
        "Gardevoir": _ps(
            "Gardevoir",
            items=["Life Orb", "Choice Specs", "Focus Sash", "Assault Vest", "Lum Berry"],
            moves=["Moonblast", "Psyshock", "Calm Mind", "Protect", "Trick Room"],
            spreads=[
                ("Modest", "0/0/0/28/4/4", 60.0),
                ("Timid", "0/0/4/0/4/24", 40.0),
            ],
        ),
        # Mega Stone holder — item must never mutate
        "Kangaskhan": _ps(
            "Kangaskhan",
            items=["Kangaskhanite", "Sitrus Berry", "Safety Goggles", "Lum Berry", "Leftovers"],
            moves=["Fake Out", "Double-Edge", "Sucker Punch", "Protect"],
            spreads=[("Adamant", "28/0/0/0/4/4", 100.0)],
        ),
        "Urshifu": _ps(
            "Urshifu",
            items=["Choice Band", "Life Orb", "Focus Sash", "Sitrus Berry", "Lum Berry"],
            moves=["Wicked Blow", "Close Combat", "U-turn", "Protect"],
            spreads=[("Jolly", "0/28/0/0/4/4", 100.0)],
        ),
        "Rillaboom": _ps(
            "Rillaboom",
            items=["Choice Band", "Life Orb", "Assault Vest", "Sitrus Berry", "Leftovers"],
            moves=["Grassy Glide", "Wood Hammer", "Fake Out", "U-turn"],
            spreads=[("Adamant", "0/28/0/0/4/4", 100.0)],
        ),
        "Whimsicott": _ps(
            "Whimsicott",
            items=["Focus Sash", "Sitrus Berry", "Safety Goggles", "Lum Berry", "Mental Herb"],
            moves=["Tailwind", "Moonblast", "Encore", "Protect"],
            spreads=[("Timid", "0/0/4/0/4/24", 100.0)],
        ),
        "Landorus-Therian": _ps(
            "Landorus-Therian",
            items=["Choice Scarf", "Rocky Helmet", "Life Orb", "Assault Vest", "Lum Berry"],
            moves=["Earthquake", "Rock Slide", "U-turn", "Protect"],
            spreads=[("Jolly", "0/28/0/0/4/4", 100.0)],
        ),
        "Calyrex-Shadow": _ps(
            "Calyrex-Shadow",
            items=["Choice Specs", "Life Orb", "Focus Sash", "Lum Berry", "Safety Goggles"],
            moves=["Astral Barrage", "Psyshock", "Nasty Plot", "Protect"],
            spreads=[("Timid", "0/0/0/28/4/4", 100.0)],
        ),
    }
    return m


def _spec(species: str, item: str, ev_str: str = "0/0/4/0/4/24",
          nature: str = "Timid", moves: list[str] | None = None) -> TeamSpec:
    return TeamSpec(
        species=species, item=item, ability="Ability",
        moves=moves or ["Move A", "Move B", "Move C", "Move D"],
        nature=nature, ev_str=ev_str,
    )


def _valid_team(mg: Metagame) -> list[TeamSpec]:
    """Six-slot team with unique species and unique items."""
    slots = [
        ("Incineroar",     "Safety Goggles", "4/0/0/0/28/0"),
        ("Gardevoir",      "Life Orb",        "0/0/0/28/4/4"),
        ("Kangaskhan",     "Kangaskhanite",   "28/0/0/0/4/4"),
        ("Urshifu",        "Choice Band",     "0/28/0/0/4/4"),
        ("Rillaboom",      "Focus Sash",      "0/28/0/0/4/4"),
        ("Whimsicott",     "Sitrus Berry",    "0/0/4/0/4/24"),
    ]
    return [_spec(sp, it, ev) for sp, it, ev in slots]


# ===========================================================================
# 1. _base_species
# ===========================================================================

class TestBaseSpecies:
    @pytest.mark.parametrize("name, expected", [
        # Plain name — unchanged
        ("Incineroar",       "Incineroar"),
        ("Gardevoir",        "Gardevoir"),
        # Mega evolutions
        ("Kangaskhan-Mega",  "Kangaskhan"),
        ("Charizard-Mega-X", "Charizard"),
        ("Charizard-Mega-Y", "Charizard"),
        # Regional forms
        ("Ninetales-Alola",  "Ninetales"),
        ("Weezing-Galar",    "Weezing"),
        ("Samurott-Hisui",   "Samurott"),
        # Gender forms
        ("Basculegion-M",    "Basculegion"),
        ("Basculegion-F",    "Basculegion"),
        ("Indeedee-M",       "Indeedee"),
        ("Indeedee-F",       "Indeedee"),
        # Ogerpon masks
        ("Ogerpon-Wellspring",   "Ogerpon"),
        ("Ogerpon-Hearthflame",  "Ogerpon"),
        ("Ogerpon-Cornerstone",  "Ogerpon"),
        # Landorus-Therian should NOT be stripped (no matching suffix)
        ("Landorus-Therian", "Landorus-Therian"),
    ])
    def test_various_forms(self, name, expected):
        assert _base_species(name) == expected

    def test_mega_x_before_mega(self):
        # "Charizard-Mega-X" must resolve to "Charizard", not "Charizard-Mega".
        # Ensures longer suffixes are checked first.
        assert _base_species("Charizard-Mega-X") == "Charizard"

    def test_gender_forms_identical_base(self):
        assert _base_species("Basculegion-M") == _base_species("Basculegion-F")


# ===========================================================================
# 2. EV string parsing
# ===========================================================================

class TestEvParsing:
    def test_roundtrip(self):
        for ev_str in ("0/28/4/0/4/0", "0/0/0/28/4/4", "32/0/0/0/0/0", "0/0/0/0/0/32"):
            assert _evs_to_str(_parse_evs(ev_str)) == ev_str

    def test_padding_to_six(self):
        evs = _parse_evs("4/28")
        assert len(evs) == 6
        assert evs[:2] == [4, 28]
        assert evs[2:] == [0, 0, 0, 0]

    def test_truncates_to_six(self):
        evs = _parse_evs("1/2/3/4/5/6/7/8")
        assert len(evs) == 6


# ===========================================================================
# 3. Annealing schedules
# ===========================================================================

class TestAnnealing:
    def test_anneal_boundary_start(self):
        assert _anneal(1, 20, 1.0, 0.0) == pytest.approx(1.0, abs=1e-6)

    def test_anneal_boundary_end(self):
        # Decay coeff -3 → final value ≈ v_min + (v0-v_min)*e^-3 ≈ v_min + 0.05*(v0-v_min).
        # Verify it converged to within 10% of the full range above v_min.
        v = _anneal(20, 20, 1.0, 0.0)
        assert v < 0.10, f"Annealing did not converge near v_min: {v}"

    def test_anneal_monotone_decreasing(self):
        values = [_anneal(g, 20, 1.0, 0.0) for g in range(1, 21)]
        assert values == sorted(values, reverse=True)

    def test_mutation_prob_approaches_tmin(self):
        # At the last generation the value should be well below the midpoint
        # (confirming it decayed rather than staying flat).
        v = _mutation_prob(20, 20, 0.35, 0.05)
        midpoint = (0.35 + 0.05) / 2
        assert 0.05 <= v <= midpoint, f"Mutation prob not converging toward t_min: {v}"

    def test_max_shift_starts_high(self):
        assert _max_spread_shift(1, 20) == 24

    def test_max_shift_ends_low(self):
        assert _max_spread_shift(20, 20) <= 3

    def test_max_shift_monotone(self):
        values = [_max_spread_shift(g, 20) for g in range(1, 21)]
        assert values == sorted(values, reverse=True)


# ===========================================================================
# 4. _mutate_spread invariants
# ===========================================================================

class TestMutateSpread:
    def test_total_sp_conserved(self, rng):
        spec = _spec("Incineroar", "Safety Goggles", "0/28/4/0/0/4")
        total_before = sum(_parse_evs(spec.ev_str))
        for _ in range(N_TRIALS):
            result = _mutate_spread(spec, rng)
            assert sum(_parse_evs(result.ev_str)) == total_before

    def test_each_stat_in_bounds(self, rng):
        spec = _spec("Urshifu", "Choice Band", "0/28/0/0/4/4")
        for _ in range(N_TRIALS):
            result = _mutate_spread(spec, rng)
            evs = _parse_evs(result.ev_str)
            assert all(0 <= v <= SP_MAX for v in evs), f"Out-of-bounds: {evs}"

    def test_physical_never_gains_spa(self, rng):
        # Atk > 0, SpA = 0 — SpA must remain 0 after every mutation
        spec = _spec("Urshifu", "Choice Band", "0/28/0/0/4/4")
        assert _parse_evs(spec.ev_str)[_ATK_IDX] > 0
        assert _parse_evs(spec.ev_str)[_SPA_IDX] == 0
        for _ in range(N_TRIALS):
            result = _mutate_spread(spec, rng, max_shift=SP_MAX)
            evs = _parse_evs(result.ev_str)
            assert evs[_SPA_IDX] == 0, f"SpA gained on physical attacker: {evs}"

    def test_special_never_gains_atk(self, rng):
        # SpA > 0, Atk = 0 — Atk must remain 0 after every mutation
        spec = _spec("Gardevoir", "Life Orb", "0/0/0/28/4/4")
        assert _parse_evs(spec.ev_str)[_SPA_IDX] > 0
        assert _parse_evs(spec.ev_str)[_ATK_IDX] == 0
        for _ in range(N_TRIALS):
            result = _mutate_spread(spec, rng, max_shift=SP_MAX)
            evs = _parse_evs(result.ev_str)
            assert evs[_ATK_IDX] == 0, f"Atk gained on special attacker: {evs}"

    def test_max_shift_respected(self, rng):
        spec = _spec("Whimsicott", "Focus Sash", "0/0/4/0/4/24")
        for max_s in (1, 2, 4):
            for _ in range(N_TRIALS):
                before = _parse_evs(spec.ev_str)
                after  = _parse_evs(_mutate_spread(spec, rng, max_shift=max_s).ev_str)
                diffs  = [abs(a - b) for a, b in zip(after, before)]
                assert max(diffs) <= max_s, f"Shift exceeded max_shift={max_s}: {diffs}"

    def test_unchanged_when_no_donors(self, rng):
        # All-zero spread has no donor stats — should return unchanged
        spec = _spec("Whimsicott", "Focus Sash", "0/0/0/0/0/0")
        result = _mutate_spread(spec, rng)
        assert result.ev_str == spec.ev_str


# ===========================================================================
# 5. _mutate_spread_distribution
# ===========================================================================

class TestMutateSpreadDistribution:
    def test_picks_from_pool(self, rng, mg):
        spec = _spec("Gardevoir", "Life Orb", "0/0/0/28/4/4", nature="Modest")
        valid_combos = {(n, ev) for n, ev, _ in mg.pokemon["Gardevoir"].spreads}
        for _ in range(100):
            result = _mutate_spread_distribution(spec, mg, rng)
            assert (result.nature, result.ev_str) in valid_combos

    def test_actually_changes(self, rng, mg):
        # With two spreads available, mutation should eventually pick the other one
        spec = _spec("Gardevoir", "Life Orb", "0/0/0/28/4/4", nature="Modest")
        changed = any(
            _mutate_spread_distribution(spec, mg, rng).nature != spec.nature
            for _ in range(100)
        )
        assert changed, "Spread distribution mutation never changed anything"

    def test_single_spread_unchanged(self, rng, mg):
        # Kangaskhan has only one spread — mutation should be a no-op
        spec = _spec("Kangaskhan", "Kangaskhanite", "28/0/0/0/4/4", nature="Adamant")
        result = _mutate_spread_distribution(spec, mg, rng)
        assert (result.nature, result.ev_str) == (spec.nature, spec.ev_str)


# ===========================================================================
# 6. _mutate_move
# ===========================================================================

class TestMutateMove:
    def test_still_has_four_moves(self, rng, mg):
        spec = _spec("Incineroar", "Safety Goggles", "4/0/0/0/28/0",
                     moves=["Fake Out", "Knock Off", "Flare Blitz", "Parting Shot"])
        for _ in range(N_TRIALS):
            result = _mutate_move(spec, mg, rng)
            assert len([m for m in result.moves if m]) == 4

    def test_new_move_from_pool(self, rng, mg):
        valid_moves = {m for m, _ in mg.pokemon["Incineroar"].moves}
        spec = _spec("Incineroar", "Safety Goggles", "4/0/0/0/28/0",
                     moves=["Fake Out", "Knock Off", "Flare Blitz", "Parting Shot"])
        for _ in range(100):
            result = _mutate_move(spec, mg, rng)
            for m in result.moves:
                if m:
                    assert m in valid_moves, f"Unknown move appeared: {m}"

    def test_only_one_move_changes(self, rng, mg):
        spec = _spec("Incineroar", "Safety Goggles", "4/0/0/0/28/0",
                     moves=["Fake Out", "Knock Off", "Flare Blitz", "Parting Shot"])
        for _ in range(100):
            result = _mutate_move(spec, mg, rng)
            n_changed = sum(a != b for a, b in zip(spec.moves, result.moves))
            assert n_changed <= 1, f"More than one move changed: {spec.moves} → {result.moves}"


# ===========================================================================
# 7. _mutate_item
# ===========================================================================

class TestMutateItem:
    def test_mega_stone_never_changes(self, rng, mg):
        spec = _spec("Kangaskhan", "Kangaskhanite", "28/0/0/0/4/4")
        assert spec.item in _MEGA_STONES
        for _ in range(N_TRIALS):
            result = _mutate_item(spec, mg, rng, used_items=set())
            assert result.item == "Kangaskhanite", "Mega Stone was mutated!"

    def test_respects_item_clause(self, rng, mg):
        spec = _spec("Incineroar", "Safety Goggles", "4/0/0/0/28/0")
        # Block 3 of 4 alternatives — one should still be reachable.
        used = {"Sitrus Berry", "Assault Vest", "Lum Berry"}
        for _ in range(N_TRIALS):
            result = _mutate_item(spec, mg, rng, used_items=used)
            assert result.item not in used, f"Item clause violated: {result.item}"

    def test_item_can_change(self, rng, mg):
        spec = _spec("Incineroar", "Safety Goggles", "4/0/0/0/28/0")
        changed = any(
            _mutate_item(spec, mg, rng, used_items=set()).item != spec.item
            for _ in range(100)
        )
        assert changed, "Item never changed despite alternatives being available"

    def test_unchanged_when_pool_exhausted(self, rng, mg):
        # Block all of Incineroar's non-current items — pool becomes empty.
        spec = _spec("Incineroar", "Safety Goggles", "4/0/0/0/28/0")
        all_others = {"Sitrus Berry", "Assault Vest", "Lum Berry", "Leftovers"}
        result = _mutate_item(spec, mg, rng, used_items=all_others)
        assert result.item == "Safety Goggles"  # unchanged when pool is empty


# ===========================================================================
# 8. _ensure_item_clause
# ===========================================================================

class TestEnsureItemClause:
    def test_no_duplicates_after_call(self, rng, mg):
        # Give two Pokemon the same item
        team = _valid_team(mg)
        team[1] = _spec("Gardevoir", team[0].item, "0/0/0/28/4/4")  # dup item
        result = _ensure_item_clause(team, mg, rng)
        items = [ts.item for ts in result if ts.item]
        assert len(items) == len(set(items)), f"Duplicate items remain: {items}"

    def test_mega_stone_preserved(self, rng, mg):
        team   = _valid_team(mg)
        result = _ensure_item_clause(team, mg, rng)
        kang   = next(ts for ts in result if ts.species == "Kangaskhan")
        assert kang.item == "Kangaskhanite"

    def test_valid_team_unchanged(self, rng, mg):
        # A team with already-unique items should come out identical
        team   = _valid_team(mg)
        result = _ensure_item_clause(team, mg, rng)
        assert [ts.item for ts in result] == [ts.item for ts in team]


# ===========================================================================
# 9. _mutate_species
# ===========================================================================

class TestMutateSpecies:
    def test_no_duplicate_base_species(self, rng, mg):
        team = _valid_team(mg)
        for slot in range(len(team)):
            used_bases = {_base_species(ts.species) for i, ts in enumerate(team) if i != slot}
            used_items = {ts.item for i, ts in enumerate(team) if i != slot and ts.item}
            result = _mutate_species(team, slot, mg, rng, used_bases, used_items)
            bases = [_base_species(ts.species) for ts in result]
            assert len(bases) == len(set(bases)), f"Duplicate species after mutation: {bases}"

    def test_no_item_clause_violation(self, rng, mg):
        team = _valid_team(mg)
        for slot in range(len(team)):
            used_bases = {_base_species(ts.species) for i, ts in enumerate(team) if i != slot}
            used_items = {ts.item for i, ts in enumerate(team) if i != slot and ts.item}
            for _ in range(20):
                result = _mutate_species(team, slot, mg, rng, used_bases, used_items)
                items = [ts.item for ts in result if ts.item]
                assert len(items) == len(set(items)), f"Item clause violated: {items}"


# ===========================================================================
# 10. _mutate_individual — independence and clause invariants
# ===========================================================================

class TestMutateIndividual:
    def test_species_clause_maintained(self, rng, mg):
        ind = Individual(team=_valid_team(mg))
        for _ in range(50):
            result = _mutate_individual(ind, mg, mutation_prob=1.0, rng=rng, max_shift=24)
            bases = [_base_species(ts.species) for ts in result.team]
            assert len(bases) == len(set(bases)), f"Species clause broken: {bases}"

    def test_item_clause_maintained(self, rng, mg):
        ind = Individual(team=_valid_team(mg))
        for _ in range(50):
            result = _mutate_individual(ind, mg, mutation_prob=1.0, rng=rng, max_shift=24)
            items = [ts.item for ts in result.team if ts.item]
            assert len(items) == len(set(items)), f"Item clause broken: {items}"

    def test_mega_stone_preserved(self, rng, mg):
        ind = Individual(team=_valid_team(mg))
        # Find the slot index holding Kangaskhan in the original team.
        orig_slot = next(i for i, ts in enumerate(ind.team) if "Kangaskhan" in ts.species)
        for _ in range(N_TRIALS):
            result = _mutate_individual(ind, mg, mutation_prob=1.0, rng=rng, max_shift=24)
            slot_spec = result.team[orig_slot]
            # Only assert if the slot still holds Kangaskhan (species mutation may replace it).
            if "Kangaskhan" in slot_spec.species:
                assert slot_spec.item == "Kangaskhanite", "Mega Stone stripped from Kangaskhan"

    def test_spread_and_move_can_both_change(self, rng, mg):
        # With prob=1.0, spread AND move mutations should both fire on at least
        # one slot across many trials (independence check).
        ind = Individual(team=_valid_team(mg))
        both_changed = False
        for _ in range(200):
            result = _mutate_individual(ind, mg, mutation_prob=1.0, rng=rng, max_shift=16)
            for orig, new in zip(ind.team, result.team):
                spread_changed = orig.ev_str != new.ev_str or orig.nature != new.nature
                move_changed   = orig.moves != new.moves
                if spread_changed and move_changed:
                    both_changed = True
                    break
            if both_changed:
                break
        assert both_changed, "Spread and move mutations never both fired on the same slot"

    def test_sp_total_conserved_after_mutation(self, rng, mg):
        ind = Individual(team=_valid_team(mg))
        for _ in range(N_TRIALS):
            result = _mutate_individual(ind, mg, mutation_prob=0.5, rng=rng, max_shift=8)
            for ts in result.team:
                total = sum(_parse_evs(ts.ev_str))
                assert total <= SP_TOTAL, f"SP total exceeded for {ts.species}: {total}"


# ===========================================================================
# 11. _crossover_teams
# ===========================================================================

class TestCrossoverTeams:
    def _parent_b(self, mg: Metagame) -> list[TeamSpec]:
        slots = [
            ("Calyrex-Shadow", "Choice Specs",  "0/0/0/28/4/4"),
            ("Landorus-Therian", "Choice Scarf", "0/28/0/0/4/4"),
            ("Urshifu",        "Life Orb",       "0/28/0/0/4/4"),
            ("Rillaboom",      "Choice Band",    "0/28/0/0/4/4"),
            ("Whimsicott",     "Focus Sash",     "0/0/4/0/4/24"),
            ("Incineroar",     "Sitrus Berry",   "4/0/0/0/28/0"),
        ]
        return [_spec(sp, it, ev) for sp, it, ev in slots]

    def test_always_six_pokemon(self, rng, mg):
        pa = _valid_team(mg)
        pb = self._parent_b(mg)
        for _ in range(100):
            child = _crossover_teams(pa, pb, mg, rng)
            assert len(child) == 6

    def test_no_duplicate_base_species(self, rng, mg):
        pa = _valid_team(mg)
        pb = self._parent_b(mg)
        for _ in range(N_TRIALS):
            child = _crossover_teams(pa, pb, mg, rng)
            bases = [_base_species(ts.species) for ts in child]
            assert len(bases) == len(set(bases)), f"Duplicate species in child: {bases}"

    def test_no_duplicate_items(self, rng, mg):
        pa = _valid_team(mg)
        pb = self._parent_b(mg)
        for _ in range(N_TRIALS):
            child = _crossover_teams(pa, pb, mg, rng)
            items = [ts.item for ts in child if ts.item]
            assert len(items) == len(set(items)), f"Duplicate items in child: {items}"

    def test_child_contains_dna_from_both_parents(self, rng, mg):
        pa = _valid_team(mg)
        pb = self._parent_b(mg)
        pa_bases = {_base_species(ts.species) for ts in pa}
        pb_bases = {_base_species(ts.species) for ts in pb}
        got_from_a = got_from_b = False
        for _ in range(200):
            child = _crossover_teams(pa, pb, mg, rng)
            child_bases = {_base_species(ts.species) for ts in child}
            if child_bases & pa_bases:
                got_from_a = True
            if child_bases & pb_bases:
                got_from_b = True
            if got_from_a and got_from_b:
                break
        assert got_from_a and got_from_b, "Crossover never drew from both parents"


# ===========================================================================
# 12. _build_spec
# ===========================================================================

class TestBuildSpec:
    def test_produces_valid_teamspec(self, rng, mg):
        for species in mg.pokemon:
            spec = _build_spec(species, mg, rng)
            assert spec.species == species
            assert len(spec.moves) == 4
            assert spec.nature
            evs = _parse_evs(spec.ev_str)
            assert len(evs) == 6
            assert all(0 <= v <= SP_MAX for v in evs)

    def test_respects_forbidden_items(self, rng, mg):
        # Block the top item only — the species still has valid alternatives.
        forbidden = {"Safety Goggles"}
        for _ in range(100):
            spec = _build_spec("Incineroar", mg, rng, forbidden_items=forbidden)
            assert spec.item not in forbidden, f"Forbidden item appeared: {spec.item}"


# ===========================================================================
# 13. Individual fitness
# ===========================================================================

class TestIndividualFitness:
    def test_zero_fitness_before_evaluation(self, mg):
        ind = Individual(team=_valid_team(mg))
        assert ind.fitness == 0.0
        assert ind.series_played == 0

    def test_fitness_all_wins(self, mg):
        ind = Individual(team=_valid_team(mg), series_wins=10, series_losses=0, series_ties=0)
        assert ind.fitness == pytest.approx(1.0)

    def test_fitness_tie_counts_half(self, mg):
        ind = Individual(team=_valid_team(mg), series_wins=0, series_losses=0, series_ties=4)
        assert ind.fitness == pytest.approx(0.5)

    def test_reset_clears_record(self, mg):
        ind = Individual(team=_valid_team(mg), series_wins=5, series_losses=3, series_ties=1)
        ind.reset_fitness()
        assert ind.series_played == 0
        assert ind.fitness == 0.0
