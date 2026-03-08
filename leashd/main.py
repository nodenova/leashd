"""CLI entry point for leashd."""

import asyncio
import signal
import sys

import structlog

from leashd.app import build_engine
from leashd.config_store import inject_global_config_as_env
from leashd.connectors.base import BaseConnector
from leashd.core.config import LeashdConfig
from leashd.exceptions import ConfigError, ConnectorError, LeashdError

logger = structlog.get_logger()


def _detect_connector(config: LeashdConfig) -> str | None:
    """Auto-detect which connector to use from config fields.

    Explicit ``config.connector`` wins; otherwise first token found wins.
    """
    if config.connector:
        return config.connector
    if config.telegram_bot_token:
        return "telegram"
    if config.slack_bot_token:
        return "slack"
    if config.whatsapp_gateway_url:
        return "whatsapp"
    if config.signal_phone_number:
        return "signal"
    if config.imessage_server_url:
        return "imessage"
    return None


def _build_connector(name: str, config: LeashdConfig) -> BaseConnector:
    """Lazy-import and construct the named connector."""
    if name == "telegram":
        from leashd.connectors.telegram import TelegramConnector

        return TelegramConnector(config.telegram_bot_token)  # type: ignore[arg-type]
    if name == "slack":
        from leashd.connectors.slack import SlackConnector

        return SlackConnector(
            bot_token=config.slack_bot_token,  # type: ignore[arg-type]
            app_token=config.slack_app_token,  # type: ignore[arg-type]
        )
    if name == "whatsapp":
        from leashd.connectors.whatsapp import WhatsAppConnector

        return WhatsAppConnector(
            gateway_url=config.whatsapp_gateway_url,  # type: ignore[arg-type]
            gateway_token=config.whatsapp_gateway_token,  # type: ignore[arg-type]
            phone_number=config.whatsapp_phone_number,  # type: ignore[arg-type]
        )
    if name == "signal":
        from leashd.connectors.signal_connector import SignalConnector

        return SignalConnector(
            phone_number=config.signal_phone_number,  # type: ignore[arg-type]
            cli_url=config.signal_cli_url,  # type: ignore[arg-type]
        )
    if name == "imessage":
        from leashd.connectors.imessage import IMessageConnector

        return IMessageConnector(
            server_url=config.imessage_server_url,  # type: ignore[arg-type]
            password=config.imessage_password,  # type: ignore[arg-type]
        )
    msg = f"Unknown connector: {name}"
    raise ConfigError(msg)


async def _run_cli(config: LeashdConfig) -> None:
    engine = build_engine(config)
    await engine.startup()

    logger.info(
        "cli_starting",
        working_directories=[str(d) for d in config.approved_directories],
    )
    print(f"leashd ready — working in {config.approved_directories}")
    print("Enter a prompt (Ctrl+D to exit):\n")

    try:
        while True:
            try:
                prompt = input("> ")
            except EOFError:
                break

            if not prompt.strip():
                continue

            print("\nProcessing...\n")
            response = await engine.handle_message(
                user_id="cli",
                text=prompt,
                chat_id="cli",
            )
            print(f"\n{response}\n")
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("cli_shutting_down")
        await engine.shutdown()
        print("\nShutdown complete.")


async def _run_connector(config: LeashdConfig, connector_name: str) -> None:
    connector = _build_connector(connector_name, config)
    engine = build_engine(config, connector=connector)
    await engine.startup()
    try:
        await connector.start()
    except Exception:
        logger.error("connector_startup_failed", connector=connector_name)
        await engine.shutdown()
        raise

    logger.info(
        "connector_starting",
        connector=connector_name,
        working_directories=[str(d) for d in config.approved_directories],
    )
    print(
        f"leashd ready via {connector_name} — working in {config.approved_directories}"
    )

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop_event.set)

    if hasattr(signal, "SIGHUP"):
        _reload_tasks: set[asyncio.Task[None]] = set()

        def _schedule_reload() -> None:
            task = asyncio.ensure_future(engine.reload_config())
            _reload_tasks.add(task)
            task.add_done_callback(_reload_tasks.discard)

        loop.add_signal_handler(signal.SIGHUP, _schedule_reload)

    try:
        await stop_event.wait()
    finally:
        logger.info("connector_shutting_down", connector=connector_name)
        await connector.stop()
        await engine.shutdown()
        print("\nShutdown complete.")


async def _main() -> None:
    from leashd.daemon import cleanup as daemon_cleanup

    inject_global_config_as_env()

    try:
        config = LeashdConfig()  # type: ignore[call-arg]  # pydantic-settings loads from env
    except (ConfigError, LeashdError, ValueError) as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        print(
            "Run 'leashd init' or set LEASHD_APPROVED_DIRECTORIES.",
            file=sys.stderr,
        )
        sys.exit(1)

    connector_name = _detect_connector(config)
    try:
        if connector_name:
            await _run_connector(config, connector_name)
        else:
            await _run_cli(config)
    except ConnectorError as e:
        print(f"Connector failed: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        daemon_cleanup()


def start() -> None:
    """Start the engine — called by cli.py after smart-start checks."""
    asyncio.run(_main())


def run() -> None:
    """Entry point registered in pyproject.toml. Delegates to CLI router."""
    from leashd.cli import main as cli_main

    cli_main()
