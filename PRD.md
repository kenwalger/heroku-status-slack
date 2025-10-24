# 🧾 PRD: Heroku Monitoring Slack Bot Workshop

## 1️⃣ Overview

The goal of this workshop is to build a Heroku Monitoring Bot that:

+ Monitors a Heroku app’s dynos, releases, config vars, and add-ons
+ Sends real-time alerts to a Slack channel
+ Provides a `/heroku-status [app_name]` Slash Command to query app health
+ Includes a **web dashboard** for viewing and updating configuration dynamically

Each participant will deploy their own instance of the bot to Heroku, using their personal Heroku account and Slack app.

## 2️⃣ Core Features
### 2.1 Health Monitoring

+ Track Heroku dyno states (up, down, crashed)
+ Detect new releases and deployments
+ Monitor configuration variable changes
+ Track add-on availability
+ Persist previous dyno states, last release, and config var hash in Heroku Postgres (`app_state`table) so alerts trigger reliably even after dyno restarts.

### 2.2 Slack Integration

+ Post alerts to a configurable Slack channel
+ Support /heroku-status [app_name] command
+ Provide /heroku-status help for usage guidance
+ Format messages using Markdown and emojis for clarity:
  + 🚨 ALERT: Dyno Crash Detected
  + ⚠️ WARNING: Dynos Down
  + 🚀 New Deploy Detected
  + ⚙️ Config Vars Changed

### 2.3 Web Dashboard

+ Display current monitoring configuration
+ Show status: ACTIVE / INACTIVE
+ Provide form to update dynamic configuration:
  + App Name (required)
  + Slack Channel (required, e.g., `#alerts`)
  + Check Interval (1–60 minutes)
+ Apply changes immediately (no redeploy needed)

## 3️⃣ Technical Specifications
### 3.1 Stack

+ Python 3.x
+ Flask — lightweight, workshop-friendly framework
+ APScheduler — background health checks
+ Requests — Heroku API calls
+ Slack SDK — Slack message and command handling

### 3.2 Heroku API

Base URL: `https://api.heroku.com`
Endpoints used:

+ `/apps/{app_name}` — metadata
+ `/apps/{app_name}/dynos` — dyno states
+ `/apps/{app_name}/releases` — releases
+ `/apps/{app_name}/addons` — add-ons
+ `/apps/{app_name}/config-vars` — config vars

Authorization: `HEROKU_API_KEY` environment variable

### 3.3 Scheduler

+ Runs every `CHECK_INTERVAL_MINUTES`
+ Checks for dyno status, new releases, config var changes
+ Posts Slack alerts on change detection

### 3.4 State Management

+ Tracks previous dyno states, last release version, config var hash
+ Compares current state to detect deltas
+ Database: Heroku Postgres used to persist app monitoring state. Table: app_state with columns: `app_name` (PK), `last_release`, `dynos` (JSONB), `config_vars_hash`, `updated_at`.

## 4️⃣ Web Dashboard Visual Design

Theme: Western / Rodeo
Colors: #593b32, #4a312a, #fff7d4
Fonts: Georgia, Palatino, or other serif
Vibe: Friendly, rustic, functional
Visuals: 🤠 in headers, 🌵 for tips
Status badges: ACTIVE / INACTIVE prominently displayed

Inline CSS or linked /static/css/dashboard.css for styling consistency.

## 5️⃣ Configuration
### 5.1 Required Config Vars
| Key | Description |
| --- | ----------- |
| `HEROKU_API_KEY` | Your Heroku API key (`heroku auth:token`) |
| `SLACK_BOT_TOKEN` | Your Slack app’s Bot User OAuth Token | 
| `MONITORED_APP_NAME` | App name to monitor | 
| `SLACK_CHANNEL` | Slack channel for alerts |
| `CHECK_INTERVAL_MINUTES` | Polling interval (default: 5) |
| `DATABASE_URL` | Heroku provides automatically when Postgres add-on is provisioned. |

### 5.2 Setting Config Vars
#### Option 1: Using the Heroku Dashboard

1. Go to your app in the Heroku Dashboard
2. Click Settings → Reveal Config Vars
3. Add the required key/value pairs listed above

#### Option 2: Using the Heroku CLI

```bash
heroku config:set HEROKU_API_KEY=<your_api_key>
heroku config:set SLACK_BOT_TOKEN=<your_slack_token>
heroku config:set MONITORED_APP_NAME=<your_app_name>
heroku config:set SLACK_CHANNEL=#<lastname>-heroku-alerts
heroku config:set CHECK_INTERVAL_MINUTES=5
```

> 🔐 These values are securely stored on Heroku and never committed to Git.

## 6️⃣ Alert Templates

| Event | Slack Message Example |
| ----- | --------------------- |
| Crashed dyno | 🚨 ALERT: Dyno Crash Detected 🚨
App: my-app
• web.1 (web) |
| Down dyno | ⚠️ WARNING: Dynos Down ⚠️
App: my-app
• worker.1 (worker) | 
| New deploy | 🚀 New Deploy Detected 🚀
App: my-app
Version: v42 
Deployed by: `user@example.com` |
| Config change | ⚙️ Config Vars Changed ⚙️
App: my-app
Review changes in Heroku dashboard |

Use Markdown and emojis for readability.

## 7️⃣ Deployment Instructions

### Step 1: Fork or clone the repo

```bash
git clone <repo>
cd heroku-monitoring-bot
```

### Step 2: Create your Heroku app

```bash
heroku create <lastname>-heroku-status
```

### Step 3: Provision a Heroku Postgress add-on:

```bash
heroku addons:create heroku-postgresql:essential-0 --app YOUR_APP_NAME
```

### Step 4: Create the `app_state` table

```bash
heroku pg:psql --app YOUR_APP_NAME
CREATE TABLE app_state (
    app_name TEXT PRIMARY KEY,
    last_release TEXT,
    dynos JSONB,
    config_vars_hash BIGINT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

> Note that `DATABASE_URL` is automatically set by Heroku.

### Step 5: Set config vars

Using the Dashboard or CLI (see section 5.2)

### Step 6: Deploy to Heroku

```bash
git push heroku main
```

### Step 7: Add the Slack Slash Command

+ Go to your Slack App → Slash Commands
+ Create `/heroku-status`
+ Set Request URL to: `https://<your-heroku-app>.herokuapp.com/slack/command`

## 8️⃣ Success Criteria

✅ /heroku-status responds with app info in Slack
✅ /heroku-status help shows usage instructions
✅ Alerts appear in the configured Slack channel on:
  + Dyno crashes
  + New releases
  + Config changes
✅ Dashboard reflects and updates monitoring configuration

## 9️⃣ Workshop Goals

+ Deploy your own Heroku Monitoring Bot
+ Connect it to Slack for real-time notifications
+ Use `/heroku-status` to inspect your Heroku app
+ Update monitoring configuration from the dashboard
+ Persist dynamic configuration and app state in Postgres.
+ Observe that Slack alerts are now sent when changes occur even after dyno restarts.

### Extension Ideas:

+ Persist dynamic config to JSON or Postgres
+ Add richer Slack blocks or attachments
+ Monitor multiple apps
+ Add `/heroku-releases` or `/heroku-config` commands