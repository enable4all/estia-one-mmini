"""The bridge's process entry: the command line (`local-assistant`, `python -m local_assistant`), bridge.log and
launchd.log kept to their sizes in-process, signal-cli as the config says, and the run loop that stops on SIGTERM and
SIGINT. `app.py` hands on `main`, `model_pin`, `setup_logging` and `signal_client`: the names the tests, the console
script and `__main__` import from it."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import logging.handlers
import os
import signal as os_signal
import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, TextIO

from local_assistant import spotify_auth
from local_assistant.config import DEFAULT_CONFIG_PATH, Config, ConfigError, find_java_home, load_config
from local_assistant.fetcher import Fetcher
from local_assistant.gemma import ModelPin, OllamaGemma
from local_assistant.mail_auth import MailAuthError, TokenStore, device_login
from local_assistant.signal_io import SignalClient, call_env, signal_cli_command, signal_data
from local_assistant.spotify_auth import SpotifyAuthError, SpotifyTokens, login
from local_assistant.store import Store

if TYPE_CHECKING:
    from local_assistant.app import App

log = logging.getLogger("local_assistant")  # the app's own logger: its lines keep their name

BRIDGE_LOG_BYTES = 10_000_000  # bridge.log is rotated in-process at this size ...
BRIDGE_LOG_FILES = 5  # ... keeping this many older files (bridge.log.1 to .5)
STREAM_LOG_BYTES = 10_000_000  # launchd.log (what the bridge writes to stdout and stderr) is emptied over this size


def cap_file(fd: int, limit: int) -> bool:
    """Empty the regular file behind `fd` when it is larger than `limit` (a terminal or a pipe is left alone);
    whether it was emptied. launchd opens the daemon's log for appending, so writing goes on at the new end."""
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= limit:
            return False
        os.ftruncate(fd, 0)
        with contextlib.suppress(OSError):
            os.lseek(fd, 0, os.SEEK_SET)
        return True
    except OSError:
        return False


class CappedStream:
    """sys.stdout or sys.stderr with its file kept under a size: checked before every write."""

    def __init__(self, stream: TextIO, limit: int) -> None:
        self._stream = stream
        self._limit = limit

    def write(self, text: str) -> int:
        with contextlib.suppress(OSError, ValueError):
            self._stream.flush()
            cap_file(self._stream.fileno(), self._limit)
        return self._stream.write(text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def cap_streams(limit: int) -> None:
    """launchd.log: the bridge's stdout and stderr (uncaught errors, Python's warnings) go there, outside bridge.log.
    Emptied now when over `limit`, and before any write that finds it over."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        try:
            fd = stream.fileno()
        except (OSError, ValueError, AttributeError):
            continue
        if stat.S_ISREG(os.fstat(fd).st_mode):
            cap_file(fd, limit)
            setattr(sys, name, CappedStream(stream, limit))


def setup_logging(
    log_dir: Path,
    *,
    foreground: bool,
    bridge_cap: int = BRIDGE_LOG_BYTES,
    stream_cap: int = STREAM_LOG_BYTES,
) -> None:
    """bridge.log rotated in-process (10 MB x 5 older files), launchd.log capped in-process: nothing outside the bridge
    (no newsyslog, no root) touches its logs. Every file it makes is private (umask 077, as the daemon's Umask)."""
    os.umask(0o077)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_dir.chmod(0o700)
    cap_streams(stream_cap)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    handler = logging.handlers.RotatingFileHandler(
        log_dir / "bridge.log", maxBytes=bridge_cap, backupCount=BRIDGE_LOG_FILES, encoding="utf-8"
    )
    with contextlib.suppress(OSError):
        (log_dir / "bridge.log").chmod(0o600)  # one made before the umask (an older install)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if foreground:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)


def signal_client(cfg: Config) -> SignalClient:
    """signal-cli as configured: with [calls] the JVM build (Homebrew's native one cannot place calls) with Java,
    IPv4 and the call helper in its environment, subscribed to call events."""
    if not cfg.calls_enabled:
        return SignalClient(
            signal_cli_command(cfg.signal_cli, cfg.bot_number, config_dir=signal_data()), cfg.allowed_acis
        )
    cfg.call_socket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    java_home = cfg.java_home or find_java_home()
    if java_home is None:
        log.warning("calls: no Java found with /usr/libexec/java_home; set java_home in [calls] if signal-cli fails")
    env = call_env(java_home=java_home, tunnel=cfg.call_tunnel, socket_dir=cfg.call_socket_dir, home=Path.home())
    command = signal_cli_command(str(cfg.call_signal_cli), cfg.bot_number, jvm=True, config_dir=signal_data())
    return SignalClient(command, cfg.allowed_acis, env=env, calls=True)


async def _serve(app: App) -> None:
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    if task is not None:
        for signum in (os_signal.SIGTERM, os_signal.SIGINT):
            loop.add_signal_handler(signum, task.cancel)
    try:
        await app.run()
    except asyncio.CancelledError:
        log.info("stopping")
    finally:
        if app.calls:
            await app.calls.hangup()  # before signal-cli stops, so the caller hears the call end
        await app.signal.stop()


def model_pin(cfg: Config, gemma: OllamaGemma) -> ModelPin:
    """Always: Ollama's own entry for the alias is read at the start, every 5 minutes and before every chat; once a
    read sees a cloud model, no message goes to it; with model_digest, any other digest pauses answers too."""
    return ModelPin(gemma.model_entry, cfg.model_digest)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="local-assistant", description="Private Signal assistant on the local Gemma")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--foreground", action="store_true", help="also log to the terminal")
    parser.add_argument(
        "--mail-login", metavar="ADDRESS", help="sign in one configured mailbox (device code), then exit"
    )
    parser.add_argument(
        "--mail-only",
        action="store_true",
        help="with --mail-login: sign in mail alone, without the mailbox's calendar (Calendars.Read)",
    )
    parser.add_argument(
        "--spotify-login", action="store_true", help="sign in to Spotify (paste the address), then exit"
    )
    args = parser.parse_args(argv)
    if args.mail_only and not args.mail_login:
        parser.error("--mail-only goes with --mail-login <address>")
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"local-assistant: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if args.mail_login:
        try:
            signed_in = asyncio.run(
                device_login(cfg, args.mail_login, TokenStore(cfg.mail_token_path), mail_only=args.mail_only)
            )
        except MailAuthError as exc:
            print(f"local-assistant: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        print(f"signed in: {signed_in} (the bridge picks the new sign-in up by itself)")
        return
    if args.spotify_login:
        if not cfg.spotify_enabled:
            print("local-assistant: add [spotify] with your app's client_id to the config first", file=sys.stderr)
            raise SystemExit(2)
        try:
            tokens = SpotifyTokens(cfg.spotify_token_path, cfg.spotify_client_id)
            asyncio.run(login(tokens, read=spotify_auth.read_answer, name=cfg.name))
        except SpotifyAuthError as exc:
            print(f"local-assistant: {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        return
    setup_logging(cfg.log_dir, foreground=args.foreground)
    store = Store.open(cfg.db_path)
    signal = signal_client(cfg)
    gemma = OllamaGemma(
        cfg.ollama_base_url,
        cfg.model,
        unreachable_seconds=cfg.gemma_unreachable_seconds,
        request_timeout=cfg.gemma_request_timeout,
    )
    pin = model_pin(cfg, gemma)
    log.info(
        "starting: model %s (%s), %d allowed account(s)",
        cfg.model,
        "pinned" if cfg.model_digest else "not pinned",
        len(cfg.allowed_acis),
    )
    from local_assistant.app import App  # here and not above: app.py imports this module

    try:
        asyncio.run(_serve(App(cfg, store, signal, gemma, Fetcher(), model_pin=pin)))
    finally:
        store.close()
