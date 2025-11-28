import time
from contextlib import contextmanager
from typing import Dict, Optional
from datetime import datetime
from collections import defaultdict
from core.logger import get_logger

logger = get_logger(__name__)


class PerformanceMonitor:
    """Track and report performance metrics"""
    
    def __init__(self):
        self.metrics: Dict[str, list] = defaultdict(list)
        self.success_counts: Dict[str, int] = defaultdict(int)
        self.failure_counts: Dict[str, int] = defaultdict(int)
    
    @contextmanager
    def measure(self, operation_name: str, context: Optional[Dict] = None):
        """
        Context manager to measure operation duration.
        
        Usage:
            with monitor.measure("scraping", {"department": "CSE"}):
                # do scraping work
                pass
        """
        start_time = time.time()
        error = None
        
        try:
            yield
            self.success_counts[operation_name] += 1
        except Exception as e:
            error = e
            self.failure_counts[operation_name] += 1
            raise
        finally:
            duration = time.time() - start_time
            duration_ms = duration * 1000
            
            # Record metric
            self.metrics[operation_name].append({
                "duration": duration,
                "duration_ms": duration_ms,
                "timestamp": datetime.now(),
                "context": context or {},
                "success": error is None
            })
            
            # Log with performance context
            if error:
                logger.error(
                    f"{operation_name} failed",
                    duration=duration,
                    context=context or {},
                    exc_info=True
                )
            else:
                logger.info(
                    f"{operation_name} completed",
                    duration_ms=duration_ms,
                    context=context or {}
                )
    
    def get_stats(self, operation_name: str) -> Dict:
        """Get statistics for a specific operation"""
        if operation_name not in self.metrics:
            return {}
        
        metrics = self.metrics[operation_name]
        durations = [m["duration"] for m in metrics]
        
        if not durations:
            return {}
        
        return {
            "operation": operation_name,
            "count": len(durations),
            "success_count": self.success_counts[operation_name],
            "failure_count": self.failure_counts[operation_name],
            "success_rate": self.success_counts[operation_name] / len(durations) * 100 if durations else 0,
            "avg_duration_ms": sum(durations) * 1000 / len(durations),
            "min_duration_ms": min(durations) * 1000,
            "max_duration_ms": max(durations) * 1000,
            "total_duration_s": sum(durations)
        }
    
    def get_all_stats(self) -> Dict[str, Dict]:
        """Get statistics for all operations"""
        return {
            op_name: self.get_stats(op_name)
            for op_name in self.metrics.keys()
        }
    
    def log_summary(self):
        """Log performance summary for all operations"""
        all_stats = self.get_all_stats()
        
        if not all_stats:
            logger.info("No performance metrics collected yet")
            return
        
        logger.info("=" * 60)
        logger.info("PERFORMANCE SUMMARY")
        logger.info("=" * 60)
        
        for op_name, stats in all_stats.items():
            logger.info(
                f"{op_name}: "
                f"{stats['count']} runs, "
                f"{stats['success_rate']:.1f}% success, "
                f"avg {stats['avg_duration_ms']:.0f}ms "
                f"(min {stats['min_duration_ms']:.0f}ms, max {stats['max_duration_ms']:.0f}ms)"
            )
        
        logger.info("=" * 60)
    
    def reset(self):
        """Reset all metrics"""
        self.metrics.clear()
        self.success_counts.clear()
        self.failure_counts.clear()
        logger.info("Performance metrics reset")


# Global instance
_performance_monitor = None

def get_performance_monitor() -> PerformanceMonitor:
    """Get singleton performance monitor instance"""
    global _performance_monitor
    if _performance_monitor is None:
        _performance_monitor = PerformanceMonitor()
    return _performance_monitor
