"""
Phase 3: Notification Delivery Module
Sends formatted briefings to Discord and Telegram
"""
import logging
import aiohttp
import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from config import config

logger = logging.getLogger(__name__)


def _fmt_score(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


class NotificationManager:
    """Handles sending notifications to Discord and Telegram"""
    
    def __init__(self):
        self.discord_webhook = config.DISCORD_WEBHOOK_URL
        self.telegram_token = config.TELEGRAM_BOT_TOKEN
        self.telegram_chat_id = config.TELEGRAM_CHAT_ID
    
    async def send_notification(
        self,
        briefing: str,
        analysis: Dict[str, Dict],
        pulse_meta: Optional[Dict] = None,
    ) -> bool:
        """
        Send notification to configured channels
        """
        results = []
        
        if config.ENABLE_DISCORD and self.discord_webhook:
            result = await self._send_discord(briefing, analysis, pulse_meta=pulse_meta)
            results.append(result)
        
        if config.ENABLE_TELEGRAM and self.telegram_token and self.telegram_chat_id:
            result = await self._send_telegram(briefing, analysis, pulse_meta=pulse_meta)
            results.append(result)
        
        return all(results) if results else False
    
    async def _send_discord(
        self, briefing: str, analysis: Dict[str, Dict], pulse_meta: Optional[Dict] = None
    ) -> bool:
        """
        Send formatted message to Discord webhook.
        Discord limits: 10 embeds per request, 6000 total chars across embeds.
        Chunks into multiple requests when needed.
        """
        _FIELD_LIMIT = 1024
        _EMBED_LIMIT = 10

        def _cap_fields(embed: Dict) -> Dict:
            for f in embed.get("fields", []):
                if len(f.get("value", "")) > _FIELD_LIMIT:
                    f["value"] = f["value"][:_FIELD_LIMIT - 1] + "…"
            if len(embed.get("description", "")) > 4096:
                embed["description"] = embed["description"][:4095] + "…"
            return embed

        try:
            embeds = [_cap_fields(e) for e in self._format_discord_embeds(analysis, pulse_meta=pulse_meta)]
            chunks = [embeds[i:i + _EMBED_LIMIT] for i in range(0, max(len(embeds), 1), _EMBED_LIMIT)]
            async with aiohttp.ClientSession() as session:
                for idx, chunk in enumerate(chunks):
                    payload = {
                        "content": briefing if idx == 0 else "",
                        "embeds": chunk,
                    }
                    async with session.post(self.discord_webhook, json=payload) as response:
                        if response.status not in (200, 204):
                            body = await response.text()
                            logger.error("Discord API error: %s — %s", response.status, body[:400])
                            return False
            logger.info("Discord notification sent (%d embed(s), %d request(s))", len(embeds), len(chunks))
            return True

        except Exception as e:
            logger.error("Error sending Discord notification: %s", e)
            return False
    
    async def _send_telegram(
        self, briefing: str, analysis: Dict[str, Dict], pulse_meta: Optional[Dict] = None
    ) -> bool:
        """
        Send formatted message to Telegram
        """
        try:
            message = self._format_telegram_message(briefing, analysis, pulse_meta=pulse_meta)
            
            url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
            payload = {
                "chat_id": self.telegram_chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    if response.status == 200:
                        logger.info("Telegram notification sent successfully")
                        return True
                    else:
                        logger.error(f"Telegram API error: {response.status}")
                        return False
        
        except Exception as e:
            logger.error(f"Error sending Telegram notification: {e}")
            return False
    
    def _format_discord_embeds(
        self, analysis: Dict[str, Dict], pulse_meta: Optional[Dict] = None
    ) -> List[Dict]:
        """
        Format analysis results as Discord embeds with buy recommendations
        """
        # Separate embeds: Summary + Individual stocks
        embeds = []
        
        # Color codes
        sentiment_colors = {
            'Bullish': 0x00FF00,      # Green
            'Bearish': 0xFF0000,      # Red
            'Neutral': 0x808080       # Gray
        }
        
        recommendation_colors = {
            'Strong Buy': 0x00AA00,   # Bright Green
            'Buy': 0x00FF00,          # Green
            'Hold': 0xFFAA00,         # Orange
            'Sell': 0xFF0000          # Red
        }
        
        # Create summary embed first
        bullish_list = [s for s, a in analysis.items() if a.get('sentiment') == 'Bullish']
        bearish_list = [s for s, a in analysis.items() if a.get('sentiment') == 'Bearish']
        buy_list = [s for s, a in analysis.items() if a.get('buy_recommendation', '').lower() in ['buy', 'strong buy']]
        
        overview = ""
        if pulse_meta and pulse_meta.get("market_overview"):
            overview = str(pulse_meta["market_overview"])[:3500]
        themes = pulse_meta.get("themes") if pulse_meta else None
        if themes:
            overview = (overview + "\n\n**Themes:** " + ", ".join(str(t) for t in themes[:8]))[:4090]

        summary_embed = {
            "title": "📊 Market Analysis Summary",
            "color": 0x0099FF,
            "fields": [
                {
                    "name": f"🚀 Bullish Stocks ({len(bullish_list)})",
                    "value": ', '.join(bullish_list) if bullish_list else 'None',
                    "inline": False
                },
                {
                    "name": f"📉 Bearish Stocks ({len(bearish_list)})",
                    "value": ', '.join(bearish_list) if bearish_list else 'None',
                    "inline": False
                },
                {
                    "name": f"💰 Buy Recommendations ({len(buy_list)})",
                    "value": ', '.join(buy_list) if buy_list else 'None',
                    "inline": False
                }
            ],
            "timestamp": datetime.now().isoformat(),
        }
        if overview:
            summary_embed["description"] = overview
        embeds.append(summary_embed)
        
        # Create individual stock embeds
        for symbol, data in analysis.items():
            sentiment = data.get('sentiment', 'Unknown')
            color = sentiment_colors.get(sentiment, 0x808080)
            
            # Override color for strong buy recommendations
            buy_recommendation = data.get('buy_recommendation', 'Hold')
            if buy_recommendation in ['Strong Buy', 'Buy']:
                color = recommendation_colors.get(buy_recommendation, 0x00FF00)
            
            title = f"{symbol} - {sentiment}"
            if buy_recommendation.lower() in ['buy', 'strong buy']:
                title += f" | {buy_recommendation}"
            
            embed = {
                "title": title,
                "color": color,
                "fields": []
            }

            # Add current price if available
            cur_price = data.get("current_price")
            pct_change = data.get("percent_change")
            if cur_price not in (None, "N/A"):
                price_str = f"${cur_price}"
                if pct_change not in (None, "N/A"):
                    arrow = "▲" if float(pct_change) >= 0 else "▼"
                    price_str += f" ({arrow} {pct_change}%)"
                embed["fields"].append({
                    "name": "Current Price",
                    "value": price_str,
                    "inline": True
                })

            embed["fields"].extend([
                {
                    "name": "Sentiment Score",
                    "value": _fmt_score(data.get("sentiment_score", "N/A")),
                    "inline": True
                },
                {
                    "name": "Confidence",
                    "value": f"{data.get('confidence', 'N/A')}%",
                    "inline": True
                }
            ])
            
            # Add buy recommendation if available
            if data.get('buy_recommendation'):
                embed['fields'].append({
                    "name": "Buy Score",
                    "value": f"{data.get('buy_score', 0)}%",
                    "inline": True
                })
                embed['fields'].append({
                    "name": "Recommendation",
                    "value": data.get('buy_recommendation', 'Hold'),
                    "inline": True
                })
                embed['fields'].append({
                    "name": "Rationale",
                    "value": data.get('buy_rationale', 'N/A'),
                    "inline": False
                })
            elif data.get('summary'):
                # Only show thesis when there's no buy rationale (which already includes it)
                embed['fields'].append({
                    "name": "Investment Thesis",
                    "value": data['summary'],
                    "inline": False
                })
            
            embed['timestamp'] = datetime.now().isoformat()
            embeds.append(embed)
        
        return embeds
    
    def _format_telegram_message(
        self, briefing: str, analysis: Dict[str, Dict], pulse_meta: Optional[Dict] = None
    ) -> str:
        """
        Format analysis results for Telegram with buy recommendations
        """
        message = f"<b>📊 Daily Market Analysis</b>\n"
        now_et = datetime.now(ZoneInfo("US/Eastern"))
        message += f"<i>{now_et.strftime('%Y-%m-%d %H:%M:%S %Z')}</i>\n"
        message += "=" * 40 + "\n\n"
        if pulse_meta and pulse_meta.get("market_overview"):
            message += f"<b>Overview</b>\n{pulse_meta['market_overview'][:2800]}\n\n"
        if pulse_meta and pulse_meta.get("themes"):
            message += "<b>Themes</b>: " + ", ".join(str(t) for t in pulse_meta["themes"][:8]) + "\n\n"
        message += f"{briefing}\n\n"
        
        # Bullish stocks section
        bullish = [s for s, a in analysis.items() if a.get('sentiment') == 'Bullish']
        bearish = [s for s, a in analysis.items() if a.get('sentiment') == 'Bearish']
        buy_recommendations = [
            (s, a) for s, a in analysis.items()
            if a.get('buy_recommendation', '').lower() in ['buy', 'strong buy']
        ]
        
        message += "<b>🚀 BULLISH STOCKS</b>\n"
        if bullish:
            for symbol in bullish:
                sentiment_score = analysis[symbol].get('sentiment_score', 0)
                price_info = ""
                cur_price = analysis[symbol].get('current_price')
                if cur_price not in (None, 'N/A'):
                    pct = analysis[symbol].get('percent_change')
                    price_info = f" | ${cur_price}"
                    if pct not in (None, 'N/A'):
                        price_info += f" ({pct}%)"
                message += f"  • <b>{symbol}</b> (Score: {_fmt_score(sentiment_score)}{price_info})\n"
        else:
            message += "  None\n"
        
        message += "\n<b>📉 BEARISH STOCKS</b>\n"
        if bearish:
            for symbol in bearish:
                sentiment_score = analysis[symbol].get('sentiment_score', 0)
                price_info = ""
                cur_price = analysis[symbol].get('current_price')
                if cur_price not in (None, 'N/A'):
                    pct = analysis[symbol].get('percent_change')
                    price_info = f" | ${cur_price}"
                    if pct not in (None, 'N/A'):
                        price_info += f" ({pct}%)"
                message += f"  • <b>{symbol}</b> (Score: {_fmt_score(sentiment_score)}{price_info})\n"
        else:
            message += "  None\n"
        
        # Buy recommendations
        message += "\n<b>💰 STOCKS TO BUY</b>\n"
        if buy_recommendations:
            buy_recommendations.sort(key=lambda x: x[1].get('buy_score', 0), reverse=True)
            for symbol, data in buy_recommendations:
                buy_score = data.get('buy_score', 0)
                recommendation = data.get('buy_recommendation', 'Buy')
                rationale = data.get('buy_rationale', '')
                
                message += f"\n  🎯 <b>{symbol}</b>\n"
                cur_price = data.get('current_price')
                price_tag = f" | ${cur_price}" if cur_price not in (None, 'N/A') else ""
                message += f"      {recommendation} | Buy Score: {buy_score}%{price_tag}\n"
                message += f"      {rationale}\n"
        else:
            message += "  No strong buy opportunities identified.\n"
        
        # Detailed analysis
        message += "\n<b>📋 DETAILED ANALYSIS</b>\n"
        message += "=" * 40 + "\n"
        
        for symbol, data in analysis.items():
            sentiment = data.get('sentiment', 'Unknown')
            emoji = "🚀" if sentiment == "Bullish" else "📉" if sentiment == "Bearish" else "➡️"
            
            cur_price = data.get('current_price')
            pct_change = data.get('percent_change')
            price_str = ""
            if cur_price not in (None, 'N/A'):
                price_str = f" @ ${cur_price}"
                if pct_change not in (None, 'N/A'):
                    price_str += f" ({pct_change}%)"
            message += f"\n{emoji} <b>{symbol}</b>{price_str} - {sentiment}\n"
            
            if data.get('buy_rationale'):
                message += f"   {data['buy_rationale']}\n"
                if data.get('buy_score'):
                    message += f"   💡 Buy Score: {data['buy_score']}%\n"
            elif data.get('summary'):
                message += f"   {data['summary']}\n"
        
        return message
    
    def send_notification_sync(
        self,
        briefing: str,
        analysis: Dict[str, Dict],
        pulse_meta: Optional[Dict] = None,
    ) -> bool:
        """
        Synchronous wrapper for sending notifications
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                self.send_notification(briefing, analysis, pulse_meta=pulse_meta)
            )
            return result
        finally:
            loop.close()


class MessageFormatter:
    """Formats analysis into readable briefing messages"""
    
    @staticmethod
    def create_briefing(
        analysis: Dict[str, Dict],
        market_context: Dict,
        pulse_meta: Optional[Dict] = None,
    ) -> str:
        """
        Create a concise market briefing from analysis
        """
        bullish = [s for s, a in analysis.items() if a.get("sentiment") == "Bullish"]
        bearish = [s for s, a in analysis.items() if a.get("sentiment") == "Bearish"]
        buys = [
            s
            for s, a in analysis.items()
            if str(a.get("buy_recommendation", "")).lower() in ("buy", "strong buy")
        ]

        briefing = "📈 **Daily Investment Brief**\n"
        now_et = datetime.now(ZoneInfo("US/Eastern"))
        briefing += f"📅 {now_et.strftime('%A, %B %d, %Y at %I:%M %p %Z')}\n\n"

        if pulse_meta and pulse_meta.get("market_overview"):
            briefing += f"**Tape**\n{pulse_meta['market_overview'][:900]}\n\n"

        if bullish:
            briefing += f"🚀 **Bullish (from headlines)**: {', '.join(bullish)}\n"
        if bearish:
            briefing += f"⚠️ **Bearish (from headlines)**: {', '.join(bearish)}\n"
        if buys:
            briefing += f"💡 **Ideas to research**: {', '.join(buys)}\n"

        briefing += "\n*AI synthesis from live Finnhub + RSS — not financial advice.*"

        return briefing[:2000]
