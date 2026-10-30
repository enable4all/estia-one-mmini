"""The owner's maintenance commands that run as her, through `/usr/bin/sudo /usr/local/estia/bin/estia` (which stops the bridge
first and starts it again after: one signal-cli per account, and the lock is read at the start):

    estia signal listIdentities|trust|listDevices|removeDevice [options]   her signal-cli, with the bridge's flags
    estia unlock                                                           ends /lock from the Mac
    estia signal-login                                                     her account moves to one without a number

`signal` runs only those subcommands: nothing that sends, registers or changes her profile; it runs from / with her
config's signal-cli (the JVM build with Java and IPv4 in its environment when [calls] is on). `signal-login` is the one
that registers: it takes over the account without a number whose Account ID, Recovery key and username root read from
your terminal (her user cannot open it) and hands over on stdin, sets her profile name and username, and only then
switches her config to it; her old number's account stays in her signal-cli data as it was.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import time
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from local_assistant import harden, hostsec, ownuser, prompts
from local_assistant.config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config, parse_config
from local_assistant.signal_io import IPV4, RATE_LIMIT_ERROR, TRUST_NEW_IDENTITIES, jvm_opts, signal_data
from local_assistant.store import Store

SUBCOMMANDS = frozenset({"listIdentities", "trust", "listDevices", "removeDevice"})
LOCK_KEYS = ("lock:on", "lock:device", "lock:since")  # commands.LOCK_KEY, LOCK_DEVICE_KEY, LOCK_SINCE_KEY
# lifecycle.PROFILE_NAME_KEY, PROFILE_AVATAR_KEY: cleared after the move, so the bridge sends her name and picture to
# the new account at its next start
PROFILE_KEYS = ("signal_profile_name", "signal_profile_avatar")
REGISTER_SECONDS = 120.0  # one request to Signal's servers (setup.REGISTER_SECONDS)
_MANUAL = ("--receive-mode", "manual")
_WORD = re.compile(r"^[A-Za-z0-9+_.:= -]{1,200}$")
_ACCOUNT_LINE = re.compile(r"""^bot_number\s*=\s*(?:"[^"\n]*"|'[^'\n]*')""", re.MULTILINE)


def signal_command(cfg: Config, words: list[str]) -> tuple[list[str], dict[str, str]]:
    """Her signal-cli for one of SUBCOMMANDS: (argv, extra environment). Anything else is a ValueError."""
    if not words or words[0] not in SUBCOMMANDS:
        raise ValueError(f"only {', '.join(sorted(SUBCOMMANDS))}")
    for word in words[1:]:
        if not _WORD.match(word) or word.startswith("--config") or word == "-c":
            raise ValueError("an option that is not allowed here")
    data = ["--config", str(signal_data())]  # her account record keeps /var/empty as home: named, as the bridge's
    if cfg.calls_enabled:
        java = cfg.java_home
        env = {**({"JAVA_HOME": str(java)} if java is not None else {}), "JAVA_OPTS": jvm_opts(Path.home())}
        return [str(cfg.call_signal_cli), *data, *TRUST_NEW_IDENTITIES, "-a", cfg.bot_number, *words], env
    return [cfg.signal_cli, IPV4, *data, *TRUST_NEW_IDENTITIES, "-a", cfg.bot_number, *words], {}


def unlock(cfg: Config) -> bool:
    """Clear the lock (/lock) in her database: whether she was locked."""
    store = Store.open(cfg.db_path)
    try:
        locked = store.get_state(LOCK_KEYS[0]) == "on"
        for key in LOCK_KEYS:
            store.delete_state(key)
    finally:
        store.close()
    return locked


def login_command(cfg: Config, account: str = "") -> tuple[list[str], dict[str, str]]:
    """Her signal-cli's JSON-RPC, requests on its input: without `account` in multi-account mode (signal-cli 0.14.9
    registers only there), with it for that one account; (argv, extra environment). The build the bridge runs: with
    [calls] the pinned JVM build, with its Java and IPv4 in JAVA_OPTS (as `signal_command`), else her config's
    native build with IPv4 as an argument (review S6: Homebrew's native build may be another version than the JVM
    one after an update, and the bridge then opens what it wrote). The bridge's identity rule and her own data
    folder either way."""
    one = ["-a", account] if account else []
    rest = ["--config", str(signal_data()), *TRUST_NEW_IDENTITIES, *one, "jsonRpc", *_MANUAL]
    if cfg.calls_enabled:
        java = cfg.java_home
        env = {**({"JAVA_HOME": str(java)} if java is not None else {}), "JAVA_OPTS": jvm_opts(Path.home())}
        return [str(cfg.call_signal_cli), *rest], env
    return [cfg.signal_cli, IPV4, *rest], {}


def login_argv(cfg: Config, account: str = "") -> list[str]:
    """`login_command`'s argv: what is shown, and run."""
    return login_command(cfg, account)[0]


def _login_rpc(
    cfg: Config, account: str, method: str, params: dict[str, object], secrets: dict[str, str]
) -> harden.RpcAnswer:
    """One request to her signal-cli (`login_command`), its environment on top of hers."""
    argv, extra = login_command(cfg, account)
    env = {**os.environ, **extra} if extra else None
    return harden.json_rpc_answer(argv, method, params, secrets=secrets, timeout=REGISTER_SECONDS, env=env)


def read_login(stdin: TextIO) -> tuple[str, str, str, str]:
    """(Account ID, Recovery key, username, the six-digit code or "") from the one JSON line root wrote, each checked
    again here (the same normalizers as setup's; older root-run tools send no code). ValueError with the reason,
    never the key."""
    try:
        data = json.loads(stdin.readline())
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise ValueError(prompts.SIGNAL_LOGIN_NOTHING)
    account = hostsec.account_id(str(data.get("account", "")))
    key = hostsec.recovery_key(str(data.get("recovery_key", "")))
    name = hostsec.username(str(data.get("username", "")))
    return account, key, name, hostsec.totp_code(str(data.get("totp", "")))


def switch_config(path: Path, account: str) -> Path:
    """Her config with `bot_number` naming the Account ID, every other line as it was: a private copy of the old one
    first (<name>.bak-<time>, 0600, never over an earlier copy), then the new text through a new file (0600) moved
    into place, checked to load first. The copy's path; ValueError or OSError when it cannot be done."""
    text = path.read_text(encoding="utf-8")
    if len(_ACCOUNT_LINE.findall(text)) != 1:
        raise ValueError("her config has no single bot_number line to change")
    new = _ACCOUNT_LINE.sub(lambda _: f'bot_number = "{account}"', text)
    try:
        if parse_config(tomllib.loads(new)).bot_number != account:
            raise ValueError("her config would not name the new account")
    except (tomllib.TOMLDecodeError, ConfigError) as exc:
        raise ValueError(f"her config would not load: {exc}") from exc
    copy = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    _write_private(copy, text, os.O_EXCL)
    fresh = path.with_name(f".{path.name}.new")
    _write_private(fresh, new, os.O_TRUNC)
    os.replace(fresh, path)
    return copy


def _write_private(path: Path, text: str, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | mode, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    path.chmod(0o600)


def signal_login(cfg: Config, path: Path, stdin: TextIO, say: Callable[[str], None] = print) -> int:
    """Her account moves to one without a number, as her, the bridge stopped: taken over in her own signal-cli data
    (register with `recoveryKey`, on its input; `reregister`, so a second run takes it over again), her profile name
    (signal-cli issue 2124: a move can lose it) and her username set, then her store's profile keys cleared and her
    config switched to the Account ID. A step that fails switches nothing: she keeps running on her account as before. Her
    old account is never unregistered or deleted (`old_account_lines`). 0 when switched."""
    try:
        account, key, name, totp = read_login(stdin)
    except ValueError as exc:
        say(f"✗ {exc} {prompts.SIGNAL_LOGIN_KEPT}")
        return 1
    if account.lower() in {aci.lower() for aci in cfg.allowed_acis}:  # yours: never taken over (review S9)
        say(f"✗ {prompts.SIGNAL_LOGIN_ALLOWED} {prompts.SIGNAL_LOGIN_KEPT}")
        return 1
    # a run before moved it but stopped (a username refused): not registered again (review S8). One her config names
    # already is: Signal logged her out (another device took it over), and this run takes it back (review S2)
    if account != cfg.bot_number and ownuser.has_account(signal_data(), account):
        say(prompts.SIGNAL_LOGIN_HERE)
    else:
        code_shown = " and <code>" if totp else ""
        say("  $ " + " ".join(login_argv(cfg)) + f"   (register <Account ID> with <Recovery key>{code_shown} on stdin)")
        params: dict[str, object] = {"account": account, "recoveryKey": key, "reregister": True}
        if totp:  # Signal asked for it on a try before (review S7)
            params["totp"] = totp
        secrets = {key: "<Recovery key>", account: "<Account ID>", **({totp: "<code>"} if totp else {})}
        done, why, code = _login_rpc(cfg, "", "register", params, secrets)
        if not done and code == RATE_LIMIT_ERROR:
            say(
                f"{prompts.SIGNAL_LOGIN_LIMITED.format(reason=' '.join(why.split())[:300])} {prompts.SIGNAL_LOGIN_KEPT}"
            )
            return 1
        if not done and "totp" in why.lower():
            say(f"{prompts.SIGNAL_LOGIN_TOTP} {prompts.SIGNAL_LOGIN_KEPT}")
            return 1
        if not done:
            say(f"✗ Signal: {why.strip()[:300]}. {prompts.SIGNAL_LOGIN_KEPT}")
            return 1
        say(prompts.SETUP_LOGIN_DONE.format(name=cfg.name))
    key = ""
    shown = " ".join(login_argv(cfg, "<Account ID>"))
    profile = prompts.signal_profile_name(cfg.name)
    say(f"  $ {shown}   (updateProfile on stdin)")
    done, why = _account_rpc(cfg, account, "updateProfile", {"givenName": profile, "familyName": ""})
    say(prompts.SETUP_PROFILE_NAME.format(profile=profile) if done else prompts.SETUP_PROFILE_LATER.format(reason=why))
    say(f"  $ {shown}   (updateAccount {name} on stdin)")
    done, why = _account_rpc(cfg, account, "updateAccount", {"username": name})
    if not done:
        say(prompts.SIGNAL_LOGIN_USERNAME.format(username=name, reason=why.strip()[:200]))
        return 1
    say(prompts.SETUP_USERNAME_SET.format(username=name))
    try:
        store = Store.open(cfg.db_path)
        try:
            for state in PROFILE_KEYS:
                store.delete_state(state)
        finally:
            store.close()
        copy = switch_config(path, account)
    except (OSError, ValueError) as exc:
        say(f"✗ Config: {exc}. {prompts.SIGNAL_LOGIN_KEPT}")
        return 1
    say(prompts.SIGNAL_LOGIN_SWITCHED.format(account=hostsec.shown_account(account), copy=copy))
    say(prompts.SIGNAL_LOGIN_NEXT.format(username=name, kept=old_account_lines(cfg.bot_number)))
    return 0


def old_account_lines(old: str) -> str:
    """`estia signal-login`'s last lines on her old account (review of 0.9.7, S13): kept as it was, and what to do
    about her old chat, for a number (its Registration Lock lapses after 7 days offline) or an account without one."""
    if hostsec.is_account_id(old):
        text = f"{hostsec.OLD_ACCOUNT_KEPT} {hostsec.OLD_ACCOUNT_RISK}"
    else:
        text = f"{hostsec.OLD_NUMBER_KEPT} {hostsec.OLD_NUMBER_RISK}"
    return textwrap.fill(text, 116, subsequent_indent="  ")


def _account_rpc(cfg: Config, account: str, method: str, params: dict[str, object]) -> tuple[bool, str]:
    answer = _login_rpc(cfg, account, method, params, {account: "<Account ID>"})
    return answer.done, answer.why


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="admin", description="Her maintenance commands (run as her).")
    sub = parser.add_subparsers(dest="command", required=True)
    signal_cmd = sub.add_parser("signal")
    signal_cmd.add_argument("words", nargs=argparse.REMAINDER)
    sub.add_parser("unlock")
    sub.add_parser("signal-login", help="her account moves to one without a number (its details on stdin)")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(DEFAULT_CONFIG_PATH)
    except ConfigError as exc:
        print(f"✗ config: {exc}", file=sys.stderr)
        return 1
    if args.command == "signal-login":
        return signal_login(cfg, DEFAULT_CONFIG_PATH, sys.stdin)
    if args.command == "unlock":
        print("✓ Unlocked: she answers again." if unlock(cfg) else "She was not locked.")
        return 0
    try:
        command, extra = signal_command(cfg, list(args.words))
    except ValueError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    done = subprocess.run(command, cwd="/", env={**os.environ, **extra}, check=False)  # her environment (env -i)
    return done.returncode


if __name__ == "__main__":
    raise SystemExit(main())
