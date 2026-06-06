"""Download Pokemon Showdown battle replays via the public JSON API.

The replay search endpoint returns battles newest-first, 51 per page:
    GET https://replay.pokemonshowdown.com/search.json?format=<id>&page=<n>

Individual replays are fetched as:
    GET https://replay.pokemonshowdown.com/<battle-id>.json

Usage (as a script)::

    python -m showdown_ai.downloader \\
        --format gen9championsvgc2026regma \\
        --out downloaded_logs/gen9championsvgc2026regma \\
        --max-pages 50 \\
        --min-rating 1200

Call directly::

    from showdown_ai.downloader import download_format_logs
    stats = download_format_logs(
        format_id="gen9championsvgc2026regma",
        output_dir="downloaded_logs/gen9championsvgc2026regma",
        max_pages=50,
        min_rating=1200,
        verbose=True,
    )
"""

from __future__ import annotations

import json
import logging
import time
import random
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_BASE = "https://replay.pokemonshowdown.com"
_RESULTS_PER_PAGE = 51   # Showdown's fixed page size

# ---------------------------------------------------------------------------
# Rating bucket routing
# ---------------------------------------------------------------------------

# Threshold definitions: (minimum_rating, folder_name)
# Battles are placed in the highest bucket they qualify for.
# Anything below the lowest threshold (or unrated) goes into "all".
RATING_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (1500, "1500"),
    (1250, "1250"),
)
_BUCKET_ALL = "all"   # catches unrated games and those below every threshold


def rating_bucket(rating: Optional[int]) -> str:
    """Return the subfolder name for a battle with the given rating.

    >>> rating_bucket(1600)
    '1500'
    >>> rating_bucket(1350)
    '1250'
    >>> rating_bucket(1100)
    'all'
    >>> rating_bucket(None)
    'all'
    """
    if rating is not None:
        for threshold, name in RATING_THRESHOLDS:
            if rating >= threshold:
                return name
    return _BUCKET_ALL


# ---------------------------------------------------------------------------
# Metadata returned by the search endpoint (no log text included)
# ---------------------------------------------------------------------------

@dataclass
class BattleMeta:
    id: str
    format: str
    players: tuple[str, str]
    rating: Optional[int]   # None for unrated battles
    uploadtime: int          # unix timestamp
    private: bool


def _parse_meta(item: dict) -> BattleMeta:
    players = item.get("players", ["?", "?"])
    return BattleMeta(
        id=item["id"],
        format=item.get("format", ""),
        players=(players[0], players[1]),
        rating=item.get("rating"),
        uploadtime=item.get("uploadtime", 0),
        private=bool(item.get("private", 0)),
    )


# ---------------------------------------------------------------------------
# Low-level HTTP helpers
# ---------------------------------------------------------------------------

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
    "Gecko/20100101 Firefox/125.0",
]


def _headers() -> dict:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "application/json",
        "Referer": f"{_BASE}/",
    }


def _get_json(url: str, retries: int = 3) -> Optional[object]:
    """Fetch a URL and return parsed JSON, with exponential backoff on errors."""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_headers())
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                wait = 2 ** attempt * random.uniform(2, 4)
                logger.warning(f"Rate-limited (429) on {url}. Waiting {wait:.0f}s…")
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
                time.sleep(2 ** attempt)
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_battle_page(format_id: str, page: int) -> list[BattleMeta]:
    """Fetch one page of battle metadata from the search endpoint.

    Returns an empty list when the page is beyond the available history.
    """
    url = f"{_BASE}/search.json?format={format_id}&page={page}"
    data = _get_json(url)
    if not data or not isinstance(data, list):
        return []
    return [_parse_meta(item) for item in data]


def download_battle_log(
    battle_id: str,
    output_dir: Path,
    *,
    rating: Optional[int] = None,
    delay: float = 0.0,
) -> Optional[Path]:
    """Download a single battle's full JSON log into the appropriate rating bucket.

    The ``rating`` is used to select a subfolder (see :func:`rating_bucket`).
    If ``rating`` is not supplied the battle goes into the ``all/`` bucket.

    Returns the path to the saved file, or None on failure.
    Skips silently if the file already exists in *any* bucket subfolder.
    """
    output_dir = Path(output_dir)
    bucket = rating_bucket(rating)
    dest_dir = output_dir / bucket
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{battle_id}.json"

    # Check if already downloaded in any bucket (handles re-runs with changed thresholds)
    if dest.exists():
        logger.debug(f"Already downloaded: {battle_id}")
        return dest
    for _, folder in RATING_THRESHOLDS:
        alt = output_dir / folder / f"{battle_id}.json"
        if alt.exists():
            logger.debug(f"Already downloaded (in {folder}/): {battle_id}")
            return alt
    alt_all = output_dir / _BUCKET_ALL / f"{battle_id}.json"
    if alt_all.exists():
        logger.debug(f"Already downloaded (in all/): {battle_id}")
        return alt_all

    if delay:
        time.sleep(delay)

    url = f"{_BASE}/{battle_id}.json"
    data = _get_json(url)
    if data is None:
        return None

    dest.write_text(json.dumps(data), encoding="utf-8")
    logger.info(f"Saved {battle_id} → {bucket}/")
    return dest


def _already_exists(battle_id: str, output_dir: Path) -> bool:
    """Return True if the battle log is already on disk in any rating bucket."""
    for _, folder in RATING_THRESHOLDS:
        if (output_dir / folder / f"{battle_id}.json").exists():
            return True
    return (output_dir / _BUCKET_ALL / f"{battle_id}.json").exists()


def _collect_all_metadata(
    format_id: str,
    *,
    page_delay: float = 0.3,
    verbose: bool = False,
) -> list[BattleMeta]:
    """Phase 1: fetch metadata for every available battle across all pages.

    Uses a short delay since responses are small JSON (no full log content).
    Stops when the API returns an empty page.
    """
    all_meta: list[BattleMeta] = []
    page = 1
    while True:
        battles = get_battle_page(format_id, page)
        if not battles:
            break
        all_meta.extend(battles)
        if verbose:
            print(f"  Collecting: page {page} ({len(all_meta)} battles so far)…")
        page += 1
        time.sleep(page_delay)
    return all_meta


def download_format_logs(
    format_id: str,
    output_dir: str | Path,
    *,
    workers: int = 5,
    page_delay: float = 0.3,
    battle_delay: float = 0.3,
    verbose: bool = False,
) -> dict:
    """Download all available replays for a format.

    Uses a two-phase approach to avoid page-drift and maximise speed:

    **Phase 1 — collect** (sequential, fast):
        Paginate through the search API to gather every battle's metadata.
        Because no files are written during this phase, new battles added
        mid-run can only cause us to see *more* battles, never duplicates.

    **Phase 2 — download** (concurrent, fast):
        Filter out battles already on disk, then fetch the remaining logs
        in parallel using a thread pool.

    Parameters
    ----------
    format_id:
        Showdown format ID, e.g. ``"gen9championsvgc2026regma"``.
    output_dir:
        Root directory.  Files are written into ``<output_dir>/<bucket>/``
        subfolders based on rating (``all/``, ``1250/``, ``1500/``).
    workers:
        Number of concurrent download threads (default 5).
    page_delay:
        Seconds between search-page requests during phase 1 (default 0.3).
    battle_delay:
        Seconds each worker sleeps before making its download request
        (staggers requests within the pool, default 0.3).
    verbose:
        Print per-phase progress to stdout.

    Returns
    -------
    dict with keys ``downloaded``, ``skipped``, ``filtered``, ``failed``, ``pages``.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = {"downloaded": 0, "skipped": 0, "filtered": 0, "failed": 0, "pages": 0}

    # ------------------------------------------------------------------
    # Phase 1: collect all metadata
    # ------------------------------------------------------------------
    if verbose:
        print(f"Phase 1: collecting battle list for {format_id}…")

    all_meta = _collect_all_metadata(format_id, page_delay=page_delay, verbose=verbose)
    stats["pages"] = max(1, len(all_meta) // _RESULTS_PER_PAGE)

    if verbose:
        print(f"         {len(all_meta)} battles found across ~{stats['pages']} pages.\n")

    # Deduplicate by ID (page drift can cause the same battle to appear on
    # two consecutive pages if new battles are uploaded during collection)
    seen_ids: set[str] = set()
    unique_meta: list[BattleMeta] = []
    for meta in all_meta:
        if meta.id not in seen_ids:
            seen_ids.add(meta.id)
            unique_meta.append(meta)

    # ------------------------------------------------------------------
    # Partition: skip private / already-downloaded; queue the rest
    # ------------------------------------------------------------------
    to_download: list[BattleMeta] = []
    for meta in unique_meta:
        if meta.private:
            stats["filtered"] += 1
            continue
        if _already_exists(meta.id, output_dir):
            stats["skipped"] += 1
        else:
            to_download.append(meta)

    if verbose:
        print(
            f"Phase 2: downloading {len(to_download)} new battles "
            f"({stats['skipped']} already on disk, "
            f"{stats['filtered']} private) "
            f"using {workers} workers…"
        )

    if not to_download:
        if verbose:
            print("Nothing to download.")
        return stats

    # ------------------------------------------------------------------
    # Phase 2: concurrent downloads
    # ------------------------------------------------------------------
    completed = 0

    def _download(meta: BattleMeta) -> Optional[Path]:
        # Small random stagger so workers don't all hit the server at once
        time.sleep(random.uniform(0, battle_delay))
        return download_battle_log(meta.id, output_dir, rating=meta.rating)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_download, m): m for m in to_download}
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if result:
                stats["downloaded"] += 1
            else:
                stats["failed"] += 1
            if verbose and completed % 50 == 0:
                print(
                    f"  {completed}/{len(to_download)} complete "
                    f"({stats['downloaded']} saved, {stats['failed']} failed)"
                )

    return stats


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _main() -> None:
    import argparse

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    parser = argparse.ArgumentParser(
        description="Download Pokemon Showdown replays for a format.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--format", "-f",
        default="gen9championsvgc2026regma",
        metavar="FORMAT_ID",
        help="Showdown format ID",
    )
    parser.add_argument(
        "--out", "-o",
        default=None,
        metavar="DIR",
        help="Output directory (default: downloaded_logs/<format_id>)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        metavar="N",
        help="Concurrent download threads for phase 2",
    )
    parser.add_argument(
        "--page-delay",
        type=float,
        default=0.3,
        metavar="SECS",
        help="Delay between metadata page requests (phase 1)",
    )
    parser.add_argument(
        "--battle-delay",
        type=float,
        default=0.3,
        metavar="SECS",
        help="Per-worker stagger delay before each download (phase 2)",
    )
    args = parser.parse_args()

    format_id = args.format
    out_dir = args.out or f"downloaded_logs/{format_id}"

    print(f"Format:  {format_id}")
    print(f"Output:  {out_dir}")
    print(f"Workers: {args.workers}")
    print()

    stats = download_format_logs(
        format_id=format_id,
        output_dir=out_dir,
        workers=args.workers,
        page_delay=args.page_delay,
        battle_delay=args.battle_delay,
        verbose=True,
    )

    print(f"\nDone.")
    print(f"  Pages fetched:  {stats['pages']}")
    print(f"  Downloaded:     {stats['downloaded']}")
    print(f"  Already had:    {stats['skipped']}")
    print(f"  Filtered out:   {stats['filtered']}")
    print(f"  Failed:         {stats['failed']}")


if __name__ == "__main__":
    _main()
