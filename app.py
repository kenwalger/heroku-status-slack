import os
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Configuration from environment variables
HEROKU_API_KEY = os.environ.get('HEROKU_API_KEY')
MONITORED_APP = os.environ.get('MONITORED_APP_NAME', '')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')
SLACK_CHANNEL = os.environ.get('SLACK_CHANNEL', '#alerts')
CHECK_INTERVAL = int(os.environ.get('CHECK_INTERVAL_MINUTES', '5'))

# Initialize Slack client
slack_client = None
if SLACK_BOT_TOKEN:
    slack_client = WebClient(token=SLACK_BOT_TOKEN)

# Store previous state to detect changes
app_state = {
    'dynos': {},
    'last_release': None,
    'config_vars_hash': None
}


class HerokuAPIClient:
    """Client for interacting with Heroku Platform API"""

    BASE_URL = 'https://api.heroku.com'

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            'Accept': 'application/vnd.heroku+json; version=3',
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

    def _request(self, method, endpoint, **kwargs):
        """Make HTTP request to Heroku API"""
        url = f"{self.BASE_URL}{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {e}")
            return None

    def get_app_info(self, app_name):
        """GET /apps/{app_id_or_name} - Basic app metadata"""
        return self._request('GET', f'/apps/{app_name}')

    def get_dynos(self, app_name):
        """GET /apps/{app_id_or_name}/dynos - List of dynos and their states"""
        return self._request('GET', f'/apps/{app_name}/dynos')

    def get_releases(self, app_name, limit=5):
        """GET /apps/{app_id_or_name}/releases - Recent releases"""
        return self._request('GET', f'/apps/{app_name}/releases?order=desc', params={'limit': limit})

    def get_addons(self, app_name):
        """GET /apps/{app_id_or_name}/addons - Attached add-ons"""
        return self._request('GET', f'/apps/{app_name}/addons')

    def get_config_vars(self, app_name):
        """GET /apps/{app_id_or_name}/config-vars - Configuration variables"""
        return self._request('GET', f'/apps/{app_name}/config-vars')

    def get_formation(self, app_name):
        """GET /apps/{app_id_or_name}/formation - Dyno formation"""
        return self._request('GET', f'/apps/{app_name}/formation')


# Initialize Heroku client
heroku_client = HerokuAPIClient(HEROKU_API_KEY) if HEROKU_API_KEY else None


def send_slack_message(text, blocks=None, channel=None):
    """Send a message to Slack"""
    if not slack_client:
        logger.warning("Slack client not configured, skipping message")
        return

    try:
        response = slack_client.chat_postMessage(
            channel=channel or SLACK_CHANNEL,
            text=text,
            blocks=blocks
        )
        logger.info(f"Slack message sent: {text[:50]}...")
    except SlackApiError as e:
        logger.error(f"Failed to send Slack message: {e}")


def format_dyno_status(dynos):
    """Format dyno information for display"""
    if not dynos:
        return "No dynos running"

    dyno_summary = {}
    for dyno in dynos:
        dyno_type = dyno.get('type', 'unknown')
        state = dyno.get('state', 'unknown')

        if dyno_type not in dyno_summary:
            dyno_summary[dyno_type] = {'total': 0, 'states': {}}

        dyno_summary[dyno_type]['total'] += 1
        dyno_summary[dyno_type]['states'][state] = dyno_summary[dyno_type]['states'].get(state, 0) + 1

    status_lines = []
    for dyno_type, info in dyno_summary.items():
        states_str = ', '.join([f"{count} {state}" for state, count in info['states'].items()])
        status_lines.append(f"• {dyno_type}: {info['total']} dynos ({states_str})")

    return '\n'.join(status_lines)


def check_app_health(app_name):
    """Perform comprehensive health check on the app"""
    if not heroku_client:
        logger.warning("Heroku client not configured")
        return

    logger.info(f"Running health check for {app_name}")

    # Check dynos
    dynos = heroku_client.get_dynos(app_name)
    if dynos:
        check_dyno_health(app_name, dynos)

    # Check releases
    releases = heroku_client.get_releases(app_name, limit=3)
    if releases:
        check_recent_releases(app_name, releases)

    # Check config vars
    config_vars = heroku_client.get_config_vars(app_name)
    if config_vars:
        check_config_changes(app_name, config_vars)


def check_dyno_health(app_name, dynos):
    """Check dyno states and alert on issues"""
    crashed_dynos = []
    down_dynos = []

    for dyno in dynos:
        dyno_id = dyno.get('id')
        dyno_name = dyno.get('name', dyno_id)
        dyno_type = dyno.get('type', 'unknown')
        state = dyno.get('state', 'unknown')

        # Store current state
        app_state['dynos'][dyno_id] = {
            'name': dyno_name,
            'type': dyno_type,
            'state': state
        }

        # Alert on problematic states
        if state == 'crashed':
            crashed_dynos.append(f"{dyno_name} ({dyno_type})")
        elif state == 'down':
            down_dynos.append(f"{dyno_name} ({dyno_type})")

    # Send alerts for crashed dynos
    if crashed_dynos:
        message = f"🚨 *ALERT: Dyno Crash Detected* 🚨\n\n"
        message += f"App: `{app_name}`\n"
        message += f"Crashed dynos:\n" + '\n'.join([f"• {d}" for d in crashed_dynos])
        send_slack_message(message)

    # Send alerts for down dynos
    if down_dynos:
        message = f"⚠️ *WARNING: Dynos Down* ⚠️\n\n"
        message += f"App: `{app_name}`\n"
        message += f"Down dynos:\n" + '\n'.join([f"• {d}" for d in down_dynos])
        send_slack_message(message)


def check_recent_releases(app_name, releases):
    """Check recent releases and correlate with issues"""
    if not releases:
        return

    latest_release = releases[0]
    release_version = latest_release.get('version')

    # Check if this is a new release
    if app_state['last_release'] and app_state['last_release'] != release_version:
        created_at = latest_release.get('created_at', '')
        description = latest_release.get('description', 'No description')
        user_email = latest_release.get('user', {}).get('email', 'Unknown')

        message = f"🚀 *New Deploy Detected* 🚀\n\n"
        message += f"App: `{app_name}`\n"
        message += f"Version: v{release_version}\n"
        message += f"Deployed by: {user_email}\n"
        message += f"Description: {description}\n"
        message += f"Time: {created_at}\n\n"
        message += f"_Monitoring for issues..._"

        send_slack_message(message)

    # Update stored release version
    app_state['last_release'] = release_version


def check_config_changes(app_name, config_vars):
    """Monitor config var changes"""
    # Create a hash of config var keys (not values for security)
    config_hash = hash(frozenset(config_vars.keys()))

    if app_state['config_vars_hash'] and app_state['config_vars_hash'] != config_hash:
        message = f"⚙️ *Config Vars Changed* ⚙️\n\n"
        message += f"App: `{app_name}`\n"
        message += f"Config variables have been modified.\n"
        message += f"_Review changes in the Heroku dashboard._"

        send_slack_message(message)

    app_state['config_vars_hash'] = config_hash


@app.route('/')
def index():
    """Health check endpoint"""
    return jsonify({
        'status': 'ok',
        'service': 'Heroku Monitoring Bot',
        'monitored_app': MONITORED_APP,
        'timestamp': datetime.utcnow().isoformat()
    })


@app.route('/health')
def health():
    """Detailed health endpoint"""
    config_status = {
        'heroku_api_configured': HEROKU_API_KEY is not None,
        'slack_configured': SLACK_BOT_TOKEN is not None,
        'monitored_app': MONITORED_APP,
        'check_interval': CHECK_INTERVAL
    }
    return jsonify(config_status)


@app.route('/slack/command', methods=['POST'])
def slack_command():
    """Handle Slack slash commands"""
    # Parse the slash command
    command_text = request.form.get('text', '')
    command = request.form.get('command', '')
    response_url = request.form.get('response_url')

    logger.info(f"Received Slack command: {command} {command_text}")

    # Handle /heroku-status command
    if command == '/heroku-status':
        app_name = command_text.strip() or MONITORED_APP

        if not app_name:
            return jsonify({
                'response_type': 'ephemeral',
                'text': '❌ Please specify an app name or configure MONITORED_APP_NAME'
            })

        status_info = get_app_status(app_name)

        return jsonify({
            'response_type': 'in_channel',
            'text': status_info
        })

    return jsonify({
        'response_type': 'ephemeral',
        'text': f'Unknown command: {command}'
    })


def get_app_status(app_name):
    """Get comprehensive app status"""
    if not heroku_client:
        return "❌ Heroku API not configured"

    # Fetch app info
    app_info = heroku_client.get_app_info(app_name)
    dynos = heroku_client.get_dynos(app_name)
    releases = heroku_client.get_releases(app_name, limit=3)
    addons = heroku_client.get_addons(app_name)
    formation = heroku_client.get_formation(app_name)

    if not app_info:
        return f"❌ Could not fetch info for app: {app_name}"

    # Build status message
    status = f"📊 *Heroku App Status: {app_name}* 📊\n\n"

    # App info
    status += f"*App Details:*\n"
    status += f"• Name: `{app_info.get('name')}`\n"
    status += f"• Owner: {app_info.get('owner', {}).get('email', 'Unknown')}\n"
    status += f"• Region: {app_info.get('region', {}).get('name', 'Unknown')}\n"
    status += f"• Stack: {app_info.get('stack', {}).get('name', 'Unknown')}\n"
    status += f"• Web URL: {app_info.get('web_url', 'N/A')}\n\n"

    # Dyno status
    status += f"*Dyno Status:*\n"
    if dynos:
        status += format_dyno_status(dynos) + "\n\n"
    else:
        status += "No dynos currently running\n\n"

    # Formation
    if formation:
        status += f"*Dyno Formation:*\n"
        for process in formation:
            status += f"• {process.get('type')}: {process.get('quantity')} x {process.get('size')}\n"
        status += "\n"

    # Recent releases
    if releases:
        status += f"*Recent Releases:*\n"
        for release in releases[:3]:
            version = release.get('version')
            description = release.get('description', 'No description')
            created = release.get('created_at', '')
            status += f"• v{version}: {description} ({created})\n"
        status += "\n"

    # Add-ons
    if addons:
        status += f"*Add-ons:*\n"
        for addon in addons:
            name = addon.get('name', 'Unknown')
            plan = addon.get('plan', {}).get('name', 'Unknown')
            state = addon.get('state', 'Unknown')
            status += f"• {name} ({plan}) - {state}\n"
    else:
        status += f"*Add-ons:* None\n"

    return status


# Initialize scheduler
scheduler = BackgroundScheduler()

def scheduled_health_check():
    """Scheduled health check job"""
    if MONITORED_APP:
        check_app_health(MONITORED_APP)
    else:
        logger.warning("No monitored app configured")


@app.before_request
def initialize_scheduler():
    """Initialize the scheduler on first request"""
    if not scheduler.running:
        if MONITORED_APP and CHECK_INTERVAL > 0:
            scheduler.add_job(
                func=scheduled_health_check,
                trigger="interval",
                minutes=CHECK_INTERVAL,
                id='health_check',
                name='Periodic health check',
                replace_existing=True
            )
            scheduler.start()
            logger.info(f"Scheduler started: checking {MONITORED_APP} every {CHECK_INTERVAL} minutes")


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
