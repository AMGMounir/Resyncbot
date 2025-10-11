"""
PERFORMANCE MONITOR - Optional Development Tool

This module provides real-time performance monitoring for ResyncBot during development
and production. It tracks CPU, memory, disk usage, and network connections.

WHEN TO USE:
- Debugging performance issues
- Monitoring resource usage during heavy operations
- Production deployments to track bot health
- Identifying memory leaks or bottlenecks

FEATURES:
- Automatic alerts for high CPU/memory/disk usage
- Performance summaries logged every 5 minutes
- Health status checks (Healthy/Warning/Critical)
- Non-blocking background monitoring

HOW IT WORKS:
The monitor runs in a background thread and checks system metrics every 30 seconds.
It logs warnings when resources exceed safe thresholds:
- CPU > 80%: Warning logged
- Memory > 85%: Warning logged  
- Disk > 90%: Warning logged

USAGE:
This module is imported and started automatically when DEBUG_MODE=true in your .env.
You can also manually start/stop monitoring or get current metrics.

Example:
    from performance_monitor import start_performance_monitoring, get_performance_stats
    
    # Start monitoring
    start_performance_monitoring()
    
    # Get current stats
    stats = get_performance_stats()
    print(stats['health'])  # "🟢 Healthy" / "🟡 Warning" / "🔴 Critical"

NOTE: For most local development, this is optional. It's most useful for:
- Production deployments
- Diagnosing performance issues
- Long-running bot instances
"""

import psutil
import time
import logging
from threading import Thread
import asyncio
from datetime import datetime, timedelta

logger = logging.getLogger("ResyncBot")
import psutil
import time
import logging
from threading import Thread

logger = logging.getLogger("ResyncBot")

class PerformanceMonitor:
    def __init__(self, check_interval=30):
        self.check_interval = check_interval
        self.running = False
        self.metrics = {
            'cpu_percent': 0,
            'memory_percent': 0,
            'disk_usage': 0,
            'active_connections': 0,
            'uptime': 0
        }
        
    def start_monitoring(self):
        """Start the performance monitoring in a background thread"""
        self.running = True
        monitor_thread = Thread(target=self._monitor_loop, daemon=True)
        monitor_thread.start()
        logger.info("🔍 Performance monitoring started")
        
    def stop_monitoring(self):
        """Stop the performance monitoring"""
        self.running = False
        logger.info("🛑 Performance monitoring stopped")
        
    def _monitor_loop(self):
        """Main monitoring loop"""
        start_time = time.time()
        
        while self.running:
            try:
                # CPU usage
                self.metrics['cpu_percent'] = psutil.cpu_percent(interval=1)
                
                # Memory usage
                memory = psutil.virtual_memory()
                self.metrics['memory_percent'] = memory.percent
                
                # Disk usage
                disk = psutil.disk_usage('/')
                self.metrics['disk_usage'] = (disk.used / disk.total) * 100
                
                # Network connections
                self.metrics['active_connections'] = len(psutil.net_connections())
                
                # Uptime
                self.metrics['uptime'] = time.time() - start_time
                
                # Log alerts for high usage
                if self.metrics['cpu_percent'] > 80:
                    logger.warning(f"⚠️ High CPU usage: {self.metrics['cpu_percent']:.1f}%")
                    
                if self.metrics['memory_percent'] > 85:
                    logger.warning(f"⚠️ High memory usage: {self.metrics['memory_percent']:.1f}%")
                    
                if self.metrics['disk_usage'] > 90:
                    logger.warning(f"⚠️ High disk usage: {self.metrics['disk_usage']:.1f}%")
                
                # Log performance summary every 5 minutes
                if int(self.metrics['uptime']) % 300 == 0:
                    self._log_performance_summary()
                    
            except Exception as e:
                logger.error(f"Performance monitoring error: {e}")
                
            time.sleep(self.check_interval)
    
    def _log_performance_summary(self):
        """Log a summary of current performance metrics"""
        logger.info(
            f"📊 Performance: CPU {self.metrics['cpu_percent']:.1f}% | "
            f"RAM {self.metrics['memory_percent']:.1f}% | "
            f"Disk {self.metrics['disk_usage']:.1f}% | "
            f"Connections {self.metrics['active_connections']}"
        )
    
    def get_metrics(self):
        """Get current performance metrics"""
        return self.metrics.copy()
    
    def get_health_status(self):
        """Get overall health status"""
        if (self.metrics['cpu_percent'] > 90 or 
            self.metrics['memory_percent'] > 95 or 
            self.metrics['disk_usage'] > 95):
            return "🔴 Critical"
        elif (self.metrics['cpu_percent'] > 70 or 
              self.metrics['memory_percent'] > 80 or 
              self.metrics['disk_usage'] > 85):
            return "🟡 Warning"
        else:
            return "🟢 Healthy"

# Global monitor instance
monitor = PerformanceMonitor()

def start_performance_monitoring():
    """Start global performance monitoring"""
    monitor.start_monitoring()

def get_performance_stats():
    """Get current performance statistics"""
    return {
        "metrics": monitor.get_metrics(),
        "health": monitor.get_health_status()
    }