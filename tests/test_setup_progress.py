"""Five dots for each download setup makes (setup_progress; owner, 27 Oct: "nothing fancy, no %, it is annoying, maybe 5
dots, lighting up with progress of downloads"): ○○○○○ to ●●●●●, only forward, no percent and no number that ticks; a
download that stalls keeps its dots, and after a while a line says it is still working."""

import http.server
import json
import re
import shlex
import sys
import threading
import urllib.error
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import ClassVar

import pytest

from local_assistant import prompts, setup_progress
from local_assistant.setup_progress import Watch

pytestmark = pytest.mark.usefixtures("no_system_paths")

REAL_POST = setup_progress._post  # before conftest's guard replaces it: only ever pointed at a test's own server


def layer(name: str) -> str:
    """A layer's digest, in the form Ollama gives it."""
    return "sha256:" + (name * 64)[:64]


class Lines:
    """A face's progress, as it draws it: every line, and where each progress ended."""

    def __init__(self) -> None:
        self.lines: list[str] = []
        self.ended = 0

    def progress(self, text: str) -> None:
        self.lines.append(text)

    def progress_done(self) -> None:
        self.ended += 1


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def lit(line: str) -> int:
    return line.count(setup_progress.LIT)


def test_five_dots_light_up_with_the_bytes_and_never_go_back() -> None:
    face, clock = Lines(), Clock()
    watch = Watch(face, "her model", clock=clock)
    watch.start()
    for done, total in ((0, 100), (19, 100), (20, 100), (55, 100), (30, 100), (61, 200), (99, 100), (100, 100)):
        watch.update(done, total)
    watch.finish()
    assert face.lines == [
        "  her model ○○○○○",
        "  her model ●○○○○",
        "  her model ●●○○○",  # then 30 of 100, and 61 of 200: the dots stay
        "  her model ●●●●○",  # 99 and 100 of 100: the fifth only once it is done
        "  her model ●●●●●",
    ]
    assert face.ended == 1
    for line in face.lines:  # no percent, no number that ticks
        assert "%" not in line and not re.search(r"\d", line)


def test_a_download_that_stalls_keeps_its_dots_and_says_it_is_still_working() -> None:
    face, clock = Lines(), Clock()
    watch = Watch(face, "her model", clock=clock)
    watch.start()
    watch.update(45, 100)
    clock.now = setup_progress.STUCK_SECONDS - 1
    watch.waited()
    assert face.lines[-1] == "  her model ●●○○○"
    clock.now = setup_progress.STUCK_SECONDS + 1
    watch.waited()
    assert face.lines[-1] == f"  her model ●●○○○ {prompts.SETUP_STILL_WORKING}"
    clock.now += setup_progress.STUCK_SECONDS + 1
    watch.waited()  # said once, then again only after a longer while
    assert lit(face.lines[-1]) == 2 and len(face.lines) == 3
    clock.now += setup_progress.STILL_AGAIN_SECONDS
    watch.waited()
    assert len(face.lines) == 4 and face.lines[-1].endswith(prompts.SETUP_STILL_WORKING)


def test_bytes_still_coming_between_two_dots_are_never_called_still_working() -> None:
    """Round 1's issue 75: a dot of the 26B download takes about 293 s at 100 Mbit/s, so "still working" showed
    between two healthy dots. The wait counts from the last new byte, not the last dot."""
    face, clock = Lines(), Clock()
    watch = Watch(face, "her model", clock=clock)
    watch.start()
    for second in range(0, 300, 10):  # 300 s of steady bytes, all within the first dot
        clock.now = float(second)
        watch.update(second, 1500)
        watch.waited()
    assert not any(prompts.SETUP_STILL_WORKING in line for line in face.lines)
    clock.now += setup_progress.STUCK_SECONDS + 1  # then nothing new: said
    watch.update(290, 1500)
    watch.waited()
    assert face.lines[-1].endswith(prompts.SETUP_STILL_WORKING)


def stream(*objects: object, error: Exception | None = None) -> Iterator[bytes]:
    for item in objects:
        yield (json.dumps(item) + "\n").encode()
    if error is not None:
        raise error


def test_her_model_comes_through_ollamas_own_pull_with_its_layers_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, dict[str, object]]] = []

    def post(url: str, payload: Mapping[str, object]) -> Iterator[bytes]:
        asked.append((url, dict(payload)))
        return stream(
            {"status": "pulling manifest"},
            {"status": "pulling a", "digest": layer("a"), "total": 1000, "completed": 0},
            {"status": "pulling a", "digest": layer("a"), "total": 1000, "completed": 500},
            {"status": "pulling b", "digest": layer("b"), "total": 10, "completed": 10},
            {"status": "pulling a", "digest": layer("a"), "total": 1000, "completed": 1000},
            {"status": "verifying sha256 digest"},
            {"status": "success"},
        )

    face = Lines()
    assert setup_progress.pull("http://127.0.0.1:11434", "gemma4:26b-mlx", Watch(face, "her model"), post=post) == ""
    assert asked == [("http://127.0.0.1:11434/api/pull", {"model": "gemma4:26b-mlx", "stream": True})]
    assert [lit(line) for line in face.lines] == [0, 2, 4, 5] and face.ended == 1


def test_a_pull_ollama_refuses_or_that_breaks_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    face = Lines()
    refused = setup_progress.pull(
        "http://x", "t", Watch(face, "her model"), post=lambda url, payload: stream({"error": "file does not exist"})
    )
    assert refused == "file does not exist" and face.ended == 1
    broke = setup_progress.pull(
        "http://x", "t", Watch(Lines(), "m"), post=lambda url, payload: stream(error=OSError("connection reset"))
    )
    assert "connection reset" in broke
    ended = setup_progress.pull(
        "http://x", "t", Watch(Lines(), "m"), post=lambda url, payload: stream({"status": "pulling"})
    )
    assert ended == prompts.SETUP_PULL_ENDED


def test_a_stalled_pull_keeps_waiting_and_says_it_is_still_working(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_progress, "STUCK_SECONDS", 0.05)
    gate = threading.Event()

    def post(url: str, payload: Mapping[str, object]) -> Iterator[bytes]:
        yield (json.dumps({"digest": layer("a"), "total": 100, "completed": 30}) + "\n").encode()
        assert gate.wait(10)
        yield (json.dumps({"status": "success"}) + "\n").encode()

    face = Lines()

    def release() -> None:
        while not any(line.endswith(prompts.SETUP_STILL_WORKING) for line in face.lines):
            threading.Event().wait(0.01)
        gate.set()

    threading.Thread(target=release, daemon=True).start()
    assert setup_progress.pull("http://x", "t", Watch(face, "her model"), post=post) == ""
    assert face.lines[-1] == "  her model ●●●●●"
    assert any(line == f"  her model ●○○○○ {prompts.SETUP_STILL_WORKING}" for line in face.lines)


def test_a_pull_that_repeats_the_same_bytes_says_it_is_still_working() -> None:
    """Review PROM-01: Ollama's pull sends its progress on a timer whether bytes came or not, so a stalled layer
    repeats the same line; with no new dot that long, the line says it is still working, as with no line at all."""
    face = Lines()
    ticks = iter(range(0, 100_000, 10))  # ten seconds a look: the same line, again and again, for minutes
    same = {"digest": layer("a"), "total": 100, "completed": 30}
    watch = Watch(face, "her model", clock=lambda: float(next(ticks)))
    said = setup_progress.pull("http://x", "t", watch, post=lambda u, p: stream(*[same] * 40, {"status": "success"}))
    assert said == ""
    assert face.lines[:3] == [
        "  her model ○○○○○",
        "  her model ●○○○○",
        f"  her model ●○○○○ {prompts.SETUP_STILL_WORKING}",
    ]


def test_her_models_dots_count_against_its_whole_size_as_its_layers_come_one_at_a_time() -> None:
    """Review PROM-06: Ollama names a layer only when its turn comes (gemma4:26b-mlx has 1073), so the dots count
    against the model's whole size, known from its pinned build, and rise across the whole download."""
    face = Lines()
    lines: list[object] = []
    for name in "abcd":
        lines += [{"digest": layer(name), "total": 250, "completed": done} for done in (0, 125, 250)]
    assert (
        setup_progress.pull(
            "http://x",
            "t",
            Watch(face, "her model"),
            total=1000,
            post=lambda u, p: stream(*lines, {"status": "success"}),
        )
        == ""
    )
    assert [lit(line) for line in face.lines] == [0, 1, 2, 3, 4, 5]
    face = Lines()  # a size too small for what Ollama names: its layers' sum counts instead
    assert (
        setup_progress.pull(
            "http://x",
            "t",
            Watch(face, "her model"),
            total=10,
            post=lambda u, p: stream({"digest": layer("a"), "total": 100, "completed": 50}, {"status": "success"}),
        )
        == ""
    )
    assert [lit(line) for line in face.lines] == [0, 2, 5]


def test_only_a_layer_in_ollamas_own_digest_form_counts_and_their_number_is_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Review SEC-7: a digest counts only as sha256 and 64 hex digits, and the layers kept are capped (far above
    the 1073 of gemma4:26b-mlx), so a stream with no end of names cannot fill setup's memory."""
    face = Lines()
    forms = ("a", "sha256:" + "A" * 64, "sha256:" + "0" * 63, "sha256:" + "0" * 64 + "\n", "md5:" + "0" * 64, 7)
    odd = [{"digest": digest, "total": 100, "completed": 100} for digest in forms]
    assert (
        setup_progress.pull("http://x", "t", Watch(face, "m"), post=lambda u, p: stream(*odd, {"status": "success"}))
        == ""
    )
    assert [lit(line) for line in face.lines] == [0, 5]
    monkeypatch.setattr(setup_progress, "MAX_LAYERS", 2)
    face = Lines()
    many = [{"digest": layer(name), "total": 100, "completed": 100 if name == "c" else 0} for name in "abc"]
    assert (
        setup_progress.pull("http://x", "t", Watch(face, "m"), post=lambda u, p: stream(*many, {"status": "success"}))
        == ""
    )
    assert [lit(line) for line in face.lines] == [0, 5]  # the third layer never counted


def test_what_ollama_or_a_broken_stream_says_is_shown_without_control_characters() -> None:
    """Review SEC-6: Ollama's error and the reason a stream broke reach your terminal and the window's log, so
    escape sequences, text turned around and invisible characters are each a "?" (hostsec.clean)."""
    said = setup_progress.pull(
        "http://x", "t", Watch(Lines(), "m"), post=lambda u, p: stream({"error": "bad\u001b[2K name\u202e here\u200b"})
    )
    assert said == "bad?[2K name? here?"
    broke = setup_progress.pull(
        "http://x", "t", Watch(Lines(), "m"), post=lambda u, p: stream(error=OSError("reset\u001b[2K\u202e\nnow"))
    )
    assert "\u001b" not in broke and "\u202e" not in broke and "\n" not in broke and "reset?[2K?" in broke


class Ollama(http.server.BaseHTTPRequestHandler):
    """A test's own stand-in for Ollama's /api/pull on this Mac: a redirect, or one line too long."""

    seen: ClassVar[list[str]] = []

    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/moved":
            self.send_response(302)
            self.send_header("Location", "/elsewhere")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"x" * (1 << 17) if self.path == "/long" else b'{"status": "success"}\n'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        type(self).seen.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def ollama() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Ollama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    Ollama.seen = []
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def test_the_pull_follows_no_redirect_and_reads_no_line_without_end(ollama: str) -> None:
    """Review SEC-7: Ollama's pull is a POST to this Mac; a redirect would turn it into a GET to anywhere, so it is
    an error, never followed; a line is read up to 64 KB, and a longer one ends the stream."""
    assert list(REAL_POST(f"{ollama}/api/pull", {"model": "t"})) == [b'{"status": "success"}\n']
    with pytest.raises(urllib.error.HTTPError):
        list(REAL_POST(f"{ollama}/moved", {"model": "t"}))
    assert Ollama.seen == []
    with pytest.raises(ValueError, match=re.escape(prompts.SETUP_PULL_LINE_LONG)):
        list(REAL_POST(f"{ollama}/long", {"model": "t"}))


def test_a_download_program_that_cannot_start_is_an_exit_code(tmp_path: Path) -> None:
    """Review SEC-9: a curl that is missing or cannot run gives an exit code, as the other changes do, so the step
    says the download did not finish instead of a Python error."""
    face = Lines()
    code = setup_progress.fetch(
        [str(tmp_path / "no-curl"), "-o", str(tmp_path / "x")], tmp_path / "x", 10, Watch(face, "Ollama")
    )
    assert code == 127 and face.lines == ["  Ollama ○○○○○"] and face.ended == 1


def fake_curl(tmp_path: Path, chunks: int, code: int = 0) -> list[str]:
    """A download that writes `chunks` pieces of 100 bytes into its -o file, a little apart, then ends with `code`."""
    script = tmp_path / "curl.py"
    script.write_text(
        "import sys, time\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        f"for _ in range({chunks}):\n"
        "    with open(out, 'ab') as f: f.write(b'x' * 100)\n"
        "    time.sleep(0.03)\n"
        f"sys.exit({code})\n"
    )
    return [sys.executable, str(script)]


def test_a_file_download_lights_its_dots_by_the_file_as_it_grows(tmp_path: Path) -> None:
    target = tmp_path / "ollama-darwin.tgz"
    face = Lines()
    argv = [*fake_curl(tmp_path, 10), "-o", str(target)]
    assert setup_progress.fetch(argv, target, 1000, Watch(face, "Ollama"), poll=0.01) == 0
    counts = [lit(line) for line in face.lines]
    assert counts[0] == 0 and counts[-1] == 5 and counts == sorted(counts) and len(set(counts)) == len(counts)
    assert face.ended == 1


def test_a_file_download_that_fails_keeps_its_dots_and_its_exit_code(tmp_path: Path) -> None:
    target = tmp_path / "ollama-darwin.tgz"
    face = Lines()
    argv = [*fake_curl(tmp_path, 3, code=22), "-o", str(target)]
    assert setup_progress.fetch(argv, target, 1000, Watch(face, "Ollama"), poll=0.01) == 22
    assert lit(face.lines[-1]) < 5 and face.ended == 1


def test_the_mac_stays_awake_while_a_download_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """macOS's own caffeinate, by its full path under the tools' root, held until the download ends (it ends with
    setup too: -w names setup's process)."""
    log = tmp_path / "caffeinate.log"
    tool = tmp_path / "tools/usr/bin/caffeinate"
    tool.parent.mkdir(parents=True)
    tool.write_text(f'#!/bin/sh\necho "$@" >> {shlex.quote(str(log))}\nexec sleep 30\n')
    tool.chmod(0o755)
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(tmp_path / "tools"))
    with setup_progress.awake() as held:
        assert held is not None and held.poll() is None
        for _ in range(500):  # its first line written before it is ended
            if log.exists() and log.read_text():
                break
            threading.Event().wait(0.01)
    assert held.poll() is not None
    words = log.read_text().split()
    assert words[:2] == ["-i", "-w"] and words[2].isdigit()
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(tmp_path / "none"))
    with setup_progress.awake() as held:  # no caffeinate: the download goes on anyway
        assert held is None
