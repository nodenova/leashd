"""E2E tests for questions, plan reviews, and interrupt prompts."""

import asyncio
import contextlib

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

from tests.e2e.conftest import SimpleNamespace, inject, wait_for


@pytest.mark.e2e
class TestQuestionCards:
    async def test_question_card_renders_in_messages(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "question",
            {
                "interaction_id": "q-render",
                "question": "Pick a flavour",
                "header": "Choose",
                "options": [
                    {"label": "Vanilla", "value": "vanilla"},
                    {"label": "Chocolate", "value": "chocolate"},
                ],
            },
        )
        await authed_page.wait_for_selector(
            '[data-interaction-id="q-render"]', timeout=5000
        )
        title = await authed_page.text_content(
            '[data-interaction-id="q-render"] .question-title'
        )
        assert title == "Choose"
        # Both option buttons should be present
        opts = authed_page.locator(
            '[data-interaction-id="q-render"] .question-option-btn'
        )
        assert await opts.count() == 2

    async def test_question_option_click_sends_interaction_response(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "question",
            {
                "interaction_id": "q-click",
                "question": "Which option?",
                "header": "Select",
                "options": [{"label": "Option A", "value": "a"}],
            },
        )
        await authed_page.wait_for_selector(
            '[data-interaction-id="q-click"]', timeout=5000
        )
        await authed_page.click(
            '[data-interaction-id="q-click"] .question-option-btn:first-child'
        )

        await wait_for(
            lambda: bool(capture["interactions"]),
            msg="No interaction response captured",
        )
        resp = capture["interactions"][0]
        assert resp["interaction_id"] == "q-click"
        assert resp["answer"] == "a"

    async def test_question_free_text_submit(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "question",
            {
                "interaction_id": "q-freetext",
                "question": "What should I name it?",
                "header": "Name",
                "options": [],
            },
        )
        await authed_page.wait_for_selector(
            '[data-interaction-id="q-freetext"]', timeout=5000
        )
        await authed_page.fill(
            '[data-interaction-id="q-freetext"] .question-textarea', "custom answer"
        )
        await authed_page.click(
            '[data-interaction-id="q-freetext"] .question-submit-btn'
        )

        await wait_for(
            lambda: bool(capture["interactions"]),
            msg="No interaction response for free-text submit",
        )
        resp = capture["interactions"][0]
        assert resp["interaction_id"] == "q-freetext"
        assert resp["answer"] == "custom answer"

    async def test_question_skip_button(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "question",
            {
                "interaction_id": "q-skip",
                "question": "Optional question",
                "header": "Skip test",
                "options": [],
            },
        )
        await authed_page.wait_for_selector(
            '[data-interaction-id="q-skip"]', timeout=5000
        )
        await authed_page.click('[data-interaction-id="q-skip"] .question-skip-btn')

        await wait_for(
            lambda: bool(capture["interactions"]),
            msg="No interaction response for skip",
        )
        resp = capture["interactions"][0]
        assert resp["interaction_id"] == "q-skip"
        assert resp["answer"] == ""

    async def test_question_card_resolved_state(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "question",
            {
                "interaction_id": "q-resolved",
                "question": "Pick one",
                "header": "Choice",
                "options": [{"label": "Alpha", "value": "alpha"}],
            },
        )
        await authed_page.wait_for_selector(
            '[data-interaction-id="q-resolved"]', timeout=5000
        )
        await authed_page.click(
            '[data-interaction-id="q-resolved"] .question-option-btn:first-child'
        )

        resolved_sel = '[data-interaction-id="q-resolved"] .question-resolved'
        await authed_page.wait_for_selector(resolved_sel, timeout=5000)
        text = await authed_page.text_content(resolved_sel)
        assert "Alpha" in (text or "")

    async def test_question_prevents_double_submit(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "question",
            {
                "interaction_id": "q-double",
                "question": "Pick",
                "header": "Double",
                "options": [{"label": "Once", "value": "once"}],
            },
        )
        await authed_page.wait_for_selector(
            '[data-interaction-id="q-double"]', timeout=5000
        )
        # Click twice rapidly — second may fail if card already resolved
        await authed_page.click(
            '[data-interaction-id="q-double"] .question-option-btn:first-child'
        )
        with contextlib.suppress(PlaywrightError):
            await authed_page.click(
                '[data-interaction-id="q-double"] .question-option-btn:first-child'
            )

        await wait_for(
            lambda: bool(capture["interactions"]),
            msg="No interaction response for double-submit test",
        )
        # Should only have ONE response despite double click
        assert len(capture["interactions"]) == 1


@pytest.mark.e2e
class TestPlanReview:
    async def test_plan_review_renders_with_accept_buttons(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        interaction_id = "pr-render"
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "plan_review",
            {
                "interaction_id": interaction_id,
                "description": "## Plan\n- Step 1\n- Step 2",
            },
        )
        msg_id = f"plan-review-{interaction_id}"
        await authed_page.wait_for_selector(
            f'[data-message-id="{msg_id}"]', timeout=5000
        )
        buttons = authed_page.locator(f'[data-message-id="{msg_id}"] .msg-btn')
        texts = [
            await buttons.nth(i).text_content() for i in range(await buttons.count())
        ]
        assert "Accept" in texts
        assert "Adjust" in texts

    async def test_plan_review_accept_sends_response(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        interaction_id = "pr-accept"
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "plan_review",
            {"interaction_id": interaction_id, "description": "Plan to accept"},
        )
        msg_id = f"plan-review-{interaction_id}"
        await authed_page.wait_for_selector(
            f'[data-message-id="{msg_id}"]', timeout=5000
        )
        # Click the "Accept" button (first button)
        buttons = authed_page.locator(f'[data-message-id="{msg_id}"] .msg-btn')
        for i in range(await buttons.count()):
            text = await buttons.nth(i).text_content()
            if text == "Accept":
                await buttons.nth(i).click()
                break

        await wait_for(
            lambda: bool(capture["interactions"]),
            msg="No interaction response for plan accept",
        )
        resp = capture["interactions"][0]
        assert resp["interaction_id"] == interaction_id
        assert resp["answer"] == "clean_edit"

    async def test_plan_review_adjust_sends_response(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        interaction_id = "pr-adjust"
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "plan_review",
            {"interaction_id": interaction_id, "description": "Plan to adjust"},
        )
        msg_id = f"plan-review-{interaction_id}"
        await authed_page.wait_for_selector(
            f'[data-message-id="{msg_id}"]', timeout=5000
        )
        buttons = authed_page.locator(f'[data-message-id="{msg_id}"] .msg-btn')
        for i in range(await buttons.count()):
            text = await buttons.nth(i).text_content()
            if text == "Adjust":
                await buttons.nth(i).click()
                break

        await wait_for(
            lambda: bool(capture["interactions"]),
            msg="No interaction response for plan adjust",
        )
        resp = capture["interactions"][0]
        assert resp["interaction_id"] == interaction_id
        assert resp["answer"] == "adjust"

    async def test_plan_review_buttons_disabled_after_click(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        interaction_id = "pr-disable"
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "plan_review",
            {"interaction_id": interaction_id, "description": "Plan test"},
        )
        msg_id = f"plan-review-{interaction_id}"
        await authed_page.wait_for_selector(
            f'[data-message-id="{msg_id}"]', timeout=5000
        )
        # Click Accept
        accept_btn = authed_page.locator(
            f'[data-message-id="{msg_id}"] .msg-btn >> text=Accept'
        ).first
        await accept_btn.click()
        await asyncio.sleep(0.3)

        # All buttons should now be disabled
        buttons = authed_page.locator(f'[data-message-id="{msg_id}"] .msg-btn')
        for i in range(await buttons.count()):
            is_disabled = await buttons.nth(i).is_disabled()
            assert is_disabled, f"Button {i} should be disabled after click"


@pytest.mark.e2e
class TestInterruptBanner:
    async def test_interrupt_prompt_shows_banner(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "interrupt_prompt",
            {
                "interrupt_id": "irq-show",
                "message_preview": "my queued message",
                "message_id": "irq-msg-1",
            },
        )
        await authed_page.wait_for_selector(
            "#queued-banner:not([hidden])", timeout=5000
        )
        preview = await authed_page.text_content("#queued-banner .queued-text")
        assert "my queued message" in (preview or "")

    async def test_interrupt_send_now_sends_response(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "interrupt_prompt",
            {
                "interrupt_id": "irq-send",
                "message_preview": "queued task",
                "message_id": "irq-msg-2",
            },
        )
        await authed_page.wait_for_selector(
            "#queued-banner:not([hidden])", timeout=5000
        )
        await authed_page.click('[data-action="send"]')

        await wait_for(
            lambda: bool(capture["interrupts"]),
            msg="No interrupt response captured",
        )
        resp = capture["interrupts"][0]
        assert resp["interrupt_id"] == "irq-send"
        assert resp["send_now"] is True

    async def test_interrupt_dismiss_hides_banner(
        self, authed_page: Page, test_server: SimpleNamespace, capture: dict
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "interrupt_prompt",
            {
                "interrupt_id": "irq-dismiss",
                "message_preview": "will be dismissed",
                "message_id": "irq-msg-3",
            },
        )
        await authed_page.wait_for_selector(
            "#queued-banner:not([hidden])", timeout=5000
        )
        await authed_page.click('[data-action="dismiss"]')

        # Banner hides (after fade animation ~400ms)
        await authed_page.wait_for_selector(
            "#queued-banner", state="hidden", timeout=3000
        )

        assert capture["interrupts"]
        assert capture["interrupts"][0]["send_now"] is False
