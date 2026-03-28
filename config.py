"""
Configuration manager for the Investment Research Pipeline.
Non-secret settings load from pipeline_config.yaml (committed); secrets from environment.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml
from dotenv import load_dotenv

_CONFIG_PATH = Path(__file__).resolve().parent / "pipeline_config.yaml"
_ENV_PATH = Path(__file__).resolve().parent / ".env"

load_dotenv(dotenv_path=_ENV_PATH, override=False)

# Nasdaq feeds are often slow or bot-sensitive; add them back under live_news.rss_feeds if needed.
DEFAULT_LIVE_RSS_FEEDS = [
    "https://www.marketwatch.com/rss/topstories",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://finance.yahoo.com/news/rssindex",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml",
]


def _normalize_openrouter_model(model: str) -> str:
    raw = (model or "").strip()
    if not raw:
        return "openrouter/free"
    if "/" in raw:
        return raw

    legacy_map = {
        "claude-3-5-haiku": "anthropic/claude-3.5-haiku",
        "claude-3-5-sonnet": "anthropic/claude-3.5-sonnet",
        "claude-3-7-sonnet": "anthropic/claude-3.7-sonnet",
        "claude-3-opus": "anthropic/claude-3-opus",
        "claude-3-sonnet": "anthropic/claude-3-sonnet",
        "claude-3-haiku": "anthropic/claude-3-haiku",
    }
    for prefix, replacement in legacy_map.items():
        if raw == prefix or raw.startswith(prefix + "-"):
            return replacement
    return raw


def _load_yaml_defaults() -> Dict[str, Any]:
    if not _CONFIG_PATH.is_file():
        return {}
    with open(_CONFIG_PATH, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data if isinstance(data, dict) else {}


def _parse_bool(value: Optional[str], default: bool) -> bool:
    if value is None:
        return default
    return value.lower() == "true"


@dataclass
class Config:
    """Central configuration for all pipeline components"""

    ANALYSIS_MODE: str = "market_pulse"
    LIVE_NEWS_ENABLED: bool = True
    LIVE_RSS_FEEDS: List[str] = field(default_factory=list)
    RSS_MAX_ENTRIES_PER_FEED: int = 30
    MERGE_UNFILTERED_RSS_IN_MARKET_PULSE: bool = True
    FINNHUB_FOR_MATCHED_SYMBOLS: bool = True
    MAX_FINNHUB_SYMBOLS: int = 40
    # market_pulse ingestion
    MP_MARKET_NEWS_CATEGORY: str = "general"
    MP_MACRO_NEWS_MAX_ITEMS: int = 18
    # Benchmark indices only (optional price snapshot for LLM). Not a stock pick list.
    MP_BENCHMARK_SYMBOLS: List[str] = field(
        default_factory=lambda: ["SPY", "DIA", "QQQ"]
    )
    MP_MOVERS_ENABLED: bool = True
    MP_MOVERS_GAINERS_LIMIT: int = 18
    MP_MOVERS_LOSERS_LIMIT: int = 18
    MP_MOVERS_MOST_ACTIVES_LIMIT: int = 15
    MP_MAX_SYMBOLS_FOR_MOVER_NEWS: int = 32
    MP_EXTRA_NEWS_TICKERS: List[str] = field(default_factory=list)
    MP_FINNHUB_NEWS_MAX_PER_TICKER: int = 8
    MP_MAX_HEADLINES_FOR_LLM: int = 72
    YAHOO_PER_TICKER_ENABLED: bool = True
    YAHOO_MAX_PER_TICKER: int = 8
    LLM_MARKET_PULSE_MAX_TOKENS: int = 5000
    OPENROUTER_API_URL: str = "https://openrouter.ai/api/v1/chat/completions"
    OPENROUTER_API_KEY: str = ""
    FINNHUB_API_KEY: str = ""
    DISCORD_WEBHOOK_URL: str = ""
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    NEWS_API_KEY: str = ""
    LLM_MODEL: str = "openrouter/free"
    LLM_MAX_TOKENS: int = 1000
    LLM_REQUEST_TIMEOUT: int = 180
    PROXY_LIST: List[str] = field(default_factory=list)
    REQUEST_TIMEOUT: int = 10
    RSS_REQUEST_TIMEOUT: int = 35
    FINNHUB_REQUEST_PAUSE_SEC: float = 0.05
    RETRIES: int = 3
    SCHEDULE_TIME: str = "10:00"
    SCHEDULE_TIMEZONE: str = "US/Eastern"
    LOG_LEVEL: str = "INFO"
    ENABLE_DISCORD: bool = True
    ENABLE_TELEGRAM: bool = False

    def __post_init__(self):
        y = _load_yaml_defaults()

        mp = y.get("market_pulse") or {}
        if isinstance(mp, dict):
            if "market_news_category" in mp:
                self.MP_MARKET_NEWS_CATEGORY = str(
                    mp.get("market_news_category", self.MP_MARKET_NEWS_CATEGORY)
                ).strip().lower()
            if "benchmark_symbols" in mp and isinstance(mp["benchmark_symbols"], list):
                self.MP_BENCHMARK_SYMBOLS = [
                    str(s).strip() for s in mp["benchmark_symbols"] if str(s).strip()
                ]
            movers = mp.get("movers") or {}
            if isinstance(movers, dict):
                self.MP_MOVERS_ENABLED = bool(movers.get("enabled", self.MP_MOVERS_ENABLED))
                self.MP_MOVERS_GAINERS_LIMIT = int(
                    movers.get("gainers", self.MP_MOVERS_GAINERS_LIMIT)
                )
                self.MP_MOVERS_LOSERS_LIMIT = int(
                    movers.get("losers", self.MP_MOVERS_LOSERS_LIMIT)
                )
                self.MP_MOVERS_MOST_ACTIVES_LIMIT = int(
                    movers.get("most_actives", self.MP_MOVERS_MOST_ACTIVES_LIMIT)
                )
                self.MP_MAX_SYMBOLS_FOR_MOVER_NEWS = int(
                    movers.get("max_symbols_for_news", self.MP_MAX_SYMBOLS_FOR_MOVER_NEWS)
                )
            if "extra_news_tickers" in mp:
                extra = mp["extra_news_tickers"]
                if isinstance(extra, list):
                    self.MP_EXTRA_NEWS_TICKERS = [
                        str(s).strip().upper() for s in extra if str(s).strip()
                    ]
            self.MP_MACRO_NEWS_MAX_ITEMS = int(
                mp.get(
                    "macro_news_max_items",
                    mp.get("yahoo_rss_max_per_index", self.MP_MACRO_NEWS_MAX_ITEMS),
                )
            )
            self.MP_FINNHUB_NEWS_MAX_PER_TICKER = int(
                mp.get(
                    "finnhub_news_max_per_ticker",
                    mp.get("yahoo_news_max_per_ticker", self.MP_FINNHUB_NEWS_MAX_PER_TICKER),
                )
            )
            self.MP_MAX_HEADLINES_FOR_LLM = int(
                mp.get("max_headlines_for_llm", self.MP_MAX_HEADLINES_FOR_LLM)
            )
            self.MERGE_UNFILTERED_RSS_IN_MARKET_PULSE = bool(
                mp.get("merge_unfiltered_rss", self.MERGE_UNFILTERED_RSS_IN_MARKET_PULSE)
            )
            self.FINNHUB_REQUEST_PAUSE_SEC = float(
                mp.get(
                    "finnhub_request_pause_sec",
                    mp.get("yahoo_request_pause_sec", self.FINNHUB_REQUEST_PAUSE_SEC),
                )
            )
            self.YAHOO_PER_TICKER_ENABLED = bool(
                mp.get("yahoo_per_ticker_enabled", self.YAHOO_PER_TICKER_ENABLED)
            )
            self.YAHOO_MAX_PER_TICKER = int(
                mp.get("yahoo_max_per_ticker", self.YAHOO_MAX_PER_TICKER)
            )

        self.LLM_MARKET_PULSE_MAX_TOKENS = int(
            y.get("llm_market_pulse_max_tokens", self.LLM_MARKET_PULSE_MAX_TOKENS)
        )
        self.OPENROUTER_API_URL = str(
            y.get("openrouter_api_url", self.OPENROUTER_API_URL)
        ).strip()

        self.RSS_REQUEST_TIMEOUT = int(
            y.get("rss_request_timeout", self.RSS_REQUEST_TIMEOUT)
        )

        live = y.get("live_news") or {}
        if isinstance(live, dict):
            self.LIVE_NEWS_ENABLED = bool(live.get("enabled", self.LIVE_NEWS_ENABLED))
            feeds = live.get("rss_feeds")
            if isinstance(feeds, list) and feeds:
                self.LIVE_RSS_FEEDS = [str(u).strip() for u in feeds if str(u).strip()]
            elif not self.LIVE_RSS_FEEDS:
                self.LIVE_RSS_FEEDS = list(DEFAULT_LIVE_RSS_FEEDS)
            self.RSS_MAX_ENTRIES_PER_FEED = int(
                live.get("rss_max_entries_per_feed", self.RSS_MAX_ENTRIES_PER_FEED)
            )
            self.FINNHUB_FOR_MATCHED_SYMBOLS = bool(
                live.get(
                    "finnhub_for_matched_symbols",
                    live.get("yfinance_for_matched_symbols", self.FINNHUB_FOR_MATCHED_SYMBOLS),
                )
            )
            self.MAX_FINNHUB_SYMBOLS = int(
                live.get(
                    "max_finnhub_symbols",
                    live.get("max_yfinance_symbols", self.MAX_FINNHUB_SYMBOLS),
                )
            )
        elif not self.LIVE_RSS_FEEDS:
            self.LIVE_RSS_FEEDS = list(DEFAULT_LIVE_RSS_FEEDS)

        self.LLM_MODEL = str(y.get("llm_model", self.LLM_MODEL))
        self.LLM_MAX_TOKENS = int(y.get("llm_max_tokens", self.LLM_MAX_TOKENS))
        self.REQUEST_TIMEOUT = int(y.get("request_timeout", self.REQUEST_TIMEOUT))
        self.LLM_REQUEST_TIMEOUT = int(y.get("llm_request_timeout", self.LLM_REQUEST_TIMEOUT))
        self.RETRIES = int(y.get("retries", self.RETRIES))
        proxies = y.get("proxy_list")
        if isinstance(proxies, list):
            self.PROXY_LIST = [str(p) for p in proxies]
        self.SCHEDULE_TIME = str(y.get("schedule_time", self.SCHEDULE_TIME))
        self.SCHEDULE_TIMEZONE = str(y.get("schedule_timezone", self.SCHEDULE_TIMEZONE))
        self.LOG_LEVEL = str(y.get("log_level", self.LOG_LEVEL))
        self.ENABLE_DISCORD = bool(y.get("enable_discord", self.ENABLE_DISCORD))
        self.ENABLE_TELEGRAM = bool(y.get("enable_telegram", self.ENABLE_TELEGRAM))

        self.OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", self.OPENROUTER_API_KEY)
        self.FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", self.FINNHUB_API_KEY)
        if os.getenv("OPENROUTER_API_URL"):
            self.OPENROUTER_API_URL = os.getenv("OPENROUTER_API_URL", "").strip()
        self.DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", self.DISCORD_WEBHOOK_URL)
        self.TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", self.TELEGRAM_BOT_TOKEN)
        self.TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", self.TELEGRAM_CHAT_ID)
        self.NEWS_API_KEY = os.getenv("NEWS_API_KEY", self.NEWS_API_KEY)

        if os.getenv("LLM_MODEL"):
            self.LLM_MODEL = os.getenv("LLM_MODEL", self.LLM_MODEL).strip()
        if os.getenv("LLM_MAX_TOKENS"):
            self.LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", str(self.LLM_MAX_TOKENS)))
        if os.getenv("REQUEST_TIMEOUT"):
            self.REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", str(self.REQUEST_TIMEOUT)))
        if os.getenv("LLM_REQUEST_TIMEOUT"):
            self.LLM_REQUEST_TIMEOUT = int(os.getenv("LLM_REQUEST_TIMEOUT", str(self.LLM_REQUEST_TIMEOUT)))
        if os.getenv("RETRIES"):
            self.RETRIES = int(os.getenv("RETRIES", str(self.RETRIES)))
        if os.getenv("LOG_LEVEL"):
            self.LOG_LEVEL = os.getenv("LOG_LEVEL", self.LOG_LEVEL)
        self.ENABLE_DISCORD = _parse_bool(os.getenv("ENABLE_DISCORD"), self.ENABLE_DISCORD)
        self.ENABLE_TELEGRAM = _parse_bool(os.getenv("ENABLE_TELEGRAM"), self.ENABLE_TELEGRAM)
        if os.getenv("SCHEDULE_TIME"):
            self.SCHEDULE_TIME = os.getenv("SCHEDULE_TIME", self.SCHEDULE_TIME)
        if os.getenv("SCHEDULE_TIMEZONE"):
            self.SCHEDULE_TIMEZONE = os.getenv("SCHEDULE_TIMEZONE", self.SCHEDULE_TIMEZONE)

        self.LLM_MODEL = _normalize_openrouter_model(self.LLM_MODEL)

    def validate(self) -> bool:
        """Validate that required configuration is present"""
        if not self.OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY must be set")
        if not self.FINNHUB_API_KEY:
            raise ValueError("FINNHUB_API_KEY must be set")
        if not (self.DISCORD_WEBHOOK_URL or (self.TELEGRAM_BOT_TOKEN and self.TELEGRAM_CHAT_ID)):
            raise ValueError("At least one notification channel (Discord or Telegram) must be configured")
        return True


config = Config()
