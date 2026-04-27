"""Lightweight framework for Pokemon Showdown doubles experimentation."""

from .agents import Agent, RandomLegalAgent
from .downloader import download_battle_log, download_format_logs, get_battle_list
from .engine import BattleResult, ShowdownBattleRunner
from .logs import BattleLog, ParsedEvent, load_showdown_log_json, split_perspective_logs
from .state import PerspectiveState, StateTracker
from .headless_scraper import scrape_battles, scrape_battles_async, HeadlessScraper, ScraperConfig

__all__ = [
    "Agent",
    "BattleResult",
    "BattleLog",
    "HeadlessScraper",
    "ParsedEvent",
    "PerspectiveState",
    "RandomLegalAgent",
    "ScraperConfig",
    "ShowdownBattleRunner",
    "StateTracker",
    "download_battle_log",
    "download_format_logs",
    "get_battle_list",
    "load_showdown_log_json",
    "scrape_battles",
    "scrape_battles_async",
    "split_perspective_logs",
]
