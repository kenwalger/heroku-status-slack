"""
Health checking functionality for Heroku Monitoring Bot.

This module provides health check functions to monitor dynos, releases,
and configuration changes for Heroku apps.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Dict, List
import psycopg2
import psycopg2.extras

from config import DATABASE_URL, dynamic_config
from database import load_app_state, save_app_state
from slack_integration import send_slack_message

logger = logging.getLogger(__name__)


def check_dyno_health(app_name: str, dynos: List[dict], state: dict) -> None:
    """
    Compare current dynos to previous state and send Slack alerts for crashes or downtime.

    Args:
        app_name (str): Heroku app name.
        dynos (List[dict]): Current dyno info from Heroku API.
        state (dict): Monitoring state dict; will be updated in-place.

    Returns:
        None
    """
    last_dynos = state.get('dynos', {})

    for dyno in dynos:
        name = dyno.get('name')
        status = dyno.get('state')

        if last_dynos.get(name) and last_dynos[name] != status:
            if status.lower() == 'crashed':
                send_slack_message(f"🚨 *Dyno Crash Detected* 🚨\nApp: `{app_name}`\n• {name} ({dyno.get('type')})")
            elif status.lower() == 'down':
                send_slack_message(f"⚠️ *Dynos Down* ⚠️\nApp: `{app_name}`\n• {name} ({dyno.get('type')})")

    # Update state for next check
    state['dynos'] = {d['name']: d['state'] for d in dynos}


def check_recent_releases(app_name: str, releases: List[dict], state: dict) -> None:
    """
    Check for new releases and send Slack alerts if a new deploy is detected.

    Args:
        app_name (str): Heroku app name.
        releases (List[dict]): List of recent release dictionaries.
        state (dict): Monitoring state dict; will be updated in-place.

    Returns:
        None
    """
    if not releases:
        return

    latest = releases[0]
    release_version = str(latest.get('version'))
    last_known = state.get('last_release')

    if last_known is not None and last_known != release_version:
        user_email = latest.get('user', {}).get('email', 'Unknown')
        created_at = latest.get('created_at', '')
        description = latest.get('description', 'No description')

        noticed_at = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')

        send_slack_message(
            f"🚀 *New Deploy Detected at {noticed_at}* 🚀\n\n"
            f"App: `{app_name}`\n"
            f"Version: v{release_version}\n"
            f"Deployed by: {user_email}\n"
            f"Description: {description}\n"
            f"Time: {created_at}\n\n"
            "_Monitoring for issues..._"
        )

    state['last_release'] = release_version


def check_config_changes(app_name: str, config_vars: dict, state: dict) -> None:
    """
    Detect changes to config vars and send Slack alerts.

    Args:
        app_name (str): Heroku app name.
        config_vars (dict): Current config vars.
        state (dict): Monitoring state dict; will be updated in-place.

    Returns:
        None
    """
    last_hash = None
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT config_vars_hash FROM app_state WHERE app_name = %s", (app_name,))
            row = cur.fetchone()
            last_hash = row['config_vars_hash'] if row else None
    except Exception as e:
        logger.error(f"Error loading config state for {app_name}: {e}")
        last_hash = state.get('config_vars_hash')
    finally:
        if conn:
            conn.close()

    # Compute hash of current config
    config_json = json.dumps(config_vars, sort_keys=True)
    config_hash = hashlib.sha256(config_json.encode('utf-8')).hexdigest()

    # Send Slack alert if config changed
    if last_hash and last_hash != config_hash:
        send_slack_message(
            f"⚙️ *Config Vars Changed at {datetime.now(timezone.utc).isoformat()}* ⚙️\n"
            f"App: `{app_name}`\nReview changes in Heroku dashboard."
        )

    # Update in-memory state
    state['config_vars_hash'] = config_hash
    state['updated_at'] = datetime.now(timezone.utc).isoformat()

    # Persist state to DB
    try:
        conn = psycopg2.connect(DATABASE_URL)
        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
                cur.execute("""
                    INSERT INTO app_state (app_name, config_vars_hash, updated_at)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (app_name) DO UPDATE
                    SET config_vars_hash = EXCLUDED.config_vars_hash,
                        updated_at = EXCLUDED.updated_at
                """, (app_name, config_hash, state['updated_at']))
        # conn automatically committed by 'with conn:'
    except Exception as e:
        # Log error but don't crash scheduler
        logger.error(f"Error persisting config state for {app_name}: {e}")
    finally:
        if conn:
            conn.close()


def check_app_health(app_name: str, heroku_client) -> None:
    """
    Orchestrate the health check: dynos, releases, and config vars.

    Args:
        app_name (str): Heroku app name.
        heroku_client: Initialized HerokuAPIClient instance.

    Returns:
        None
    """
    if not heroku_client:
        logger.warning("Heroku client not configured")
        return

    logger.info(f"[Health] Checking health for {app_name}")

    state = load_app_state(app_name) or {}
    logger.info(f"[Health] Loaded state from DB: {state}")

    dynos = heroku_client.get_dynos(app_name)
    releases = heroku_client.get_releases(app_name, limit=3)
    config_vars = heroku_client.get_config_vars(app_name)

    logger.info(f"[Health] Dynos: {[d['name'] + ':' + d['state'] for d in dynos]}")
    logger.info(f"[Health] Releases: {releases[-1]['description']} (v{releases[-1]['version']})")
    logger.info(f"[Health] Config vars: {config_vars}")

    if dynos:
        check_dyno_health(app_name, dynos, state)
    if releases:
        releases_sorted = sorted(releases, key=lambda r: r['version'], reverse=True)
        check_recent_releases(app_name, releases_sorted, state)
    if config_vars:
        check_config_changes(app_name, config_vars, state)

    logger.info(f"[DB] About to save state: {state}")
    save_app_state(app_name, state)

