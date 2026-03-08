# Signal Setup

Drive your leashd agent sessions from Signal via the signal-cli HTTP daemon.

## Prerequisites

- leashd installed
- `signal-cli` installed ([github.com/AsamK/signal-cli](https://github.com/AsamK/signal-cli))
- A phone number for the bot (dedicated number recommended)
- Java 25+ (for JVM build) or use the native build

## 1. Install signal-cli

### macOS

```bash
brew install signal-cli
```

### Linux (native build)

```bash
VERSION=$(curl -Ls -o /dev/null -w %{url_effective} https://github.com/AsamK/signal-cli/releases/latest | sed -e 's/^.*\/v//')
curl -L -O "https://github.com/AsamK/signal-cli/releases/download/v${VERSION}/signal-cli-${VERSION}-Linux-native.tar.gz"
sudo tar xf "signal-cli-${VERSION}-Linux-native.tar.gz" -C /opt
sudo ln -sf /opt/signal-cli /usr/local/bin/
signal-cli --version
```

## 2. Register or Link

### Path A: Link existing account (QR)

```bash
signal-cli link -n "leashd"
```

Scan the QR code with Signal on your phone.

### Path B: Register dedicated number (SMS)

```bash
signal-cli -a +15551234567 register
```

If captcha is required:

1. Open `https://signalcaptchas.org/registration/generate.html`
2. Complete captcha, copy the `signalcaptcha://...` URL
3. Register with captcha:

```bash
signal-cli -a +15551234567 register --captcha 'signalcaptcha://...'
signal-cli -a +15551234567 verify <CODE>
```

**Warning**: Registering a number with signal-cli can de-authenticate the main Signal app for that number. Use a dedicated bot number.

## 3. Start signal-cli Daemon

```bash
signal-cli -a +15551234567 daemon --http=localhost:8080
```

Keep this running (use systemd, tmux, or screen).

## 4. Configure leashd

Add to `~/.leashd/config.yaml`:

```yaml
signal:
  phone_number: "+15551234567"
  cli_url: "http://localhost:8080"
```

Or use environment variables:

```bash
export LEASHD_SIGNAL_PHONE_NUMBER="+15551234567"
export LEASHD_SIGNAL_CLI_URL="http://localhost:8080"
```

### Install the dependency

```bash
uv pip install 'leashd[signal]'
```

## 5. Start

```bash
leashd start
```

leashd auto-detects Signal when `LEASHD_SIGNAL_PHONE_NUMBER` is set.

## 6. Verify

1. Send a Signal message to the bot's phone number.
2. You should see a response.

## Features

- **Typing indicators** — the bot shows typing while processing
- **File attachments** — the agent can send files via base64
- **Group messages** — messages from Signal groups are routed by group ID

## Text-Based Interactions

Signal does not support inline buttons. Approvals, questions, and plan reviews use text-based fallback:

- **Approvals**: Reply `approve`, `reject`, or `approve-all`
- **Questions**: Reply with a number (e.g., `1`, `2`)
- **Plan reviews**: Reply with a number (1–4)

## Troubleshooting

- **"Signal connector requires httpx"**: Run `uv pip install 'leashd[signal]'`
- **No messages received**: Check that signal-cli daemon is running and reachable at the configured URL
- **Typing fails silently**: Normal — typing indicator errors are suppressed
- **Registration captcha expired**: Captcha tokens expire quickly; run registration immediately after copying
- **Keep signal-cli updated**: Old releases can break as Signal server APIs change
