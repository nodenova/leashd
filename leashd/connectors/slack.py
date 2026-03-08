"""Slack connector — Socket Mode with Block Kit buttons and streaming."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import structlog

from leashd.connectors._shared import (
    APPROVAL_PREFIX,
    INTERACTION_PREFIX,
    INTERRUPT_PREFIX,
    activity_label,
    retry_on_error,
    split_text,
)
from leashd.connectors.base import BaseConnector, InlineButton

try:
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
    from slack_bolt.async_app import AsyncApp
except ImportError as exc:
    raise ImportError(
        "Slack connector requires slack-bolt. "
        "Install with: uv pip install 'leashd[slack]'"
    ) from exc

logger = structlog.get_logger()

_MAX_MESSAGE_LENGTH = 4000
_CLEANUP_DELAY = 4.0


class SlackConnector(BaseConnector):
    """Slack connector using Socket Mode and Block Kit interactive elements."""

    def __init__(self, bot_token: str, app_token: str) -> None:
        super().__init__()
        self._bot_token = bot_token
        self._app_token = app_token
        self._app: AsyncApp | None = None
        self._handler: AsyncSocketModeHandler | None = None
        self._cleanup_tasks: set[asyncio.Task[None]] = set()

        self._activity_message_id: dict[str, str] = {}
        self._activity_last_text: dict[str, str] = {}
        self._plan_message_ids: dict[str, list[str]] = {}
        self._question_message_ids: dict[str, str] = {}
        self._approval_tool_names: dict[str, str] = {}

    async def start(self) -> None:
        self._app = AsyncApp(token=self._bot_token)

        @self._app.event("message")  # type: ignore
        async def _on_message(event: dict[str, Any], say: Any) -> None:  # noqa: ARG001
            await self._handle_message_event(event)

        @self._app.action({"action_id": ".*"})  # type: ignore
        async def _on_action(ack: Any, body: dict[str, Any]) -> None:
            await ack()
            await self._handle_action(body)

        self._handler = AsyncSocketModeHandler(self._app, self._app_token)
        await self._handler.start_async()
        logger.info("slack_connector_started")

    async def stop(self) -> None:
        if self._handler:
            await self._handler.close_async()
        logger.info("slack_connector_stopped")

    # --- Messaging ---

    async def send_message(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[InlineButton]] | None = None,
    ) -> None:
        if not self._app:
            return
        chunks = split_text(text, _MAX_MESSAGE_LENGTH)
        try:
            for i, chunk in enumerate(chunks):
                is_last = i == len(chunks) - 1
                blocks = (
                    _text_with_buttons(chunk, buttons) if is_last and buttons else None
                )
                kwargs: dict[str, Any] = {"channel": chat_id, "text": chunk}
                if blocks:
                    kwargs["blocks"] = blocks
                await retry_on_error(
                    lambda kw=kwargs: self._app.client.chat_postMessage(**kw),  # type: ignore
                    operation="slack_send_message",
                )
        except Exception:
            logger.exception("slack_send_message_failed", chat_id=chat_id)

    async def send_message_with_id(self, chat_id: str, text: str) -> str | None:
        if not self._app:
            return None
        truncated = text[:_MAX_MESSAGE_LENGTH]
        try:
            resp = await retry_on_error(
                lambda: self._app.client.chat_postMessage(  # type: ignore
                    channel=chat_id, text=truncated
                ),
                operation="slack_send_message_with_id",
            )
            return str(resp["ts"])
        except Exception:
            logger.exception("slack_send_message_with_id_failed", chat_id=chat_id)
            return None

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        if not self._app:
            return
        truncated = text[:_MAX_MESSAGE_LENGTH]
        try:
            await retry_on_error(
                lambda: self._app.client.chat_update(  # type: ignore
                    channel=chat_id, ts=message_id, text=truncated
                ),
                operation="slack_edit_message",
            )
        except Exception:
            logger.debug("slack_edit_message_failed", chat_id=chat_id)

    async def delete_message(self, chat_id: str, message_id: str) -> None:
        if not self._app:
            return
        try:
            await self._app.client.chat_delete(channel=chat_id, ts=message_id)
        except Exception:
            logger.debug("slack_delete_message_failed", chat_id=chat_id)

    def schedule_message_cleanup(
        self, chat_id: str, message_id: str, *, delay: float = _CLEANUP_DELAY
    ) -> None:
        task = asyncio.create_task(self._delayed_delete(chat_id, message_id, delay))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    async def _delayed_delete(
        self, chat_id: str, message_id: str, delay: float
    ) -> None:
        await asyncio.sleep(delay)
        await self.delete_message(chat_id, message_id)

    # --- Typing ---

    async def send_typing_indicator(self, chat_id: str) -> None:
        """No native bot typing in Slack — no-op."""

    # --- Files ---

    async def send_file(self, chat_id: str, file_path: str) -> None:
        if not self._app:
            return
        try:
            path = Path(file_path)
            await retry_on_error(
                lambda: self._app.client.files_upload_v2(  # type: ignore
                    channel=chat_id,
                    file=str(path),
                    filename=path.name,
                ),
                operation="slack_send_file",
            )
        except Exception:
            logger.exception("slack_send_file_failed", chat_id=chat_id)

    # --- Approvals ---

    async def request_approval(
        self, chat_id: str, approval_id: str, description: str, tool_name: str = ""
    ) -> str | None:
        if not self._app:
            return None

        if tool_name.startswith("Bash::"):
            cmd = tool_name.split("::", 1)[1]
            approve_all_label = f"Approve all '{cmd}' cmds"
        elif tool_name:
            approve_all_label = f"Approve all {tool_name}"
        else:
            approve_all_label = "Approve all in session"

        self._approval_tool_names[approval_id] = tool_name

        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": description[:3000]}},
            {
                "type": "actions",
                "elements": [
                    _button("Approve", f"{APPROVAL_PREFIX}yes:{approval_id}"),
                    _button("Reject", f"{APPROVAL_PREFIX}no:{approval_id}"),
                    _button(approve_all_label, f"{APPROVAL_PREFIX}all:{approval_id}"),
                ],
            },
        ]
        try:
            resp = await retry_on_error(
                lambda: self._app.client.chat_postMessage(  # type: ignore
                    channel=chat_id,
                    text=description[:_MAX_MESSAGE_LENGTH],
                    blocks=blocks,
                ),
                operation="slack_request_approval",
            )
            ts = str(resp["ts"])
            logger.info(
                "slack_approval_requested", chat_id=chat_id, approval_id=approval_id
            )
            return ts
        except Exception:
            logger.exception("slack_request_approval_failed", chat_id=chat_id)
            return None

    # --- Questions ---

    async def send_question(
        self,
        chat_id: str,
        interaction_id: str,
        question_text: str,
        header: str,
        options: list[dict[str, str]],
    ) -> None:
        if not self._app:
            return
        text = f"*{header}*\n{question_text}" if header else question_text
        elements = [
            _button(
                opt.get("label", ""),
                f"{INTERACTION_PREFIX}{interaction_id}:{opt.get('label', '')}",
            )
            for opt in options
        ]
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "_Or reply with a message for a custom answer._",
                },
            },
            {"type": "actions", "elements": elements[:25]},
        ]
        try:
            resp = await retry_on_error(
                lambda: self._app.client.chat_postMessage(  # type: ignore
                    channel=chat_id,
                    text=text,
                    blocks=blocks,
                ),
                operation="slack_send_question",
            )
            ts = str(resp["ts"])
            self._question_message_ids[chat_id] = ts
        except Exception:
            logger.exception("slack_send_question_failed", chat_id=chat_id)

    async def clear_question_message(self, chat_id: str) -> None:
        ts = self._question_message_ids.pop(chat_id, None)
        if ts:
            await self.delete_message(chat_id, ts)

    # --- Plan review ---

    async def send_plan_messages(self, chat_id: str, plan_text: str) -> list[str]:
        ids: list[str] = []
        chunks = split_text(plan_text, _MAX_MESSAGE_LENGTH)
        for chunk in chunks:
            ts = await self.send_message_with_id(chat_id, chunk)
            if ts:
                ids.append(ts)
        self._plan_message_ids[chat_id] = ids
        return ids

    async def send_plan_review(
        self, chat_id: str, interaction_id: str, description: str
    ) -> None:
        await self.clear_activity(chat_id)
        plan_ids = await self.send_plan_messages(chat_id, description)

        review_header = "Claude has written up a plan. Proceed with implementation?"
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": review_header}},
            {
                "type": "actions",
                "elements": [
                    _button(
                        "Yes, clear context + auto-edits",
                        f"{INTERACTION_PREFIX}{interaction_id}:clean_edit",
                    ),
                    _button(
                        "Yes, auto-accept edits",
                        f"{INTERACTION_PREFIX}{interaction_id}:edit",
                    ),
                    _button(
                        "Yes, manually approve",
                        f"{INTERACTION_PREFIX}{interaction_id}:default",
                    ),
                    _button(
                        "Adjust the plan",
                        f"{INTERACTION_PREFIX}{interaction_id}:adjust",
                    ),
                ],
            },
        ]
        try:
            resp = await retry_on_error(
                lambda: self._app.client.chat_postMessage(  # type: ignore
                    channel=chat_id,
                    text=review_header,
                    blocks=blocks,
                ),
                operation="slack_send_plan_review",
            )
            ts = str(resp["ts"])
            plan_ids.append(ts)
        except Exception:
            logger.exception("slack_send_plan_review_failed", chat_id=chat_id)

        self._plan_message_ids[chat_id] = plan_ids

    async def clear_plan_messages(self, chat_id: str) -> None:
        plan_ids = self._plan_message_ids.pop(chat_id, [])
        for ts in plan_ids:
            await self.delete_message(chat_id, ts)

    async def delete_messages(self, chat_id: str, message_ids: list[str]) -> None:
        for ts in message_ids:
            await self.delete_message(chat_id, ts)
        self._plan_message_ids.pop(chat_id, None)

    # --- Activity ---

    async def send_activity(
        self, chat_id: str, tool_name: str, description: str
    ) -> str | None:
        if not self._app:
            return None
        emoji, verb = activity_label(tool_name, description)
        text = f"{emoji} {verb}: {description}"

        existing = self._activity_message_id.get(chat_id)
        if existing:
            if self._activity_last_text.get(chat_id) == text:
                return existing
            try:
                await self._app.client.chat_update(
                    channel=chat_id, ts=existing, text=text
                )
                self._activity_last_text[chat_id] = text
                return existing
            except Exception:
                self._activity_message_id.pop(chat_id, None)
                self._activity_last_text.pop(chat_id, None)

        ts = await self.send_message_with_id(chat_id, text)
        if ts:
            self._activity_message_id[chat_id] = ts
            self._activity_last_text[chat_id] = text
        return ts

    async def clear_activity(self, chat_id: str) -> None:
        ts = self._activity_message_id.pop(chat_id, None)
        self._activity_last_text.pop(chat_id, None)
        if ts:
            await self.delete_message(chat_id, ts)

    # --- Interrupts ---

    async def send_interrupt_prompt(
        self, chat_id: str, interrupt_id: str, message_preview: str
    ) -> str | None:
        if not self._app:
            return None
        preview = message_preview[:200]
        text = f'💬 New message received:\n"{preview}"\n\nInterrupt current task?'
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "actions",
                "elements": [
                    _button("Send Now 📩", f"{INTERRUPT_PREFIX}send:{interrupt_id}"),
                    _button("Wait ⏳", f"{INTERRUPT_PREFIX}wait:{interrupt_id}"),
                ],
            },
        ]
        try:
            resp = await retry_on_error(
                lambda: self._app.client.chat_postMessage(  # type: ignore
                    channel=chat_id, text=text, blocks=blocks
                ),
                operation="slack_send_interrupt",
            )
            return str(resp["ts"])
        except Exception:
            logger.exception("slack_send_interrupt_failed", chat_id=chat_id)
            return None

    # --- Inbound event handling ---

    async def _handle_message_event(self, event: dict[str, Any]) -> None:
        # Ignore bot messages and message_changed subtypes
        if event.get("bot_id") or event.get("subtype"):
            return

        user_id = event.get("user", "")
        text = event.get("text", "")
        chat_id = event.get("channel", "")

        if not user_id or not text or not chat_id:
            return

        # Route /commands
        if text.startswith("/") and self._command_handler:
            parts = text.split(None, 1)
            command = parts[0].lstrip("/")
            args = parts[1] if len(parts) > 1 else ""
            try:
                result = await self._command_handler(user_id, command, args, chat_id)
                if result:
                    await self.send_message(chat_id, result)
            except Exception:
                logger.exception("slack_command_handler_error", chat_id=chat_id)
            return

        if self._message_handler:
            try:
                result = await self._message_handler(user_id, text, chat_id)
                if result == "":
                    pass  # Engine handled via streaming
            except Exception:
                logger.exception("slack_message_handler_error", chat_id=chat_id)
                await self.send_message(
                    chat_id, "An error occurred while processing your message."
                )

    async def _handle_action(self, body: dict[str, Any]) -> None:
        actions = body.get("actions", [])
        if not actions:
            return
        action_id = actions[0].get("action_id", "")
        chat_id = body.get("channel", {}).get("id", "")
        message_ts = body.get("message", {}).get("ts", "")

        if action_id.startswith(APPROVAL_PREFIX):
            await self._resolve_approval(action_id, chat_id, message_ts, body)
        elif action_id.startswith(INTERACTION_PREFIX):
            await self._resolve_interaction(action_id, chat_id, message_ts)
        elif action_id.startswith(INTERRUPT_PREFIX):
            await self._resolve_interrupt(action_id, chat_id, message_ts)

    async def _resolve_approval(
        self, action_id: str, chat_id: str, message_ts: str, body: dict[str, Any]
    ) -> None:
        suffix = action_id[len(APPROVAL_PREFIX) :]
        if ":" not in suffix:
            return
        decision, approval_id = suffix.split(":", 1)
        approved = decision in ("yes", "all")

        tool_name = self._approval_tool_names.pop(approval_id, "")

        resolved = False
        if self._approval_resolver:
            try:
                resolved = await self._approval_resolver(approval_id, approved)
            except Exception:
                logger.exception(
                    "slack_approval_resolver_error", approval_id=approval_id
                )

        if resolved and decision == "all" and self._auto_approve_handler:
            user_id = body.get("user", {}).get("id", "")
            if user_id:
                self._auto_approve_handler(chat_id, tool_name)

        status = "Approved ✓" if approved else "Rejected ✗"
        if not resolved:
            status = "Expired (approval no longer active)"
        elif decision == "all":
            status = f"Approved ✓ (auto-approving future {tool_name})"

        if message_ts and self._app:
            try:
                await self._app.client.chat_update(
                    channel=chat_id, ts=message_ts, text=status, blocks=[]
                )
                self.schedule_message_cleanup(chat_id, message_ts)
            except Exception:
                logger.debug("slack_update_approval_failed")

    async def _resolve_interaction(
        self, action_id: str, chat_id: str, message_ts: str
    ) -> None:
        suffix = action_id[len(INTERACTION_PREFIX) :]
        if ":" not in suffix:
            return
        interaction_id, answer = suffix.split(":", 1)

        resolved = False
        if self._interaction_resolver:
            try:
                resolved = await self._interaction_resolver(interaction_id, answer)
            except Exception:
                logger.exception("slack_interaction_resolver_error")

        is_plan_review = answer in ("clean_edit", "edit", "default", "adjust")
        if is_plan_review:
            plan_ids = self._plan_message_ids.pop(chat_id, [])
            for pid in plan_ids:
                if pid != message_ts:
                    await self.delete_message(chat_id, pid)
            await self.delete_message(chat_id, message_ts)
            if resolved and answer != "adjust":
                ack_ts = await self.send_message_with_id(
                    chat_id, "✓ Proceeding with implementation..."
                )
                if ack_ts:
                    self.schedule_message_cleanup(chat_id, ack_ts)
        else:
            ts = self._question_message_ids.pop(chat_id, None)
            if ts:
                await self.delete_message(chat_id, ts)

    async def _resolve_interrupt(
        self, action_id: str, chat_id: str, message_ts: str
    ) -> None:
        suffix = action_id[len(INTERRUPT_PREFIX) :]
        if ":" not in suffix:
            return
        decision, interrupt_id = suffix.split(":", 1)
        send_now = decision == "send"

        resolved = False
        if self._interrupt_resolver:
            try:
                resolved = await self._interrupt_resolver(interrupt_id, send_now)
            except Exception:
                logger.exception("slack_interrupt_resolver_error")

        if resolved:
            status = "⚡ Interrupting..." if send_now else "Queued ✓"
        else:
            status = "Expired (task already completed)"

        if message_ts and self._app:
            try:
                await self._app.client.chat_update(
                    channel=chat_id, ts=message_ts, text=status, blocks=[]
                )
                if resolved:
                    self.schedule_message_cleanup(chat_id, message_ts)
            except Exception:
                logger.debug("slack_update_interrupt_failed")


# --- Block Kit helpers ---


def _button(text: str, action_id: str) -> dict[str, Any]:
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": text[:75]},
        "action_id": action_id[:255],
    }


def _text_with_buttons(
    text: str, buttons: list[list[InlineButton]]
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "section", "text": {"type": "mrkdwn", "text": text[:3000]}},
    ]
    elements = []
    for row in buttons:
        for btn in row:
            elements.append(_button(btn.text, btn.callback_data))
    if elements:
        blocks.append({"type": "actions", "elements": elements[:25]})
    return blocks
