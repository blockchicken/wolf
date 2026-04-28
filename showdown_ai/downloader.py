"""Download Showdown battle logs with rate limiting to avoid IP blocks."""

import json
import time
import random
import logging
import gzip
import zlib
from pathlib import Path
from typing import Optional, List
import urllib.request
import urllib.error
from datetime import datetime



logger = logging.getLogger(__name__)


def get_browser_headers() -> dict:
    """Generate headers that mimic browser requests."""
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
    ]
    
    return {
        'User-Agent': random.choice(user_agents),
        'Referer': 'https://replay.pokemonshowdown.com/',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Accept-Encoding': 'gzip, deflate',  # Removed 'br' (Brotli) - not supported
        'DNT': '1',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }


def get_battle_list(
    format_id: str,
    limit: int = 50,
    offset: int = 0,
    retry_count: int = 3,
) -> List[str]:
    """
    Fetch list of battle IDs for a format from Showdown.
    
    Args:
        format_id: Format to search
        limit: Max results per request
        offset: Pagination offset
        retry_count: Number of retries on failure
        
    Returns:
        List of battle IDs
    """
    import re
    
    url = f"https://replay.pokemonshowdown.com/?format={format_id}&offset={offset}"
    
    for attempt in range(retry_count):
        try:
            req = urllib.request.Request(url, headers=get_browser_headers())
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read().decode('utf-8')
                pattern = rf'{format_id}-\d+'
                battles = list(set(re.findall(pattern, data)))
                logger.info(f"Fetched {len(battles)} battles for {format_id}")
                return battles[:limit]
                    
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_time = (2 ** attempt) * random.uniform(1, 2)
                logger.warning(f"Rate limited (429). Waiting {wait_time:.1f}s...")
                time.sleep(wait_time)
            elif e.code in (403, 404):
                logger.warning(f"Access denied (HTTP {e.code})")
                return []
            else:
                logger.error(f"HTTP Error {e.code}")
        except Exception as e:
            logger.error(f"Error fetching battles: {e}")
            if attempt < retry_count - 1:
                time.sleep(2 ** attempt)
    
    return []


def download_battle_log(
    battle_id: str,
    output_dir: Path = Path("downloaded_logs"),
    min_delay: float = 0.5,
    max_delay: float = 2.0,
    retry_count: int = 3,
) -> Optional[Path]:
    """
    Download a single battle log JSON from Showdown.
    
    Args:
        battle_id: Battle ID (e.g., 'gen9championsvgc2026regma-12345678')
        output_dir: Directory to save logs
        min_delay: Minimum delay between requests (seconds)
        max_delay: Maximum delay between requests (seconds)
        retry_count: Number of retries on failure
        
    Returns:
        Path to saved file if successful, None otherwise
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    output_file = output_dir / f"{battle_id}.json"
    
    if output_file.exists():
        logger.debug(f"Already have {battle_id}")
        return output_file
    
    delay = random.uniform(min_delay, max_delay)
    time.sleep(delay)
    
    url = f"https://replay.pokemonshowdown.com/{battle_id}.json"
    
    for attempt in range(retry_count):
        try:
            req = urllib.request.Request(url, headers=get_browser_headers())
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read()
                
                # Handle gzip compression if present
                if data[:2] == b'\x1f\x8b':  # gzip magic number
                    data = gzip.decompress(data)
                
                # Handle raw deflate compression
                if data[:2] == b'\x78\x9c' or data[:2] == b'\x78\xda':  # deflate headers
                    try:
                        data = zlib.decompress(data, -zlib.MAX_WBITS)
                    except:
                        pass  # Keep original if decompression fails
                
                output_file.write_bytes(data)
                logger.info(f"Downloaded {battle_id}")
                return output_file
                
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait_time = (2 ** attempt) * random.uniform(1, 2)
                logger.warning(f"Rate limited on {battle_id}. Waiting {wait_time:.1f}s...")
                time.sleep(wait_time)
            elif e.code == 404:
                logger.warning(f"Battle {battle_id} not found (404)")
                return None
            elif e.code == 403:
                logger.warning(f"Access denied for {battle_id} (403)")
                return None
            else:
                logger.error(f"HTTP {e.code} for {battle_id}")
                if attempt < retry_count - 1:
                    time.sleep(2 ** attempt)
                    
        except Exception as e:
            logger.error(f"Error downloading {battle_id}: {e}")
            if attempt < retry_count - 1:
                time.sleep(2 ** attempt)
    
    logger.error(f"Failed to download {battle_id}")
    return None


def download_format_logs(
    format_id: str,
    output_dir: Path = Path("downloaded_logs"),
    battle_ids: List[str] = None,
    max_battles: Optional[int] = None,
    min_delay: float = 0.5,
    max_delay: float = 2.0,
    api_delay: float = 2.0,
) -> dict:
    """
    Download battles for a format.
    
    Args:
        format_id: Format to download
        output_dir: Directory to save logs
        battle_ids: List of battle IDs to download
        max_battles: Max battles to download
        min_delay: Min delay between individual downloads
        max_delay: Max delay between individual downloads
        api_delay: Delay between API list requests
        
    Returns:
        Dict with stats: {'downloaded': int, 'failed': int, 'skipped': int}
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    if battle_ids is None:
        battle_ids = []
        page = 0
        while len(battle_ids) < (max_battles or 999999):
            new_ids = get_battle_list(format_id, limit=50, offset=page * 50)
            if not new_ids:
                break
            battle_ids.extend(new_ids)
            page += 1
            time.sleep(api_delay)
        
        if max_battles:
            battle_ids = battle_ids[:max_battles]
    
    stats = {'downloaded': 0, 'failed': 0, 'skipped': 0}
    
    for battle_id in battle_ids:
        result = download_battle_log(battle_id, output_dir, min_delay, max_delay)
        if result is None:
            stats['failed'] += 1
        else:
            stats['downloaded'] += 1
    
    return stats
