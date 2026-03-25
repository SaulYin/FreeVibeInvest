"""
Monitoring and Health Check Module
Tracks pipeline health, errors, and performance
"""
import logging
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from dataclasses import dataclass, asdict
from enum import Enum

logger = logging.getLogger(__name__)


class HealthStatus(Enum):
    """Pipeline health status levels"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"


@dataclass
class ExecutionMetrics:
    """Tracks execution metrics for a pipeline run"""
    timestamp: str
    duration_seconds: float
    articles_scraped: int
    stocks_analyzed: int
    notifications_sent: bool
    errors: List[str]
    status: str


class HealthMonitor:
    """Monitors pipeline health and performance"""
    
    def __init__(self, metrics_file: str = "metrics/pipeline_metrics.jsonl"):
        self.metrics_file = Path(metrics_file)
        self.metrics_file.parent.mkdir(parents=True, exist_ok=True)
        self.start_time = None
        self.errors = []
    
    def start_execution(self):
        """Mark start of pipeline execution"""
        self.start_time = time.time()
        self.errors = []
        logger.info("Pipeline execution started")
    
    def log_error(self, error: str):
        """Log an error during execution"""
        self.errors.append(error)
        logger.error(f"Pipeline error: {error}")
    
    def end_execution(self, 
                     articles_scraped: int, 
                     stocks_analyzed: int,
                     notifications_sent: bool) -> ExecutionMetrics:
        """Mark end of pipeline execution and save metrics"""
        
        if not self.start_time:
            raise RuntimeError("Execution not started")
        
        duration = time.time() - self.start_time
        
        # Determine health status
        if self.errors:
            status = HealthStatus.DEGRADED.value if notifications_sent else HealthStatus.FAILED.value
        else:
            status = HealthStatus.HEALTHY.value
        
        metrics = ExecutionMetrics(
            timestamp=datetime.now().isoformat(),
            duration_seconds=round(duration, 2),
            articles_scraped=articles_scraped,
            stocks_analyzed=stocks_analyzed,
            notifications_sent=notifications_sent,
            errors=self.errors,
            status=status
        )
        
        # Save metrics
        self._save_metrics(metrics)
        
        logger.info(f"Pipeline execution completed in {duration:.2f}s - Status: {status}")
        return metrics
    
    def _save_metrics(self, metrics: ExecutionMetrics):
        """Save execution metrics to file"""
        try:
            with open(self.metrics_file, 'a') as f:
                f.write(json.dumps(asdict(metrics)) + '\n')
        except Exception as e:
            logger.error(f"Failed to save metrics: {e}")
    
    def get_health_status(self, hours: int = 24) -> Dict:
        """Get overall health status based on recent executions"""
        try:
            recent_metrics = self._get_recent_metrics(hours)
            
            if not recent_metrics:
                return {
                    'status': HealthStatus.DEGRADED.value,
                    'reason': 'No recent executions found',
                    'last_check': datetime.now().isoformat()
                }
            
            # Calculate health metrics
            total_runs = len(recent_metrics)
            successful_runs = len([m for m in recent_metrics if m['status'] == HealthStatus.HEALTHY.value])
            success_rate = (successful_runs / total_runs * 100) if total_runs > 0 else 0
            
            avg_duration = sum(m['duration_seconds'] for m in recent_metrics) / total_runs if total_runs > 0 else 0
            
            # Determine overall status
            if success_rate >= 80:
                status = HealthStatus.HEALTHY.value
            elif success_rate >= 50:
                status = HealthStatus.DEGRADED.value
            else:
                status = HealthStatus.FAILED.value
            
            return {
                'status': status,
                'success_rate': f"{success_rate:.1f}%",
                'total_runs': total_runs,
                'successful_runs': successful_runs,
                'average_duration_seconds': round(avg_duration, 2),
                'last_check': datetime.now().isoformat(),
                'recent_errors': self._get_recent_errors(recent_metrics, limit=3)
            }
        
        except Exception as e:
            logger.error(f"Error getting health status: {e}")
            return {
                'status': HealthStatus.DEGRADED.value,
                'error': str(e)
            }
    
    def _get_recent_metrics(self, hours: int = 24) -> List[Dict]:
        """Get metrics from the last N hours"""
        if not self.metrics_file.exists():
            return []
        
        cutoff_time = datetime.now() - timedelta(hours=hours)
        recent = []
        
        try:
            with open(self.metrics_file, 'r') as f:
                for line in f:
                    try:
                        data = json.loads(line)
                        exec_time = datetime.fromisoformat(data['timestamp'])
                        if exec_time > cutoff_time:
                            recent.append(data)
                    except (json.JSONDecodeError, ValueError):
                        continue
        
        except Exception as e:
            logger.error(f"Error reading metrics: {e}")
        
        return recent
    
    def _get_recent_errors(self, metrics: List[Dict], limit: int = 3) -> List[str]:
        """Extract recent errors from metrics"""
        errors = []
        for metric in metrics[:limit]:
            errors.extend(metric.get('errors', []))
        return errors[:limit]
    
    def generate_report(self, days: int = 7) -> str:
        """Generate a health report for the last N days"""
        health = self.get_health_status(hours=days*24)
        
        report = f"""
=== Investment Research Pipeline Health Report ===
Generated: {datetime.now().isoformat()}
Period: Last {days} days

Status: {health.get('status', 'Unknown').upper()}
Success Rate: {health.get('success_rate', 'N/A')}
Total Runs: {health.get('total_runs', 0)}
Successful Runs: {health.get('successful_runs', 0)}
Average Duration: {health.get('average_duration_seconds', 0)}s

Recent Errors:
"""
        
        if health.get('recent_errors'):
            for error in health['recent_errors']:
                report += f"  - {error}\n"
        else:
            report += "  None\n"
        
        return report


class PerformanceTracker:
    """Tracks and analyzes performance over time"""
    
    def __init__(self):
        self.timings = {}
    
    def record_timing(self, component: str, duration: float):
        """Record timing for a component"""
        if component not in self.timings:
            self.timings[component] = []
        self.timings[component].append(duration)
    
    def get_stats(self, component: str) -> Dict:
        """Get statistics for a component"""
        if component not in self.timings or not self.timings[component]:
            return {}
        
        timings = self.timings[component]
        return {
            'count': len(timings),
            'min': min(timings),
            'max': max(timings),
            'avg': sum(timings) / len(timings),
            'total': sum(timings)
        }
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """Get statistics for all components"""
        return {component: self.get_stats(component) for component in self.timings}


class ErrorHandler:
    """Centralized error handling and recovery"""
    
    RECOVERABLE_ERRORS = [
        'timeout',
        'connection',
        'temporarily',
        'rate limit',
        'unavailable',
    ]
    
    CRITICAL_ERRORS = [
        'api key',
        'authentication',
        'permission denied',
    ]
    
    @staticmethod
    def is_recoverable(error: str) -> bool:
        """Determine if error is recoverable"""
        error_lower = error.lower()
        return any(keyword in error_lower for keyword in ErrorHandler.RECOVERABLE_ERRORS)
    
    @staticmethod
    def is_critical(error: str) -> bool:
        """Determine if error is critical"""
        error_lower = error.lower()
        return any(keyword in error_lower for keyword in ErrorHandler.CRITICAL_ERRORS)
    
    @staticmethod
    def should_retry(error: str, retry_count: int = 0, max_retries: int = 3) -> bool:
        """Determine if operation should be retried"""
        if retry_count >= max_retries:
            return False
        return ErrorHandler.is_recoverable(error)
