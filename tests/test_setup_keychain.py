"""Setup's keychain writes (setup_keychain): a secret only on security's stdin, read back and compared, never shown;
a locked login keychain opened on the terminal first (macOS asks, hidden)."""

import json
import shlex
import sys
from pathlib import Path

import pytest

from local_assistant import setup_keychain

FAKE = Path(__file__).parent / "fakes" / "fake_security.py"
SECRET = "abcd" * 16  # a made-up key


@pytest.fixture
def security(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fake security as /usr/bin/security under the tools' root; its folder."""
    folder = tmp_path / "security"
    folder.mkdir()
    tool = tmp_path / "tools/usr/bin/security"
    tool.parent.mkdir(parents=True)
    run = f"exec {shlex.quote(sys.executable)} {shlex.quote(str(FAKE))}"
    tool.write_text(f'#!/bin/sh\nFAKE_SECURITY_DIR={shlex.quote(str(folder))} {run} "$@"\n')
    tool.chmod(0o755)
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(tmp_path / "tools"))
    return folder


def calls(folder: Path) -> list[list[str]]:
    path = folder / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def stored(folder: Path) -> dict[str, str]:
    path = folder / "items.json"
    return json.loads(path.read_text()) if path.exists() else {}


def test_a_secret_goes_only_on_stdin_and_is_read_back(security: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (security / "unlocked").touch()
    assert setup_keychain.save("estia-test", "alex", SECRET, label="Estia: a test key")
    assert stored(security) == {"estia-test alex": SECRET}
    made = calls(security)
    assert ["-i"] in made and any(call[0] == "find-generic-password" for call in made)
    assert all(SECRET not in " ".join(call) for call in made)  # ps shows arguments to every user of the Mac
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err and out.out == ""


def test_a_locked_keychain_is_opened_on_the_terminal_first(security: Path) -> None:
    said: list[str] = []
    assert setup_keychain.save(
        "estia-test", "alex", SECRET, label="Estia: a test key", before_unlock=lambda: said.append("asks")
    )
    assert said == ["asks"]
    names = [call[0] for call in calls(security)]
    assert names[0] == "show-keychain-info" and names.index("unlock-keychain") < names.index("-i")
    unlock = next(call for call in calls(security) if call[0] == "unlock-keychain")
    assert unlock == ["unlock-keychain", str(setup_keychain.login_keychain())]  # no -p: macOS asks, hidden


def test_a_keychain_that_stays_locked_keeps_nothing(security: Path) -> None:
    (security / "refuse_unlock").touch()
    saved = setup_keychain.save("estia-test", "alex", SECRET, label="Estia: a test key")
    assert not saved and saved is setup_keychain.Saved.LOCKED
    assert stored(security) == {} and ["-i"] not in calls(security)


def test_a_value_read_back_that_differs_is_a_failure(security: Path) -> None:
    (security / "unlocked").touch()
    (security / "corrupt").touch()
    assert not setup_keychain.save("estia-test", "alex", SECRET, label="Estia: a test key")


@pytest.mark.parametrize("secret", ['ab"cd', "ab\\cd", "ab\ncd", "", "ab\x00cd"])
def test_a_secret_security_cannot_take_on_one_line_is_never_sent(security: Path, secret: str) -> None:
    (security / "unlocked").touch()
    assert not setup_keychain.save("estia-test", "alex", secret, label="Estia: a test key")
    assert calls(security) == []


def asking(folder: Path) -> list[str]:
    """The items that trust no program: every read of them asks in a macOS dialog."""
    path = folder / "asking.json"
    return json.loads(path.read_text()) if path.exists() else []


def test_an_item_that_asks_at_every_read_trusts_no_program_and_is_never_read(
    security: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 27 Oct ("A"): the Recovery key and the Account ID trust no program, not even macOS's own security tool
    (-T ""), so every read asks through a macOS dialog; setup's check that the save worked reads only the item's
    attributes (no -w: no secret, no dialog)."""
    (security / "unlocked").touch()
    saved = setup_keychain.save("estia-test", "alex", SECRET, label="Estia: a test key", asks=True)
    assert saved and saved is setup_keychain.Saved.KEPT
    assert stored(security) == {"estia-test alex": SECRET} and asking(security) == ["estia-test alex"]
    made = calls(security)
    assert ["-i"] in made
    assert not any(call[0] == "find-generic-password" and "-w" in call for call in made)  # never read back
    assert any(call[0] == "find-generic-password" for call in made)  # its attributes, after the save
    assert all(SECRET not in " ".join(call) for call in made)
    assert not (security / "asked").exists()  # no read ever asked
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err and out.out == ""


def test_an_item_there_before_is_replaced_by_one_that_asks_without_reading_it(security: Path) -> None:
    """An item kept by an earlier run (one that trusted macOS's security tool) is deleted first, never read, then
    written again trusting no program: an update in place (-U) would keep its old access (macOS keeps an item's
    access when it updates it)."""
    (security / "unlocked").touch()
    assert setup_keychain.save("estia-test", "alex", "old" * 8, label="Estia: a test key")
    assert asking(security) == []
    assert setup_keychain.save("estia-test", "alex", SECRET, label="Estia: a test key", asks=True)
    assert stored(security) == {"estia-test alex": SECRET} and asking(security) == ["estia-test alex"]
    names = [call[0] for call in calls(security)]
    assert names.count("delete-generic-password") == 1
    assert names.index("delete-generic-password") < len(names) - 1 - names[::-1].index("-i")
    assert not (security / "asked").exists()


def test_an_item_that_asks_and_was_not_written_is_a_failure(security: Path) -> None:
    (security / "unlocked").touch()
    (security / "refuse_add").touch()
    saved = setup_keychain.save("estia-test", "alex", SECRET, label="Estia: a test key", asks=True)
    assert not saved and saved is setup_keychain.Saved.NOT_KEPT  # none was there before: nothing is lost
    assert stored(security) == {}


def test_an_asking_save_that_fails_after_the_copy_from_before_was_deleted_says_so(security: Path) -> None:
    """Review SEC-5: the copy from before is deleted first (an update would keep its old access), so an add that then
    fails leaves no item at all: the save says the copy from before is removed too, never just "not kept"."""
    (security / "unlocked").touch()
    assert setup_keychain.save("estia-test", "alex", "old" * 8, label="Estia: a test key", asks=True)
    (security / "refuse_add").touch()
    saved = setup_keychain.save("estia-test", "alex", SECRET, label="Estia: a test key", asks=True)
    assert not saved and saved is setup_keychain.Saved.REMOVED
    assert stored(security) == {} and not (security / "asked").exists()
    assert setup_keychain.save("estia-test", "alex", SECRET, label="l") is setup_keychain.Saved.NOT_KEPT  # -U too


@pytest.mark.parametrize("asks", [True, False])
def test_in_the_window_macos_box_gets_unlocks_time_and_one_left_unanswered_is_locked(
    security: Path, monkeypatch: pytest.MonkeyPatch, asks: bool
) -> None:
    """Review S06: in the window macOS's own box asks when the write reaches the locked keychain, and that write had
    30 s (the checks around it 10 s): a friend who took 40 s got "not kept", then a second box for the Account ID.
    Each security call there gets unlock()'s time, and a save that leaves the keychain locked says so (LOCKED)."""
    import subprocess

    real = subprocess.run
    timeouts: list[tuple[str, object]] = []

    def run(argv: list[str], *args: object, **kwargs: object) -> object:
        timeouts.append((argv[1], kwargs.get("timeout")))
        return real(argv, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr("local_assistant.setup_keychain.subprocess.run", run)
    opened: list[bool] = []
    saved = setup_keychain.save("estia-test", "alex", SECRET, label="l", asks=asks, opener=lambda: True, opened=opened)
    assert saved is setup_keychain.Saved.LOCKED and stored(security) == {}  # the box left unanswered, or cancelled
    item = [seconds for name, seconds in timeouts if name in ("-i", "find-generic-password", "delete-generic-password")]
    assert item and all(seconds == setup_keychain.UNLOCK_SECONDS for seconds in item), timeouts
    assert not [call for call in calls(security) if call[0] == "find-generic-password" and "-w" in call]  # no 2nd box
    (security / "desk").touch()  # at the desk, the box answered
    assert setup_keychain.save("estia-test", "alex", SECRET, label="l", asks=asks, opener=lambda: True) is (
        setup_keychain.Saved.KEPT
    )
    assert (security / "boxed").read_text().count("add-generic-password") == 1


def test_without_security_nothing_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(tmp_path))
    assert not setup_keychain.unlocked() and not setup_keychain.save("s", "a", SECRET, label="l")


def test_the_login_keychain_is_yours() -> None:
    assert setup_keychain.login_keychain() == Path.home() / "Library/Keychains/login.keychain-db"
