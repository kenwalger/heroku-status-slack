import os
import json
import logging
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Static configuration from environment variables (cannot be changed via UI)
HEROKU_API_KEY = os.environ.get('HEROKU_API_KEY')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')

# Dynamic configuration (can be changed via web UI)
dynamic_config = {
    'monitored_app': os.environ.get('MONITORED_APP_NAME', ''),
    'slack_channel': os.environ.get('SLACK_CHANNEL', '#alerts'),
    'check_interval': int(os.environ.get('CHECK_INTERVAL_MINUTES', '5'))
}

# Helper function to get current config values
def get_monitored_app():
    return dynamic_config.get('monitored_app', '')

def get_slack_channel():
    return dynamic_config.get('slack_channel', '#alerts')

def get_check_interval():
    return dynamic_config.get('check_interval', 5)

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
            channel=channel or get_slack_channel(),
            text=text,
            blocks=blocks
        )
        logger.info(f"Slack message sent to {channel or get_slack_channel()}: {text[:50]}...")
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
    """Main dashboard page"""
    return render_template(
        'dashboard.html',
        current_app=get_monitored_app(),
        current_channel=get_slack_channel(),
        check_interval=get_check_interval(),
        monitoring_active=bool(get_monitored_app() and HEROKU_API_KEY and SLACK_BOT_TOKEN),
        heroku_api_configured=bool(HEROKU_API_KEY),
        slack_configured=bool(SLACK_BOT_TOKEN)
    )


@app.route('/update-config', methods=['POST'])
def update_config():
    """Update monitoring configuration"""
    app_name = request.form.get('app_name', '').strip()
    slack_channel = request.form.get('slack_channel', '').strip()
    check_interval_str = request.form.get('check_interval', '').strip()

    if not app_name or not slack_channel or not check_interval_str:
        return redirect(url_for('index') + '?error=All fields are required')

    # Validate check interval
    try:
        check_interval = int(check_interval_str)
        if check_interval < 1:
            return redirect(url_for('index') + '?error=Check interval must be at least 1 minute')
        if check_interval > 60:
            return redirect(url_for('index') + '?error=Check interval cannot exceed 60 minutes')
    except ValueError:
        return redirect(url_for('index') + '?error=Check interval must be a valid number')

    # Update dynamic configuration
    old_app = get_monitored_app()
    old_interval = get_check_interval()
    dynamic_config['monitored_app'] = app_name
    dynamic_config['slack_channel'] = slack_channel
    dynamic_config['check_interval'] = check_interval

    logger.info(f"Configuration updated: app={app_name}, channel={slack_channel}, interval={check_interval}")

    # Update scheduler if the monitored app or interval changed
    if old_app != app_name or old_interval != check_interval:
        restart_scheduler()

    return redirect(url_for('index') + '?success=true')


@app.route('/api/status')
def api_status():
    """JSON API endpoint for current status"""
    return jsonify({
        'status': 'ok',
        'service': 'Heroku Monitoring Bot',
        'monitored_app': get_monitored_app(),
        'slack_channel': get_slack_channel(),
        'check_interval': get_check_interval(),
        'monitoring_active': bool(get_monitored_app() and HEROKU_API_KEY and SLACK_BOT_TOKEN),
        'timestamp': datetime.utcnow().isoformat()
    })


@app.route('/health')
def health():
    """Detailed health endpoint"""
    config_status = {
        'heroku_api_configured': HEROKU_API_KEY is not None,
        'slack_configured': SLACK_BOT_TOKEN is not None,
        'monitored_app': get_monitored_app(),
        'slack_channel': get_slack_channel(),
        'check_interval': get_check_interval()
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

        if command_text.lower() == "help":
        return jsonify({
            'response_type': 'ephemeral',
            'text': (
                "🤠 *Heroku Monitoring Bot Help* 🤠\n\n"
                "• `/heroku-status [app_name]` - Get current status of a monitored Heroku app\n"
                "• Configure monitoring via the web dashboard at `/`\n"
                "• Alerts are sent to your configured Slack channel when dynos crash, releases deploy, or config vars change\n"
                "• Check interval can be adjusted in the dashboard (1-60 min)\n"
                "• Static configuration (Heroku API key, Slack Bot Token) requires a redeploy to change\n"
            )
        })

        app_name = command_text.strip() or get_monitored_app()

        if not app_name:
            return jsonify({
                'response_type': 'ephemeral',
                'text': '❌ Please specify an app name or configure monitoring via the web UI'
            })

        threading.Thread(target=fetch_and_post_status, args=(app_name, response_url)).start()
        return jsonify({
            'response_type': 'ephemeral',
            'text': f"⏳ Fetching status for `{app_name}`... You should see results shortly."
        })

    return jsonify({
        'response_type': 'ephemeral',
        'text': f'Unknown command: {command}'
    })



def fetch_and_post_status(app_name, response_url):
    """Fetch the Heroku status and post it asynchronously to Slack"""
    try:
        status_info = get_app_status(app_name)
        requests.post(response_url, json={
            'response_type': 'in_channel',
            'text': status_info
        })
        logger.info(f"Posted status update for {app_name} to Slack")
    except Exception as e:
        logger.error(f"Failed to post status for {app_name}: {e}")
        requests.post(response_url, json={
            'response_type': 'ephemeral',
            'text': f"❌ Failed to fetch status for `{app_name}`: {e}"
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
    releases = heroku_client.get_releases(app_name)
    releases_sorted = sorted(releases, key=lambda r: r['version'], reverse=True)

    recent_releases = releases_sorted[:3]

    if recent_releases:
        status += f"*Recent Releases:*\n"
        for release in recent_releases:
            version = release.get('version')
            description = release.get('description', 'No description')
            if description.lower().startswith('deploy '):
                description = f"Code push ({description.split(' ')[1]})"
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
scheduler_initialized = False


def scheduled_health_check():
    """Scheduled health check job"""
    monitored_app = get_monitored_app()
    if monitored_app:
        logger.info(f"Running scheduled check for: {monitored_app}")
        check_app_health(monitored_app)
    else:
        logger.warning("No monitored app configured")


def restart_scheduler():
    """Restart the scheduler with updated configuration"""
    global scheduler_initialized

    monitored_app = get_monitored_app()
    check_interval = get_check_interval()

    if not monitored_app or check_interval <= 0:
        logger.info("Scheduler not started: no app configured or invalid interval")
        return

    try:
        # Remove existing job if it exists
        if scheduler.running and scheduler.get_job('health_check'):
            scheduler.remove_job('health_check')
            logger.info("Removed existing scheduler job")

        # Add new job with updated config
        scheduler.add_job(
            func=scheduled_health_check,
            trigger="interval",
            minutes=check_interval,
            id='health_check',
            name='Periodic health check',
            replace_existing=True
        )

        # Start scheduler if not already running
        if not scheduler.running:
            scheduler.start()
            scheduler_initialized = True

        logger.info(f"Scheduler updated: checking {monitored_app} every {check_interval} minutes")
    except Exception as e:
        logger.error(f"Error restarting scheduler: {e}")


@app.before_request
def initialize_scheduler():
    """Initialize the scheduler on first request"""
    global scheduler_initialized

    if not scheduler_initialized:
        monitored_app = get_monitored_app()
        check_interval = get_check_interval()
        if monitored_app and check_interval > 0:
            restart_scheduler()
            scheduler_initialized = True


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
