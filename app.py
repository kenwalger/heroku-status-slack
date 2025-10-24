import os
import json
import logging
import threading
from datetime import datetime
from flask import Flask, request, jsonify, render_template, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import psycopg2

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Static configuration from environment variables
HEROKU_API_KEY = os.environ.get('HEROKU_API_KEY')
SLACK_BOT_TOKEN = os.environ.get('SLACK_BOT_TOKEN')
DATABASE_URL = os.environ.get('DATABASE_URL')  # Heroku Postgres URL

# Dynamic configuration (can be changed via web UI)
dynamic_config = {
    'monitored_app': os.environ.get('MONITORED_APP_NAME', ''),
    'slack_channel': os.environ.get('SLACK_CHANNEL', '#alerts'),
    'check_interval': int(os.environ.get('CHECK_INTERVAL_MINUTES', '5'))
}

# Initialize Slack client
slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

# --------------------------
# Database helpers
# --------------------------
def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def load_app_state(app_name):
    """Load persisted app state from Postgres"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT last_release, dynos, config_vars_hash FROM app_state WHERE app_name = %s",
                (app_name,)
            )
            row = cur.fetchone()
            if row:
                last_release, dynos_json, config_vars_hash = row
                return {
                    'last_release': last_release,
                    'dynos': json.loads(dynos_json) if dynos_json else {},
                    'config_vars_hash': config_vars_hash
                }
            return {'last_release': None, 'dynos': {}, 'config_vars_hash': None}

def save_app_state(app_name, state):
    """Persist app state to Postgres"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO app_state (app_name, last_release, dynos, config_vars_hash, updated_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (app_name)
                DO UPDATE SET last_release = EXCLUDED.last_release,
                              dynos = EXCLUDED.dynos,
                              config_vars_hash = EXCLUDED.config_vars_hash,
                              updated_at = EXCLUDED.updated_at
            """, (
                app_name,
                state['last_release'],
                json.dumps(state['dynos']),
                state['config_vars_hash'],
                datetime.utcnow()
            ))

# --------------------------
# Heroku API Client
# --------------------------
class HerokuAPIClient:
    BASE_URL = 'https://api.heroku.com'

    def __init__(self, api_key):
        self.api_key = api_key
        self.headers = {
            'Accept': 'application/vnd.heroku+json; version=3',
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json'
        }

    def _request(self, method, endpoint, **kwargs):
        url = f"{self.BASE_URL}{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Heroku API request failed: {e}")
            return None

    def get_app_info(self, app_name):
        return self._request('GET', f'/apps/{app_name}')

    def get_dynos(self, app_name):
        return self._request('GET', f'/apps/{app_name}/dynos')

    def get_releases(self, app_name, limit=5):
        return self._request('GET', f'/apps/{app_name}/releases?order=desc', params={'limit': limit})

    def get_addons(self, app_name):
        return self._request('GET', f'/apps/{app_name}/addons')

    def get_config_vars(self, app_name):
        return self._request('GET', f'/apps/{app_name}/config-vars')

    def get_formation(self, app_name):
        return self._request('GET', f'/apps/{app_name}/formation')


# Initialize Heroku client
heroku_client = HerokuAPIClient(HEROKU_API_KEY) if HEROKU_API_KEY else None

# --------------------------
# Slack messaging
# --------------------------
def send_slack_message(text, blocks=None, channel=None):
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

# --------------------------
# Health checks
# --------------------------
def check_dyno_health(app_name, dynos, state):
    """Check dyno states and alert on issues"""
    crashed, down = [], []

    prev_dynos = state.get('dynos', {})

    for dyno in dynos:
        dyno_id = dyno.get('id')
        dyno_name = dyno.get('name', dyno_id)
        dyno_type = dyno.get('type', 'unknown')
        dyno_state = dyno.get('state', 'unknown')

        # Compare with previous state
        prev_state = prev_dynos.get(dyno_id, {}).get('state')
        if prev_state != dyno_state:
            if dyno_state == 'crashed':
                crashed.append(f"{dyno_name} ({dyno_type})")
            elif dyno_state == 'down':
                down.append(f"{dyno_name} ({dyno_type})")

        # Update current state
        prev_dynos[dyno_id] = {'name': dyno_name, 'type': dyno_type, 'state': dyno_state}

    if crashed:
        send_slack_message(f"🚨 *ALERT: Dyno Crash Detected* 🚨\n\nApp: `{app_name}`\nCrashed dynos:\n• " + '\n• '.join(crashed))
    if down:
        send_slack_message(f"⚠️ *WARNING: Dynos Down* ⚠️\n\nApp: `{app_name}`\nDown dynos:\n• " + '\n• '.join(down))

    state['dynos'] = prev_dynos

def check_recent_releases(app_name, releases, state):
    if not releases:
        return

    latest = releases[0]
    release_version = latest.get('version')

    if state.get('last_release') and state['last_release'] != release_version:
        user_email = latest.get('user', {}).get('email', 'Unknown')
        created_at = latest.get('created_at', '')
        description = latest.get('description', 'No description')

        send_slack_message(
            f"🚀 *New Deploy Detected* 🚀\n\n"
            f"App: `{app_name}`\n"
            f"Version: v{release_version}\n"
            f"Deployed by: {user_email}\n"
            f"Description: {description}\n"
            f"Time: {created_at}\n\n"
            "_Monitoring for issues..._"
        )

    state['last_release'] = release_version

def check_config_changes(app_name, config_vars, state):
    config_hash = hash(frozenset(config_vars.keys()))
    if state.get('config_vars_hash') and state['config_vars_hash'] != config_hash:
        send_slack_message(
            f"⚙️ *Config Vars Changed* ⚙️\n\n"
            f"App: `{app_name}`\n"
            f"Config variables have been modified.\n"
            "_Review changes in the Heroku dashboard._"
        )
    state['config_vars_hash'] = config_hash

def check_app_health(app_name):
    if not heroku_client:
        logger.warning("Heroku client not configured")
        return

    state = load_app_state(app_name)

    dynos = heroku_client.get_dynos(app_name)
    releases = heroku_client.get_releases(app_name, limit=3)
    config_vars = heroku_client.get_config_vars(app_name)

    if dynos:
        check_dyno_health(app_name, dynos, state)
    if releases:
        check_recent_releases(app_name, releases, state)
    if config_vars:
        check_config_changes(app_name, config_vars, state)

    save_app_state(app_name, state)

# --------------------------
# Scheduler
# --------------------------
scheduler = BackgroundScheduler()
scheduler_initialized = False

def scheduled_health_check():
    monitored_app = dynamic_config.get('monitored_app')
    if monitored_app:
        logger.info(f"Running scheduled check for: {monitored_app}")
        check_app_health(monitored_app)

def restart_scheduler():
    global scheduler_initialized
    monitored_app = dynamic_config.get('monitored_app')
    interval = dynamic_config.get('check_interval', 5)

    if not monitored_app or interval <= 0:
        logger.info("Scheduler not started: no app configured or invalid interval")
        return

    try:
        if scheduler.running and scheduler.get_job('health_check'):
            scheduler.remove_job('health_check')
        scheduler.add_job(
            func=scheduled_health_check,
            trigger="interval",
            minutes=interval,
            id='health_check',
            name='Periodic health check',
            replace_existing=True
        )
        if not scheduler.running:
            scheduler.start()
            scheduler_initialized = True

        logger.info(f"Scheduler updated: checking {monitored_app} every {interval} minutes")
    except Exception as e:
        logger.error(f"Error restarting scheduler: {e}")

@app.before_request
def initialize_scheduler():
    global scheduler_initialized
    if not scheduler_initialized and dynamic_config.get('monitored_app'):
        restart_scheduler()
        scheduler_initialized = True

# --------------------------
# Flask routes
# --------------------------
@app.route('/')
def index():
    return render_template(
        'dashboard.html',
        current_app=dynamic_config.get('monitored_app', ''),
        current_channel=dynamic_config.get('slack_channel', '#alerts'),
        check_interval=dynamic_config.get('check_interval', 5),
        monitoring_active=bool(dynamic_config.get('monitored_app') and HEROKU_API_KEY and SLACK_BOT_TOKEN),
        heroku_api_configured=bool(HEROKU_API_KEY),
        slack_configured=bool(SLACK_BOT_TOKEN)
    )

@app.route('/update-config', methods=['POST'])
def update_config():
    app_name = request.form.get('app_name', '').strip()
    slack_channel = request.form.get('slack_channel', '').strip()
    check_interval_str = request.form.get('check_interval', '').strip()

    if not app_name or not slack_channel or not check_interval_str:
        return redirect(url_for('index') + '?error=All fields are required')

    try:
        check_interval = int(check_interval_str)
        if not (1 <= check_interval <= 60):
            raise ValueError
    except ValueError:
        return redirect(url_for('index') + '?error=Invalid interval')

    old_app = dynamic_config.get('monitored_app')
    old_interval = dynamic_config.get('check_interval')

    dynamic_config['monitored_app'] = app_name
    dynamic_config['slack_channel'] = slack_channel
    dynamic_config['check_interval'] = check_interval

    if old_app != app_name or old_interval != check_interval:
        restart_scheduler()

    return redirect(url_for('index') + '?success=true')

@app.route('/api/status')
def api_status():
    return jsonify({
        'status': 'ok',
        'service': 'Heroku Monitoring Bot',
        'monitored_app': dynamic_config.get('monitored_app'),
        'slack_channel': dynamic_config.get('slack_channel'),
        'check_interval': dynamic_config.get('check_interval'),
        'monitoring_active': bool(dynamic_config.get('monitored_app') and HEROKU_API_KEY and SLACK_BOT_TOKEN),
        'timestamp': datetime.utcnow().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({
        'heroku_api_configured': HEROKU_API_KEY is not None,
        'slack_configured': SLACK_BOT_TOKEN is not None,
        'monitored_app': dynamic_config.get('monitored_app'),
        'slack_channel': dynamic_config.get('slack_channel'),
        'check_interval': dynamic_config.get('check_interval')
    })

@app.route('/slack/command', methods=['POST'])
def slack_command():
    command_text = request.form.get('text', '')
    command = request.form.get('command', '')
    response_url = request.form.get('response_url')

    if command != '/heroku-status':
        return jsonify({'response_type': 'ephemeral', 'text': f'Unknown command: {command}'})

    if command_text.lower() == "help":
        return jsonify({
            'response_type': 'ephemeral',
            'text': (
                "🤠 *Heroku Monitoring Bot Help* 🤠\n\n"
                "• `/heroku-status [app_name]` - Get current status of a monitored Heroku app\n"
                "• Configure monitoring via the web dashboard at `/`\n"
                "• Alerts are sent to Slack when dynos crash, releases deploy, or config vars change\n"
                "• Check interval can be adjusted in the dashboard (1-60 min)\n"
            )
        })

    app_name = command_text.strip() or dynamic_config.get('monitored_app')
    if not app_name:
        return jsonify({'response_type': 'ephemeral', 'text': '❌ Specify an app name or configure monitoring'})

    threading.Thread(target=fetch_and_post_status, args=(app_name, response_url)).start()
    return jsonify({'response_type': 'ephemeral', 'text': f"⏳ Fetching status for `{app_name}`..."})

def fetch_and_post_status(app_name, response_url):
    try:
        status_info = get_app_status(app_name)
        requests.post(response_url, json={'response_type': 'in_channel', 'text': status_info})
    except Exception as e:
        logger.error(f"Failed to post status for {app_name}: {e}")
        requests.post(response_url, json={'response_type': 'ephemeral', 'text': f"❌ Failed: {e}"})

def get_app_status(app_name):
    if not heroku_client:
        return "❌ Heroku API not configured"

    app_info = heroku_client.get_app_info(app_name)
    dynos = heroku_client.get_dynos(app_name)
    releases = heroku_client.get_releases(app_name, limit=3)
    addons = heroku_client.get_addons(app_name)
    formation = heroku_client.get_formation(app_name)

    if not app_info:
        return f"❌ Could not fetch info for app: {app_name}"

    status = f"📊 *Heroku App Status: {app_name}* 📊\n\n"
    status += f"*App Details:*\n• Name: `{app_info.get('name')}`\n• Owner: {app_info.get('owner', {}).get('email','Unknown')}\n• Region: {app_info.get('region', {}).get('name','Unknown')}\n• Stack: {app_info.get('stack', {}).get('name','Unknown')}\n• Web URL: {app_info.get('web_url','N/A')}\n\n"

    status += "*Dyno Status:*\n" + (format_dyno_status(dynos) + "\n\n" if dynos else "No dynos currently running\n\n")

    if formation:
        status += "*Dyno Formation:*\n"
        for proc in formation:
            status += f"• {proc.get('type')}: {proc.get('quantity')} x {proc.get('size')}\n"
        status += "\n"

    if releases:
        status += "*Recent Releases:*\n"
        for release in sorted(releases, key=lambda r: r['version'], reverse=True)[:3]:
            desc = release.get('description', 'No description')
            status += f"• v{release.get('version')}: {desc} ({release.get('created_at','')})\n"
        status += "\n"

    if addons:
        status += "*Add-ons:*\n"
        for addon in addons:
            status += f"• {addon.get('name','Unknown')} ({addon.get('plan', {}).get('name','Unknown')}) - {addon.get('state','Unknown')}\n"
    else:
        status += "*Add-ons:* None\n"

    return status

def format_dyno_status(dynos):
    if not dynos:
        return "No dynos running"

    summary = {}
    for dyno in dynos:
        t = dyno.get('type','unknown')
        s = dyno.get('state','unknown')
        if t not in summary: summary[t] = {'total':0,'states':{}}
        summary[t]['total'] += 1
        summary[t]['states'][s] = summary[t]['states'].get(s,0) + 1

    lines = []
    for t, info in summary.items():
        states_str = ', '.join([f"{c} {st}" for st, c in info['states'].items()])
        lines.append(f"• {t}: {info['total']} dynos ({states_str})")
    return '\n'.join(lines)

# --------------------------
# Run Flask
# --------------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
