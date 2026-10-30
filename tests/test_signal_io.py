import asyncio
import base64
import json
import sys
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from local_assistant.signal_io import (
    RATE_LIMIT_WAIT_S,
    CaptchaRejectedError,
    NotAllowedError,
    RateLimitError,
    SignalClient,
    SignalError,
    UntrustedIdentityError,
    attachment_timeout,
    call_env,
    shown_error,
    signal_cli_command,
    stderr_line,
)
from tests.conftest import ME, STRANGER, T0
from tests.fakes.fake_signal_cli import LEAKS

FAKE = Path(__file__).parent / "fakes" / "fake_signal_cli.py"


@pytest.fixture
def fake_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "signal"
    folder.mkdir()
    monkeypatch.setenv("FAKE_SIGNAL_DIR", str(folder))
    return folder


def make_client(command: list[str] | None = None, **kwargs: Any) -> SignalClient:
    settings: dict[str, Any] = {"request_timeout": 5.0, "backoff_start": 0.05, "backoff_max": 0.2}
    settings.update(kwargs)
    return SignalClient(command or [sys.executable, str(FAKE)], frozenset({ME}), **settings)


def deliver(folder: Path, envelope: dict[str, Any]) -> None:
    with (folder / "inbox.jsonl").open("a") as handle:
        handle.write(json.dumps(envelope) + "\n")


def requests(folder: Path) -> list[dict[str, Any]]:
    path = folder / "requests.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


async def until(condition: Callable[[], bool], limit: float = 5.0) -> None:
    async with asyncio.timeout(limit):
        while not condition():
            await asyncio.sleep(0.02)


def test_command_forces_ipv4_and_manual_receive() -> None:
    command = signal_cli_command("/opt/homebrew/bin/signal-cli", "+306900000000")
    assert command[:2] == ["/opt/homebrew/bin/signal-cli", "-Djava.net.preferIPv4Stack=true"]
    assert command[command.index("-a") + 1] == "+306900000000"
    assert command[command.index("jsonRpc") + 1 : command.index("jsonRpc") + 3] == ["--receive-mode", "manual"]
    assert "--scrub-log" in command
    assert "--ignore-attachments" not in command  # photos are fetched with getAttachment


async def test_subscribes_then_delivers_envelopes(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "hi"}})
        stream = client.envelopes()
        envelope = await asyncio.wait_for(anext(stream), 5)
        assert envelope["dataMessage"]["message"] == "hi"
        assert requests(fake_dir)[0]["method"] == "subscribeReceive"
    finally:
        await client.stop()


async def test_send_returns_signal_timestamp(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        timestamp = await client.send(ME, "Hello!")
        assert timestamp > 0
        send = next(r for r in requests(fake_dir) if r["method"] == "send")
        assert send["params"] == {"recipient": [ME], "message": "Hello!"}
    finally:
        await client.stop()


async def test_text_styles_go_out_as_signal_clis_text_style(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        await client.send(ME, "Today\n• 09:30 Dentist", styles=("0:5:BOLD",))
        await client.send(ME, "plain")
        first, second = [r["params"] for r in requests(fake_dir) if r["method"] == "send"]
        assert first == {"recipient": [ME], "message": "Today\n• 09:30 Dentist", "textStyle": ["0:5:BOLD"]}
        assert second == {"recipient": [ME], "message": "plain"}  # no styles: the request as before
    finally:
        await client.stop()


@pytest.mark.parametrize("bad", ["0:6:BOLD", "0:5:bold", "0:5:UNDERLINE", "0-5-BOLD", "3:0:ITALIC"])
async def test_a_style_signal_cli_would_refuse_is_never_sent(fake_dir: Path, bad: str) -> None:
    """signal-cli fails the whole send on one bad entry (and never checks a range against the text): the text goes out
    without styles instead."""
    client = make_client()
    await client.start()
    try:
        assert await client.send(ME, "hello", styles=("0:1:BOLD", bad)) > 0
        send = next(r for r in requests(fake_dir) if r["method"] == "send")
        assert send["params"] == {"recipient": [ME], "message": "hello"}
    finally:
        await client.stop()


@pytest.mark.parametrize(
    ("styles", "error"),
    [
        (["0:5:bold"], "Invalid style"),
        (["0-5-BOLD"], "Invalid textStyle syntax"),
        ("0:5:WAVY", "Invalid style"),
    ],
)
async def test_the_fake_refuses_text_styles_as_signal_cli_does(
    fake_dir: Path, styles: str | list[str], error: str
) -> None:
    client = make_client()
    await client.start()
    try:
        with pytest.raises(SignalError) as info:
            await client.request("send", {"recipient": [ME], "message": "hello", "textStyle": styles})
        assert error in info.value.detail  # signal-cli's words: never in the error's text (review S1)
        assert await client.request("send", {"recipient": [ME], "message": "hello", "textStyle": "0:5:BOLD"})
    finally:
        await client.stop()


async def test_a_voice_note_is_an_attachment_with_the_voice_note_flag(fake_dir: Path, tmp_path: Path) -> None:
    note = tmp_path / "answer.m4a"
    note.write_bytes(b"\x00\x00\x00\x1cftypM4A " + bytes(100))
    client = make_client()
    await client.start()
    try:
        assert await client.send_voice_note(ME, note) > 0
        send = next(r for r in requests(fake_dir) if r["method"] == "send")
        assert send["params"] == {"recipient": [ME], "attachments": [str(note)], "voiceNote": True}
        with pytest.raises(NotAllowedError):
            await client.send_voice_note(STRANGER, note)
    finally:
        await client.stop()


async def test_a_document_goes_as_a_data_link_attachment_with_its_name(fake_dir: Path) -> None:
    """signal-cli 0.14.9's send takes "attachments": paths or RFC 2397 data links with a filename parameter (URL
    decoded): the PDF's bytes go inline, so signal-cli opens no file of the folder (no link to follow)."""
    data = b"%PDF-1.7\nPets are allowed.\n"
    client = make_client()
    await client.start()
    try:
        assert await client.send_document(ME, data, "2026-10-14 Lease (2).pdf") > 0
        send = next(r for r in requests(fake_dir) if r["method"] == "send")
        encoded = base64.b64encode(data).decode()
        link = f"data:application/pdf;filename=2026-10-14%20Lease%20%282%29.pdf;base64,{encoded}"
        assert send["params"] == {"recipient": [ME], "attachments": [link]}
        with pytest.raises(NotAllowedError):
            await client.send_document(STRANGER, data, "lease.pdf")
    finally:
        await client.stop()


async def test_a_large_document_gets_time_for_its_upload(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review O9 (0.9.7): /docs send of a PDF up to 25 MB had signal-cli's fixed request limit; a slow upload was
    reported failed while signal-cli could still deliver it, so a retry sent it twice. The limit grows with the size
    (1 s per 200 KB more); other requests keep theirs."""
    monkeypatch.setenv("FAKE_SIGNAL_ATTACHMENT_DELAY", "1.5")
    client = make_client(request_timeout=1.0)
    await client.start()
    try:
        data = b"%PDF-1.7\n" + b"x" * 400_000  # 1 s + 2 s
        assert await client.send_document(ME, data, "manual.pdf") > 0
        assert await client.send(ME, "small") > 0
    finally:
        await client.stop()


def test_an_attachments_time_limit_grows_with_its_size() -> None:
    assert attachment_timeout(60.0, 0) == 60.0
    assert attachment_timeout(60.0, 25_000_000) == 185.0  # the largest PDF /docs sends: 60 s plus 125 s


async def test_refuses_to_send_to_anyone_not_allowed(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        with pytest.raises(NotAllowedError):
            await client.send(STRANGER, "leak")
        with pytest.raises(NotAllowedError):
            await client.typing(STRANGER)
        with pytest.raises(NotAllowedError):
            await client.receipt(STRANGER, T0)
        assert [r["method"] for r in requests(fake_dir)] == ["subscribeReceive"]
    finally:
        await client.stop()


async def test_typing_and_receipt_use_signal_cli_parameter_names(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        await client.typing(ME, stop=True)
        await client.receipt(ME, T0)
        by_method = {r["method"]: r["params"] for r in requests(fake_dir)}
        assert by_method["sendTyping"] == {"recipient": [ME], "stop": True}
        assert by_method["sendReceipt"] == {"recipient": ME, "targetTimestamp": [T0], "type": "read"}
    finally:
        await client.stop()


async def test_untrusted_identity_error_is_typed(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-4")
    client = make_client()
    await client.start()
    try:
        with pytest.raises(UntrustedIdentityError) as info:
            await client.send(ME, "Hello!")
        assert info.value.code == -4
    finally:
        await client.stop()


def rate_limited(**result: Any) -> str:
    """signal-cli 0.14.9's error data for a send Signal's server limited (-5): the send's results."""
    return json.dumps({"response": {"timestamp": T0, "results": [{"type": "RATE_LIMIT_FAILURE", **result}]}})


async def test_a_rate_limit_is_typed_with_its_wait(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """-5 (signal-cli's RATELIMIT_ERROR): Signal's server limits her sending for retryAfterSeconds; a short one is
    waited out (`wait`), a long one is no try now (the resend later), as the man page's codes say."""
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-5")
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR_DATA", rate_limited(retryAfterSeconds=7))
    client = make_client()
    await client.start()
    try:
        with pytest.raises(RateLimitError) as info:
            await client.send(ME, "Hello!")
        error = info.value
        assert (error.code, error.retry_after, error.proof_required) == (-5, 7.0, False)
        assert error.wait(2.0) == 7.0 and isinstance(error, SignalError)
    finally:
        await client.stop()
    assert RateLimitError("x", -5, retry_after=RATE_LIMIT_WAIT_S + 1).wait(2.0) is None  # too long: no try now
    assert RateLimitError("x", -5).wait(2.0) == 2.0  # no time told: the usual pause
    assert SignalError("x").wait(2.0) == 2.0


async def test_a_captcha_signal_asks_for_stops_the_tries_and_is_logged_without_its_token(
    fake_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A rate limit with a challenge token (proof required): lifted by a captcha or after the wait Signal names
    (signal-cli: "or wait N seconds"), so no more tries now; the log says what Signal asks, the wait and where the
    steps are, never the token."""
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-5")
    challenge = "made-up-challenge-0123"  # Signal's challenge for the captcha (a made-up one)
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR_DATA", rate_limited(token=challenge, retryAfterSeconds=86400))
    client = make_client()
    await client.start()
    try:
        with pytest.raises(RateLimitError) as info:
            await client.send(ME, "Hello!")
        assert info.value.proof_required and info.value.wait(2.0) is None
    finally:
        await client.stop()
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "captcha" in logged and "setup_assistant.md" in logged and challenge not in logged
    assert "or after 86400 s" in logged and "until it is solved" not in logged


async def test_a_rejected_captcha_is_typed(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", "-6")  # CAPTCHA_REJECTED_ERROR
    client = make_client()
    await client.start()
    try:
        with pytest.raises(CaptchaRejectedError) as info:
            await client.send(ME, "Hello!")
        assert info.value.code == -6 and info.value.wait(2.0) is None  # a new captcha is needed: no try now
    finally:
        await client.stop()


async def test_restarts_after_a_crash_and_keeps_delivering(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_CRASH_ONCE", "1")
    client = make_client()
    await client.start()
    try:
        stream = client.envelopes()
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "one"}})
        first = await asyncio.wait_for(anext(stream), 5)
        await until(lambda: client.restarts >= 1 and client.ready)
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0 + 1, "dataMessage": {"message": "two"}})
        second = await asyncio.wait_for(anext(stream), 5)
        assert [first["dataMessage"]["message"], second["dataMessage"]["message"]] == ["one", "two"]
    finally:
        await client.stop()


async def test_failing_signal_cli_backs_off_and_start_times_out() -> None:
    client = make_client(command=[sys.executable, "-c", "raise SystemExit(1)"], request_timeout=0.6)
    with pytest.raises(TimeoutError):
        await client.start()
    try:
        assert 1 <= client.restarts <= 8  # waits 0.05, 0.1, 0.2, 0.2 ...: not a tight loop
    finally:
        await client.stop()


async def test_get_attachment_returns_the_file_bytes(fake_dir: Path) -> None:
    (fake_dir / "attachments").mkdir()
    (fake_dir / "attachments" / "abc123.jpg").write_bytes(b"\xff\xd8\xff photo bytes")
    client = make_client()
    await client.start()
    try:
        assert await client.get_attachment(ME, "abc123.jpg") == b"\xff\xd8\xff photo bytes"
        request = next(r for r in requests(fake_dir) if r["method"] == "getAttachment")
        assert request["params"] == {"id": "abc123.jpg", "recipient": ME}
        with pytest.raises(SignalError):
            await client.get_attachment(ME, "missing.jpg")
        with pytest.raises(NotAllowedError):
            await client.get_attachment(STRANGER, "abc123.jpg")
    finally:
        await client.stop()


async def test_a_20_mb_photo_fits_in_one_json_line(fake_dir: Path) -> None:
    (fake_dir / "attachments").mkdir()
    big = bytes(range(256)) * (20_000_000 // 256)  # about 26.7 MB as base64 in one line
    (fake_dir / "attachments" / "big.jpg").write_bytes(big)
    client = make_client()
    await client.start()
    try:
        assert await client.get_attachment(ME, "big.jpg") == big
        assert client.restarts == 0
    finally:
        await client.stop()


async def test_an_oversized_line_is_dropped_without_a_restart(fake_dir: Path) -> None:
    (fake_dir / "attachments").mkdir()
    (fake_dir / "attachments" / "huge.jpg").write_bytes(b"\xff" * 200_000)  # ~270 KB of base64 in one line
    (fake_dir / "attachments" / "small.jpg").write_bytes(b"\xff\xd8\xff small")
    client = make_client(read_limit=64 * 1024, request_timeout=1.0)
    await client.start()
    try:
        with pytest.raises(SignalError):
            await client.get_attachment(ME, "huge.jpg")  # its answer is dropped: the request times out
        assert await client.get_attachment(ME, "small.jpg") == b"\xff\xd8\xff small"
        assert client.restarts == 0
    finally:
        await client.stop()


def test_the_jvm_build_gets_no_java_argument() -> None:
    native = signal_cli_command("/x/signal-cli", "+306900000000")
    jvm = signal_cli_command("/x/signal-cli", "+306900000000", jvm=True)
    assert "-Djava.net.preferIPv4Stack=true" not in jvm  # the JVM launcher would pass it to signal-cli: JAVA_OPTS
    assert jvm == [native[0], *native[2:]]


def test_signal_cli_is_told_its_data_folder(tmp_path: Path) -> None:
    # live 13 Oct: under her own user macOS kept /var/empty as the account's home and Java (signal-cli) reads its home
    # from there, not from $HOME: the folder is given explicitly
    command = signal_cli_command("/x/signal-cli", "+306900000000", config_dir=tmp_path / "signal-cli")
    assert command[command.index("--config") + 1] == str(tmp_path / "signal-cli")
    assert command.index("--config") < command.index("-a")
    assert "--config" not in signal_cli_command("/x/signal-cli", "+306900000000")


def test_call_env_points_signal_cli_at_java_and_the_call_helper(tmp_path: Path) -> None:
    env = call_env(java_home=Path("/opt/jdk"), tunnel=Path("/t/sct"), socket_dir=tmp_path, home=Path("/var/estia"))
    assert env == {
        "JAVA_HOME": "/opt/jdk",
        "JAVA_OPTS": "-Djava.net.preferIPv4Stack=true -Duser.home=/var/estia",
        "SIGNAL_CALL_TUNNEL_BIN": "/t/sct",
        "SIGNAL_CALL_TUNNEL_AUDIO_MODE": "pipe",
        "SIGNAL_CALL_TUNNEL_SOCKET_DIR": str(tmp_path),
    }
    spaced = call_env(java_home=None, tunnel=Path("/t/sct"), socket_dir=tmp_path, home=Path("/tmp/a b"))
    assert spaced["JAVA_OPTS"] == "-Djava.net.preferIPv4Stack=true"  # JAVA_OPTS splits on spaces: such a home stays out


async def test_the_child_gets_the_extra_environment_on_top_of_the_bridges(fake_dir: Path) -> None:
    client = make_client(env={"JAVA_OPTS": "-Dx=1"})
    await client.start()  # FAKE_SIGNAL_DIR still comes from the bridge's own environment
    try:
        launch = json.loads((fake_dir / "launch.json").read_text())
        assert launch["env"]["JAVA_OPTS"] == "-Dx=1"
    finally:
        await client.stop()


async def test_with_calls_it_subscribes_to_call_events_before_receiving(fake_dir: Path) -> None:
    client = make_client(calls=True)
    await client.start()
    try:
        assert [r["method"] for r in requests(fake_dir)][:2] == ["subscribeCallEvents", "subscribeReceive"]
    finally:
        await client.stop()


async def test_without_calls_there_is_no_call_subscription(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        assert [r["method"] for r in requests(fake_dir)] == ["subscribeReceive"]
    finally:
        await client.stop()


async def test_a_failed_call_subscription_does_not_stop_texting(
    fake_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_NO_CALLS", "1")
    client = make_client(calls=True)
    await client.start()
    try:
        assert client.ready
    finally:
        await client.stop()


async def test_call_events_have_their_own_stream(fake_dir: Path) -> None:
    client = make_client(calls=True)
    await client.start()
    try:
        event = {"callId": -5, "state": "RINGING_INCOMING", "uuid": ME, "isOutgoing": False}
        deliver(fake_dir, {"method": "callEvent", "params": {"subscription": 1, "result": event}})
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "hi"}})
        assert await asyncio.wait_for(anext(client.call_events()), 5) == event
        envelope = await asyncio.wait_for(anext(client.envelopes()), 5)
        assert envelope["dataMessage"]["message"] == "hi"  # an envelope after it still arrives
    finally:
        await client.stop()


async def test_a_signal_cli_restart_ends_any_call(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_CRASH_ONCE", "1")
    client = make_client(calls=True)
    await client.start()
    try:
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "crash"}})
        ended = await asyncio.wait_for(anext(client.call_events()), 5)
        assert ended == {"state": "ENDED", "reason": "signal_cli_restarted"}
    finally:
        await client.stop()


async def test_a_call_is_placed_only_to_an_allowed_account(fake_dir: Path) -> None:
    client = make_client(calls=True)
    await client.start()
    try:
        result = await client.start_call(ME)
        assert result["callId"] == 42
        ringing = client._call_events.get_nowait()  # signal-cli sends the ringing event before startCall's reply
        assert (ringing["state"], ringing["isOutgoing"], ringing["uuid"]) == ("RINGING_OUTGOING", True, ME)
        with pytest.raises(NotAllowedError):
            await client.start_call(STRANGER)
        calls = [(r["method"], r["params"]) for r in requests(fake_dir) if r["method"] == "startCall"]
        assert calls == [("startCall", {"recipient": [ME]})]
    finally:
        await client.stop()


async def test_accept_and_hangup_only_for_allowed_callers(fake_dir: Path) -> None:
    client = make_client(calls=True)
    await client.start()
    try:
        result = await client.accept_call(ME, -5)
        assert result["state"] == "CONNECTING"
        await client.hangup_call(ME, -5)
        with pytest.raises(NotAllowedError):
            await client.accept_call(STRANGER, 6)
        with pytest.raises(NotAllowedError):
            await client.hangup_call(STRANGER, 6)
        calls = [(r["method"], r["params"]) for r in requests(fake_dir) if r["method"].endswith("Call")]
        assert calls == [("acceptCall", {"callId": -5}), ("hangupCall", {"callId": -5})]
    finally:
        await client.stop()


# hardening: the identity check pinned, receive exceptions kept, linked devices listed


@pytest.mark.parametrize("jvm", [False, True])
def test_new_identities_are_trusted_only_on_first_use(jvm: bool) -> None:
    """A changed safety number is never trusted by itself (a SIM swap of the user's number looks just like one): the
    flag is pinned in the command, not left to signal-cli's default."""
    command = signal_cli_command("/x/signal-cli", "+306900000000", jvm=jvm)
    assert command.count("--trust-new-identities") == 1
    assert command[command.index("--trust-new-identities") + 1] == "on-first-use"
    assert "always" not in command
    assert command.index("--trust-new-identities") < command.index("jsonRpc")  # a global option


UNTRUSTED: dict[str, Any] = {
    "exception": {"message": f"Untrusted identity: {ME}", "type": "UntrustedIdentityException"},
    "envelope": {
        "source": ME,
        "sourceNumber": None,
        "sourceUuid": ME,
        "sourceName": None,
        "sourceDevice": 1,
        "timestamp": T0,
        "serverReceivedTimestamp": T0 + 5,
        "serverDeliveredTimestamp": T0 + 9,
    },
}


async def test_a_receive_exception_is_kept_on_its_own_stream(fake_dir: Path) -> None:
    """signal-cli 0.14.9 puts an `exception` (message, type) next to the envelope of a message it could not open,
    such as one from a changed safety number: it reaches the bridge as a problem (type, sender, device; never the
    exception's message, which names the account), instead of being dropped."""
    client = make_client()
    await client.start()
    try:
        deliver(fake_dir, UNTRUSTED)
        deliver(fake_dir, {"exception": {"message": "Invalid message", "type": "InvalidMessageException"}})
        problems = client.problems()
        first = await asyncio.wait_for(anext(problems), 5)
        assert (first.kind, first.sender, first.device) == ("UntrustedIdentityException", ME, 1)
        assert first.untrusted_identity
        second = await asyncio.wait_for(anext(problems), 5)  # no envelope at all: still reported
        assert (second.kind, second.sender, second.device, second.untrusted_identity) == (
            "InvalidMessageException",
            None,
            None,
            False,
        )
        assert not hasattr(first, "message")  # the exception's text (it names the account) is not kept
    finally:
        await client.stop()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [("Untrusted\nidentity <script>", "Untrustedidentityscript"), ("", "unknown"), (None, "unknown"), (7, "unknown")],
)
def test_a_problems_type_is_kept_as_a_plain_word(kind: object, expected: str) -> None:
    client = make_client()
    client._dispatch({"method": "receive", "params": {"result": {"exception": {"type": kind}}}})
    assert client._problems.get_nowait().kind == expected


async def test_linked_devices_are_listed_as_ids_and_creation_times_only(
    fake_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    devices = [
        {"id": 1, "name": None, "createdTimestamp": 1_700_000_000_000, "lastSeenTimestamp": 1_760_000_000_000},
        {"id": 3, "name": "Example tablet", "createdTimestamp": 1_760_000_100_000, "lastSeenTimestamp": None},
    ]
    monkeypatch.setenv("FAKE_SIGNAL_DEVICES", json.dumps(devices))
    client = make_client()
    await client.start()
    try:
        listed = await client.list_devices()
        assert [(device.id, device.created) for device in listed] == [(1, 1_700_000_000_000), (3, 1_760_000_100_000)]
        assert "Example tablet" not in repr(listed)  # names are never kept
        assert requests(fake_dir)[-1]["method"] == "listDevices"
    finally:
        await client.stop()


async def test_odd_device_entries_are_left_out(fake_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    devices: list[object] = [{"id": 1}, {"id": "2"}, {"id": True}, "x", {"id": 0}, {"id": 4, "createdTimestamp": "?"}]
    monkeypatch.setenv("FAKE_SIGNAL_DEVICES", json.dumps(devices))
    client = make_client()
    await client.start()
    try:
        assert [(device.id, device.created) for device in await client.list_devices()] == [(1, None), (4, None)]
    finally:
        await client.stop()


async def test_the_fake_names_the_sending_device_as_signal_cli_does(fake_dir: Path) -> None:
    client = make_client()
    await client.start()
    try:
        deliver(fake_dir, {"sourceUuid": ME, "timestamp": T0, "dataMessage": {"message": "hi"}})
        deliver(fake_dir, {"sourceUuid": ME, "sourceDevice": 2, "timestamp": T0 + 1, "dataMessage": {"message": "x"}})
        stream = client.envelopes()
        assert (await asyncio.wait_for(anext(stream), 5))["sourceDevice"] == 1
        assert (await asyncio.wait_for(anext(stream), 5))["sourceDevice"] == 2
    finally:
        await client.stop()


def leaked(caplog: pytest.LogCaptureFixture) -> list[str]:
    """The made-up number, account and challenge token the fake's errors name, wherever the log holds one."""
    logged = " ".join(record.getMessage() for record in caplog.records)
    return [value for value in (*LEAKS, ME) if value in logged]


@pytest.mark.parametrize("code", ["-1", "-4", "-5", "-6"])
async def test_signal_clis_words_never_reach_an_errors_text(
    fake_dir: Path, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    """A traceback (every log.exception: a call that failed, a message that failed) prints an error's text, so
    signal-cli's words, which name the recipient's number or account and a captcha's challenge token, stay out of it,
    and a refused recipient is not named either; the code is kept, the words only in `detail` (review S1)."""
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR", code)
    monkeypatch.setenv("FAKE_SIGNAL_SEND_ERROR_DATA", rate_limited(token="made-up", retryAfterSeconds=60))
    client = make_client()
    await client.start()
    try:
        with pytest.raises(SignalError) as info:
            await client.send(ME, "Hello!")
        with pytest.raises(NotAllowedError) as refused:
            await client.send(STRANGER, "Hello!")
    finally:
        await client.stop()
    shown = "".join(traceback.format_exception(info.value) + traceback.format_exception(refused.value))
    assert [value for value in (*LEAKS, ME, STRANGER) if value in shown] == []
    assert info.value.code == int(code) and LEAKS[0] in info.value.detail


def test_an_error_is_shown_by_its_type_and_code_only() -> None:
    """signal-cli's message can name the recipient's number or account and a captcha's challenge token (review S1):
    the log keeps the error's type and code, never its words."""
    words = 'CAPTCHA proof required for sending to "+306900000099" with challenge token "secret"'
    assert shown_error(RateLimitError(words, -5)) == "RateLimitError (code -5)"
    assert shown_error(SignalError(words)) == "SignalError"
    assert shown_error(NotAllowedError(f"recipient {STRANGER} is not on the allowlist")) == "NotAllowedError"


@pytest.mark.parametrize(
    ("line", "shown", "kind"),
    [
        (
            'WARN  SendHelper - CAPTCHA proof required for sending to "+306900000099", available options '
            '"[RECAPTCHA]" with challenge token "made-up", or wait "86400" seconds. (ProofRequiredException)',
            "signal-cli: WARN SendHelper: captcha asked (ProofRequiredException; {n} characters not logged)",
            "captcha asked",
        ),
        (
            "User +306900000099 is not registered.",
            "signal-cli: not registered ({n} characters not logged)",
            "not registered",
        ),
        (
            "Error while checking account cccccccc-cccc-4ccc-8ccc-cccccccccccc: [401] Authorization failed!",
            "signal-cli: authorization failed ({n} characters not logged)",
            "authorization failed",
        ),
        (
            "ERROR App - Error loading state file for user +306900000099: (DeprecatedVersionException)",
            "signal-cli: ERROR App: client too old (DeprecatedVersionException; {n} characters not logged)",
            "client too old",
        ),
        (
            "INFO  ReceiveHelper - Connection closed unexpectedly, reconnecting in 100 ms",
            "signal-cli: INFO ReceiveHelper: a line ({n} characters not logged)",
            None,
        ),
        ("\tat org.asamk.signal.App.loadManager(App.java:344)", "", None),  # a stack frame: nothing to log
        ("   ", "", None),
    ],
)
def test_signal_clis_error_output_is_summed_up_without_its_words(line: str, shown: str, kind: str | None) -> None:
    """Its stderr names numbers, accounts and challenge tokens too (review S1): only the level, the class that
    logged, a kind from a fixed list and the exceptions' names are logged, with the count of characters left out."""
    assert stderr_line(line) == (shown.format(n=len(line.strip())), kind)


async def test_signal_clis_stderr_reaches_the_log_without_numbers_or_tokens(
    fake_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("FAKE_SIGNAL_STDERR", "1")
    client = make_client()
    caplog.set_level("INFO")
    await client.start()
    try:
        await until(lambda: any("authorization failed" in r.getMessage() for r in caplog.records))
    finally:
        await client.stop()
    assert any("signal-cli: WARN SendHelper: captcha asked" in r.getMessage() for r in caplog.records)
    assert leaked(caplog) == []


async def test_a_failed_send_and_a_failed_start_are_logged_without_signal_clis_words(
    fake_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The fake's errors name a number, an account and a token, as signal-cli's do: none reaches the log."""
    monkeypatch.setenv("FAKE_SIGNAL_SUBSCRIBE_ERROR", "1")
    client = make_client(request_timeout=0.8)
    with pytest.raises(TimeoutError):
        await client.start()
    await client.stop()
    assert any("signal-cli: SignalError (code -1)" in r.getMessage() for r in caplog.records)
    assert leaked(caplog) == []


async def test_each_ready_and_each_exit_is_told_with_the_failures_kind(
    fake_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Signal logged this Mac out (another device took her account over): signal-cli exits at every start with "User …
    is not registered." and was restarted quietly every 60 s (review S2). Each ready and each exit is told to the
    watchers, the exit with its kind from signal-cli's stderr (never its words), so status.json and doctor can say so."""
    told: list[tuple[bool, str]] = []
    client = make_client()
    client.watch(lambda ready, failure: told.append((ready, failure)))
    await client.start()
    try:
        assert told == [(True, "")]
        monkeypatch.setenv("FAKE_SIGNAL_SUBSCRIBE_ERROR", "1")
        assert client._proc is not None
        client._proc.terminate()
        await until(lambda: len(told) >= 3)
        assert told[1] == (False, "exited")  # stopped from outside: no kind in its words
        assert told[2] == (False, "not registered")
        assert not client.ready
        monkeypatch.delenv("FAKE_SIGNAL_SUBSCRIBE_ERROR")
        await until(lambda: client.ready, 10)
        assert told[-1] == (True, "")
    finally:
        await client.stop()
    assert told[-1] == (True, "")  # a stop is no failure


async def test_a_watcher_that_fails_never_stops_signal_cli(fake_dir: Path) -> None:
    def broken(ready: bool, failure: str) -> None:
        raise RuntimeError("a bug in a watcher")

    client = make_client()
    client.watch(broken)
    await client.start()
    try:
        assert client.ready
    finally:
        await client.stop()
