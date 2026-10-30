"""signal-cli as a supervised child process, spoken to over JSON-RPC on stdin/stdout.

Only this module talks to signal-cli, and it refuses to send anything to an account
outside the allowlist, so a bug or a prompt injection cannot message third parties.
signal-cli's own log lines (stderr) go into the bridge log summed up (`stderr_line`), and an error is logged by its type
and code (`shown_error`): signal-cli's words can name a number, an account or a captcha's challenge token.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import json
import logging
import os
import re
import time
import urllib.parse
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from local_assistant.signal_text import valid

log = logging.getLogger(__name__)

UNTRUSTED_KEY_ERROR = -4  # signal-cli SignalJsonRpcCommandHandler.UNTRUSTED_KEY_ERROR
RATE_LIMIT_ERROR = -5  # its RATELIMIT_ERROR: Signal's server limits her sending for now
CAPTCHA_REJECTED_ERROR = -6  # its CAPTCHA_REJECTED_ERROR: Signal refused the captcha given
RATE_LIMIT_WAIT_S = 10.0  # a rate limit that lifts within this is waited out before the next try; a longer one is not
CAPTCHA_STEPS = "setup_assistant.md, 7. Operations, 'Signal limits her messages'"


class SignalError(Exception):
    """signal-cli reported an error, did not answer, or is not running. Its text is code's own words only: signal-cli's
    own (they can name the recipient's number or account and a captcha's challenge token) are kept in `detail`, never
    in the text a traceback prints (review S1)."""

    def __init__(self, message: str, code: int | None = None, *, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail  # signal-cli's words: never logged, never shown

    def wait(self, pause: float) -> float | None:
        """Seconds to wait before the next try of what failed (`pause`, the caller's own), None for no try now."""
        return pause


class UntrustedIdentityError(SignalError):
    """The recipient's safety number changed and has not been confirmed (signal-cli trust)."""


class RateLimitError(SignalError):
    """Signal's server refused for now (-5): `retry_after` seconds when it said; `proof_required` when it asks for a
    captcha first (a challenge came), lifted by a solved captcha or after that wait: no try now either way."""

    def __init__(
        self,
        message: str,
        code: int | None = None,
        *,
        retry_after: float | None = None,
        proof_required: bool = False,
        detail: str = "",
    ) -> None:
        super().__init__(message, code, detail=detail)
        self.retry_after = retry_after
        self.proof_required = proof_required

    def wait(self, pause: float) -> float | None:
        if self.proof_required or (self.retry_after or 0) > RATE_LIMIT_WAIT_S:
            return None
        return pause if self.retry_after is None else self.retry_after


class CaptchaRejectedError(SignalError):
    """Signal refused the captcha given (-6): a new one is needed, so no try now."""

    def wait(self, pause: float) -> float | None:
        return None


def shown_error(exc: BaseException) -> str:
    """An error for the log: its type and, for signal-cli's, its JSON-RPC code. Never its words: signal-cli's message
    can name the recipient's number or account, and for a captcha request the challenge token (0.14.9: 'CAPTCHA proof
    required for sending to "%s", … with challenge token "%s"'); NotAllowedError's names the recipient."""
    code = exc.code if isinstance(exc, SignalError) else None
    return type(exc).__name__ + (f" (code {code})" if code is not None else "")


# signal-cli's stderr, summed up (review S1): its log lines ("WARN  SendHelper - …", `--scrub-log` does not reach a
# challenge token) and its plain error lines ("User <number> is not registered.") name numbers, accounts and tokens.
# Logged: the level, the class that logged, the first kind below its words match and the exceptions it names.
_LOG_LINE = re.compile(r"^(ERROR|WARN|INFO|DEBUG|TRACE)\s+([A-Za-z0-9_$.]{1,60})\s+-\s+(.*)$")
_FRAME = re.compile(r"^(?:at\s|\.\.\.\s\d+\s(?:more|common))")  # a Java stack frame, "... 12 more"
_EXCEPTION = re.compile(r"\b[A-Z][A-Za-z0-9]{0,60}(?:Exception|Error)\b")
STDERR_KINDS = (  # (kind, its words): the account's own state first
    ("not registered", re.compile(r"\bis not registered\b|\bNotRegisteredException\b", re.IGNORECASE)),
    ("authorization failed", re.compile(r"authori[sz]ation failed|AUTHENTICATION_FAILED", re.IGNORECASE)),
    ("client too old", re.compile(r"DeprecatedVersion|deprecated version|\[499\]", re.IGNORECASE)),
    ("captcha asked", re.compile(r"proof required|ProofRequired|captcha", re.IGNORECASE)),
    ("rate limit", re.compile(r"rate limit|RateLimit", re.IGNORECASE)),
)
ACCOUNT_KINDS = frozenset({"not registered", "authorization failed", "client too old"})  # her account, not a send


def stderr_line(line: str) -> tuple[str, str | None]:
    """One line of signal-cli's stderr as the log keeps it, and its kind (STDERR_KINDS) when its words name one:
    ("signal-cli: WARN SendHelper: captcha asked (ProofRequiredException; 190 characters not logged)", "captcha
    asked"). A stack frame or an empty line: ("", None). Never a word of its message."""
    text = line.strip()
    if not text or _FRAME.match(text):
        return "", None
    found = _LOG_LINE.match(text)
    source = f"{found.group(1)} {found.group(2)}: " if found else ""
    words = found.group(3) if found else text
    kind = next((name for name, pattern in STDERR_KINDS if pattern.search(words)), None)
    exceptions = list(dict.fromkeys(_EXCEPTION.findall(words)))[:3]
    extra = "; ".join([", ".join(exceptions)] if exceptions else [])
    left_out = f"{len(text)} characters not logged"
    return f"signal-cli: {source}{kind or 'a line'} ({extra + '; ' if extra else ''}{left_out})", kind


def rate_limited(message: str, data: object) -> RateLimitError:
    """A -5 answer as its error: the longest `retryAfterSeconds` of the send's results and whether one carries a
    challenge (`token`: Signal wants a captcha), read from the error's data; logged without the token."""
    response = data.get("response") if isinstance(data, dict) else None
    results = response.get("results") if isinstance(response, dict) else None
    rows = [row for row in results if isinstance(row, dict)] if isinstance(results, list) else []
    waits = [float(row["retryAfterSeconds"]) for row in rows if isinstance(row.get("retryAfterSeconds"), int | float)]
    error = RateLimitError(
        "Signal limits her sending",
        RATE_LIMIT_ERROR,
        retry_after=max(waits, default=None),
        proof_required=any(row.get("token") for row in rows),
        detail=message,
    )
    if error.proof_required:  # lifted by a captcha, or after Signal's wait (signal-cli: "or wait N seconds")
        log.error(
            "signal-cli: Signal asks for a captcha before she sends more%s; her messages stop until then (%s)",
            f", or after {error.retry_after:.0f} s" if waits else "",
            CAPTCHA_STEPS,
        )
    else:
        log.warning(
            "signal-cli: Signal limits her sending (rate limit%s)", f", {error.retry_after:.0f} s" if waits else ""
        )
    return error


# One JSON line can carry a whole photo as base64 (20 MB → 26.7 MB), see getAttachment.
READ_LIMIT = 32 * 1024 * 1024


class NotAllowedError(Exception):
    """Refused: the recipient is not on the allowlist."""


IPV4 = "-Djava.net.preferIPv4Stack=true"
# A changed safety number is never trusted by itself: a SIM swap of the user's number looks exactly like one. Pinned
# here, not left to signal-cli's default; setup's own signal-cli calls pass the same.
TRUST_NEW_IDENTITIES = ("--trust-new-identities", "on-first-use")
UNTRUSTED_IDENTITY = "UntrustedIdentityException"  # signal-cli's receive exception for a changed safety number
_PLAIN_WORD = re.compile(r"[^A-Za-z0-9_.]")


@dataclass(frozen=True)
class ReceiveProblem:
    """A message signal-cli received but could not open (its receive `exception`): the exception's type, and who sent
    it from which device when the envelope says so. Never the exception's message: it names the account."""

    kind: str  # e.g. UntrustedIdentityException
    sender: str | None = None  # the sender's ACI, lowercase
    device: int | None = None  # the sender's device (1: their phone)

    @property
    def untrusted_identity(self) -> bool:
        """The sender's safety number changed and has not been confirmed: the message was dropped."""
        return self.kind == UNTRUSTED_IDENTITY


@dataclass(frozen=True)
class LinkedDevice:
    """One device of the bot's own Signal account (listDevices): its id and when it was linked, never its name."""

    id: int
    created: int | None  # epoch milliseconds


def device_number(value: object) -> int | None:
    """A Signal device id (1 or more), or None for anything else."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else None


def signal_cli_command(
    signal_cli: str, bot_number: str, *, jvm: bool = False, config_dir: Path | None = None
) -> list[str]:
    """IPv4 only: chat.signal.org also answers over IPv6, but Signal's captcha is checked against an IPv4 address, so
    registering over IPv6 fails with 403 (signal-cli issue 1788); the flag keeps one network path for every command.
    New identities are trusted on first use only (`TRUST_NEW_IDENTITIES`), pinned in code.
    Manual receive (subscribe once ready); photos are fetched with getAttachment, so attachments are not ignored
    (strangers' files are deleted on receipt by the app).
    The JVM build's launcher would hand a -D argument to signal-cli itself: it gets IPv4 from JAVA_OPTS.
    `config_dir`: signal-cli's data folder, given explicitly (Java takes its home from the account record, which
    macOS keeps at /var/empty for her own user, not from $HOME)."""
    return [
        signal_cli,
        *([] if jvm else [IPV4]),
        "--scrub-log",
        *(["--config", str(config_dir)] if config_dir is not None else []),
        *TRUST_NEW_IDENTITIES,
        "-a",
        bot_number,
        "jsonRpc",
        "--receive-mode",
        "manual",
        "--ignore-stories",
        "--ignore-avatars",
        "--ignore-stickers",
    ]


def signal_data() -> Path:
    """signal-cli's data folder under $HOME, the folder signal-cli uses by default, named on every command of hers
    (the bridge's, `estia signal`, `signal-login`, the Registration Lock): her own user's account record keeps
    /var/empty as home, where Java would look."""
    return Path.home() / ".local" / "share" / "signal-cli"


def jvm_opts(home: Path | None) -> str:
    """JAVA_OPTS for the JVM signal-cli: IPv4, and `home` as Java's user.home (JAVA_OPTS splits on spaces: a home
    with a space stays out; signal-cli's data folder is given with --config anyway)."""
    return IPV4 + (f" -Duser.home={home}" if home is not None and not any(c.isspace() for c in str(home)) else "")


def call_env(*, java_home: Path | None, tunnel: Path, socket_dir: Path, home: Path | None = None) -> dict[str, str]:
    """What the JVM signal-cli and the call helper it starts per call need, on top of the bridge's environment:
    Java (none given: the launcher looks for `java` itself), IPv4, our signal-call-tunnel build in pipe mode
    (audio over a Unix socket in a private folder). `home` becomes Java's user.home (`jvm_opts`)."""
    return {
        **({"JAVA_HOME": str(java_home)} if java_home is not None else {}),
        "JAVA_OPTS": jvm_opts(home),
        "SIGNAL_CALL_TUNNEL_BIN": str(tunnel),
        "SIGNAL_CALL_TUNNEL_AUDIO_MODE": "pipe",
        "SIGNAL_CALL_TUNNEL_SOCKET_DIR": str(socket_dir),
    }


ATTACHMENT_BYTES_PER_SECOND = 200_000  # a send with a file gets 1 s more per 200 KB (review O9: a 25 MB PDF, 185 s)


def attachment_timeout(base: float, size: int) -> float:
    """The time signal-cli gets for a send with a file of `size` bytes: the request limit plus 1 s per 200 KB, so a slow
    upload is not reported failed while signal-cli may still deliver it (a retry would send it twice)."""
    return base + size / ATTACHMENT_BYTES_PER_SECOND


# Put on the call-event stream when signal-cli stops: its calls (and their helpers) are gone.
RESTART_ENDS_CALLS: dict[str, Any] = {"state": "ENDED", "reason": "signal_cli_restarted"}


class SignalClient:
    def __init__(
        self,
        command: Sequence[str],
        allowed: frozenset[str],
        *,
        env: Mapping[str, str] | None = None,
        calls: bool = False,
        request_timeout: float = 60.0,
        backoff_start: float = 1.0,
        backoff_max: float = 60.0,
        stable_after: float = 60.0,
        read_limit: int = READ_LIMIT,
    ) -> None:
        self._command = list(command)
        self._env = dict(env) if env else None
        self._calls = calls
        self._read_limit = read_limit
        self._allowed = frozenset(aci.lower() for aci in allowed)
        self._request_timeout = request_timeout
        self._backoff_start = backoff_start
        self._backoff_max = backoff_max
        self._stable_after = stable_after
        self._envelopes: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._call_events: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._problems: asyncio.Queue[ReceiveProblem] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._next_id = 0
        self._proc: asyncio.subprocess.Process | None = None
        self._ready = asyncio.Event()
        self._supervisor: asyncio.Task[None] | None = None
        self._stopping = False
        self.restarts = 0
        self._watchers: list[Callable[[bool, str], None]] = []
        self._run_failure = ""  # what this run's stderr said of her account (ACCOUNT_KINDS), if anything

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def watch(self, callback: Callable[[bool, str], None]) -> None:
        """`callback(ready, failure)` each time signal-cli is ready (subscribed; failure "") and each time a run of it
        ends while not stopping (failure: what its stderr said of her account, `ACCOUNT_KINDS`, or "exited"; never
        its words). Logged out by Signal it ends at every start, restarted up to every `backoff_max` (review S2)."""
        self._watchers.append(callback)

    def _tell(self, ready: bool, failure: str) -> None:
        for callback in self._watchers:
            try:
                callback(ready, failure)
            except Exception:  # a watcher never stops signal-cli
                log.exception("signal-cli: a watcher failed")

    async def start(self) -> None:
        """Start supervising signal-cli and wait until it is subscribed (TimeoutError if it never is)."""
        self._supervisor = asyncio.create_task(self._supervise(), name="signal-cli")
        await asyncio.wait_for(self._ready.wait(), self._request_timeout)

    async def stop(self) -> None:
        self._stopping = True
        if self._supervisor is not None:
            self._supervisor.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._supervisor
        await self._terminate()

    async def envelopes(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            yield await self._envelopes.get()

    async def problems(self) -> AsyncIterator[ReceiveProblem]:
        """Messages signal-cli received but could not open (a changed safety number, ...): never dropped silently."""
        while True:
            yield await self._problems.get()

    async def list_devices(self) -> tuple[LinkedDevice, ...]:
        """The bot account's own devices (this Mac is 1), through the running signal-cli: ids and creation times."""
        result = await self.request("listDevices", {})
        devices: list[LinkedDevice] = []
        for item in result if isinstance(result, list) else []:
            number = device_number(item.get("id")) if isinstance(item, dict) else None
            if number is None:
                continue
            created = item.get("createdTimestamp")
            created = created if isinstance(created, int) and not isinstance(created, bool) else None
            devices.append(LinkedDevice(number, created))
        return tuple(devices)

    async def call_events(self) -> AsyncIterator[dict[str, Any]]:
        """signal-cli's callEvent results (callId, state, uuid, …), plus RESTART_ENDS_CALLS after each restart."""
        while True:
            yield await self._call_events.get()

    async def accept_call(self, aci: str, call_id: int) -> dict[str, Any]:
        """Answer the ringing call `call_id` from `aci` (the ACI its ringing event named)."""
        self._check_allowed(aci)
        result = await self.request("acceptCall", {"callId": call_id})
        return result if isinstance(result, dict) else {}

    async def hangup_call(self, aci: str, call_id: int) -> None:
        self._check_allowed(aci)
        await self.request("hangupCall", {"callId": call_id})

    async def start_call(self, aci: str) -> dict[str, Any]:
        """Place a Signal call to `aci` (an allowed account only: a wake-up call or a call reminder). Its ringing event
        comes before this reply; the reply names the call id (and may name the call helper's socket)."""
        self._check_allowed(aci)
        result = await self.request("startCall", {"recipient": [aci]})
        return result if isinstance(result, dict) else {}

    async def send(self, aci: str, text: str, *, styles: Sequence[str] = ()) -> int:
        """Send a text, with Signal's text styles when given (signal_text: "start:length:STYLE", UTF-16 units). One
        entry signal-cli would refuse fails its whole send, so then the text goes out without styles."""
        self._check_allowed(aci)
        params: dict[str, Any] = {"recipient": [aci], "message": text}
        if styles and valid(text, styles):
            params["textStyle"] = list(styles)
        elif styles:
            log.warning("send: %d text styles refused (unknown style or out of the text): sent without", len(styles))
        result = await self.request("send", params)
        timestamp = result.get("timestamp") if isinstance(result, dict) else None
        if not isinstance(timestamp, int):
            raise SignalError("send returned no timestamp")
        return timestamp

    async def send_voice_note(self, aci: str, path: Path) -> int:
        """Send an audio file as a Signal voice note (signal-cli reads the file while sending)."""
        self._check_allowed(aci)
        result = await self.request("send", {"recipient": [aci], "attachments": [str(path)], "voiceNote": True})
        timestamp = result.get("timestamp") if isinstance(result, dict) else None
        if not isinstance(timestamp, int):
            raise SignalError("send returned no timestamp")
        return timestamp

    async def send_document(self, aci: str, data: bytes, filename: str, media_type: str = "application/pdf") -> int:
        """Send a document back to the allowed user who asked (/docs send N), as `media_type` (a PDF's, or a text
        file's: documents.media_type): signal-cli 0.14.9's send takes "attachments" as paths or RFC 2397 data links
        with a URL-encoded filename parameter. The bytes go inline, so signal-cli opens no file in the documents folder
        (nothing a link swapped in could redirect)."""
        self._check_allowed(aci)
        name = urllib.parse.quote(filename, safe="")
        link = f"data:{media_type};filename={name};base64,{base64.b64encode(data).decode('ascii')}"
        limit = attachment_timeout(self._request_timeout, len(data))
        result = await self.request("send", {"recipient": [aci], "attachments": [link]}, answer_within=limit)
        timestamp = result.get("timestamp") if isinstance(result, dict) else None
        if not isinstance(timestamp, int):
            raise SignalError("send returned no timestamp")
        return timestamp

    async def update_profile(self, name: str) -> None:
        """The bot's Signal profile name (shown to the user)."""
        await self.request("updateProfile", {"givenName": name, "familyName": ""})

    async def update_avatar(self, path: Path) -> None:
        """The bot's Signal profile picture: signal-cli reads the file (a full path); fields not sent, such as the
        name, stay as they are."""
        await self.request("updateProfile", {"avatar": str(path)})

    async def typing(self, aci: str, *, stop: bool = False) -> None:
        self._check_allowed(aci)
        await self.request("sendTyping", {"recipient": [aci], "stop": stop})

    async def receipt(self, aci: str, timestamp: int) -> None:
        self._check_allowed(aci)
        await self.request("sendReceipt", {"recipient": aci, "targetTimestamp": [timestamp], "type": "read"})

    async def get_attachment(self, aci: str, attachment_id: str) -> bytes:
        """The bytes of an attachment the allowed user sent (signal-cli returns base64)."""
        self._check_allowed(aci)
        result = await self.request("getAttachment", {"id": attachment_id, "recipient": aci})
        data = result.get("data") if isinstance(result, dict) else result
        if not isinstance(data, str):
            raise SignalError("getAttachment returned no data")
        try:
            return base64.b64decode(data, validate=True)
        except binascii.Error as exc:
            raise SignalError("getAttachment returned invalid base64") from exc

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, answer_within: float | None = None
    ) -> Any:
        """One JSON-RPC request; `answer_within`: its answer's limit in seconds, when not the request limit (a send with
        a file)."""
        try:
            await asyncio.wait_for(self._ready.wait(), self._request_timeout)
        except TimeoutError as exc:
            raise SignalError("signal-cli is not running") from exc
        return await self._call(method, params or {}, answer_within=answer_within)

    def _check_allowed(self, aci: str) -> None:
        if aci.lower() not in self._allowed:
            raise NotAllowedError("the recipient is not on the allowlist")  # never named: a traceback prints it

    async def _call(self, method: str, params: dict[str, Any], *, answer_within: float | None = None) -> Any:
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise SignalError("signal-cli is not running")
        self._next_id += 1
        request_id = str(self._next_id)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        line = json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        try:
            proc.stdin.write(line.encode() + b"\n")
            await proc.stdin.drain()
            return await asyncio.wait_for(future, self._request_timeout if answer_within is None else answer_within)
        except TimeoutError as exc:
            raise SignalError(f"signal-cli did not answer {method}") from exc
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise SignalError("signal-cli went away") from exc
        finally:
            self._pending.pop(request_id, None)

    async def _supervise(self) -> None:
        delay = self._backoff_start
        while not self._stopping:
            started = time.monotonic()
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except SignalError as exc:
                log.warning("signal-cli: %s", shown_error(exc))
            except OSError as exc:  # its program could not be started
                self._run_failure = self._run_failure or "not started"
                log.error("signal-cli failed: %s", shown_error(exc))
            except Exception as exc:  # never its traceback's words either
                log.error("signal-cli failed: %s", shown_error(exc))
            self._ready.clear()
            self._fail_pending(SignalError("signal-cli exited"))
            if self._calls:
                self._call_events.put_nowait(dict(RESTART_ENDS_CALLS))
            if self._stopping:
                break
            self._tell(False, self._run_failure or "exited")
            if time.monotonic() - started >= self._stable_after:
                delay = self._backoff_start
            self.restarts += 1
            log.warning("signal-cli exited; restart %d in %.1f s", self.restarts, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._backoff_max)

    async def _run_once(self) -> None:
        self._run_failure = ""
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self._read_limit,
            env={**os.environ, **self._env} if self._env else None,
        )
        reader = asyncio.create_task(self._read(self._proc))
        # When signal-cli's output ends, fail waiting requests at once (e.g. subscribeReceive of a
        # process that exits immediately) instead of letting them run into the request timeout.
        reader.add_done_callback(lambda _task: self._fail_pending(SignalError("signal-cli exited")))
        stderr = asyncio.create_task(self._drain_stderr(self._proc))
        try:
            if self._calls:  # first: signal-cli drops call offers while nobody listens for call events
                try:
                    await self._call("subscribeCallEvents", {})
                except SignalError as exc:
                    log.warning("signal-cli: no call events (%s); calls are off until it restarts", shown_error(exc))
            await self._call("subscribeReceive", {})
            self._ready.set()
            log.info("signal-cli ready (pid %s)", self._proc.pid)
            self._tell(True, "")
            await reader
        finally:
            reader.cancel()
            await self._terminate()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):  # its last lines say why it ended
                await asyncio.wait_for(asyncio.shield(stderr), 1.0)
            stderr.cancel()

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stdout is None:
            return
        while True:
            try:
                line = await proc.stdout.readline()
            except ValueError:  # a line over the limit: dropped (its request times out), signal-cli keeps running
                log.warning("signal-cli: a line over %d bytes was dropped", self._read_limit)
                continue
            if not line:
                break
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                log.warning("signal-cli: unreadable line (%d bytes)", len(line))
                continue
            if isinstance(obj, dict):
                self._dispatch(obj)

    async def _drain_stderr(self, proc: asyncio.subprocess.Process) -> None:
        if proc.stderr is None:
            return
        while line := await proc.stderr.readline():
            text, kind = stderr_line(line.decode(errors="replace"))
            if kind in ACCOUNT_KINDS:
                self._run_failure = kind
            if text:
                log.info("%s", text)

    def _dispatch(self, obj: dict[str, Any]) -> None:
        if "id" in obj and obj.get("method") is None:
            future = self._pending.get(str(obj["id"]))
            if future is None or future.done():
                return
            error = obj.get("error")
            if isinstance(error, dict):
                code = error.get("code")
                code = code if isinstance(code, int) else None
                message = str(error.get("message", "signal-cli error"))
                if code == RATE_LIMIT_ERROR:
                    future.set_exception(rate_limited(message, error.get("data")))
                    return
                typed = {UNTRUSTED_KEY_ERROR: UntrustedIdentityError, CAPTCHA_REJECTED_ERROR: CaptchaRejectedError}
                words = "signal-cli refused the request" + (f" (code {code})" if code is not None else "")
                future.set_exception(typed.get(code or 0, SignalError)(words, code, detail=message))
            else:
                future.set_result(obj.get("result"))
            return
        if obj.get("method") == "callEvent":
            params = obj.get("params")
            event = params.get("result", params) if isinstance(params, dict) else None
            if isinstance(event, dict):
                self._call_events.put_nowait(event)
            return
        if obj.get("method") == "receive":
            params = obj.get("params")
            container = params.get("result", params) if isinstance(params, dict) else None
            envelope = container.get("envelope") if isinstance(container, dict) else None
            exception = container.get("exception") if isinstance(container, dict) else None
            if isinstance(exception, dict):
                self._problems.put_nowait(_problem(exception, envelope if isinstance(envelope, dict) else {}))
            if isinstance(envelope, dict):
                self._envelopes.put_nowait(envelope)

    def _fail_pending(self, error: SignalError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()

    async def _terminate(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), 10)
        except TimeoutError:
            proc.kill()
            await proc.wait()


def _problem(exception: Mapping[str, Any], envelope: Mapping[str, Any]) -> ReceiveProblem:
    """signal-cli 0.14.9's receive exception ({"message", "type"}) and its envelope, as a ReceiveProblem."""
    kind = exception.get("type")
    word = _PLAIN_WORD.sub("", kind)[:60] if isinstance(kind, str) else ""
    sender = envelope.get("sourceUuid")
    return ReceiveProblem(
        word or "unknown",
        sender.lower() if isinstance(sender, str) and sender else None,
        device_number(envelope.get("sourceDevice")),
    )
