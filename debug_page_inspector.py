#!/usr/bin/env python3
"""
Debug script to inspect actual page structure and find correct selectors for battle links.
"""

import asyncio
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)


async def main():
    """Debug page structure."""
    try:
        from playwright.async_api import async_playwright
        
        format_id = "gen9championsvgc2026regma"
        url = f"https://replay.pokemonshowdown.com/?format={format_id}"
        
        logger.info("=" * 70)
        logger.info("DEBUGGING PAGE STRUCTURE")
        logger.info("=" * 70)
        logger.info(f"URL: {url}\n")
        
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=False)
            page = await browser.new_page()
            
            logger.info("Loading page...")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            logger.info("Page loaded! Let me wait a bit more for JS rendering...")
            await page.wait_for_timeout(3000)
            
            logger.info("\nNow let me inspect the page structure...\n")
            
            # Get all links on page
            logger.info("=" * 70)
            logger.info("ALL LINKS ON PAGE")
            logger.info("=" * 70)
            
            links = await page.query_selector_all("a")
            logger.info(f"Total <a> tags found: {len(links)}\n")
            
            # Show first 20 links
            for i, link in enumerate(links[:20]):
                href = await link.get_attribute("href")
                text = await link.text_content()
                logger.info(f"[{i+1}] href={href}")
                logger.info(f"    text={text}\n")
            
            # Look for replay links specifically
            logger.info("=" * 70)
            logger.info("SEARCHING FOR REPLAY LINKS")
            logger.info("=" * 70)
            
            replay_links = await page.query_selector_all("a[href*='replay.pokemonshowdown.com']")
            logger.info(f"Links with 'replay.pokemonshowdown.com' in href: {len(replay_links)}\n")
            
            if replay_links:
                logger.info("First 10 replay links:")
                for i, link in enumerate(replay_links[:10]):
                    href = await link.get_attribute("href")
                    logger.info(f"  {i+1}. {href}")
            
            # Try different selectors
            logger.info("\n" + "=" * 70)
            logger.info("TESTING DIFFERENT SELECTORS")
            logger.info("=" * 70)
            
            selectors_to_test = [
                "a[href*='replay.pokemonshowdown.com']",
                "a[href*='gen9']",
                "a[href^='/']",
                ".battle-link",
                "[href*='2594']",  # Looking for battle IDs
            ]
            
            for selector in selectors_to_test:
                count = await page.query_selector_all(selector)
                logger.info(f"{selector:40} -> {len(count)} matches")
            
            # Get page content and save for inspection
            logger.info("\n" + "=" * 70)
            logger.info("SAVING PAGE CONTENT FOR INSPECTION")
            logger.info("=" * 70)
            
            content = await page.content()
            
            # Save full HTML
            html_file = Path("debug_page_content.html")
            html_file.write_text(content)
            logger.info(f"✓ Full page HTML saved to: {html_file}")
            
            # Look for battle ID patterns in content
            import re
            pattern = rf'{format_id}-\d+'
            found_ids = re.findall(pattern, content)
            logger.info(f"\n✓ Found {len(set(found_ids))} unique battle IDs using regex pattern")
            
            if found_ids:
                logger.info(f"\nFirst 5 battle IDs found:")
                for bid in list(set(found_ids))[:5]:
                    logger.info(f"  - {bid}")
            
            # Check page source for clues
            logger.info("\n" + "=" * 70)
            logger.info("LOOKING FOR SPECIFIC PATTERNS")
            logger.info("=" * 70)
            
            if 'battleLink' in content:
                logger.info("✓ Found 'battleLink' in page")
            if 'href=' in content:
                logger.info("✓ Found 'href=' in page")
            if format_id in content:
                logger.info(f"✓ Found '{format_id}' in page")
            
            logger.info("\n" + "=" * 70)
            logger.info("DEBUG SUMMARY")
            logger.info("=" * 70)
            logger.info(f"Total links on page: {len(links)}")
            logger.info(f"Replay links found: {len(replay_links)}")
            logger.info(f"Battle IDs via regex: {len(set(found_ids))}")
            logger.info(f"\nPage content saved to: debug_page_content.html")
            logger.info("Keep the browser open to inspect manually (F12 for DevTools)")
            logger.info("Press Enter in terminal when done...")
            
            input()
            
            await browser.close()
        
    except Exception as e:
        logger.error(f"Error: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())
