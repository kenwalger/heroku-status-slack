import os
import hashlib
import json
import logging
import threading
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template, redirect, url_for
from apscheduler.schedulers.background import BackgroundScheduler
import requests
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from typing import Dict, Optional, Any, List
import psycopg2
import psycopg2.extras

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

# Scheduler globals
scheduler = BackgroundScheduler()
scheduler_initialized = False

# Initialize Slack client
slack_client = WebClient(token=SLACK_BOT_TOKEN) if SLACK_BOT_TOKEN else None

# --------------------------
# Database helpers
# --------------------------
def get_db_connection() -> psycopg2.extensions.connection:
    """
    Create and return a new database connection to the configured Postgres database.

    Returns:
        psycopg2.extensions.connection: Active database connection.
    """
    return psycopg2.connect(DATABASE_URL, sslmode='require')

def load_app_state(app_name:str) -> Dict[str, Any]:
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


def save_app_state(app_name:str, state: Dict[str, Any]) -> None:
    """
    Persist monitoring state for a Heroku app to Postgres.

    Args:
        app_name (str): Name of the app.
        state (Dict[str, Any]): State dict to persist. Expected keys:
            'last_release', 'dynos', 'config_vars_hash', 'updated_at'.

    Returns:
        None
    """
    conn = psycopg2.connect(dsn=os.environ['DATABASE_URL'])
    try:
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
                state.get('last_release'),
                json.dumps(state.get('dynos', {})),  # ensure we always save JSON string
                state.get('config_vars_hash'),
                datetime.now(timezone.utc).isoformat()
            ))
            conn.commit()
    finally:
        conn.close()


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

    def _request(self, method: str, endpoint: str, **kwargs) -> Optional[dict]:
        """
        Make a request to the Heroku API.

        Args:
            method (str): HTTP method, e.g., 'GET', 'POST'.
            endpoint (str): API endpoint path.
            **kwargs: Additional arguments for requests.request.

        Returns:
            Optional[dict]: Parsed JSON response or None on failure.
        """
        url = f"{self.BASE_URL}{endpoint}"
        try:
            response = requests.request(method, url, headers=self.headers, **kwargs)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Heroku API request failed: {e}")
            return None

    def get_app_info(self, app_name: str) -> Optional[dict]:
        """
        Get general information for a Heroku app.

        Args:
            app_name (str): Heroku app name.

        Returns:
            Optional[dict]: App info dictionary or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}')

    def get_dynos(self, app_name: str) -> Optional[list[dict]]:
        """
        Get the dyno states for a Heroku app.

        Args:
            app_name (str): The Heroku app name.

        Returns:
            Optional[list[dict]]: List of dyno info dictionaries or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}/dynos')

    def get_releases(self, app_name: str, limit: int = 5) -> Optional[list[dict]]:
        """
        Get recent releases for a Heroku app.

        Args:
            app_name (str): Heroku app name.
            limit (int): Maximum number of releases to fetch.

        Returns:
            Optional[list[dict]]: List of release dictionaries or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}/releases?order=desc', params={'limit': limit})

    def get_addons(self, app_name: str) -> Optional[list[dict]]:
        """
        Get installed add-ons for a Heroku app.

        Args:
            app_name (str): Heroku app name.

        Returns:
            Optional[list[dict]]: List of add-on dictionaries or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}/addons')

    def get_config_vars(self, app_name: str) -> Optional[dict]:
        """
        Get configuration variables for a Heroku app.

        Args:
            app_name (str): Heroku app name.

        Returns:
            Optional[dict]: Dictionary of config vars or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}/config-vars')

    def get_formation(self, app_name: str) -> Optional[list[dict]]:
        """
        Get the dyno formation (type/size/quantity) for a Heroku app.

        Args:
            app_name (str): Heroku app name.

        Returns:
            Optional[list[dict]]: List of formation dictionaries or None if request fails.
        """
        return self._request('GET', f'/apps/{app_name}/formation')


# Initialize Heroku client
heroku_client = HerokuAPIClient(HEROKU_API_KEY) if HEROKU_API_KEY else None

# --------------------------
# Slack messaging
# --------------------------
def send_slack_message(text: str, 
                       blocks: Optional[List[Dict[str, Any]]] = None, 
                       channel: Optional[str]=None
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

# --------------------------
# Health checks
# --------------------------
def check_dyno_health(app_name: str, dynos: list[dict], state: dict) -> None:
    """
    Compare current dynos to previous state and send Slack alerts for crashes or downtime.

    Args:
        app_name (str): Heroku app name.
        dynos (list[dict]): Current dyno info from Heroku API.
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

def check_recent_releases(app_name: str, releases: list[dict], state: dict) -> None:
    """
    Check for new releases and send Slack alerts if a new deploy is detected.

    Args:
        app_name (str): Heroku app name.
        releases (list[dict]): List of recent release dictionaries.
        state (dict): Monitoring state dict; will be updated in-place.

    Returns:
        None
    """
    if not releases:
        return

    latest = releases[0]
    release_version = latest.get('version')
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
        print(f"Error loading config state for {app_name}: {e}")
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
        # Log error but don’t crash scheduler
        print(f"Error persisting config state for {app_name}: {e}")
    finally:
        if conn:
            conn.close()

def check_app_health(app_name: str) -> None:
    """
    Orchestrate the health check: dynos, releases, and config vars.

    Args:
        app_name (str): Heroku app name.

    Returns:
        None
    """
    if not heroku_client:
        logger.warning("Heroku client not configured")
        return

    state = load_app_state(app_name) or {}

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


def initialize_scheduler_once():
    global scheduler_initialized
    # Only start scheduler in the first web dyno
    if not scheduler_initialized and os.environ.get("DYNO", "").startswith("web.1"):
        restart_scheduler()
        scheduler_initialized = True

def scheduled_health_check() -> None:
    """
    Scheduled job function for the APScheduler.
    Runs `check_app_health` for the currently monitored app.
    """
    monitored_app = dynamic_config.get('monitored_app')
    if not monitored_app:
        return
    
    logger.info(f"Running scheduled check for: {monitored_app}")
    check_app_health(monitored_app)

def restart_scheduler() -> None:
    """
    Restart or initialize the scheduler with the current monitored app and interval.
    Removes existing job if present, updates interval, and starts scheduler if needed.
    """
    monitored_app = dynamic_config.get('monitored_app')
    interval = dynamic_config.get('check_interval', 5)

    if not monitored_app or interval <= 0:
        logger.info("Scheduler not started: no app configured or invalid interval")
        return

    if scheduler.get_job('health_check'):
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
        logger.info(f"Scheduler started: checking {monitored_app} every {interval} minutes")
    else:
        logger.info(f"Scheduler updated: checking {monitored_app} every {interval} minutes")


@app.before_request
def initialize_scheduler():
    """
    Ensure the scheduler starts once per web dyno.
    """
    global scheduler_initialized
    if not scheduler_initialized and dynamic_config.get('monitored_app'):
        restart_scheduler()
        scheduler_initialized = True

# --------------------------
# Flask routes
# --------------------------
@app.route('/')
def index() -> str:
    """
    Render the dashboard page with current dynamic configuration.

    Returns:
        str: Rendered HTML template.
    """
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
def update_config() -> Any:
    """
    Update the dynamic monitoring configuration from the web dashboard.

    Returns:
        Flask redirect response back to the dashboard with optional query parameters.
    """
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
def api_status() -> Dict[str, Any]:
    """
    Return JSON with current service and monitoring configuration.

    Returns:
        Dict[str, Any]: Status payload.
    """
    return jsonify({
        'status': 'ok',
        'service': 'Heroku Monitoring Bot',
        'monitored_app': dynamic_config.get('monitored_app'),
        'slack_channel': dynamic_config.get('slack_channel'),
        'check_interval': dynamic_config.get('check_interval'),
        'monitoring_active': bool(dynamic_config.get('monitored_app') and HEROKU_API_KEY and SLACK_BOT_TOKEN),
        'timestamp': datetime.now(timezone.utc).isoformat()
    })

@app.route('/health')
def health() -> Dict[str, Any]:
    """
    Return JSON with system and monitoring health info.

    Returns:
        Dict[str, Any]: Health payload.
    """
    return jsonify({
        'heroku_api_configured': HEROKU_API_KEY is not None,
        'slack_configured': SLACK_BOT_TOKEN is not None,
        'monitored_app': dynamic_config.get('monitored_app'),
        'slack_channel': dynamic_config.get('slack_channel'),
        'check_interval': dynamic_config.get('check_interval')
    })

@app.route('/slack/command', methods=['POST'])
def slack_command() -> Any:
    """
    Handle the /heroku-status Slack slash command.

    Returns:
        Flask Response: JSON response to Slack (ephemeral or in_channel).
    """
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

def fetch_and_post_status(app_name: str, response_url: str) -> None:
    """
    Fetch the current status of a Heroku app and post it back to Slack.

    Args:
        app_name (str): The Heroku app to fetch status for.
        response_url (str): Slack response URL provided in the slash command payload.

    Returns:
        None
    """
    try:
        status_info = get_app_status(app_name)
        requests.post(response_url, json={'response_type': 'in_channel', 'text': status_info})
    except Exception as e:
        logger.error(f"Failed to post status for {app_name}: {e}")
        requests.post(response_url, json={'response_type': 'ephemeral', 'text': f"❌ Failed: {e}"})

def get_app_status(app_name: str) -> str:
    """
    Build a detailed textual status of a Heroku app, including dynos, releases, formation, and add-ons.

    Args:
        app_name (str): The Heroku app to inspect.

    Returns:
        str: A formatted string suitable for Slack messages.
    """
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

def format_dyno_status(dynos: Optional[list[dict]]) -> str:
    """
    Summarize dyno states grouped by type.

    Args:
        dynos (Optional[list[dict]]): List of dyno dictionaries from Heroku API.

    Returns:
        str: Human-readable summary of dyno states.
    """    
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
