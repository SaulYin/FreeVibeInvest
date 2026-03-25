"""
Phase 1: Live market headlines — Finnhub market news, company news, and optional RSS.
"""
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, FrozenSet, List, Optional, Set

import feedparser
import requests

from config import config
from finnhub_api import finnhub_get, finnhub_timestamp_to_iso
from market_movers import fetch_mover_symbol_order

logger = logging.getLogger(__name__)

_USER_AGENT = "StockAdviceBot/1.0 (investment research; +https://github.com)"


def _fetch_rss_feed(url: str):
    headers = {"User-Agent": _USER_AGENT}
    timeout = getattr(config, "RSS_REQUEST_TIMEOUT", None) or config.REQUEST_TIMEOUT
    r = requests.get(url, timeout=timeout, headers=headers)
    r.raise_for_status()
    return feedparser.parse(r.content)


def _extract_tickers_from_text(text: str, universe: FrozenSet[str]) -> List[str]:
    found: Set[str] = set()
    for m in re.finditer(r"\$([A-Z]{1,5})\b", text):
        t = m.group(1)
        if t in universe:
            found.add(t)
    for m in re.finditer(r"\b([A-Z]{3,5})\b", text):
        t = m.group(1)
        if t in universe:
            found.add(t)
    return sorted(found, key=len, reverse=True)


def _dedupe_key(item: Dict) -> str:
    link = (item.get("link") or "").strip()
    title = (item.get("title") or "").strip()
    return link if link else f"t:{title}"


def _iso_now() -> str:
    return datetime.now().isoformat()


def _date_days_ago(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).date().isoformat()


_YAHOO_FINANCE_RSS_URL = "https://finance.yahoo.com/rss/headline?s={symbol}"


def _normalize_finnhub_news_item(item: Dict, symbol: str) -> Dict:
    return {
        "source": item.get("source") or "Finnhub",
        "title": item.get("headline", ""),
        "summary": (item.get("summary") or "")[:500],
        "link": item.get("url", ""),
        "published": finnhub_timestamp_to_iso(item.get("datetime")),
        "symbol": symbol,
        "timestamp": _iso_now(),
    }


class FinancialNewsScraper:
    def __init__(self):
        pass

    def scrape_yahoo_finance_per_ticker(self, symbol: str, max_items: int = 8) -> List[Dict]:
        """Yahoo Finance RSS headlines for a single ticker."""
        url = _YAHOO_FINANCE_RSS_URL.format(symbol=symbol)
        try:
            feed = _fetch_rss_feed(url)
            articles: List[Dict] = []
            for entry in feed.entries[:max_items]:
                articles.append(
                    {
                        "source": "Yahoo Finance",
                        "title": entry.get("title", ""),
                        "summary": (entry.get("summary", "") or "")[:500],
                        "link": entry.get("link", ""),
                        "published": entry.get("published", ""),
                        "symbol": symbol,
                        "timestamp": _iso_now(),
                    }
                )
            logger.info("Yahoo Finance RSS: %d headlines for %s", len(articles), symbol)
            return articles
        except Exception as e:
            logger.warning("Yahoo Finance RSS failed for %s: %s", symbol, e)
            return []

    def scrape_finnhub_company_news(self, symbol: str, max_items: int = 10) -> List[Dict]:
        try:
            payload = finnhub_get(
                "/company-news",
                {
                    "symbol": symbol,
                    "from": _date_days_ago(4),
                    "to": datetime.utcnow().date().isoformat(),
                },
                timeout=config.REQUEST_TIMEOUT,
            )
            items = payload if isinstance(payload, list) else []
            news = [_normalize_finnhub_news_item(item, symbol) for item in items[:max_items]]
            logger.info("Finnhub company news: %d articles for %s", len(news), symbol)
            return news
        except Exception as e:
            logger.error("Finnhub company news error for %s: %s", symbol, e)
            return []

    def scrape_finnhub_market_news(self) -> List[Dict]:
        articles: List[Dict] = []
        try:
            payload = finnhub_get(
                "/news",
                {"category": config.MP_MARKET_NEWS_CATEGORY},
                timeout=config.REQUEST_TIMEOUT,
            )
            items = payload if isinstance(payload, list) else []
            for item in items[: config.MP_MACRO_NEWS_MAX_ITEMS]:
                articles.append(_normalize_finnhub_news_item(item, "MARKET"))
            logger.info(
                "Finnhub market news: %d articles for category=%s",
                len(articles),
                config.MP_MARKET_NEWS_CATEGORY,
            )
        except Exception as e:
            logger.error("Finnhub market news failed: %s", e)
        return articles

    def scrape_rss_unfiltered(self) -> List[Dict]:
        """General macro / sector headlines (no ticker filter)."""
        articles: List[Dict] = []
        max_e = config.RSS_MAX_ENTRIES_PER_FEED
        for feed_url in config.LIVE_RSS_FEEDS:
            try:
                feed = _fetch_rss_feed(feed_url)
                title_feed = feed.feed.get("title", "RSS")
                for entry in feed.entries[:max_e]:
                    articles.append(
                        {
                            "source": title_feed,
                            "title": entry.get("title", ""),
                            "summary": (entry.get("summary", "") or "")[:500],
                            "link": entry.get("link", ""),
                            "published": entry.get("published", ""),
                            "symbol": "MARKET",
                                "timestamp": _iso_now(),
                        }
                    )
                logger.info("RSS (market pulse): %s", feed_url)
            except Exception as e:
                logger.error("RSS failed %s: %s", feed_url, e)
        return articles

    def _merge_dedupe(self, buckets: List[List[Dict]], max_total: int) -> List[Dict]:
        seen: Set[str] = set()
        out: List[Dict] = []
        for bucket in buckets:
            for item in bucket:
                k = _dedupe_key(item)
                if not k or k in seen:
                    continue
                seen.add(k)
                out.append(item)
                if len(out) >= max_total:
                    return out
        return out

    def scrape_market_pulse_bundle(self) -> Dict[str, List[Dict]]:
        """Finnhub market news + company news for movers + optional broad RSS."""
        parts: List[List[Dict]] = [self.scrape_finnhub_market_news()]
        cap = config.MP_FINNHUB_NEWS_MAX_PER_TICKER
        news_syms: List[str] = []
        if config.MP_MOVERS_ENABLED:
            news_syms = fetch_mover_symbol_order(
                config.MP_MOVERS_GAINERS_LIMIT,
                config.MP_MOVERS_LOSERS_LIMIT,
                config.MP_MOVERS_MOST_ACTIVES_LIMIT,
                config.REQUEST_TIMEOUT,
            )
        for sym in config.MP_EXTRA_NEWS_TICKERS:
            if sym not in news_syms:
                news_syms.append(sym)
        news_syms = news_syms[: config.MP_MAX_SYMBOLS_FOR_MOVER_NEWS]
        logger.info(
            "Finnhub company news for %d symbols (movers + extra, cap %d)",
            len(news_syms),
            config.MP_MAX_SYMBOLS_FOR_MOVER_NEWS,
        )
        for sym in news_syms:
            parts.append(self.scrape_finnhub_company_news(sym, max_items=cap))
        if config.YAHOO_PER_TICKER_ENABLED:
            for sym in news_syms:
                parts.append(
                    self.scrape_yahoo_finance_per_ticker(sym, max_items=config.YAHOO_MAX_PER_TICKER)
                )
        if config.MERGE_UNFILTERED_RSS_IN_MARKET_PULSE and config.LIVE_NEWS_ENABLED:
            parts.append(self.scrape_rss_unfiltered())
        merged = self._merge_dedupe(parts, config.MP_MAX_HEADLINES_FOR_LLM)
        logger.info("Market pulse bundle: %d unique headlines for LLM", len(merged))
        return {"news": merged, "earnings": [], "timestamp": _iso_now()}

    def scrape_all(self) -> Dict[str, List[Dict]]:
        return self.scrape_market_pulse_bundle()


def get_market_context(symbols: List[str]) -> Dict:
    context = {"timestamp": _iso_now(), "stocks": {}}
    for symbol in symbols:
        try:
            payload = finnhub_get(
                "/quote",
                {"symbol": symbol},
                timeout=config.REQUEST_TIMEOUT,
            )
            if not isinstance(payload, dict):
                raise ValueError("unexpected Finnhub quote payload")
            context["stocks"][symbol] = {
                "current_price": payload.get("c", "N/A"),
                "previous_close": payload.get("pc", "N/A"),
                "day_high": payload.get("h", "N/A"),
                "day_low": payload.get("l", "N/A"),
                "percent_change": payload.get("dp", "N/A"),
            }
        except Exception as e:
            logger.error("Market context %s: %s", symbol, e)
            context["stocks"][symbol] = {"error": str(e)}
    return context
