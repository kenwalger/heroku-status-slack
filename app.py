"""
Heroku Monitoring Bot - Flask Application

Main Flask app that provides web UI and API endpoints for monitoring Heroku apps.
"""
import os
import logging
import threading
import requests
import psycopg2
from datetime import datetime, timezone
from flask import Flask, request, jsonify, render_template, redirect, url_for
from typing import Dict, Any, List, Optional

from config import HEROKU_API_KEY, SLACK_BOT_TOKEN, DATABASE_URL, dynamic_config
from database import get_db_connection
from heroku_client import HerokuAPIClient
from slack_integration import send_slack_message
from health_checker import check_app_health
from scheduler import scheduler, restart_scheduler, initialize_scheduler

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Initialize clients
heroku_client = HerokuAPIClient(HEROKU_API_KEY) if HEROKU_API_KEY else None

# Initialize scheduler
initialize_scheduler(heroku_client)


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
        restart_scheduler(heroku_client)

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


@app.route('/test-db')
def test_db():
    """
    Test database connection (debugging route).

    Returns:
        Flask Response: JSON with database status.
    """
    try:
        conn = psycopg2.connect(DATABASE_URL, sslmode='require')
        with conn.cursor() as cur:
            cur.execute("SELECT NOW()")
            result = cur.fetchone()
        return jsonify({'status': 'ok', 'db_time': str(result[0])})
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)})
    finally:
        if conn:
            conn.close()


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


def format_dyno_status(dynos: Optional[List[dict]]) -> str:
    """
    Summarize dyno states grouped by type.

    Args:
        dynos (Optional[List[dict]]): List of dyno dictionaries from Heroku API.

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
