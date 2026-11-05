"""The owner's maintenance commands that run as her (`sudo /usr/local/estia/bin/estia signal …` and `… unlock`):
her own signal-cli with the bridge's flags, only the subcommands named, and the lock ended from the Mac."""

import dataclasses
import io
import json
import shlex
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from local_assistant import admin, prompts
from local_assistant.commands import LOCK_DEVICE_KEY, LOCK_KEY, LOCK_SINCE_KEY
from local_assistant.config import load_config
from local_assistant.store import Store

NUMBER = "+306900000000"
ACI = "11111111-2222-4333-8444-555555555555"


def config(tmp_path: Path, calls: bool = False) -> Path:
    path = tmp_path / "config.toml"
    text = (
        f'bot_number = "{NUMBER}"\nallowed_acis = ["{ACI}"]\nsignal_cli = "/usr/local/estia/current/vendor/'
        'signal-cli-native/bin/signal-cli"\n'
    )
    if calls:
        text += f'[voice]\n[calls]\nsignal_cli = "{tmp_path}/jvm/bin/signal-cli"\njava_home = "{tmp_path}/jdk"\n'
    path.write_text(text)
    path.chmod(0o600)
    return path


def test_her_signal_cli_runs_with_the_bridges_flags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Her own data folder by name, as the bridge's signal-cli has it: her account record keeps /var/empty as home,
    where signal-cli would find no account (review I39)."""
    monkeypatch.setenv("HOME", str(tmp_path / "her"))
    cfg = load_config(config(tmp_path))
    argv, env = admin.signal_command(cfg, ["listIdentities", "-n", "+306900000001"])
    data = f"{tmp_path}/her/.local/share/signal-cli"
    assert argv[0].endswith("signal-cli-native/bin/signal-cli")
    assert argv[1:4] == ["-Djava.net.preferIPv4Stack=true", "--config", data]
    assert argv[4:8] == ["--trust-new-identities", "on-first-use", "-a", NUMBER]
    assert argv[8:] == ["listIdentities", "-n", "+306900000001"]
    assert env == {}


def test_with_calls_the_jvm_build_gets_java_and_ipv4_from_its_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = load_config(config(tmp_path, calls=True))
    monkeypatch.setenv("HOME", str(tmp_path / "her"))  # after the load: a long home has no room for the call sockets
    argv, env = admin.signal_command(cfg, ["listDevices"])
    assert argv[0] == f"{tmp_path}/jvm/bin/signal-cli" and "-Djava.net.preferIPv4Stack=true" not in argv
    assert argv[1:3] == ["--config", f"{tmp_path}/her/.local/share/signal-cli"]
    assert env["JAVA_HOME"] == f"{tmp_path}/jdk"
    assert env["JAVA_OPTS"] == f"-Djava.net.preferIPv4Stack=true -Duser.home={tmp_path}/her"


def test_her_commands_name_the_bridges_signal_cli_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """One folder for the bridge, `estia signal`, `estia signal-login` and the Registration Lock: signal_io's."""
    from local_assistant import entry, harden, signal_io

    monkeypatch.setenv("HOME", str(tmp_path / "her"))
    data = str(signal_io.signal_data())
    assert data == f"{tmp_path}/her/.local/share/signal-cli"
    assert getattr(entry, "signal_data") is signal_io.signal_data  # noqa: B009 (the bridge's own import)
    assert getattr(admin, "signal_data") is signal_io.signal_data  # noqa: B009
    assert harden.set_pin_argv("signal-cli", NUMBER)[2:4] == ["--config", data]
    assert harden.set_pin_argv("signal-cli", NUMBER, jvm=True)[1:3] == ["--config", data]


@pytest.mark.parametrize(
    "words",
    [
        ["send", "-m", "hi", "+306900000001"],
        ["register"],
        ["listIdentities", "--config", "/tmp/x"],
        [],
        ["trust", "\x1b"],
    ],
)
def test_only_the_named_subcommands_run(tmp_path: Path, words: list[str]) -> None:
    cfg = load_config(config(tmp_path))
    with pytest.raises(ValueError):
        admin.signal_command(cfg, words)


def test_unlock_from_the_mac_clears_the_lock(tmp_path: Path) -> None:
    cfg = dataclasses.replace(load_config(config(tmp_path)), data_dir=tmp_path / "data")  # never the real one
    cfg.data_dir.mkdir(parents=True)
    store = Store.open(cfg.db_path)
    for key, value in ((LOCK_KEY, "on"), (LOCK_DEVICE_KEY, f"{ACI} 2"), (LOCK_SINCE_KEY, "1791000000")):
        store.set_state(key, value)
    store.close()
    assert admin.unlock(cfg) is True
    store = Store.open(cfg.db_path)
    assert store.get_state(LOCK_KEY) is None and store.get_state(LOCK_DEVICE_KEY) is None
    store.close()
    assert admin.unlock(cfg) is False  # not locked: nothing to do


# ------------------------------------------------------------------ `estia signal-login`: her account moves to one without a number

FAKE = Path(__file__).parent / "fakes" / "fake_signal_cli.py"
NUMBERLESS = "99999999-8888-4777-8666-555555555555"  # her new account's Account ID (made up)
KEY = "abcdefghijklmnopqrstuvwxyz012345" * 2  # its Recovery key (made up; the fake's default)


def moving(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bot: str = NUMBER, **env: str) -> tuple[Path, Path, Path]:
    """Her own user's home under tmp_path: her config (a number, or `bot`), her store with the profile's keys set, and
    the fake signal-cli as her config's. (config, the fake's folder, her store)"""
    home = tmp_path / "her"
    monkeypatch.setenv("HOME", str(home))
    folder = tmp_path / "signal"
    folder.mkdir()
    monkeypatch.setenv("FAKE_SIGNAL_DIR", str(folder))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    program = tmp_path / "signal-cli"
    program.write_text(f'#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE))} "$@"\n')
    program.chmod(0o755)
    path = home / ".config/local-assistant/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        f'# her config\nbot_number = "{bot}"   # her number\nallowed_acis = ["{ACI}"]\nsignal_cli = "{program}"\n'
        'name = "Tiffany"\n'
    )
    path.chmod(0o600)
    monkeypatch.setattr(admin, "DEFAULT_CONFIG_PATH", path)
    cfg = load_config(path)
    cfg.data_dir.mkdir(parents=True)
    store = Store.open(cfg.db_path)
    for key in admin.PROFILE_KEYS:
        store.set_state(key, "set for her number")
    store.close()
    return path, folder, cfg.db_path


def requests(folder: Path) -> list[dict[str, Any]]:
    path = folder / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def login(**fields: str) -> str:
    return json.dumps({"account": NUMBERLESS, "recovery_key": KEY, "username": "tiffany.42", **fields}) + "\n"


def test_signal_login_moves_her_to_the_account_without_a_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """As her, the bridge stopped: the account is taken over in her own signal-cli data (the key on signal-cli's input,
    never a command line), her profile name and username are set, her config names the Account ID (a private copy of
    the old one kept), the profile's keys are cleared so the bridge sends her name and picture to the new account,
    and the next step is printed. Her old number's account is never touched."""
    path, folder, db = moving(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO(login()))
    assert admin.main(["signal-login"]) == 0
    sent = requests(folder)
    assert [r["method"] for r in sent] == ["register", "updateProfile", "updateAccount"]
    assert sent[0]["params"] == {"account": NUMBERLESS, "recoveryKey": KEY, "reregister": True}
    assert sent[1]["params"] == {"givenName": "Tiffany (AI)", "familyName": ""}
    assert sent[2]["params"] == {"username": "tiffany.42"}
    data = str(tmp_path / "her/.local/share/signal-cli")
    argvs = [json.loads(line) for line in (folder / "launches.jsonl").read_text().splitlines()]
    assert all(["--config", data] == argv[argv.index("--config") : argv.index("--config") + 2] for argv in argvs)
    assert all(KEY not in " ".join(argv) for argv in argvs) and "-a" not in argvs[0]
    assert not any(word in " ".join(argv) for argv in argvs for word in ("unregister", "deleteLocalAccountData"))
    text = path.read_text()
    assert f'bot_number = "{NUMBERLESS}"' in text and NUMBER not in text and "# her config" in text
    assert load_config(path).bot_number == NUMBERLESS
    copies = list(path.parent.glob("config.toml.bak-*"))
    assert len(copies) == 1 and NUMBER in copies[0].read_text() and stat.S_IMODE(copies[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    store = Store.open(db)
    assert all(store.get_state(key) is None for key in admin.PROFILE_KEYS)
    store.close()
    out = capsys.readouterr().out
    assert "search for her username tiffany.42" in out and "estia doctor" in out and "never unregistered" in out
    assert NUMBERLESS not in out and KEY not in out and "ending in 5555" in out


@pytest.mark.parametrize(
    ("given", "env", "why"),
    [
        (login(username="tiffany.42"), {"FAKE_SIGNAL_TAKEN": "tiffany.42"}, "Username is not available"),
        (login(recovery_key="z" * 64), {}, "recovery key is incorrect"),
        (login(recovery_key=KEY[:63]), {}, "63 characters"),
        (login(username="tiffany"), {}, "two or more digits"),
        ("not json\n", {}, "nothing to read"),
        ("", {}, "nothing to read"),
    ],
    ids=["username taken", "key refused", "short key", "username form", "not json", "nothing"],
)
def test_nothing_is_switched_when_a_step_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    given: str,
    env: dict[str, str],
    why: str,
) -> None:
    """Her config and store stay as they were, so she keeps running on her account; the line says why (never the key)
    and that nothing was switched."""
    path, _folder, db = moving(tmp_path, monkeypatch, **env)
    before = path.read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO(given))
    assert admin.main(["signal-login"]) == 1
    assert path.read_text() == before and not list(path.parent.glob("config.toml.bak-*"))
    store = Store.open(db)
    assert all(store.get_state(key) == "set for her number" for key in admin.PROFILE_KEYS)
    store.close()
    said = capsys.readouterr()
    assert why in said.out + said.err and "her account as before" in said.out + said.err
    assert KEY not in said.out + said.err and "z" * 64 not in said.out + said.err


def test_an_allowed_accounts_own_account_id_is_refused_before_anything_is_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Pasting your own Account ID and key from the password manager would take your own Signal account over and log
    your phone out (review S9): an account in allowed_acis is refused, and nothing reaches signal-cli."""
    path, folder, _db = moving(tmp_path, monkeypatch)
    before = path.read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO(login(account=ACI.upper())))
    assert admin.main(["signal-login"]) == 1
    assert requests(folder) == [] and path.read_text() == before
    out = capsys.readouterr().out
    assert prompts.SIGNAL_LOGIN_ALLOWED in out and "her account as before" in out and ACI not in out


def test_a_username_signal_refuses_needs_no_second_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A username Signal refuses ends signal-login after the takeover (review S8): run again, her account is on this
    Mac already (her signal-cli data says registered), so it is not registered again; only the username is new."""
    path, folder, _db = moving(tmp_path, monkeypatch, FAKE_SIGNAL_TAKEN="tiffany.42")
    monkeypatch.setattr("sys.stdin", io.StringIO(login()))
    assert admin.main(["signal-login"]) == 1
    assert [r["method"] for r in requests(folder)] == ["register", "updateProfile", "updateAccount"]
    out = capsys.readouterr().out
    assert "not registered again" in out
    monkeypatch.setattr("sys.stdin", io.StringIO(login(username="tiffany.43")))
    assert admin.main(["signal-login"]) == 0
    assert [r["method"] for r in requests(folder)][3:] == ["updateProfile", "updateAccount"]  # no second register
    assert f'bot_number = "{NUMBERLESS}"' in path.read_text()
    assert prompts.SIGNAL_LOGIN_HERE in capsys.readouterr().out


def test_her_own_account_is_registered_again_to_take_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Her config names the account already: Signal logged her out (another device took it over with the Recovery
    key), and signal-login takes it back (setup_assistant.md, 'Signal logged her out'). Her signal-cli data may still
    say registered, so it is registered again, never skipped (review S8 skips only a run before that stopped)."""
    path, folder, _db = moving(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO(login()))
    assert admin.main(["signal-login"]) == 0  # moved: her config names the account, her data says registered
    for copy in path.parent.glob(f"{path.name}.bak-*"):  # a copy per second: the second run comes within it
        copy.unlink()
    monkeypatch.setattr("sys.stdin", io.StringIO(login()))
    assert admin.main(["signal-login"]) == 0
    assert [r["method"] for r in requests(folder)][3:] == ["register", "updateProfile", "updateAccount"]
    assert requests(folder)[3]["params"] == {"account": NUMBERLESS, "recoveryKey": KEY, "reregister": True}
    assert f'bot_number = "{NUMBERLESS}"' in path.read_text()


def test_signals_limit_on_registrations_is_named_with_its_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """-5 at the takeover: Signal limits registrations for now; each run is one more try (review S8)."""
    path, _folder, _db = moving(tmp_path, monkeypatch, FAKE_SIGNAL_REGISTER_LIMIT="1")
    before = path.read_text()
    monkeypatch.setattr("sys.stdin", io.StringIO(login()))
    assert admin.main(["signal-login"]) == 1
    assert path.read_text() == before
    out = capsys.readouterr().out
    assert "Signal limits registrations for now" in out and "Next attempt may be tried at" in out
    assert "her account as before" in out


def test_the_six_digit_code_signal_asks_for_goes_with_the_takeover_on_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Signal asks for a TOTP code (review S7): without one, the line says to run it again and type the code at its
    question (it looped on "check the key" before); with one, it goes in the request on signal-cli's input."""
    path, folder, _db = moving(tmp_path, monkeypatch, FAKE_SIGNAL_TOTP="123456")
    monkeypatch.setattr("sys.stdin", io.StringIO(login()))
    assert admin.main(["signal-login"]) == 1
    out = capsys.readouterr().out
    assert prompts.SIGNAL_LOGIN_TOTP in out and "her account as before" in out
    monkeypatch.setattr("sys.stdin", io.StringIO(login(totp="123456")))
    assert admin.main(["signal-login"]) == 0
    register = [r for r in requests(folder) if r["method"] == "register"][-1]
    assert register["params"] == {"account": NUMBERLESS, "recoveryKey": KEY, "reregister": True, "totp": "123456"}
    argvs = [json.loads(line) for line in (folder / "launches.jsonl").read_text().splitlines()]
    assert all("123456" not in " ".join(argv) for argv in argvs) and "123456" not in capsys.readouterr().out
    assert f'bot_number = "{NUMBERLESS}"' in path.read_text()


def test_with_calls_the_takeover_runs_the_build_the_bridge_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With [calls] on the bridge runs the pinned JVM build: the takeover and the profile's requests run it too, with
    its Java and IPv4 in JAVA_OPTS, as `estia signal` does; never Homebrew's native build, whose version may differ
    after an update (review S6)."""
    path, folder, _db = moving(tmp_path, monkeypatch)
    jvm = tmp_path / "jvm-signal-cli"
    jvm.write_text((tmp_path / "signal-cli").read_text())
    jvm.chmod(0o755)
    short = tempfile.mkdtemp(prefix="c", dir="/tmp")
    path.write_text(
        path.read_text()
        + f'[voice]\n[calls]\nsignal_cli = "{jvm}"\njava_home = "{tmp_path}/jdk"\nsocket_dir = "{short}"\n'
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(login()))
    assert admin.main(["signal-login"]) == 0
    argvs = [json.loads(line) for line in (folder / "launches.jsonl").read_text().splitlines()]
    assert len(argvs) == 3 and all("-Djava.net.preferIPv4Stack=true" not in argv for argv in argvs)
    launch = json.loads((folder / "launch.json").read_text())
    assert launch["env"]["JAVA_HOME"] == f"{tmp_path}/jdk"
    assert launch["env"]["JAVA_OPTS"].startswith("-Djava.net.preferIPv4Stack=true")
    assert admin.login_command(load_config(path))[0][0] == str(jvm)


OLD_ACCOUNT = "77777777-6666-4555-8444-333333333333"  # her account without a number before the move (made up)


def test_after_a_move_from_a_number_the_last_lines_say_to_stop_trusting_her_old_chat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review of 0.9.7, S13: her old number's account stays registered but never comes online, so its Registration
    Lock lapses after 7 days and whoever gets the number next can write from her old chat: the last lines say so."""
    from local_assistant import hostsec

    moving(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO(login()))
    assert admin.main(["signal-login"]) == 0
    out = " ".join(capsys.readouterr().out.split())
    assert hostsec.OLD_NUMBER_KEPT in out and hostsec.OLD_NUMBER_RISK in out
    for words in ("Registration Lock lapses after 7 days", "Delete or block", "never trust it again", "keep the SIM"):
        assert words in hostsec.OLD_NUMBER_RISK, words


def test_a_move_from_an_account_without_a_number_names_no_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SECURITY.md's step 2 moves an account without a number to a new one: her old account had no number, so the
    last lines name her old account and her old chat, never a number or a SIM."""
    from local_assistant import hostsec

    path, _folder, _db = moving(tmp_path, monkeypatch, bot=OLD_ACCOUNT)
    monkeypatch.setattr("sys.stdin", io.StringIO(login()))
    assert admin.main(["signal-login"]) == 0
    assert load_config(path).bot_number == NUMBERLESS
    out = " ".join(capsys.readouterr().out.split())
    assert hostsec.OLD_ACCOUNT_KEPT in out and hostsec.OLD_NUMBER_KEPT not in out and "SIM" not in out
    assert "number" not in out.split("Signal account ✓.", 1)[1]  # the lines on her old account
    assert "never trust it again" in out


def test_the_profile_keys_are_the_bridges() -> None:
    from local_assistant import lifecycle

    assert admin.PROFILE_KEYS == (lifecycle.PROFILE_NAME_KEY, lifecycle.PROFILE_AVATAR_KEY)
