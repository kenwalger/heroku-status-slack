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


def scheduled_health_check(heroku_client) -> None:
    """
    Scheduled job function for the APScheduler.
    Runs `check_app_health` for the currently monitored app.

    Args:
        heroku_client: Initialized HerokuAPIClient instance.
    """
    monitored_app = dynamic_config.get('monitored_app')
    if not monitored_app:
        logger.warning("[Scheduler] No app configured for health check")
        return
    
    logger.info(f"[Scheduler] Running scheduled check for: {monitored_app}")
    try:
        check_app_health(monitored_app, heroku_client)
    except Exception as e:
        logger.exception(f"[Scheduler] Error during health check for {monitored_app}: {e}")


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

    # CRITICAL: Check if job already exists first
    existing_job = scheduler.get_job('health_check')
    if existing_job:
        logger.info("Health check job already exists, marking as initialized")
        _scheduler_initialized = True
        return

    # Remove any existing job (extra safety)
    try:
        removed = scheduler.remove_job('health_check')
        logger.info(f"Removed existing health_check job")
    except Exception as e:
        # Job doesn't exist, which is fine
        logger.debug(f"No existing job to remove: {e}")

    # Double-check job doesn't exist before adding
    if scheduler.get_job('health_check'):
        logger.warning("Job already exists just before add_job call! This should not happen.")
        _scheduler_initialized = True
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

