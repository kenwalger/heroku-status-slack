"""
Database operations for Heroku Monitoring Bot.

This module handles all database connections and state management for tracking
app monitoring state.
"""
import os
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any
import psycopg2
import psycopg2.extras
from psycopg2.extensions import connection as DBConnection

from config import DATABASE_URL

logger = logging.getLogger(__name__)


def get_db_connection() -> DBConnection:
    """
    Create and return a new database connection to the configured Postgres database.

    Returns:
        psycopg2.extensions.connection: Active database connection.
    """
    return psycopg2.connect(DATABASE_URL, sslmode='require')


def load_app_state(app_name: str) -> Dict[str, Any]:
    """
    Load the persisted monitoring state for a given Heroku app.

    Args:
        app_name (str): Name of the Heroku app.

    Returns:
        Dict[str, Any]: Dictionary containing:
            - 'last_release': str | None
            - 'dynos': dict[str, str]  # dyno name -> state
            - 'config_vars_hash': int | None
            - 'updated_at': datetime | None
    """
    state = {
        'last_release': None,
        'dynos': {},
        'config_vars_hash': None,
        'updated_at': None
    }

    conn = psycopg2.connect(dsn=os.environ['DATABASE_URL'])
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.DictCursor) as cur:
            cur.execute("SELECT * FROM app_state WHERE app_name = %s", (app_name,))
            row = cur.fetchone()
            if row:
                state['last_release'] = row['last_release']
                # dynos stored as JSONB in Postgres; if already a dict, just use it
                dynos = row['dynos']
                if isinstance(dynos, str):
                    state['dynos'] = json.loads(dynos)
                elif isinstance(dynos, dict):
                    state['dynos'] = dynos
                else:
                    state['dynos'] = {}
                state['config_vars_hash'] = row['config_vars_hash']
                state['updated_at'] = row['updated_at']
            else:
                state['config_vars_hash'] = None
                state['dynos'] = {}
                state['last_release'] = None
                state['updated_at'] = None
    finally:
        conn.close()

    return state


def save_app_state(app_name: str, state: Dict[str, Any]) -> None:
    """
    Persist monitoring state for a Heroku app to Postgres.

    Args:
        app_name (str): Name of the app.
        state (Dict[str, Any]): State dict to persist. Expected keys:
            'last_release', 'dynos', 'config_vars_hash', 'updated_at'.

    Returns:
        None
    """
    conn = None
    try:
        conn = psycopg2.connect(dsn=os.environ['DATABASE_URL'])
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO app_state (app_name, last_release, dynos, config_vars_hash, updated_at)
                VALUES (%s, %s, %s::jsonb, %s, %s)
                ON CONFLICT (app_name) DO UPDATE
                SET last_release = EXCLUDED.last_release,
                    dynos = EXCLUDED.dynos,
                    config_vars_hash = EXCLUDED.config_vars_hash,
                    updated_at = EXCLUDED.updated_at
            """, (
                app_name,
                state.get('last_release') or '',
                json.dumps(state.get('dynos', {})),  # ensure we always save JSON string
                state.get('config_vars_hash'),
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
            logger.info(f"[DB] Successfully committed state for {app_name}")
    except Exception as e:
        logger.error(f"[DB] Failed to save state for {app_name}: {e}")
    finally:
        if conn:
            conn.close()

