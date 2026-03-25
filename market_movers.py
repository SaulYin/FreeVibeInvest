"""
Finnhub-backed mover selection.
Gainers/losers come from quote change across a liquid US large-cap universe.
"""
from __future__ import annotations

from collections import Counter
import logging
from typing import Dict, List

from config import config
from finnhub_api import finnhub_get

logger = logging.getLogger(__name__)

_LIQUID_FALLBACK = [
    "NVDA",
    "AAPL",
    "MSFT",
    "AMZN",
    "META",
    "GOOGL",
    "AVGO",
    "TSLA",
    "AMD",
    "NFLX",
    "JPM",
    "SPY",
    "QQQ",
    "DIA",
]


def _load_mover_universe() -> List[str]:
    return list(_LIQUID_FALLBACK)


def _collect_quote_changes(timeout: int) -> List[Dict]:
    rows: List[Dict] = []
    for symbol in _load_mover_universe():
        try:
            payload = finnhub_get("/quote", {"symbol": symbol}, timeout=timeout)
        except Exception as exc:
            logger.debug("Finnhub quote skipped for %s: %s", symbol, exc)
            continue
        if not isinstance(payload, dict):
            continue
        current_price = payload.get("c") or 0
        previous_close = payload.get("pc") or 0
        if not current_price and not previous_close:
            continue
        try:
            percent_change = float(payload.get("dp") or 0.0)
        except (TypeError, ValueError):
            percent_change = 0.0
        rows.append(
            {
                "symbol": symbol,
                "dp": percent_change,
                "price": current_price,
            }
        )
    logger.info("Finnhub quotes collected for %d universe symbols", len(rows))
    return rows


def _news_interest_proxy(count: int, timeout: int, allowed: set[str]) -> List[str]:
    if count <= 0:
        return []
    mentions: Counter[str] = Counter()
    try:
        payload = finnhub_get(
            "/news",
            {"category": config.MP_MARKET_NEWS_CATEGORY},
            timeout=timeout,
        )
        items = payload if isinstance(payload, list) else []
        for item in items[:50]:
            related = str(item.get("related") or "")
            for symbol in related.split(","):
                ticker = symbol.strip().upper()
                if ticker in allowed:
                    mentions[ticker] += 1
    except Exception as exc:
        logger.warning("Finnhub market-news proxy failed: %s", exc)
    if mentions:
        ordered = [symbol for symbol, _ in mentions.most_common(count)]
        logger.info("Finnhub market-news proxy → %d symbols", len(ordered))
        return ordered
    return [symbol for symbol in _LIQUID_FALLBACK if symbol in allowed][:count]


def fetch_mover_symbol_order(
    gainers_limit: int,
    losers_limit: int,
    most_actives_limit: int,
    timeout: int,
) -> List[str]:
    """
    Ordered unique list: gainers first, then losers, then a news-interest proxy.
    Preserves priority for headline budget caps.
    """
    quotes = _collect_quote_changes(timeout)
    gainers = [
        row["symbol"]
        for row in sorted((r for r in quotes if r["dp"] > 0), key=lambda r: r["dp"], reverse=True)[
            :gainers_limit
        ]
    ]
    losers = [
        row["symbol"]
        for row in sorted((r for r in quotes if r["dp"] < 0), key=lambda r: r["dp"])[
            :losers_limit
        ]
    ]
    interest = _news_interest_proxy(
        most_actives_limit,
        timeout,
        {row["symbol"] for row in quotes} | set(_LIQUID_FALLBACK),
    )
    seen = set()
    ordered: List[str] = []
    for bucket in (gainers, losers, interest):
        for s in bucket:
            if s not in seen:
                seen.add(s)
                ordered.append(s)
    return ordered
