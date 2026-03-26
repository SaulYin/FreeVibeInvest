"""
Phase 2: AI Sentiment Analysis Module
Uses OpenRouter API to analyze news sentiment and extract market catalysts
"""
import json
import logging
import re
import time
import requests
from json import JSONDecoder
from typing import Any, Dict, List, Optional
from config import config

logger = logging.getLogger(__name__)


def _llm_http_timeout() -> int:
    return max(120, int(config.REQUEST_TIMEOUT))


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences that some models wrap around JSON output."""
    t = text.strip()
    if t.startswith("```"):
        # drop the opening fence line (```json or just ```)
        t = t[t.find("\n") + 1:] if "\n" in t else t[3:]
    if t.endswith("```"):
        t = t[: t.rfind("```")]
    return t.strip()


_CONFIDENCE_WORDS = {"high": 80, "medium": 60, "low": 40}


def _parse_confidence(val: Any, default: int = 65) -> int:
    """Convert an LLM confidence value to an int 0-100.
    Handles numbers, numeric strings, and word labels (High/Medium/Low)."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return max(0, min(100, int(val)))
    s = str(val).strip().lower()
    if s in _CONFIDENCE_WORDS:
        return _CONFIDENCE_WORDS[s]
    try:
        return max(0, min(100, int(float(s))))
    except (ValueError, TypeError):
        return default

def _parse_json_object(text: str) -> Optional[Dict[str, Any]]:
    text = _strip_code_fences(text)
    start = text.find("{")
    if start < 0:
        return None
    try:
        obj, _ = JSONDecoder().raw_decode(text[start:])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


class SentimentAnalyzer:
    """Analyzes financial news sentiment using OpenRouter chat models"""
    
    MARKET_PULSE_SYSTEM = """You are a US equity market analyst. You ONLY infer from the headlines provided — do not invent tickers or facts not in the text.

Rules:
- Prefer liquid US tickers (1–5 uppercase letters; BRK.B style allowed). Ignore bonds/ETFs unless newsworthy.
- Do not list the same ticker in both bullish and bearish.
- Aim for up to 5 bullish, up to 4 bearish, up to 3 potential_buys.
- Keep each thesis and risk under 12 words. market_overview: 1-2 sentences.
- Educational only — not personal financial advice.

Return ONLY valid JSON (no markdown code fences) with exactly this shape:
{
  "market_overview": "1-2 sentences",
  "themes": ["short label"],
  "bullish": [{"symbol": "TICKER", "thesis": "string", "confidence": 0-100}],
  "bearish": [{"symbol": "TICKER", "thesis": "string", "confidence": 0-100}],
  "potential_buys": [{"symbol": "TICKER", "thesis": "string", "risk": "string", "conviction": "High|Medium|Low"}]
}"""

    MARKET_PULSE_TERSE = """You are a US equity market analyst. Only use facts from the headlines below.
Return ONLY valid JSON (no markdown). Max 3 bullish, 2 bearish, 2 potential_buys. Max 8 words per field.
{
  "market_overview": "1 sentence",
  "themes": ["label"],
  "bullish": [{"symbol": "TICKER", "thesis": "string", "confidence": 0-100}],
  "bearish": [{"symbol": "TICKER", "thesis": "string", "confidence": 0-100}],
  "potential_buys": [{"symbol": "TICKER", "thesis": "string", "risk": "string", "conviction": "High|Medium|Low"}]
}"""

    def __init__(self):
        self.api_key = config.OPENROUTER_API_KEY
        self.api_url = config.OPENROUTER_API_URL
        self.model = config.LLM_MODEL

    def analyze_market_pulse(
        self, articles: List[Dict], index_context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Single LLM pass over a bundle of live headlines → bulls / bears / potential buys.
        """
        _EMPTY = {
            "market_overview": "",
            "themes": [],
            "bullish": [],
            "bearish": [],
            "potential_buys": [],
        }
        _MAX_ATTEMPTS = 3
        _HEADLINE_BACKOFF = 0.50  # trim input by 50% on each length failure
        # Keep max_tokens at the configured value — never shrink it on retry.
        # Shrinking max_tokens is counterproductive: the model already can't fit
        # its reply, so requesting fewer tokens makes truncation worse.
        _MAX_TOKENS = config.LLM_MARKET_PULSE_MAX_TOKENS

        ctx_lines = []
        if index_context and index_context.get("stocks"):
            for sym, d in index_context["stocks"].items():
                if isinstance(d, dict) and "error" not in d:
                    ctx_lines.append(
                        f"{sym}: last {d.get('current_price')} (prev {d.get('previous_close')})"
                    )
        ctx_block = "\n".join(ctx_lines) if ctx_lines else "N/A"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "HTTP-Referer": "https://investment-research.local",
            "X-OpenRouter-Title": "Investment Research Pipeline",
            "Content-Type": "application/json",
        }

        cap = min(len(articles), config.MP_MAX_HEADLINES_FOR_LLM)
        length_failures = 0  # track how many times we hit the output token limit

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            terse = length_failures > 0
            system_content = self.MARKET_PULSE_TERSE if terse else self.MARKET_PULSE_SYSTEM
            max_tokens = _MAX_TOKENS

            lines = []
            title_limit = 140 if terse else 200
            summary_limit = 80 if terse else 140
            for i, a in enumerate(articles[:cap], 1):
                title = (a.get("title") or "")[:title_limit]
                summary = (a.get("summary") or "")[:summary_limit]
                src = a.get("source") or "?"
                lines.append(f"{i}. [{src}] {title}\n   {summary}")
            blob = "\n".join(lines)

            user_msg = (
                "Here are today's market headlines (Finnhub market/company news + Yahoo Finance + RSS feeds), deduped.\n"
                "Scan ALL of them for ticker mentions and extract as many signals as the text supports:\n\n"
                f"{blob}\n\n"
                f"Liquid index proxies:\n{ctx_block}\n"
            )
            payload = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_msg},
                ],
                # Cap reasoning tokens for thinking models (e.g. stepfun)
                # so they don't consume the entire max_tokens budget on
                # chain-of-thought, leaving nothing for the JSON response.
                "reasoning": {"effort": "low"},
            }

            try:
                logger.info(
                    "Market pulse LLM attempt %d/%d (%d headlines, max_tokens=%d%s)",
                    attempt, _MAX_ATTEMPTS, cap, max_tokens, ", terse" if terse else "",
                )
                response = requests.post(
                    self.api_url, headers=headers, json=payload, timeout=_llm_http_timeout()
                )
                response.raise_for_status()
                resp_json = response.json()
                message = resp_json["choices"][0]["message"]
                text = message.get("content") or ""
                finish = resp_json["choices"][0].get("finish_reason", "")

                if not text:
                    refusal = message.get("refusal") or ""
                    logger.warning(
                        "Market pulse attempt %d: empty content (finish_reason=%r, refusal=%r)",
                        attempt, finish, refusal,
                    )
                    if finish == "length":
                        length_failures += 1
                        cap = max(10, int(cap * _HEADLINE_BACKOFF))
                        logger.info(
                            "length failure — reducing headline cap to %d, terse mode on next attempt",
                            cap,
                        )
                    continue

                parsed = _parse_json_object(text)
                if not parsed:
                    logger.warning(
                        "Market pulse attempt %d: could not parse JSON (finish_reason=%r). Raw (first 600 chars):\n%s",
                        attempt, finish, text[:600],
                    )
                    if finish == "length":
                        length_failures += 1
                        cap = max(10, int(cap * _HEADLINE_BACKOFF))
                        logger.info(
                            "length failure (JSON parse) — cap→%d, terse on next attempt", cap
                        )
                    continue

                logger.info(
                    "Market pulse attempt %d succeeded: %d bull, %d bear, %d buys",
                    attempt,
                    len(parsed.get("bullish") or []),
                    len(parsed.get("bearish") or []),
                    len(parsed.get("potential_buys") or []),
                )
                return parsed

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status == 429:
                    retry_after = int(e.response.headers.get("Retry-After", 60))
                    wait = min(retry_after, 120)
                    logger.warning(
                        "Market pulse attempt %d: 429 rate-limited — waiting %ds before retry",
                        attempt, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error("Market pulse LLM attempt %d HTTP error: %s", attempt, e)
                if attempt == _MAX_ATTEMPTS:
                    return {**_EMPTY, "market_overview": str(e), "error": str(e)}
            except Exception as e:
                logger.error("Market pulse LLM attempt %d error: %s", attempt, e)
                if attempt == _MAX_ATTEMPTS:
                    return {**_EMPTY, "market_overview": str(e), "error": str(e)}

        logger.error("Market pulse: all %d attempts failed", _MAX_ATTEMPTS)
        return {**_EMPTY, "market_overview": "All LLM attempts failed.", "error": "max_retries"}

    @staticmethod
    def market_pulse_report_to_stock_analysis(report: Dict[str, Any]) -> Dict[str, Dict]:
        """Map structured market pulse JSON into per-ticker dicts the rest of the pipeline expects."""
        out: Dict[str, Dict] = {}

        def blank(sym: str) -> Dict:
            return {
                "symbol": sym,
                "sentiment": "Neutral",
                "sentiment_score": 0.0,
                "buy_recommendation": "Hold",
                "buy_score": 0,
                "buy_rationale": "",
                "catalysts": [],
                "summary": "",
                "confidence": 0,
            }

        conv_to_score = {"high": 78, "medium": 58, "low": 42}

        for b in report.get("bullish") or []:
            if isinstance(b, str):
                b = {"symbol": b}
            if not isinstance(b, dict):
                continue
            sym = str(b.get("symbol", "")).upper().strip()
            if not sym or not re.match(r"^[A-Z][A-Z0-9.]{0,9}$", sym):
                continue
            o = blank(sym)
            o["sentiment"] = "Bullish"
            o["summary"] = str(b.get("thesis", ""))
            o["confidence"] = _parse_confidence(b.get("confidence"), 65)
            o["sentiment_score"] = min(1.0, max(0.0, o["confidence"] / 100.0))
            out[sym] = o

        for b in report.get("bearish") or []:
            if isinstance(b, str):
                b = {"symbol": b}
            if not isinstance(b, dict):
                continue
            sym = str(b.get("symbol", "")).upper().strip()
            if not sym or not re.match(r"^[A-Z][A-Z0-9.]{0,9}$", sym):
                continue
            if sym in out:
                continue
            o = blank(sym)
            o["sentiment"] = "Bearish"
            o["summary"] = str(b.get("thesis", ""))
            o["confidence"] = _parse_confidence(b.get("confidence"), 65)
            o["sentiment_score"] = -min(1.0, max(0.0, o["confidence"] / 100.0))
            out[sym] = o

        for p in report.get("potential_buys") or []:
            if isinstance(p, str):
                p = {"symbol": p}
            if not isinstance(p, dict):
                continue
            sym = str(p.get("symbol", "")).upper().strip()
            if not sym or not re.match(r"^[A-Z][A-Z0-9.]{0,9}$", sym):
                continue
            thesis = str(p.get("thesis", ""))
            risk = str(p.get("risk", ""))
            conv = str(p.get("conviction", "Medium")).split("|")[0].strip().lower()
            score = conv_to_score.get(conv, 52)
            if sym not in out:
                o = blank(sym)
                o["sentiment"] = "Bullish"
                o["summary"] = thesis
                o["confidence"] = score
                o["sentiment_score"] = score / 100.0
                out[sym] = o
            o = out[sym]
            o["buy_recommendation"] = "Buy" if score >= 50 else "Hold"
            o["buy_score"] = score
            o["buy_rationale"] = f"{thesis} | Risk: {risk}" if risk else thesis
            if thesis:
                o["catalysts"] = [
                    {"catalyst": thesis[:240], "impact": "Medium", "timeframe": "short-term"}
                ]

        return out

    def generate_executive_summary(self, analysis_results: Dict[str, Dict]) -> str:
        """
        Generate actionable market analysis with buy recommendations
        """
        bullish_stocks = [
            (s, a) for s, a in analysis_results.items() 
            if a.get('sentiment') == 'Bullish'
        ]
        bearish_stocks = [
            (s, a) for s, a in analysis_results.items() 
            if a.get('sentiment') == 'Bearish'
        ]
        buy_recommendations = [
            (s, a) for s, a in analysis_results.items() 
            if a.get('buy_recommendation', '').lower() in ['strong buy', 'buy']
        ]
        
        # Sort buy recommendations by buy score
        buy_recommendations.sort(key=lambda x: x[1].get('buy_score', 0), reverse=True)
        
        summary = f"""
📊 **MARKET ANALYSIS SUMMARY**

"""
        
        # ===== CURRENT MARKET SENTIMENT =====
        summary += f"""{'='*50}
📈 MARKET SENTIMENT OVERVIEW
{'='*50}

🚀 BULLISH STOCKS ({len(bullish_stocks)}):
"""
        if bullish_stocks:
            for symbol, analysis in bullish_stocks:
                score = analysis.get('sentiment_score', 0)
                confidence = analysis.get('confidence', 0)
                summary += f"   • {symbol} (Score: {score:.2f}, Confidence: {confidence}%)\n"
                if analysis.get('summary'):
                    summary += f"     → {analysis['summary']}\n"
        else:
            summary += "   None\n"
        
        summary += f"""
📉 BEARISH STOCKS ({len(bearish_stocks)}):
"""
        if bearish_stocks:
            for symbol, analysis in bearish_stocks:
                score = analysis.get('sentiment_score', 0)
                confidence = analysis.get('confidence', 0)
                summary += f"   • {symbol} (Score: {score:.2f}, Confidence: {confidence}%)\n"
                if analysis.get('summary'):
                    summary += f"     → {analysis['summary']}\n"
        else:
            summary += "   None\n"
        
        # ===== BUY RECOMMENDATIONS =====
        summary += f"""
{'='*50}
💰 STOCKS TO BUY (Buy Opportunities)
{'='*50}

"""
        if buy_recommendations:
            for symbol, analysis in buy_recommendations:
                buy_score = analysis.get('buy_score', 0)
                recommendation = analysis.get('buy_recommendation', 'Hold')
                rationale = analysis.get('buy_rationale', '')
                
                summary += f"\n🎯 {symbol} - {recommendation} (Buy Score: {buy_score}%)\n"
                summary += f"   Rationale: {rationale}\n"
                
                # Top catalyst for buying
                if analysis.get('catalysts'):
                    top_catalyst = analysis['catalysts'][0]
                    summary += f"   Key Driver: {top_catalyst.get('catalyst', 'N/A')}\n"
        else:
            summary += "   No strong buy opportunities identified at this time.\n"
        
        # ===== TOP CATALYSTS =====
        summary += f"""
{'='*50}
🔥 TOP MARKET CATALYSTS
{'='*50}

"""
        all_catalysts = []
        for symbol, analysis in analysis_results.items():
            for catalyst in analysis.get('catalysts', [])[:2]:  # Top 2 catalysts per stock
                all_catalysts.append({
                    'symbol': symbol,
                    'catalyst': catalyst.get('catalyst', ''),
                    'impact': catalyst.get('impact', ''),
                    'timeframe': catalyst.get('timeframe', ''),
                })
        
        # Sort by impact
        impact_order = {'High': 0, 'Medium': 1, 'Low': 2}
        all_catalysts.sort(key=lambda x: impact_order.get(x['impact'], 3))
        
        for i, cat in enumerate(all_catalysts[:8], 1):
            summary += f"{i}. [{cat['symbol']}] {cat['catalyst']}\n"
            summary += f"   Impact: {cat['impact']} | Timeframe: {cat['timeframe']}\n"
        
        return summary
    
    def get_top_buy_opportunities(self, analysis_results: Dict[str, Dict], limit: int = 5) -> List[Dict]:
        """
        Identify and rank the top stocks to buy based on analysis
        """
        buy_opportunities = []
        
        for symbol, analysis in analysis_results.items():
            buy_score = analysis.get('buy_score', 0)
            buy_recommendation = analysis.get('buy_recommendation', 'Hold').lower()
            
            # Only consider Buy or Strong Buy recommendations
            if buy_recommendation in ['buy', 'strong buy'] and buy_score >= 50:
                buy_opportunities.append({
                    'symbol': symbol,
                    'buy_score': buy_score,
                    'buy_recommendation': analysis.get('buy_recommendation', ''),
                    'buy_rationale': analysis.get('buy_rationale', ''),
                    'sentiment': analysis.get('sentiment', ''),
                    'sentiment_score': analysis.get('sentiment_score', 0),
                    'confidence': analysis.get('confidence', 0),
                    'top_catalyst': analysis.get('catalysts', [{}])[0] if analysis.get('catalysts') else {},
                })
        
        # Sort by buy score
        buy_opportunities.sort(key=lambda x: x['buy_score'], reverse=True)
        
        return buy_opportunities[:limit]
