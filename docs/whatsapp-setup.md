# WhatsApp Setup (OpenClaw Bridge)

Drive your leashd agent sessions from WhatsApp via the OpenClaw gateway bridge.

## Prerequisites

- leashd installed
- [OpenClaw](https://github.com/nichochar/openclaw) gateway installed and running
- A phone with WhatsApp linked to the gateway

## 1. Set up OpenClaw Gateway

Follow the OpenClaw docs to install and start the gateway:

```bash
openclaw onboard
```

Link your WhatsApp account:

```bash
openclaw channels login --channel whatsapp
```

This shows a QR code — scan it with WhatsApp on your phone.

## 2. Get Gateway Credentials

The gateway runs a WebSocket server (default: `ws://127.0.0.1:18789`). You need the gateway URL and an auth token.

Check your OpenClaw config for the gateway token, or set one:

```bash
openclaw config set gateway.token "your-secret-token"
```

## 3. Configure leashd

Add to `~/.leashd/config.yaml`:

```yaml
whatsapp:
  gateway_url: "ws://127.0.0.1:18789"
  gateway_token: "your-secret-token"
  phone_number: "+15551234567"
```

Or use environment variables:

```bash
export LEASHD_WHATSAPP_GATEWAY_URL="ws://127.0.0.1:18789"
export LEASHD_WHATSAPP_GATEWAY_TOKEN="your-secret-token"
export LEASHD_WHATSAPP_PHONE_NUMBER="+15551234567"
```

### Install the dependency

```bash
uv pip install 'leashd[whatsapp]'
```

## 4. Start

```bash
leashd start
```

leashd auto-detects WhatsApp when `LEASHD_WHATSAPP_GATEWAY_URL` is set.

## 5. Verify

1. Send a WhatsApp message to the linked number.
2. You should see a response from the agent.

## How It Works

leashd connects to the OpenClaw gateway via WebSocket. Incoming WhatsApp messages arrive as `chat.event` frames; outgoing replies are sent via `send` RPC calls.

## Text-Based Interactions

WhatsApp does not support inline buttons. Approvals, questions, and plan reviews use text-based fallback:

- **Approvals**: Reply `approve`, `reject`, or `approve-all`
- **Questions**: Reply with a number (e.g., `1`, `2`)
- **Plan reviews**: Reply with a number (1–4)

## Limitations

- No inline buttons (text-based fallback)
- No message editing or streaming
- No typing indicators via the gateway
- OpenClaw gateway must be running for WhatsApp to work
- File sending requires the file to be accessible via URL

## Troubleshooting

- **"WhatsApp connector requires websockets"**: Run `uv pip install 'leashd[whatsapp]'`
- **Connection refused**: Verify the OpenClaw gateway is running at the configured URL
- **Auth failed**: Check that `gateway_token` matches the OpenClaw gateway config
- **No messages**: Ensure WhatsApp is linked via `openclaw channels login --channel whatsapp`
- **Reconnection**: leashd auto-reconnects with exponential backoff on WebSocket disconnects
