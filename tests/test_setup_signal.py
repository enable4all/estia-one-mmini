"""Setup's Signal steps (setup_signal): her account, a spare number or one without a number, the codes and PINs, the
Registration Lock, and who may talk to her (the one-time word)."""

import json
import re
import subprocess
import threading
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from local_assistant import hostsec, prompts
from local_assistant.config import parse_config
from local_assistant.setup import (
    Sender,
    Wizard,
    one_time_word,
    owner_by_word,
    render_config,
    senders,
)
from local_assistant.setup_questions import Checked, plain_text
from tests.fakes.fake_signal_cli import WRONG_CODE, WRONG_KEY
from tests.setup_helpers import (
    ACI,
    BOT,
    EXAMPLE,
    TERMINAL,
    Pty,
    envelope,
    fake_signal_cli,
    filled,
    launches,
    refuse,
    rpc_requests,
    typed_once,
)

pytestmark = pytest.mark.usefixtures("no_real_ollama", "no_system_paths", "a_terminal")


def test_who_sent_hello_is_found_in_what_signal_cli_received() -> None:
    lines = [
        envelope("+306900000001", "Alex", "hello"),
        envelope("+306900000001", "Alex", None),  # a receipt or typing notice: no message
        "not json",
        envelope("+306900000002", "Sam", "hi there", uuid="66666666-7777-4888-9999-000000000000"),
        envelope("+306900000001", "Alex", "hello again"),  # the same person once
    ]
    found = senders(lines)
    assert [(s.name, s.aci, s.text) for s in found] == [
        ("Alex", ACI, "hello"),
        ("Sam", "66666666-7777-4888-9999-000000000000", "hi there"),
    ]


def test_the_owner_is_the_one_who_sent_the_one_time_word() -> None:
    """A profile name can be copied by anyone; the one-time word only the owner was shown cannot."""
    found = [
        Sender("Alex", "66666666-7777-4888-9999-000000000000", "hello"),  # someone named Alex, but not the word
        Sender("Alex", ACI, "estia-4821"),
    ]
    assert owner_by_word(found, "estia-4821") == ACI
    assert owner_by_word(found, "estia-9999") is None


def test_the_word_is_found_after_another_message_from_the_same_account() -> None:
    """The owner sent "hi" first, then the word, in the same 20-second look: only each account's first message was
    kept, so the word was lost (review S18). Every text is kept; the word still counts only from one account."""
    lines = [envelope("+306900000001", "Alex", "hi"), envelope("+306900000001", "Alex", "estia-4821")]
    assert owner_by_word(senders(lines), "estia-4821") == ACI
    copied = [*lines, envelope("+306900000002", "Sam", "estia-4821", uuid="66666666-7777-4888-9999-000000000000")]
    assert owner_by_word(senders(copied), "estia-4821") is None  # from two accounts: neither


@pytest.mark.parametrize("sent", ["μαρια-3239", "ΜΑΡΊΑ-3239", " Μαρία-3239 "])
def test_a_greek_word_counts_without_its_accent(sent: str) -> None:
    """signal-greek-word (round 1): a Greek name gives a word like "μαρία-3239", which had to be typed on the phone
    with its accent and case. Accents and case do not count; another number never does."""
    found = [Sender("Alex", ACI, sent)]
    assert owner_by_word(found, one_time_word("Μαρία", 3239)) == ACI
    assert owner_by_word(found, one_time_word("Μαρία", 3240)) is None


def test_the_one_time_word_is_her_name_and_a_number() -> None:
    assert one_time_word("Estia", 4821) == "estia-4821"
    assert one_time_word("Tiffany", 1000) == "tiffany-1000"
    assert one_time_word("Mary-Jane Zoë", 9999) == "mary-jane-zoë-9999"


def owner_step(
    monkeypatch: pytest.MonkeyPatch, arrivals: list[bool], stops: list[bool] | None = None
) -> tuple[Wizard, list[str], list[list[str]]]:
    """The owner step with a fake signal-cli and no Enter asked for: look number i brings the shown one-time word
    when arrivals[i], else nothing; 20 seconds pass with each look; after look i, "q" was typed when stops[i]."""
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    said: list[str] = []
    commands: list[list[str]] = []

    def run(command: list[str], quietly: bool = False, **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        said.append("<looked quietly>" if quietly else "<looked>")
        shown = re.search(r"estia-\d{4}", "\n".join(said))
        assert shown is not None
        lines = [envelope("+306900000001", "Alex", shown.group())] if arrivals[len(commands) - 1] else []
        return subprocess.CompletedProcess(command, 0, "\n".join(lines), "")

    seconds = iter(range(0, 100_000, 20))
    typed = iter(stops or [])
    monkeypatch.setattr("builtins.input", refuse)  # the wait needs no Enter
    monkeypatch.setattr(wizard, "say", said.append)
    monkeypatch.setattr(wizard.face, "say", said.append)  # what the face shows too
    monkeypatch.setattr(wizard, "run", run)
    monkeypatch.setattr(wizard, "receive_quietly", lambda command: run(command, quietly=True))
    monkeypatch.setattr(wizard, "word_clock", lambda: float(next(seconds)))
    monkeypatch.setattr(wizard, "word_stopped", lambda: next(typed, False))
    return wizard, said, commands


def test_the_word_is_waited_for_by_itself_and_the_reasons_come_after_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spec: the word shown, setup looks by itself (no Enter), after 60 s the four usual reasons, then it goes on
    looking: the word arrives at the fourth look, and nothing was asked."""
    wizard, said, commands = owner_step(monkeypatch, [False, False, False, True])
    assert wizard.owner(BOT) == ACI
    assert len(commands) == 4 and all("receive" in command for command in commands)
    text = "\n".join(said)
    assert said.count("<looked>") == 1 and said.count("<looked quietly>") == 3  # the command shown once
    assert "wrong number" in text and f"send it to {BOT}" in text and "Note to Self" in text
    assert text.count("has not arrived yet") == 1 and text.index("1. It went") < text.index("4. It came")
    assert len(set(re.findall(r"estia-\d{4}", text))) == 1  # the same word on every look
    assert f"send it to {BOT} (Estia's new number)" in text
    assert prompts.SETUP_WORD_RECEIVED in said and "Only your account can talk to Estia" in said[-1]
    assert prompts.SETUP_WORD_ASK.format(seconds=20) in text
    word = re.findall(r"estia-\d{4}", text)[0]
    assert f"\n    {word}\n" in f"\n{text}\n"  # shown large: on a line of its own


def test_the_reasons_wait_for_a_minute(monkeypatch: pytest.MonkeyPatch) -> None:
    wizard, said, _commands = owner_step(monkeypatch, [False, True])
    assert wizard.owner(BOT) == ACI
    assert "has not arrived yet" not in "\n".join(said)


def test_q_stops_the_wait_for_the_word(monkeypatch: pytest.MonkeyPatch) -> None:
    wizard, _said, commands = owner_step(monkeypatch, [False, True], stops=[True])
    with pytest.raises(SystemExit, match="Run setup again"):
        wizard.owner(BOT)
    assert len(commands) == 1


def test_a_typed_q_is_read_from_the_terminal_without_waiting(tmp_path: Path) -> None:
    """q and Enter on the terminal while setup looks: read without waiting, and nothing else typed is taken."""
    from local_assistant.setup_signal import typed_stop

    pty = Pty()
    try:
        assert not typed_stop(pty.path)  # nothing typed: no wait
        pty.type("hello\r")
        assert not typed_stop(pty.path)
        pty.type("q\r")
        assert typed_stop(pty.path)
    finally:
        pty.close()
    assert not typed_stop(str(tmp_path / "no-terminal"))


def signal_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hidden: list[str], **env: str
) -> tuple[Wizard, list[str], Path]:
    """The Signal number step against the fake signal-cli: the person types her number, then `hidden` at the hidden
    prompts (code, PIN, ...), and no to the Registration Lock; `env` sets the fake's answers."""
    program, folder = fake_signal_cli(tmp_path, monkeypatch, **env)
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli = program
    said: list[str] = []
    typed = iter(hidden)

    def type_hidden(prompt: str = "") -> str:
        try:
            return next(typed)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr(
        "builtins.input", lambda prompt="": "n" if "Registration Lock" in prompt else "y" if "right?" in prompt else BOT
    )
    monkeypatch.setattr("local_assistant.setup.read_hidden", type_hidden)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    monkeypatch.setattr(wizard, "say", said.append)
    monkeypatch.setattr(wizard.face, "say", said.append)  # what the face shows too
    return wizard, said, folder


def verify_codes(folder: Path) -> list[object]:
    return [r["params"]["verificationCode"] for r in rpc_requests(folder) if r["method"] == "verify"]  # type: ignore[index]


def test_a_wrong_sms_code_can_be_typed_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wizard, said, folder = signal_step(tmp_path, monkeypatch, ["111-111", "222-222"], FAKE_SIGNAL_CODE="222222")
    assert wizard.signal_number() == BOT
    assert verify_codes(folder) == ["111111", "222222"]
    assert any("type q to stop" in line for line in said)


@pytest.mark.parametrize(
    ("hidden", "verified"),
    [(["111111", "q"], 1), (["111111"], 1), (["222222", "q"], 1)],
    ids=["q-after-a-refused-code", "no terminal", "q-at-the-old-pin"],
)
def test_q_or_no_terminal_stops_the_sms_code_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hidden: list[str], verified: int
) -> None:
    """A stop is not a failed check (audit, 19 Oct: q printed "Verification failed"): it says the number is not
    registered yet and what the next run does."""
    wizard, _said, folder = signal_step(
        tmp_path, monkeypatch, hidden, FAKE_SIGNAL_CODE="222222", FAKE_SIGNAL_PIN="9876"
    )
    with pytest.raises(SystemExit) as stopped:
        wizard.signal_number()
    # q at the old PIN names the lock's ways out (signal-reglock-unknown-dead-end): a run again meets the same lock
    words = prompts.SETUP_OLD_PIN_STOPPED if hidden == ["222222", "q"] else prompts.SETUP_CODE_STOPPED
    assert str(stopped.value) == words and "Verification failed" not in str(stopped.value)
    assert "not registered yet" in prompts.SETUP_CODE_STOPPED and "Run setup again" in prompts.SETUP_CODE_STOPPED
    # also after a code was checked and refused: never "before the code was checked" (review, 19 Oct)
    assert prompts.SETUP_CODE_STOPPED.startswith("Stopped: her number is not registered yet.")
    # signal-no-sms-loop (round 1): a SIM that gets no SMS looped through new codes up to Signal's limit; the stop
    # names the limit and the way out, the account without a number
    assert "Signal limits how many codes it sends" in prompts.SETUP_CODE_STOPPED
    assert "A SIM that gets no SMS: scripts/setup.sh --redo signal, for a Signal Account without a number." in (
        " ".join(prompts.SETUP_CODE_STOPPED.split())
    )
    assert len(verify_codes(folder)) == verified


def test_signals_limit_at_the_sms_code_stops_with_its_time_in_local_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S05: verify's -5 read "Check the code … then type them again", its time in UTC, and each try was one
    more verify while Signal limits them. It stops, as a registration Signal limits does, its time in local time."""
    from local_assistant.setup_signal import local_times

    wizard, said, folder = signal_step(tmp_path, monkeypatch, ["111111", "111111"], FAKE_SIGNAL_VERIFY_ERROR="-5")
    with pytest.raises(SystemExit) as stopped:
        wizard.signal_number()
    stop = str(stopped.value)
    assert stop.startswith("✗ Signal: it limits registrations for now (Rate limit reached")
    assert local_times("2026-10-25T09:40Z") in stop and "09:40Z" not in stop and "Check the code" not in stop
    assert "Check the code" not in "\n".join(said)
    assert len(verify_codes(folder)) == 1  # no second verify while Signal limits them


def test_no_answer_at_the_sms_code_names_the_network_never_the_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S05: a Wi-Fi drop at verify (signal-cli's -3, or no answer) read as a wrong code."""
    wizard, said, folder = signal_step(tmp_path, monkeypatch, ["111111", "111111"], FAKE_SIGNAL_VERIFY_ERROR="-3")
    with pytest.raises(SystemExit) as stopped:
        wizard.signal_number()  # the code typed twice, then no more: q
    assert str(stopped.value) == prompts.SETUP_CODE_STOPPED
    reason = "Verify error: java.net.SocketException: Network is unreachable"
    assert said.count(prompts.SETUP_CODE_NO_ANSWER.format(reason=reason)) == 2
    assert not any("Check the code" in line for line in said)
    assert len(verify_codes(folder)) == 2


def test_a_wrong_sms_code_as_signal_cli_answers_it_says_check_the_code_never_the_wifi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix review (signal-S05-minus3-is-not-only-no-answer): signal-cli 0.14.9 answers a wrong SMS code with its IO
    error, -3, as it does a network failure; S05 read every -3 as "no answer from Signal … Check the Wi-Fi", so a typo
    sent the person to the Wi-Fi, and each try was one more verify until Signal's limit. Only a -3 that names the
    network is no answer."""
    wizard, said, folder = signal_step(tmp_path, monkeypatch, ["111111", "222222"], FAKE_SIGNAL_CODE="222222")
    assert wizard.signal_number() == BOT
    assert prompts.SETUP_CODE_FAILED.format(reason=WRONG_CODE) in said
    assert not any("no answer from Signal" in line or "Wi-Fi" in line for line in said)
    assert verify_codes(folder) == ["111111", "222222"]


def test_signal_clis_io_error_is_no_answer_only_when_it_names_the_network() -> None:
    """signal-cli 0.14.9 gives every IOException -3 (its VerifyCommand and RegisterCommand): a network failure, and
    Signal's refusal of a code or a key too. No code at all, or a -3 in java.net's or setup's own words, is no answer;
    any other -3 keeps Signal's refusal and its fix (fix review, S05; the route without a number likewise)."""
    from local_assistant import setup_signal

    network = "Verify error: java.net.SocketTimeoutException: timeout"
    assert setup_signal.no_answer(None, "") and setup_signal.no_answer(-3, network)
    assert setup_signal.no_answer(
        -3, "Failed to register: java.net.UnknownHostException: chat.signal.org (PushNetworkException)"
    )
    assert not setup_signal.no_answer(-3, WRONG_CODE) and not setup_signal.no_answer(-3, WRONG_KEY)
    assert not setup_signal.no_answer(-1, network)  # a user error stays one
    assert setup_signal.login_failed(WRONG_KEY, -3) == prompts.SETUP_LOGIN_FAILED.format(reason=WRONG_KEY)
    assert setup_signal.card_message(-3, why=WRONG_KEY) == prompts.SETUP_CARD_REFUSED
    assert setup_signal.card_message(-3, why=network) == prompts.SETUP_CARD_NO_ANSWER
    again = "Failed to register: Account is already registered (IOException)"
    assert setup_signal.register_failed(again, -3) == prompts.SETUP_REGISTER_FAILED.format(reason=again)


def hidden_prompts(said: list[str]) -> list[str]:
    return [line.removeprefix("<hidden> ") for line in said if line.startswith("<hidden> ")]


def test_the_old_pin_is_asked_only_when_signal_says_the_number_has_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec: "Old PIN only when Signal says the number has one" (a new SIM has none, and the question confused); then
    the code is sent again with it, and a PIN Signal refuses is asked again without the code."""
    wizard, said, folder = number_step(tmp_path, monkeypatch, ["+30 690 000 0000", "", "n"], ["111111"])
    assert wizard.signal_number() == BOT
    assert hidden_prompts(said) == [prompts.SETUP_SMS_ASK]
    (tmp_path / "pin").mkdir()
    wizard, said, folder = number_step(
        tmp_path / "pin", monkeypatch, ["+30 690 000 0000", "", "n"], ["111111", "1234", "9876"], FAKE_SIGNAL_PIN="9876"
    )
    assert wizard.signal_number() == BOT
    assert hidden_prompts(said) == [prompts.SETUP_SMS_ASK, prompts.SETUP_OLD_PIN_ASK, prompts.SETUP_OLD_PIN_ASK]
    verified = [r["params"] for r in rpc_requests(folder) if r["method"] == "verify"]
    assert verified == [
        {"account": BOT, "verificationCode": "111111"},
        {"account": BOT, "verificationCode": "111111", "pin": "1234"},
        {"account": BOT, "verificationCode": "111111", "pin": "9876"},
    ]
    text = "\n".join(said)
    assert plain_text(prompts.SETUP_OLD_PIN) in text and "1234" not in text and "9876" not in text


@pytest.mark.parametrize(
    ("typed", "message"),
    [
        ("12-34", "The code has 6 digits, like 123-456."),
        ("1234567", "The code has 6 digits, like 123-456."),
        ("12345a", "The code has 6 digits, like 123-456."),
    ],
)
def test_a_code_that_is_not_six_digits_is_refused_and_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, typed: str, message: str
) -> None:
    wizard, said, folder = number_step(tmp_path, monkeypatch, ["+30 690 000 0000", "", "n"], [typed, "111 111"])
    assert wizard.signal_number() == BOT
    assert message in said and verify_codes(folder) == ["111111"]  # the refused one never went to Signal
    assert hidden_prompts(said) == [prompts.SETUP_SMS_ASK, prompts.SETUP_SMS_ASK]


def test_a_captcha_paste_that_is_not_the_link_is_asked_again_and_shown_by_its_start_and_length(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "signal-hcaptcha.00000000-0000-4000-8000-000000000000.registration.fake-token-777"
    link = f"signalcaptcha://{token}"
    wizard, said, folder = number_step(
        tmp_path,
        monkeypatch,
        ["+30 690 000 0000", "", "n"],
        ["https://signalcaptchas.org", link, "111111"],
        FAKE_SIGNAL_CAPTCHA=token,
    )
    assert wizard.signal_number() == BOT
    assert "That is not the link yet. It starts with signalcaptcha://." in said
    assert prompts.SETUP_CAPTCHA_SHOWN.format(count=len(link)) in said
    assert all("fake-token-777" not in line for line in said)
    assert [r["params"] for r in rpc_requests(folder) if r["method"] == "register"][-1] == {
        "account": BOT,
        "captcha": token,
    }


def test_a_code_signal_refuses_is_said_on_the_question_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review setup-window-step-refusal-lost: the reason is said with the question it is for, right before its line
    (in Terminal its words name q)."""
    wizard, said, _folder = number_step(tmp_path, monkeypatch, ["+30 690 000 0000", "", "n"], ["222222", "111111"])
    assert wizard.signal_number() == BOT
    reason = prompts.SETUP_CODE_FAILED.format(reason=WRONG_CODE)  # 0.14.9's -3 (fix review, S05)
    at = said.index(reason)
    assert said[at + 1] == f"<hidden> {prompts.SETUP_SMS_ASK}"
    titles = [i for i, line in enumerate(said) if line.startswith("SMS code\n")]
    assert len(titles) == 2 and titles[1] < at  # its screen again, then why


def test_a_captcha_signal_refuses_is_asked_again_never_ending_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#11: a refused captcha stopped setup; now "Signal refused this link. Get a new one." and the steps again."""
    good = "right-token"
    wizard, said, _folder = number_step(
        tmp_path,
        monkeypatch,
        ["+30 690 000 0000", "", "n"],
        ["signalcaptcha://wrong-token-1234", f"signalcaptcha://{good}", "111111"],
        FAKE_SIGNAL_CAPTCHA=good,
    )
    assert wizard.signal_number() == BOT
    assert prompts.SETUP_CAPTCHA_REFUSED in said and all("wrong-token-1234" not in line for line in said)
    assert hidden_prompts(said)[:2] == [prompts.SETUP_CAPTCHA_ASK] * 2
    (tmp_path / "q").mkdir()
    wizard, said, _folder = number_step(
        tmp_path / "q", monkeypatch, ["+30 690 000 0000", ""], ["q"], FAKE_SIGNAL_CAPTCHA=good
    )
    with pytest.raises(SystemExit) as stopped:
        wizard.signal_number()
    assert str(stopped.value) == prompts.SETUP_CAPTCHA_STOPPED


@pytest.mark.parametrize(
    ("pins", "message"),
    [
        (["9q"], "At least 4 characters."),  # never digits a temporary folder's name may hold (pytest-412)
        (["1" * 65], "At most 64."),
        (["12 34"], "No spaces."),
        (["7319", "7391"], "The two did not match."),
    ],
)
def test_a_lock_pin_that_does_not_fit_says_why_in_the_specs_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pins: list[str], message: str
) -> None:
    from local_assistant import harden

    set_pins: list[str] = []

    def set_pin(signal_cli: str, number: str, pin: str, **kwargs: object) -> tuple[bool, str]:
        set_pins.append(pin)
        return True, ""

    monkeypatch.setattr(harden, "set_pin", set_pin)
    hidden = ["111111", *pins, "731904", "731904"]
    wizard, said, _folder = number_step(tmp_path, monkeypatch, ["+30 690 000 0000", "", ""], hidden)
    assert wizard.signal_number() == BOT
    assert message in said and "✓ matches" in said and set_pins == ["731904"]
    assert all(pin not in line for pin in (*pins, "731904") for line in said if not line.startswith("<"))


def test_no_code_or_pin_reaches_the_answers_file_a_command_line_or_the_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every secret of the spare number's step: the captcha link, the SMS code, the old PIN and the new one, typed
    hidden; the answers file keeps the route's step answers only (her number), never one of them."""
    from local_assistant import harden, setup_answers

    token = "signal-hcaptcha.00000000-0000-4000-8000-000000000000.registration.fake-token-777"
    secrets = [f"signalcaptcha://{token}", "222-222", "9876", "731904", "731904"]
    monkeypatch.setattr(harden, "set_pin", lambda cli, number, pin, **kw: (True, ""))
    wizard, said, folder = number_step(
        tmp_path,
        monkeypatch,
        ["+30 690 000 0000", "", ""],
        secrets,
        FAKE_SIGNAL_CAPTCHA=token,
        FAKE_SIGNAL_CODE="222222",
        FAKE_SIGNAL_PIN="9876",
    )
    wizard.answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    assert wizard.signal_number() == BOT
    kept = setup_answers.default_path().read_text(encoding="utf-8")
    shown = "\n".join(line for line in said if not line.startswith("<")) + capsys.readouterr().out
    for secret in (*secrets, "222222", token, "fake-token-777"):
        assert secret not in kept, secret
        assert secret not in shown, secret
        assert not any(secret in arg for argv in launches(folder) for arg in argv), secret
    assert BOT in kept


# ------------------------------------------------------------------ her account without a number (Signal Login)

NUMBERLESS = "99999999-8888-4777-8666-555555555555"  # her Account ID (made up)
KEY = "abcdefghijklmnopqrstuvwxyz012345" * 2  # her Recovery key as Signal stores it (made up; the fake's default)
SHOWN_KEY = "ABCDEFGHIJKLMN#PQRSTUVWXYZ=12345" * 2  # as Signal shows it: O as #, 0 as =


def shown_lines(key: str) -> str:
    """The key as Signal's screen shows it: four lines of four groups, a blank line between them."""
    rows = [key[at : at + 16] for at in range(0, 64, 16)]
    return "\n\n".join(" ".join(row[i : i + 4] for i in range(0, 16, 4)) for row in rows)


def numberless_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hidden: list[str], typed: list[str], **env: str
) -> tuple[Wizard, list[str], Path]:
    """The step for an account without a number against the fake signal-cli: `hidden` at the hidden prompts (the
    Account ID, the Recovery key, …), `typed` at the visible ones (the username, Enter to try again, …)."""
    program, folder = fake_signal_cli(tmp_path, monkeypatch, **env)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # setup reads your signal-cli data there, never the Mac's
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli = program
    said: list[str] = []
    secrets = iter(hidden)
    answers = iter(typed)
    asked: list[str] = []
    events: list[str] = []  # in order: "hidden <prompt>", "ask <question>", "flush"

    def type_hidden(prompt: str, needed: int, counted: Callable[[str], bool], tty: str | None = None) -> str:
        asked.append(prompt)
        events.append(f"hidden {prompt}")
        try:
            return next(secrets)
        except StopIteration:
            raise EOFError from None

    def type_answer(prompt: str = "") -> str:
        said.append(prompt)
        events.append(f"ask {prompt}")
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    def type_code(prompt: str = "") -> str:  # the six-digit code: one hidden line
        return type_hidden(prompt, 6, str.isdigit)

    monkeypatch.setattr("local_assistant.setup.read_hidden_lines", type_hidden)
    monkeypatch.setattr("local_assistant.setup.read_hidden", type_code)
    monkeypatch.setattr("local_assistant.setup.flush_typed", lambda tty=None: events.append("flush"))
    monkeypatch.setattr("builtins.input", type_answer)
    monkeypatch.setattr(wizard, "say", said.append)
    monkeypatch.setattr(wizard.face, "say", said.append)  # what the face shows too
    wizard.asked_hidden = asked  # type: ignore[attr-defined]
    wizard.events = events  # type: ignore[attr-defined]
    return wizard, said, folder


def test_an_account_without_a_number_moves_here_with_its_key_on_signal_clis_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signal Login (23 Oct): the Account ID and the Recovery key, typed hidden (the key as Signal shows it, on
    several lines), go to signal-cli's register on its input, never on a command line; then her profile name and her
    username. The screen shows placeholders for both, never the key or the whole ID."""
    wizard, said, folder = numberless_step(
        tmp_path, monkeypatch, [NUMBERLESS.upper(), shown_lines(SHOWN_KEY)], ["estia.42"]
    )
    assert wizard.numberless() == NUMBERLESS and wizard.username == "estia.42"
    requests = rpc_requests(folder)
    register = next(r for r in requests if r["method"] == "register")
    assert register["params"] == {"account": NUMBERLESS, "recoveryKey": KEY, "reregister": True}
    profile = next(r for r in requests if r["method"] == "updateProfile")
    assert profile["params"] == {"givenName": "Estia (AI)", "familyName": ""}
    account = next(r for r in requests if r["method"] == "updateAccount")
    assert account["params"] == {"username": "estia.42"}
    for argv in launches(folder):
        assert KEY not in " ".join(argv) and SHOWN_KEY[:8] not in " ".join(argv)
    first = launches(folder)[0]
    assert "-a" not in first and "jsonRpc" in first  # register: multi-account mode, as signal-cli 0.14.9 needs
    text = "\n".join(said)
    assert KEY[:12] not in text and NUMBERLESS not in text and "<Recovery key>" in text and "<Account ID>" in text
    assert hostsec.NUMBERLESS_STEPS in text and hostsec.KEEP_BOTH in " ".join(text.split())  # wrapped on screen
    assert "Register without number" in text and "8.31.2" in text and "Google Play" in text
    assert "new chat, then search for estia.42" in text
    assert wizard.asked_hidden == [hostsec.ID_ASK, hostsec.KEY_ASK]  # type: ignore[attr-defined]


def test_a_key_pasted_with_dashes_and_invisible_characters_reaches_signal_cli_as_signal_stores_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner, 25 Oct: a key from a password manager in dashed groups, with a zero-width space, a no-break space and a
    byte order mark from the paste, is registered at the first try, in Signal's stored form (lower case, # and =
    read back), never with a dash or an invisible character in it."""
    pasted = "﻿" + "-​".join(SHOWN_KEY[at : at + 4] for at in range(0, 64, 4)) + " "
    wizard, _, folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, pasted], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS
    registers = [r for r in rpc_requests(folder) if r["method"] == "register"]
    assert [r["params"] for r in registers] == [{"account": NUMBERLESS, "recoveryKey": KEY, "reregister": True}]


def test_a_key_that_does_not_fit_is_asked_again_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stray symbol or a short key never reaches Signal: the line says why, without the key, and asks again."""
    wizard, said, folder = numberless_step(
        tmp_path,
        monkeypatch,
        [NUMBERLESS, SHOWN_KEY[:63], SHOWN_KEY[:20] + "%" + SHOWN_KEY[20:], SHOWN_KEY],
        ["estia.42"],
    )
    assert wizard.numberless() == NUMBERLESS
    assert [r["method"] for r in rpc_requests(folder)].count("register") == 1
    text = "\n".join(said)
    assert "That key has 63 characters" in text
    assert 'Character 21 of the 65 read is "%" (percent sign), which Signal does not use' in text
    assert SHOWN_KEY[:12] not in text


def test_a_key_signal_refuses_can_be_typed_again_and_q_stops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    other = "z" * 64  # a well-formed key that is not hers
    wizard, said, folder = numberless_step(
        tmp_path, monkeypatch, [NUMBERLESS, other, NUMBERLESS, KEY], ["", "estia.42"]
    )
    assert wizard.numberless() == NUMBERLESS
    keys = [r["params"]["recoveryKey"] for r in rpc_requests(folder) if r["method"] == "register"]  # type: ignore[index]
    assert keys == [other, KEY]
    text = "\n".join(said)
    assert "recovery key is incorrect" in text and other not in text
    # 0.14.9's -3 for a wrong key: Signal's refusal, never "no answer" (fix review, S05)
    assert prompts.SETUP_LOGIN_FAILED.format(reason=WRONG_KEY) in said and "no answer from Signal" not in text
    (tmp_path / "q").mkdir()
    (tmp_path / "q2").mkdir()
    wizard, _said, folder = numberless_step(tmp_path / "q", monkeypatch, [NUMBERLESS, other], ["q"])
    with pytest.raises(SystemExit) as stopped:
        wizard.numberless()
    assert str(stopped.value) == prompts.SETUP_LOGIN_STOPPED
    wizard, _said, folder = numberless_step(tmp_path / "q2", monkeypatch, ["q"], [])
    with pytest.raises(SystemExit):
        wizard.numberless()
    assert rpc_requests(folder) == []  # q at the Account ID: nothing sent


def test_a_username_signal_refuses_or_that_does_not_fit_is_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec: the form checked in one sentence first; one Signal says is taken gets another suggested, two random
    digits, and Enter takes it."""
    import secrets

    monkeypatch.setattr(secrets, "randbelow", lambda below: 48)  # the suggestion's digits: 58
    wizard, said, folder = numberless_step(
        tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia", "estia.42", ""], FAKE_SIGNAL_TAKEN="estia.42"
    )
    assert wizard.numberless() == NUMBERLESS and wizard.username == "estia.58"
    names = [r["params"]["username"] for r in rpc_requests(folder) if r["method"] == "updateAccount"]  # type: ignore[index]
    assert names == ["estia.42", "estia.58"]  # "estia" never reached Signal
    text = "\n".join(said)
    assert prompts.SETUP_USERNAME_RULE in said and "estia.42 is taken. Try estia.58?" in said
    assert "Username is not available" not in text
    (tmp_path / "q").mkdir()
    wizard, _said, _folder = numberless_step(tmp_path / "q", monkeypatch, [NUMBERLESS, KEY], [])  # no terminal: stop
    with pytest.raises(SystemExit) as stopped:
        wizard.numberless()
    assert str(stopped.value) == prompts.SETUP_USERNAME_STOPPED


def test_a_username_signal_will_not_take_says_why_in_terminal_with_its_q(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review S01's Terminal side: the reason, in Terminal's words (q stops), then the question again. Its causes are
    no answer within 120 s, -3 and -5, never the name (its form is checked first; "taken" has its own line), so the
    fix is to try again first (fix review, dialogue-05: "Type another one" gave up a good name)."""
    wizard, said, _folder = numberless_step(
        tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42", "estia.43"], FAKE_SIGNAL_USERNAME_ERROR="estia.42"
    )
    assert wizard.numberless() == NUMBERLESS and wizard.username == "estia.43"
    reason = "Failed to set username: java.net.SocketTimeoutException: timeout"
    assert prompts.SETUP_USERNAME_FAILED.format(reason=reason) in said
    assert prompts.SETUP_USERNAME_FAILED.endswith("). Try again, type another one, or q to stop.")
    assert prompts.SETUP_WINDOW_USERNAME_FAILED.endswith("). Try again, or type another one.")


def test_a_rate_limit_or_no_answer_never_says_check_the_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Signal limits registrations (-5): its words and the wait, never "check the Account ID and the Recovery key",
    so an Enter is no new registration while Signal limits them (review S8)."""
    wizard, said, _folder = numberless_step(
        tmp_path, monkeypatch, [NUMBERLESS, KEY], ["q"], FAKE_SIGNAL_REGISTER_LIMIT="1"
    )
    with pytest.raises(SystemExit):
        wizard.numberless()
    text = "\n".join(said)
    assert "Signal limits registrations for now" in text and "Next attempt may be tried at" in text
    assert "Check the Account ID" not in text and prompts.SETUP_LOGIN_AGAIN_LATER in text


def test_after_signals_limit_the_cards_name_its_wait_never_a_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """c-13 (confirm-2): after Signal's limit (-5) and Enter to try again, the cards came back with "Signal refused it.
    Compare each group with your phone.": the key was right, and each new try is one more registration. They name
    Signal's wait, in local time, and that nothing was checked."""
    from local_assistant import setup_signal

    wizard, said, _folder = numberless_step(
        tmp_path, monkeypatch, [NUMBERLESS, KEY, NUMBERLESS, KEY], ["", "q"], FAKE_SIGNAL_REGISTER_LIMIT="1"
    )
    with pytest.raises(SystemExit):
        wizard.numberless()
    when = setup_signal.local_times("2026-10-25T09:40Z")
    # plain lines' hidden read keeps no group (review W13): each is typed again, and the line says so
    limited = prompts.SETUP_CARD_LIMITED_UNTIL.format(when=when).replace(
        prompts.SETUP_CARD_KEPT, prompts.SETUP_CARD_TYPED_AGAIN
    )
    assert said.count(f"  {limited}") == 2  # before the Account ID, then before the Recovery key
    assert not any(prompts.SETUP_CARD_KEPT in line for line in said)
    assert not any(prompts.SETUP_CARD_REFUSED in line for line in said)


def test_signals_limit_on_the_cards_names_its_wait() -> None:
    from datetime import timedelta, timezone

    from local_assistant import setup_signal

    athens = timezone(timedelta(hours=2))
    why = "Rate limit reached\nNext attempt may be tried at 2026-10-25T09:40Z"
    assert setup_signal.signal_wait(why, tz=athens) == "11:40 on 25 Oct (local time)"
    assert setup_signal.signal_wait("Rate limit reached") == ""
    assert setup_signal.card_message(-5, "11:40 on 25 Oct (local time)") == (
        "Signal limits registrations until 11:40 on 25 Oct (local time), so nothing was checked. Every group is kept."
    )
    assert setup_signal.card_message(-5) == (
        "Signal limits registrations for now, so nothing was checked. Every group is kept."
    )


def test_setup_signal_limit_without_time() -> None:
    """Review S07 and S09: the limit's screen said "Stop here keeps every answer" (the next stop asks for the Account
    ID and the Recovery key again) and "wait until the time Signal names" when it named none; a number's stop said
    "wait until then" with no then. Each says what Stop here keeps, and with no time to run setup again later."""
    from local_assistant import setup_signal
    from local_assistant.setup_answers import Answers
    from local_assistant.setup_plan import Mac

    keeps = "Stop here keeps your other answers; the Account ID and the Recovery key are typed again."
    for when in ("11:40 on 25 Oct (local time)", ""):
        screen = setup_signal._question("login_again").at(
            Answers.memory(), Mac(), {"limit": setup_signal.limit_line(when)}
        )
        lead = plain_text(screen.lead)
        assert "keeps every answer" not in lead and "the time Signal names" not in lead
        assert " ".join(lead.split()).endswith(keeps)
        assert (f"until {when}, then try again" in lead) is bool(when)
        assert ("Signal named no time" in lead) is not bool(when)
    stop = setup_signal.register_failed("Rate limit reached", -5)
    assert "wait until then" not in stop and "run setup again later" in stop
    assert stop.startswith("✗ Signal: it limits registrations for now (Rate limit reached); it named no time.")
    assert "wait until then" in setup_signal.register_failed("Next attempt may be tried at 2026-10-25T09:40Z", -5)


def test_an_account_already_moved_here_is_not_registered_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A run that stopped at the username: the account is in your signal-cli data, registered. Setup asks for no
    Recovery key and registers nothing again; it sets the profile name and the username (review S8)."""
    data = tmp_path / "home/.local/share/signal-cli/data"
    data.mkdir(parents=True)
    (data / "accounts.json").write_text(
        json.dumps({"accounts": [{"path": "222222", "number": None, "uuid": NUMBERLESS}]})
    )
    (data / "222222").write_text(json.dumps({"version": 9, "registered": True}))
    wizard, said, folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS and wizard.username == "estia.42"
    assert [r["method"] for r in rpc_requests(folder)] == ["updateProfile", "updateAccount"]
    assert wizard.asked_hidden == [hostsec.ID_ASK]  # type: ignore[attr-defined]
    assert prompts.SETUP_LOGIN_HERE.format(name="Estia") in said


def test_a_run_again_after_a_stop_at_the_username_asks_only_for_her_username(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second deep scan, signal-03: setup stopped at the username said to choose 2 and type the Account ID and the
    Recovery key again, but a run again keeps the Account ID, finds her account on this Mac and asks only for her
    username. The stop's words say so."""
    from local_assistant.setup_answers import Answers

    wizard, _said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], [])
    with pytest.raises(SystemExit) as stopped:
        wizard.numberless()
    assert str(stopped.value) == prompts.SETUP_USERNAME_STOPPED
    stop = " ".join(prompts.SETUP_USERNAME_STOPPED.split())
    assert "setup asks only for her username" in stop and "choose 2" not in stop and "Recovery key" not in stop
    first = wizard.answers.values()  # what the answers file keeps from the run that stopped
    (tmp_path / "again").mkdir()
    wizard, said, folder = numberless_step(tmp_path / "again", monkeypatch, [], ["estia.42"])
    wizard.answers = Answers(None, first)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # the same Mac: her signal-cli data from the first run
    assert wizard.numberless() == NUMBERLESS and wizard.username == "estia.42"
    assert wizard.asked_hidden == []  # type: ignore[attr-defined]
    assert [r["method"] for r in rpc_requests(folder)] == ["updateProfile", "updateAccount"]
    assert prompts.SETUP_LOGIN_HERE.format(name="Estia") in said


def test_the_six_digit_code_signal_asks_for_is_asked_hidden_and_sent_on_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """signal-cli's manual: "If automatic account recovery reports that a TOTP token is required, rerun register with
    both --recovery-key and --totp." Setup looped on "check the Account ID and the Recovery key" (review S7); now it
    asks for the code, hidden, and sends it with the key on signal-cli's input, never a command line or the screen."""
    wizard, said, folder = numberless_step(
        tmp_path, monkeypatch, [NUMBERLESS, KEY, "12 34", "123456"], ["estia.42"], FAKE_SIGNAL_TOTP="123456"
    )
    assert wizard.numberless() == NUMBERLESS
    registers = [r["params"] for r in rpc_requests(folder) if r["method"] == "register"]
    assert registers[-1] == {"account": NUMBERLESS, "recoveryKey": KEY, "reregister": True, "totp": "123456"}
    assert len(registers) == 2
    assert wizard.asked_hidden.count(prompts.SETUP_TOTP_ASK) == 2  # type: ignore[attr-defined]
    text = "\n".join(said)
    shown = plain_text(prompts.SETUP_TOTP)
    assert shown in text and "123456" not in text and "Check the Account ID" not in text
    # signal-totp-technical (round 1): signal-cli's own words ("rerun register with --totp TOKEN") stay off the screen
    assert "--totp" not in text and "TOTP" not in shown and "signal-cli" not in shown
    assert all("123456" not in " ".join(argv) for argv in launches(folder))


def test_the_enter_after_the_key_never_answers_the_next_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review, 23 Oct: the key's read ends at its 64th character's line end; an Enter pressed after it (as a blank
    line) waited in the terminal while signal-cli worked and answered the next question with nothing. What was typed
    ahead is dropped before each question after the key (the username, "type them again"), and an empty username
    is asked again with no line that it is wrong."""
    other = "z" * 64  # a well-formed key that is not hers
    wizard, said, _folder = numberless_step(
        tmp_path, monkeypatch, [NUMBERLESS, other, NUMBERLESS, KEY], ["", "", "estia.42"]
    )
    assert wizard.numberless() == NUMBERLESS and wizard.username == "estia.42"
    events = wizard.events  # type: ignore[attr-defined]
    for at, event in enumerate(events):
        if event.startswith("ask "):
            assert events[at - 1] == "flush", (at, event)
    ask = prompts.SETUP_USERNAME_ASK_NICK.format(nickname="estia")
    assert [event for event in events if event.startswith("ask ")] == [
        f"ask {prompts.SETUP_LOGIN_AGAIN} ",
        f"ask {ask} ",
        f"ask {ask} ",
    ]
    assert prompts.SETUP_USERNAME_RULE not in said


def test_a_whole_note_pasted_at_the_key_question_is_refused_and_never_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review of P1, R2-S1, on a real terminal: the password manager's whole note (the Account ID with its dashes, then
    the key's four lines) at the key question read as exactly 64 once the rest was dropped and the ID's dashes left
    out, and register went to Signal with a wrong key, a registration toward its limit. The rest is counted now: 96,
    refused, nothing sent; the key pasted alone then (over four lines) registers once, with the key; an extra Enter
    after the key changes nothing."""
    wizard, said, folder = numberless_step(tmp_path, monkeypatch, [], ["estia.42"])
    pty = Pty()
    monkeypatch.setattr(
        "local_assistant.setup.read_hidden_lines",
        lambda prompt, needed, counted, tty=None: hostsec.read_hidden_lines(prompt, needed, counted, pty.path),
    )
    got: list[str | BaseException] = []

    def run() -> None:
        try:
            got.append(wizard.numberless())
        except (SystemExit, EOFError, OSError) as exc:
            got.append(exc)

    rows = [" ".join(SHOWN_KEY[at : at + 16][i : i + 4] for i in range(0, 16, 4)) for at in range(0, 64, 16)]
    key_ask = hostsec.KEY_ASK.replace("\n", "\r\n").encode()  # as the terminal shows its line break
    try:
        reader = threading.Thread(target=run, daemon=True)
        reader.start()
        pty.wait_for(hostsec.ID_ASK.encode())
        pty.type(NUMBERLESS + "\r")
        pty.wait_for(key_ask)
        pty.type("\r".join([NUMBERLESS, *rows]) + "\r")  # the whole note
        pty.wait_for(key_ask, occurrence=2)
        assert rpc_requests(folder) == []  # refused: no register sent
        pty.type("\r".join(rows) + "\r\r")  # the key alone, and an extra Enter
        reader.join(20)
    finally:
        pty.close()
    assert got == [NUMBERLESS], got
    registers = [r["params"] for r in rpc_requests(folder) if r["method"] == "register"]
    assert registers == [{"account": NUMBERLESS, "recoveryKey": KEY, "reregister": True}]
    refusals = [line for line in said if "this key has" in line]
    assert len(refusals) == 1 and "this key has 96" in refusals[0]
    assert SHOWN_KEY[:8] not in "\n".join(said) and NUMBERLESS[:8] not in "\n".join(said)


def account_step(monkeypatch: pytest.MonkeyPatch, typed: list[str]) -> tuple[Wizard, list[str], list[str]]:
    """Her Signal account's first questions in the person's own terminal: `typed` at each visible prompt (then the
    end of input); the routes' own steps stubbed. What was said, and every prompt, in order."""
    monkeypatch.setattr(Wizard, "terminal", lambda self: True)
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    said: list[str] = []
    prompted: list[str] = []
    answers = iter(typed)

    def type_answer(prompt: str = "") -> str:
        prompted.append(prompt)
        said.append(f"<asked> {prompt}")
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", type_answer)
    monkeypatch.setattr(wizard, "say", said.append)
    monkeypatch.setattr(wizard.face, "say", said.append)  # what the face shows too
    monkeypatch.setattr(wizard, "signal_number", lambda: "signal_number")
    monkeypatch.setattr(wizard, "numberless", lambda: "numberless")
    return wizard, said, prompted


@pytest.mark.parametrize(("answer", "route"), [("", "signal_number"), ("1", "signal_number"), ("2", "numberless")])
def test_her_signal_account_is_a_spare_number_or_an_account_without_one(
    monkeypatch: pytest.MonkeyPatch, answer: str, route: str
) -> None:
    """One question, the two routes in one line each with what each needs; Enter takes the recommended one, the spare
    number, listed first; an answer that is neither is asked again with what to type."""
    wizard, said, _prompted = account_step(monkeypatch, ["", "3", answer])
    assert wizard.signal_account() == route
    text = "\n".join(said)
    route, no_number = plain_text(prompts.SETUP_ROUTE_NUMBER), plain_text(prompts.SETUP_ROUTE_NO_NUMBER)
    assert route in text and no_number in text and "$2.99" in text
    assert "a call she answers on it is untested" in text and prompts.SETUP_ACCOUNT_UNKNOWN in text


def test_signal_on_your_phone_comes_first_and_setup_waits_for_a_yes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The preview's Signal screen in Terminal: her own account, yours as it is, and where to get Signal, free;
    a no says where and asks again, so the routes come only once Signal is on the phone."""
    wizard, said, prompted = account_step(monkeypatch, ["n", "", ""])
    assert wizard.signal_account() == "signal_number"
    text = "\n".join(said)
    assert plain_text(prompts.SETUP_SIGNAL_APP.format(name="Estia")) in text and "signal.org/install" in text
    assert "Estia gets her own Signal account: the number of a spare SIM, or a Signal Account without a number." in (
        " ".join(text.split())
    )
    assert prompted[:2] == [f"{prompts.SETUP_SIGNAL_APP_ASK} [Y/n] "] * 2
    assert text.index(prompts.SETUP_SIGNAL_APP_FIRST) < text.index(prompts.SETUP_ACCOUNT_ASK)


def test_the_end_of_input_at_signal_on_your_phone_stops_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """signal-eof-wrong-words (round 1): Ctrl-D there said "run scripts/setup.sh in your own Terminal window" to a
    person in it; in a terminal it is setup's usual stop, without one the line that says where to run it."""
    wizard, _said, _prompted = account_step(monkeypatch, [])
    with pytest.raises(SystemExit) as stopped:
        wizard.signal_account()
    assert str(stopped.value) == prompts.SETUP_STOPPED
    monkeypatch.setattr(Wizard, "terminal", lambda self: False)
    with pytest.raises(SystemExit) as stopped:
        wizard.signal_account()
    assert str(stopped.value) == prompts.SETUP_NO_TERMINAL


def test_each_route_shows_its_cost_before_the_choice_in_every_face() -> None:
    """Spec: the spare number first, "Recommended" (its reason, the tested route, in the small line); no number with its
    cost on the card before the choice. gum shows the options' labels and the list, the window the cards, plain lines
    the list: each holds the cost."""
    from local_assistant.setup_plan import question

    route = question("route")
    number, no_number = route.options
    assert (number.id, number.tag) == ("number", "Recommended") and route.recommended == "number"
    first = route.window_options()[0]
    assert route.advice == "the tested route" and (first.tag, first.line) == (
        "Recommended",
        f"The tested route. {prompts.SETUP_ROUTE_NUMBER_LINE}",
    )
    assert no_number.id == "no_number" and not no_number.tag
    assert "$2.99 or EUR 3.49" in no_number.label and "Android phone with Google Play" in no_number.line
    assert "SMS code" in number.label + number.line
    assert "$2.99 or EUR 3.49" in route.listing  # plain lines and gum: the list before "Which one?"
    assert "a call she answers on it is untested" in " ".join(route.listing.split()) and route.kind == "cards"


@pytest.mark.parametrize(
    ("country", "typed", "number"),
    [
        ("GR", "690 000 0000", "+306900000000"),  # the Mac's region: its code in front
        ("GR", "(690) 000-0000", "+306900000000"),  # brackets, dashes and spaces left out
        ("GR", "+30 690 000 0000", "+306900000000"),
        ("GR", "0030 690 000 0000", "+306900000000"),  # 00 is the + dialled
        ("GB", "07700 900000", "+447700900000"),  # the 0 dialled at home goes; privacy-ok: Ofcom's drama range
        ("US", "1 (555) 010-0000", "+15550100000"),  # the 1 dialled at home; privacy-ok: 555-01xx is fiction
        ("IT", "06 1234 5678", "+390612345678"),  # Italy keeps its 0; privacy-ok: a made-up number
        ("RU", "8 912 000 00 00", "+79120000000"),  # privacy-ok: a made-up number
        ("", "+30​690 000 0000\n", "+306900000000"),  # a paste's hidden characters and breaks
    ],
)
def test_her_number_takes_the_code_of_the_macs_region(country: str, typed: str, number: str) -> None:
    from local_assistant.setup_plan import Mac, question

    check = question("number").check
    assert check is not None
    assert check(typed, Answered(), Mac(country=country)).value == number


@pytest.mark.parametrize(
    ("country", "typed", "message"),
    [
        ("GR", "6900", "A number has 8 to 15 digits with its country code; this has 6."),
        ("GR", "+30 6900", "A number has 8 to 15 digits with its country code; this has 6."),
        ("GR", "+30 690 000 0000 0000 0", "A number has 8 to 15 digits with its country code; this has 17."),
        ("GR", "+30 69x 000 0000", "A number holds only digits after the +; 'x' is not one."),
        ("", "690 000 0000", prompts.SETUP_NUMBER_BAD),  # no region: the + and the code are needed
        ("GR", "", "A number has 8 to 15 digits with its country code; this has 0."),
    ],
)
def test_a_number_that_does_not_fit_is_refused_in_one_sentence(country: str, typed: str, message: str) -> None:
    from local_assistant.setup_plan import Mac, question

    check = question("number").check
    assert check is not None
    assert check(typed, Answered(), Mac(country=country)) == Checked(None, message)


def test_her_number_is_shown_back_with_spaces() -> None:
    from local_assistant.setup_signal import spaced

    assert spaced("+306900000000") == "+30 690 000 0000"
    assert spaced("+15550100000") == "+1 555 010 0000"  # privacy-ok: 555-01xx is fiction
    assert spaced("+4915112345678") == "+49 151 1234 5678"  # privacy-ok: a made-up number
    assert spaced("+3726000000") == "+372 600 0000"  # privacy-ok: a made-up number


def test_the_number_question_says_the_macs_code() -> None:
    from local_assistant.setup_plan import Mac, question

    greek = question("number").at(Answered(), Mac(country="GR"), {"name": "Estia"})
    assert greek.ask == prompts.SETUP_NUMBER_ASK_REGION.format(code="30")
    assert question("number").at(Answered(), Mac(), {"name": "Estia"}).ask == prompts.SETUP_NUMBER_ASK
    assert question("number").stop == prompts.SETUP_NUMBER_STOP and question("number").placeholder


class Answered:
    """Answers so far, as a question reads them (none unless given)."""

    def __init__(self, **values: object) -> None:
        self.values = values

    def get(self, qid: str) -> Any:
        return self.values.get(qid)

    def kept(self, qid: str) -> bool:
        return qid in self.values


def number_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, typed: list[str], hidden: list[str], **env: str
) -> tuple[Wizard, list[str], Path]:
    """The spare number's step against the fake signal-cli in the person's terminal: `typed` at the visible prompts,
    `hidden` at the hidden ones, in order; then the end of input. Every line said and every prompt, in order."""
    program, folder = fake_signal_cli(tmp_path, monkeypatch, **env)
    monkeypatch.setattr(Wizard, "terminal", lambda self: True)
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli = program
    said: list[str] = []
    answers, secrets = iter(typed), iter(hidden)

    def type_answer(prompt: str = "") -> str:
        said.append(f"<asked> {prompt}")
        try:
            return next(answers)
        except StopIteration:
            raise EOFError from None

    def type_hidden(prompt: str = "") -> str:
        said.append(f"<hidden> {prompt}")
        try:
            return next(secrets)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", type_answer)
    monkeypatch.setattr("local_assistant.setup.read_hidden", type_hidden)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    monkeypatch.setattr(wizard, "say", said.append)
    monkeypatch.setattr(wizard.face, "say", said.append)  # what the face shows too
    return wizard, said, folder


def test_her_number_is_shown_back_before_signal_counts_a_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec rule 8: "+30 690 000 0000: right?" once it fits, before anything registers; a yes goes on, a no asks the
    number again (each shown back in its turn)."""
    typed = ["+30 690 000 0001", "n", "+30 690 000 0002", "n", "+30 690 000 0000", "y", "n"]
    wizard, said, folder = number_step(tmp_path, monkeypatch, typed, ["111111"])
    assert wizard.signal_number() == BOT
    asked = [line for line in said if line.startswith("<asked> ")]
    right = [line for line in asked if "right?" in line]
    assert right == [
        f"<asked> {prompts.SETUP_NUMBER_RIGHT.format(found=n)} [Y/n] "
        for n in ("+30 690 000 0001", "+30 690 000 0002", "+30 690 000 0000")
    ]
    assert [r["params"] for r in rpc_requests(folder) if r["method"] == "register"] == [{"account": BOT}]
    register = next(at for at, line in enumerate(said) if f"register {BOT} on stdin:" in line)
    assert said.index(right[-1]) < register


def test_a_number_that_does_not_fit_is_asked_again_never_ending_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec rule 5: a mistake in the number (#10) stopped setup; now its one sentence, then the question again."""
    typed = ["+30 6900", "+30 69x 000 0000", "+30 690 000 0000", "", "n"]
    wizard, said, _folder = number_step(tmp_path, monkeypatch, typed, ["111111"])
    assert wizard.signal_number() == BOT
    text = "\n".join(said)
    assert "this has 6." in text and "'x' is not one." in text
    assert text.count(f"<asked> {prompts.SETUP_NUMBER_ASK_REGION.format(code='1')} ") == 3  # conftest: en_US


def test_the_end_of_input_at_her_number_stops_before_anything_is_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wizard, _said, folder = number_step(tmp_path, monkeypatch, [], [])
    with pytest.raises(SystemExit) as stopped:
        wizard.signal_number()
    assert str(stopped.value) == prompts.SETUP_NUMBER_STOPPED and rpc_requests(folder) == []


def test_a_rerun_keeps_the_route_and_the_number_and_registers_it_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Never retype (spec rule 5): her number, shown back once when it was typed, is kept in the answers file; a run
    after a stop says "Kept: …" and registers it, asking nothing visible."""
    from local_assistant import setup_answers

    path = setup_answers.default_path()
    first = setup_answers.Answers.load(path, say=print)
    wizard, _said, _folder = number_step(tmp_path, monkeypatch, ["+30 690 000 0000", "y"], ["q"])
    wizard.answers = first
    with pytest.raises(SystemExit):
        wizard.signal_number()  # stopped at the SMS code: the number is kept
    assert first.get("number") == BOT
    again = setup_answers.Answers.load(path, say=print)
    (tmp_path / "again").mkdir()
    wizard, said, _folder = number_step(tmp_path / "again", monkeypatch, ["n"], ["111111"])
    wizard.answers = again
    assert wizard.signal_number() == BOT
    text = "\n".join(said)
    assert f"Kept: Her number: {BOT}. To change it: scripts/setup.sh --redo number" in text
    assert "<asked> Her number" not in text and "right?" not in text


def test_the_owners_account_is_never_hers(monkeypatch: pytest.MonkeyPatch) -> None:
    """The word came from the very account setup just moved here (the owner's own Account ID was pasted): refused,
    so no config names one account as both (review S9)."""
    wizard, _said, _commands = owner_step(monkeypatch, [True])
    wizard.username = "estia.42"
    with pytest.raises(SystemExit, match="your own"):
        wizard.owner(ACI)


def test_the_owner_finds_her_by_her_username(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no number, the one-time word goes to her username (new chat, search), and the receive command on screen
    shows a placeholder for her Account ID."""
    wizard, said, commands = owner_step(monkeypatch, [False, False, False, True])
    wizard.username = "estia.42"
    shown: list[list[str]] = []
    run = wizard.run

    def run_shown(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        shown.append(kwargs.pop("shown", command))
        return run(command, **kwargs)

    monkeypatch.setattr(wizard, "run", run_shown)
    assert wizard.owner(NUMBERLESS) == ACI
    text = "\n".join(said)
    assert "search for her username estia.42" in text and "in a new chat with her username estia.42" in text
    assert NUMBERLESS not in text and all(NUMBERLESS not in " ".join(argv) for argv in shown)
    assert all(NUMBERLESS in command for command in commands)  # signal-cli itself gets it


def test_the_config_takes_her_account_id_in_place_of_a_number() -> None:
    text = render_config(EXAMPLE, NUMBERLESS, ACI, "en")
    assert parse_config(tomllib.loads(text)).bot_number == NUMBERLESS
    with pytest.raises(ValueError, match="Account ID"):
        render_config(EXAMPLE, "her-account", ACI, "en")


def test_the_dry_run_names_both_routes(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("builtins.input", refuse)
    Wizard(Path("/nonexistent/config.toml"), dry_run=True).signal_account()
    out = capsys.readouterr().out
    assert "register <number> on stdin" in out and "register <Account ID> with <Recovery key> on stdin" in out
    assert "updateAccount" in out


def test_the_captcha_code_and_pin_go_to_signal_cli_on_stdin_never_in_its_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Typed hidden, sent over signal-cli's JSON-RPC on stdin (ps shows a program's arguments to every user of the
    Mac), and not printed either: the commands are shown with placeholders, on every try (the terminal may be an AI's
    transcript, SETUP-WITH-YOUR-AI.md). register and verify exist only in signal-cli's multi-account mode (no -a)."""
    token = "signal-hcaptcha.00000000-0000-4000-8000-000000000000.registration.fake-token-777"
    captcha = f"signalcaptcha://{token}"
    secrets = [captcha, "111-111", "222-222", "9876"]
    hidden = iter(secrets)
    program, folder = fake_signal_cli(
        tmp_path, monkeypatch, FAKE_SIGNAL_CAPTCHA=token, FAKE_SIGNAL_CODE="222222", FAKE_SIGNAL_PIN="9876"
    )
    monkeypatch.setattr(
        "builtins.input", lambda prompt="": "n" if "Registration Lock" in prompt else "y" if "right?" in prompt else BOT
    )
    monkeypatch.setattr("local_assistant.setup.read_hidden", lambda prompt="": next(hidden))
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli = program
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert [(r["method"], r["params"]) for r in rpc_requests(folder)] == [
        ("register", {"account": BOT}),
        ("register", {"account": BOT, "captcha": token}),
        ("verify", {"account": BOT, "verificationCode": "111111"}),
        ("verify", {"account": BOT, "verificationCode": "222222"}),
        ("verify", {"account": BOT, "verificationCode": "222222", "pin": "9876"}),
    ]
    started = launches(folder)
    assert len(started) == 5 and all("jsonRpc" in argv and "-a" not in argv for argv in started)
    for secret in (*secrets, "111111", "222222", token):
        assert not any(secret in arg for argv in started for arg in argv)
    out = capsys.readouterr().out
    for secret in (*secrets, "111111", "222222", "fake-token-777"):
        assert secret not in out
    assert out.count(f"verify {BOT} with <SMS code> and <PIN> on stdin") == 1
    assert out.count(f"verify {BOT} with <SMS code> on stdin") == 2
    assert f"register {BOT} with <captcha link> on stdin" in out


def test_a_refused_registration_says_why_without_the_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A registration Signal refuses for another reason (here its limit): setup stops with the reason, never the
    link."""
    wizard, _said, _folder = signal_step(
        tmp_path, monkeypatch, ["signalcaptcha://right-token"], FAKE_SIGNAL_REGISTER_LIMIT="1"
    )
    with pytest.raises(SystemExit) as stopped:
        wizard.signal_number()
    assert "right-token" not in str(stopped.value)
    # signal-register-errors-raw (round 1): the limit in plain words, the time as the Mac's clock shows it, a Fix
    assert str(stopped.value).startswith("✗ Signal: it limits registrations for now (Rate limit reached Next attempt")
    assert "on 25 Oct (local time)" in str(stopped.value) and "2026-10-25T09:40Z" not in str(stopped.value)
    assert "Fix: wait until then, then run setup again: your answers are kept" in str(stopped.value)
    assert "Registration failed" not in str(stopped.value)


def test_the_registration_lock_pin_is_typed_twice_and_never_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Right after the SMS code: the PIN twice, hidden; it goes to signal-cli on stdin and is never printed."""
    from local_assistant import harden

    hidden = iter(["111-111", "7319-04", "731904", "731904", "731904"])
    pins: list[tuple[str, str, str]] = []

    def set_pin(signal_cli: str, number: str, pin: str, **kwargs: object) -> tuple[bool, str]:
        pins.append((signal_cli, number, pin))
        return True, ""

    def answer(prompt: str = "") -> str:
        return "y" if "Registration Lock" in prompt or "right?" in prompt else BOT

    monkeypatch.setattr(harden, "set_pin", set_pin)
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr("local_assistant.setup.read_hidden", lambda prompt="": next(hidden))
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli, _folder = fake_signal_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert [pin for _cli, number, pin in pins] == ["731904"] and pins[0][1] == BOT
    out = capsys.readouterr().out
    assert "731904" not in out and "7319-04" not in out
    assert "password manager" in out and "locked for 7 days" in out
    assert "The PIN needs no spaces" not in out and "did not match" in out
    assert "setPin with <PIN> on stdin" in out


def test_the_registration_lock_can_be_left_for_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import harden

    monkeypatch.setattr(harden, "set_pin", refuse)
    monkeypatch.setattr(
        "builtins.input", lambda prompt="": "n" if "Registration Lock" in prompt else "y" if "right?" in prompt else BOT
    )
    monkeypatch.setattr("local_assistant.setup.read_hidden", lambda prompt="": "111111" if "SMS" in prompt else "")
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli, _folder = fake_signal_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert "scripts/harden.sh" in capsys.readouterr().out


def test_signal_cli_calls_pin_the_identity_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """register, verify and receive trust a new identity only on first use, like the bridge."""
    wizard, _said, folder = signal_step(tmp_path, monkeypatch, ["111-111"])
    wizard.signal_number()
    started = launches(folder)
    assert len(started) == 2 and all(c[c.index("--trust-new-identities") + 1] == "on-first-use" for c in started)


def test_every_signal_cli_command_of_setup_names_your_signal_data_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second deep scan, signal-04: register, verify, the PIN and the one-time word's receive name signal-cli's data
    folder (`--config`), as the bridge, harden, signal-login and the move do. A signal-cli that follows XDG_DATA_HOME
    kept the registration in another folder, where the PIN, the move and a run again never looked."""
    from local_assistant.setup import registration_argv
    from local_assistant.signal_io import signal_data

    def names_it(command: list[str]) -> bool:
        return "--config" in command and command[command.index("--config") + 1] == str(signal_data())

    assert names_it(registration_argv("signal-cli"))
    wizard, _said, folder = signal_step(tmp_path, monkeypatch, ["111-111"])
    wizard.signal_number()
    started = launches(folder)
    assert len(started) == 2 and all(names_it(command) for command in started), started
    owner, _said, commands = owner_step(monkeypatch, [True])
    assert owner.owner(BOT) == ACI
    assert commands and all(names_it(command) for command in commands), commands


# ------------------------------------------------------------------ only a verified number is offered (findings 20, 53)


def signal_accounts(home: Path, **registered: bool | None) -> None:
    """signal-cli's account list, and each account's own file with its "registered" flag (None: no file)."""
    data = home / ".local/share/signal-cli/data"
    data.mkdir(parents=True)
    listed = []
    for i, (number, flag) in enumerate(registered.items()):
        listed.append({"path": str(100 + i), "environment": "LIVE", "number": number.replace("n", "+")})
        if flag is not None:
            (data / str(100 + i)).write_text(json.dumps({"version": 8, "registered": flag}))
    (data / "accounts.json").write_text(json.dumps({"accounts": listed, "version": 2}))


@pytest.mark.parametrize(
    ("registered", "offered"),
    [
        ({"n306900000000": True}, ["+306900000000"]),
        ({"n306900000000": False}, []),  # register ran, verify did not: register it again
        ({"n306900000000": None}, []),  # no account file
        ({"n306900000000": False, "n306900000001": True}, ["+306900000001"]),
    ],
)
def test_only_a_verified_number_is_offered_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registered: dict[str, bool | None], offered: list[str]
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    signal_accounts(tmp_path, **registered)
    assert Wizard(tmp_path / "config.toml", dry_run=False).registered_numbers() == offered


def test_a_known_number_says_what_yes_and_no_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = typed_once(monkeypatch, "")  # Enter, in the person's own Terminal
    monkeypatch.setattr("local_assistant.harden.in_terminal", lambda: True)
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    monkeypatch.setattr(wizard, "registered_numbers", lambda: [BOT])
    assert wizard.signal_number() == BOT
    out = capsys.readouterr().out
    assert f"{BOT} finished registering with Signal on this Mac before" in out
    assert "Yes uses it" in out and "No registers a number again" in out
    assert asked == [f"{filled(prompts.SETUP_KNOWN_NUMBER_ASK)} [Y/n] "]  # Enter: the recommended yes (29 Oct)


@pytest.mark.parametrize("typed", ["+30 690 000 0000", "+30-690-000-0000", " +306900000000 "])
def test_her_number_may_be_typed_with_spaces_or_dashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, typed: str
) -> None:
    """A space in her number stopped setup; the prompt says spaces and dashes are fine, and they are left out."""
    program, folder = fake_signal_cli(tmp_path, monkeypatch, FAKE_SIGNAL_CODE="222222")
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt="": "n" if "Registration Lock" in prompt else "y" if "right?" in prompt else typed,
    )
    hidden = iter(["222222", ""])
    monkeypatch.setattr("local_assistant.setup.read_hidden", lambda prompt="": next(hidden))
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli = program
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert rpc_requests(folder)[0]["params"] == {"account": BOT}


@pytest.mark.parametrize(
    ("name", "nickname"),
    [("Estia", "estia"), ("Mary-Jane Zoë", "mary_jane_zoe"), ("Jo", ""), ("3PO Robot", "po_robot"), ("Ζωή", "")],
)
def test_her_username_is_suggested_from_her_name(name: str, nickname: str) -> None:
    from local_assistant.setup_signal import nickname as suggested

    assert suggested(name) == nickname


@pytest.mark.parametrize(
    ("typed", "username"),
    [("42", "estia.42"), ("estia.42", "estia.42"), ("Maya_AI.07", "Maya_AI.07"), (" 1234 ", "estia.1234")],
)
def test_the_digits_alone_are_enough_for_her_username(typed: str, username: str) -> None:
    from local_assistant.setup_plan import Mac, question

    check = question("username").check
    assert check is not None
    assert check(typed, Answered(name="Estia"), Mac()) == Checked(username)


@pytest.mark.parametrize("typed", ["estia", "estia.00", "estia.1", "es.42", "4estia.42", "estia.1234567890"])
def test_a_username_that_does_not_fit_is_refused_in_the_specs_words(typed: str) -> None:
    from local_assistant.setup_plan import Mac, question

    check = question("username").check
    assert check is not None
    assert check(typed, Answered(name="Estia"), Mac()) == Checked(None, prompts.SETUP_USERNAME_RULE)
    assert prompts.SETUP_USERNAME_RULE == (
        "Nickname: 3 to 32 letters, digits or underscores, no digit first. After the dot: 2 to 9 digits, not 00."
    )


def test_her_username_is_kept_once_signal_took_it_and_a_rerun_uses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """People find her by it ("People find her as estia.42."); kept in the answers file, so a run that stopped after
    it sets the same one again without asking."""
    from local_assistant import setup_answers

    wizard, said, folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["42"])
    wizard.answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    assert wizard.numberless() == NUMBERLESS
    assert prompts.SETUP_USERNAME_FOUND.format(username="estia.42") in said
    assert wizard.answers.get("username") == "estia.42"
    (tmp_path / "again").mkdir()
    wizard, said, folder = numberless_step(tmp_path / "again", monkeypatch, [NUMBERLESS, KEY], [])
    wizard.answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    assert wizard.numberless() == NUMBERLESS and wizard.username == "estia.42"
    assert "Kept: Username: estia.42. To change it: scripts/setup.sh --redo username" in said
    assert [r["params"] for r in rpc_requests(folder) if r["method"] == "updateAccount"] == [{"username": "estia.42"}]


def test_the_account_id_is_kept_and_the_recovery_key_only_in_the_keychain() -> None:
    """Owner's choice: both typed on the card laid out as Signal's; the Account ID kept in the answers file (it is
    no secret by itself), the Recovery key never: only in your login keychain."""
    from local_assistant import setup_answers, setup_keycard
    from local_assistant.setup_plan import Mac, question

    account, key = question("account_id"), question("recovery_key")
    assert account.layout is setup_keycard.ACCOUNT_ID and key.layout is setup_keycard.RECOVERY_KEY
    assert account.keep == "saved" and not account.is_secret and "account_id" in setup_answers.saved_ids()
    assert key.keep == "keychain" and key.is_secret and "recovery_key" not in setup_answers.saved_ids()
    assert account.check is not None and key.check is not None
    assert account.check(NUMBERLESS.upper(), Answered(), Mac()) == Checked(NUMBERLESS)
    assert key.check(SHOWN_KEY, Answered(), Mac()) == Checked(KEY)
    assert key.check(SHOWN_KEY[:63], Answered(), Mac()).message.startswith("That key has 63 characters")


def on_the_card(
    monkeypatch: pytest.MonkeyPatch, wizard: Wizard, entries: list[list[str] | None]
) -> list[dict[str, object]]:
    """gum's face in a terminal wide enough: the Account ID and the key are typed on the card (setup_keycard.run_card,
    here each entry in turn: the groups typed, or None for Esc); each card's opening is recorded."""
    from local_assistant import setup_keycard
    from local_assistant.setup_face import GumFace

    opened: list[dict[str, object]] = []
    given = iter(entries)

    def run_card(card: object, **kwargs: object) -> list[str] | None:
        opened.append({"card": card, **kwargs})
        return next(given)

    face = GumFace(wizard.readers(), Path("/nonexistent/gum"))
    monkeypatch.setattr(face, "say", wizard.face.say)
    wizard.face = face
    monkeypatch.setattr(setup_keycard, "run_card", run_card)
    monkeypatch.setattr(setup_keycard, "fits", lambda tty=None: True)
    return opened


def key_groups(key: str) -> list[str]:
    return [key[at : at + 4] for at in range(0, 64, 4)]


ID_GROUPS = ["99999999", "8888", "4777", "8666", "555555555555"]


def test_on_the_card_the_account_id_and_the_key_are_typed_visibly_never_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Owner, 25 Oct ("guided typing"): with gum's menus the Account ID and the Recovery key are typed on the key
    card, laid out as Signal's; nothing is read hidden, and register gets the key as Signal stores it."""
    from local_assistant import setup_keycard

    wizard, said, folder = numberless_step(tmp_path, monkeypatch, [], ["estia.42"])
    monkeypatch.setattr("local_assistant.setup.read_hidden_lines", refuse)
    opened = on_the_card(monkeypatch, wizard, [ID_GROUPS, key_groups(SHOWN_KEY)])
    assert wizard.numberless() == NUMBERLESS
    assert [entry["card"] for entry in opened] == [setup_keycard.ACCOUNT_CARD, setup_keycard.RECOVERY_CARD]
    assert all(entry["groups"] == [] and entry["message"] == "" for entry in opened)
    register = next(r for r in rpc_requests(folder) if r["method"] == "register")
    assert register["params"] == {"account": NUMBERLESS, "recoveryKey": KEY, "reregister": True}
    assert SHOWN_KEY[:8] not in "\n".join(said)


def test_a_key_signal_refuses_reopens_the_card_with_every_group_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spec: "If Signal refuses the key, the card reopens with every group kept: 'Signal refused it. Compare each
    group with your phone.'" Both cards, in turn; no Enter asked in between."""
    other = "Z" * 64  # a well-formed key that is not hers
    wizard, said, _folder = numberless_step(tmp_path, monkeypatch, [], ["estia.42"])
    opened = on_the_card(monkeypatch, wizard, [ID_GROUPS, key_groups(other), ID_GROUPS, key_groups(SHOWN_KEY)])
    assert wizard.numberless() == NUMBERLESS
    assert [(entry["groups"], entry["message"]) for entry in opened[2:]] == [
        (ID_GROUPS, prompts.SETUP_CARD_REFUSED),
        (key_groups(other), prompts.SETUP_CARD_REFUSED),
    ]
    assert f"ask {prompts.SETUP_LOGIN_AGAIN} " not in wizard.events  # type: ignore[attr-defined]
    assert "recovery key is incorrect" in "\n".join(said)


def test_esc_on_the_card_stops_setup_before_anything_is_sent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    wizard, _said, folder = numberless_step(tmp_path, monkeypatch, [], [])
    on_the_card(monkeypatch, wizard, [ID_GROUPS, None])
    with pytest.raises(SystemExit) as stopped:
        wizard.numberless()
    assert str(stopped.value) == prompts.SETUP_LOGIN_STOPPED and rpc_requests(folder) == []


def test_a_narrow_terminal_or_plain_lines_read_the_key_hidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The card needs a terminal of 60 columns; plain lines (--plain, a screen reader) keep the hidden read."""
    from local_assistant import setup_keycard

    wizard, _said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    on_the_card(monkeypatch, wizard, [])
    monkeypatch.setattr(setup_keycard, "fits", lambda tty=None: False)
    assert wizard.numberless() == NUMBERLESS
    assert wizard.asked_hidden == [hostsec.ID_ASK, hostsec.KEY_ASK]  # type: ignore[attr-defined]


def test_a_kept_account_id_is_used_again_and_never_shown_in_full(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Account ID is kept once Signal took it (owner's choice); a run after a stop uses it, says so without
    showing it whole (as every line does), and asks for the key only."""
    from local_assistant import setup_answers

    answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    wizard, _said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    wizard.answers = answers
    assert wizard.numberless() == NUMBERLESS and answers.get("account_id") == NUMBERLESS
    (tmp_path / "again").mkdir()
    wizard, said, _folder = numberless_step(tmp_path / "again", monkeypatch, [KEY], [])
    wizard.answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    assert wizard.numberless() == NUMBERLESS
    kept = prompts.SETUP_KEPT.format(shown=f"Account ID: {hostsec.shown_account(NUMBERLESS)}", id="account_id")
    assert kept in said and NUMBERLESS not in "\n".join(said)
    assert wizard.asked_hidden == [hostsec.KEY_ASK]  # type: ignore[attr-defined]


# ------------------------------------------------------------------ the typed key and Account ID, kept in your keychain


@pytest.fixture
def keychain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The fake security as /usr/bin/security under the tools' root (tests/fakes/fake_security.py); its folder."""
    import shlex
    import sys

    folder = tmp_path / "security"
    folder.mkdir()
    tool = tmp_path / "tools/usr/bin/security"
    tool.parent.mkdir(parents=True)
    fake = Path(__file__).parent / "fakes" / "fake_security.py"
    run = f"exec {shlex.quote(sys.executable)} {shlex.quote(str(fake))}"
    tool.write_text(f'#!/bin/sh\nFAKE_SECURITY_DIR={shlex.quote(str(folder))} {run} "$@"\n')
    tool.chmod(0o755)
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(tmp_path / "tools"))
    return folder


def keychain_calls(folder: Path) -> list[list[str]]:
    path = folder / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def keychain_items(folder: Path) -> dict[str, str]:
    path = folder / "items.json"
    return json.loads(path.read_text()) if path.exists() else {}


def me() -> str:
    import os
    import pwd

    return pwd.getpwuid(os.getuid()).pw_name


def test_the_typed_key_and_account_id_go_into_your_login_keychain_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 25 Oct: "also save it in the keychain". Once Signal took them, both are written once, as Signal shows
    them (its copy button's form), on security's input only; never in an argument, the answers file or the screen.
    Owner, 27 Oct ("A"): both trust no program, so every read asks in a macOS dialog, and setup never reads them
    back (its check reads their attributes only). The key never in the answers file; the Account ID there too
    (owner's choice)."""
    from local_assistant import setup_answers

    (keychain / "unlocked").touch()
    other = "z" * 64  # a well-formed key that is not hers: refused, never kept
    wizard, said, _folder = numberless_step(
        tmp_path, monkeypatch, [NUMBERLESS, other, NUMBERLESS, KEY], ["", "estia.42"]
    )
    wizard.answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    assert wizard.numberless() == NUMBERLESS
    assert keychain_items(keychain) == {
        f"estia-signal-recovery-key {me()}": SHOWN_KEY,
        f"estia-signal-account-id {me()}": NUMBERLESS.upper(),
    }
    calls = keychain_calls(keychain)
    assert calls.count(["-i"]) == 2  # written once each, from the one entry Signal took
    asking = json.loads((keychain / "asking.json").read_text())
    assert sorted(asking) == sorted(keychain_items(keychain))  # both trust no program: every read asks
    assert not any(call[0] == "find-generic-password" and "-w" in call for call in calls)  # never read back
    for secret in (KEY, SHOWN_KEY, other):
        assert all(secret not in " ".join(call) for call in calls)
        assert secret not in "\n".join(said) + capsys.readouterr().out
        assert secret not in setup_answers.default_path().read_text(encoding="utf-8")
    assert prompts.SETUP_KEYCHAIN_KEPT in said
    assert setup_answers.Answers.load(setup_answers.default_path(), say=print).get("account_id") == NUMBERLESS


def test_a_locked_keychain_is_opened_on_the_terminal_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: Path
) -> None:
    """Over SSH the login keychain is usually locked: setup says so, then macOS asks for its password on the
    terminal, hidden (no password passes through setup)."""
    wizard, said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS
    names = [call[0] for call in keychain_calls(keychain)]
    assert prompts.SETUP_KEYCHAIN_LOCKED in said and names.index("unlock-keychain") < names.index("-i")
    assert said.index(prompts.SETUP_KEYCHAIN_LOCKED) < said.index(prompts.SETUP_KEYCHAIN_KEPT)
    assert len(keychain_items(keychain)) == 2


def test_a_keychain_setup_opened_is_locked_again_once_both_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: Path
) -> None:
    """Review SEC-3: while the login keychain is open, a program you run can read the Recovery key and the Account ID
    through macOS's own security tool without asking; setup locks it again when it opened it (over SSH), and leaves
    one that was open before as it was."""
    wizard, _said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS
    names = [call[0] for call in keychain_calls(keychain)]
    assert names.count("-i") == 2 and names[-1] == "lock-keychain" and names.index("-i") < names.index("lock-keychain")
    assert not (keychain / "unlocked").exists() and len(keychain_items(keychain)) == 2
    (tmp_path / "open").mkdir()
    (keychain / "unlocked").touch()
    wizard, _said, _folder = numberless_step(tmp_path / "open", monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS
    assert names.count("lock-keychain") == [call[0] for call in keychain_calls(keychain)].count("lock-keychain")
    assert (keychain / "unlocked").exists()


def test_a_keychain_that_stays_locked_keeps_nothing_and_setup_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: Path
) -> None:
    (keychain / "refuse_unlock").touch()
    wizard, said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS and wizard.username == "estia.42"
    assert keychain_items(keychain) == {} and prompts.SETUP_KEYCHAIN_NOT_KEPT in said
    assert hostsec.KEEP_BOTH in " ".join(prompts.SETUP_KEYCHAIN_NOT_KEPT.split())


def test_each_item_is_saved_on_its_own_and_a_failed_one_is_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: Path
) -> None:
    """Review SEC-5: the Account ID is saved even when the Recovery key's save fails, and the lines name each one;
    a copy from before that was deleted first, then not written again, is said to be removed too."""
    (keychain / "unlocked").touch()
    (keychain / "items.json").write_text(json.dumps({f"estia-signal-recovery-key {me()}": "OLD" * 20}))
    (keychain / "refuse_add").write_text("estia-signal-recovery-key\n")  # only the key's add fails
    wizard, said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS
    assert keychain_items(keychain) == {f"estia-signal-account-id {me()}": NUMBERLESS.upper()}
    text = "\n".join(said)
    assert prompts.SETUP_KEYCHAIN_REMOVED.format(item="Recovery key") in text
    assert prompts.SETUP_KEYCHAIN_ONE_KEPT.format(item="Account ID") in text
    assert hostsec.KEEP_BOTH in " ".join(text.split())
    assert prompts.SETUP_KEYCHAIN_KEPT not in said and prompts.SETUP_KEYCHAIN_NOT_KEPT not in said
    (tmp_path / "id").mkdir()
    (keychain / "items.json").unlink()
    (keychain / "refuse_add").write_text("estia-signal-account-id\n")  # only the Account ID's, none there before
    wizard, said, _folder = numberless_step(tmp_path / "id", monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS
    assert keychain_items(keychain) == {f"estia-signal-recovery-key {me()}": SHOWN_KEY}
    text = "\n".join(said)
    assert prompts.SETUP_KEYCHAIN_ONE_KEPT.format(item="Recovery key") in text
    assert prompts.SETUP_KEYCHAIN_ONE_NOT_KEPT.format(item="Account ID") in text
    assert "removed" not in text and hostsec.KEEP_BOTH in " ".join(text.split())


def test_a_keychain_that_stays_locked_is_asked_open_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: Path
) -> None:
    """A wrong Mac password keeps it locked: the Account ID's save never asks a second time."""
    (keychain / "refuse_unlock").touch()
    wizard, said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS
    assert [call[0] for call in keychain_calls(keychain)].count("unlock-keychain") == 1
    assert said.count(prompts.SETUP_KEYCHAIN_LOCKED) == 1 and prompts.SETUP_KEYCHAIN_NOT_KEPT in said


def test_in_the_window_a_keychain_left_locked_shows_macos_box_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: Path
) -> None:
    """Review S06: in the window the Recovery key's write reaches the locked keychain and macOS's own box asks; left
    unanswered (or cancelled), the Account ID's save raised a second box, against "a wrong Mac password: never asked
    twice". Only one write is tried while the keychain stays locked."""
    wizard, said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    monkeypatch.setattr(wizard, "keychain_opener", lambda: lambda: True)  # the window's: the box at the write
    assert wizard.numberless() == NUMBERLESS
    assert [call for call in keychain_calls(keychain) if call == ["-i"]] == [["-i"]]
    assert prompts.SETUP_KEYCHAIN_NOT_KEPT in said and keychain_items(keychain) == {}


def test_in_the_window_a_box_left_at_the_delete_asks_no_second_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: Path
) -> None:
    """Fix review (signal-S06-asks-path-asks-again): the Recovery key and the Account ID go the asks path, which
    deletes a copy from before first, up to three times. With both there from an earlier run and the keychain locked,
    each delete reached the locked keychain: where macOS's own box asks at a delete and is left unanswered or
    cancelled, the copy stays and the next delete asked again, up to three boxes of 5 minutes each. A delete that
    leaves the copy while the keychain stays locked ends the save: one box, then nothing more is tried."""
    from local_assistant.setup_signal import KEYCHAIN_ID, KEYCHAIN_KEY

    before = {f"{KEYCHAIN_KEY} {me()}": "OLD" * 20, f"{KEYCHAIN_ID} {me()}": NUMBERLESS.upper()}
    (keychain / "items.json").write_text(json.dumps(before))
    (keychain / "delete_asks").touch()
    wizard, said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS, KEY], ["estia.42"])
    monkeypatch.setattr(wizard, "keychain_opener", lambda: lambda: True)  # the window's: macOS's box at the change
    assert wizard.numberless() == NUMBERLESS
    changes = [call[0] for call in keychain_calls(keychain) if call[0] in ("delete-generic-password", "-i")]
    assert changes == ["delete-generic-password"]  # one box, at the first delete
    assert keychain_items(keychain) == before  # nothing changed: both copies from before stay
    assert prompts.SETUP_KEYCHAIN_NOT_KEPT in said


def test_an_account_already_here_or_a_refused_key_keeps_nothing_new(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keychain: Path
) -> None:
    (keychain / "unlocked").touch()
    data = tmp_path / "home/.local/share/signal-cli/data"
    data.mkdir(parents=True)
    (data / "accounts.json").write_text(
        json.dumps({"accounts": [{"path": "222222", "number": None, "uuid": NUMBERLESS}]})
    )
    (data / "222222").write_text(json.dumps({"version": 9, "registered": True}))
    wizard, _said, _folder = numberless_step(tmp_path, monkeypatch, [NUMBERLESS], ["estia.42"])
    assert wizard.numberless() == NUMBERLESS
    assert keychain_calls(keychain) == []  # no key typed: nothing to keep
    (tmp_path / "q").mkdir()
    wizard, _said, _folder = numberless_step(tmp_path / "q", monkeypatch, [NUMBERLESS, "z" * 64], ["q"])
    with pytest.raises(SystemExit):
        wizard.numberless()
    assert keychain_calls(keychain) == []  # a key Signal refused is never kept


def test_every_line_of_the_signal_step_fits_the_terminal_in_plain_words() -> None:
    """The rules every setup line keeps (owner, 19-20 Oct), on this track's own words: a terminal's 135 columns with
    the longest values filled in, no "secure", "seamless" or "most people", "admin rights" never "sudo"."""
    from local_assistant.prompts import setup_signal

    values = {
        "name": "Maria-Christina Ann",
        "number": "+30 690 000 0000",
        "found": "+30 690 000 0000",
        "username": "maria_christina_ann.123456789",
        "nickname": "maria_christina_ann",
        "other": "maria_christina_ann.58",
        "word": "maria-christina-ann-4821",
        "code": "998",
        "count": "96",
        "room": "64",
        "group": "16",
        "place": "12",
        "size": "12",
        "symbol": hostsec.KEY_LOOKALIKE,
        "refusal": prompts.SETUP_CARD_NOT_KEY,
        "reason": "x" * 60,
        "label": "Recovery key",
        "item": "Recovery key",
        "ipv4": "-Djava.net.preferIPv4Stack=true",
        "when": "10:40 on 25 Oct (local time)",
    }
    card = {"SETUP_CARD_SYMBOL", "SETUP_CARD_NOT_KEY", "SETUP_CARD_NOT_ID"}  # drawn on the card, wrapped there
    for name in setup_signal.__all__:
        text = getattr(setup_signal, name).format(**values)
        if name in card:
            continue
        for line in text.splitlines():
            assert len(line) <= TERMINAL, (name, len(line), line)
        words = f" {text.lower()} "
        assert "secure" not in words and "seamless" not in words and "most people" not in words, name
        assert " sudo " not in words.replace("/usr/bin/sudo", ""), name


# the cards' own names (owner, 28 Oct): "A spare SIM's number" and "Without number: a Signal Account / Login"
_BARE_ACCOUNT = re.compile(r"(?<!Signal )\baccount without a number|spare phone number", re.IGNORECASE)


@pytest.mark.parametrize(
    "name",
    [
        "SETUP_ACCOUNT_UNKNOWN",
        "SETUP_NUMBERLESS_DRY_RUN",
        "SETUP_LOGIN_HERE",
        "SETUP_LOGIN_DONE",
        "SETUP_OWNER_IS_HER",
        "SIGNAL_LOGIN_HERE",
    ],
)
def test_setup_names_her_account_as_the_route_cards_do(name: str) -> None:
    """Second deep scan (dialogue-signal-account-naming): the refusal under the route cards and the lines after them
    said "a spare phone number" and "an account without a number", against the cards' "spare SIM's" and "Signal
    Account"; the same thing keeps the same words on every screen."""
    text = getattr(prompts, name)
    assert not _BARE_ACCOUNT.search(text), text
    assert "Signal Account" in text or "spare SIM's" in text


def test_no_setup_line_names_route_2_but_as_the_card_does() -> None:
    """Review S08 (dialogue D098-06): six lines (the SMS code's Stop here and stop, the old PIN's screen and stop, a
    refused registration and --redo signal on an install set up) still said "an account without a number" while the
    card the friend goes back to says "a Signal Account"; every setup line names it as the card does."""
    bare = {
        name
        for name in dir(prompts)
        if name.startswith("SETUP_")
        and isinstance(value := getattr(prompts, name), str)
        and _BARE_ACCOUNT.search(value)
    }
    assert bare == set()
    for name in ("SETUP_CODE_STOP", "SETUP_CODE_STOPPED", "SETUP_OLD_PIN", "SETUP_OLD_PIN_STOPPED",
                 "SETUP_REGISTER_FAILED", "SETUP_REDO_SIGNAL"):  # fmt: skip
        assert "a Signal Account without a number" in " ".join(getattr(prompts, name).split()), name


def test_the_route_refusal_and_the_moved_line_in_the_cards_words() -> None:
    assert prompts.SETUP_ACCOUNT_UNKNOWN == (
        "  Type 1 for a spare SIM's number or 2 for a Signal Account without a number."
    )
    assert prompts.SETUP_LOGIN_DONE.format(name="Maya") == (
        "✓ Signal: Maya's Signal Account is on this Mac now; Signal on the Android phone is logged out."
    )


def test_her_account_id_is_shown_in_the_cards_words() -> None:
    """The same scan: a run again's "Kept: Account ID: …" and signal-login's switched line named her account "the
    account without a number ending in …"; the last four characters of the Account ID only, as before."""
    shown = hostsec.shown_account(NUMBERLESS)  # a patterned fake: the privacy scan refuses any other UUID
    assert shown == "the Signal Account ending in 5555"
    assert NUMBERLESS[:8] not in shown
    assert not _BARE_ACCOUNT.search(shown)


@pytest.mark.parametrize("code", [None, -3])
def test_no_answer_from_signal_is_never_read_as_a_wrong_key(code: int | None) -> None:
    """signal-route2-network-as-wrong-key: signal-cli's -3 (its IO error: the Wi-Fi dropped while the account moved)
    fell to "check the Account ID and the Recovery key", and the cards reopened with "Signal refused it". It is no
    answer, as no code is: the network named, the cards reopened with the groups kept and no refusal."""
    from local_assistant import setup_signal

    why = "java.net.UnknownHostException: chat.signal.org"
    said = setup_signal.login_failed(why, code)
    assert said.startswith("That did not work (") and "no answer from Signal" in said and "network" in said
    assert setup_signal.card_message(code, why=why) == prompts.SETUP_CARD_NO_ANSWER
    assert setup_signal.card_message(-1) == prompts.SETUP_CARD_REFUSED
    assert prompts.SETUP_CARD_NO_ANSWER == "No answer from Signal: check the Wi-Fi. Every group is kept."


@pytest.mark.parametrize(("planned", "offered"), [(BOT, True), ("+306900000001", False)])
def test_a_number_set_up_here_is_offered_only_when_it_is_the_plans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planned: str, offered: bool
) -> None:
    """signal-known-number-ignores-plan: after a change to SIM B (Change, or --redo number) the number set up here
    before (SIM A) was offered as hers, recommended; then her config had SIM A and the answers SIM B. It is offered
    only when it is the plan's number; else the plan's number is registered."""
    asked = typed_once(monkeypatch, "")
    monkeypatch.setattr("local_assistant.harden.in_terminal", lambda: True)
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.planned["number"] = planned
    monkeypatch.setattr(wizard, "registered_numbers", lambda: [BOT])
    steps: list[str] = []
    for name in ("register_number", "verify_number", "registration_lock"):
        monkeypatch.setattr(wizard, name, lambda number, name=name: steps.append(f"{name} {number}"))
    assert wizard.signal_number() == planned
    assert (asked == [f"{filled(prompts.SETUP_KNOWN_NUMBER_ASK)} [Y/n] "]) is offered
    assert steps == ([] if offered else [f"register_number {planned}", f"verify_number {planned}",
                                          f"registration_lock {planned}"])  # fmt: skip


def test_the_old_pin_screen_names_your_own_number_the_last_owners_lock_and_the_ways_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """signal-number-is-own and signal-reglock-unknown-dead-end: the old-PIN screen said nothing at the one telling
    moment (your own number: registering it logs your phone's Signal out), and q promised a new code while a re-run
    met the same lock. It names both, Signal's 7 days and the two ways out; q at it stops with them too."""
    lead = plain_text(prompts.SETUP_OLD_PIN)
    assert "If this is your own number, stop here: registering it logs your phone's Signal out." in lead
    assert "Signal frees it after 7 days without use" in lead
    assert "scripts/setup.sh --redo number" in lead and "--redo signal" in lead
    assert "asks Signal for a new code" not in prompts.SETUP_OLD_PIN_STOP
    wizard, _said, _folder = number_step(
        tmp_path, monkeypatch, ["+30 690 000 0000", "", "n"], ["111111", "q"], FAKE_SIGNAL_PIN="9876"
    )
    with pytest.raises(SystemExit) as stopped:
        wizard.signal_number()
    assert str(stopped.value) == prompts.SETUP_OLD_PIN_STOPPED
    assert "--redo number" in prompts.SETUP_OLD_PIN_STOPPED and "--redo signal" in prompts.SETUP_OLD_PIN_STOPPED


def test_the_card_without_a_number_says_a_spare_phone_never_your_own() -> None:
    """signal-numberless-steps-late: the card before the choice named only "an Android phone with Google Play"; never
    the phone with your own Signal (one Signal app holds one account) came at Step 3, about 25 minutes later."""
    line = plain_text(prompts.SETUP_ROUTE_NO_NUMBER_LINE)
    assert line.splitlines()[:2] == [
        "Needs: a spare or borrowed Android phone with Google Play where Signal is not set up yet,",
        "never the phone with your own Signal.",
    ]


@pytest.mark.parametrize(("answer", "told"), [("2", True), ("", False)])
def test_route_2_picked_says_the_phones_part_at_once(monkeypatch: pytest.MonkeyPatch, answer: str, told: bool) -> None:
    """signal-numberless-steps-late: the phone's part (a spare Android, Register without number, Pay, the Account ID
    and the Recovery key) came only at the Signal step, about 25 minutes after the plan. Picked, the route says it
    right away, so the account is made while the downloads run; the spare number's route says nothing of it."""
    wizard, said, _prompted = account_step(monkeypatch, ["", answer])
    wizard.signal_account()
    text = "\n".join(said)
    heads_up = prompts.SETUP_ROUTE_NO_NUMBER_NEXT  # said as setup's words are (the face draws the bold)
    assert (heads_up in text) is told
    if told:
        assert text.index(prompts.SETUP_ACCOUNT_ASK) < text.index(heads_up)
        assert "**Register without number**" in heads_up and "Account ID and the Recovery key" in heads_up


def test_a_registration_signal_refuses_is_said_by_what_happened() -> None:
    """signal-register-errors-raw: the number route raised signal-cli's text as it came (a UTC time on two lines, a
    Java exception, "No answer within 120 s."), with no Fix, while route 2 had plain words for the same cases. The
    limit names the wait in local time; no answer (no code, or -3) names the Wi-Fi; anything else names the way
    without a number."""
    from datetime import timedelta, timezone

    from local_assistant.setup_signal import local_times, register_failed

    athens = timezone(timedelta(hours=2))
    limited = register_failed("Rate limit reached\nNext attempt may be tried at 2026-10-25T09:40Z", -5, tz=athens)
    assert limited == (
        "✗ Signal: it limits registrations for now (Rate limit reached Next attempt may be tried at 11:40 on 25 Oct"
        " (local time)).\n  Fix: wait until then, then run setup again: your answers are kept, and each try is one"
        " more registration."
    )
    for code in (None, -3):
        said = register_failed("No answer within 120 s.", code)
        assert said.startswith(
            "✗ Signal: no answer from Signal (No answer within 120 s.): the number was not registered."
        )
        assert "check the Wi-Fi, then run setup again: your answers are kept" in said
    other = register_failed("java.net.UnknownHostException: chat.signal.org", -1)
    assert other.startswith("✗ Signal: the number was not registered (java.net.UnknownHostException: chat.signal.org).")
    assert "scripts/setup.sh --redo signal" in other
    assert local_times("at 2026-10-25T09:40:05Z and 2026-12-31T23:30Z", tz=athens) == (
        "at 11:40 on 25 Oct (local time) and 01:30 on 1 Jan (local time)"
    )


def test_a_registration_says_it_waits_for_signal_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Two silent minutes before "No answer within 120 s.": one line says what setup waits for, before each request."""
    wizard, said, _folder = signal_step(
        tmp_path, monkeypatch, ["signalcaptcha://right-token"], FAKE_SIGNAL_REGISTER_LIMIT="1"
    )
    with pytest.raises(SystemExit):
        wizard.signal_number()
    assert prompts.SETUP_SIGNAL_WAITING.format(minutes=2) == "  Waiting for Signal (up to 2 minutes)."
    assert said.count(prompts.SETUP_SIGNAL_WAITING.format(minutes=2)) == 1  # the one register (Signal's limit)
    shown = next(at for at, line in enumerate(said) if f"register {BOT} on stdin" in line)  # the request, then the wait
    assert said.index(prompts.SETUP_SIGNAL_WAITING.format(minutes=2)) == shown + 1
