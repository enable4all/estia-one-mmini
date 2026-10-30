"""Five dots for her model's download and Ollama's (owner, 27 Oct: "nothing fancy, no %, it is annoying, maybe 5 dots,
lighting up with progress of downloads"): ○○○○○ to ●●●●●, in Terminal and in the setup window, no percent and no number
that ticks. The dots only go forward, and the fifth lights only once the download is done; one that stalls keeps its
dots, and after STUCK_SECONDS without a new dot a line says it is still working (again after STILL_AGAIN_SECONDS).

Her model comes through Ollama's own pull (its /api/pull stream on this Mac: each layer's total and completed bytes),
Ollama's release by its file's size as curl writes it. macOS's own caffeinate keeps the Mac awake for the whole run."""

from __future__ import annotations

import contextlib
import json
import os
import queue
import re
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from email.message import Message
from pathlib import Path
from typing import IO, Protocol

from local_assistant import hostsec, prompts

DOTS = 5
LIT, DIM = "●", "○"
STUCK_SECONDS = 180.0  # no new dot this long: one line says it is still working
STILL_AGAIN_SECONDS = 600.0  # and again after this long, while it stays so
READ_SECONDS = 600.0  # no word at all from Ollama this long: the pull stopped (setup again resumes it)
LINE_MAX = 1 << 16  # one line of Ollama's answer at most (64 KB): a longer one ends the stream
MAX_LAYERS = 8192  # the layers counted at most (gemma4:26b-mlx has 1073): more are left out
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")  # a layer's digest as Ollama names it


class Draws(Protocol):
    """What draws the dots (setup_face's faces): a progress line that takes the place of the one before, and its end."""

    def progress(self, text: str) -> None: ...

    def progress_done(self) -> None: ...


def lit(done: int, total: int) -> int:
    """How many of the five are lit for `done` of `total` bytes."""
    if total <= 0 or done <= 0:
        return 0
    return min(DOTS, DOTS * done // total)


def line(label: str, count: int, *, still: bool = False) -> str:
    """The dots' line: "  her model ●●○○○", and when it stalls "… still working"."""
    dots = LIT * count + DIM * (DOTS - count)
    return f"  {label} {dots}" + (f" {prompts.SETUP_STILL_WORKING}" if still else "")


class Watch:
    """One download's dots, drawn by `face` at each new one; never fewer than before."""

    def __init__(self, face: Draws, label: str, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.face = face
        self.label = label
        self.clock = clock
        self.count = -1  # nothing drawn yet
        self.since = clock()  # the last new byte or dot, or the last "still working"
        self.said_still = False
        self.done = -1  # the bytes so far (round 1's issue 75: a 26B dot takes about 293 s at 100 Mbit/s)

    def _draw(self, count: int) -> None:
        self.count = count
        self.since = self.clock()
        self.said_still = False
        self.face.progress(line(self.label, count))

    def start(self) -> None:
        """○○○○○, once."""
        if self.count < 0:
            self._draw(0)

    def update(self, done: int, total: int) -> None:
        """The bytes so far: a new dot when they reach it (four at most until `finish`); new bytes are news, so the
        wait for "still working" counts from them."""
        count = min(DOTS - 1, lit(done, total))
        if count > self.count:
            self._draw(count)
        if done > self.done:
            self.done = done
            self.since = self.clock()

    def waited(self) -> None:
        """No news: once STUCK_SECONDS passed without a new dot, a line that it is still working, its dots kept; again
        after STILL_AGAIN_SECONDS."""
        heard = getattr(self.face, "heard", None)  # the setup window: its Stop here, read between two looks
        if callable(heard):
            heard()
        wait = STILL_AGAIN_SECONDS if self.said_still else STUCK_SECONDS
        now = self.clock()
        if now - self.since >= wait:
            self.since = now
            self.said_still = True
            self.face.progress(line(self.label, max(self.count, 0), still=True))

    def finish(self) -> None:
        """Done: all five, and the line ends."""
        if self.count < DOTS:
            self._draw(DOTS)
        self.face.progress_done()

    def stop(self) -> None:
        """It did not finish: its dots stay as they are, and the line ends."""
        self.face.progress_done()


Post = Callable[[str, Mapping[str, object]], Iterable[bytes]]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error, never followed: urllib turns a POST it follows into a GET to any host."""

    def redirect_request(
        self, req: urllib.request.Request, fp: IO[bytes], code: int, msg: str, headers: Message, newurl: str
    ) -> urllib.request.Request | None:
        return None


def _post(url: str, payload: Mapping[str, object]) -> Iterator[bytes]:
    """POST JSON to Ollama on this Mac (never through a proxy, never redirected) and its answer line by line, each
    at most LINE_MAX bytes: a longer one ends the stream (ValueError)."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    with opener.open(request, timeout=READ_SECONDS) as answer:
        while raw := answer.readline(LINE_MAX):
            if len(raw) >= LINE_MAX and not raw.endswith(b"\n"):
                raise ValueError(prompts.SETUP_PULL_LINE_LONG)
            yield raw


def _whole(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _shown(text: str) -> str:
    """What Ollama or a broken stream said, on one line, each control, format or separator character a "?"
    (`hostsec.clean`: it reaches your terminal and the window's log), at most 300 characters."""
    return hostsec.clean(" ".join(text.split()))[:300]


def pull(base: str, tag: str, watch: Watch, *, total: int = 0, post: Post | None = None) -> str:
    """A model through Ollama's own pull (`base`/api/pull, streamed: one JSON object a line, each layer's digest, total
    and completed bytes), its dots lit by all the layers' bytes so far against `total`, the model's whole size (Ollama
    names a layer only when its turn comes), or the layers' own sum when that is larger: "" once Ollama says success,
    else why not (Ollama's error, what broke the stream, or that it ended first). The same bytes again count as no
    news (Ollama repeats its line while a layer stalls). Ctrl-C stops it, as at a question."""
    events: queue.Queue[tuple[str, object]] = queue.Queue()
    send = post or _post  # looked up when it runs (the tests' guard replaces it)

    def read() -> None:
        try:
            for raw in send(f"{base}/api/pull", {"model": tag, "stream": True}):
                events.put(("line", raw))
            events.put(("end", None))
        except Exception as exc:  # any end of the stream is said: never a wait without end
            events.put(("error", str(exc) or type(exc).__name__))

    threading.Thread(target=read, name="ollama-pull", daemon=True).start()
    layers: dict[str, tuple[int, int]] = {}
    done_sum = total_sum = 0  # kept as each line comes, never summed again
    watch.start()
    while True:
        try:
            kind, item = events.get(timeout=min(1.0, STUCK_SECONDS))
        except queue.Empty:
            watch.waited()
            continue
        if kind != "line":
            watch.stop()
            return _shown(str(item)) if kind == "error" else prompts.SETUP_PULL_ENDED
        try:
            said = json.loads(item if isinstance(item, bytes) else b"")
        except ValueError:
            continue
        if not isinstance(said, dict):
            continue
        if "error" in said:
            watch.stop()
            return _shown(str(said["error"]))
        if said.get("status") == "success":
            watch.finish()
            return ""
        digest = said.get("digest")
        if (
            isinstance(digest, str)
            and _DIGEST.fullmatch(digest)
            and "total" in said
            and (digest in layers or len(layers) < MAX_LAYERS)
        ):
            old_done, old_total = layers.get(digest, (0, 0))
            layers[digest] = done, size = _whole(said.get("completed")), _whole(said.get("total"))
            done_sum += done - old_done
            total_sum += size - old_total
            before = watch.count
            watch.update(done_sum, max(total, total_sum))
            if watch.count == before:
                watch.waited()
        else:
            watch.waited()


def fetch(argv: Sequence[str], path: Path, total: int, watch: Watch, *, poll: float = 0.5) -> int:
    """A download by a program (curl, its own meter off) into `path`, its dots lit by the file's size against `total`
    bytes (about the file's size: the fifth lights only once the program ended well). The program's exit code, 127
    when it cannot start."""
    try:
        process = subprocess.Popen(list(argv), stdin=subprocess.DEVNULL)
    except OSError:  # missing or not runnable: an exit code, as a change's (review SEC-9)
        watch.start()
        watch.stop()
        return 127
    watch.start()
    try:
        while True:
            try:
                code = process.wait(timeout=poll)
                break
            except subprocess.TimeoutExpired:
                size = path.stat().st_size if path.is_file() else 0
                before = watch.count
                watch.update(size, total)
                if watch.count == before:
                    watch.waited()
    except BaseException:
        process.terminate()
        process.wait()
        watch.stop()
        raise
    if code == 0:
        watch.finish()
    else:
        watch.stop()
    return code


@contextlib.contextmanager
def awake() -> Iterator[subprocess.Popen[bytes] | None]:
    """The Mac kept awake while setup runs (run_setup: the whole run, plat-09): macOS's own caffeinate by its full
    path (-i: no idle sleep; -w: it ends with setup too); None when it does not start, and setup goes on anyway. A
    closed lid without an external display still sleeps the Mac: the screen says to keep it open."""
    tool = hostsec.tool_root() / "usr/bin/caffeinate"
    try:
        held: subprocess.Popen[bytes] | None = subprocess.Popen(
            [str(tool), "-i", "-w", str(os.getpid())],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        held = None
    try:
        yield held
    finally:
        if held is not None:
            held.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                held.wait(timeout=5)
