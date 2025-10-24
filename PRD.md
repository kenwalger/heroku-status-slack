# PRD: Heroku Monitoring Slack Bot

**Purpose**: Developer-facing spec for generating a functional Flask app. Copy-pasteable into a code generator like Vibe.

## 1️⃣ App Overview

+ Name: Heroku Monitoring Slack Bot
+ Purpose: Monitor a Heroku app and send Slack alerts on dyno crashes, new releases, or config changes.
+ Audience: Developers (code generators or direct implementation)
+ Stack: Python 3.x, Flask, APScheduler, Requests, Slack SDK, psycopg2

## 2️⃣ Features
### 2.1 Health Monitoring

+ Track Heroku dynos: up, down, crashed
+ Detect new releases / deployments
+ Monitor configuration variable changes
+ Track add-ons availability
+ Persist previous app state in Postgres (app_state table) to survive dyno restarts

### 2.2 Slack Integration

+ Post alerts to a configurable Slack channel
+ Slash Command `/heroku-status [app_name]`
+ `/heroku-status help` shows usage instructions
+ Alerts formatted with Markdown + emojis:
  + 🚨 ALERT: Dyno Crash Detected
  + ⚠️ WARNING: Dynos Down
  + 🚀 New Deploy Detected
  + ⚙️ Config Vars Changed

### 2.3 Web Dashboard

+ Display current monitoring configuration
+ Show monitoring status: ACTIVE / INACTIVE
+ Update dynamic configuration:
  + Monitored app
  + Slack channel
  + Check interval (1–60 minutes)
+ Apply changes immediately without redeploy

## 3️⃣ Data Model

**Table**: `app_state`

| Column | Type | Notes |
| ------ | ---- | ----- | 
| `app_name` | TEXT | Primary key |
| `last_release` | TEXT | Last known release version |
| `dynos` | JSONB | Dyno status snapshot |
| `config_vars_hash` | BIGINT | Hash of config var keys |
| `updated_at` | TIMESTAMP | Defaults to `CURRENT_TIMESTAMP` |

## 4️⃣ Endpoints

| Route | Method | Purpose |
| ----- | ------ | ------- |
| `/` | GET | Web dashboard |
| `/update-config` | POST | Update monitored app, channel, interval |
| `/api/status` | GET | Return JSON status |
| `/health` | GET | Detailed health JSON |
| `/slack/command` | POST | Handle `/heroku-status` slash command |

## 5️⃣ Scheduler

+ Interval: `CHECK_INTERVAL_MINUTES`
+ Job: `check_app_health()`
  + Compare current dynos, releases, config vars to previous state
  + Post Slack alerts on change
+ Uses APScheduler (BackgroundScheduler)

## 6️⃣ Config Variables

| Key | Purpose |
| --- | ------- |
| `HEROKU_API_KEY` | Heroku API access |
| `SLACK_BOT_TOKEN` | Slack bot token |
| `MONITORED_APP_NAME` | App to monitor |
| `SLACK_CHANNEL` | Channel for alerts | 
| `CHECK_INTERVAL_MINUTES` | Scheduler interval | 
| `DATABASE_URL` | Heroku Postgres connection | 

## 7️⃣ Slack Alert Templates

| Event | Example Message |
| ----- | --------------- |
| Crashed dyno | 🚨 ALERT: Dyno Crash Detected 🚨<br>App: my-app<br>• web.1 (web) |
| Down dyno | ⚠️ WARNING: Dynos Down ⚠️<br>App: my-app<br>• worker.1 (worker) |
| New deploy | 🚀 New Deploy Detected 🚀<br>App: my-app<br>Version: v42<br>Deployed by: user@example.com |
| Config change | ⚙️ Config Vars Changed ⚙️<br>App: my-app<br>Review changes in Heroku dashboard |