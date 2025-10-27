"""
Slack integration for Heroku Monitoring Bot.

This module handles all Slack messaging functionality including sending
notifications to configured channels.
"""
import logging
from typing import Optional, List, Dict, Any
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from config import SLACK_BOT_TOKEN, dynamic_config

logger = logging.getLogger(__name__)

# Initialize Slack client
slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None


def send_slack_message(text: str, 
                       blocks: Optional[List[Dict[str, Any]]] = None, 
                       channel: Optional[str] = None
                       ) -> None:
    """
    Send a message to a Slack channel using the Slack WebClient.

    Args:
        text (str): The message text to send.
        blocks (Optional[List[Dict[str, Any]]]): Optional Slack block kit payload.
        channel (Optional[str]): Slack channel ID or name. Defaults to configured channel.

    Returns:
        None
    """
    if not slack_client:
        logger.warning("Slack client not configured, skipping message")
        return

    try:
        slack_client.chat_postMessage(
            channel=channel or dynamic_config['slack_channel'],
            text=text,
            blocks=blocks
        )
        logger.info(f"Slack message sent to {channel or dynamic_config['slack_channel']}: {text[:50]}...")
    except SlackApiError as e:
        logger.error(f"Failed to send Slack message: {e}")

