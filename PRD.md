PRD: Heroku Monitoring Slack Bot Workshop
1️⃣ Overview

The goal of this workshop is to build a Heroku Monitoring Bot that:

Monitors a Heroku app’s dynos, releases, config vars, and add-ons.

Sends real-time alerts to Slack channels.

Provides a /heroku-status Slack slash command to query app health.

Offers a web dashboard for viewing current monitoring configuration and updating dynamic settings.

2️⃣ Core Features
2.1 Health Monitoring

Track Heroku dyno states (up, down, crashed).

Detect new releases and deployments.

Monitor configuration changes (config vars).

Track add-on statuses.

2.2 Slack Integration

Post alerts to a configurable Slack channel.

Support /heroku-status [app_name] slash command.

Format messages using Markdown and emojis:

Crashed dynos: 🚨 *ALERT: Dyno Crash Detected* 🚨

Downtime warning: ⚠️ *WARNING: Dynos Down* ⚠️

New deploy: 🚀 *New Deploy Detected* 🚀

Config change: ⚙️ *Config Vars Changed* ⚙️

2.3 Web Dashboard

Display current monitoring configuration: app, Slack channel, interval.

Show status: ACTIVE / INACTIVE.

Provide a form for updating dynamic configuration:

App Name (app_name) — required

Slack Channel (slack_channel) — required, must include #

Check Interval (check_interval) — required, 1–60 minutes

Submit updates via POST to /update-config.

Changes apply immediately without redeploy.

3️⃣ Technical Specifications
3.1 Stack

Python 3.x

Flask (workshop-friendly for routing and templates)

apscheduler for background health checks

requests for Heroku API calls

slack_sdk for Slack messaging

3.2 Heroku API

Base URL: https://api.heroku.com

Required endpoints:

/apps/{app_name} — app metadata

/apps/{app_name}/dynos — dyno states

/apps/{app_name}/releases — recent releases

/apps/{app_name}/addons — add-ons

/apps/{app_name}/config-vars — config vars

Authorization via HEROKU_API_KEY environment variable

3.3 Scheduler

Background job runs every check_interval minutes.

Calls health check functions for dynos, releases, and config changes.

Alerts posted to Slack on state changes.

3.4 State Management

Store previous dyno states, last release, and config var hash in-memory.

Compare current state to previous to detect changes.

Optional: save dynamic config to JSON for persistence (workshop extension).

4️⃣ Web Dashboard Visual Design

Theme: Western / rodeo vibes

Colors: browns, tans, wheat colors (#593b32, #4a312a, #fff7d4)

Typography: Georgia, Palatino, serif

Form styling: labeled inputs with clear default values

Emojis: 🤠 for headers, 🌵 in tips

Status badges: ACTIVE / INACTIVE clearly visible

5️⃣ Configuration
5.1 Environment Variables

HEROKU_API_KEY — required

SLACK_BOT_TOKEN — required

Optional:

MONITORED_APP_NAME — initial app to monitor

SLACK_CHANNEL — initial Slack channel

CHECK_INTERVAL_MINUTES — default polling interval

5.2 Dynamic Configuration

Editable via dashboard form.

Immediate effect in scheduler.

Validate check_interval between 1 and 60.

6️⃣ Alert Templates
Event	Slack Message Example
Crashed dyno	🚨 *ALERT: Dyno Crash Detected* 🚨\nApp: my-app\nCrashed dynos:\n• web.1 (web)
Down dyno	⚠️ *WARNING: Dynos Down* ⚠️\nApp: my-app\nDown dynos:\n• worker.1 (worker)
New deploy	🚀 *New Deploy Detected* 🚀\nApp: my-app\nVersion: v42\nDeployed by: user@example.com
Config change	⚙️ *Config Vars Changed* ⚙️\nApp: my-app\nReview changes in Heroku dashboard.

Use Markdown formatting for lists, bold text, and emojis.

7️⃣ Deployment Instructions

Setup

git clone <repo>

cd <repo>

python -m venv venv && source venv/bin/activate

pip install -r requirements.txt

Environment

Set required env vars: HEROKU_API_KEY, SLACK_BOT_TOKEN, etc.

Optional: MONITORED_APP_NAME, SLACK_CHANNEL, CHECK_INTERVAL_MINUTES

Run Locally

python app.py

Visit http://localhost:5000

Deploy to Heroku

heroku create

git push heroku main

Set env vars in Heroku dashboard or CLI

Success Criteria

/heroku-status Slack command returns status.

Alerts post to Slack for simulated dyno events (can stop dynos manually).

Dashboard updates dynamic configuration successfully.

8️⃣ Workshop Goals

Hands-on: Participants fill in the PRD in their coding environment (Vibe, Claude, etc.)

Output: Working Flask app integrated with Slack and Heroku API

Extension Exercises:

Persist dynamic configuration in JSON or database

Add richer Slack blocks or attachments

Support multiple apps and channels