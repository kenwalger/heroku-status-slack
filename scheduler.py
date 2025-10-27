"""
Scheduler management for Heroku Monitoring Bot.

This module handles the APScheduler setup and management for periodic
health checks.
"""
import os
import logging
import threading
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor

from config import dynamic_config
from health_checker import check_app_health

logger = logging.getLogger(__name__)

# Scheduler globals
executors = {"default": ThreadPoolExecutor(1)}
scheduler = BackgroundScheduler(executors=executors)

# Lock to prevent duplicate job registration
_scheduler_lock = threading.Lock()
_scheduler_initialized = False

# Execution lock to prevent concurrent job execution
_execution_lock = threading.Lock()
_job_running = False


def scheduled_health_check(heroku_client) -> None:
    """
    Scheduled job function for the APScheduler.
    Runs `check_app_health` for the currently monitored app.

    Args:
        heroku_client: Initialized HerokuAPIClient instance.
    """
    global _job_running
    
    monitored_app = dynamic_config.get('monitored_app')
    if not monitored_app:
        logger.warning("[Scheduler] No app configured for health check")
        return
    
    # Prevent concurrent execution if duplicate jobs somehow exist
    # Check and set flag atomically
    with _execution_lock:
        if _job_running:
            logger.warning("[Scheduler] Health check already running, skipping duplicate job execution")
            return
        # Set flag WHILE holding the lock
        _job_running = True
    
    try:
        logger.info(f"[Scheduler] Running scheduled check for: {monitored_app}")
        check_app_health(monitored_app, heroku_client)
    except Exception as e:
        logger.exception(f"[Scheduler] Error during health check for {monitored_app}: {e}")
    finally:
        with _execution_lock:
            _job_running = False


def _restart_scheduler_impl(heroku_client) -> None:
    """
    Internal implementation of restart_scheduler without lock acquisition.
    Call restart_scheduler() instead.
    """
    global _scheduler_initialized
    
    # Abort if already initialized with a job
    if _scheduler_initialized:
        logger.info("Scheduler already initialized, skipping restart")
        return
    
    monitored_app = dynamic_config.get('monitored_app')
    interval = dynamic_config.get('check_interval', 5)

    if not monitored_app or interval <= 0:
        logger.info("Scheduler not started: no app configured or invalid interval")
        return

    # AGGRESSIVE: Remove ALL health_check jobs to prevent duplicates
    all_jobs = scheduler.get_jobs()
    for job in all_jobs:
        if job.id == 'health_check':
            try:
                scheduler.remove_job('health_check')
                logger.info(f"Removed existing health_check job before registration")
            except Exception as e:
                logger.warning(f"Failed to remove job: {e}")

    # Triple-check: verify no job exists now
    if scheduler.get_job('health_check'):
        logger.error("ERROR: Job still exists after removal attempt!")
        return
    
    # Add job - using replace_existing=True as defense in depth
    scheduler.add_job(
        func=lambda: scheduled_health_check(heroku_client),
        trigger="interval",
        minutes=interval,
        id='health_check',
        name='Periodic health check',
        replace_existing=True,
        max_instances=1,
        coalesce=True
    )
    
    # Verify it was added successfully
    added_job = scheduler.get_job('health_check')
    if not added_job:
        logger.error("ERROR: Job was not added successfully!")
        return
        
    logger.info(f"Registered health_check job with interval {interval} minutes")
    
    # Verify only one job exists
    jobs = scheduler.get_jobs()
    health_check_jobs = [j for j in jobs if j.id == 'health_check']
    if len(health_check_jobs) > 1:
        logger.error(f"ERROR: Found {len(health_check_jobs)} health_check jobs! This should never happen.")
        # Remove all but the first one
        for job in health_check_jobs[1:]:
            logger.error(f"Removing duplicate job: {job}")
            scheduler.remove_job(job.id)
    else:
        logger.info(f"Verified: {len(health_check_jobs)} health_check job(s) exist")

    # Start scheduler if not running
    if not scheduler.running:
        scheduler.start()
        logger.info(f"Scheduler started: checking {monitored_app} every {interval} minutes")
    else:
        logger.info(f"Scheduler job updated: checking {monitored_app} every {interval} minutes")
    
    # Mark as initialized ONLY after successfully registering job
    _scheduler_initialized = True


def restart_scheduler(heroku_client) -> None:
    """
    Restart or initialize the scheduler with the current monitored app and interval.
    Removes existing job if present, updates interval, and starts scheduler if needed.

    Args:
        heroku_client: Initialized HerokuAPIClient instance.
    """
    global _scheduler_initialized
    
    # Use lock to prevent concurrent registration
    with _scheduler_lock:
        # Reset flag to allow restart
        _scheduler_initialized = False
        _restart_scheduler_impl(heroku_client)


def initialize_scheduler(heroku_client) -> None:
    """
    Initialize the scheduler on the web.1 dyno.

    Args:
        heroku_client: Initialized HerokuAPIClient instance.
    """
    global _scheduler_initialized
    
    if os.environ.get("DYNO", "").startswith("web.1"):
        logger.info(f"[INIT] initialize_scheduler called (initialized={_scheduler_initialized})")
        
        # Use lock to prevent concurrent initialization
        with _scheduler_lock:
            logger.info("[INIT] Acquired lock")
            
            if _scheduler_initialized:
                logger.info("[INIT] Scheduler already initialized, skipping auto-start")
                return
            
            existing_job = scheduler.get_job('health_check')
            if existing_job:
                logger.info("[INIT] Health check job already registered, marking as initialized")
                _scheduler_initialized = True
                return
            
            if scheduler.running:
                logger.info("[INIT] Scheduler already running, marking as initialized")
                _scheduler_initialized = True
                return
            
            logger.info("[INIT] Starting scheduler on web.1 dyno")
            _restart_scheduler_impl(heroku_client)
            logger.info(f"[INIT] Completed initialization (initialized={_scheduler_initialized})")

