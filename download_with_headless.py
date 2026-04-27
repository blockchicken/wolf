#!/usr/bin/env python3
"""
Download Pokemon Showdown battles using headless browser scraper.

Usage:
    python download_with_headless.py --format gen9championsvgc2026regma --limit 100
    python download_with_headless.py --format gen9championsvgc2026regma --limit 500 --no-headless

This script:
1. Scrapes the replay directory using Playwright headless browser
2. Extracts battle IDs from JavaScript-rendered content
3. Downloads battle logs with smart rate limiting
4. Organizes downloads into format-specific subfolders
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from datetime import datetime

from showdown_ai.headless_scraper import scrape_battles_async
from showdown_ai.downloader import download_battle_log


def setup_logging(verbose: bool = False):
    """Setup console logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Download Pokemon Showdown battles using headless browser",
        epilog="Example: python download_with_headless.py --format gen9championsvgc2026regma --limit 100"
    )
    parser.add_argument(
        "--format",
        default="gen9championsvgc2026regma",
        help="Format ID to download (default: gen9championsvgc2026regma)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max battles to download (default: 100)"
    )
    parser.add_argument(
        "--output",
        default="./downloaded_logs",
        help="Output base directory (default: ./downloaded_logs). Format subfolders will be created automatically."
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in non-headless mode (shows UI)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose logging output"
    )
    
    args = parser.parse_args()
    setup_logging(args.verbose)
    
    logger = logging.getLogger(__name__)
    logger.info("=" * 70)
    logger.info("Pokemon Showdown Battle Downloader (Headless Browser)")
    logger.info("=" * 70)
    
    format_id = args.format
    limit = args.limit
    # Create format-specific subfolder for organization
    output_dir = Path(args.output) / format_id
    headless = not args.no_headless
    
    logger.info(f"Format: {format_id}")
    logger.info(f"Max battles: {limit}")
    logger.info(f"Output dir: {output_dir}")
    logger.info(f"Headless mode: {headless}")
    
    try:
        # Step 1: Scrape battle IDs
        logger.info("\n" + "=" * 70)
        logger.info("STEP 1: Scraping battle IDs from replay directory...")
        logger.info("=" * 70)
        
        battle_ids = await scrape_battles_async(
            format_id=format_id,
            max_battles=limit,
            headless=headless
        )
        
        if not battle_ids:
            logger.error("✗ No battles found!")
            return 1
        
        logger.info(f"\n✓ Found {len(battle_ids)} battles to download")
        
        # Step 2: Download battle logs
        logger.info("\n" + "=" * 70)
        logger.info("STEP 2: Downloading battle logs...")
        logger.info("=" * 70)
        
        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        failed = 0
        skipped = 0
        
        for i, battle_id in enumerate(battle_ids, 1):
            logger.info(f"[{i}/{len(battle_ids)}] Downloading {battle_id}...")
            
            result = download_battle_log(
                battle_id,
                output_dir=output_dir,
                min_delay=0.5,
                max_delay=2.0,
                retry_count=3
            )
            
            if result is None:
                failed += 1
            elif str(result).endswith(".json") and (output_dir / f"{battle_id}.json").exists():
                if i > 1:  # First one might be new
                    file_stat = (output_dir / f"{battle_id}.json").stat()
                    if file_stat.st_ctime < (datetime.now().timestamp() - 5):
                        skipped += 1
                    else:
                        downloaded += 1
                else:
                    downloaded += 1
            else:
                downloaded += 1
        
        # Summary
        logger.info("\n" + "=" * 70)
        logger.info("DOWNLOAD SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Downloaded: {downloaded}")
        logger.info(f"Skipped (already existed): {skipped}")
        logger.info(f"Failed: {failed}")
        logger.info(f"Total: {downloaded + skipped + failed}")
        logger.info(f"Output directory: {output_dir.resolve()}")
        
        return 0 if failed == 0 else 1
        
    except ImportError as e:
        logger.error(f"✗ {e}")
        logger.error("\nTo install Playwright, run:")
        logger.error("  pip install playwright")
        logger.error("  playwright install")
        return 1
    except KeyboardInterrupt:
        logger.info("\n✗ Interrupted by user")
        return 130
    except Exception as e:
        logger.error(f"✗ Error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
