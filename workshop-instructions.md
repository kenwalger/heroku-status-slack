# Workshop Guide: Heroku+Slack Monitoring App

### Prerequisites

1. Heroku Account
Each attendee must have their own Heroku account.

2. Heroku CLI Installed
[Download](https://devcenter.heroku.com/articles/heroku-cli) & install.

Verify installation:

```bash
heroku --version
```

3. Slack Workspace
Each attendee needs access to a Slack workspace where they can create slash commands and channels.

4. Git Installed
To push the app to Heroku:
```bash
git --version
```

5. Browser
To manage config vars in the Heroku UI.

## Step 1: Clone the Workshop App

```bash
git clone <REPO_URL>
cd heroku-status
```

> Replace <REPO_URL> with the Git URL provided by the workshop.

## Step 2: Create a Heroku App

```bash
heroku login
heroku create <lastname>-heroku-status
```

> Replace <lastname> with your last name to ensure unique app names.

## Step 3: Set Required Config Vars

You can set config vars either in the Heroku Dashboard (UI) or using the Heroku CLI.

### 🧭 Option 1: Using the Heroku Dashboard

+ Go to your app in the Heroku dashboard.
+ Click **Settings → Reveal Config Vars**.
+ Add the following key/value pairs:

| Key | Value | Notes |
| --- | ----- | ----- |
| `HEROKU_API_KEY` | Your personal Heroku API Key | Get it via `heroku auth:token` |
| `SLACK_BOT_TOKEN` | Slack Bot User OAuth Token | Create in Slack App → OAuth & Permissions |
| `MONITORED_APP_NAME` | Name of the app you want to monitor | Typically your own Heroku app |
| `SLACK_CHANNEL` | Slack channel for alerts | e.g., `#<lastname>-heroku-alerts` |
| `CHECK_INTERVAL_MINUTES` | How often to poll Heroku | Default `5`, range `1–60` |

### 💻 Option 2: Using the Heroku CLI

You can set the same config vars directly from your terminal:

```bash
heroku config:set HEROKU_API_KEY=<your_api_key>
heroku config:set SLACK_BOT_TOKEN=<your_slack_bot_token>
heroku config:set MONITORED_APP_NAME=<your_app_name>
heroku config:set SLACK_CHANNEL=#<lastname>-heroku-alerts
heroku config:set CHECK_INTERVAL_MINUTES=5
```

> 🔐 Note: The `heroku config:set` command securely stores values — they’re never exposed in your code or Git history.

> Tip: Config vars replace .env — no local .env file needed.

## Step 4: Add Postgress Database

```bash
heroku addons:create heroku-postgresl:essential-0 --app <your_app_name>
```

> Note: This will automatically create a `DATABASE_URL` config variable for your application.

## Step 5: Create the `app_state` Table

```bash
heroku pg:psql --app <your-app-name>
CREATE TABLE app_state (
    app_name TEXT PRIMARY KEY,
    last_release TEXT,
    dynos JSONB,
    config_vars_hash BIGINT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Step 6: Deploy to Heroku

```bash
git push heroku main
```

Heroku will build and start your Flask app automatically.

## Step 7: Configure Slack Slash Command

1. Go to your Slack workspace **→ Apps → Manage → Create App**
2. Add a **Slash Command**:
  + Command: `/heroku-status` (or `/lastname-heroku-status` for uniqueness)
  + Request URL: `https://<your-heroku-app>.herokuapp.com/slack/command`
3. Install the Slack app to your workspace and note the **Bot Token**.

## Step 8: Verify Setup

Try Slack:

+ Help:

  ```bash
  /heroku-status help
  ```

Returns usage instructions.

+ Status:

  ```bash
  /heroku-status
  ```

Returns current monitored app info: dynos, releases, add-ons, and config changes.

> Your slash command now interacts directly with your Heroku app, not a local server.

### Notes & Tips

+ Each attendee runs their own Heroku instance. No shared API key is needed.
+ Unique Slack channels/slash commands prevent collisions in a shared workspace.
+ All polling/alerts run asynchronously on Heroku — no local Flask server required.
+ Adjust `CHECK_INTERVAL_MINUTES` in the Heroku config vars for more or less frequent checks.