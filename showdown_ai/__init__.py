"""Lightweight framework for Pokemon Showdown doubles experimentation."""

from .agents import Agent, RandomLegalAgent
from .downloader import (
    BattleMeta,
    RATING_THRESHOLDS,
    download_battle_log,
    download_format_logs,
    get_battle_page,
    rating_bucket,
)
from .engine import BattleResult, ShowdownBattleRunner
from .filters import validate_log, filter_logs
from .logs import BattleLog, ParsedEvent, load_showdown_log_json, split_perspective_logs
from .state import PerspectiveState, StateTracker
from .headless_scraper import scrape_battles, scrape_battles_async, HeadlessScraper, ScraperConfig
from .training_data import (
    SlotAction,
    TurnExample,
    extract_examples,
    extract_examples_from_dir,
)
from .battle_runner import (
    BattleRunner,
    DecisionHandler,
    RandomDecisionHandler,
    BattleState,
)

__all__ = [
    "Agent",
    "BattleMeta",
    "filter_logs",
    "get_battle_page",
    "RATING_THRESHOLDS",
    "rating_bucket",
    "validate_log",
    "BattleResult",
    "BattleLog",
    "BattleRunner",
    "BattleState",
    "DecisionHandler",
    "HeadlessScraper",
    "ParsedEvent",
    "PerspectiveState",
    "RandomDecisionHandler",
    "RandomLegalAgent",
    "ScraperConfig",
    "ShowdownBattleRunner",
    "SlotAction",
    "StateTracker",
    "TurnExample",
    "download_battle_log",
    "download_format_logs",
    "extract_examples",
    "extract_examples_from_dir",
    "extract_examples_from_dir",
    "get_battle_list",
    "load_showdown_log_json",
    "scrape_battles",
    "scrape_battles_async",
    "split_perspective_logs",
]
