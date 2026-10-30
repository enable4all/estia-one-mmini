import asyncio
import contextlib
import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest

from local_assistant.gemma import (
    Completion,
    GemmaError,
    GemmaUnavailableError,
    ModelEntry,
    ModelPausedError,
    ModelPin,
    OllamaGemma,
    PinnedGemma,
    Timing,
    parse_completion,
)


def completion_json(content: str = "ok", tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}],
        "usage": {"prompt_tokens": 71, "completion_tokens": 5},
    }


class FakeOllama(BaseHTTPRequestHandler):
    replies: ClassVar[list[tuple[int, Any]]] = []
    requests: ClassVar[list[dict[str, Any]]] = []
    delay: ClassVar[float] = 0.0
    loaded: ClassVar[list[dict[str, Any]]] = []
    gets: ClassVar[list[str]] = []
    get_status: ClassVar[int] = 200

    def _reply(self, status: int, payload: Any) -> None:
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        with contextlib.suppress(OSError):  # the client may have gone away (cancellation test)
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append({"path": self.path, "body": body})
        time.sleep(FakeOllama.delay)
        status, payload = FakeOllama.replies.pop(0) if FakeOllama.replies else (200, completion_json())
        self._reply(status, payload)

    def do_GET(self) -> None:
        FakeOllama.gets.append(self.path)
        if FakeOllama.get_status != 200:
            self._reply(FakeOllama.get_status, {"error": "fake"})
            return
        self._reply(200, {"models": FakeOllama.loaded})

    def log_message(self, format: str, *args: Any) -> None:
        pass


@pytest.fixture
def base_url() -> Iterator[str]:
    FakeOllama.replies, FakeOllama.requests, FakeOllama.delay, FakeOllama.loaded = [], [], 0.0, []
    FakeOllama.gets, FakeOllama.get_status = [], 200
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    httpd.shutdown()
    httpd.server_close()


class FakeTime:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_chat_returns_text_and_usage(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    result = await gemma.chat([{"role": "user", "content": "hi"}], max_tokens=50)
    assert (result.content, result.prompt_tokens, result.finish_reason) == ("ok", 71, "stop")


async def test_payload_has_effort_tools_and_schema(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    tools = [{"type": "function", "function": {"name": "remember", "parameters": {"type": "object"}}}]
    schema = {"type": "object"}
    await gemma.chat(
        [{"role": "user", "content": "hi"}],
        max_tokens=50,
        tools=tools,
        reasoning_effort="high",
        temperature=0,
        json_schema=schema,
    )
    request = FakeOllama.requests[0]
    assert request["path"] == "/v1/chat/completions"
    body = request["body"]
    assert (body["model"], body["max_tokens"], body["reasoning_effort"], body["temperature"]) == (
        "local-worker",
        50,
        "high",
        0,
    )
    assert body["tools"] == tools
    assert body["response_format"]["json_schema"]["schema"] == schema
    # streamed with usage: Ollama (0.40.4) sends its own prompt and generation times only in a stream's usage chunk
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}


async def test_every_request_sends_googles_top_p(base_url: str) -> None:
    """Without top_p in the request, Ollama's OpenAI layer sends 1.0 (openai/openai.go:769-773, 0.40.4), over the
    model's own 0.95, Google's setting for Gemma 4 (with temperature 1.0 and top_k 64, which stay as they are)."""
    from local_assistant.gemma import TOP_P

    gemma = OllamaGemma(base_url, "local-worker")
    await gemma.chat([{"role": "user", "content": "hi"}], max_tokens=50)
    await gemma.chat([], max_tokens=5, temperature=0.8, json_schema={"type": "object"}, reasoning_effort="high")
    assert TOP_P == 0.95
    assert [request["body"]["top_p"] for request in FakeOllama.requests] == [0.95, 0.95]
    assert "top_k" not in FakeOllama.requests[0]["body"]  # the OpenAI layer has no top_k: the model's 64 applies


async def test_every_request_keeps_the_model_loaded_as_the_service_did(base_url: str) -> None:
    """The Ollama app runs without setup's service file, so its OLLAMA_KEEP_ALIVE (120m) is not there: every request
    names it (Ollama's OpenAI layer takes keep_alive since 0.40.0, openai/openai.go; an older one ignores it)."""
    from local_assistant import ollama_build
    from local_assistant.gemma import KEEP_ALIVE

    gemma = OllamaGemma(base_url, "local-worker")
    await gemma.chat([{"role": "user", "content": "hi"}], max_tokens=50)
    assert KEEP_ALIVE == ollama_build.KEEP_ALIVE == "120m"
    assert FakeOllama.requests[0]["body"]["keep_alive"] == "120m"


def test_completion_tokens_and_the_reasoning_fields_size_are_parsed() -> None:
    """For the empty answers' log (17 Oct): what the model generated, and how much went to a reasoning field (its size
    only: the text is never kept)."""
    body = completion_json("")
    body["choices"][0]["message"]["reasoning"] = "a hidden thought"
    body["choices"][0]["finish_reason"] = "length"
    parsed = parse_completion(body)
    assert (parsed.completion_tokens, parsed.reasoning_chars, parsed.finish_reason) == (5, 16, "length")
    assert parse_completion(completion_json("ok")).reasoning_chars == 0
    assert "hidden" not in repr(parsed)


async def test_tool_calls_are_parsed(base_url: str) -> None:
    call = {"id": "call_1", "type": "function", "function": {"name": "remember", "arguments": '{"fact": "x"}'}}
    FakeOllama.replies = [(200, completion_json("", [call]))]
    result = await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50)
    [tool] = result.tool_calls
    assert (tool.id, tool.name, json.loads(tool.arguments)) == ("call_1", "remember", {"fact": "x"})


async def test_transient_errors_are_retried(base_url: str) -> None:
    FakeOllama.replies = [(503, {"error": "loading"}), (503, {"error": "loading"}), (200, completion_json("hi"))]
    fake = FakeTime()
    gemma = OllamaGemma(base_url, "local-worker", sleep=fake.sleep, clock=fake.clock)
    result = await gemma.chat([], max_tokens=50)
    assert result.content == "hi"
    assert fake.sleeps == [2.0, 4.0]


async def test_gives_up_when_ollama_stays_down() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    fake = FakeTime()
    gemma = OllamaGemma(
        f"http://127.0.0.1:{port}/v1", "local-worker", unreachable_seconds=10, sleep=fake.sleep, clock=fake.clock
    )
    with pytest.raises(GemmaUnavailableError, match="unreachable"):
        await gemma.chat([], max_tokens=50)
    assert fake.sleeps == [2.0, 4.0]


async def test_client_errors_are_not_retried(base_url: str) -> None:
    FakeOllama.replies = [(400, {"error": "bad request"})]
    with pytest.raises(GemmaError, match="HTTP 400"):
        await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50)
    assert len(FakeOllama.requests) == 1


async def test_cancelling_a_call_returns_at_once(base_url: str) -> None:
    FakeOllama.delay = 3.0
    gemma = OllamaGemma(base_url, "local-worker")
    task = asyncio.create_task(gemma.chat([], max_tokens=50))
    await asyncio.sleep(0.3)
    started = time.monotonic()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert time.monotonic() - started < 1.0
    assert len(FakeOllama.requests) == 1


async def test_status(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    assert await gemma.status() == "not loaded (loads with the next message, ~8 s)"
    FakeOllama.loaded = [{"name": "gemma4:26b-mlx"}]
    assert await gemma.status() == "loaded"
    assert await OllamaGemma("http://127.0.0.1:9/v1", "local-worker").status() == "Ollama unreachable"


def test_parse_completion_rejects_unexpected_shapes() -> None:
    with pytest.raises(GemmaError):
        parse_completion({"choices": []})
    dict_args = completion_json("", [{"id": "c", "function": {"name": "forget", "arguments": {"fact": "3"}}}])
    assert json.loads(parse_completion(dict_args).tool_calls[0].arguments) == {"fact": "3"}


# a streamed answer (Ollama's OpenAI layer, 0.40.4: middleware/openai.go): chunks of content, reasoning and tool
# calls, a finish chunk, then with include_usage a chunk with the usage and Ollama's own timings


def event(payload: dict[str, Any] | str) -> bytes:
    return b"data: " + (payload if isinstance(payload, str) else json.dumps(payload)).encode() + b"\n\n"


def delta(finish: str | None = None, **fields: Any) -> dict[str, Any]:
    return {"object": "chat.completion.chunk", "choices": [{"index": 0, "delta": fields, "finish_reason": finish}]}


def usage(prompt: int = 17213, cached: int | None = 4, completion: int = 171, timed: bool = True) -> dict[str, Any]:
    counts: dict[str, Any] = {"prompt_tokens": prompt, "completion_tokens": completion}
    if cached is not None:
        counts["prompt_tokens_details"] = {"cached_tokens": cached}
    chunk: dict[str, Any] = {"object": "chat.completion.chunk", "choices": [], "usage": counts}
    if timed:
        chunk["timings"] = {"prompt_n": prompt, "prompt_ms": 13512.0, "predicted_n": completion, "predicted_ms": 7004.0}
    return chunk


def stream(*chunks: dict[str, Any], done: bool = True) -> bytes:
    return b"".join(event(chunk) for chunk in chunks) + (event("[DONE]") if done else b"")


async def test_a_streamed_answer_is_joined_with_ollamas_counts_and_times(base_url: str) -> None:
    FakeOllama.replies = [
        (
            200,
            stream(
                delta(role="assistant", content="", reasoning="a hidden thought"),
                delta(content="It is "),
                delta(content="sunny."),
                delta("stop"),
                usage(),
            ),
        )
    ]
    result = await OllamaGemma(base_url, "local-worker").chat([{"role": "user", "content": "hi"}], max_tokens=300)
    assert (result.content, result.finish_reason, result.tool_calls) == ("It is sunny.", "stop", ())
    assert (result.prompt_tokens, result.cached_tokens, result.completion_tokens) == (17213, 4, 171)
    assert (result.prompt_ms, result.generation_ms, result.reasoning_chars) == (13512.0, 7004.0, 16)
    assert result.total_ms >= 0 and "hidden" not in repr(result)


async def test_a_streamed_answer_keeps_line_breaks_unicode_has_inside_its_json(base_url: str) -> None:
    """Ollama's encoder (Go) leaves U+0085, U+2028 and U+2029 unescaped inside a chunk's JSON (a web page's garbled
    ellipsis she quotes is enough): only a newline ends an event, so the chunk stays whole and the answer is read."""
    words = "Open today\x85 until 18:00\u2028and\u2029Sunday\x1c\x1d\x1e too\x0b\x0c."
    raw = b"data: " + json.dumps(delta(content=words), ensure_ascii=False).encode() + b"\n\n"
    FakeOllama.replies = [(200, raw + stream(delta("stop"), usage()))]
    result = await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50)
    assert (result.content, result.finish_reason) == (words, "stop")


async def test_a_stream_with_windows_line_ends_is_read_too(base_url: str) -> None:
    body = stream(delta(content="Sunny."), delta("stop"), usage()).replace(b"\n", b"\r\n")
    FakeOllama.replies = [(200, body)]
    result = await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50)
    assert (result.content, result.finish_reason, result.prompt_tokens) == ("Sunny.", "stop", 17213)


async def test_a_streamed_tool_call_is_read_whole_and_in_pieces(base_url: str) -> None:
    whole = {"id": "call_1", "type": "function", "function": {"name": "web_search", "arguments": '{"query": "x"}'}}
    first = {"index": 0, "id": "call_2", "type": "function", "function": {"name": "remember", "arguments": '{"fa'}}
    rest = {"index": 0, "function": {"arguments": 'ct": "y"}'}}
    FakeOllama.replies = [
        (
            200,
            stream(delta(tool_calls=[whole]), delta(tool_calls=[first]), delta(tool_calls=[rest]), delta("tool_calls")),
        )
    ]
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]
    result = await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50, tools=tools)
    assert [(call.id, call.name, json.loads(call.arguments)) for call in result.tool_calls] == [
        ("call_1", "web_search", {"query": "x"}),
        ("call_2", "remember", {"fact": "y"}),
    ]
    assert (result.finish_reason, result.cached_tokens, result.prompt_ms) == ("tool_calls", None, None)  # no usage


async def test_a_streamed_tool_call_without_tools_offered_is_dropped_as_ollama_drops_it_unstreamed(
    base_url: str,
) -> None:
    """Unstreamed, Ollama keeps tool calls only when the request offered tools (server/routes.go, writeChatResponse);
    a stream passes them on: read the same either way."""
    call = {"id": "call_1", "type": "function", "function": {"name": "remember", "arguments": "{}"}}
    FakeOllama.replies = [(200, stream(delta(content="Fine."), delta(tool_calls=[call]), delta("tool_calls")))]
    result = await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50)
    assert (result.content, result.tool_calls, result.finish_reason) == ("Fine.", (), "stop")


DATA = Path(__file__).parent / "data"


async def test_ollamas_own_streams_are_read_with_their_tool_call_counts_and_times(base_url: str) -> None:
    """Two answers captured from the real Ollama (0.40.4, local-worker, 22 Oct; the suite's web_lookup case): her
    web_search call, whole in one chunk with its id and index, and the answer after its result, in 17 pieces."""
    FakeOllama.replies = [
        (200, (DATA / "ollama_stream_tool_call.txt").read_bytes()),
        (200, (DATA / "ollama_stream_answer.txt").read_bytes()),
    ]
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {"type": "object"}}}]
    gemma = OllamaGemma(base_url, "local-worker")
    asked = await gemma.chat([], max_tokens=600, tools=tools)
    [call] = asked.tool_calls
    assert (call.id, call.name, json.loads(call.arguments)) == (
        "call_h40f2cxk",
        "web_search",
        {"query": "Ajax vs PSV score 4 October 2026"},
    )
    assert (asked.content, asked.finish_reason, asked.prompt_tokens, asked.cached_tokens) == ("", "tool_calls", 6720, 5)
    assert (asked.prompt_ms, asked.generation_ms, asked.completion_tokens) == (4487.0, 677.0, 26)
    answer = await gemma.chat([], max_tokens=600, tools=tools)
    assert answer.tool_calls == () and answer.finish_reason == "stop" and answer.content.startswith("Yes,")
    assert (answer.prompt_tokens, answer.cached_tokens, answer.prompt_ms) == (6889, 6716, 197.0)


async def test_a_stream_cut_off_before_its_end_is_tried_again_like_ollama_down(base_url: str) -> None:
    """Unstreamed, an error during generation is an HTTP 500, tried again; a stream has sent its 200 by then and just
    stops (no finish chunk, no [DONE]): the same retry."""
    FakeOllama.replies = [
        (200, stream(delta(content="It is "), done=False)),
        (200, stream(delta(content="Sunny."), delta("stop"), usage())),
    ]
    fake = FakeTime()
    result = await OllamaGemma(base_url, "local-worker", sleep=fake.sleep, clock=fake.clock).chat([], max_tokens=50)
    assert result.content == "Sunny." and fake.sleeps == [2.0] and len(FakeOllama.requests) == 2


async def test_an_error_in_a_stream_is_an_error(base_url: str) -> None:
    FakeOllama.replies = [(200, event({"error": {"message": "model runner stopped", "type": "api_error"}}))]
    with pytest.raises(GemmaError, match="error in its answer"):
        await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50)


async def test_an_unstreamed_answer_is_still_read_with_its_cache_count(base_url: str) -> None:
    """A server that answers unstreamed (an older Ollama, the tests' fakes): its JSON as before; the cache count is in
    its usage, the times are not."""
    body = completion_json("hi")
    body["usage"]["prompt_tokens_details"] = {"cached_tokens": 60}
    FakeOllama.replies = [(200, body)]
    FakeOllama.delay = 0.05
    result = await OllamaGemma(base_url, "local-worker").chat([], max_tokens=50)
    assert (result.content, result.prompt_tokens, result.cached_tokens) == ("hi", 71, 60)
    assert (result.prompt_ms, result.generation_ms) == (None, None)
    assert result.total_ms >= 50  # the request in the bridge, the wait for Ollama included


def test_an_answers_timing_sums_its_requests_and_names_the_rest_as_waiting() -> None:
    """Turn 998 of the owner's call (22 Oct, Ollama's log): 17,213 prompt tokens, 4 of them cached (the prompt cache
    had been taken by another program's requests), 13.5 s to read the prompt, 7 s to generate, 20.6 s in all."""
    first = Completion("", prompt_tokens=17213, cached_tokens=4, completion_tokens=171, prompt_ms=13512.0)
    first = replace(first, generation_ms=7004.0, total_ms=20642.0)
    assert Timing.of([first]).line() == (
        "1 request, 17,213 prompt tokens, 17,209 not cached, prompt eval 13,512 ms, 171 tokens generated in "
        "7,004 ms, total 20,642 ms (126 ms waiting or loading)"
    )
    second = Completion("ok", prompt_tokens=17400, cached_tokens=17300, completion_tokens=20, total_ms=900.0)
    two = Timing.of([first, second])
    assert two.line() == (
        "2 requests, 17,400 prompt tokens, 17,309 not cached, 191 tokens generated, total 21,542 ms"
    )  # the second had no times: no prompt eval or generation time is claimed for the answer
    assert Timing.of([Completion("ok", prompt_tokens=5, total_ms=10.0)]).line() == (
        "1 request, 5 prompt tokens, 0 tokens generated, total 10 ms"
    )
    assert Timing.of([]).line() == "no request"


# hardening: the model alias must keep the digest pinned in the config, else answers pause

DIGEST = "ab" * 32
OTHER = "cd" * 32


async def test_the_aliass_digest_comes_from_the_tags_list(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    FakeOllama.loaded = [
        {"name": "gemma4:26b", "model": "gemma4:26b", "digest": OTHER},
        {"name": "local-worker:latest", "model": "local-worker:latest", "digest": DIGEST.upper()},
    ]
    assert await gemma.model_digest() == DIGEST
    assert FakeOllama.gets[-1] == "/api/tags"
    FakeOllama.loaded = [{"name": "local-worker:v2", "digest": DIGEST}]  # another tag of the name is not the alias
    assert await gemma.model_digest() is None
    tagged = OllamaGemma(base_url, "local-worker:v2")
    assert await tagged.model_digest() == DIGEST
    FakeOllama.loaded = [{"name": "local-worker:latest", "digest": f"sha256:{OTHER}"}]
    assert await gemma.model_digest() == OTHER


async def test_the_tags_entry_says_whether_the_model_is_a_cloud_one(base_url: str) -> None:
    """Ollama marks a model that runs on its servers with remote_host and remote_model (0.40.0)."""
    gemma = OllamaGemma(base_url, "local-worker")
    FakeOllama.loaded = [{"name": "local-worker:latest", "digest": DIGEST}]
    assert await gemma.model_entry() == ModelEntry(DIGEST, remote=False)
    cloud = {"remote_model": "gpt-oss:120b", "remote_host": "https://ollama.com:443"}
    FakeOllama.loaded = [{"name": "local-worker:latest", "digest": OTHER, **cloud}]
    assert await gemma.model_entry() == ModelEntry(OTHER, remote=True)
    FakeOllama.loaded = [{"name": "local-worker:latest", "digest": OTHER, "remote_host": "https://ollama.com:443"}]
    assert (await gemma.model_entry()) == ModelEntry(OTHER, remote=True)
    FakeOllama.loaded = []
    assert await gemma.model_entry() is None


async def test_a_tags_list_that_cannot_be_read_is_an_error(base_url: str) -> None:
    gemma = OllamaGemma(base_url, "local-worker")
    FakeOllama.get_status = 500
    with pytest.raises(GemmaError):
        await gemma.model_digest()
    with pytest.raises(GemmaError):
        await OllamaGemma("http://127.0.0.1:9/v1", "local-worker").model_digest()


class Digests:
    def __init__(self, *answers: str | ModelEntry | Exception | None) -> None:
        self.answers = list(answers)
        self.reads = 0

    async def __call__(self) -> str | ModelEntry | None:
        self.reads += 1
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


async def test_the_pin_pauses_on_a_mismatch_and_resumes_on_a_match() -> None:
    changes: list[bool] = []
    digests = Digests(DIGEST, OTHER, None, GemmaError("down"), DIGEST)
    pin = ModelPin(digests, DIGEST, on_change=changes.append)
    assert await pin.check() is True and not pin.paused
    assert await pin.check() is False and pin.paused  # another model under the alias
    assert await pin.check() is False and pin.paused  # the alias is gone: still no match
    assert await pin.check() is None and pin.paused  # Ollama silent: nothing known, the pause stays
    assert await pin.check() is True and not pin.paused
    assert changes == [True, False]  # told once each way


async def test_the_entry_is_read_before_every_chat() -> None:
    digests = Digests(DIGEST)
    pin = ModelPin(digests, DIGEST)
    assert await pin.ensure() and await pin.ensure() and await pin.ensure()
    assert digests.reads == 3


async def test_a_name_switched_to_a_cloud_model_gets_not_even_the_next_chat() -> None:
    """No minute's grace: the alias turned into a cloud model one second after a chat, the next chat is held."""
    inner = Inner()
    entries = Digests(ModelEntry(DIGEST, remote=False), ModelEntry(DIGEST, remote=True))
    gemma = PinnedGemma(inner, ModelPin(entries, ""))
    await gemma.chat([], max_tokens=5)
    with pytest.raises(ModelPausedError, match="cloud"):
        await gemma.chat([], max_tokens=5)
    assert inner.chats == 1


async def test_an_unreadable_entry_is_read_again_at_the_next_chat() -> None:
    entries = Digests(GemmaError("down"), ModelEntry(DIGEST, remote=True))
    pin = ModelPin(entries, "")
    assert await pin.ensure() is True  # nothing known: the chat itself fails while Ollama is down
    assert await pin.ensure() is False and pin.why == "cloud" and entries.reads == 2


async def test_an_unknown_digest_at_the_start_does_not_pause() -> None:
    pin = ModelPin(Digests(GemmaError("down")), DIGEST)
    assert await pin.ensure() is True and not pin.paused  # the chat itself will fail if Ollama is down


async def test_a_cloud_model_pauses_answers_with_or_without_a_pinned_digest() -> None:
    """The model always runs on the Mac: a cloud entry pauses everything, also on an install without model_digest."""
    changes: list[bool] = []
    local, cloud = ModelEntry(DIGEST, remote=False), ModelEntry(DIGEST, remote=True)
    pin = ModelPin(Digests(local, cloud, None, local), "", on_change=changes.append)
    assert await pin.check() is True and not pin.paused and pin.why == ""
    assert await pin.check() is False and pin.paused and pin.why == "cloud"
    assert await pin.check() is True and not pin.paused  # no pin and no such model: the chat itself fails
    assert await pin.check() is True and not pin.paused
    assert changes == [True, False]
    pinned = ModelPin(Digests(cloud), DIGEST)
    assert await pinned.check() is False and pinned.why == "cloud"  # even with the pinned digest


class Inner:
    def __init__(self) -> None:
        self.chats = 0

    async def chat(self, messages: list[dict[str, Any]], *, max_tokens: int, **_: Any) -> Completion:
        self.chats += 1
        return Completion("hello")

    async def status(self) -> str:
        return "loaded"


async def test_a_paused_model_gets_no_message() -> None:
    inner = Inner()
    pin = ModelPin(Digests(DIGEST, OTHER), DIGEST)
    gemma = PinnedGemma(inner, pin)
    assert (await gemma.chat([], max_tokens=5)).content == "hello" and await gemma.status() == "loaded"
    with pytest.raises(ModelPausedError):
        await gemma.chat([], max_tokens=5)
    assert inner.chats == 1
    assert "paused" in await gemma.status()


async def test_a_cloud_model_gets_no_message() -> None:
    inner = Inner()
    gemma = PinnedGemma(inner, ModelPin(Digests(ModelEntry(DIGEST, remote=True)), ""))
    with pytest.raises(ModelPausedError, match="cloud"):
        await gemma.chat([], max_tokens=5)
    assert inner.chats == 0 and "cloud" in await gemma.status()
