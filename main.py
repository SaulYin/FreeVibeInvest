"""
Main Orchestration Script
Coordinates all phases of the investment research pipeline
"""
import logging
import sys
import time
from datetime import datetime
from scraper import FinancialNewsScraper, get_market_context
from analyzer import SentimentAnalyzer
from notifier import NotificationManager, MessageFormatter
from monitoring import HealthMonitor, PerformanceTracker
from config import config

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def run_pipeline():
    monitor = HealthMonitor()
    performance = PerformanceTracker()
    monitor.start_execution()

    articles_scraped = 0
    notifications_sent = False
    pulse_meta = None

    try:
        logger.info("=" * 60)
        logger.info("Starting Investment Research Pipeline")
        logger.info("Analysis mode: %s", config.ANALYSIS_MODE)
        logger.info("Time: %s", datetime.now().isoformat())
        logger.info("=" * 60)

        config.validate()
        logger.info("✓ Configuration validated")

        logger.info("\n[PHASE 1] Data ingestion — live headlines...")
        start_phase1 = time.time()
        scraper = FinancialNewsScraper()
        # ETF proxy quotes first — then Finnhub market/company news — keeps provider traffic predictable.
        index_ctx: dict = {}
        if config.MP_BENCHMARK_SYMBOLS:
            index_ctx = get_market_context(list(config.MP_BENCHMARK_SYMBOLS))

        scraped_data = scraper.scrape_all()
        articles_scraped = len(scraped_data["news"])

        phase1_duration = time.time() - start_phase1
        performance.record_timing("data_ingestion", phase1_duration)
        logger.info(
            "✓ Collected %d headline items in %.2fs",
            articles_scraped,
            phase1_duration,
        )

        if not scraped_data["news"]:
            logger.warning("⚠ No headlines found. LLM output will be thin.")

        logger.info("\n[PHASE 2] AI analysis...")
        start_phase2 = time.time()
        analyzer = SentimentAnalyzer()

        pulse_report = analyzer.analyze_market_pulse(scraped_data["news"], index_ctx)
        pulse_meta = {
            "market_overview": pulse_report.get("market_overview", ""),
            "themes": pulse_report.get("themes") or [],
        }
        if pulse_report.get("error"):
            logger.error("✗ Analysis failed (%s) — skipping notification", pulse_report["error"])
            phase2_duration = time.time() - start_phase2
            performance.record_timing("ai_analysis", phase2_duration)
            monitor.log_error(f"Analysis failed: {pulse_report['error']}")
            monitor.end_execution(articles_scraped, 0, False)
            return 1

        analysis_results = analyzer.market_pulse_report_to_stock_analysis(pulse_report)
        if pulse_meta.get("market_overview"):
            logger.info("\n%s", pulse_meta["market_overview"])
        phase2_duration = time.time() - start_phase2
        performance.record_timing("ai_analysis", phase2_duration)
        logger.info(
            "✓ Market pulse: %d tickers in synthesized view (%.2fs)",
            len(analysis_results),
            phase2_duration,
        )

        executive_summary = analyzer.generate_executive_summary(analysis_results)
        logger.info("\n%s", executive_summary)

        buy_opportunities = analyzer.get_top_buy_opportunities(analysis_results, limit=5)
        if buy_opportunities:
            logger.info("\n🎯 TOP BUY OPPORTUNITIES:")
            for i, opp in enumerate(buy_opportunities, 1):
                logger.info(
                    "   %d. %s: %s (Score: %s%%)",
                    i,
                    opp["symbol"],
                    opp["buy_recommendation"],
                    opp["buy_score"],
                )
                logger.info("      %s", opp["buy_rationale"])

        briefing = MessageFormatter.create_briefing(
            analysis_results, index_ctx, pulse_meta=pulse_meta
        )

        logger.info("\n[PHASE 3] Delivery...")
        start_phase3 = time.time()
        notifier = NotificationManager()
        success = notifier.send_notification_sync(briefing, analysis_results, pulse_meta=pulse_meta)
        notifications_sent = success
        phase3_duration = time.time() - start_phase3
        performance.record_timing("notification_delivery", phase3_duration)

        if success:
            logger.info("✓ Notifications sent in %.2fs", phase3_duration)
        else:
            logger.warning("⚠ Notification delivery failed")
            monitor.log_error("Failed to send notifications")

        logger.info("\n[PHASE 4] Logging...")
        _log_execution_summary(analysis_results, scraped_data, performance)

        logger.info("\n" + "=" * 60)
        logger.info("Pipeline completed successfully")
        logger.info("=" * 60)

        metrics = monitor.end_execution(articles_scraped, len(analysis_results), notifications_sent)
        logger.debug("Execution metrics: %s", metrics)

        return 0

    except ValueError as e:
        logger.error("Configuration error: %s", e)
        monitor.log_error(f"Configuration error: {e}")
        monitor.end_execution(articles_scraped, 0, notifications_sent)
        return 1

    except Exception as e:
        logger.error("Pipeline error: %s", e, exc_info=True)
        monitor.log_error(f"Unexpected error: {e}")
        monitor.end_execution(articles_scraped, 0, notifications_sent)
        return 1


def _log_execution_summary(analysis_results, scraped_data, performance):
    summary = {
        "timestamp": datetime.now().isoformat(),
        "stocks_analyzed": list(analysis_results.keys()),
        "articles_processed": len(scraped_data["news"]),
        "sentiment_distribution": {
            "bullish": len([s for s, a in analysis_results.items() if a.get("sentiment") == "Bullish"]),
            "bearish": len([s for s, a in analysis_results.items() if a.get("sentiment") == "Bearish"]),
            "neutral": len([s for s, a in analysis_results.items() if a.get("sentiment") == "Neutral"]),
        },
        "performance_metrics": performance.get_all_stats(),
    }
    logger.debug("Execution Summary: %s", summary)


if __name__ == "__main__":
    sys.exit(run_pipeline())
