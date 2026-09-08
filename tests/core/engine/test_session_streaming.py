"""Engine tests — what the chat sees while several conversations run at once.

One Telegram chat is one message stream, so only the foreground conversation
gets to write to it. These pin down what happens to the ones that don't: their
reply has to survive being off-screen whole, and a prompt they raise must not
capture what the user types at the conversation they are actually looking at.
"""

import asyncio

import pytest

from leashd.agents.base import AgentResponse, BaseAgent
from leashd.core.engine import Engine
from leashd.core.interactions import InteractionCoordinator
from leashd.core.session import SessionManager
from leashd.storage.sqlite import SqliteSessionStore
from tests.conftest import MockConnector
from tests.core.engine.conftest import FakeAgent


class SwitchingAgent(BaseAgent):
    """Streams a reply in two halves, switching the chat away in between."""

    def __init__(self, engine_box: dict, *, leave: str, land_on: str):
        self._engine_box = engine_box
        self._leave = leave
        self._land_on = land_on
        self.live: set[str] = set()

    def live_chat_ids(self) -> set[str]:
        return set(self.live)

    async def cancel_chat(self, chat_id: str) -> None:
        self.live.discard(chat_id)

    async def execute(self, prompt, session, *, can_use_tool=None, **kwargs):
        on_text_chunk = kwargs["on_text_chunk"]
        await on_text_chunk("first half. ")
        engine = self._engine_box["engine"]
        await engine.handle_command("u1", "session", self._land_on, self._leave)
        await on_text_chunk("second half.")
        return AgentResponse(
            content="first half. second half.", session_id="s-1", is_error=False
        )


@pytest.fixture
async def store(tmp_path):
    store = SqliteSessionStore(tmp_path / "sessions.db")
    await store.setup()
    yield store
    await store.teardown()


@pytest.fixture
def connector():
    return MockConnector(chat_sessions=True, support_streaming=True)


def _build(config, connector, agent, policy_engine, audit_logger, store, **kwargs):
    return Engine(
        connector=connector,
        agent=agent,
        config=config,
        session_manager=SessionManager(store=store),
        policy_engine=policy_engine,
        audit=audit_logger,
        store=store,
        **kwargs,
    )


class TestReplyOfABackgroundedTurn:
    """A turn the chat moves off mid-sentence still has to be kept whole."""

    async def test_the_stored_reply_is_not_a_fragment(
        self, config, connector, policy_engine, audit_logger, store
    ):
        box: dict = {}
        engine = _build(
            config,
            connector,
            SwitchingAgent(box, leave="chat1", land_on="2"),
            policy_engine,
            audit_logger,
            store,
        )
        box["engine"] = engine
        await engine.handle_command("u1", "session", "new", "chat1")
        await engine.handle_command("u1", "session", "1", "chat1:s2")

        await engine.handle_message("u1", "go", "chat1")

        row = await store.get_last_message("u1", "chat1", role="assistant")
        assert row["content"] == "first half. second half."

    async def test_switching_back_replays_the_whole_reply(
        self, config, connector, policy_engine, audit_logger, store
    ):
        box: dict = {}
        engine = _build(
            config,
            connector,
            SwitchingAgent(box, leave="chat1", land_on="2"),
            policy_engine,
            audit_logger,
            store,
        )
        box["engine"] = engine
        await engine.handle_command("u1", "session", "new", "chat1")
        await engine.handle_command("u1", "session", "1", "chat1:s2")
        await engine.handle_message("u1", "go", "chat1")

        await engine.handle_command("u1", "session", "1", "chat1:s2")

        assert "first half. second half." in connector.sent_messages[-1]["text"]

    async def test_the_finished_reply_is_announced_not_pasted(
        self, config, connector, policy_engine, audit_logger, store
    ):
        box: dict = {}
        engine = _build(
            config,
            connector,
            SwitchingAgent(box, leave="chat1", land_on="2"),
            policy_engine,
            audit_logger,
            store,
        )
        box["engine"] = engine
        await engine.handle_command("u1", "session", "new", "chat1")
        await engine.handle_command("u1", "session", "1", "chat1:s2")

        await engine.handle_message("u1", "go", "chat1")

        assert connector.sent_messages[-1]["chat_id"] == "chat1"


class TestTypingWhileAnotherConversationIsBlocked:
    """Typed text belongs to the conversation on screen, whatever else is waiting.

    A background conversation's prompt is not in the chat — it is a notice with
    an Open button, held until that conversation is back on screen. Reading a
    typed message as its answer swallows a prompt meant for the agent the user
    can actually see.
    """

    @pytest.fixture
    def engine(self, config, connector, policy_engine, audit_logger, store):
        coordinator = InteractionCoordinator(connector, config)
        engine = _build(
            config,
            connector,
            FakeAgent(),
            policy_engine,
            audit_logger,
            store,
            interaction_coordinator=coordinator,
        )
        engine.interaction_coordinator = coordinator
        return engine

    @staticmethod
    def _answer(result):
        return result.updated_input["answers"]["Which one?"]

    async def _ask_from(self, engine, chat_id):
        asked = asyncio.create_task(
            engine.interaction_coordinator.handle_question(
                chat_id,
                {
                    "questions": [
                        {"question": "Which one?", "options": [{"label": "A"}]}
                    ]
                },
            )
        )
        await asyncio.sleep(0)
        return asked

    async def test_the_prompt_the_chat_cannot_see_does_not_take_the_message(
        self, engine
    ):
        await engine.handle_command("u1", "session", "new", "chat1")
        asked = await self._ask_from(engine, "chat1:s2")
        await engine.handle_command("u1", "session", "1", "chat1:s2")

        await engine.handle_message("u1", "work on this instead", "chat1")

        assert not asked.done()
        engine.interaction_coordinator.cancel_pending("chat1:s2")
        await asked

    async def test_the_message_starts_a_turn_in_the_conversation_on_screen(
        self, engine, connector
    ):
        await engine.handle_command("u1", "session", "new", "chat1")
        asked = await self._ask_from(engine, "chat1:s2")
        await engine.handle_command("u1", "session", "1", "chat1:s2")

        await engine.handle_message("u1", "work on this instead", "chat1")

        assert any(
            "Echo: work on this instead" in m["text"] for m in connector.sent_messages
        )
        engine.interaction_coordinator.cancel_pending("chat1:s2")
        await asked

    async def test_opening_the_conversation_makes_its_prompt_answerable_again(
        self, engine
    ):
        await engine.handle_command("u1", "session", "new", "chat1")
        asked = await self._ask_from(engine, "chat1:s2")
        await engine.handle_command("u1", "session", "1", "chat1:s2")
        await engine.handle_command("u1", "session", "2", "chat1")

        await engine.handle_message("u1", "now I can see it", "chat1:s2")

        assert self._answer(await asked) == "now I can see it"

    async def test_the_conversation_on_screen_is_answered_as_before(self, engine):
        await engine.handle_command("u1", "session", "new", "chat1")
        background = await self._ask_from(engine, "chat1:s2")
        foreground = await self._ask_from(engine, "chat1")
        await engine.handle_command("u1", "session", "1", "chat1:s2")

        await engine.handle_message("u1", "for the one I can see", "chat1")

        assert self._answer(await foreground) == "for the one I can see"
        assert not background.done()
        engine.interaction_coordinator.cancel_pending("chat1:s2")
        await background

    async def test_another_chat_entirely_is_never_answered(self, engine):
        asked = await self._ask_from(engine, "chat9")

        await engine.handle_message("u1", "not for chat9", "chat1")

        assert not asked.done()
        engine.interaction_coordinator.cancel_pending("chat9")
        await asked
