"""E2E tests for the WebUI chat message flow."""

import asyncio

import pytest
from playwright.async_api import Page

from tests.e2e.conftest import SimpleNamespace, inject, wait_for


@pytest.mark.asyncio(loop_scope="module")
@pytest.mark.e2e
class TestChatMessages:
    async def test_send_message_appears_in_chat(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "hello world")
        await authed_page.click("#send-btn")

        # Wait for the message row to appear
        await authed_page.wait_for_selector(".msg-row-user", timeout=5000)
        text = await authed_page.text_content(".msg-row-user .msg-content")
        assert text is not None
        assert "hello world" in text

    async def test_empty_state_hides_on_first_message(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        # Empty state is visible before any messages
        assert await authed_page.is_visible("#empty-state")

        await authed_page.fill("#message-input", "test")
        await authed_page.click("#send-btn")

        await authed_page.wait_for_selector(".msg-row-user", timeout=5000)
        assert await authed_page.is_hidden("#empty-state")

    async def test_stream_token_renders_and_completes(
        self, authed_page: Page, chat_id: str, test_server: SimpleNamespace
    ) -> None:
        msg_id = "stream-test-1"
        # Inject a stream_token — creates a streaming message row
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "stream_token",
            {
                "text": "Hello from the agent",
                "message_id": msg_id,
            },
        )
        await authed_page.wait_for_selector(
            f'[data-message-id="{msg_id}"]', timeout=5000
        )
        # Should have streaming class while in progress
        el = authed_page.locator(f'[data-message-id="{msg_id}"] .msg-content')
        assert "streaming" in (await el.get_attribute("class") or "")

        # Inject message_complete to finalise it
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "message_complete",
            {
                "message_id": msg_id,
                "text": "Hello from the agent",
            },
        )
        await asyncio.sleep(0.3)  # allow rAF / DOM update
        final_classes = await el.get_attribute("class") or ""
        assert "streaming" not in final_classes

    async def test_tool_indicator_appears_with_tool_name(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "tool_start",
            {
                "tool": "Bash",
                "command": "pytest",
                "message_id": "tool-test-1",
            },
        )
        await authed_page.wait_for_selector(".msg-row-tool", timeout=5000)
        name_el = authed_page.locator(".msg-row-tool .tool-name")
        assert await name_el.count() > 0
        tool_text = await name_el.first.text_content()
        assert tool_text == "Bash"

    async def test_markdown_code_block_rendered(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        code_message = "```python\nprint('hello')\n```"
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "message",
            {
                "role": "assistant",
                "text": code_message,
                "message_id": "md-test-1",
            },
        )
        await authed_page.wait_for_selector(".msg-row-assistant pre code", timeout=5000)
        # Raw backticks must NOT appear as plain text
        raw_text = await authed_page.text_content(".msg-row-assistant .msg-content")
        assert "```" not in (raw_text or "")

    async def test_enter_key_sends_message(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "keyboard send")
        await authed_page.press("#message-input", "Enter")

        await authed_page.wait_for_selector(".msg-row-user", timeout=5000)
        text = await authed_page.text_content(".msg-row-user .msg-content")
        assert "keyboard send" in (text or "")

        await wait_for(
            lambda: bool(test_server.received),
            msg="Server did not receive 'keyboard send' message",
        )
        assert any("keyboard send" in r["text"] for r in test_server.received)

    async def test_shift_enter_inserts_newline(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "line one")
        await authed_page.press("#message-input", "Shift+Enter")
        await authed_page.type("#message-input", "line two")
        await authed_page.press("#message-input", "Enter")

        await wait_for(
            lambda: bool(test_server.received),
            msg="Server did not receive multi-line message",
        )
        msg = test_server.received[-1]["text"]
        assert "line one" in msg
        assert "line two" in msg

    async def test_message_delivery_to_server(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await authed_page.fill("#message-input", "roundtrip test")
        await authed_page.click("#send-btn")

        await wait_for(
            lambda: any(r["text"] == "roundtrip test" for r in test_server.received),
            msg="Server did not receive 'roundtrip test' message",
        )

    async def test_message_delete_removes_element(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        msg_id = "del-test-1"
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "message",
            {"role": "assistant", "text": "will be deleted", "message_id": msg_id},
        )
        await authed_page.wait_for_selector(
            f'[data-message-id="{msg_id}"]', timeout=5000
        )

        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "message_delete",
            {"message_id": msg_id},
        )
        await authed_page.wait_for_selector(
            f'[data-message-id="{msg_id}"]', state="detached", timeout=5000
        )

    async def test_error_message_renders_as_system(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "error",
            {"reason": "Something failed"},
        )
        await authed_page.wait_for_selector(".msg-row-system", timeout=5000)
        text = await authed_page.text_content(".msg-row-system .msg-content")
        assert "Something failed" in (text or "")

    async def test_tool_start_shows_command_text(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        await inject(
            test_server.ws_handler,
            test_server.chat_ids,
            "tool_start",
            {"tool": "Read", "command": "src/main.py", "message_id": "tool-cmd-1"},
        )
        await authed_page.wait_for_selector(".tool-cmd", timeout=5000)
        cmd_text = await authed_page.text_content(".tool-cmd")
        assert "src/main.py" in (cmd_text or "")

    async def test_send_button_disabled_when_input_empty(
        self, authed_page: Page, test_server: SimpleNamespace
    ) -> None:
        # Empty input — button should be disabled
        is_disabled = await authed_page.evaluate(
            "document.querySelector('#send-btn').disabled"
        )
        assert is_disabled

        # Fill text — button should become enabled
        await authed_page.fill("#message-input", "test")
        await authed_page.dispatch_event("#message-input", "input")
        await asyncio.sleep(0.1)
        is_disabled = await authed_page.evaluate(
            "document.querySelector('#send-btn').disabled"
        )
        assert not is_disabled

        # Clear text — button should be disabled again
        await authed_page.fill("#message-input", "")
        await authed_page.dispatch_event("#message-input", "input")
        await asyncio.sleep(0.1)
        is_disabled = await authed_page.evaluate(
            "document.querySelector('#send-btn').disabled"
        )
        assert is_disabled
