"""E2E tests for the reconnection and pending state restoration system."""

import asyncio

import pytest
from playwright.async_api import Page

from tests.e2e.conftest import SimpleNamespace, inject


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.e2e
class TestPendingState:
    async def test_pending_state_restores_approval(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        payload = {"request_id": "ps-ap-1", "tool": "Bash", "description": "run cmd"}
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "pending_state",
            {"approvals": [payload], "question": None, "plan_review": None},
        )
        await authed_page.wait_for_selector(
            '[data-approval-id="ps-ap-1"]', timeout=5000
        )
        tool_text = await authed_page.text_content(
            '[data-approval-id="ps-ap-1"] .approval-tool'
        )
        assert tool_text == "Bash"

    async def test_pending_state_restores_question(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        question_payload = {
            "interaction_id": "ps-q-1",
            "question": "Which option?",
            "header": "Choose",
            "options": [{"label": "A", "value": "a"}],
        }
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "pending_state",
            {"approvals": [], "question": question_payload, "plan_review": None},
        )
        await authed_page.wait_for_selector(
            '[data-interaction-id="ps-q-1"]', timeout=5000
        )
        title = await authed_page.text_content(
            '[data-interaction-id="ps-q-1"] .question-title'
        )
        assert title == "Choose"

    async def test_pending_state_restores_plan_review(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        plan_payload = {
            "interaction_id": "ps-pr-1",
            "description": "## Restored Plan\n- Step A",
        }
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "pending_state",
            {"approvals": [], "question": None, "plan_review": plan_payload},
        )
        msg_id = "plan-review-ps-pr-1"
        await authed_page.wait_for_selector(
            f'[data-message-id="{msg_id}"]', timeout=5000
        )
        buttons = authed_page.locator(f'[data-message-id="{msg_id}"] .msg-btn')
        assert await buttons.count() >= 2

    async def test_pending_state_shows_streaming_content(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "pending_state",
            {
                "approvals": [],
                "question": None,
                "plan_review": None,
                "streaming_content": {
                    "message_id": "ps-stream-1",
                    "text": "partial output from agent",
                },
            },
        )
        await authed_page.wait_for_selector(
            '[data-message-id="ps-stream-1"]', timeout=5000
        )
        el = authed_page.locator('[data-message-id="ps-stream-1"] .msg-content')
        classes = await el.get_attribute("class") or ""
        assert "streaming" in classes
        text = await el.text_content()
        assert "partial output" in (text or "")

    async def test_pending_state_shows_agent_busy(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "pending_state",
            {
                "approvals": [],
                "question": None,
                "plan_review": None,
                "agent_busy": True,
            },
        )
        await authed_page.wait_for_selector(".msg-row-system", timeout=5000)
        text = await authed_page.text_content(".msg-row-system .msg-content")
        assert "working" in (text or "").lower()

    async def test_pending_state_dedup_question(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        question_payload = {
            "interaction_id": "ps-dedup-q",
            "question": "Pick one",
            "header": "Dedup",
            "options": [{"label": "X", "value": "x"}],
        }
        # First inject the question directly
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "question",
            question_payload,
        )
        await authed_page.wait_for_selector(
            '[data-interaction-id="ps-dedup-q"]', timeout=5000
        )

        # Then inject pending_state with the same question
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "pending_state",
            {"approvals": [], "question": question_payload, "plan_review": None},
        )
        await asyncio.sleep(0.4)  # allow WS processing before asserting absence

        # Should be exactly ONE question card (no duplicate)
        cards = authed_page.locator('[data-interaction-id="ps-dedup-q"]')
        assert await cards.count() == 1
