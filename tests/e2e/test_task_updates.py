"""E2E tests for task update rendering in the WebUI."""

import pytest
from playwright.async_api import Page

from tests.e2e.conftest import SimpleNamespace, inject


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.e2e
class TestTaskUpdates:
    async def test_task_phase_badge(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "task_update",
            {
                "phase": "implement",
                "status": "running",
                "description": "Writing code",
            },
        )
        await authed_page.wait_for_selector(".msg-row-system", timeout=5000)
        badge = authed_page.locator(".task-badge")
        assert await badge.count() > 0
        badge_text = await badge.first.text_content()
        assert badge_text == "implement"
        msg_text = await authed_page.text_content(".msg-row-system .msg-content")
        assert "Writing code" in (msg_text or "")

    async def test_task_completed(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "task_update",
            {"phase": "", "status": "completed", "description": "All done"},
        )
        await authed_page.wait_for_selector(".msg-row-system", timeout=5000)
        text = await authed_page.text_content(".msg-row-system .msg-content")
        assert "All done" in (text or "")

    async def test_task_failed(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "task_update",
            {"phase": "", "status": "failed", "description": "Tests failed"},
        )
        await authed_page.wait_for_selector(".msg-row-system", timeout=5000)
        text = await authed_page.text_content(".msg-row-system .msg-content")
        assert "Tests failed" in (text or "")
