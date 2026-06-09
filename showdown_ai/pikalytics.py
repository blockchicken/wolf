"""Pikalytics metagame scraper and random team generator.

The Pikalytics JSON API returns the full usage list with per-Pokémon move,
item, ability, teammate, and stat-spread breakdowns in a single request:

    GET https://www.pikalytics.com/api/l/{date}/{format}-{rating}

The ``spreads`` field in the API uses the Champions SP scale (0–32 per stat,
66 total), which maps directly to Showdown's packed team EV field.

Stored locally as JSON under a user-specified directory.  Filenames use
the pattern ``{format}-{rating}-{date}.json`` so multiple rating tiers and
historical snapshots can coexist.

Team generation uses a **log-linear conditional sampling** model:

    score(candidate | team) ∝ P(candidate) × Π  P(candidate | picked) / P(candidate)
                                                picked ∈ team

This is a naive-Bayes combination of pairwise teammate correlations.
When no correlation data exists for a pair the contribution is 1 (no update).

Usage::

    # Scrape and store (once per period)
    from showdown_ai.pikalytics import scrape_metagame, generate_packed_team

    scrape_metagame("gen9championsvgc2026regma", "data/pikalytics", rating="1500")

    # Generate teams from stored data
    team_str = generate_packed_team("data/pikalytics", "gen9championsvgc2026regma", rating="1500")
    # → packed team string ready for BattleRunner

    # CLI
    python -m showdown_ai.pikalytics --format gen9championsvgc2026regma --out data/pikalytics
"""

from __future__ import annotations

import json
import logging
import math
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BASE = "https://www.pikalytics.com"

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Stat slot indices in the "hp/atk/def/spa/spd/spe" spread string
_STAT_NAMES = ("HP", "Atk", "Def", "SpA", "SpD", "Spe")


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _get_json(url: str, retries: int = 3, delay: float = 1.0) -> Optional[object]:
    headers = {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "application/json, text/plain, */*",
        "Referer": f"{_BASE}/",
    }
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = 2 ** attempt * 3.0
                logger.warning(f"Rate-limited (429) — waiting {wait:.0f}s")
                time.sleep(wait)
            elif exc.code in (403, 404):
                logger.warning(f"HTTP {exc.code}: {url}")
                return None
            else:
                logger.error(f"HTTP {exc.code}: {url}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
        except Exception as exc:
            logger.error(f"Request error ({exc}): {url}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt * delay)
    return None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PokemonStat:
    name: str
    usage: float            # overall usage percentage (0–100)
    winrate: float          # win rate (0–100)
    moves:      list[tuple[str, float]]   # [(move_name, pct), …] sorted desc
    items:      list[tuple[str, float]]   # [(item_name, pct), …]
    abilities:  list[tuple[str, float]]   # [(ability_name, pct), …]
    teammates:  dict[str, float]          # {species: pct}  (P(teammate | this mon))
    spreads:    list[tuple[str, str, float]]  # [(nature, "hp/atk/def/spa/spd/spe", pct), …]


@dataclass
class Metagame:
    format_id: str
    rating:    str
    date:      str
    fetched_at: str
    pokemon:   dict[str, PokemonStat] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_pct(raw: str | float | int) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _parse_entry(raw: dict) -> Optional[PokemonStat]:
    name = raw.get("name") or raw.get("display_name")
    if not name:
        return None

    usage   = _parse_pct(raw.get("percent", 0))
    winrate = _parse_pct(raw.get("winPercent", 50))

    moves = sorted(
        [(m["move"], _parse_pct(m["percent"])) for m in raw.get("moves", [])],
        key=lambda x: -x[1],
    )
    items = sorted(
        [(i["item"], _parse_pct(i["percent"])) for i in raw.get("items", [])],
        key=lambda x: -x[1],
    )
    abilities = sorted(
        [(a["ability"], _parse_pct(a["percent"])) for a in raw.get("abilities", [])],
        key=lambda x: -x[1],
    )
    teammates = {
        t["pokemon"]: _parse_pct(t["percent"])
        for t in raw.get("team", [])
        if t.get("pokemon")
    }
    spreads = sorted(
        [
            (s["nature"], s["ev"], _parse_pct(s.get("percent", 0)))
            for s in raw.get("spreads", [])
            if s.get("nature") and s.get("ev")
        ],
        key=lambda x: -x[2],
    )

    return PokemonStat(
        name=name,
        usage=usage,
        winrate=winrate,
        moves=moves,
        items=items,
        abilities=abilities,
        teammates=teammates,
        spreads=spreads,
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _stat_to_dict(stat: PokemonStat) -> dict:
    return {
        "name":      stat.name,
        "usage":     stat.usage,
        "winrate":   stat.winrate,
        "moves":     stat.moves,
        "items":     stat.items,
        "abilities": stat.abilities,
        "teammates": stat.teammates,
        "spreads":   stat.spreads,
    }


def _stat_from_dict(d: dict) -> PokemonStat:
    return PokemonStat(
        name      = d["name"],
        usage     = d["usage"],
        winrate   = d["winrate"],
        moves     = [tuple(m) for m in d.get("moves", [])],
        items     = [tuple(i) for i in d.get("items", [])],
        abilities = [tuple(a) for a in d.get("abilities", [])],
        teammates = d.get("teammates", {}),
        spreads   = [tuple(s) for s in d.get("spreads", [])],
    )


def _metagame_to_dict(mg: Metagame) -> dict:
    return {
        "format_id":  mg.format_id,
        "rating":     mg.rating,
        "date":       mg.date,
        "fetched_at": mg.fetched_at,
        "pokemon":    {n: _stat_to_dict(p) for n, p in mg.pokemon.items()},
    }


def _metagame_from_dict(d: dict) -> Metagame:
    mg = Metagame(
        format_id  = d["format_id"],
        rating     = d["rating"],
        date       = d["date"],
        fetched_at = d.get("fetched_at", ""),
    )
    mg.pokemon = {n: _stat_from_dict(p) for n, p in d["pokemon"].items()}
    return mg


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def _current_ym() -> str:
    """Return the current year-month string, e.g. '2026-05'."""
    now = datetime.now()
    return f"{now.year}-{now.month:02d}"


def _prev_ym(ym: str) -> str:
    """'2026-06' → '2026-05', '2026-01' → '2025-12'."""
    year, month = int(ym[:4]), int(ym[5:])
    month -= 1
    if month == 0:
        month = 12
        year -= 1
    return f"{year}-{month:02d}"


def _name_to_slug(name: str) -> str:
    """Convert a pikalytics display name to an API slug (lowercase, hyphenated).

    Examples
    --------
    'Incineroar'     → 'incineroar'
    'Charizard-Mega-Y' → 'charizard-mega-y'
    'Ninetales-Alola'  → 'ninetales-alola'
    """
    return name.lower().replace(" ", "-")


def _fetch_detail(
    name: str,
    format_id: str,
    date: str,
    rating: str,
    base_stat: PokemonStat,
) -> PokemonStat:
    """Fetch per-Pokémon detail from the ``/api/p/`` endpoint and merge into base_stat."""
    slug = _name_to_slug(name)
    url  = f"{_BASE}/api/p/{date}/{format_id}-{rating}/{slug}"
    raw  = _get_json(url)
    if not raw or not isinstance(raw, dict):
        logger.warning(f"No detail data for {name} ({url})")
        return base_stat

    # Moves
    base_stat.moves = sorted(
        [(m["move"], _parse_pct(m["percent"])) for m in raw.get("moves", [])],
        key=lambda x: -x[1],
    )
    # Items
    base_stat.items = sorted(
        [(i["item"], _parse_pct(i["percent"])) for i in raw.get("items", [])],
        key=lambda x: -x[1],
    )
    # Abilities
    base_stat.abilities = sorted(
        [(a["ability"], _parse_pct(a["percent"])) for a in raw.get("abilities", [])],
        key=lambda x: -x[1],
    )
    # Spreads
    base_stat.spreads = sorted(
        [
            (s["nature"], s["ev"], _parse_pct(s.get("percent", 0)))
            for s in raw.get("spreads", [])
            if s.get("nature") and s.get("ev")
        ],
        key=lambda x: -x[2],
    )
    # Teammates (may be more complete in the detail endpoint)
    detail_teammates = {
        t["pokemon"]: _parse_pct(t["percent"])
        for t in raw.get("team", [])
        if t.get("pokemon")
    }
    if detail_teammates:
        base_stat.teammates = detail_teammates

    return base_stat


def scrape_metagame(
    format_id: str,
    output_dir: str | Path,
    *,
    date: Optional[str] = None,
    rating: str = "1500",
    min_usage: float = 0.5,
    workers: int = 5,
    request_delay: float = 0.4,
    verbose: bool = True,
) -> Metagame:
    """Fetch metagame data from Pikalytics and save it locally.

    Uses a two-phase approach:

    **Phase 1** — fetch the usage list (one request).
    **Phase 2** — fetch per-Pokémon detail pages concurrently for moves,
    items, abilities, spreads, and teammate correlations.

    Parameters
    ----------
    format_id:
        Showdown format ID, e.g. ``"gen9championsvgc2026regma"``.
    output_dir:
        Directory to write the JSON file.
    date:
        Period string ``"YYYY-MM"`` (defaults to the current month with
        automatic fallback to the prior month if not yet published).
    rating:
        Rating tier suffix, e.g. ``"1500"``, ``"1250"``, or ``"0"`` for all.
    min_usage:
        Minimum usage percentage to include (default 0.5 %).
    workers:
        Concurrent detail-page fetch threads (default 5).
    request_delay:
        Seconds each worker sleeps before its request to avoid hammering the API.
    verbose:
        Print progress to stdout.

    Returns
    -------
    The populated :class:`Metagame` object (also saved to disk).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if date is None:
        date = _current_ym()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Phase 1: usage list
    # ------------------------------------------------------------------
    for attempt_date in [date, _prev_ym(date)]:
        url = f"{_BASE}/api/l/{attempt_date}/{format_id}-{rating}"
        if verbose:
            print(f"Phase 1: fetching usage list from {url} …")
        raw_list = _get_json(url)
        if raw_list and isinstance(raw_list, list):
            date = attempt_date
            break
        if verbose:
            print(f"  No data for {attempt_date}, trying previous month …")
    else:
        raise RuntimeError(
            f"No data found for {format_id}-{rating} in {date} or {_prev_ym(date)}. "
            f"Pass --date YYYY-MM explicitly."
        )

    # Build initial PokemonStat objects from list data (usage + partial teammates)
    initial: dict[str, PokemonStat] = {}
    for raw in raw_list:
        stat = _parse_entry(raw)
        if stat is None or stat.usage < min_usage:
            continue
        initial[stat.name] = stat

    if verbose:
        print(f"  {len(initial)} Pokémon above {min_usage}% usage threshold.")

    # ------------------------------------------------------------------
    # Phase 2: per-Pokémon detail (concurrent)
    # ------------------------------------------------------------------
    if verbose:
        print(f"Phase 2: fetching detail data for {len(initial)} Pokémon "
              f"using {workers} workers …")

    mg = Metagame(
        format_id=format_id,
        rating=rating,
        date=date,
        fetched_at=datetime.now().isoformat(),
    )

    done = 0

    def _fetch_one(name: str) -> tuple[str, PokemonStat]:
        time.sleep(random.uniform(0, request_delay))
        return name, _fetch_detail(name, format_id, date, rating, initial[name])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_fetch_one, n): n for n in initial}
        for future in as_completed(futures):
            name, stat = future.result()
            mg.pokemon[name] = stat
            done += 1
            if verbose and done % 20 == 0:
                print(f"  {done}/{len(initial)} complete …")

    if verbose:
        print(f"  All {len(mg.pokemon)} Pokémon fetched.")

    out_path = output_dir / f"{format_id}-{rating}-{date}.json"
    out_path.write_text(
        json.dumps(_metagame_to_dict(mg), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if verbose:
        print(f"  Saved to {out_path}")

    return mg


def load_metagame(
    data_dir: str | Path,
    format_id: str,
    *,
    rating: str = "1500",
    date: Optional[str] = None,
) -> Metagame:
    """Load the most-recent (or specific) saved metagame snapshot.

    Parameters
    ----------
    data_dir:
        Directory containing the saved ``.json`` files.
    format_id:
        Format ID used when scraping.
    rating:
        Rating tier to load.
    date:
        Exact date string to load, or None to pick the newest available file.
    """
    data_dir = Path(data_dir)

    if date is not None:
        path = data_dir / f"{format_id}-{rating}-{date}.json"
        if not path.exists():
            raise FileNotFoundError(path)
    else:
        candidates = sorted(
            data_dir.glob(f"{format_id}-{rating}-*.json"),
            key=lambda p: p.name,
        )
        if not candidates:
            raise FileNotFoundError(
                f"No metagame files found in {data_dir} for format={format_id} rating={rating}"
            )
        path = candidates[-1]   # alphabetically last = most recent date

    return _metagame_from_dict(json.loads(path.read_text(encoding="utf-8")))


# ---------------------------------------------------------------------------
# Weighted sampling helpers
# ---------------------------------------------------------------------------

def _weighted_pick(items: list, weights: list[float], rng: random.Random) -> object:
    total = sum(weights)
    if total <= 0:
        return rng.choice(items)
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
    """Weighted sample without replacement."""
    remaining_items   = list(items)
    remaining_weights = list(weights)
    result = []
    for _ in range(min(k, len(remaining_items))):
        total = sum(remaining_weights)
        if total <= 0:
            result.extend(rng.sample(remaining_items, min(k - len(result), len(remaining_items))))
            break
        r = rng.uniform(0, total)
        acc = 0.0
        chosen_idx = len(remaining_items) - 1
        for idx, w in enumerate(remaining_weights):
            acc += w
            if acc >= r:
                chosen_idx = idx
                break
        result.append(remaining_items[chosen_idx])
        remaining_items.pop(chosen_idx)
        remaining_weights.pop(chosen_idx)
    return result


# ---------------------------------------------------------------------------
# Scoring / team generation
# ---------------------------------------------------------------------------

def _candidate_score(
    candidate: str,
    team: list[str],
    pokemon: dict[str, PokemonStat],
) -> float:
    """Log-linear score for adding candidate given the current team."""
    base = max(pokemon[candidate].usage / 100.0, 1e-9)
    if not team:
        return base

    # Apply lift from each already-chosen Pokémon's teammate data
    log_score = math.log(base)
    for picked in team:
        t_pct = pokemon[picked].teammates.get(candidate)
        if t_pct is None:
            continue   # no correlation data → no adjustment
        t_prob = max(t_pct / 100.0, 1e-9)
        log_score += math.log(t_prob) - math.log(base)

    return math.exp(log_score)


@dataclass
class TeamSpec:
    """One Pokémon slot in a generated team."""
    species:  str
    item:     str
    ability:  str
    moves:    list[str]           # exactly 4
    nature:   str
    ev_str:   str                 # packed EV string e.g. "0/32/2/0/0/32"


def generate_team(
    metagame: Metagame,
    *,
    size: int = 6,
    rng: Optional[random.Random] = None,
    top_moves: int = 8,
    min_move_pct: float = 2.0,
) -> list[TeamSpec]:
    """Generate one team using conditional weighted sampling.

    Parameters
    ----------
    metagame:
        Loaded metagame snapshot.
    size:
        Number of Pokémon to generate (6 for a full VGC team).
    rng:
        Optional seeded ``random.Random`` instance for reproducibility.
    top_moves:
        Consider only the top-N moves (by usage) when sampling the moveset.
    min_move_pct:
        Exclude moves below this usage percentage from sampling.

    Returns
    -------
    List of :class:`TeamSpec` objects.
    """
    if rng is None:
        rng = random.Random()

    pokemon = metagame.pokemon
    all_names = list(pokemon.keys())

    def _base_species(name: str) -> str:
        """Strip Mega/regional suffixes for duplicate detection."""
        for suffix in ("-Mega-X", "-Mega-Y", "-Mega"):
            if name.endswith(suffix):
                return name[: -len(suffix)]
        return name

    # --- Step 1: build the team composition ---
    team_names: list[str] = []
    for _ in range(size):
        team_bases = {_base_species(n) for n in team_names}
        candidates = [
            n for n in all_names
            if n not in team_names and _base_species(n) not in team_bases
        ]
        if not candidates:
            break
        scores = [_candidate_score(c, team_names, pokemon) for c in candidates]
        chosen = _weighted_pick(candidates, scores, rng)
        team_names.append(chosen)

    # --- Step 2: build each slot's moveset/item/ability/spread ---
    specs: list[TeamSpec] = []
    for name in team_names:
        stat = pokemon[name]

        # Item
        if stat.items:
            item_names, item_weights = zip(*stat.items)
            item = _weighted_pick(list(item_names), list(item_weights), rng)
        else:
            item = ""

        # Ability
        if stat.abilities:
            ab_names, ab_weights = zip(*stat.abilities)
            ability = _weighted_pick(list(ab_names), list(ab_weights), rng)
        else:
            ability = ""

        # Moves: sample 4 from the top-N most used.
        # Exclude pikalytics's "Other" catch-all and any non-move placeholders.
        _NON_MOVES = {"Other", "None", "Nothing", ""}
        move_pool = [
            (m, w) for m, w in stat.moves[:top_moves]
            if w >= min_move_pct and m not in _NON_MOVES
        ]
        if len(move_pool) < 4:
            move_pool = stat.moves[:max(4, len(move_pool))]
        if move_pool:
            mv_names, mv_weights = zip(*move_pool)
            moves = _weighted_sample_no_replace(list(mv_names), list(mv_weights), 4, rng)
        else:
            moves = []
        while len(moves) < 4:
            moves.append("")

        # Spread & nature
        if stat.spreads:
            sp_names_w, sp_ev_w, sp_pct_w = zip(*stat.spreads)
            chosen_idx = _weighted_pick(
                list(range(len(stat.spreads))),
                list(sp_pct_w),
                rng,
            )
            nature = sp_names_w[chosen_idx]
            ev_str = sp_ev_w[chosen_idx]
        else:
            nature = "Timid"
            ev_str = "0/0/0/0/0/32"   # only Speed, as a safe fallback

        specs.append(TeamSpec(
            species=name,
            item=item,
            ability=ability,
            moves=moves,
            nature=nature,
            ev_str=ev_str,
        ))

    return specs


# ---------------------------------------------------------------------------
# Packed team format
# ---------------------------------------------------------------------------

def _species_for_packed(name: str) -> str:
    """Map a pikalytics Pokémon name to its Showdown species ID.

    Pikalytics tracks Mega Pokémon as ``{species}-Mega`` or
    ``{species}-Mega-X/Y``.  Showdown expects the *base species* name in
    the packed team; the Mega Stone item triggers the transformation in battle.

    Examples
    --------
    'Incineroar'      → 'Incineroar'      (unchanged)
    'Kangaskhan-Mega' → 'Kangaskhan'
    'Charizard-Mega-Y'→ 'Charizard'
    'Ninetales-Alola' → 'Ninetales-Alola' (unchanged — regional form)
    """
    if "-Mega" in name:
        return name.split("-Mega")[0]
    return name


def _ev_str_to_showdown(ev_str: str) -> str:
    """Convert '0/32/2/0/0/32' (HP/Atk/Def/SpA/SpD/Spe) to Showdown format.

    Showdown's packed team format uses the same HP/Atk/Def/SpA/SpD/Spe order.
    Only non-zero values need to be in the string; trailing zeros may be omitted.
    We keep the full 6-field numeric form so Showdown can always parse it.
    """
    return ev_str.strip()


def team_to_packed(specs: list[TeamSpec]) -> str:
    """Convert a list of TeamSpec objects to a Showdown packed team string.

    The packed format is documented at:
    https://github.com/smogon/pokemon-showdown/blob/master/sim/TEAMS.md

    Field order per set:
        NICKNAME|SPECIES|ITEM|ABILITY|MOVES|NATURE|EVS|GENDER|IVS|SHINY|LEVEL|...

    For Champions format:
    - Nickname = species name (SPECIES field left blank = nickname is the species)
    - IVs field = blank (all 31 in Champions)
    - Level = 50
    - EVs field = Champions SP spread in numeric ``hp/atk/def/spa/spd/spe`` form
    """
    sets = []
    for spec in specs:
        # Use the base species name (strip "-Mega" suffix for Megas).
        # In Champions format, the Mega Stone item triggers transformation in battle.
        # Field order: NAME|SPECIES|ITEM|ABILITY|MOVES|NATURE|EVS|GENDER|IVS|SHINY|LEVEL|
        species_id = _species_for_packed(spec.species)
        moves_str  = ",".join(m for m in spec.moves if m)
        ev_field   = _ev_str_to_showdown(spec.ev_str)
        packed = (
            f"{species_id}|"        # NAME (nickname = base species)
            f"|"                    # SPECIES (blank = same as nickname)
            f"{spec.item}|"         # ITEM
            f"{spec.ability}|"      # ABILITY
            f"{moves_str}|"         # MOVES
            f"{spec.nature}|"       # NATURE
            f"{ev_field}|"          # EVS (Champions SP in hp/atk/def/spa/spd/spe order)
            f"|"                    # GENDER
            f"|"                    # IVS (blank = all 31)
            f"|"                    # SHINY
            f"50|"                  # LEVEL
        )
        sets.append(packed)
    return "]".join(sets)


def generate_packed_team(
    data_dir: str | Path,
    format_id: str,
    *,
    rating: str = "1500",
    date: Optional[str] = None,
    size: int = 6,
    seed: Optional[int] = None,
) -> str:
    """One-stop function: load metagame data and generate a packed team.

    Parameters
    ----------
    data_dir:
        Directory containing saved Pikalytics JSON files.
    format_id:
        Showdown format ID.
    rating:
        Rating tier to load.
    date:
        Specific snapshot date (``"YYYY-MM"``), or ``None`` for the latest.
    size:
        Number of Pokémon to include (default 6).
    seed:
        Optional RNG seed for reproducibility.

    Returns
    -------
    Packed team string, ready for :class:`BattleRunner.run_battle`.
    """
    mg  = load_metagame(data_dir, format_id, rating=rating, date=date)
    rng = random.Random(seed)
    specs = generate_team(mg, size=size, rng=rng)
    return team_to_packed(specs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Scrape Pikalytics metagame data and/or generate random teams.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd")

    # --- scrape ---
    sc = sub.add_parser("scrape", help="Download metagame data from Pikalytics")
    sc.add_argument("--format", "-f", default="gen9championsvgc2026regma", metavar="FORMAT_ID")
    sc.add_argument("--out",    "-o", default="data/pikalytics",           metavar="DIR")
    sc.add_argument("--rating", "-r", default="1500")
    sc.add_argument("--date",   "-d", default=None, help="YYYY-MM (default: current month)")
    sc.add_argument("--min-usage", type=float, default=0.5)

    # --- generate ---
    gn = sub.add_parser("generate", help="Generate a random packed team from stored data")
    gn.add_argument("--format",  "-f", default="gen9championsvgc2026regma")
    gn.add_argument("--data",    "-d", default="data/pikalytics")
    gn.add_argument("--rating",  "-r", default="1500")
    gn.add_argument("--size",    "-n", type=int, default=6)
    gn.add_argument("--seed",    "-s", type=int, default=None)
    gn.add_argument("--count",   "-c", type=int, default=1, help="Number of teams to generate")
    gn.add_argument("--show-names", action="store_true",
                    help="Also print the team composition (not just the packed string)")

    args = parser.parse_args()

    if args.cmd == "scrape":
        scrape_metagame(
            format_id=args.format,
            output_dir=args.out,
            date=args.date,
            rating=args.rating,
            min_usage=args.min_usage,
            verbose=True,
        )

    elif args.cmd == "generate":
        mg  = load_metagame(args.data, args.format, rating=args.rating)
        rng = random.Random(args.seed)
        for i in range(args.count):
            specs = generate_team(mg, size=args.size, rng=rng)
            packed = team_to_packed(specs)
            print(f"=== Team {i + 1} ===")
            if args.show_names:
                for spec in specs:
                    print(f"  {spec.species}  {spec.item}  [{', '.join(spec.moves)}]")
            print(packed)
            print()

    else:
        parser.print_help()


if __name__ == "__main__":
    _main()
