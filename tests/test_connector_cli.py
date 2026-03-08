"""Tests for connector CLI — config_store helpers, setup wizard, CLI handlers."""

from unittest.mock import patch

import pytest

from leashd.config_store import (
    get_active_connector_name,
    get_connector_config,
    load_global_config,
    save_global_config,
)
from leashd.setup import _configure_connector


@pytest.fixture
def fake_config_dir(tmp_path):
    """Redirect config_path() to a temp directory."""
    fake_path = tmp_path / ".leashd" / "config.yaml"
    fake_ws_path = tmp_path / ".leashd" / "workspaces.yaml"
    with (
        patch("leashd.config_store._CONFIG_FILE", fake_path),
        patch("leashd.config_store._WORKSPACES_FILE", fake_ws_path),
    ):
        yield fake_path


# --- get_active_connector_name ---


class TestGetActiveConnectorName:
    def test_explicit_connector_wins(self, fake_config_dir):
        save_global_config(
            {
                "connector": "slack",
                "telegram": {"bot_token": "tok"},
                "slack": {"bot_token": "xoxb-tok"},
            }
        )
        assert get_active_connector_name() == "slack"

    def test_telegram_detected(self, fake_config_dir):
        save_global_config({"telegram": {"bot_token": "tok"}})
        assert get_active_connector_name() == "telegram"

    def test_slack_detected(self, fake_config_dir):
        save_global_config({"slack": {"bot_token": "xoxb-tok"}})
        assert get_active_connector_name() == "slack"

    def test_whatsapp_detected(self, fake_config_dir):
        save_global_config({"whatsapp": {"gateway_url": "ws://localhost:18789"}})
        assert get_active_connector_name() == "whatsapp"

    def test_signal_detected(self, fake_config_dir):
        save_global_config({"signal": {"phone_number": "+15551234567"}})
        assert get_active_connector_name() == "signal"

    def test_imessage_detected(self, fake_config_dir):
        save_global_config({"imessage": {"server_url": "http://192.168.1.100:1234"}})
        assert get_active_connector_name() == "imessage"

    def test_none_when_empty(self, fake_config_dir):
        save_global_config({"approved_directories": ["/tmp/a"]})
        assert get_active_connector_name() is None

    def test_non_dict_section_skipped(self, fake_config_dir):
        save_global_config({"slack": "garbage"})
        assert get_active_connector_name() is None

    def test_detection_priority_telegram_first(self, fake_config_dir):
        save_global_config(
            {
                "telegram": {"bot_token": "tok"},
                "slack": {"bot_token": "xoxb-tok"},
            }
        )
        assert get_active_connector_name() == "telegram"

    def test_accepts_data_param(self):
        data = {"signal": {"phone_number": "+1555"}}
        assert get_active_connector_name(data) == "signal"

    def test_explicit_unknown_connector_ignored(self, fake_config_dir):
        save_global_config({"connector": "unknown", "slack": {"bot_token": "xoxb"}})
        assert get_active_connector_name() == "slack"

    def test_empty_detect_key_not_detected(self, fake_config_dir):
        save_global_config({"slack": {"app_token": "xapp-tok"}})
        assert get_active_connector_name() is None


# --- get_connector_config ---


class TestGetConnectorConfig:
    def test_returns_tuple(self, fake_config_dir):
        save_global_config({"slack": {"bot_token": "xoxb-tok", "app_token": "xapp"}})
        name, section = get_connector_config()
        assert name == "slack"
        assert section["bot_token"] == "xoxb-tok"
        assert section["app_token"] == "xapp"

    def test_missing_returns_none(self, fake_config_dir):
        save_global_config({"approved_directories": ["/tmp/a"]})
        name, section = get_connector_config()
        assert name is None
        assert section == {}

    def test_non_dict_section_returns_empty(self, fake_config_dir):
        save_global_config({"connector": "slack", "slack": "garbage"})
        name, section = get_connector_config()
        assert name == "slack"
        assert section == {}

    def test_accepts_data_param(self):
        data = {"signal": {"phone_number": "+1555", "cli_url": "http://localhost"}}
        name, section = get_connector_config(data)
        assert name == "signal"
        assert section["phone_number"] == "+1555"


# --- _configure_connector ---


class TestConfigureConnector:
    def test_slack_all_fields(self):
        inputs = iter(["xoxb-my-token", "xapp-my-token"])
        result = _configure_connector("slack", {}, input_fn=lambda _: next(inputs))
        assert result["bot_token"] == "xoxb-my-token"
        assert result["app_token"] == "xapp-my-token"

    def test_signal_all_fields(self):
        inputs = iter(["+15551234567", "http://localhost:9090"])
        result = _configure_connector("signal", {}, input_fn=lambda _: next(inputs))
        assert result["phone_number"] == "+15551234567"
        assert result["cli_url"] == "http://localhost:9090"

    def test_whatsapp_all_fields(self):
        inputs = iter(["ws://localhost:18789", "my-token", "+15551234567"])
        result = _configure_connector("whatsapp", {}, input_fn=lambda _: next(inputs))
        assert result["gateway_url"] == "ws://localhost:18789"
        assert result["gateway_token"] == "my-token"
        assert result["phone_number"] == "+15551234567"

    def test_imessage_all_fields(self):
        inputs = iter(["http://192.168.1.100:1234", "my-password"])
        result = _configure_connector("imessage", {}, input_fn=lambda _: next(inputs))
        assert result["server_url"] == "http://192.168.1.100:1234"
        assert result["password"] == "my-password"

    def test_preserves_existing_on_empty_input(self):
        existing = {"bot_token": "xoxb-existing", "app_token": "xapp-existing"}
        result = _configure_connector("slack", existing, input_fn=lambda _: "")
        assert result["bot_token"] == "xoxb-existing"
        assert result["app_token"] == "xapp-existing"

    def test_overrides_existing_with_new_input(self):
        existing = {"bot_token": "xoxb-old"}
        inputs = iter(["xoxb-new", "xapp-new"])
        result = _configure_connector(
            "slack", existing, input_fn=lambda _: next(inputs)
        )
        assert result["bot_token"] == "xoxb-new"
        assert result["app_token"] == "xapp-new"

    def test_unknown_connector_returns_existing(self):
        existing = {"key": "value"}
        result = _configure_connector("foobar", existing, input_fn=lambda _: "x")
        assert result == existing

    def test_telegram_user_id_validated_as_int(self):
        inputs = iter(["my-bot-token", "abc123"])
        result = _configure_connector("telegram", {}, input_fn=lambda _: next(inputs))
        assert result["bot_token"] == "my-bot-token"
        assert "allowed_user_ids" not in result

    def test_telegram_valid_user_id_stored_as_list(self):
        inputs = iter(["my-bot-token", "987654321"])
        result = _configure_connector("telegram", {}, input_fn=lambda _: next(inputs))
        assert result["allowed_user_ids"] == ["987654321"]

    def test_shows_current_list_value_in_prompt(self):
        existing = {"allowed_user_ids": ["111", "222"]}
        prompts = []

        def capture_input(prompt):
            prompts.append(prompt)
            return ""

        _configure_connector("telegram", existing, input_fn=capture_input)
        all_prompts = " ".join(prompts)
        assert "111, 222" in all_prompts


# --- CLI handlers ---


class TestCliConnectorShow:
    def test_no_connector(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_show

        _handle_connector_show()
        captured = capsys.readouterr()
        assert "No connector configured" in captured.out
        assert "leashd connector setup" in captured.out

    def test_slack_configured(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_show

        save_global_config(
            {
                "slack": {
                    "bot_token": "xoxb-1234567890-abc",
                    "app_token": "xapp-tok123456",
                }
            }
        )
        _handle_connector_show()
        captured = capsys.readouterr()
        assert "Slack" in captured.out
        assert "xoxb-123..." in captured.out
        assert "xoxb-1234567890-abc" not in captured.out

    def test_signal_configured(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_show

        save_global_config(
            {
                "signal": {
                    "phone_number": "+15551234567",
                    "cli_url": "http://localhost:8080",
                }
            }
        )
        _handle_connector_show()
        captured = capsys.readouterr()
        assert "Signal" in captured.out
        assert "+15551234567" in captured.out
        assert "http://localhost:8080" in captured.out

    def test_imessage_masks_password(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_show

        save_global_config(
            {"imessage": {"server_url": "http://192.168.1.100:1234", "password": "***"}}
        )
        _handle_connector_show()
        captured = capsys.readouterr()
        assert "iMessage" in captured.out

    def test_telegram_with_list_field(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_show

        save_global_config(
            {
                "telegram": {
                    "bot_token": "123456789:ABC-tok",
                    "allowed_user_ids": ["111", "222"],
                }
            }
        )
        _handle_connector_show()
        captured = capsys.readouterr()
        assert "Telegram" in captured.out
        assert "111, 222" in captured.out

    def test_whatsapp_configured(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_show

        save_global_config(
            {
                "whatsapp": {
                    "gateway_url": "ws://localhost:18789",
                    "gateway_token": "secret-gateway-tok",
                    "phone_number": "+15551234567",
                }
            }
        )
        _handle_connector_show()
        captured = capsys.readouterr()
        assert "WhatsApp" in captured.out
        assert "ws://localhost:18789" in captured.out
        assert "secret-g..." in captured.out
        assert "secret-gateway-tok" not in captured.out


class TestCliConnectorSetup:
    def test_creates_new_config(self, fake_config_dir, capsys, monkeypatch):
        from leashd.cli import _handle_connector_setup

        for key in ("LEASHD_SLACK_BOT_TOKEN", "LEASHD_SLACK_APP_TOKEN"):
            monkeypatch.delenv(key, raising=False)

        inputs = iter(["xoxb-new-tok", "xapp-new-tok"])
        with patch("builtins.input", side_effect=inputs):
            _handle_connector_setup("slack")

        captured = capsys.readouterr()
        assert "configured" in captured.out
        data = load_global_config()
        assert data["slack"]["bot_token"] == "xoxb-new-tok"
        assert data["slack"]["app_token"] == "xapp-new-tok"

    def test_interactive_name_prompt(self, fake_config_dir, capsys, monkeypatch):
        from leashd.cli import _handle_connector_setup

        for key in ("LEASHD_SIGNAL_PHONE_NUMBER", "LEASHD_SIGNAL_CLI_URL"):
            monkeypatch.delenv(key, raising=False)

        inputs = iter(["signal", "+15551234567", "http://localhost:8080"])
        with patch("builtins.input", side_effect=inputs):
            _handle_connector_setup(None)

        data = load_global_config()
        assert data["signal"]["phone_number"] == "+15551234567"

    def test_reconfigure_declined(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_setup

        save_global_config({"slack": {"bot_token": "xoxb-existing"}})
        with patch("builtins.input", return_value="n"):
            _handle_connector_setup("slack")

        captured = capsys.readouterr()
        assert "Kept existing" in captured.out
        data = load_global_config()
        assert data["slack"]["bot_token"] == "xoxb-existing"

    def test_unknown_name_exits(self, fake_config_dir):
        from leashd.cli import _handle_connector_setup

        with pytest.raises(SystemExit):
            _handle_connector_setup("foobar")

    def test_eof_during_name_prompt(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_setup

        with patch("builtins.input", side_effect=EOFError):
            _handle_connector_setup(None)

        captured = capsys.readouterr()
        assert "Aborted" in captured.out

    def test_empty_name_no_selection(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_setup

        with patch("builtins.input", return_value=""):
            _handle_connector_setup(None)

        captured = capsys.readouterr()
        assert "No connector selected" in captured.out

    def test_reconfigure_accepted(self, fake_config_dir, capsys, monkeypatch):
        from leashd.cli import _handle_connector_setup

        save_global_config({"slack": {"bot_token": "xoxb-old"}})
        for key in ("LEASHD_SLACK_BOT_TOKEN", "LEASHD_SLACK_APP_TOKEN"):
            monkeypatch.delenv(key, raising=False)

        # "y"=accept reconfigure, then new field values
        inputs = iter(["y", "xoxb-new-tok", "xapp-new-tok"])
        with patch("builtins.input", side_effect=inputs):
            _handle_connector_setup("slack")

        captured = capsys.readouterr()
        assert "configured" in captured.out
        data = load_global_config()
        assert data["slack"]["bot_token"] == "xoxb-new-tok"

    def test_eof_during_reconfigure_prompt(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_setup

        save_global_config({"slack": {"bot_token": "xoxb-existing"}})

        with patch("builtins.input", side_effect=EOFError):
            _handle_connector_setup("slack")

        captured = capsys.readouterr()
        assert "Kept existing" in captured.out
        data = load_global_config()
        assert data["slack"]["bot_token"] == "xoxb-existing"

    def test_non_dict_existing_section_handled(
        self, fake_config_dir, capsys, monkeypatch
    ):
        from leashd.cli import _handle_connector_setup

        save_global_config({"slack": "garbage"})
        monkeypatch.delenv("LEASHD_SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("LEASHD_SLACK_APP_TOKEN", raising=False)

        inputs = iter(["xoxb-tok", "xapp-tok"])
        with patch("builtins.input", side_effect=inputs):
            _handle_connector_setup("slack")

        captured = capsys.readouterr()
        assert "configured" in captured.out
        data = load_global_config()
        assert data["slack"]["bot_token"] == "xoxb-tok"


class TestCliConnectorRemove:
    def test_removes_active_connector(self, fake_config_dir, capsys, monkeypatch):
        from leashd.cli import _handle_connector_remove

        save_global_config(
            {"slack": {"bot_token": "xoxb-tok", "app_token": "xapp-tok"}}
        )
        for key in ("LEASHD_SLACK_BOT_TOKEN", "LEASHD_SLACK_APP_TOKEN"):
            monkeypatch.delenv(key, raising=False)

        with patch("builtins.input", return_value="y"):
            _handle_connector_remove()

        captured = capsys.readouterr()
        assert "removed" in captured.out
        data = load_global_config()
        assert "slack" not in data

    def test_removes_explicit_connector_field(
        self, fake_config_dir, capsys, monkeypatch
    ):
        from leashd.cli import _handle_connector_remove

        save_global_config({"connector": "slack", "slack": {"bot_token": "xoxb-tok"}})
        monkeypatch.delenv("LEASHD_SLACK_BOT_TOKEN", raising=False)

        with patch("builtins.input", return_value="y"):
            _handle_connector_remove()

        data = load_global_config()
        assert "slack" not in data
        assert "connector" not in data

    def test_no_connector_configured(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_remove

        _handle_connector_remove()
        captured = capsys.readouterr()
        assert "Nothing to remove" in captured.out

    def test_decline_remove(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_remove

        save_global_config({"slack": {"bot_token": "xoxb-tok"}})

        with patch("builtins.input", return_value="n"):
            _handle_connector_remove()

        captured = capsys.readouterr()
        assert "Kept existing" in captured.out
        data = load_global_config()
        assert "slack" in data

    def test_eof_during_remove_prompt(self, fake_config_dir, capsys):
        from leashd.cli import _handle_connector_remove

        save_global_config({"slack": {"bot_token": "xoxb-tok"}})

        with patch("builtins.input", side_effect=EOFError):
            _handle_connector_remove()

        captured = capsys.readouterr()
        assert "Kept existing" in captured.out
        data = load_global_config()
        assert "slack" in data


class TestCliConnectorDispatch:
    def test_connector_dispatch(self):
        from leashd.cli import main

        with (
            patch("leashd.cli.inject_global_config_as_env"),
            patch("leashd.cli._handle_connector") as mock_conn,
            patch("sys.argv", ["leashd", "connector"]),
        ):
            main()
            mock_conn.assert_called_once()

    def test_connector_show_dispatch(self):
        from leashd.cli import main

        with (
            patch("leashd.cli.inject_global_config_as_env"),
            patch("leashd.cli._handle_connector") as mock_conn,
            patch("sys.argv", ["leashd", "connector", "show"]),
        ):
            main()
            mock_conn.assert_called_once()

    def test_connector_setup_dispatch(self):
        from leashd.cli import main

        with (
            patch("leashd.cli.inject_global_config_as_env"),
            patch("leashd.cli._handle_connector") as mock_conn,
            patch("sys.argv", ["leashd", "connector", "setup"]),
        ):
            main()
            mock_conn.assert_called_once()

    def test_connector_setup_with_name_dispatch(self):
        from leashd.cli import main

        with (
            patch("leashd.cli.inject_global_config_as_env"),
            patch("leashd.cli._handle_connector") as mock_conn,
            patch("sys.argv", ["leashd", "connector", "setup", "slack"]),
        ):
            main()
            mock_conn.assert_called_once()
            args = mock_conn.call_args[0][0]
            assert args.connector_name == "slack"

    def test_connector_remove_dispatch(self):
        from leashd.cli import main

        with (
            patch("leashd.cli.inject_global_config_as_env"),
            patch("leashd.cli._handle_connector") as mock_conn,
            patch("sys.argv", ["leashd", "connector", "remove"]),
        ):
            main()
            mock_conn.assert_called_once()


# --- Config display ---


class TestConfigDisplayConnector:
    def test_config_shows_slack_connector(
        self, fake_config_dir, tmp_path, capsys, monkeypatch
    ):
        from leashd.cli import _handle_config

        monkeypatch.delenv("LEASHD_APPROVED_DIRECTORIES", raising=False)
        monkeypatch.delenv("LEASHD_TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("LEASHD_ALLOWED_USER_IDS", raising=False)
        monkeypatch.delenv("LEASHD_SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("LEASHD_SLACK_APP_TOKEN", raising=False)

        save_global_config(
            {
                "approved_directories": [str(tmp_path)],
                "slack": {
                    "bot_token": "xoxb-1234567890",
                    "app_token": "xapp-1234567890",
                },
            }
        )
        from leashd.config_store import inject_global_config_as_env

        inject_global_config_as_env()

        _handle_config()
        captured = capsys.readouterr()
        assert "Connector: Slack" in captured.out
        assert "xoxb-123..." in captured.out

    def test_yaml_only_shows_connector(
        self, fake_config_dir, tmp_path, capsys, monkeypatch
    ):
        from leashd.cli import _handle_config

        monkeypatch.delenv("LEASHD_APPROVED_DIRECTORIES", raising=False)
        monkeypatch.delenv("LEASHD_TELEGRAM_BOT_TOKEN", raising=False)

        save_global_config(
            {
                "approved_directories": [str(tmp_path)],
                "signal": {
                    "phone_number": "+15551234567",
                    "cli_url": "http://localhost:8080",
                },
            }
        )

        with patch("leashd.cli._try_resolve_config", return_value=None):
            _handle_config()

        captured = capsys.readouterr()
        assert "Connector: Signal" in captured.out
        assert "+15551234567" in captured.out

    def test_telegram_only_no_connector_section(
        self, fake_config_dir, tmp_path, capsys, monkeypatch
    ):
        from leashd.cli import _handle_config

        monkeypatch.delenv("LEASHD_APPROVED_DIRECTORIES", raising=False)
        monkeypatch.delenv("LEASHD_TELEGRAM_BOT_TOKEN", raising=False)

        save_global_config(
            {
                "approved_directories": [str(tmp_path)],
                "telegram": {"bot_token": "tok12345678"},
            }
        )

        with patch("leashd.cli._try_resolve_config", return_value=None):
            _handle_config()

        captured = capsys.readouterr()
        assert "Connector:" not in captured.out
        assert "tok12345..." in captured.out


# --- _mask_value ---


class TestMaskValue:
    def test_masks_long_sensitive_field(self):
        from leashd.cli import _mask_value

        assert _mask_value("bot_token", "xoxb-1234567890") == "xoxb-123..."

    def test_masks_short_sensitive_field(self):
        from leashd.cli import _mask_value

        assert _mask_value("password", "short") == "***"

    def test_passes_through_non_sensitive(self):
        from leashd.cli import _mask_value

        assert _mask_value("phone_number", "+15551234567") == "+15551234567"

    def test_masks_gateway_token(self):
        from leashd.cli import _mask_value

        assert _mask_value("gateway_token", "secret-gateway-tok") == "secret-g..."

    def test_masks_app_token(self):
        from leashd.cli import _mask_value

        assert _mask_value("app_token", "xapp-1234567890") == "xapp-123..."
