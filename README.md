# Heroku Monitoring Bot 🚀

A real-time monitoring service for Heroku applications that sends alerts to Slack when issues are detected. This bot keeps an eye on your app's health and lets you know immediately when something goes wrong!

## Features

✨ **Comprehensive Monitoring**
- 🔍 Dyno state monitoring (detects crashes and downtime)
- 🚀 Deploy tracking (correlates issues with recent releases)
- ⚙️ Config variable change detection
- 📊 Add-on health monitoring

🔔 **Smart Alerts**
- Real-time Slack notifications for dyno crashes
- Deploy notifications with user and timestamp info
- Configuration change alerts
- Customizable alert channels

💬 **Slack Commands**
- `/heroku-status` - Get instant app status overview
- Displays dyno formation, recent releases, and add-ons

## Architecture

This monitoring bot uses the [Heroku Platform API](https://devcenter.heroku.com/articles/platform-api-reference) to periodically check your app's health:

- **GET /apps/{app}** - App metadata and ownership
- **GET /apps/{app}/dynos** - Dyno states and process types
- **GET /apps/{app}/releases** - Recent deploy history
- **GET /apps/{app}/addons** - Attached add-on status
- **GET /apps/{app}/config-vars** - Configuration monitoring
- **GET /apps/{app}/formation** - Dyno scaling configuration

## Setup Instructions

### 1. Deploy to Heroku

```bash
# Clone this repository
git clone <repo-url>
cd <repo-directory>

# Create a new Heroku app (or use existing)
heroku create your-monitoring-bot

# Deploy the application
git push heroku main
```

### 2. Configure Environment Variables

Set up the required configuration on your Heroku app:

```bash
# Required: Heroku API Key
heroku config:set HEROKU_API_KEY=your_heroku_api_key

# Required: App to monitor
heroku config:set MONITORED_APP_NAME=your-app-name

# Required: Slack Bot Token (from Slack App)
heroku config:set SLACK_BOT_TOKEN=xoxb-your-slack-bot-token

# Optional: Slack channel for alerts (default: #alerts)
heroku config:set SLACK_CHANNEL=#your-channel

# Optional: Check interval in minutes (default: 5)
heroku config:set CHECK_INTERVAL_MINUTES=5
```

### 3. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Click "Create New App" → "From scratch"
3. Name it "Heroku Monitor" and select your workspace
4. Navigate to "OAuth & Permissions"
5. Add these Bot Token Scopes:
   - `chat:write` - Send messages
   - `commands` - Create slash commands
6. Install the app to your workspace
7. Copy the "Bot User OAuth Token" (starts with `xoxb-`)
8. Navigate to "Slash Commands" and create `/heroku-status`:
   - Command: `/heroku-status`
   - Request URL: `https://your-app-name.herokuapp.com/slack/command`
   - Short Description: "Get Heroku app status"
9. Invite the bot to your alert channel: `/invite @Heroku Monitor`

### 4. Get Your Heroku API Key

```bash
# Display your Heroku API key
heroku auth:token
```

Copy this token and set it as the `HEROKU_API_KEY` config var.

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `HEROKU_API_KEY` | Yes | - | Your Heroku API authorization token |
| `MONITORED_APP_NAME` | Yes | - | Name of the Heroku app to monitor |
| `SLACK_BOT_TOKEN` | Yes | - | Slack Bot User OAuth Token (xoxb-...) |
| `SLACK_CHANNEL` | No | `#alerts` | Slack channel for posting alerts |
| `CHECK_INTERVAL_MINUTES` | No | `5` | How often to check app health (minutes) |

## Usage

### Automatic Monitoring

Once deployed and configured, the bot automatically:
- Checks your app's health every N minutes (configurable)
- Sends alerts to Slack when issues are detected
- Tracks deploys and correlates them with errors
- Monitors config changes

### Manual Status Checks

In any Slack channel where the bot is present:

```
/heroku-status
```

This displays:
- App details (name, owner, region, stack)
- Current dyno status and states
- Dyno formation (process types and scaling)
- Recent releases (last 3 deploys)
- Attached add-ons and their states

You can also check specific apps:

```
/heroku-status my-other-app
```

## Alert Types

### 🚨 Dyno Crash Alert
Triggered when any dyno enters "crashed" state:
```
🚨 ALERT: Dyno Crash Detected 🚨

App: my-app
Crashed dynos:
• web.1 (web)
```

### ⚠️ Dyno Down Alert
Triggered when any dyno enters "down" state:
```
⚠️ WARNING: Dynos Down ⚠️

App: my-app
Down dynos:
• worker.1 (worker)
```

### 🚀 Deploy Notification
Triggered on new releases:
```
🚀 New Deploy Detected 🚀

App: my-app
Version: v42
Deployed by: user@example.com
Description: Deploy abc123
Time: 2025-01-15T10:30:00Z

Monitoring for issues...
```

### ⚙️ Config Change Alert
Triggered when config vars are modified:
```
⚙️ Config Vars Changed ⚙️

App: my-app
Config variables have been modified.
Review changes in the Heroku dashboard.
```

## API Endpoints

### `GET /`
Health check endpoint
- Returns service status and timestamp

### `GET /health`
Configuration status
- Shows which services are configured
- Displays monitoring settings

### `POST /slack/command`
Slack slash command handler
- Handles `/heroku-status` commands
- Returns formatted app status

## Troubleshooting

### Bot Not Sending Alerts

1. Check Slack token is valid:
   ```bash
   heroku config:get SLACK_BOT_TOKEN
   ```

2. Verify bot is in the channel:
   ```
   /invite @Heroku Monitor
   ```

3. Check application logs:
   ```bash
   heroku logs --tail
   ```

### Slash Command Not Working

1. Verify the Request URL in Slack App settings matches your app URL
2. Check that the app is deployed and running:
   ```bash
   heroku ps
   ```

### Not Detecting Issues

1. Verify the monitored app name is correct:
   ```bash
   heroku config:get MONITORED_APP_NAME
   ```

2. Check that HEROKU_API_KEY has access to the target app

3. Review scheduler logs:
   ```bash
   heroku logs --tail | grep "health check"
   ```

## Development

### Local Testing

```bash
# Install dependencies
pip install -r requirements.txt

# Set environment variables
export HEROKU_API_KEY=your_key
export MONITORED_APP_NAME=your_app
export SLACK_BOT_TOKEN=your_token
export SLACK_CHANNEL=#test-channel

# Run the application
python app.py
```

The app will be available at `http://localhost:5000`

### Project Structure

```
.
├── app.py              # Main Flask application
├── requirements.txt    # Python dependencies
├── runtime.txt         # Python version specification
├── Procfile           # Heroku process definition
└── README.md          # This file
```

## Best Practices

1. **Monitor Critical Apps Only**: Focus on production apps to reduce noise
2. **Tune Check Interval**: Balance between responsiveness and API usage
3. **Create Dedicated Channels**: Use separate Slack channels for different apps
4. **Set Up Multiple Bots**: Deploy multiple instances for different apps/teams
5. **Review Alerts Regularly**: Adjust thresholds based on false positive rates

## Contributing

Found a bug or have a feature request? Feel free to open an issue or submit a pull request!

## License

MIT License - feel free to use this for your own projects!

## Resources

- [Heroku Platform API Reference](https://devcenter.heroku.com/articles/platform-api-reference)
- [Slack API Documentation](https://api.slack.com/)
- [Flask Documentation](https://flask.palletsprojects.com/)
- [APScheduler Documentation](https://apscheduler.readthedocs.io/)

---

Built with ❤️ for the Heroku community
