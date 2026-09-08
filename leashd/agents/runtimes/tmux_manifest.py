"""On-disk record of a live ``claude`` pane, so a pane outlives the daemon.

The tmux server runs on leashd's private socket as a process of its own, so a
pane and the interactive ``claude`` inside it already survive a daemon restart.
What did not survive was leashd's side of the binding: the hook secret, the
pane-identity token map, the Claude session uuid, the JSONL read position and
the safety context a hook resolves to all lived only in
:class:`~leashd.agents.runtimes.tmux_session.TmuxSessionManager`'s memory. A
restarted daemon therefore could not route a surviving pane's hooks and reaped
it.

One manifest per session, written next to that session's managed ``--settings``
file, is everything needed to rebuild the in-memory ``TmuxClaudeSession`` and
re-adopt the pane. It is leashd's own state about a pane, not the pane's:
the session row in SQLite stays the source of truth for the *conversation*.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger()

MANIFEST_SUFFIX = ".pane.json"
SCHEMA_VERSION = 1
TMUX_NAME_PREFIX = "leashd_"


def session_id_from_tmux_name(name: str) -> str | None:
    """Recover the leashd session id from a ``leashd_<session_id>`` tmux name."""
    if not name.startswith(TMUX_NAME_PREFIX):
        return None
    session_id = name[len(TMUX_NAME_PREFIX) :]
    return session_id or None


@dataclass
class PaneManifest:
    """Everything a restarted daemon needs to re-adopt one live pane."""

    session_id: str
    chat_id: str
    user_id: str
    working_directory: str
    tmux_name: str
    settings_path: str
    mode: str = "default"
    task_run_id: str | None = None
    plan_origin: str | None = None
    pane_token: str | None = None
    claude_uuid: str | None = None
    native_auto_allowed: bool = False
    native_auto_active: bool = False
    native_ask_rules: list[str] = field(default_factory=list)
    applied_system_prompt: str | None = None
    append_system_prompt_path: str | None = None
    last_prompt: str = ""
    last_model: str | None = None
    goal_active: bool = False
    turn_active: bool = False
    jsonl_path: str | None = None
    jsonl_offset: int = 0
    jsonl_inode: int | None = None
    spawned_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    daemon_pid: int = field(default_factory=os.getpid)
    schema_version: int = SCHEMA_VERSION

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.spawned_at)

    def to_json(self) -> str:
        # ``default=str`` so an unexpected value never turns a best-effort
        # write into an exception on the spawn path that triggered it.
        return json.dumps(asdict(self), indent=2, sort_keys=True, default=str)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> PaneManifest | None:
        """Build a manifest from parsed JSON, or None when it is unusable.

        Unknown keys are dropped and missing optional keys fall back to their
        defaults, so a manifest written by a newer or older leashd is adopted
        on the fields both versions agree on instead of failing the whole
        adoption. A different ``schema_version`` is the one hard stop.
        """
        if payload.get("schema_version") != SCHEMA_VERSION:
            return None
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in payload.items() if k in known}
        required = ("session_id", "chat_id", "user_id", "working_directory")
        if not all(isinstance(kwargs.get(k), str) and kwargs[k] for k in required):
            return None
        kwargs.setdefault("tmux_name", f"{TMUX_NAME_PREFIX}{kwargs['session_id']}")
        kwargs.setdefault("settings_path", "")
        try:
            return cls(**kwargs)
        except TypeError:
            return None


def manifest_path(socket_dir: Path, session_id: str) -> Path:
    return socket_dir / f"{session_id}{MANIFEST_SUFFIX}"


def write_manifest(socket_dir: Path, manifest: PaneManifest) -> None:
    """Persist a manifest atomically. Best-effort — never raises."""
    manifest.updated_at = time.time()
    path = manifest_path(socket_dir, manifest.session_id)
    tmp = path.with_suffix(".tmp")
    try:
        socket_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(manifest.to_json())
        tmp.replace(path)
    except OSError as exc:
        logger.debug(
            "tmux_manifest_write_failed", session_id=manifest.session_id, error=str(exc)
        )
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def read_manifest(socket_dir: Path, session_id: str) -> PaneManifest | None:
    """Load a manifest, or None when it is absent, corrupt or foreign."""
    path = manifest_path(socket_dir, session_id)
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    manifest = PaneManifest.from_payload(payload)
    if manifest is None or manifest.session_id != session_id:
        return None
    return manifest


def delete_manifest(socket_dir: Path, session_id: str) -> None:
    with contextlib.suppress(OSError):
        manifest_path(socket_dir, session_id).unlink(missing_ok=True)


def prune_manifests(socket_dir: Path, *, keep: set[str]) -> int:
    """Delete every manifest whose session id is not in *keep*.

    Run after adoption so a pane that died while the daemon was down does not
    leave a file that a later start would try to adopt again.
    """
    removed = 0
    try:
        candidates = list(socket_dir.glob(f"*{MANIFEST_SUFFIX}"))
    except OSError:
        return 0
    for path in candidates:
        session_id = path.name[: -len(MANIFEST_SUFFIX)]
        if session_id in keep:
            continue
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return removed
