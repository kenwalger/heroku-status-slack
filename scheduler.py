"""
Scheduler management for Heroku Monitoring Bot.

This module handles the APScheduler setup and management for periodic
health checks.
"""
import os
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor

from config import dynamic_config
from health_checker import check_app_health

logger = logging.getLogger(__name__)

# Scheduler globals
executors = {"default": ThreadPoolExecutor(1)}
scheduler = BackgroundScheduler(executors=executors)


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


def restart_scheduler(heroku_client) -> None:
    """
    Restart or initialize the scheduler with the current monitored app and interval.
    Removes existing job if present, updates interval, and starts scheduler if needed.

    Args:
        heroku_client: Initialized HerokuAPIClient instance.
    """
    monitored_app = dynamic_config.get('monitored_app')
    interval = dynamic_config.get('check_interval', 5)

    if not monitored_app or interval <= 0:
        logger.info("Scheduler not started: no app configured or invalid interval")
        return

    try:
        scheduler.remove_job('health_check')
        logger.info("Removed existing health_check job")
    except Exception:
        # Job doesn't exist, which is fine
        pass

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
    logger.info(f"Added health_check job with interval {interval} minutes")

    if not scheduler.running:
        scheduler.start()
        logger.info(f"Scheduler started: checking {monitored_app} every {interval} minutes")
    else:
        logger.info(f"Scheduler updated: checking {monitored_app} every {interval} minutes")


def initialize_scheduler(heroku_client) -> None:
    """
    Initialize the scheduler on the web.1 dyno.

    Args:
        heroku_client: Initialized HerokuAPIClient instance.
    """
    if os.environ.get("DYNO", "").startswith("web.1"):
        if scheduler.get_job('health_check'):
            logger.info("Health check job already registered, skipping auto-start")
        elif not scheduler.running:
            logger.info("Starting scheduler on web.1 dyno")
            restart_scheduler(heroku_client)

