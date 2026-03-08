# iMessage Setup (BlueBubbles)

Drive your leashd agent sessions from iMessage via the BlueBubbles macOS server.

## Prerequisites

- leashd installed
- A Mac running macOS Sequoia (15) or later
- [BlueBubbles](https://bluebubbles.app) server installed on the Mac
- An Apple ID signed into Messages.app

## 1. Install BlueBubbles

Follow the instructions at [bluebubbles.app/install](https://bluebubbles.app/install):

1. Download and install the BlueBubbles server app on your Mac.
2. Open BlueBubbles and complete the initial setup.
3. In **Settings**, enable the **Web API** and set a password.
4. Note the server URL (e.g., `http://192.168.1.100:1234`).

### macOS Permissions

- Grant BlueBubbles **Full Disk Access** in System Settings → Privacy & Security.
- If running headless, install a LaunchAgent to keep Messages.app alive (see below).

## 2. Configure leashd

Add to `~/.leashd/config.yaml`:

```yaml
imessage:
  server_url: "http://192.168.1.100:1234"
  password: "your-bluebubbles-password"
```

Or use environment variables:

```bash
export LEASHD_IMESSAGE_SERVER_URL="http://192.168.1.100:1234"
export LEASHD_IMESSAGE_PASSWORD="your-bluebubbles-password"
```

### Install the dependency

```bash
uv pip install 'leashd[imessage]'
```

## 3. Start

```bash
leashd start
```

leashd auto-detects iMessage when `LEASHD_IMESSAGE_SERVER_URL` is set.

## 4. Verify

1. Send an iMessage to the Apple ID signed into the Mac.
2. You should see a response from the agent.

## Features

- **Typing indicators** — the bot shows typing while processing
- **File attachments** — the agent can send files via multipart upload
- **Read receipts** — supported by BlueBubbles API

## Text-Based Interactions

iMessage does not support inline buttons. Approvals, questions, and plan reviews use text-based fallback:

- **Approvals**: Reply `approve`, `reject`, or `approve-all`
- **Questions**: Reply with a number (e.g., `1`, `2`)
- **Plan reviews**: Reply with a number (1–4)

## Keeping Messages.app Alive (VM / Headless)

Some macOS setups need Messages.app poked periodically to stay responsive.

### Save the AppleScript

Save as `~/Scripts/poke-messages.scpt`:

```applescript
try
  tell application "Messages"
    if not running then
      launch
    end if
    set _chatCount to (count of chats)
  end tell
on error
end try
```

### Install a LaunchAgent

Save as `~/Library/LaunchAgents/com.user.poke-messages.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
  <dict>
    <key>Label</key>
    <string>com.user.poke-messages</string>
    <key>ProgramArguments</key>
    <array>
      <string>/bin/bash</string>
      <string>-lc</string>
      <string>/usr/bin/osascript "$HOME/Scripts/poke-messages.scpt"</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>300</integer>
    <key>StandardOutPath</key>
    <string>/tmp/poke-messages.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/poke-messages.err</string>
  </dict>
</plist>
```

Load it:

```bash
launchctl unload ~/Library/LaunchAgents/com.user.poke-messages.plist 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.user.poke-messages.plist
```

## Limitations

- macOS only — requires a Mac running BlueBubbles
- Mac must stay powered on and signed in
- No inline buttons (text-based fallback)
- No message editing or streaming
- Edit action broken on macOS 26 (Tahoe)

## Troubleshooting

- **"iMessage connector requires httpx"**: Run `uv pip install 'leashd[imessage]'`
- **"BlueBubbles server not reachable"**: Verify the server URL and that BlueBubbles is running
- **No messages received**: Check that the Web API is enabled in BlueBubbles settings
- **Typing stops working**: Verify the API password and server connectivity
- **Messages.app goes idle**: Install the poke-messages LaunchAgent (see above)
