# Headless Browser Scraper for Pokemon Showdown

This guide explains how to use the headless browser-based scraper to download Pokemon Showdown battle logs.

## Quick Start

### 1. Install Dependencies

First, install Playwright and ensure it has browser binaries:

```bash
# Install Playwright package
pip install playwright

# Install browser binaries (Chromium, Firefox, WebKit)
playwright install

# Or just Chromium (smaller download)
playwright install chromium
```

### 2. Download Battles

```bash
# Basic usage - download 100 battles
python download_with_headless.py --format gen9championsvgc2026regma --limit 100

# Download 500 battles
python download_with_headless.py --format gen9championsvgc2026regma --limit 500

# Download to custom directory
python download_with_headless.py --format gen9championsvgc2026regma --limit 100 --output ./my_battles

# Run browser in UI mode (for debugging)
python download_with_headless.py --format gen9championsvgc2026regma --limit 50 --no-headless

# Verbose logging
python download_with_headless.py --format gen9championsvgc2026regma --limit 100 -v
```

## How It Works

The headless scraper uses Playwright to:

1. **Load the replay directory** - Opens `https://replay.pokemonshowdown.com/?format={format_id}` in a headless Chromium browser
2. **Wait for JavaScript** - Waits for the page to fully render (DOM content loaded)
3. **Scroll dynamically** - Scrolls the page to trigger lazy loading of battle links
4. **Extract battle IDs** - Uses regex to extract battle IDs from the rendered HTML
5. **Download logs** - Uses the existing downloader with rate limiting to fetch individual battle JSON files

## Why Headless Browser?

Unlike the simple `urllib` scraper:

- ✅ **Handles JavaScript** - The replay directory uses JS to render links dynamically
- ✅ **Dynamic content** - Loads more battles as you scroll
- ✅ **More reliable** - No regex parsing of raw HTML, actual DOM content
- ✅ **Respects server** - Proper headless browser behavior avoids getting blocked
- ✅ **Faster** - Playwright is optimized for scraping

## Python API

You can also use the scraper programmatically:

### Async (Recommended)

```python
import asyncio
from showdown_ai.headless_scraper import scrape_battles_async

async def main():
    # Scrape battle IDs
    battle_ids = await scrape_battles_async(
        format_id="gen9championsvgc2026regma",
        max_battles=100,
        headless=True
    )
    
    print(f"Found {len(battle_ids)} battles")
    
    # Then download them
    from showdown_ai.downloader import download_battle_log
    from pathlib import Path
    
    output_dir = Path("downloaded_logs") / "gen9championsvgc2026regma"
    for battle_id in battle_ids:
        download_battle_log(battle_id, output_dir)

asyncio.run(main())
```

### Sync (Simple wrapper)

```python
from showdown_ai.headless_scraper import scrape_battles

# Scrape synchronously
battle_ids = scrape_battles(
    format_id="gen9championsvgc2026regma",
    max_battles=100,
    headless=True
)

print(f"Found {len(battle_ids)} battles")
```

## Configuration

You can customize scraper behavior by creating a `ScraperConfig`:

```python
from showdown_ai.headless_scraper import HeadlessScraper, ScraperConfig
import asyncio

config = ScraperConfig(
    headless=True,                    # Run in headless mode
    timeout_ms=30000,                 # Page load timeout (ms)
    wait_for_selector="a[href*='-']",  # Wait for battle links
    scroll_pause_time=1.0,            # Pause between scrolls (seconds)
    max_scroll_attempts=20            # Max scrolls before stopping
)

scraper = HeadlessScraper(config)

async def main():
    battles = await scraper.scrape_format_directory(
        format_id="gen9championsvgc2026regma",
        max_battles=100
    )

asyncio.run(main())
```

## Supported Formats

Use any format ID from Pokemon Showdown replays:

- `gen9championsvgc2026regma` - Gen 9 VGC 2026 Regional
- `gen9vgc2024regg` - Gen 9 VGC 2024 Regional
- Any other format available on https://replay.pokemonshowdown.com/?format=

## Output Organization

Downloads are automatically organized into format-specific subfolders:

```
downloaded_logs/
├── gen9championsvgc2026regma/
│   ├── gen9championsvgc2026regma-2594970133.json
│   ├── gen9championsvgc2026regma-2594970134.json
│   └── ... (100+ battle files)
└── gen9vgc2024regg/
    ├── gen9vgc2024regg-123456.json
    └── ... (other format battles)
```

This structure makes it easy to train models on single formats.

## Troubleshooting

### Playwright not installed
```
Error: ImportError: Playwright not installed
```
Solution: `pip install playwright && playwright install`

### Timeout waiting for elements
```
Error: Timeout waiting for selector
```
This might mean the page didn't load properly. Try:
- Increase `timeout_ms` in config
- Run with `--no-headless` to see what's happening
- Check your internet connection

### Browser takes too long to start
Playwright downloads ~500MB of browsers on first run. This is normal and only happens once.

### Getting blocked by Showdown
- Add delays between requests: increase `min_delay` and `max_delay` in downloader
- Reduce `max_battles` to download fewer at once
- Space out your scraping sessions

## Performance Tips

1. **First run takes longer** - Playwright installs browsers (~500MB)
2. **Use `max_battles`** - Scraping fewer battles is faster
3. **Parallel downloads** - Once you have battle IDs, downloads can be parallelized
4. **Reuse battle lists** - Save scraped IDs to avoid re-scraping

Example workflow:
```python
import json
from pathlib import Path
from showdown_ai.headless_scraper import scrape_battles

# Scrape once and save
battle_ids = scrape_battles("gen9championsvgc2026regma", max_battles=1000)
Path("battle_ids.json").write_text(json.dumps(battle_ids))

# Later, load and download without re-scraping
battle_ids = json.loads(Path("battle_ids.json").read_text())
```

## Debugging

To see what the browser is loading and debug selector issues:

```bash
python debug_page_inspector.py
```

This will:
1. Load the page in non-headless mode (you see the browser)
2. Show all links on the page
3. Test different CSS selectors
4. Save the page HTML to `debug_page_content.html`
5. Extract battle IDs using regex

## See Also

- [Pokemon Showdown Replays](https://replay.pokemonshowdown.com/)
- [Playwright Documentation](https://playwright.dev/python/)
