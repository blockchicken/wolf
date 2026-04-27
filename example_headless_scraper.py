#!/usr/bin/env python3
"""
Simple example of using the headless scraper.

This script demonstrates how to:
1. Scrape battle IDs using a headless browser
2. Download the battle logs
3. Show some statistics
"""

import asyncio
import json
import logging
from pathlib import Path

# Set up logging to see what's happening
logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


async def main():
    """Main example."""
    logger.info("=" * 70)
    logger.info("Pokemon Showdown Headless Scraper Example")
    logger.info("=" * 70)
    
    try:
        # Import the scraper
        from showdown_ai.headless_scraper import scrape_battles_async
        from showdown_ai.downloader import download_battle_log
        
        # Configuration
        format_id = "gen9championsvgc2026regma"
        max_battles = 10  # Start with just 10 for this example
        # Organize into format-specific subfolder
        output_dir = Path("downloaded_logs") / format_id
        
        # Step 1: Scrape battle IDs
        logger.info(f"\n1. Scraping {max_battles} battle IDs from {format_id}...")
        logger.info("   (This loads the replay directory in a headless browser)")
        
        battle_ids = await scrape_battles_async(
            format_id=format_id,
            max_battles=max_battles,
            headless=True  # Set to False to see the browser UI
        )
        
        if not battle_ids:
            logger.error("No battles found!")
            return 1
        
        logger.info(f"   ✓ Found {len(battle_ids)} battles")
        logger.info(f"   First 3: {battle_ids[:3]}")
        
        # Step 2: Save the IDs for reference
        ids_file = Path(f"battle_ids_{format_id}.json")
        ids_file.write_text(json.dumps(battle_ids, indent=2))
        logger.info(f"\n   Saved IDs to: {ids_file}")
        
        # Step 3: Download the battle logs
        logger.info(f"\n2. Downloading {len(battle_ids)} battle logs...")
        logger.info(f"   Output directory: {output_dir}")
        
        output_dir.mkdir(parents=True, exist_ok=True)
        downloaded = 0
        failed = 0
        
        for i, bid in enumerate(battle_ids, 1):
            result = download_battle_log(
                bid,
                output_dir=output_dir,
                min_delay=0.5,
                max_delay=2.0,
            )
            if result:
                downloaded += 1
                logger.info(f"   [{i}/{len(battle_ids)}] ✓ {bid}")
            else:
                failed += 1
                logger.info(f"   [{i}/{len(battle_ids)}] ✗ {bid} (failed)")
        
        # Step 4: Show summary
        logger.info(f"\n3. Summary")
        logger.info(f"   Downloaded: {downloaded}")
        logger.info(f"   Failed: {failed}")
        logger.info(f"   Files saved to: {output_dir.resolve()}")
        
        # Step 5: Show one battle file
        json_files = list(output_dir.glob("*.json"))
        if json_files:
            logger.info(f"\n4. Example: First battle file")
            battle_data = json.loads(json_files[0].read_text())
            logger.info(f"   File: {json_files[0].name}")
            logger.info(f"   Players: {battle_data.get('players', ['Unknown', 'Unknown'])}")
            logger.info(f"   Winner: {battle_data.get('winner')}")
        
        logger.info("\n" + "=" * 70)
        logger.info("✓ Example completed successfully!")
        logger.info("=" * 70)
        
        return 0
        
    except ImportError as e:
        logger.error(f"\n✗ Error: {e}")
        logger.error("\nTo fix, install Playwright:")
        logger.error("  pip install playwright")
        logger.error("  playwright install")
        return 1
    except Exception as e:
        logger.error(f"\n✗ Error: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
