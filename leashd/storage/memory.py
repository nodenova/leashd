"""In-memory session store — same behavior as the original SessionManager."""

from __future__ import annotations

from typing import TYPE_CHECKING

from leashd.core.chat_sessions import PRIMARY_INDEX, index_of, is_member

if TYPE_CHECKING:
    from leashd.core.session import Session


class MemorySessionStore:
    def __init__(self) -> None:
        self._data: dict[str, Session] = {}

    def _key(self, user_id: str, chat_id: str) -> str:
        return f"{user_id}:{chat_id}"

    async def save(self, session: Session) -> None:
        self._data[self._key(session.user_id, session.chat_id)] = session

    async def load(self, user_id: str, chat_id: str) -> Session | None:
        session = self._data.get(self._key(user_id, chat_id))
        if session and session.is_active:
            return session
        return None

    async def list_sessions(self, user_id: str, *, chat_base: str) -> list[Session]:
        return [
            session
            for session in self._data.values()
            if session.user_id == user_id
            and session.is_active
            and is_member(session.chat_id, chat_base)
        ]

    async def list_foreground_sessions(self) -> list[Session]:
        return [
            session
            for session in self._data.values()
            if session.is_active
            and session.is_foreground
            and index_of(session.chat_id) > PRIMARY_INDEX
        ]

    async def delete(self, user_id: str, chat_id: str) -> None:
        self._data.pop(self._key(user_id, chat_id), None)

    async def setup(self) -> None:
        pass

    async def teardown(self) -> None:
        pass
