import logging
import requests
from scorebot_core_lite import config

logger = logging.getLogger("scorebot_core_lite.notifications")

def send_notification(message: str):
    """Sends notification to all enabled pluggable notification channels."""
    if not message:
        return

    logger.info(f"Notification: {message}")

    # 1. Discord Webhook
    if config.DISCORD_WEBHOOK_URL:
        try:
            requests.post(config.DISCORD_WEBHOOK_URL, json={"content": message}, timeout=5)
        except Exception as e:
            logger.error(f"Failed to send Discord notification: {e}")

    # 2. Slack Webhook
    if config.SLACK_WEBHOOK_URL:
        try:
            requests.post(config.SLACK_WEBHOOK_URL, json={"text": message}, timeout=5)
        except Exception as e:
            logger.error(f"Failed to send Slack notification: {e}")

    # 3. Generic Webhook
    if config.GENERIC_WEBHOOK_URL:
        try:
            requests.post(config.GENERIC_WEBHOOK_URL, json={"message": message}, timeout=5)
        except Exception as e:
            logger.error(f"Failed to send Generic Webhook notification: {e}")

    # 4. X (formerly Twitter) API v2 POST /2/tweets
    if all([config.X_API_KEY, config.X_API_SECRET, config.X_ACCESS_TOKEN, config.X_ACCESS_SECRET]):
        try:
            from requests_oauthlib import OAuth1
            auth = OAuth1(
                config.X_API_KEY,
                config.X_API_SECRET,
                config.X_ACCESS_TOKEN,
                config.X_ACCESS_SECRET
            )
            url = "https://api.twitter.com/2/tweets"
            payload = {"text": message}
            requests.post(url, auth=auth, json=payload, timeout=5)
        except Exception as e:
            logger.error(f"Failed to send X/Twitter notification: {e}")
