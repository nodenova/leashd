"""Which leashd conversation a Telegram chat's single stream is attached to.

Telegram gives one message stream per chat, so leashd runs N conversations
behind it and shows one at a time. This router is the whole translation layer:
inbound it names the conversation a message belongs to, outbound it resolves a
conversation back to the real Telegram chat and says whether that conversation
currently owns the screen.

Background conversations keep running — they are agents in their own panes, not
paused. What changes is only what reaches the chat: nothing they produce is
written into it, neither live progress nor the prompts that block them. Each
gets a notice naming the slot; the prompt itself is held by the connector and
rendered when that conversation is put back on screen, so an approval or a
question is always read under the conversation that raised it.
"""

from __future__ import annotations

import structlog

from leashd.core.chat_sessions import base_of, split

logger = structlog.get_logger()


class ChatSessionRouter:
    """Per-Telegram-chat foreground state and chat-id translation."""

    def __init__(self) -> None:
        self._foreground: dict[str, str] = {}

    def inbound(self, telegram_chat_id: str) -> str:
        """The conversation a message arriving in this chat belongs to."""
        return self._foreground.get(telegram_chat_id, telegram_chat_id)

    def foreground(self, chat_id: str) -> str:
        """The conversation currently owning the stream of *chat_id*'s chat."""
        base = base_of(chat_id)
        return self._foreground.get(base, base)

    def activate(self, chat_id: str) -> str:
        """Attach the chat's stream to *chat_id*; returns the chat it belongs to."""
        base, index = split(chat_id)
        previous = self._foreground.get(base, base)
        if index <= 1:
            self._foreground.pop(base, None)
        else:
            self._foreground[base] = chat_id
        if previous != chat_id:
            logger.info(
                "telegram_chat_session_activated",
                telegram_chat_id=base,
                chat_id=chat_id,
                slot=index,
            )
        return base

    def target(self, chat_id: str) -> str:
        """The real Telegram chat id an outbound call must be sent to."""
        return base_of(chat_id)

    def is_foreground(self, chat_id: str) -> bool:
        base = base_of(chat_id)
        return self._foreground.get(base, base) == chat_id

    def forget(self, chat_id: str) -> None:
        """Drop *chat_id* from the foreground so the chat falls back to slot 1."""
        base = base_of(chat_id)
        if self._foreground.get(base) == chat_id:
            del self._foreground[base]
