from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from config import config

logger = logging.getLogger(__name__)

_BASE_URL = "https://finnhub.io/api/v1"
_HEADERS = {
    "User-Agent": "StockAdviceBot/1.0 (investment research; +https://github.com)",
    "Accept": "application/json",
}


def finnhub_get(endpoint: str, params: Optional[Dict[str, Any]] = None, timeout: Optional[int] = None) -> Any:
    query = dict(params or {})
    query["token"] = config.FINNHUB_API_KEY
    max_attempts = max(1, config.RETRIES)
    last_error: Optional[Exception] = None

    for attempt in range(max_attempts):
        if config.FINNHUB_REQUEST_PAUSE_SEC > 0:
            time.sleep(config.FINNHUB_REQUEST_PAUSE_SEC)
        try:
            response = requests.get(
                f"{_BASE_URL}{endpoint}",
                params=query,
                headers=_HEADERS,
                timeout=timeout or config.REQUEST_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            last_error = exc
            if _is_transient_error(exc) and attempt + 1 < max_attempts:
                wait = 2 ** (attempt + 1)
                logger.warning("Finnhub %s retry in %ds: %s", endpoint, wait, exc)
                time.sleep(wait)
                continue
            raise

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"Finnhub request failed for {endpoint}")


def finnhub_timestamp_to_iso(value: Any) -> str:
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return ""
    if ts <= 0:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _is_transient_error(err: Exception) -> bool:
    text = str(err).lower()
    return (
        "429" in text
        or "too many requests" in text
        or "timeout" in text
        or "connection" in text
        or "temporarily unavailable" in text
    )