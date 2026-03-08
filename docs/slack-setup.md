# Slack Setup

Drive your leashd agent sessions from Slack DMs with full Block Kit buttons, streaming responses, and file uploads.

## Prerequisites

- leashd installed (`uv pip install leashd` or from source)
- A Slack workspace where you can create apps

## 1. Create a Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps) and click **Create New App** → **From scratch**.
2. Name it (e.g., "leashd") and select your workspace.

### Enable Socket Mode

1. In the app settings, go to **Socket Mode** and enable it.
2. Generate an **App-Level Token** with scope `connections:write`. Copy the `xapp-...` token.

### Bot Token Scopes

Go to **OAuth & Permissions** → **Bot Token Scopes** and add:

- `chat:write` — send messages
- `files:write` — upload files
- `reactions:write` — activity indicators
- `im:history` — read DM history
- `im:read` — view DM info
- `im:write` — open DMs

### Event Subscriptions

Go to **Event Subscriptions** → **Subscribe to bot events** and add:

- `message.im` — DM messages

### App Home

Go to **App Home** and enable the **Messages Tab**.

### Install

Click **Install to Workspace** and copy the **Bot User OAuth Token** (`xoxb-...`).

## 2. Configure leashd

Add to `~/.leashd/config.yaml`:

```yaml
slack:
  bot_token: "xoxb-your-bot-token"
  app_token: "xapp-your-app-token"
```

Or use environment variables:

```bash
export LEASHD_SLACK_BOT_TOKEN="xoxb-your-bot-token"
export LEASHD_SLACK_APP_TOKEN="xapp-your-app-token"
```

### Install the dependency

```bash
uv pip install 'leashd[slack]'
```

## 3. Start

```bash
leashd start
```

leashd auto-detects the Slack connector when `LEASHD_SLACK_BOT_TOKEN` is set.

To force Slack even if other tokens are present:

```yaml
connector: slack
```

## 4. Verify

1. Open Slack, find the bot in your DMs.
2. Send a message — you should see a response.
3. Try `/status` to check engine state.

## Features

- **Block Kit buttons** for approvals, questions, and plan reviews
- **Streaming** — responses stream into an editable message
- **File uploads** — agent can send files directly to the channel
- **Activity indicators** — live status messages during tool execution
- **Slash commands** — `/dir`, `/plan`, `/edit`, `/git`, etc.

## Troubleshooting

- **"Slack connector requires slack-bolt"**: Run `uv pip install 'leashd[slack]'`
- **Connection timeout**: Verify Socket Mode is enabled and the `xapp-` token has `connections:write`
- **No messages received**: Check Event Subscriptions include `message.im` and the Messages Tab is enabled
- **Permission errors**: Ensure all required bot scopes are added and the app is reinstalled after scope changes
