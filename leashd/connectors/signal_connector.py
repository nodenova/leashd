"""Signal connector — signal-cli HTTP JSON-RPC daemon."""

from __future__ import annotations

import asyncio
import contextlib
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

try:
    import httpx
except ImportError as exc:
    raise ImportError(
        "Signal connector requires httpx. Install with: uv pip install 'leashd[signal]'"
    ) from exc

logger = structlog.get_logger()

_MAX_MESSAGE_LENGTH = 1900
_POLL_INTERVAL = 1.0


class SignalConnector(BaseConnector):
    """Signal connector using signal-cli HTTP JSON-RPC daemon."""

    def __init__(self, phone_number: str, cli_url: str) -> None:
        super().__init__()
        self._phone_number = phone_number
        self._cli_url = cli_url.rstrip("/")

        self._client: httpx.AsyncClient | None = None
        self._receive_task: asyncio.Task[None] | None = None

        # Text-based fallback state
        self._pending_approval: dict[str, str] = {}
        self._pending_interaction: dict[str, tuple[str, list[dict[str, str]]]] = {}
        self._pending_plan_review: dict[str, str] = {}

    # --- Lifecycle ---

    async def start(self) -> None:
        self._client = httpx.AsyncClient(base_url=self._cli_url, timeout=30.0)
        self._receive_task = asyncio.create_task(self._receive_loop())
        logger.info("signal_connector_started", phone=self._phone_number)

    async def stop(self) -> None:
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("signal_connector_stopped")

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
                        "/api/v2/send",
                        json={
                            "message": c,
                            "number": self._phone_number,
                            "recipients": [chat_id],
                        },
                    ),
                    operation="signal_send_message",
                )
            except Exception:
                logger.exception("signal_send_message_failed", chat_id=chat_id)

    async def send_typing_indicator(self, chat_id: str) -> None:
        if not self._client:
            return
        try:
            await self._client.put(
                f"/api/v1/typing-indicator/{self._phone_number}",
                json={"recipient": chat_id},
            )
        except Exception:
            logger.debug("signal_typing_indicator_failed", chat_id=chat_id)

    async def send_file(self, chat_id: str, file_path: str) -> None:
        if not self._client:
            return
        try:
            import base64
            from pathlib import Path

            path = Path(file_path)
            data = path.read_bytes()
            encoded = base64.b64encode(data).decode()

            await retry_on_error(
                lambda: self._client.post(  # type: ignore
                    "/api/v2/send",
                    json={
                        "number": self._phone_number,
                        "recipients": [chat_id],
                        "base64_attachments": [encoded],
                    },
                ),
                operation="signal_send_file",
            )
        except Exception:
            logger.exception("signal_send_file_failed", chat_id=chat_id)

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
            "signal_approval_requested", chat_id=chat_id, approval_id=approval_id
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

    # --- Inbound receive loop ---

    async def _receive_loop(self) -> None:
        while True:
            try:
                if not self._client:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                resp = await self._client.get(
                    f"/api/v1/receive/{self._phone_number}",
                    timeout=35.0,
                )
                if resp.status_code != 200:
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                envelopes = resp.json()
                if not isinstance(envelopes, list):
                    await asyncio.sleep(_POLL_INTERVAL)
                    continue

                for envelope in envelopes:
                    await self._handle_envelope(envelope)

            except asyncio.CancelledError:
                return
            except Exception:
                logger.debug("signal_receive_error")
                await asyncio.sleep(_POLL_INTERVAL * 2)

            await asyncio.sleep(_POLL_INTERVAL)

    async def _handle_envelope(self, envelope: dict[str, Any]) -> None:
        data_msg = envelope.get("envelope", {}).get("dataMessage")
        if not data_msg:
            return

        text = data_msg.get("message", "")
        if not text:
            return

        source = envelope.get("envelope", {}).get("sourceNumber", "") or envelope.get(
            "envelope", {}
        ).get("source", "")
        if not source:
            return

        # Group messages: use group ID as chat_id
        group_info = data_msg.get("groupInfo", {})
        chat_id = group_info.get("groupId", source) if group_info else source

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
                result = await self._command_handler(source, command, args, chat_id)
                if result:
                    await self.send_message(chat_id, result)
            except Exception:
                logger.exception("signal_command_handler_error", chat_id=chat_id)
            return

        # Normal message
        if self._message_handler:
            try:
                result = await self._message_handler(source, text, chat_id)
                if result == "":
                    pass
            except Exception:
                logger.exception("signal_message_handler_error", chat_id=chat_id)
                await self.send_message(
                    chat_id, "An error occurred while processing your message."
                )
