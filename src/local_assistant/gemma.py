"""Gemma through Ollama's OpenAI-compatible API (standard-library HTTP, cancellable).

Cancelling a call shuts its socket down, so Ollama stops generating and frees the single
model slot: needed when a background learning pass is aborted because a message came in.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.client
import json
import logging
import socket
import threading
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol
from urllib.parse import urlsplit

log = logging.getLogger(__name__)
_TRANSIENT = frozenset({500, 502, 503, 504})
# Google's sampling for Gemma 4 is temperature 1.0, top_k 64, top_p 0.95. Ollama's OpenAI layer sends top_p 1.0 when a
# request names none (openai/openai.go, 0.40.4), over the model's own 0.95, so every request names it. It has no top_k
# field: the model's 64 applies.
TOP_P = 0.95
# How long the model stays loaded after a request: every request names it (Ollama's OpenAI layer takes keep_alive since
# 0.40.0, openai/openai.go; an older one ignores it), so it holds on the Ollama app too, which has no service file of
# setup's to set OLLAMA_KEEP_ALIVE (ollama_build.KEEP_ALIVE is this value)
KEEP_ALIVE = "120m"


class GemmaError(Exception):
    """Gemma answered with an error, garbage, or not in time."""

    # what the answer had already done when it failed, in code's words (Brain.answer: her profile changes and the
    # reminders set; review I25), and the ones of them that quote outside text
    notes: tuple[str, ...] = ()
    outside: tuple[str, ...] = ()


class GemmaUnavailableError(GemmaError):
    """Ollama could not be reached within the retry window."""


class LockedError(GemmaError):
    """/lock came while an answer was worked out: no request and no tool call more in it (Brain's `stopped`)."""


class ModelPausedError(GemmaError):
    """Answers are paused: the model alias no longer has the digest pinned in the config (model_digest), or Ollama
    marks it as a cloud model (it runs on Ollama's servers; the model runs only on this Mac)."""


@dataclass(frozen=True)
class ModelEntry:
    """The alias's entry in Ollama's list (/api/tags): its digest, and whether it is a cloud model."""

    digest: str  # 64 lowercase hex digits
    remote: bool = False  # remote_host or remote_model is set: it runs on Ollama's servers, not on this Mac


def is_remote(item: dict[str, Any]) -> bool:
    """Ollama (0.40.0) names a cloud model's server in remote_host and its model there in remote_model."""
    return bool(item.get("remote_host") or item.get("remote_model"))


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class Completion:
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    prompt_tokens: int = 0
    finish_reason: str = ""
    completion_tokens: int = 0  # what the model generated, hidden thought included
    reasoning_chars: int = 0  # the size of a reasoning field Ollama sent (thinking on); its text is never kept
    cached_tokens: int | None = None  # the prompt's tokens Ollama took from its prompt cache; None: not reported
    prompt_ms: float | None = None  # Ollama's time reading the prompt (a stream's timings); None: not reported
    generation_ms: float | None = None  # Ollama's time generating; None: not reported
    total_ms: float = 0.0  # the request as the bridge saw it: a wait for Ollama's one slot and a model load included


@dataclass(frozen=True)
class Timing:
    """An answer's requests to Ollama in numbers, for its log line (metadata only, never a word): Ollama's own counts
    and times, and the bridge's wall time, so a slow answer names its cause (a prompt read anew after another
    program's requests took the prompt cache, a long generation, or a wait for Ollama's one slot or a model load)."""

    requests: int = 0
    prompt_tokens: int = 0  # the largest request's prompt
    uncached: int | None = None  # prompt tokens read anew, all requests together; None: Ollama reported no cache count
    prompt_ms: float | None = None  # None unless Ollama timed every request
    generated: int = 0
    generation_ms: float | None = None
    total_ms: float = 0.0
    # the last request's finish reason: "length" when the answer was cut at its token limit (review of 26 Oct,
    # rtcall-10: a call says a cut answer up to its last whole sentence, and only a cut one)
    finish_reason: str = ""

    @classmethod
    def of(cls, completions: Sequence[Completion]) -> Timing:
        def summed(values: list[float | None]) -> float | None:
            return None if any(value is None for value in values) else sum(value or 0.0 for value in values)

        uncached = summed([None if c.cached_tokens is None else c.prompt_tokens - c.cached_tokens for c in completions])
        return cls(
            requests=len(completions),
            prompt_tokens=max((c.prompt_tokens for c in completions), default=0),
            uncached=None if uncached is None else int(uncached),
            prompt_ms=summed([c.prompt_ms for c in completions]),
            generated=sum(c.completion_tokens for c in completions),
            generation_ms=summed([c.generation_ms for c in completions]),
            total_ms=sum(c.total_ms for c in completions),
            finish_reason=completions[-1].finish_reason if completions else "",
        )

    def line(self) -> str:
        """As logged: "1 request, 17,213 prompt tokens, 17,209 not cached, prompt eval 13,512 ms, 171 tokens generated
        in 7,004 ms, total 20,642 ms (126 ms waiting or loading)", each part only when Ollama reported it."""
        if not self.requests:
            return "no request"
        parts = [f"{self.requests} request{'s' if self.requests > 1 else ''}", f"{self.prompt_tokens:,} prompt tokens"]
        if self.uncached is not None:
            parts.append(f"{self.uncached:,} not cached")
        if self.prompt_ms is not None:
            parts.append(f"prompt eval {self.prompt_ms:,.0f} ms")
        made = f"{self.generated:,} tokens generated"
        parts.append(made if self.generation_ms is None else f"{made} in {self.generation_ms:,.0f} ms")
        total = f"total {self.total_ms:,.0f} ms"
        if self.prompt_ms is not None and self.generation_ms is not None:
            rest = max(0.0, self.total_ms - self.prompt_ms - self.generation_ms)
            total += f" ({rest:,.0f} ms waiting or loading)"
        cut = ["cut at the token limit"] if self.finish_reason == "length" else []
        return ", ".join([*parts, total, *cut])


class GemmaClient(Protocol):
    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str = "none",
        temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion: ...

    async def status(self) -> str: ...


def parse_completion(body: dict[str, Any]) -> Completion:
    try:
        choice = body["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GemmaError("unexpected response from Ollama") from exc
    calls: list[ToolCall] = []
    for raw in message.get("tool_calls") or []:
        function = raw.get("function") or {}
        arguments = function.get("arguments", "{}")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments)
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{len(calls)}"), name=str(function.get("name", "")), arguments=arguments
            )
        )
    usage = body.get("usage") or {}
    return Completion(
        content=str(message.get("content") or ""),
        tool_calls=tuple(calls),
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        finish_reason=str(choice.get("finish_reason") or ""),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        reasoning_chars=len(str(message.get("reasoning") or "")),
        **_counted(usage, body.get("timings")),
    )


def _counted(usage: Any, timings: Any) -> dict[str, Any]:
    """Ollama's cache count (usage's prompt_tokens_details) and its times (a stream's timings), where it sent them."""
    details = usage.get("prompt_tokens_details") if isinstance(usage, dict) else None
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    times = timings if isinstance(timings, dict) else {}

    def ms(key: str) -> float | None:
        value = times.get(key)
        return float(value) if isinstance(value, int | float) else None

    return {
        "cached_tokens": int(cached) if isinstance(cached, int | float) else None,
        "prompt_ms": ms("prompt_ms"),
        "generation_ms": ms("predicted_ms"),
    }


def _events(body: bytes) -> list[str]:
    """A streamed answer's events ("data: ..." lines), in order; [] for an answer that is not a stream. Split on the
    newline bytes only: Ollama leaves U+0085 and U+2028 unescaped inside a chunk's JSON, which str.splitlines would
    split."""
    lines = (raw.rstrip(b"\r").decode("utf-8", errors="replace") for raw in body.split(b"\n"))
    return [line[5:].strip() for line in lines if line.startswith("data:")]


def stream_cut_off(body: bytes) -> bool:
    """A stream that stopped before its end ([DONE]) without an error of its own: Ollama's 200 was sent before a
    failure during generation, which unstreamed is an HTTP 500 (tried again). A stream with an error is an error."""
    events = _events(body)
    return bool(events) and "[DONE]" not in events and not any(_is_error(data) for data in events)


def _is_error(data: str) -> bool:
    try:
        chunk = json.loads(data)
    except json.JSONDecodeError:
        return False
    return isinstance(chunk, dict) and bool(chunk.get("error"))


def parse_stream(body: bytes, *, tools: bool) -> Completion:
    """A streamed answer (Ollama's OpenAI layer, `stream_options.include_usage`) joined into one Completion: its
    content, the size of its reasoning, its tool calls (a call in pieces joined; none when no tools were offered, as
    Ollama answers unstreamed), the finish reason, and the last chunk's usage and timings."""
    content: list[str] = []
    reasoning = 0
    calls: list[list[str]] = []  # id, name, arguments
    finish = ""
    usage: Any = {}
    timings: Any = None
    for data in _events(body):
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError as exc:
            raise GemmaError("Ollama sent invalid JSON") from exc
        if not isinstance(chunk, dict):
            raise GemmaError("unexpected response from Ollama")
        if chunk.get("error"):
            raise GemmaError("Ollama sent an error in its answer")
        usage = chunk.get("usage") or usage
        timings = chunk.get("timings") or timings
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            content.append(str(delta.get("content") or ""))
            reasoning += len(str(delta.get("reasoning") or ""))
            for raw in delta.get("tool_calls") or []:
                function = raw.get("function") or {}
                arguments = function.get("arguments", "")
                arguments = arguments if isinstance(arguments, str) else json.dumps(arguments)
                if calls and not raw.get("id") and not function.get("name"):
                    calls[-1][2] += arguments  # the rest of the call before
                else:
                    calls.append([str(raw.get("id") or f"call_{len(calls)}"), str(function.get("name", "")), arguments])
            finish = str(choice.get("finish_reason") or finish)
    if not tools and finish == "tool_calls":  # unstreamed, that answer's tool calls are dropped and it says "stop"
        finish = "stop"
    return Completion(
        content="".join(content),
        tool_calls=tuple(ToolCall(call_id, name, args or "{}") for call_id, name, args in calls) if tools else (),
        prompt_tokens=int(usage.get("prompt_tokens") or 0) if isinstance(usage, dict) else 0,
        finish_reason=finish,
        completion_tokens=int(usage.get("completion_tokens") or 0) if isinstance(usage, dict) else 0,
        reasoning_chars=reasoning,
        **_counted(usage, timings),
    )


class _Call:
    """One HTTP request in a worker thread that the event loop can abort."""

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.conn = http.client.HTTPConnection(host, port, timeout=timeout)
        self.cancelled = threading.Event()

    def run(self, method: str, path: str, body: bytes | None) -> tuple[int, bytes]:
        self.conn.connect()
        if self.cancelled.is_set():
            raise ConnectionAbortedError("cancelled")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        self.conn.request(method, path, body=body, headers=headers)
        response = self.conn.getresponse()
        return response.status, response.read()

    def abort(self) -> None:
        self.cancelled.set()
        sock = self.conn.sock
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.shutdown(socket.SHUT_RDWR)


class OllamaGemma:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        unreachable_seconds: float = 120,
        request_timeout: float = 600,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        parts = urlsplit(base_url)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError("base_url must look like http://127.0.0.1:11434/v1")
        self._host = parts.hostname
        self._port = parts.port or 80
        self._prefix = parts.path.rstrip("/")
        self._model = model
        self._unreachable = unreachable_seconds
        self._timeout = request_timeout
        self._sleep = sleep
        self._clock = clock

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str = "none",
        temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        # streamed, with its usage: Ollama (0.40.4) sends its prompt and generation times only in a stream's last
        # chunk (middleware/openai.go); the answer is read whole all the same
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "reasoning_effort": reasoning_effort,
            "top_p": TOP_P,
            "keep_alive": KEEP_ALIVE,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = tools
        if temperature is not None:
            payload["temperature"] = temperature
        if json_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": json_schema, "strict": True},
            }
        started = self._clock()
        status, body = await self._with_retries("POST", f"{self._prefix}/chat/completions", payload)
        if status != 200:
            raise GemmaError(f"Ollama HTTP {status}: {body[:200].decode(errors='replace')}")
        if _events(body):
            completion = parse_stream(body, tools=bool(tools))
        else:  # an answer that is not a stream (an older Ollama): read as before
            try:
                data = json.loads(body)
            except json.JSONDecodeError as exc:
                raise GemmaError("Ollama sent invalid JSON") from exc
            completion = parse_completion(data)
        return replace(completion, total_ms=(self._clock() - started) * 1000)

    async def status(self) -> str:
        root = self._prefix.removesuffix("/v1")
        try:
            status, body = await self._request("GET", f"{root}/api/ps", None, 5.0)
        except (OSError, http.client.HTTPException):
            return "Ollama unreachable"
        if status != 200:
            return f"Ollama HTTP {status}"
        try:
            models = json.loads(body).get("models") or []
        except (json.JSONDecodeError, AttributeError):
            return "Ollama sent invalid JSON"
        return "loaded" if models else "not loaded (loads with the next message, ~8 s)"

    async def model_entry(self) -> ModelEntry | None:
        """This model's entry in Ollama's list (/api/tags; a name without a tag is ":latest"): its digest as 64
        lowercase hex digits, and whether it is a cloud model; None when Ollama lists no such model. GemmaError when
        Ollama does not answer properly."""
        root = self._prefix.removesuffix("/v1")
        try:
            status, body = await self._request("GET", f"{root}/api/tags", None, 5.0)
        except (OSError, http.client.HTTPException) as exc:
            raise GemmaError(f"Ollama unreachable ({type(exc).__name__})") from exc
        if status != 200:
            raise GemmaError(f"Ollama HTTP {status}")
        try:
            models = json.loads(body).get("models") or []
        except (json.JSONDecodeError, AttributeError) as exc:
            raise GemmaError("Ollama sent invalid JSON") from exc
        wanted = self._model if ":" in self._model else f"{self._model}:latest"
        for item in models if isinstance(models, list) else []:
            if isinstance(item, dict) and item.get("name") == wanted:
                digest = str(item.get("digest") or "").strip().lower().removeprefix("sha256:")
                return ModelEntry(digest, remote=is_remote(item))
        return None

    async def model_digest(self) -> str | None:
        """The digest Ollama lists for this model's name; None when Ollama lists no such model."""
        entry = await self.model_entry()
        return None if entry is None else entry.digest

    async def _with_retries(self, method: str, path: str, payload: dict[str, Any]) -> tuple[int, bytes]:
        deadline = self._clock() + self._unreachable
        delay = 2.0
        while True:
            try:
                status, body = await self._request(method, path, payload, self._timeout)
            except TimeoutError as exc:
                raise GemmaError(f"no answer within {self._timeout:.0f} s") from exc
            except (OSError, http.client.HTTPException) as exc:
                problem = str(exc) or type(exc).__name__
            else:
                if status not in _TRANSIENT and not (status == 200 and stream_cut_off(body)):
                    return status, body
                problem = f"HTTP {status}" if status != 200 else "the answer stopped before its end"
            if self._clock() + delay > deadline:
                raise GemmaUnavailableError(f"Ollama unreachable for {self._unreachable:.0f} s ({problem})")
            log.warning("Ollama not available (%s); retrying in %.0f s", problem, delay)
            await self._sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _request(
        self, method: str, path: str, payload: dict[str, Any] | None, seconds: float
    ) -> tuple[int, bytes]:
        call = _Call(self._host, self._port, seconds)
        body = json.dumps(payload).encode() if payload is not None else None
        try:
            return await asyncio.to_thread(call.run, method, path, body)
        except asyncio.CancelledError:
            call.abort()
            raise
        finally:
            with contextlib.suppress(Exception):
                call.conn.close()


class ModelPin:
    """The model alias against Ollama's own entry for it, read before every chat (one local request): a cloud model
    (it runs on Ollama's servers; the model runs only on this Mac), or with `expected` (model_digest) any other model
    under that name, pauses every Gemma call (answers, learning, mail sorting, her own messages) until it runs here
    with the pinned digest again. An answer that cannot be read (Ollama silent) changes nothing; at the start that
    means no pause (the chat itself fails while Ollama is down), and the next chat reads it again. `read` gives the
    entry (a bare digest counts as a model on this Mac)."""

    def __init__(
        self,
        read: Callable[[], Awaitable[ModelEntry | str | None]],
        expected: str,
        *,
        on_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._read = read
        self.expected = expected
        self._on_change = on_change
        self.why = ""  # "": answers go out; "cloud": a cloud model; "digest": not the pinned digest

    @property
    def paused(self) -> bool:
        return bool(self.why)

    def on_change(self, listener: Callable[[bool], None]) -> None:
        """Told once each time answers pause (True), resume (False), or pause for another reason (True again)."""
        self._on_change = listener

    async def check(self) -> bool | None:
        """Read the entry now: True when answers may go out, False when they are paused, None when it cannot be
        read (the state stays)."""
        try:
            found = await self._read()
        except GemmaError as exc:
            log.warning("model pin: the model's entry could not be read (%s)", type(exc).__name__)
            return None
        entry = ModelEntry(found) if isinstance(found, str) else found
        why = "cloud" if entry is not None and entry.remote else ""
        if not why and self.expected and (entry is None or entry.digest != self.expected):
            why = "digest"
        if why != self.why:
            self.why = why
            if why == "cloud":
                log.error("model pin: Ollama lists the model as a cloud model (not on this Mac); answers are paused")
            elif why == "digest":
                log.error("model pin: the model's digest does not match the pin; answers are paused")
            else:
                log.warning("model pin: the model runs on this Mac with its pinned digest again; answers resume")
            if self._on_change is not None:
                self._on_change(self.paused)
        return not why

    async def ensure(self) -> bool:
        """Before every chat: read the entry again (no grace period: a name switched to a cloud model gets not even
        the next chat); False while paused."""
        await self.check()
        return not self.paused


class PinnedGemma:
    """A GemmaClient that sends nothing to the model while its pin says paused (ModelPausedError instead)."""

    def __init__(self, inner: GemmaClient, pin: ModelPin) -> None:
        self._inner = inner
        self.pin = pin

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        reasoning_effort: str = "none",
        temperature: float | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        if not await self.pin.ensure():
            raise ModelPausedError(f"answers are paused: {self.paused_reason()}")
        return await self._inner.chat(
            messages,
            max_tokens=max_tokens,
            tools=tools,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
            json_schema=json_schema,
        )

    def paused_reason(self) -> str:
        if self.pin.why == "cloud":
            return "the model is an Ollama cloud model (it runs on Ollama's servers, not on this Mac)"
        return "the model does not match its pinned digest"

    async def status(self) -> str:
        if self.pin.paused:
            return f"paused: {self.paused_reason()}"
        return await self._inner.status()
