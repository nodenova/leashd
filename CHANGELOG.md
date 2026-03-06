# Changelog

## [0.6.0] - 2026-03-05
- **added**: API spec discovery — auto-scans for `.http`, `.rest`, `openapi.yaml/json`, `swagger.yaml/json` and injects them into the test prompt so the agent uses real endpoints instead of guessing
- **added**: `api_specs` field in `.leashd/test.yaml` for explicit API spec file paths (overrides auto-discovery)
- **added**: Test session context injection on resume — reads `.leashd/test-session.md` and prepends it to the prompt so the agent resumes from prior progress
- **added**: Docker commands (`docker compose`, `docker build`, `docker run`, etc.) auto-approved in both test and implement modes
- **changed**: Phase 2 (Server Startup) now mentions Docker — checks for `docker-compose.yml`/`compose.yaml` before falling back to dev server
- **changed**: Phase 5 (Backend Verification) now references discovered API spec files as authoritative endpoint source
- **changed**: `leashd ws add` merges directories into existing workspaces instead of replacing
- **added**: `leashd ws remove <name> <dir...>` removes specific directories from a workspace
- **added**: `leashd restart` command (stop + start)
- **added**: Live config reload via SIGHUP — `add-dir`, `remove-dir`, and workspace changes propagate to running daemon without restart; new `leashd reload` command
- **changed**: Task pipeline simplified from 11 phases to 3 core phases (plan→implement→test) with dynamic phase insertion based on task keywords
- **added**: Implement phase now includes mandatory lint/format, type check, and unit test verification before completion
- **added**: Test phase uses agentic testing via TestRunnerPlugin (browser tools, multi-phase workflow, self-healing) instead of plain `uv run pytest`
- **added**: `IMPLEMENT_BASH_AUTO_APPROVE` constant — auto-approves common dev tool bash commands during implement phase
- **added**: `_build_phase_pipeline()` dynamically inserts `explore` and `validate_plan` phases based on task description keywords
- **added**: `phase_pipeline` field on `TaskRun` for per-task phase customization with SQLite persistence and migration
- **changed**: Auto-approvals are now cleared between phase transitions to prevent stale approvals leaking across phases
- **changed**: `_merge_project_config` renamed to `merge_project_config` (public API for task orchestrator reuse)
- **fixed**: `/plan` command now always routes to human review even when `auto_plan=True` — AI reviewer is only used for auto-initiated plans
- **fixed**: Write/Edit auto-approve no longer enabled prematurely inside `can_use_tool` while session is still in plan mode — deferred to `_exit_plan_mode`
- **fixed**: TaskOrchestrator `validate_spec` and `validate_plan` phases now use "plan" mode instead of "auto" to prevent unrestricted file writes during validation
- **fixed**: ExitPlanMode is now denied for task-orchestrated sessions — phase transitions are managed by the orchestrator
- **added**: `plan_origin` field on Session tracks how plan mode was entered (`"user"`, `"auto"`, or `"task"`)
- **added**: `/stop` command — cancels all ongoing work (agent, autonomous task, loop) without resetting session
- **changed**: `/clear` now also cancels autonomous tasks and autonomous loop before resetting
- **fixed**: `SessionManager.reset()` now clears `mode_instruction` and `task_run_id`
- **added**: `cost` field in `session.completed` event data for task cost tracking
- **added**: SQLite persistence for `mode`, `mode_instruction`, `task_run_id` session fields
- **changed**: Autonomous setup guide and quick start now use CLI commands (`leashd autonomous enable/setup`) instead of `.env` files as the primary configuration path
- **added**: `leashd autonomous` CLI subcommand — `setup`, `enable`, `disable`, `show` for managing autonomous mode config
- **added**: Autonomous mode section in `leashd init` setup wizard with guided prompts
- **added**: `autonomous` YAML config section in `~/.leashd/config.yaml` with env var bridging for pydantic-settings
- **added**: `resolve_policy_name()` resolves short policy names (e.g. `autonomous`) to full paths
- **added**: `leashd config` now displays autonomous mode status
- **added**: Task orchestrator plugin — multi-phase autonomous workflow (spec→explore→validate→plan→implement→test→PR) with crash recovery, SQLite persistence, retry loops, and per-chat concurrency
- **added**: AI auto-approver plugin — Claude Haiku replaces human approval taps for `require_approval` tools
- **added**: Autonomous loop plugin — post-task test-and-retry with `/test` integration
- **added**: Autonomous policy (`autonomous.yaml`) for minimal-interruption operation
- **added**: Compound command classification prevents policy evasion via `&&`/`||`/`;`
- **added**: `approver_type` field in audit log entries
- **added**: `session_mode` field in audit tool-attempt entries
- **added**: Load CLAUDE.md from all workspace directories via SDK `add_dirs`
- **added**: `session.completed` event emitted after each agent run for plugin integration
- **added**: Auto-plan step — AI plan review via Claude Haiku replaces Telegram approval when `auto_plan=True`
- **added**: Auto-PR creation — after tests pass in autonomous mode, agent creates a PR when `auto_pr=True`
- **added**: `gh-cli-pr` policy rule in `autonomous.yaml` for GitHub CLI PR operations
- **added**: Task orchestrator documentation across all docs (architecture, plugins, events, configuration, engine)


## [0.5.0] - 2026-03-02
- **added**: Daemon mode — `leashd` now runs in the background by default; `leashd stop` for graceful shutdown, `leashd status` to check, `leashd start -f` for foreground
- **added**: CLI subcommands — `leashd init`, `add-dir`, `remove-dir`, `dirs`, `config` for managing configuration without manual `.env` editing
- **added**: First-time setup wizard — guided flow prompts for approved directories and optional Telegram credentials on first run
- **added**: Global config at `~/.leashd/config.yaml` — persistent base-layer config that env vars and `.env` files override
- **added**: `leashd ws` commands for workspace management (`add`, `remove`, `show`, `list`)
- **changed**: Broadened Python support from 3.13+ to 3.10+ (replaced `datetime.UTC` with `datetime.timezone.utc`, added CI matrix for 3.10-3.13)

## [0.4.0] - 2026-03-01
- **changed**: Rebranded from "tether" to "leashd" — package name, env var prefix (`LEASHD_*`), config dir (`.leashd/`), all imports, CLI entry point, and documentation
- **added**: Apache 2.0 license
- **added**: PyPI package metadata (classifiers, URLs, keywords, `py.typed` marker)
- **added**: `/workspace` (alias `/ws`) — group related repos under named workspaces for multi-repo context. YAML config in `.leashd/workspaces.yaml`, inline keyboard buttons, and workspace-aware system prompt injection

## [0.3.0] - 2026-02-26
- **added**: `/git merge <branch>` — AI-assisted conflict resolution with auto-resolve/abort buttons and 4-phase merge workflow
- **added**: `/test` command — 9-phase agent-driven test workflow with structured args (`--url`, `--framework`, `--dir`, `--no-e2e`, `--no-unit`, `--no-backend`), project config (`.leashd/test.yaml`), write-ahead crash recovery, and context persistence across sessions
- **added**: `/plan <text>` and `/edit <text>` — switch mode and start agent in one step
- **added**: `/dir` inline keyboard buttons for one-tap directory switching
- **added**: Message interrupt — inline buttons to interrupt or wait during agent execution instead of silent queuing
- **added**: `dev-tools.yaml` policy overlay — auto-allows common dev commands (package managers, linters, test runners)
- **added**: Auto-delete transient messages (interrupt prompts, ack messages, completion notices)
- **fixed**: Git callback buttons now auto-delete after action completes instead of persisting as stale UI
- **fixed**: Plan approval messages (content + buttons) now fully cleaned up after user decision, with brief ack for proceed actions
- **fixed**: Agent resilience — exponential backoff on retries, auto-retry for transient API errors, 30-minute execution timeout, session continuity on timeout, and pending messages preserved on transient errors
- **fixed**: Playwright MCP tools now available when agent works in repos without their own `.mcp.json`

## [0.2.1] - 2026-02-23
- **added**: Network resilience for Telegram connector — exponential-backoff retries on `NetworkError`/`TimedOut` for startup and send operations
- **fixed**: Streaming freezes on long responses — overflow now finalizes current message and chains into a new one instead of silently truncating at 4000 chars
- **fixed**: Sub-agent permission inheritance — map session modes to SDK `PermissionMode` so Task-spawned sub-agents can write/edit files in auto mode

## [0.2.0] - 2026-02-23
- **added**: Git integration — full `/git` command suite accessible from Telegram with inline action buttons (`status`, `branch`, `checkout`, `diff`, `log`, `add`, `commit`, `push`, `pull`), auto-generated commit messages, fuzzy branch matching, and audit logging

## [0.1.0] - 2026-02-22

- Initial release
