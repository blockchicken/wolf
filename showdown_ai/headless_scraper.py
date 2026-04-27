"""Headless browser scraper for Pokemon Showdown replay directory using Playwright."""

import logging
import re
import time
from pathlib import Path
from typing import List, Optional, Set
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScraperConfig:
    """Configuration for headless scraper."""
    headless: bool = True
    timeout_ms: int = 30000
    wait_for_selector: str = "a[href*='-']"
    scroll_pause_time: float = 1.0
    max_scroll_attempts: int = 20


class HeadlessScraper:
    """Scrape Pokemon Showdown replay directory using headless browser."""
    
    def __init__(self, config: Optional[ScraperConfig] = None):
        """Initialize scraper with optional config."""
        self.config = config or ScraperConfig()
        self.browser = None
        self.context = None
    
    async def _ensure_playwright(self):
        """Import and check Playwright availability."""
        try:
            from playwright.async_api import async_playwright
            return async_playwright()
        except ImportError:
            raise ImportError(
                "Playwright not installed. Install with:\n"
                "  pip install playwright\n"
                "  playwright install"
            )
    
    async def scrape_format_directory(
        self,
        format_id: str,
        max_battles: Optional[int] = None,
    ) -> List[str]:
        """
        Scrape battle IDs from format directory using headless browser.
        
        Args:
            format_id: Format to scrape (e.g., 'gen9championsvgc2026regma')
            max_battles: Max battles to collect (None = no limit)
            
        Returns:
            List of unique battle IDs
        """
        pw_context = await self._ensure_playwright()
        async with pw_context as playwright:
            browser = await playwright.chromium.launch(headless=self.config.headless)
            page = await browser.new_page()
            
            try:
                battle_ids: Set[str] = set()
                url = f"https://replay.pokemonshowdown.com/?format={format_id}"
                
                logger.info(f"Loading {url}...")
                await page.goto(url, wait_until="domcontentloaded", timeout=self.config.timeout_ms)
                
                # Wait for battle links to appear - they use relative hrefs like "format-id-numbers"
                logger.info(f"Waiting for battle links...")
                await page.wait_for_selector("a[href*='-']", timeout=self.config.timeout_ms)
                
                # Scroll to load all battles dynamically
                battle_ids = await self._scroll_and_collect(page, format_id, max_battles)
                
                logger.info(f"✓ Scraped {len(battle_ids)} unique battles from {format_id}")
                return sorted(list(battle_ids))
                
            finally:
                await browser.close()
    
    async def _scroll_and_collect(
        self,
        page,
        format_id: str,
        max_battles: Optional[int] = None,
    ) -> Set[str]:
        """Scroll page to load all battles and collect IDs."""
        battle_ids: Set[str] = set()
        last_count = 0
        scroll_attempts = 0
        
        # Pattern to match battle IDs
        pattern = rf'{format_id}-\d+'
        
        while scroll_attempts < self.config.max_scroll_attempts:
            # Extract all battle IDs from current page content
            content = await page.content()
            found_ids = set(re.findall(pattern, content))
            
            newly_found = found_ids - battle_ids
            if newly_found:
                battle_ids.update(newly_found)
                logger.info(f"  Found {len(battle_ids)} battles total (added {len(newly_found)})")
            
            # Check if we've reached the limit
            if max_battles and len(battle_ids) >= max_battles:
                logger.info(f"Reached max_battles limit ({max_battles})")
                break
            
            # Check if we found new battles on this scroll
            if len(battle_ids) == last_count:
                logger.info("No new battles found on this scroll - likely reached end")
                scroll_attempts += 1
            else:
                scroll_attempts = 0  # Reset counter if we found new content
            
            last_count = len(battle_ids)
            
            # Scroll down to load more
            logger.debug(f"Scrolling... (attempt {scroll_attempts + 1}/{self.config.max_scroll_attempts})")
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(int(self.config.scroll_pause_time * 1000))
        
        return battle_ids


async def scrape_battles_async(
    format_id: str,
    max_battles: Optional[int] = None,
    headless: bool = True,
) -> List[str]:
    """
    Scrape battle IDs from Showdown replay directory.
    
    Async wrapper for scraping with headless browser.
    
    Args:
        format_id: Format to scrape
        max_battles: Max battles to collect
        headless: Whether to run browser in headless mode
        
    Returns:
        List of battle IDs
    """
    config = ScraperConfig(headless=headless)
    scraper = HeadlessScraper(config)
    return await scraper.scrape_format_directory(format_id, max_battles)


def scrape_battles(
    format_id: str,
    max_battles: Optional[int] = None,
    headless: bool = True,
) -> List[str]:
    """
    Scrape battle IDs from Showdown replay directory (sync wrapper).
    
    Args:
        format_id: Format to scrape (e.g., 'gen9championsvgc2026regma')
        max_battles: Max battles to collect (None = unlimited)
        headless: Whether to run browser in headless mode
        
    Returns:
        List of battle IDs
        
    Example:
        >>> battle_ids = scrape_battles("gen9championsvgc2026regma", max_battles=100)
        >>> print(f"Found {len(battle_ids)} battles")
    """
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    return loop.run_until_complete(
        scrape_battles_async(format_id, max_battles, headless)
    )
