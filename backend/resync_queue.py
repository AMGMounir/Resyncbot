"""
DUAL QUEUE SYSTEM - Smart Job Distribution for Premium Users

This module implements a sophisticated dual-queue system that provides faster
processing for premium users while maintaining fair service for all users.

Since premium is disabled in production at the moment, priority queues are disabled.

HOW IT WORKS:
=============
The system maintains two separate job queues, each with its own worker pool:

1. **Regular Queue** (Free + Premium overflow)
   - Processes jobs from free users
   - Also handles premium users when priority queue is longer
   - Default queue for all non-premium users

2. **Priority Queue** (Premium users)
   - Dedicated workers for premium users only
   - Premium users automatically routed here
   - Provides faster processing during high load
"""
import asyncio
import logging
from dataclasses import dataclass
from typing import Callable
from config import Config
from premium_utils import premium_manager
import time
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("ResyncBot")
from dataclasses import dataclass, field

@dataclass
class QueuedJob:
    """A job in the queue"""
    job_func: Callable
    user_id: int
    job_id: str
    is_premium: bool
    queued_at: float = field(default_factory=time.time)

class DualQueueSystem:
    """Dual queue system with regular and priority pools"""
    
    def __init__(self):
        # Separate queues
        self.regular_queue = asyncio.Queue()
        self.priority_queue = asyncio.Queue()
        
        # Worker pools
        self.regular_workers = []
        self.priority_workers = []
        
        # Active job tracking
        self.active_regular_jobs = {}  # worker_id -> job_info
        self.active_priority_jobs = {}  # worker_id -> job_info

    async def put(self, job_func: Callable, user_id: int, job_id: str = None):
        """Add a job to the appropriate queue"""
        is_premium = premium_manager.is_premium_user(user_id)
        
        queued_job = QueuedJob(
            job_func=job_func,
            user_id=user_id,
            job_id=job_id or f"job_{user_id}_{asyncio.get_event_loop().time()}",
            is_premium=is_premium
        )
        
        if is_premium:
            # Premium users: choose the shorter queue
            regular_size = self.regular_queue.qsize()
            priority_size = self.priority_queue.qsize()
            
            if priority_size <= regular_size:
                await self.priority_queue.put(queued_job)
                logger.info(f"🌟 Premium user {user_id} → Priority queue (size: {priority_size + 1})")
            else:
                await self.regular_queue.put(queued_job)
                logger.info(f"🌟 Premium user {user_id} → Regular queue (shorter, size: {regular_size + 1})")
        else:
            # Free users: always go to regular queue
            await self.regular_queue.put(queued_job)
            regular_size = self.regular_queue.qsize()
            logger.info(f"👤 Free user {user_id} → Regular queue (size: {regular_size})")
    
    async def start_workers(self):
        """Start both worker pools"""
        logger.info(f"🚀 Starting dual queue system:")
        logger.info(f"   • {Config.NUM_WORKERS} regular workers")
        logger.info(f"   • {Config.NUM_WORKERS} priority workers")
        
        # Start regular workers
        for i in range(Config.NUM_WORKERS):
            worker = asyncio.create_task(self._regular_worker(i + 1))
            self.regular_workers.append(worker)
        
        # Start priority workers  
        for i in range(Config.NUM_WORKERS):
            worker = asyncio.create_task(self._priority_worker(i + 1))
            self.priority_workers.append(worker)
    
    async def _regular_worker(self, worker_id: int):
        """Worker for the regular queue"""
        logger.info(f"🔷 Regular Worker {worker_id} started")
        
        while True:
            queued_job = None
            try:
                queued_job = await self.regular_queue.get()
                user_type = "Premium" if queued_job.is_premium else "Free"
                
                # Track active job
                self.active_regular_jobs[worker_id] = {
                    "job_id": queued_job.job_id,
                    "user_id": queued_job.user_id,
                    "is_premium": queued_job.is_premium,
                    "started_at": time.time(),
                    "queued_at": queued_job.queued_at
                }
                
                logger.info(f"🔷 Regular Worker {worker_id} processing {user_type} job for user {queued_job.user_id}")
                
                await queued_job.job_func()
                logger.info(f"✅ Regular Worker {worker_id} finished {user_type} job")
                
            except asyncio.CancelledError:
                logger.info(f"🛑 Regular Worker {worker_id} cancelled")
                if queued_job:
                    self.regular_queue.task_done()
                break
                
            except Exception as e:
                logger.error(f"❌ Regular Worker {worker_id} job failed: {e}")
                
            finally:
                # Remove from active jobs tracking
                if worker_id in self.active_regular_jobs:
                    del self.active_regular_jobs[worker_id]
                    
                if queued_job:
                    self.regular_queue.task_done()
    
    async def _priority_worker(self, worker_id: int):
        """Worker for the priority queue"""
        logger.info(f"🌟 Priority Worker {worker_id} started")
        
        while True:
            queued_job = None
            try:
                queued_job = await self.priority_queue.get()
                
                # Track active job
                self.active_priority_jobs[worker_id] = {
                    "job_id": queued_job.job_id,
                    "user_id": queued_job.user_id,
                    "is_premium": queued_job.is_premium,
                    "started_at": time.time(),
                    "queued_at": queued_job.queued_at
                }
                
                logger.info(f"🌟 Priority Worker {worker_id} processing Premium job for user {queued_job.user_id}")
                
                await queued_job.job_func()
                logger.info(f"✅ Priority Worker {worker_id} finished Premium job")
                
            except asyncio.CancelledError:
                logger.info(f"🛑 Priority Worker {worker_id} cancelled")
                if queued_job:
                    self.priority_queue.task_done()
                break
                
            except Exception as e:
                logger.error(f"❌ Priority Worker {worker_id} job failed: {e}")
                
            finally:
                # Remove from active jobs tracking
                if worker_id in self.active_priority_jobs:
                    del self.active_priority_jobs[worker_id]
                    
                if queued_job:
                    self.priority_queue.task_done()
    
    def get_queue_stats(self) -> dict:
        """Get detailed queue statistics including active jobs"""
        return {
            'regular_queue_size': self.regular_queue.qsize(),
            'priority_queue_size': self.priority_queue.qsize(),
            'regular_active_jobs': len(self.active_regular_jobs),
            'priority_active_jobs': len(self.active_priority_jobs),
            'total_queued': self.regular_queue.qsize() + self.priority_queue.qsize(),
            'total_active': len(self.active_regular_jobs) + len(self.active_priority_jobs),
            'regular_workers': len(self.regular_workers),
            'priority_workers': len(self.priority_workers),
            'active_regular_jobs': self.active_regular_jobs,
            'active_priority_jobs': self.active_priority_jobs
        }
    
    def qsize(self) -> int:
        """Get total queue size"""
        return self.regular_queue.qsize() + self.priority_queue.qsize()

# Global queue system
job_queue = DualQueueSystem()

async def start_worker_pool():
    """Start the dual worker pool system"""
    await job_queue.start_workers()

def get_queue_size() -> int:
    """Get current total queue size"""
    return job_queue.qsize()

def get_queue_stats() -> dict:
    """Get detailed queue statistics"""
    return job_queue.get_queue_stats()