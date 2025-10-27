"""
Configuration management for Heroku Monitoring Bot.

This module handles all configuration from environment variables and dynamic
configuration that can be changed via the web UI.
"""
import os
import logging

logger = logging.getLogger(__name__)

# Static configuration from environment variables
HEROKU_API_KEY = os.environ.get('HEROKU_API_KEY')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')
BOT_APP_NAME = os.environ.get('BOT_APP_NAME')  # Name of this monitoring bot app on Heroku

# Dynamic configuration (can be changed via web UI)
dynamic_config = {
    'monitored_app': os.environ.get('MONITORED_APP_NAME', ''),
    'slack_channel': os.environ.get('SLACK_CHANNEL', '#alerts'),
    'check_interval': int(os.environ.get('CHECK_INTERVAL_MINUTES', '5'))
}

def is_configured() -> bool:
    """Check if all required configuration is present."""
    return bool(HEROKU_API_KEY and SLACK_BOT_TOKEN and DATABASE_URL)

