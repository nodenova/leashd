"""Tests for WebUI livereload in dev mode."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from leashd.connectors.web import WebConnector
from leashd.core.config import LeashdConfig
from leashd.web.app import create_app
from leashd.web.models import ServerMessage
from leashd.web.ws_handler import WebSocketHandler


@pytest.fixture
def dev_config(tmp_path):
    return LeashdConfig(
        approved_directories=[tmp_path],
        web_enabled=True,
        web_api_key="test-key",
        web_port=9998,
        web_dev_mode=True,
    )


@pytest.fixture
def prod_config(tmp_path):
    return LeashdConfig(
        approved_directories=[tmp_path],
        web_enabled=True,
        web_api_key="test-key",
        web_port=9998,
    )


class TestReloadMessageType:
    def test_reload_server_message_validates(self):
        msg = ServerMessage(type="reload")
        assert msg.type == "reload"
        assert msg.payload == {}


class TestWatcherLifecycle:
    async def test_watcher_task_created_in_dev_mode(self, dev_config):
        connector = WebConnector(dev_config)
        mock_server = MagicMock()
        mock_server.serve = AsyncMock()
        with (
            patch("uvicorn.Config"),
            patch("uvicorn.Server", return_value=mock_server),
            patch.object(connector, "_watch_static_files", new_callable=AsyncMock),
        ):
            await connector.start()
            assert connector._watcher_task is not None
            await connector.stop()

    async def test_no_watcher_task_without_dev_mode(self, prod_config):
        connector = WebConnector(prod_config)
        mock_server = MagicMock()
        mock_server.serve = AsyncMock()
        with (
            patch("uvicorn.Config"),
            patch("uvicorn.Server", return_value=mock_server),
        ):
            await connector.start()
            assert connector._watcher_task is None
            await connector.stop()

    async def test_stop_cancels_watcher(self, dev_config):
        connector = WebConnector(dev_config)
        connector._watcher_stop = asyncio.Event()
        connector._watcher_task = asyncio.create_task(asyncio.sleep(100))
        connector._server = MagicMock()
        connector._server.should_exit = False

        await connector.stop()

        assert connector._watcher_stop.is_set()
        assert connector._watcher_task is None


class TestFileWatcher:
    async def test_file_change_broadcasts_reload(self, dev_config):
        connector = WebConnector(dev_config)
        connector._ws_handler.broadcast = AsyncMock()

        async def fake_awatch(*_args, **_kwargs):
            yield {("modified", "app.js")}

        with patch("watchfiles.awatch", fake_awatch):
            await connector._watch_static_files()

        connector._ws_handler.broadcast.assert_awaited_once()
        msg = connector._ws_handler.broadcast.call_args[0][0]
        assert msg.type == "reload"

    async def test_graceful_when_watchfiles_missing(self, dev_config):
        connector = WebConnector(dev_config)
        with patch.dict("sys.modules", {"watchfiles": None}):
            await connector._watch_static_files()


class TestNoCacheMiddleware:
    def test_nocache_headers_in_dev_mode(self, dev_config):
        from fastapi.testclient import TestClient

        ws_handler = WebSocketHandler(api_key="test-key")
        app = create_app(dev_config, ws_handler)
        client = TestClient(app)

        resp = client.get("/api/health")
        assert "no-cache" in resp.headers.get("cache-control", "")

    def test_no_nocache_headers_in_production(self, prod_config):
        from fastapi.testclient import TestClient

        ws_handler = WebSocketHandler(api_key="test-key")
        app = create_app(prod_config, ws_handler)
        client = TestClient(app)

        resp = client.get("/api/health")
        assert "no-cache" not in resp.headers.get("cache-control", "")
