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
from .features import StateFeatures, encode_state, build_vocab_from_examples
from .pikalytics import (
    Metagame,
    PokemonStat,
    TeamSpec,
    scrape_metagame,
    load_metagame,
    generate_team,
    team_to_packed,
    generate_packed_team,
)
from .filters import validate_log, filter_logs
from .logs import BattleLog, ParsedEvent, load_showdown_log_json, split_perspective_logs
from .model import BattlePolicy
from .model_handler import ModelDecisionHandler
from .state import PerspectiveState, StateTracker
from .headless_scraper import scrape_battles, scrape_battles_async, HeadlessScraper, ScraperConfig
from .training_data import (
    SlotAction,
    TurnExample,
    extract_examples,
    extract_examples_from_dir,
)
from .vocab import BattleVocab, Vocab
from .battle_runner import (
    BattleRunner,
    DecisionHandler,
    RandomDecisionHandler,
    BattleState,
)

__all__ = [
    "Agent",
    "BattleMeta",
    "BattlePolicy",
    "BattleVocab",
    "filter_logs",
    "get_battle_page",
    "RATING_THRESHOLDS",
    "rating_bucket",
    "validate_log",
    "BattleResult",
    "BattleLog",
    "BattleRunner",
    "BattleState",
    "build_vocab_from_examples",
    "DecisionHandler",
    "encode_state",
    "HeadlessScraper",
    "ModelDecisionHandler",
    "ParsedEvent",
    "PerspectiveState",
    "RandomDecisionHandler",
    "RandomLegalAgent",
    "ScraperConfig",
    "ShowdownBattleRunner",
    "SlotAction",
    "StateFeatures",
    "StateTracker",
    "TurnExample",
    "Vocab",
    "download_battle_log",
    "download_format_logs",
    "extract_examples",
    "extract_examples_from_dir",
    "Metagame",
    "PokemonStat",
    "TeamSpec",
    "generate_packed_team",
    "generate_team",
    "load_metagame",
    "scrape_metagame",
    "team_to_packed",
    "load_showdown_log_json",
    "scrape_battles",
    "scrape_battles_async",
    "split_perspective_logs",
]
