"""iMessage connector — BlueBubbles REST API."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

import structlog

from leashd.connectors._shared import (
    format_text_approval,
    format_text_plan_review,
    format_text_question,
    parse_text_response,
    retry_on_error,
    split_text,
)
from leashd.connectors.base import BaseConnector, InlineButton
from leashd.exceptions import ConnectorError

try:
    import httpx
except ImportError as exc:
    raise ImportError(
        "iMessage connector requires httpx. "
        "Install with: uv pip install 'leashd[imessage]'"
    ) from exc

logger = structlog.get_logger()

_MAX_MESSAGE_LENGTH = 5000
_POLL_INTERVAL = 2.0


class IMessageConnector(BaseConnector):
    """iMessage connector using BlueBubbles REST API."""

    def __init__(self, server_url: str, password: str) -> None:
        super().__init__()
        self._server_url = server_url.rstrip("/")
        self._password = password

        self._client: httpx.AsyncClient | None = None
        self._poll_task: asyncio.Task[None] | None = None
        self._last_message_ts: int = 0

        # Text-based fallback state
        self._pending_approval: dict[str, str] = {}
        self._pending_interaction: dict[str, tuple[str, list[dict[str, str]]]] = {}
        self._pending_plan_review: dict[str, str] = {}

    # --- Lifecycle ---

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            base_url=self._server_url,
            params={"password": self._password},
            timeout=30.0,
        )

        # Verify server connectivity
        try:
            resp = await self._client.get("/api/v1/server/info")
            resp.raise_for_status()
            logger.info("imessage_server_connected", info=resp.json())
        except Exception as exc:
            await self._client.aclose()
            self._client = None
            msg = f"BlueBubbles server not reachable at {self._server_url}"
            raise ConnectorError(msg) from exc

        # Start polling from now
        self._last_message_ts = int(time.time() * 1000)
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("imessage_connector_started")

    async def stop(self) -> None:
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._poll_task
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("imessage_connector_stopped")

    # --- Messaging ---

    async def send_message(
        self,
        chat_id: str,
        text: str,
        buttons: list[list[InlineButton]] | None = None,  # noqa: ARG002
    ) -> None:
        if not self._client:
            return
        chunks = split_text(text, _MAX_MESSAGE_LENGTH)
        for chunk in chunks:
            try:
                await retry_on_error(
                    lambda c=chunk: self._client.post(  # type: ignore
                        "/api/v1/message/text",
                        json={
                            "chatGuid": chat_id,
                            "message": c,
                            "tempGuid": f"leashd-{time.time_ns()}",
                        },
                    ),
                    operation="imessage_send_message",
                )
            except Exception:
                logger.exception("imessage_send_message_failed", chat_id=chat_id)

    async def send_typing_indicator(self, chat_id: str) -> None:
        if not self._client:
            return
        try:
            await self._client.post(f"/api/v1/chat/{chat_id}/typing")
        except Exception:
            logger.debug("imessage_typing_indicator_failed", chat_id=chat_id)

    async def send_file(self, chat_id: str, file_path: str) -> None:
        if not self._client:
            return
        try:
            from pathlib import Path

            path = Path(file_path)
            with path.open("rb") as f:
                await retry_on_error(
                    lambda: self._client.post(  # type: ignore
                        "/api/v1/message/attachment",
                        data={"chatGuid": chat_id},
                        files={
                            "attachment": (path.name, f, "application/octet-stream")
                        },
                    ),
                    operation="imessage_send_file",
                )
        except Exception:
            logger.exception("imessage_send_file_failed", chat_id=chat_id)

    # --- Approvals (text-based fallback) ---

    async def request_approval(
        self,
        chat_id: str,
        approval_id: str,
        description: str,
        tool_name: str = "",  # noqa: ARG002
    ) -> str | None:
        text = format_text_approval(description)
        self._pending_approval[chat_id] = approval_id
        await self.send_message(chat_id, text)
        logger.info(
            "imessage_approval_requested", chat_id=chat_id, approval_id=approval_id
        )
        return None

    # --- Questions (text-based fallback) ---

    async def send_question(
        self,
        chat_id: str,
        interaction_id: str,
        question_text: str,
        header: str,
        options: list[dict[str, str]],
    ) -> None:
        text = format_text_question(question_text, header, options)
        self._pending_interaction[chat_id] = (interaction_id, options)
        await self.send_message(chat_id, text)

    # --- Plan review (text-based fallback) ---

    async def send_plan_review(
        self, chat_id: str, interaction_id: str, description: str
    ) -> None:
        text = format_text_plan_review(description)
        self._pending_plan_review[chat_id] = interaction_id
        await self.send_message(chat_id, text)

    # --- Inbound poll loop ---

    async def _poll_loop(self) -> None:
        while True:
            try:
                if not self._client:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                resp = await self._client.post(
                    "/api/v1/message/query",
                    json={
                        "after": self._last_message_ts,
                        "limit": 50,
                        "sort": "ASC",
                        "with": ["chats"],
                    },
                    timeout=35.0,
                )
                if resp.status_code != 200:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                data = resp.json()
                messages = data.get("data", [])
                if not isinstance(messages, list):
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                for msg in messages:
                    await self._handle_message(msg)

                    # Advance cursor
                    date_created = msg.get("dateCreated", 0)
                    if date_created > self._last_message_ts:
                        self._last_message_ts = date_created

            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("imessage_poll_error")

            await asyncio.sleep(_POLL_INTERVAL)

    async def _handle_message(self, msg: dict[str, Any]) -> None:
        # Skip outgoing messages (isFromMe == true)
        if msg.get("isFromMe", False):
            return

        text = msg.get("text", "")
        if not text:
            return

        # Extract sender handle
        handle = msg.get("handle", {})
        sender = ""
        if isinstance(handle, dict):
            sender = handle.get("address", "") or handle.get("id", "")
        elif isinstance(handle, str):
            sender = handle

        if not sender:
            return

        # Extract chat GUID from chats array
        chats = msg.get("chats", [])
        chat_id = ""
        if chats and isinstance(chats, list):
            chat_id = chats[0].get("guid", "") or chats[0].get("chatIdentifier", "")
        if not chat_id:
            chat_id = sender

        # Check text-based responses for pending states
        parsed = parse_text_response(
            text,
            has_pending_approval=chat_id in self._pending_approval,
            has_pending_interaction=chat_id in self._pending_interaction,
            pending_options=(
                self._pending_interaction[chat_id][1]
                if chat_id in self._pending_interaction
                else None
            ),
            has_pending_plan_review=chat_id in self._pending_plan_review,
        )

        if parsed:
            if parsed.kind == "approval" and self._approval_resolver:
                approval_id = self._pending_approval.pop(chat_id, "")
                if approval_id:
                    approved = parsed.value in ("approve", "approve-all")
                    await self._approval_resolver(approval_id, approved)
                    if parsed.value == "approve-all" and self._auto_approve_handler:
                        self._auto_approve_handler(chat_id, "")
                return

            if parsed.kind == "interaction" and self._interaction_resolver:
                iid, _opts = self._pending_interaction.pop(chat_id, ("", []))
                if iid:
                    await self._interaction_resolver(iid, parsed.value)
                return

            if parsed.kind == "plan_review" and self._interaction_resolver:
                iid = self._pending_plan_review.pop(chat_id, "")
                if iid:
                    await self._interaction_resolver(iid, parsed.value)
                return

        # Route /commands
        if text.startswith("/") and self._command_handler:
            cmd_parts = text.split(None, 1)
            command = cmd_parts[0].lstrip("/")
            args = cmd_parts[1] if len(cmd_parts) > 1 else ""
            try:
                result = await self._command_handler(sender, command, args, chat_id)
                if result:
                    await self.send_message(chat_id, result)
            except Exception:
                logger.exception("imessage_command_handler_error", chat_id=chat_id)
            return

        # Normal message
        if self._message_handler:
            try:
                result = await self._message_handler(sender, text, chat_id)
                if result == "":
                    pass
            except Exception:
                logger.exception("imessage_message_handler_error", chat_id=chat_id)
                await self.send_message(
                    chat_id, "An error occurred while processing your message."
                )
