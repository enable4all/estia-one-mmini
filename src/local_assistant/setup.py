"""`scripts/setup.sh`: set her up step by step (by the config's name, default Estia), the parts she needs to work,
then the extras you pick.

Safe to run again: every step checks first and skips what is done. The codes typed here are Signal's one-time captcha
link and SMS code and a registration-lock PIN, or an account without a number's Account ID and Recovery key, and web
search's keys (Ollama's and Brave's): each read hidden from the terminal itself (never from stdin, which may be an
AI's pipe) or, at the desk, from the setup window over setup's own pipe; Signal's go to signal-cli on stdin (never as
an argument, which other users of the Mac can see), the web keys into her web.env (0600), and the keychain copies
(the web keys, and the Recovery key and the Account ID once Signal took them) go to /usr/bin/security on its stdin.
Nothing is sent anywhere except what signal-cli, Ollama and the steps' own lookups (a home area at OpenStreetMap, one
test search at ollama.com) do for the step you are on. It runs in the person's own terminal, or, started by their AI
without one, at the Mac's own screen with the setup window as its only face (setup_ai).
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import errno
import math
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import termios
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from local_assistant import (
    doctor,
    harden,
    hostsec,
    ollama_build,
    ownuser,
    prompts,
    setup_admin,
    setup_ai,
    setup_answers,
    setup_progress,
    share,
)
from local_assistant.commands import voices_here
from local_assistant.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_NAME,
    DEFAULT_STYLE,
    DEFAULT_VOICE,
    LOCAL_HOSTS,
    STYLES,
    Config,
    ConfigError,
    check_name,
    default_voice,
    load_config,
)
from local_assistant.configtext import PROJECTS_LINES
from local_assistant.configtext import with_chords as with_chords
from local_assistant.configtext import with_projects as with_projects
from local_assistant.configtext import with_section as with_section
from local_assistant.hostsec import SUDO, estia_root, flush_typed, read_hidden_lines
from local_assistant.language import CODES, ENGLISH
from local_assistant.setup_answers import Answers, AnswersInUse, saved_ids
from local_assistant.setup_extras import EXTRAS as EXTRAS
from local_assistant.setup_extras import REDO_SET_UP, ExtrasSteps
from local_assistant.setup_extras import Extra as Extra
from local_assistant.setup_extras import outside_home as outside_home
from local_assistant.setup_extras import parse_extras as parse_extras
from local_assistant.setup_extras import projects_refusal as projects_refusal
from local_assistant.setup_face import Back, Face, PlainFace, Readers, choose_face
from local_assistant.setup_plan import QUESTIONS, Mac, question, typed_name, unkept_ssh
from local_assistant.setup_plan import nuclear_text as nuclear_text
from local_assistant.setup_questions import Checked, Question, Value, fill, yes_or_no
from local_assistant.setup_signal import SIGNAL_REDO, SignalSteps
from local_assistant.setup_signal import Sender as Sender
from local_assistant.setup_signal import login_failed as login_failed
from local_assistant.setup_signal import needed_code as needed_code
from local_assistant.setup_signal import one_time_word as one_time_word
from local_assistant.setup_signal import owner_by_word as owner_by_word
from local_assistant.setup_signal import senders as senders
from local_assistant.setup_window import WindowFace
from local_assistant.setup_window import face as window_face
from local_assistant.signal_io import signal_data
from local_assistant.voice import pocket_standard

REPO = Path(__file__).resolve().parents[2]
SHARE_FAILED = 3  # scripts/migrate-to-own-user.sh: she was moved and only the projects share after it failed
MOVE_REFUSED = -1  # the window's move: macOS's box refused, then Not now (never a script's exit code)
OLLAMA = ollama_build.BASE  # Ollama's official build, on this Mac only (the Ollama step keeps or installs it)
IPV4 = "-Djava.net.preferIPv4Stack=true"  # registration goes through Java's HTTP client; hCaptcha is IPv4 only
PLACEHOLDER_NUMBER = "+300000000000"
PLACEHOLDER_ACI = "00000000-0000-0000-0000-000000000000"
_NUMBER = re.compile(r"^\+[0-9]{8,15}$")
_ACI = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
TTY = "/dev/tty"  # the person's own terminal: every hidden answer is read from it, never from stdin
RECEIVE_SECONDS = 20  # how long one look for the one-time word waits
REGISTER_SECONDS = 120.0  # one register or verify request to Signal's servers
# the same answer refused this often in a row from a stdin that is no terminal (a pipe, a test's typing) is the end
# of input, never a loop without end; a person at a terminal is asked again for as long as they type
REFUSED_MAX = 25
SKIP_WORDS = ("s", "skip", "n", "no", "q")  # at a command setup offers: not this one
# the name question's suggestions (owner's picks), and the English voice each brings: a female name Pocket TTS's heart
# on either model (owner, 27 Oct: "make it default"; 29 Oct: "on 16gb make Heart the default as well": `female_voice`);
# a male one its alba (26 Oct: "alba & michael for males"); the other ten languages speak with
# Supertonic's voice F1 whatever the name, but Spanish, Italian, Portuguese, Dutch, German and French in Pocket TTS's
# jane ([voice] language_voice)
FEMALE_NAMES = ("Maya", "Melina")  # owner, 27 Oct: "give only 4 suggestions": Estia, Maya, Melina, Socrates
MALE_NAMES = ("Socrates",)
FEMALE_VOICE = DEFAULT_VOICE
MALE_VOICE = "alba"
_VOICE_LINE = re.compile(rf'^#voice = "{DEFAULT_VOICE}"', re.MULTILINE)
# setup's last step: the English voices (owner, 27 Oct: "heart, jane, eve, vera (female); alba, michael (male)", "not
# af_heart, do heart"; never by the model's size), heart first on either model, vera only where Pocket TTS's 100M model
# runs (`voice.pocket_standard`)
FEMALE_VOICES = ("heart", "jane", "eve", "vera")
MALE_VOICES = ("alba", "michael")
_DIGEST_LINE = re.compile(r'^#model_digest = "sha256:0{64}"', re.MULTILINE)
_CONTEXT_LINE = re.compile(r"^#context_tokens = 32768$", re.MULTILINE)
_OLLAMA_LINE = re.compile(r'^#ollama = "build"$', re.MULTILINE)
_OLLAMA_URL_LINE = re.compile(r'^ollama_base_url = "http://127\.0\.0\.1:11434/v1"$', re.MULTILINE)
_PROJECTS_LINES = PROJECTS_LINES  # the example's [projects] block, commented out (configtext)


@dataclass(frozen=True)
class ModelChoice:
    tag: str
    digest: str  # the build's manifest digest (Ollama's list shows its first 12 digits as the ID): checked after a pull
    num_ctx: int  # the context window the local-worker alias gets
    note: str
    ollama: str = ollama_build.MIN_TEXT  # the oldest Ollama it works with: JSON-schema output on the MLX engine
    tested: bool = True  # passed her safety tests; False: setup says what it may do and asks first
    download: str = ""  # its size and time, as its step's line says them ("18.3 GB, about 25 min")
    size: int = 0  # its pinned build's bytes, every blob of the manifest: the dots of its pull count against it
    steps_minutes: int = 30  # every step's downloads and builds at 100 Mbit/s, without calls (README, How long)


# Gemma 4's builds for Apple silicon (MLX), by their explicit tags (owner, 15 Oct). Never the bare gemma4:26b or
# gemma4:12b: since Ollama 0.40.0 they are lists, and what they download depends on the Ollama version (on 0.40.x the
# untested 26b-nvfp4 build).
GEMMA_26B = "gemma4:26b-mlx"
GEMMA_26B_DIGEST = "f0fc7e0ae4947d382989b1f57db3d098a86b87c5b6e9523b9561ba81a2a64879"  # 18.3 GB, the live model
GEMMA_12B = "gemma4:12b-mlx"
GEMMA_12B_DIGEST = "ded7a27350032202d9e9b2a6071e8aa89959ab156771b5228f30863c741c4970"  # 7.7 GB
# each pinned manifest's blobs (layers and config) in bytes, read on the live Mac (Ollama 0.40.4, 27 Oct): 1073 layers
# and the config for 26B, 738 and the config for 12B; Ollama's pull names a layer only when its turn comes
GEMMA_26B_BYTES = 18_334_651_584 + 422
GEMMA_12B_BYTES = 7_714_252_694 + 432


def choose_model(memory_gb: float) -> ModelChoice | None:
    """The best model for this Mac's memory, or None below 16 GB. Gemma 4 26B (a mixture of experts, 4B active) is
    the tested one and fast on Apple silicon, but its 18.3 GB need that much of the GPU's share; 12B fits next to macOS
    and the voice models on 16 and 24 GB, but failed some of her safety tests (15 Oct, run twice; SECURITY.md, known
    gaps): setup says what it may do and asks first (`Wizard.model`; owner, 19 Oct: never what failed). Both
    need Ollama 0.33.1 or later."""
    if memory_gb < 15:
        return None
    if memory_gb < 30:
        note = "a 7.7 GB download, about 10 minutes at 100 Mbit/s"
        context = 16_384 if memory_gb < 20 else 32_768
        return ModelChoice(
            GEMMA_12B,
            GEMMA_12B_DIGEST,
            context,
            note,
            tested=False,
            download="7.7 GB, about 10 min",
            size=GEMMA_12B_BYTES,
            steps_minutes=15,
        )
    context = 32_768 if memory_gb < 44 else 65_536
    note = "an 18.3 GB download, about 25 minutes at 100 Mbit/s; the tested model"
    return ModelChoice(
        GEMMA_26B, GEMMA_26B_DIGEST, context, note, download="18.3 GB, about 25 min", size=GEMMA_26B_BYTES
    )


def memory_gb() -> float:
    """This Mac's memory in GB (GiB, as macOS counts it)."""
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30


def female_voice(memory: float) -> str:
    """The English voice setup writes for a female name on a Mac with `memory` GB: heart, on either of Pocket TTS's
    models (`voice.pocket_standard`; owner, 29 Oct)."""
    return default_voice(pocket_standard(memory))


def voices_line(memory: float) -> str:
    """Setup's last step (`ExtrasSteps.closing`, once her voice service is installed): the English voices of a Mac
    with `memory` GB, each one /voices lists there (`commands.voices_here`: vera only beside the 100M model;
    review PROM-03), and how to get /voices, /tools and /help in Signal."""
    here = voices_here(pocket_standard(memory))
    female = ", ".join(name for name in FEMALE_VOICES if name in here)
    return prompts.SETUP_VOICES.format(female=female, male=", ".join(name for name in MALE_VOICES if name in here))


def modelfile(choice: ModelChoice) -> str:
    """The alias local-worker: the chosen model with a context window large enough for a long chat, and for Gemma 4
    of 12B or more Google's prompt for that size (ollama_build.LARGE_PROMPT): Ollama tells the sizes apart by the
    model's name, which local-worker does not have (empty answers, 17 Oct)."""
    return alias_modelfile(choice.tag, choice.num_ctx)


def alias_modelfile(tag: str, num_ctx: int | None) -> str:
    """local-worker's Modelfile: FROM the tag, its context window (None: no line, Ollama's own applies), and the
    large prompt for a Gemma 4 tag of 12B or more."""
    lines = [f"FROM {tag}", *([f"PARAMETER num_ctx {num_ctx}"] if num_ctx is not None else [])]
    if ollama_build.large_gemma4(tag):
        lines.append(f"RENDERER {ollama_build.LARGE_PROMPT}")
    return "\n".join(lines) + "\n"


def context_of(parameters: str) -> int | None:
    """The num_ctx in /api/show's parameters text ("num_ctx    32768" lines); None when it sets none."""
    for line in parameters.splitlines():
        key, _, value = line.strip().partition(" ")
        if key == "num_ctx" and value.strip().isdigit():
            return int(value.strip())
    return None


# What an alias may add to its model's size in Ollama's list: its parameters and settings, a few hundred bytes (setup's
# num_ctx line: 16). Weights, an adapter or a draft model add megabytes.
ALIAS_EXTRA_BYTES = 64 * 1024


def _settings(parameters: str) -> list[str]:
    """/api/show's parameters text as sorted lines, spaces evened out and num_ctx left out: what an alias holds besides
    its context."""
    lines = (" ".join(line.split()) for line in parameters.splitlines())
    return sorted(line for line in lines if line and line.split(" ", 1)[0] != "num_ctx")


def _held(shown: Mapping[str, object]) -> tuple[object, ...]:
    """What a model holds, from its /api/show answer: its weights (architecture and parameter count from model_info,
    format and quantization from details; Ollama reads them from the weights' own files), its template, system prompt
    and messages, and its parameters but num_ctx."""
    info = shown.get("model_info")
    details = shown.get("details")
    info = info if isinstance(info, Mapping) else {}
    details = details if isinstance(details, Mapping) else {}
    return (
        info.get("general.architecture"),
        info.get("general.parameter_count"),
        details.get("format"),
        details.get("quantization_level"),
        str(shown.get("template") or ""),
        str(shown.get("system") or ""),
        repr(shown.get("messages") or []),
        _settings(str(shown.get("parameters") or "")),
    )


def made_from(
    alias: Mapping[str, object],
    alias_shown: Mapping[str, object],
    tag: Mapping[str, object],
    tag_shown: Mapping[str, object],
) -> bool:
    """Whether an alias is the tag with at most a context of its own, as setup made local-worker before 0.9.3 (FROM the
    tag, PARAMETER num_ctx): its digest is its own, and Ollama names no parent for an MLX build (its manifest has no
    model layer, so parent_model is ""), so it is traced by what it holds (`_held`, from each /api/show answer) and by
    its size in Ollama's list (`alias`, `tag`: their /api/tags entries), within ALIAS_EXTRA_BYTES of the tag's. False
    when any of it cannot be read: weights or settings a remake from the tag would drop are never guessed at."""
    alias_bytes, tag_bytes = _whole(alias.get("size")), _whole(tag.get("size"))
    if alias_bytes is None or tag_bytes is None or abs(alias_bytes - tag_bytes) > ALIAS_EXTRA_BYTES:
        return False
    held = _held(alias_shown)
    architecture, count = held[0], _whole(held[1])
    return isinstance(architecture, str) and bool(architecture) and bool(count) and held == _held(tag_shown)


def _whole(value: object) -> int | None:
    """A whole number from Ollama's JSON (never a bool); None for anything else."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _digest_of(entry: object) -> str:
    """A model's digest in its /api/tags entry, lowercase and without "sha256:" ("" when none)."""
    digest = entry.get("digest") if isinstance(entry, Mapping) else None
    return str(digest or "").lower().removeprefix("sha256:")


def suggested(answer: str) -> tuple[str, str] | None:
    """A suggested name (any case, or 1 for the default) as it is written, with its voice (a female name's is heart,
    on either of Pocket TTS's models: `female_voice`); None for any other name."""
    if answer.strip() == "1":
        return DEFAULT_NAME, FEMALE_VOICE
    for names, voice in (((DEFAULT_NAME, *FEMALE_NAMES), FEMALE_VOICE), (MALE_NAMES, MALE_VOICE)):
        for name in names:
            if answer.strip().casefold() == name.casefold():
                return name, voice
    return None


def render_config(
    example: str,
    bot_number: str,
    aci: str,
    language: str = ENGLISH,
    *,
    name: str = DEFAULT_NAME,
    voice: str = FEMALE_VOICE,
    model_digest: str = "",
    projects: tuple[str, str] | None = None,
    style: str = DEFAULT_STYLE,
    context_tokens: int | None = None,
    ollama: str = "build",
    ollama_url: str = "",
) -> str:
    """config.example.toml with her Signal account (a number, or an account without one by its Account ID), the owner's
    account ID, the language, her name, her voice and her built-in style ([personality] style) filled in, the model's
    pin when its digest is given, its context window when `context_tokens` names it (review I24), `ollama = "app"` when
    the person chose the Ollama app (owner, 29 Oct), her own Ollama's address when `ollama_url` names another than the
    example's (http://127.0.0.1:8151/v1: her own, on this Mac only), and [projects] with
    its folder and level when `projects` names them;
    everything else stays as the example has it (the extras commented out: the voice goes into the commented [voice]
    block's voice line, used once [voice] is turned on)."""
    if not _NUMBER.match(bot_number) and not hostsec.is_account_id(bot_number):
        raise ValueError(
            "her Signal number must be + and the country code and digits, like +306900000000, or her account's "
            "Account ID"
        )
    if not _ACI.match(aci):
        raise ValueError("the account ID must be a UUID")
    if language not in CODES:
        raise ValueError(f"the language must be one of {', '.join(CODES)}")
    number_line = f'bot_number = "{PLACEHOLDER_NUMBER}"'
    aci_line = f'allowed_acis = ["{PLACEHOLDER_ACI}"]'
    if example.count(number_line) != 1 or example.count(aci_line) != 1:
        raise ValueError("config.example.toml no longer has its bot_number and allowed_acis placeholder lines")
    try:
        name = check_name(name)
    except ConfigError as exc:
        raise ValueError(str(exc)) from exc
    if voice not in (FEMALE_VOICE, MALE_VOICE):
        raise ValueError(f"the voice must be {FEMALE_VOICE} or {MALE_VOICE}")
    if style not in STYLES:
        raise ValueError(f"the style must be {' or '.join(STYLES)}")
    style_line = re.compile(rf'^style = "{DEFAULT_STYLE}"', re.MULTILINE)
    if len(style_line.findall(example)) != 1:
        raise ValueError(f'config.example.toml no longer has its style = "{DEFAULT_STYLE}" line')
    language_line = re.compile(r'^language = "en"', re.MULTILINE)
    if len(language_line.findall(example)) != 1:
        raise ValueError('config.example.toml no longer has its language = "en" line')
    name_line = re.compile(rf'^name = "{DEFAULT_NAME}"', re.MULTILINE)
    if len(name_line.findall(example)) != 1 or len(_VOICE_LINE.findall(example)) != 1:
        raise ValueError(f'config.example.toml no longer has its name = "{DEFAULT_NAME}" and #voice lines')
    text = example.replace(number_line, f'bot_number = "{bot_number}"').replace(aci_line, f'allowed_acis = ["{aci}"]')
    text = name_line.sub(f'name = "{name}"', language_line.sub(f'language = "{language}"', text))
    text = style_line.sub(f'style = "{style}"', text)
    if model_digest:
        if not re.fullmatch(r"[0-9a-f]{64}", model_digest) or len(_DIGEST_LINE.findall(text)) != 1:
            raise ValueError(
                "the model's digest must be 64 hex digits, and the example must have its #model_digest line"
            )
        text = _DIGEST_LINE.sub(f'model_digest = "sha256:{model_digest}"', text)
    if context_tokens is not None:
        if len(_CONTEXT_LINE.findall(text)) != 1:
            raise ValueError("config.example.toml no longer has its #context_tokens = 32768 line")
        text = _CONTEXT_LINE.sub(f"context_tokens = {context_tokens}", text)
    if ollama != "build":
        if ollama != "app" or len(_OLLAMA_LINE.findall(text)) != 1:
            raise ValueError('ollama must be "build" or "app", and the example must have its #ollama = "build" line')
        text = _OLLAMA_LINE.sub('ollama = "app"', text)
    if ollama_url and ollama_url != "http://127.0.0.1:11434/v1":
        address = urllib.parse.urlsplit(ollama_url)
        if address.scheme != "http" or address.hostname not in LOCAL_HOSTS or len(_OLLAMA_URL_LINE.findall(text)) != 1:
            raise ValueError(
                "ollama_url must be Ollama on this Mac, and the example must have its ollama_base_url line"
            )
        text = _OLLAMA_URL_LINE.sub(f'ollama_base_url = "{ollama_url}"', text)
    if projects is not None:
        if any(len(line.findall(text)) != 1 for line in _PROJECTS_LINES):
            raise ValueError("config.example.toml no longer has its commented [projects], root and default lines")
        text = with_projects(text, *projects)
    return _VOICE_LINE.sub(f'#voice = "{voice}"', text)


def has_terminal() -> bool:
    """Setup's questions and Signal's one-time codes need the person's own terminal: stdin is one, and it opens."""
    if not sys.stdin.isatty():
        return False
    try:
        os.close(os.open(TTY, os.O_RDWR | os.O_NOCTTY))
    except OSError:
        return False
    return True


def read_hidden(prompt: str, tty: str | None = None) -> str:
    """A hidden line of any length from the terminal itself, never from stdin (a pipe there may be an AI's). Its line
    mode is off while it reads: macOS keeps a typed line to 1,023 characters and drops the rest with the Enter, and
    Signal's captcha link is longer. Nothing is echoed; Backspace and Ctrl-U edit; Ctrl-C stops as usual. Ctrl-D on an
    empty line raises EOFError, no terminal OSError; the terminal's mode is always put back."""
    fd = os.open(tty or TTY, os.O_RDWR | os.O_NOCTTY)
    try:
        before = termios.tcgetattr(fd)
        quiet = termios.tcgetattr(fd)
        quiet[3] &= ~(termios.ICANON | termios.ECHO)
        quiet[6][termios.VMIN] = 1
        quiet[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSAFLUSH, quiet)
        try:
            os.write(fd, prompt.encode())
            return _hidden_line(fd)
        finally:
            termios.tcsetattr(fd, termios.TCSAFLUSH, before)
            os.write(fd, b"\n")
    finally:
        os.close(fd)


def _hidden_line(fd: int) -> str:
    """Bytes up to Enter (CR or LF), with Backspace (one character, all its UTF-8 bytes) and Ctrl-U applied."""
    line = bytearray()
    while True:
        chunk = os.read(fd, 4096)
        if not chunk:
            raise EOFError
        for byte in chunk:
            if byte in (0x0A, 0x0D):
                return line.decode("utf-8", errors="replace").strip()
            if byte == 0x04 and not line:  # Ctrl-D on an empty line
                raise EOFError
            if byte in (0x08, 0x7F):
                while line and line.pop() & 0xC0 == 0x80:  # a continuation byte: the character goes on
                    pass
            elif byte == 0x15:  # Ctrl-U: the line so far
                line.clear()
            elif byte >= 0x20 or byte == 0x09:
                line.append(byte)


def registration_argv(signal_cli: str) -> list[str]:
    """signal-cli's JSON-RPC in multi-account mode (no -a): only there does 0.14.9 offer register and verify, so the
    captcha, the SMS code and a PIN go on stdin. It opens every account signal-cli keeps here; setup registers only
    when no config is set up yet, so no bridge holds one of them. Its data folder by name, as every other command of
    hers (`signal_io.signal_data`: one that followed XDG_DATA_HOME was never found again; second deep scan,
    signal-04)."""
    return [signal_cli, IPV4, "--config", str(signal_data()), *harden.TRUST, "jsonRpc", "--receive-mode", "manual"]


@contextlib.contextmanager
def ctrl_c_held() -> Iterator[list[int]]:
    """While a step's program runs: a Ctrl-C is noted, never raised in setup. The terminal sends it to every program
    in the foreground, so the step's own program gets it and decides (harden ignores it from her stop to her start
    again, the move restores her, a deploy removes the release it built); setup raising there killed the program
    halfway (`subprocess.run` ends it with SIGKILL), and she could stay stopped (review setup-ctrl-c-kills-harden).
    A handler, never SIG_IGN, which a program setup starts would inherit. Off the main thread nothing changes."""
    pressed: list[int] = []
    try:
        before = signal.signal(signal.SIGINT, lambda signum, _frame: pressed.append(signum))
    except ValueError:  # not the main thread: signals are not set there
        yield pressed
        return
    try:
        yield pressed
    finally:
        signal.signal(signal.SIGINT, before)


class _Known:
    """The answers so far, as a question reads them: this run's plan first, then the answers file."""

    def __init__(self, planned: Mapping[str, Value], answers: Answers) -> None:
        self.planned = planned
        self.answers = answers

    def get(self, qid: str) -> Value | None:
        return self.planned[qid] if qid in self.planned else self.answers.get(qid)

    def kept(self, qid: str) -> bool:
        return self.answers.kept(qid)

    def done(self, step: str) -> bool:
        return self.answers.done(step)


# ------------------------------------------------------------------ the steps (interactive; each checks first)


class Wizard(SignalSteps, ExtrasSteps):
    def __init__(
        self,
        config_path: Path,
        *,
        dry_run: bool,
        name: str = DEFAULT_NAME,
        answers: Answers | None = None,
        face: Face | None = None,
    ) -> None:
        self.config_path = config_path
        self.dry_run = dry_run
        self.answers = answers if answers is not None else Answers.memory()  # nothing on disk unless given a file
        self.planned: dict[str, Value] = {}  # this run's plan (phase A) and the answers kept from an earlier run
        self.planning = False  # phase A runs: each answer is part of the plan, used quietly at its step
        # the plan's answers given before going back (owner, 27 Oct: "the answers so far are kept"): each question
        # gone back to is asked again, its answer before shown and taken by Enter
        self.gone_back: dict[str, Value] = {}
        self.redo: frozenset[str] = frozenset()  # the questions `--redo` named: asked again
        self.move_stop = ""  # why the move did not go through: setup's stop (always_on)
        self.fixed_mac: Mac | None = None  # the Mac's answers for this run (run_setup reads them once)
        # every question and every line through one face (plain lines unless given another); its readers are the
        # Wizard's own, called as they are when it asks
        self.face: Face = face if face is not None else PlainFace(self.readers())
        self.name = name  # hers: the config's, once there is one, or the one chosen at the name question
        self.voice = female_voice(memory_gb())  # the English voice that goes with the name chosen here
        self.style = DEFAULT_STYLE  # her built-in style: the config's, once there is one, or the one chosen here
        self.style_asked = False  # the style question came already (the config step): the end asks it no more
        self.signal_cli = shutil.which("signal-cli") or "signal-cli"
        self.username = ""  # her Signal username, for an account without a number (how you find her)
        # the Ollama step names the one in use; your home from the user database, as the service file's
        self.ollama_program = str(ollama_build.owner_home() / "opt/ollama/current/ollama")
        # a first install stops at an Ollama the step refuses; a re-run where she is set up (main says so) shows it
        # as "!" and goes on, and the model step then pulls nothing
        self.first_install = True
        self.ollama_refused = False
        self.ollama_app: Path | None = None  # the Ollama app, when the person chose it: the config records it
        self.ollama_app_asked = False  # chosen this run (not kept from before): a re-run says the config's line
        # where setup talks to Ollama: her own address once the step chose her own Ollama, else 11434 (today's); your
        # config's ollama and ollama_base_url, once it is set up (the layout's rule 2)
        self.ollama_base = OLLAMA
        self.config_ollama: tuple[str, str] | None = None
        self.clock: Callable[[], float] = time.monotonic  # the wait for her voice at the end
        self.sleep: Callable[[float], None] = time.sleep
        self.asking_words: list[str] = []  # the values filled into the question being asked (her number): private
        self.steps: list[str] = []  # this run's steps after its plan, by key (prompts.SETUP_STEPS): "Step n of m"
        self.step_now = ""  # the step that runs now (its key): a stop names it in the window ("1 of 9")
        self.ai: setup_ai.Run | None = None  # started by an AI without a terminal: the window only (setup_ai)
        self.finished = False  # the run reached its last words (closing)

    @property
    def mac(self) -> Mac:
        """What the Mac answers: read once for a run (run_setup), else as it is at the moment of asking (a step
        called on its own: the remote login, screen, her own user, an older install)."""
        if self.fixed_mac is not None:
            return self.fixed_mac
        return Mac.read(memory_gb=memory_gb(), own_user=self.own_user())

    @property
    def known(self) -> _Known:
        """The answers so far, as a question reads them: this run's plan first, then the answers file."""
        return _Known(self.planned, self.answers)

    def words(self) -> dict[str, str]:
        """What the questions' texts fill in: her name and the like."""
        return {
            "name": self.name,
            "default": DEFAULT_NAME,
            "names": " · ".join((DEFAULT_NAME, *FEMALE_NAMES, *MALE_NAMES)),
            "nuclear": nuclear_text(self.name),
            "move": str(REPO / "scripts" / "migrate-to-own-user.sh"),
            "harden": str(REPO / "scripts" / "harden.sh"),
        }

    def private_values(self) -> list[str]:
        """Every answer of yours as text, but her name and the options picked (a fixed id), and what is filled into the
        question being asked (her number from signal-cli's own list): what no program's arguments may hold (gum's)."""
        found: list[str] = list(self.asking_words)
        for qid, value in {**self.answers.values(), **self.planned}.items():
            if qid == "name" or isinstance(value, bool):
                continue
            try:
                q: Question | None = question(qid).at(self.known, self.mac)
            except KeyError:
                q = None
            items = value.values() if isinstance(value, Mapping) else value if isinstance(value, tuple) else (value,)
            found += [item for item in items if item and (q is None or q.option(item) is None)]
        return found

    def plan(self) -> None:
        """Phase A, before any step runs: every question of the plan that fits this Mac and the answers so far, in
        the list's order. A kept answer is one line ("Kept: …"); every other is asked, checked and saved at once. At
        each question asked after another one, Back (b in Terminal) goes one question back (owner, 27 Oct): that one
        is asked again, its answer before shown and taken by Enter, and the plan goes on from there, every answer
        kept. A dry run shows each and asks none."""
        ids = [q.id for q in QUESTIONS if q.plan]
        asked: list[str] = []  # the questions this run asked, in turn: where Back goes
        at = 0
        self.planning = True
        try:
            while at < len(ids):
                qid = ids[at]
                if qid in self.planned or not self.shows(qid):
                    at += 1
                    continue
                fresh = qid in self.gone_back or not self.kept_before(qid)
                self.face.back = bool(asked) and fresh and not self.dry_run
                try:
                    self.answer(qid)
                except Back:
                    before = asked.pop()
                    if before in self.planned:
                        self.gone_back[before] = self.planned.pop(before)
                    at = ids.index(before)
                    continue
                if fresh and qid in self.planned:
                    asked.append(qid)
                at += 1
        finally:
            self.planning = False
            self.face.back = False
            self.face.no_back = prompts.SETUP_NO_BACK_HERE  # the steps: a b typed there is no answer

    def shows(self, qid: str) -> bool:
        """The plan asks this question here: it fits this Mac and the answers so far, or `--redo` named a first
        install's question whose step runs on an install set up too (calls: `REDO_SET_UP`)."""
        return question(qid).shows(self.known, self.mac) or (qid in self.redo and qid in REDO_SET_UP)

    def kept_before(self, qid: str) -> bool:
        """An answer an earlier run kept, which the plan shows in one line ("Kept: …") and asks nothing for."""
        q = question(qid)
        return q.keep == "saved" and not q.is_secret and self.answers.kept(qid) and qid not in self.gone_back

    def answer(self, qid: str, *, refused: str = "", **words: str) -> Value | None:
        """One question of the list: its answer from the plan or from an earlier run, quietly (an earlier run's in
        one line, "Kept: …"); else asked through the face until its check passes, shown back and saved (never a
        secret, a step's answer or a no that means "not now"). The end of input takes the recommendation and keeps
        nothing; for a yes or no it is a no. A dry run shows the question and takes the yes, or the recommendation,
        so it can show what follows. None: no answer (the end of input with nothing recommended). `refused`: why
        the answer before was refused by what it went to (Signal), said on the question itself, so the window shows
        it on its screen too (review setup-window-step-refusal-lost)."""
        q = question(qid).at(self.known, self.mac, {**self.words(), **words})
        if qid in self.planned:
            return self.planned[qid]
        kept = self.answers.get(qid) if self.kept_before(qid) else None
        if kept is not None:
            self.face.show(q, kept)
            self.planned[qid] = kept
            self.took(qid, kept, asked=False)
            return kept
        self.asking_words = [value for key, value in words.items() if key != "name" and value]
        previous = self.gone_back.pop(qid, None)
        self.face.previous = previous
        try:
            self.face.show(q, None)
            if refused and not self.dry_run:
                self.face.checked(q, Checked(None, refused))
            if self.dry_run:
                hint = "[Y/n]" if q.recommended == "yes" else "[y/N]"
                ask = f"{q.ask} {hint}" if q.kind == "yes_no" else q.ask
                self.say(fill(q.dry, {"ask": q.ask, "hint": hint}) if q.dry else prompts.SETUP_ASKS.format(ask=ask))
                value = self._dry_value(q)
                given = False
            else:
                value, given = self._asked(q, previous)
        except Back:
            if previous is not None:  # asked again later: its answer before stays the default
                self.gone_back[qid] = previous
            raise
        finally:
            self.asking_words = []
            self.face.previous = None
        if value is None:
            return None
        if given and q.keep == "saved" and not q.is_secret and not (q.forget_no and value is False):
            self.answers.set(qid, value)
        if self.planning and not q.is_secret and q.keep != "step":  # a step's own (a "Right?") is asked each time
            self.planned[qid] = value
        self.took(qid, value, asked=given)
        return value

    def _dry_value(self, q: Question) -> Value | None:
        """What a dry run takes: the yes (it shows what a yes runs), else the recommendation as its check keeps it."""
        if q.kind == "yes_no":
            return True
        if q.recommended is None:
            return None
        return (q.check(q.recommended, self.known, self.mac) if q.check else Checked(q.recommended)).value

    def _asked(self, q: Question, previous: Value | None = None) -> tuple[Value | None, bool]:
        """(the answer, whether it was given): asked until its check passes, each refusal said in one sentence.
        Enter takes the answer before (a question gone back to), else the recommendation; the end of input takes the
        recommendation too but gives nothing (a yes or no: a no). q at a question with a quiet way out stops setup,
        every answer so far kept (her number: "Not at hand?")."""
        message = ""
        last, repeats = None, 0  # the same refused answer in a row
        while True:
            raw, given = self._raw(q, message, previous), True
            if raw is None:
                if q.kind == "yes_no" or q.recommended is None:
                    return (False, False) if q.kind == "yes_no" else (None, False)
                raw, given = q.recommended, False
            elif raw == "" and q.kind != "yes_no" and (previous is not None or q.recommended is not None):
                raw = previous if previous is not None else q.recommended
            quiet = q.stop_typed and self.face.asking != "window"  # the window has its own Stop here
            if quiet and isinstance(raw, str) and raw.strip().lower() == "q":
                raise KeyboardInterrupt  # main says how to go on: setup again starts at this question
            assert raw is not None
            result = q.check(raw, self.known, self.mac) if q.check is not None else Checked(raw)
            self.face.checked(q, result)
            confirm = result.value is not None and given and q.confirm and result.confirm
            if confirm and self._confirmed(q.confirm, result.confirm) is not True:  # "Found: …. Right?" A no
                message = ""  # asks again
            elif result.value is not None or not given:
                return result.value, given
            else:
                message = result.message
            repeats = repeats + 1 if raw == last else 1
            last = raw
            # a pipe, never a person: the end of input (an AI-run's answers are the window's, a person's: never)
            if repeats >= REFUSED_MAX and not sys.stdin.isatty() and self.ai is None:
                return (False, False) if q.kind == "yes_no" else (None, False)

    def _confirmed(self, qid: str, found: str) -> Value | None:
        """Its "Right?" (her number shown back, the home area found): Back there is a no, and asks it again. The
        question it confirms keeps its Back and its answer before (a no asks it again, as it was)."""
        back, previous, self.face.back = self.face.back, self.face.previous, False
        try:
            return self.answer(qid, found=found)
        finally:
            self.face.back, self.face.previous = back, previous

    def _raw(self, q: Question, message: str, previous: Value | None = None) -> Value | None:
        """The answer as the face gives it, by the question's kind (`previous`: the answer before, gone back to)."""
        if q.kind == "yes_no":
            # Enter takes the recommended answer wherever a line says it is recommended (owner's call, 29 Oct)
            default = previous if isinstance(previous, bool) else q.recommended == "yes"
            return self.face.confirm(q, default=default)
        if q.kind in ("choice", "cards", "folder"):
            return self.face.choose(q, previous)
        if q.kind == "switches":
            return self.face.switches(q, previous)
        if q.kind in ("text", "number"):
            return self.face.text(q, previous, message)
        if q.kind == "fields":
            return self.face.fields(q, previous, message)
        if q.kind in ("hidden", "code", "pin"):
            return self.face.hidden(q, message)
        if q.kind == "key":
            return self.face.key(q, message, ())
        raise ValueError(f"{q.id}: a {q.kind} screen has no answer")

    def took(self, qid: str, value: Value, *, asked: bool) -> None:
        """What an answer sets for the steps after it: her name and voice, her style; the smaller model's no stops
        setup before anything is downloaded, and so does the end of input at Signal on your phone. (`asked`: just
        given, not kept from before.)"""
        if qid == "name" and isinstance(value, str):
            known = suggested(value)
            self.name = known[0] if known is not None else value
            if known is not None:  # a female name's voice as this run's Mac has it (heart on either model)
                self.voice = known[1] if known[1] == MALE_VOICE else female_voice(self.mac.memory_gb)
        elif qid == "voice":
            self.voice = MALE_VOICE if value == "male" else female_voice(self.mac.memory_gb)
        elif qid == "style" and isinstance(value, str) and value in STYLES:
            self.style = value
        elif qid == "ollama_app" and asked:
            self.ollama_app_asked = True
        elif qid == "smaller_model" and value is False:
            raise SystemExit(prompts.SETUP_SMALLER_MODEL_NO)
        elif qid == "route" and value == "no_number" and not self.answers.kept("account_id"):
            self.say(prompts.SETUP_ROUTE_NO_NUMBER_NEXT)  # the phone's part now, not at the Signal step (round 1)
        elif qid == "signal_app" and value is not True:  # the end of input there: setup waits only for a yes
            # Ctrl-D in your own terminal is setup's usual stop; without a terminal, where to run it
            raise SystemExit(prompts.SETUP_STOPPED if self.terminal() else prompts.SETUP_NO_TERMINAL)

    def plan_steps(self, *, configured: bool) -> None:
        """The steps this run takes after its plan, in order: each says its place as it starts (`begin`). Her Signal
        account and her config only on a first install, calls only on the plan's yes, the Extra Features only when
        some are picked (a re-run, which shows them all, too)."""
        keys = ["ollama", "model", *([] if configured else ["signal", "config"]), "always_on", "security"]
        if self.given("calls") == "yes":
            keys.append("calls")
        chosen = self.picked()
        if chosen is None or chosen:
            keys.append("extras")
        self.steps = [*keys, "check"]

    def begin(self, key: str, detail: str = "") -> None:
        """One plain line as a step starts (owner, 27 Oct: "Step 3 of 9: her model (18 GB, about 25 min)"), its
        size and time where it downloads; nothing for a step this run does not count."""
        if key in self.steps:
            self.step_now = key
            what = prompts.SETUP_STEPS[key] + (f" ({detail})" if detail else "")
            self.say(prompts.SETUP_STEP.format(n=self.steps.index(key) + 1, m=len(self.steps), what=what))
            if self.ai is not None:  # the step's place and its size: setup's own words, never an answer
                self.ai.say(prompts.SETUP_STEP.format(n=self.steps.index(key) + 1, m=len(self.steps), what=what))

    def steps_time(self) -> None:
        """Once the plan is answered: how long the steps take, README's figures (the friend's review, ux-21); nothing
        when her model is there already, as in a re-run."""
        choice = choose_model(memory_gb())
        if choice is None or not self.model_download():
            return
        calls = prompts.SETUP_STEPS_TIME_CALLS if self.given("calls") == "yes" else ""
        size = choice.download.split(",", 1)[0]
        self.say(prompts.SETUP_STEPS_TIME.format(minutes=choice.steps_minutes, size=size, calls=calls))

    def model_download(self) -> str:
        """Her model's size and time for its step's line: "" when Ollama lists local-worker, or nothing is pulled
        (an Ollama setup refused)."""
        choice = choose_model(memory_gb())
        if choice is None or self.mac.model_listed or self.ollama_refused:
            return ""
        return choice.download

    def pull(self, tag: str, *, size: int = 0) -> str:
        """Her model through Ollama's own pull (its /api/pull stream on this Mac), five dots lighting up with it
        against its whole `size` (review PROM-06), the Mac kept awake by the run (run_setup); "" once Ollama says
        it is done, else why not. A dry run only says it."""
        self.say(prompts.SETUP_PULLING.format(url=f"{self.ollama_base}/api/pull", tag=tag))
        if self.dry_run:
            return ""
        watch = setup_progress.Watch(self.face, prompts.SETUP_STEPS["model"])
        return setup_progress.pull(self.ollama_base, tag, watch, total=size)  # the Mac is kept awake by the whole run

    def readers(self) -> Readers:
        """The readers a face asks with: the Wizard's own, looked up at each call (the tests patch them)."""
        return Readers(
            hidden=lambda prompt: self.hidden(prompt),
            hidden_lines=lambda prompt, needed, counted: self.hidden_lines(prompt, needed, counted),
            flush=lambda: self.flush(),
            terminal=lambda: self.terminal(),
        )

    def say(self, text: str) -> None:
        self.face.say(text)

    def command(self, text: str) -> None:
        """A command setup runs that asks nothing in Terminal (signal-cli's requests on its input, a look for the
        word): printed, and the window keeps its screen."""
        self.face.command(text)

    def typed(self, question: str) -> str | None:
        """The answer as typed; None without a terminal (a pipe, or Ctrl-D)."""
        return self.face.line(question)

    def ask(self, question: str, default: str = "") -> str:
        return self.typed(question) or default  # no terminal: take the default

    def terminal(self) -> bool:
        """The person's own terminal shows the question and answers it: only there may Enter mean yes."""
        return harden.in_terminal()

    def yes(self, question: str, *, default: bool = False) -> bool:
        """y or n. Enter takes the safe answer, `default` (owner, 19 Oct: [Y/n] or [y/N] says which), only from a
        terminal; the end of input (Ctrl-D, a pipe) is always a no, and Ctrl-C stops setup (`main`)."""
        terminal = self.terminal()
        enter = default and terminal
        while True:
            answer = self.typed(f"{question} {'[Y/n]' if enter else '[y/N]'}")
            if answer is None:
                return False
            if not answer.strip():
                return enter
            said = yes_or_no(answer)  # English or Greek; anything else asked again (answers-extras-yes-no-other-words)
            if said is not None or not terminal:  # a pipe, never a person: a no
                return said is True
            self.say(prompts.SETUP_YES_NO_AGAIN)

    def again(self, question: str, *, qid: str = "", **words: str) -> bool:
        """Enter (or anything but q): try again; q, or no terminal (Ctrl-D, a pipe): stop. In the window, its own
        screen (`qid`, its fields filled from `words`): Try again, or the way out."""
        if qid and self.in_window:
            return self.answer(qid, **words) is True
        answer = self.typed(question)
        return answer is not None and answer.lower() != "q"

    def hidden(self, prompt: str) -> str:
        """A hidden answer (the captcha link, a code, a PIN), from the terminal only; Ctrl-D counts as q. Without a
        terminal setup stops and says where to run it: a code is never read from stdin."""
        try:
            return read_hidden(prompt)
        except EOFError:
            return "q"
        except OSError:
            raise SystemExit(prompts.SETUP_NO_TERMINAL) from None

    def hidden_lines(self, prompt: str, needed: int, counted: Callable[[str], bool]) -> str:
        """Hidden lines from the terminal only, until `needed` characters that `counted` takes have come (a key
        shown on several lines: `hostsec.read_hidden_lines`); EOFError and OSError as that reader raises them."""
        return read_hidden_lines(prompt, needed, counted, TTY)

    def flush(self) -> None:
        """Drop what was typed ahead on the terminal (an Enter after a key) before the next question shows."""
        flush_typed(TTY)

    def run(
        self,
        command: list[str],
        *,
        shown: list[str] | None = None,
        printed: bool = False,
        why: str = "",
        answer: Callable[[str, list[str]], str | None] | None = None,
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        """Run a fixed command (no shell), printed first (as `shown` when it holds a secret; `printed`: shown already,
        by `offer`). In a dry run only print it. One whose output setup reads (`capture_output`) asks nothing: the
        window keeps its screen. In the window the face runs it (`Face.run`): its output in the window's log, one in
        today's sudo form through macOS's own box (`why`: that box's line); `answer`: a question it asks on a marked
        line (setup_admin.ASK_MARK), answered on a window screen (Terminal: the command asks there itself). A Ctrl-C
        while it runs is the program's to handle: setup waits for it to end, then stops (`ctrl_c_held`; main says how
        to go on)."""
        line = "  $ " + " ".join(command if shown is None else shown)
        if printed:
            pass
        elif kwargs.get("capture_output"):
            self.command(line)
        else:
            self.say(line)
        if self.dry_run:
            return subprocess.CompletedProcess(command, 0, "", "")
        cwd = kwargs.get("cwd")
        given = kwargs.get("input")
        ran = self.face.run(
            command,
            input=given if isinstance(given, str) else None,
            capture=bool(kwargs.get("capture_output")),
            cwd=cwd if isinstance(cwd, Path) else None,
            why=why,
            answer=answer,
        )
        if ran is not None:
            return ran
        if hostsec.SUDO_GUARD is not None:  # an AI-run: sudo is never setup's own (setup_ai.guarded)
            hostsec.SUDO_GUARD(command)
        with ctrl_c_held() as pressed:
            done: subprocess.CompletedProcess[str] = subprocess.run(  # type: ignore[call-overload]
                command, text=True, check=False, **kwargs
            )
        if pressed:
            raise KeyboardInterrupt
        return done

    def offer(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        failed: str = "",
        why: str = "",
        window: Callable[[], int] | None = None,
        what: str = "",
    ) -> bool:
        """A command you would otherwise type (owner, 27 Oct: "pre-fill the command so that the user does not have to
        type"; a program cannot type into Terminal's command line): shown, then "Press Enter to run it (or s to
        skip)", and setup runs it. What was typed while the command before ran is dropped first (an Enter typed
        during brew's install never runs the next one). Enter (or y) runs it; s, skip, n, no, q or the end of input
        skips it; anything else says which key does what and asks again. In the window, its own screen: the command
        with Copy, Run it (recommended) or Skip. True when it ran and ended well; `failed` is said when it ran and did
        not, one line when it could not start (no such program); `why`: macOS's box's line, for one with admin rights;
        `window`: what the window runs in its place (its exit code; Terminal runs the command); `what`: what it does,
        the window's screen's first line. A dry run shows it and asks nothing."""
        self.command("  $ " + " ".join(command))
        if self.dry_run:
            self.say(prompts.SETUP_ASKS.format(ask=prompts.SETUP_PRESS_ENTER))
            return self.run(command, printed=True, cwd=cwd).returncode == 0
        if self.face.asking == "window":  # its own screen: the command with Copy, Run it or Skip (owner, 29 Oct)
            if not self.answer("offer_run", **setup_admin.offer_words(command, what)):
                self.say(prompts.SETUP_OFFER_SKIPPED)
                return False
            return self._offered(command, cwd=cwd, failed=failed, why=why, window=window)
        repeats = 0
        while True:
            self.flush()
            answer = self.face.line(prompts.SETUP_PRESS_ENTER)
            if answer is None or answer.lower() in SKIP_WORDS:
                self.say(prompts.SETUP_OFFER_SKIPPED)
                return False
            if answer.lower() in ("", "y", "yes"):
                return self._offered(command, cwd=cwd, failed=failed, why=why)
            self.say(prompts.SETUP_OFFER_HOW)
            repeats += 1
            if repeats >= REFUSED_MAX and not sys.stdin.isatty():  # a pipe, never a person: the end of input
                self.say(prompts.SETUP_OFFER_SKIPPED)
                return False

    def window_change(
        self, why: str, *, refused: str = "", failed: Callable[[str], None] | None = None
    ) -> Callable[[Sequence[str], bytes | None], int | None] | None:
        """A step's changes through the setup window while it is open (`Face.run`: its output in the log, admin rights
        through macOS's own box, `why` that box's line); None in Terminal, where the step runs them itself (sudo asks
        there). A change with input gets it on its stdin, its output not said. `refused`: the step's stop when macOS's
        box was refused, then Not now (nothing ran: never the stop of a command that failed). `failed`: given what
        such a change printed when it failed (review W23: her account's ✗ line, which Terminal shows, was lost)."""
        if not self.in_window:
            return None

        def change(argv: Sequence[str], input: bytes | None) -> int | None:
            given = input.decode() if input is not None else None
            ran = self.face.run(list(argv), input=given, capture=input is not None, cwd=None, why=why)
            if ran is None and hostsec.SUDO_GUARD is not None:  # an AI-run: the step's own sudo, never (setup_ai)
                hostsec.SUDO_GUARD(argv)
            if ran is not None and refused and ran.stderr == setup_admin.REFUSED:
                raise SystemExit(refused)
            if ran is not None and ran.returncode != 0 and input is not None and failed is not None:
                failed("\n".join(setup_admin.shown(line) for line in str(ran.stdout or "").splitlines()))
            return None if ran is None else ran.returncode

        return change

    def _offered(
        self, command: list[str], *, cwd: Path | None, failed: str, why: str, window: Callable[[], int] | None = None
    ) -> bool:
        """An offered command, run on the yes: True when it ended well; `failed` said when it did not. `window` runs in
        its place while the window is open (it closed meanwhile: the command, in Terminal)."""
        try:
            if window is not None and self.in_window:
                done = window() == 0
            else:
                done = self.run(command, printed=True, cwd=cwd, why=why).returncode == 0
        except OSError as exc:
            self.say(prompts.SETUP_OFFER_NOT_RUN.format(program=command[0], why=exc.strerror or exc))
            return False
        if not done and failed:
            self.say(failed)
        return done

    def registration(
        self, method: str, params: dict[str, object], shown: str, secrets: dict[str, str]
    ) -> harden.RpcAnswer:
        """register or verify: one JSON-RPC request on signal-cli's stdin, printed first with `shown` (placeholders
        for the secrets). (done, why not, the error's code); a secret signal-cli's error repeats shows as its
        placeholder."""
        argv = registration_argv(self.signal_cli)
        # what goes on its input, then the command on a line of its own (owner, 27 Oct: shorter lines)
        self.command(f"  {shown} on stdin:\n  $ " + " ".join(argv))
        if self.dry_run:
            return harden.RpcAnswer(True, "", None)
        self.say(prompts.SETUP_SIGNAL_WAITING.format(minutes=round(REGISTER_SECONDS / 60)))  # two silent minutes else
        return harden.json_rpc_answer(argv, method, params, secrets=secrets, timeout=REGISTER_SECONDS)

    # 0. Ollama's official build (owner, 15 Oct), before the model it runs
    def ollama(self) -> None:
        """Keep the official Ollama that answers, or install its pinned build, checked, always on (ollama_build);
        anything else stops a first install with the fix the person runs. A re-run where she is set up already
        (configured, or in her own user) shows that as "!" with its fix and goes on with the other steps, except an
        Ollama that someone other than you may control (`ollama_build.endangers`): that stops every run. Off the live
        Mac (a test's ESTIA_ROOT) it is only printed, as in a dry run."""
        memory = memory_gb()
        choice = choose_model(memory)
        if choice is None:
            self.say(prompts.SETUP_TOO_SMALL.format(memory=memory, name=self.name))
            raise SystemExit(1)
        tools = ollama_build.Tools(say=self.say)
        home = ollama_build.owner_home()  # never $HOME: root's service file runs the program under it at every start
        # the Ollama app, only when it is on this Mac and the person chose it (owner, 29 Oct); else the official build
        app = Path(self.mac.ollama_app) if self.mac.ollama_app and self.given("ollama_app") == "app" else None
        found = self.layout_found()
        # her own Ollama (owner, 29 Oct: "Her own, in _estia"): set up already, or a new install with her own account
        # (the plan's move: yes), the Ollama app tried first when chosen; anything else: today's step, as it was
        hers = found == "hers" or (
            found is None and ollama_build.layout_planned(None, app=False, move=self.given("move") is True) == "hers"
        )
        why = prompts.ADMIN_WHY_OLLAMA_HERS if hers else prompts.ADMIN_WHY_OLLAMA
        # in the window: macOS's box; a failed change's words kept for the step's stop (review W23)
        tools.changer = self.window_change(why, refused=prompts.OLLAMA_ADMIN_REFUSED, failed=tools.kept_failure)
        if hers:
            python = REPO / ".venv" / "bin" / "python"
            step = ollama_build.Install(
                tools,
                home,
                dry_run=self.dry_run or not tools.live,
                say=self.say,
                watch=lambda label: setup_progress.Watch(self.face, label),
                command=self.command,
                app=app if found is None else None,  # hers set up: it stays
                layout="hers",
                # her account by the checkout's own tools as root, only before she moved (then it is there)
                account=None
                if self.own_user()
                else [SUDO, str(python), "-I", "-m", "local_assistant.ownuser", "account"],
                model_bytes=0 if found == "hers" and self.mac.model_listed else choice.size,
                name=self.name,
            )
        else:
            step = ollama_build.Install(
                tools,
                home,
                dry_run=self.dry_run or not tools.live,
                say=self.say,
                watch=lambda label: setup_progress.Watch(self.face, label),  # its download's five dots
                command=self.command,  # its download asks nothing in Terminal: the window keeps its screen
                app=app,
                leftover=found is None,  # setup's service file then is an earlier try's: beside the app it goes
            )
        try:
            self.ollama_program = step.run(choice.num_ctx)
            self.use_base(step.base)
            app = app if step.used == "app" else None
            self.ollama_app = app
            if app is not None and self.ollama_app_asked and not self.first_install:  # her config records it
                if self.own_user():
                    self.her_config(prompts.SETUP_OLLAMA_APP_HERS, ("ollama-app",))
                else:
                    self.say(prompts.SETUP_OLLAMA_APP_CONFIG.format(path=self.config_path))
        except ollama_build.Refusal as refused:
            if self.first_install or refused.fatal:  # someone other than you may control it: every run stops
                raise
            self.say(ollama_build.warned(str(refused)))
            self.say(prompts.SETUP_OLLAMA_GOES_ON)
            self.ollama_refused = True

    def layout_found(self) -> ollama_build.Layout | None:
        """The layout this install has already (setup's service file, your config set up, the move), read only."""
        ollama, url = self.config_ollama or (None, None)
        return ollama_build.layout_found(estia_root(), config_ollama=ollama, config_url=url, moved=self.own_user())

    def use_base(self, base: str) -> None:
        """Ollama's address from here on; her model's list read again when it moved (the plan read it before)."""
        if base == self.ollama_base:
            return
        self.ollama_base = base
        if self.fixed_mac is not None:
            listed = self.listed_models()
            has_alias = any(item.get("name") in ("local-worker", "local-worker:latest") for item in listed)
            self.fixed_mac = dataclasses.replace(self.fixed_mac, model_listed=has_alias)

    # 1. the model
    def listed_models(self) -> list[dict[str, object]]:
        """Ollama's model list, read-only (the doctor's GET), so a dry run sees what a real run sees; empty when
        Ollama does not answer."""
        try:
            listed = doctor.Probe().get_json(f"{self.ollama_base}/api/tags").get("models", [])
        except (OSError, ValueError, AttributeError, TypeError):
            return []
        return [item for item in listed if isinstance(item, dict)] if isinstance(listed, list) else []

    def model(self) -> None:
        memory = memory_gb()
        listed = self.listed_models()
        alias = next((item for item in listed if item.get("name") in ("local-worker", "local-worker:latest")), None)
        if alias is not None:
            shown = self.shown("local-worker")
            if shown is None or not ollama_build.gets_small_prompt("local-worker", shown, alias):
                self.say("✓ Model: local-worker is there.")
                return
            self.remake(alias, shown, listed)
            return
        if self.ollama_refused:  # never a download through an Ollama setup refused
            self.say(prompts.SETUP_MODEL_AFTER_OLLAMA)
            return
        choice = choose_model(memory)
        if choice is None:
            self.say(prompts.SETUP_TOO_SMALL.format(memory=memory, name=self.name))
            raise SystemExit(1)
        self.say(f"Model: {choice.tag} for this Mac's {memory:.0f} GB ({choice.note}).")
        if not choice.tested:  # owner, 15 Oct: still picked, but said plainly and never downloaded without a yes
            self.answer("smaller_model")  # asked in the plan; a no stops setup there (`took`)
        self.say(prompts.SETUP_DOWNLOAD)
        why = self.pull(choice.tag, size=choice.size)
        if why and "no space" in why.lower():  # a full disk: named, never "run again to resume it" (round 1)
            raise SystemExit(prompts.SETUP_DISK_FULL.format(why=why))
        if why:
            raise SystemExit(prompts.SETUP_PULL_FAILED.format(why=why))
        got = "" if self.dry_run else self.listed_digest(choice.tag)
        if not self.dry_run and got != choice.digest:  # the tested build, by its digest: never one changed since
            raise SystemExit(
                prompts.SETUP_MODEL_OTHER_BUILD.format(
                    tag=choice.tag, got=got[:12] or "no digest", want=choice.digest[:12]
                )
            )
        spec = REPO / ".local-worker.Modelfile"
        if not self.dry_run:
            spec.write_text(modelfile(choice), encoding="utf-8")
        try:
            made = self.run(self.ollama_command("create", "local-worker", "-f", str(spec))).returncode == 0
        finally:
            if not self.dry_run:
                spec.unlink(missing_ok=True)
        if not made:
            raise SystemExit(prompts.SETUP_MODEL_NOT_MADE)
        done = "(dry run) would set" if self.dry_run else "✓ Model:"
        self.say(f"{done} local-worker = {choice.tag}, context {choice.num_ctx} tokens.")

    def ollama_command(self, *args: str) -> list[str]:
        """The ollama program setup runs (as you) for a model command, at the address in use: her own Ollama's by
        OLLAMA_HOST, the CLI's own variable (its default is 11434)."""
        if self.ollama_base == ollama_build.HER_BASE:
            return ["/usr/bin/env", f"OLLAMA_HOST=127.0.0.1:{ollama_build.HER_PORT}", self.ollama_program, *args]
        return [self.ollama_program, *args]

    def shown(self, name: str) -> dict[str, object] | None:
        """Ollama's /api/show answer for a model (read-only: its Modelfile, parameters and size); None when Ollama
        does not answer."""
        try:
            answer = doctor.Probe().post_json(f"{self.ollama_base}/api/show", {"model": name})
        except (OSError, ValueError):
            return None
        return answer if isinstance(answer, dict) else None

    def alias_base(
        self, alias: dict[str, object], shown: dict[str, object], listed: list[dict[str, object]]
    ) -> ModelChoice | None:
        """Which of setup's models local-worker is made from, only while that model is listed with its tested digest:
        the one whose digest it has (a copy, `ollama cp`), else the one it holds with at most a context of its own
        (`made_from`: what setup made before 0.9.3; that model's /api/show read, read-only). `shown`: the alias's own
        /api/show answer. None when it cannot be told."""
        choices = [choice for choice in (choose_model(32), choose_model(16)) if choice is not None]
        entries = {str(item.get("name")): item for item in listed}
        tested = [choice for choice in choices if _digest_of(entries.get(choice.tag)) == choice.digest]
        own = _digest_of(alias)
        copy = next((choice for choice in tested if own == choice.digest), None)
        if copy is not None:
            return copy
        for choice in tested:
            tag_shown = self.shown(choice.tag)
            if tag_shown is not None and made_from(alias, shown, entries[choice.tag], tag_shown):
                return choice
        return None

    def remake(self, alias: dict[str, object], shown: dict[str, object], listed: list[dict[str, object]]) -> None:
        """A re-run's alias with the small models' prompt (made before 0.9.3; empty answers, 17 Oct): said, and on a
        yes made again from the same model with its own context, so only the prompt changes. Its digest changes:
        the new one is printed with the pin's line. Never through an Ollama setup refused, never from a model setup
        cannot tell (the way by hand instead); a failed create leaves the old alias, and setup goes on."""
        self.say(prompts.SETUP_MODEL_SMALL_PROMPT)
        if self.ollama_refused:
            self.say(prompts.SETUP_MODEL_REMAKE_AFTER_OLLAMA)
            return
        base = self.alias_base(alias, shown, listed)
        if base is None:
            self.say(prompts.SETUP_MODEL_PROMPT_BY_HAND)
            return
        stop = prompts.SETUP_MODEL_STOP_HERS if self.own_user() else prompts.SETUP_MODEL_STOP_YOURS
        if not self.answer("remake", tag=base.tag, stop=stop):  # a dry run shows it and what a yes runs
            self.say(prompts.SETUP_MODEL_REMAKE_LATER)
            return
        hers = self.own_user()
        her_command = [SUDO, str(ownuser.BIN / "estia")]
        why = prompts.ADMIN_WHY_HER  # macOS's own box, in the window
        stopped = hers and self.offer([*her_command, "stop"], why=why)  # stopped first, on your Enter
        try:
            self._remade(base, shown, hers)
        finally:
            if stopped:  # and started again, whatever happened
                self.offer([*her_command, "start"], why=why)

    def _remade(self, base: ModelChoice, shown: dict[str, object], hers: bool) -> None:
        """local-worker made again from `base` with its own context; then its new digest and the pin's line (in her
        own user, her config opened as her on Enter)."""
        spec = REPO / ".local-worker.Modelfile"
        if not self.dry_run:
            spec.write_text(alias_modelfile(base.tag, context_of(str(shown.get("parameters") or ""))), encoding="utf-8")
        try:
            made = self.run(self.ollama_command("create", "local-worker", "-f", str(spec))).returncode == 0
        finally:
            if not self.dry_run:
                spec.unlink(missing_ok=True)
        if self.dry_run:
            return
        if not made:
            self.say(prompts.SETUP_MODEL_REMAKE_FAILED)
            return
        self.say(prompts.SETUP_MODEL_REMADE)
        digest = self.model_digest()
        if not digest:
            self.say(prompts.SETUP_MODEL_REMADE_UNLISTED)
            return
        where = prompts.SETUP_MODEL_PIN_HERS if hers else prompts.SETUP_MODEL_PIN_YOURS.format(path=self.config_path)
        self.say(prompts.SETUP_MODEL_REMADE_PIN.format(digest=digest, where=where))
        if hers and self.in_window:  # her config's pin, as her on a yes (only a model_digest line there changes)
            self.her_config("", ("model-digest", digest))
        elif hers:  # her config is hers alone: opened as her (s skips it, without model_digest in it)
            self.offer([SUDO, str(ownuser.BIN / "estia"), "config"])

    # 1b. her name (asked in the plan, so the steps after it use it)
    def her_name(self) -> None:
        """One question: Enter keeps Estia; a suggestion brings its voice; any other name that fits the config's rule
        asks for a female or male voice. A dry run only shows the question."""
        self.answer("name")
        if not self.dry_run and typed_name(self.known):
            self.answer("voice")

    # 4. the language, the config and the personality
    def language(self) -> str:
        """One of the 11 (Enter: the Mac's region's, else English); a dry run only shows the list."""
        chosen = self.answer("language")
        return chosen if isinstance(chosen, str) and chosen in CODES else ENGLISH

    def her_style(self) -> str:
        """Her built-in style while there is no soul.md: hearth (Enter) or playful, a name or its first letters; no
        terminal keeps hearth. A dry run only shows the question."""
        self.style_asked = True
        self.answer("style")
        return self.style

    def model_digest(self) -> str:
        """The digest Ollama lists for local-worker now (read-only); "" when it lists none."""
        return self.listed_digest("local-worker:latest")

    def listed_digest(self, name: str) -> str:
        """The digest Ollama lists for a model now (read-only; a name without a tag is :latest); "" when none."""
        names = {name, name.removesuffix(":latest")}
        try:
            listed = doctor.Probe().get_json(f"{self.ollama_base}/api/tags").get("models", [])
        except (OSError, ValueError, AttributeError, TypeError):
            return ""
        for item in listed if isinstance(listed, list) else []:
            if isinstance(item, dict) and item.get("name") in names:
                digest = str(item.get("digest") or "").lower().removeprefix("sha256:")
                return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else ""
        return ""

    def config(self, number: str, aci: str) -> None:
        """The config from the example, with the model's pin when Ollama lists the alias setup just made, and
        [projects] when the user asks for it. No soul.md: her built-in style is the default until the user writes
        one."""
        example = (REPO / "config.example.toml").read_text(encoding="utf-8")
        digest = self.model_digest()
        shown = self.shown("local-worker")  # the context it was made with (review I24); None: Ollama's default line
        context = context_of(str(shown.get("parameters") or "")) if shown else None
        language = self.language()
        style = self.her_style()
        projects = self.projects()
        text = render_config(
            example,
            number,
            aci,
            language,
            name=self.name,
            voice=self.voice,
            model_digest=digest,
            projects=projects,
            style=style,
            context_tokens=context,
            ollama="build" if self.ollama_app is None else "app",
            ollama_url=f"{self.ollama_base}/v1",  # her own Ollama's address, or 11434 (the example's)
        )
        self._write_private(self.config_path, self.extra_sections(text))
        if self.dry_run:
            self.say(f"(dry run) would write config {self.config_path} (mode 600, only you can read it).")
        else:
            self.say(f"✓ Config: {self.config_path} (only you can read it).")
        if digest:
            self.say(prompts.SETUP_PINNED)

    def personality(self) -> None:
        """Her style file, said at the end, once it is known where her files are: in her own user as her (her ~ is
        /var/estia; the move takes your folder), else in your config folder. No soul.md: her built-in style. A re-run
        in her own user asks the style here (the config step did not run) and prints the lines to add as her, and
        offers her config, only for a style given this run (one kept from before is in her config already)."""
        if self.own_user():
            if not self.style_asked:
                given = not self.answers.kept("style")  # asked this run (the plan, or here), not kept from before
                self.her_style()
                if self.dry_run:
                    self.say(prompts.SETUP_STYLE_HERS_DRY_RUN)
                elif given:
                    self.her_config(
                        prompts.SETUP_STYLE_HERS.format(style=self.style, save=prompts.NANO_SAVE), ("style", self.style)
                    )
            self.say(
                prompts.SETUP_PERSONALITY_HERS.format(
                    name=self.name,
                    save=prompts.NANO_SAVE,
                )
            )
            return
        soul = self.config_path.parent / "soul.md"
        if soul.exists():
            self.say(f"✓ Personality: {soul} (yours; edit it any time).")
        else:
            self.say(
                prompts.SETUP_PERSONALITY.format(
                    name=self.name, folder=soul.parent, style=self.style, words=prompts.STYLE_WORDS[self.style]
                )
            )

    def _write_private(self, path: Path, text: str) -> None:
        """Write a file only the user can read; a file already there is copied first, never just replaced."""
        if path.exists():
            self._keep_copy(path)
        if self.dry_run:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        path.chmod(0o600)

    def _keep_copy(self, path: Path) -> None:
        """Copy a file about to be replaced to <name>.bak-<time>, mode 600 (never over an earlier copy)."""
        copy = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        if self.dry_run:
            self.say(f"  (would keep a copy of {path} as {copy}, mode 600)")
            return
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(path.read_bytes())
        copy.chmod(0o600)
        self.say(f"Kept a copy of the old {path.name}: {copy} (only you can read it).")

    # 5. always on: in her own user
    def own_user(self) -> bool:
        """She runs as her own user: the bridge's service file says so (after a rollback it names you again)."""
        return ownuser.moved(estia_root())

    def always_on(self) -> bool:
        """Her own user: done already, the move (on a yes), or skipped. False only when the move failed: then setup
        stops with `move_stop`, before the security step and the extras, drawn as step 1's stops are (the window's
        stop screen; stops-reruns-move-failed). The move's script ends `SHARE_FAILED` (3) only when its last
        step, the projects share, fails after the move: she runs in her own user then, so setup says so, with the
        share's command, and goes on. Any other failure is the move's, even with her service file left in place (a
        restore that failed too)."""
        if self.own_user():
            self.say(f"✓ Always on: as her own macOS account {ownuser.ROLE_USER}, from {ownuser.CURRENT}.")
            return True
        move = str(REPO / "scripts" / "migrate-to-own-user.sh")
        # Enter moves her (the safe answer), except over SSH without screen, where the question's lines say no
        if self.dry_run:  # a dry run shows the question with the command a yes runs
            self.answer("move")
            return True
        if not self.answer("move"):  # a no: said now, and in the closing list (answers-extras-move-no-unsaid)
            self.say(prompts.SETUP_MOVE_SKIPPED)
            self.for_terminal = (*self.for_terminal, prompts.SETUP_MOVE_NOT_RUNNING)
            return True
        if not self.first_install and not self.her_signal_here():  # before macOS's box: the move would refuse it
            self.move_stop = prompts.SETUP_MOVE_NO_SIGNAL
            return False
        code = self.move_in_window() if self.in_window else self.run([move]).returncode
        if code == MOVE_REFUSED:  # the window's box refused, then Not now: nothing ran (review ADM-11)
            self.move_stop = prompts.SETUP_MOVE_REFUSED
            return False
        if code != 0:
            if code != SHARE_FAILED or not self.own_user():
                self.move_stop = prompts.SETUP_MOVE_FAILED.format(move=move)
                return False
            self.say(
                prompts.SETUP_SHARE_FAILED.format(
                    name=self.name, user=ownuser.ROLE_USER, share=REPO / "scripts" / "share-projects.sh"
                )
            )
        if self.own_user():  # moved: a kept yes would move her again, unasked, at a run after a rollback (MAC-4)
            self.answers.drop("move")
        return True

    def her_signal_here(self) -> bool:
        """Her config's Signal account is verified in your signal-cli folder, which the move takes to her (c-5, round
        3: a set-up install runs no Signal step, so the move's own refusal was a dead end). No number: the move says
        what is missing itself."""
        number = ownuser.bot_number(self.config_path)
        return number is None or ownuser.has_account(Path.home() / ".local/share/signal-cli", number)

    def move_in_window(self) -> int:
        """The move as scripts/migrate-to-own-user.sh runs it, in the window: the move itself as root (the checkout's
        own Python, `ownuser migrate`; macOS's own box asks once, and root learns who asked from the window, as from
        sudo), its lines in the window's log; then, as you, the projects share in-process, its close question a
        window screen. The script's exit code: 0, 1 (the move failed), SHARE_FAILED (only the share failed), or
        MOVE_REFUSED (macOS's box refused, then Not now: nothing ran)."""
        python = REPO / ".venv" / "bin" / "python"
        if ownuser.moved(hostsec.tool_root()):  # the script's guard, by the tools' root: never an ESTIA_ROOT's (ADM-7)
            self.say(prompts.SETUP_ALREADY_MOVED)
            return 0
        if not python.exists():
            self.say(prompts.SETUP_NO_VENV.format(repo=REPO))
            return 1
        argv = [SUDO, str(python), "-I", "-m", "local_assistant.ownuser", "migrate", "--checkout", str(REPO)]
        moved = self.run(argv, why=prompts.ADMIN_WHY_MOVE)
        if moved.returncode != 0:
            return MOVE_REFUSED if moved.stderr == setup_admin.REFUSED else 1
        marker = hostsec.tool_root() / "usr/local/estia/migration.json"  # own-user.sh's, as the script passes it
        done = share.main(["--marker", str(marker), "--after-move"], ask=self.share_ask(), say=self.say)
        return 0 if done == 0 else SHARE_FAILED

    # 5b. the Mac's security around her, then the phone
    def security(self) -> None:
        harden_sh = str(REPO / "scripts" / "harden.sh")
        if self.answer("security") and not self.dry_run:  # a dry run shows the question with the command
            # it ends with the phone checklist; one that did not finish is said, and named again at the end
            code = self.harden_in_window() if self.in_window else self.run([harden_sh]).returncode
            if code != 0:
                self.say(prompts.SETUP_SECURITY_FAILED)
                self.for_terminal = (*self.for_terminal, prompts.SETUP_SECURITY_FAILED)
            return
        if not self.dry_run:
            self.say(f"  Later: {harden_sh}")
        for line in harden.PHONE_CHECKLIST:
            self.say(line)

    def harden_in_window(self) -> int:
        """The security step as scripts/harden.sh runs it, inside setup with the window as its face (owner, 29 Oct):
        each question a window screen, the PIN on the window's PIN screens, its sudo commands through the window
        (macOS's own box asks, "Admin rights for the security step's changes you said yes to."), its lines in the
        log. Once the window closes, the rest goes on in Terminal as harden.sh would (sudo asks there). Its exit code:
        a change that failed is said in its own words, never a traceback."""
        face = self.face
        if not isinstance(face, WindowFace):  # never: the window asks only while it is open
            return self.run([str(REPO / "scripts" / "harden.sh")]).returncode
        host = setup_admin.WindowHost(face, estia_root(), say=self.say, why=prompts.ADMIN_WHY_SECURITY)
        me = pwd.getpwuid(os.getuid())
        step = harden.Harden(
            host,
            owner=me.pw_name,
            owner_home=Path(os.environ.get("HOME") or me.pw_dir),
            own_user=harden.own_user_installed(host),
            ask=self.harden_ask(),
            hidden=self.harden_hidden(),
            say=self.say,
            window=host.window_open,
            held=host.stops_held,
        )
        try:
            return step.run()
        except (RuntimeError, OSError) as failed:  # what harden.sh's own traceback would have said
            self.say(f"✗ Security: {failed}")
            return 1

    def harden_ask(self) -> Callable[[str, bool], bool]:
        """The security step's yes or no on a window screen: its words without Terminal's recommendation line (the
        window's pill marks it), its question the last line; recommended yes where Enter takes yes, else none."""

        def ask(text: str, default: bool) -> bool:
            lines = [line for line in text.splitlines() if not line.startswith("Yes (recommended")]
            q = Question(
                "harden",
                "yes_no",
                question("security").title,
                lead="\n".join(lines[:-1]),
                ask=lines[-1] if lines else text,
                recommended="yes" if default else None,
                keep="step",
                plan=False,
            )
            self.face.show(q, None)
            yes = self.face.confirm(q, default) is True
            self.face.checked(q, Checked(yes))  # the window leaves its screen for the step's log (review W03)
            return yes

        return ask

    def harden_hidden(self) -> Callable[[str], str]:
        """The security step's PIN, typed twice on the window's own PIN screens (the Signal step's): nothing typed
        skips it, as q in Terminal."""

        def hidden(prompt: str) -> str:
            given = self.answer("lock_pin" if prompt == harden.PIN_ASK else "lock_pin_again")
            return given if isinstance(given, str) and given.strip() else "q"

        return hidden

    def voice_health(self) -> ownuser.VoiceHealth:
        """The voice service's health (127.0.0.1 only; never the running service under a test's ESTIA_ROOT); in her own
        user read as her (her voice service answers only her own account), on the admin sign-in of the check before:
        `ownuser.VoiceNotRead` when none is running."""
        if self.in_window and self.own_user() and estia_root() == Path("/"):  # as her, through the window
            found = ownuser.her_voice_health(run=self._read_as_her)
        else:
            found = ownuser.voice_health(estia_root() == Path("/"), as_her=self.own_user())
        return dict(found) if isinstance(found, Mapping) else found

    def _read_as_her(self, argv: Sequence[str]) -> tuple[int, str]:
        """A fixed command as her, its output read (her voice's health): through the window, root running sudo -n -u
        _estia (no password); admin rights not given count as sudo's own "a password is required" (1)."""
        done = self.run(list(argv), capture_output=True, why=prompts.ADMIN_WHY_HER)
        return done.returncode, str(done.stdout or "")

    def voice_end(self, *, calls: bool = False) -> None:
        """The last word on her voice when its service is installed: ready, or when calls work (a first install, a
        move or a macOS update compiles it again). A text is promised only with `calls` ([calls] in the config setup
        read; her own user's config is hers alone, so a re-run there promises none)."""
        plist = estia_root() / ownuser.DAEMONS.relative_to("/") / f"{ownuser.VOICE}.plist"
        if not self.dry_run and plist.exists():
            line = ownuser.voice_end(
                self.voice_health, calls=calls, clock=self.clock, sleep=self.sleep, say=self.say, moved=self.own_user()
            )
            self.say(line)

    def check(self) -> int:
        """Her doctor, its exit code; one that found a ✗ is named again in the closing list (check_failed)."""
        if self.own_user():  # her config and data are hers alone: read through her wrapper, by its fixed path
            self.say(prompts.SETUP_CHECK_HERS_LEAD if self.in_window else prompts.SETUP_CHECK_HERS)
            ran = self.run([SUDO, str(ownuser.BIN / "estia"), "doctor"], why=prompts.ADMIN_WHY_HER)
            code = ran.returncode
            self.check_not_run = ran.stderr == setup_admin.REFUSED  # nothing ran: no ✗ line to point at
        else:
            findings = doctor.run(self.config_path, doctor.Probe())
            self.say(doctor.render(findings))
            code = doctor.exit_code(findings)
        self.check_failed = code != 0 and not self.dry_run
        return code


class Stopped(SystemExit):
    """A stop the face has said already: exit status 1, never printed again by Python; its words stay its str."""

    def __init__(self, words: str) -> None:
        super().__init__(1)
        self.words = words

    def __str__(self) -> str:
        return self.words


def _hung_up(signum: int, frame: object) -> None:
    """The Terminal window closed (macOS's hang-up, stops-reruns-terminal-closed): Ctrl-C's way out, so the
    terminal's mode, the answers file's lock, the Mac's wake and the window are put back; nothing else is said."""
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    """Setup; Ctrl-C at any point ends it with one line on how to go on (130, as the shell reports Ctrl-C), and so
    does the Terminal window closed (`_hung_up`; its line then goes nowhere, and is not forced). Without a terminal
    (an AI's command tool) and not a dry run: one line where it cannot run (exit 2), else the AI-run, detached from the
    AI's command (setup_ai: decided here, before any thread or anything of macOS's frameworks runs; no Terminal window
    to close, so no hang-up handler)."""
    args = parser().parse_args(argv)
    if not args.dry_run and not has_terminal():
        why = setup_ai.refused(args, config=DEFAULT_CONFIG_PATH)
        if why:
            print(why, flush=True)
            return setup_ai.REFUSED
        return setup_ai.detach(lambda channel: ai_run(argv, channel))
    main_thread = threading.current_thread() is threading.main_thread()  # a handler is set only there
    before = signal.signal(signal.SIGHUP, _hung_up) if main_thread else None
    try:
        return run_setup(argv)
    except KeyboardInterrupt:
        with contextlib.suppress(OSError, ValueError):  # Terminal closed: nowhere to print
            print(f"\n{prompts.SETUP_STOPPED}", flush=True)
        return 130
    finally:
        if main_thread and before is not None:
            signal.signal(signal.SIGHUP, before)


def ai_run(argv: list[str] | None, channel: setup_ai.Channel) -> int:
    """The AI-run in this process (main's detached grandchild; the tests call it as it is): setup with the window as
    its only face, its one end point and its guards (setup_ai.run)."""
    ai = setup_ai.Run(channel)
    return setup_ai.run(ai, lambda: run_setup(argv, ai=ai))


def parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Set the assistant up step by step (safe to run again).")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH, help="where the config goes")
    parser.add_argument(
        "--extras",
        action="store_true",
        help="show the extras and how to add them (projects: asks the folder and the level and adds [projects])",
    )
    parser.add_argument("--dry-run", action="store_true", help="print the steps and commands without running them")
    parser.add_argument(
        "--redo",
        action="append",
        default=[],
        metavar="ID",
        help="ask one kept answer again (repeatable; signal: her Signal account's answers)",
    )
    parser.add_argument(
        "--answers", type=Path, metavar="FILE", help="take the answers saved by another run (each checked again)"
    )
    parser.add_argument("--plain", action="store_true", help="plain lines, one per question (no menus)")
    parser.add_argument("--terminal", action="store_true", help="ask in Terminal, never in the window")
    return parser


def run_setup(argv: list[str] | None = None, *, ai: setup_ai.Run | None = None) -> int:
    """Setup in this process: in Terminal (gum's menus, plain lines, or the window at the desk), or with `ai`, an
    AI-run, whose only face is the window (main and setup_ai.refused checked first that it can open here)."""
    args = parser().parse_args(argv)
    wizard = Wizard(args.config, dry_run=args.dry_run, face=setup_ai.StopFace(ai) if ai is not None else None)
    if ai is not None:
        wizard.ai, ai.wizard = ai, wizard
    if os.geteuid() == 0:  # root's setup would make setup's own folders root's: every run of yours then refuses them
        wizard.say(prompts.SETUP_NOT_ROOT)
        if ai is not None:
            ai.ending = prompts.SETUP_NOT_ROOT
        return 2
    no_terminal = not args.dry_run and not args.extras and not has_terminal()
    if ai is None and no_terminal:  # called without main's route: it says where to run it
        wizard.say(prompts.SETUP_NO_TERMINAL)
        return 1
    if ai is not None and (why := setup_ai.refused(args, config=DEFAULT_CONFIG_PATH, desk=False)):
        ai.ending = why
        return setup_ai.REFUSED
    wizard.fixed_mac = Mac.read(memory_gb=memory_gb(), own_user=wizard.own_user())
    if args.extras:
        wizard.extras()
        wizard.restart_her()  # once, when her config was opened as her
        return 0
    if args.answers is not None and not os.path.lexists(args.answers):
        wizard.say(prompts.SETUP_ANSWERS_NONE.format(path=args.answers))
        return 2
    known = saved_ids()
    # "signal" names her Signal account's answers at once (owner, 27 Oct: "start again with setup --redo signal")
    args.redo = [qid for given in args.redo for qid in (SIGNAL_REDO if given == "signal" else (given,))]
    unknown = [qid for qid in args.redo if qid not in known and qid not in {q.id for q in QUESTIONS}]
    if unknown:
        listed = ", ".join(q.id for q in QUESTIONS if q.id in known)
        for qid in unknown:
            wizard.say(prompts.SETUP_NO_QUESTION.format(id=qid, ids=listed))
        if ai is not None:  # never the id as typed: the questions' own ids only
            ai.ending = prompts.SETUP_AI_NO_QUESTION.format(ids=listed)
        return 2
    answers = Answers.memory() if args.dry_run else Answers.load(setup_answers.default_path(), say=wizard.say)
    if ai is not None and answers.path is None:  # refused: nothing of this run would be kept, and no one told
        ai.ending = prompts.SETUP_AI_ANSWERS_REFUSED
        return 1
    try:
        # the Mac kept awake for the whole run (plat-09): the builds and the voice wait too, not the download alone
        with answers.lock(), contextlib.nullcontext() if args.dry_run else setup_progress.awake():
            wizard.answers = answers
            if ai is not None and answers.path is not None:  # this run's log, now that no other run holds it
                ai.channel.open_log(answers.path.with_name(setup_ai.LOG))
            return _run(wizard, args)
    except AnswersInUse:
        wizard.say(prompts.SETUP_ANSWERS_IN_USE)
        if ai is not None:
            ai.ending = prompts.SETUP_AI_IN_USE
        return 1
    except SystemExit as stop:
        if not isinstance(stop.code, str):
            raise
        # a step's ✗ and its fix (owner's laptop, 29 Oct: one long line in Terminal behind the window, which only
        # said "Setup ended in Terminal."): said by the face, in Terminal with space and in the window as its own screen
        wizard.face.stopped(stop.code)
        raise Stopped(stop.code) from None
    # a file setup cannot use (a root-owned ~/.zprofile, a read-only folder): a stop with the path, never a traceback
    except OSError as exc:
        words = os_error_stop(exc)
        with contextlib.suppress(OSError):  # a closed terminal cannot show it: the exit status still says so
            wizard.face.stopped(words)
        raise Stopped(words) from None


def os_error_stop(exc: OSError) -> str:
    """A file error no step handled, as a stop (mac-ollama-home-unwritable-crash): the path and the error, with the
    fix; without a path, the error alone. A full disk (ENOSPC, or the quota: EDQUOT) gets setup's storage fix (fix
    review, dialogue-04); Finder's Read and Write only for a file of your own home your account may not use, with the
    key that shows a dot name (Finder hides ~/.zprofile); any other path or error (her folders, a missing file) is
    run again, then reported (review of 30 Oct, P07)."""
    error = exc.strerror or exc.__class__.__name__
    if exc.errno in (errno.ENOSPC, errno.EDQUOT):
        at = "" if exc.filename is None else f": {os.fsdecode(exc.filename)}"
        return prompts.SETUP_OS_ERROR_FULL.format(error=error, at=at)
    if exc.filename is None:
        return prompts.SETUP_OS_ERROR_NO_PATH.format(error=error)
    path = os.fsdecode(exc.filename)
    home = Path(os.path.normpath(Path.home()))
    where = Path(os.path.normpath(os.path.abspath(path)))
    if exc.errno not in (errno.EACCES, errno.EPERM) or not where.is_relative_to(home):
        return prompts.SETUP_OS_ERROR_OTHER.format(path=path, error=error)
    dotted = any(part.startswith(".") for part in where.relative_to(home).parts)
    hidden = prompts.SETUP_OS_ERROR_HIDDEN if dotted else ""
    return prompts.SETUP_OS_ERROR.format(path=path, error=error, hidden=hidden)


def _run(wizard: Wizard, args: argparse.Namespace) -> int:
    """The run itself, with the answers file held: the plan first (phase A), then the steps (phase B)."""
    if wizard.own_user():  # a re-run: never registers again, never rewrites her config
        wizard.say(
            f"Setup: she runs as her own macOS account {ownuser.ROLE_USER}; her config and data are readable only by that user,\n"
            "so setup reads them through /usr/bin/sudo /usr/local/estia/bin/estia and never writes them.\n"
        )
        wizard.first_install = False
        if _too_small(wizard, args):
            return 1
        if not _the_plan(wizard, args, configured=True):
            return 0
        wizard.plan_steps(configured=True)
        _disk_short(wizard, args)
        wizard.begin("ollama")
        wizard.ollama()
        wizard.begin("model", wizard.model_download())
        wizard.model()
        wizard.say(f"✓ Config, her Signal account and yours: in her own macOS account ({ownuser.HOME}).")
        wizard.begin("always_on")
        wizard.always_on()
        wizard.begin("security")
        wizard.security()
        wizard.begin("calls", prompts.SETUP_STEP_CALLS)
        wizard.calls_step()
        wizard.begin("extras")
        wizard.extras_setup()
        wizard.personality()
        wizard.restart_her()  # once, when her config was opened as her
        wizard.begin("check")
        result = wizard.check()
        wizard.voice_end()
        wizard.closing()
        wizard.finished = True
        return 0 if args.dry_run else result
    cfg: Config | None = None
    problem: ConfigError | None = None
    if args.config.exists():  # a config with a mistake is the user's work: say what is wrong, never replace it
        try:
            cfg = load_config(args.config)
            wizard.name = cfg.name  # her name from here on (the banner, the steps, the last line)
            wizard.style = cfg.personality_style
        except ConfigError as exc:
            problem = exc
    wizard.say(prompts.SETUP_OPENING.format(name=wizard.name))
    if problem is not None:
        wizard.say(f"✗ Config: {problem}")
        wizard.say(
            "  Setup stops here and changes nothing, so your config is not replaced. Fix it\n"
            "  (scripts/doctor.sh checks it), then run setup again."
        )
        if wizard.ai is not None:  # never the problem's words: they may quote a value of the config
            wizard.ai.ending = prompts.SETUP_AI_CONFIG
        return 1
    configured = cfg is not None and cfg.bot_number != PLACEHOLDER_NUMBER and PLACEHOLDER_ACI not in cfg.allowed_acis
    wizard.first_install = not configured
    if cfg is not None and configured:  # the layout's rule 2: an install set up keeps its own Ollama
        wizard.config_ollama = (cfg.ollama, cfg.ollama_base_url)
    if _too_small(wizard, args):
        return 1
    if not _the_plan(wizard, args, configured=configured):
        return 0
    wizard.plan_steps(configured=configured)
    _plan_stops(wizard, args, configured=configured)
    _disk_short(wizard, args)
    wizard.steps_time()
    wizard.begin("ollama")
    wizard.ollama()
    wizard.begin("model", wizard.model_download())
    wizard.model()
    if configured:
        wizard.say(f"✓ Config: {args.config} is set up (her Signal account and yours).")
    else:
        wizard.begin("signal")
        number = wizard.signal_account()
        aci = wizard.owner(number)
        wizard.begin("config")
        wizard.config(number, aci)
        wizard.hand_over()  # before the move, which takes the config folder to her
    wizard.begin("always_on")
    if not wizard.always_on():
        raise SystemExit(wizard.move_stop)  # the move failed: a stop, before the security step and the extras
    wizard.begin("security")
    wizard.security()
    wizard.begin("calls", prompts.SETUP_STEP_CALLS if wizard.own_user() else "")  # its builds only in her account
    wizard.calls_step()
    wizard.begin("extras")
    wizard.extras_setup()
    wizard.personality()
    wizard.restart_her()  # once, when her config was opened as her
    wizard.begin("check")
    time.sleep(0.2)
    result = wizard.check()
    wizard.voice_end(calls=cfg is not None and cfg.calls_enabled)  # a config setup just wrote has no [calls]
    wizard.closing()
    wizard.finished = True
    return 0 if args.dry_run else result  # a dry run reports, it does not fail


def _plan_stops(wizard: Wizard, args: argparse.Namespace, *, configured: bool) -> None:
    """What the plan's answers stop before step 1, so nothing downloads into a layout the stop's own fix throws away:
    a set-up config whose Signal account is gone, on the move's yes (c-16, confirm-2: the stop came at always on,
    after her model, and its fix made the next run a first install that downloaded it again); a first install's no to
    the move over a remote login outside screen, whose lines say to answer no and run setup again inside screen
    (c-12, confirm-2: the run went on with today's layout, which refuses the Ollama app, and kept that layout for the
    run inside screen). A dry run stops at nothing here."""
    if args.dry_run:
        return
    if configured and wizard.given("move") is True and not wizard.her_signal_here():
        raise SystemExit(prompts.SETUP_MOVE_NO_SIGNAL)
    if not configured and wizard.given("move") is False and unkept_ssh(wizard.mac):
        raise SystemExit(prompts.SETUP_SSH_SCREEN_STOP)


def _too_small(wizard: Wizard, args: argparse.Namespace) -> bool:
    """A Mac below 15 GB of memory, said before the plan's first question (review setup-small-mac-after-plan: it was
    said only at the Ollama step, after every question); a dry run shows the plan first, and its Ollama step says so."""
    memory = memory_gb()
    if args.dry_run or choose_model(memory) is not None:
        return False
    wizard.say(prompts.SETUP_TOO_SMALL.format(memory=memory, name=wizard.name))
    if wizard.ai is not None:  # never her name: "She"
        wizard.ai.ending = prompts.SETUP_TOO_SMALL.format(memory=memory, name="She")
    return True


OLLAMA_GB = 1.0  # Ollama's build (168 MB) unpacked, with room to spare
CALLS_GB = 7.0  # the voice models (about 1.7 GB) and the call tools' build (several GB, not measured)


def her_own_planned(wizard: Wizard) -> bool:
    """A first install whose Ollama step installs her own Ollama (the plan's move: yes; the Ollama app not chosen)."""
    app = bool(wizard.mac.ollama_app) and wizard.given("ollama_app") == "app"
    found = wizard.layout_found()
    return found is None and ollama_build.layout_planned(None, app=app, move=wizard.given("move") is True) == "hers"


def disk_needed(wizard: Wizard) -> tuple[float, str]:
    """The space this run's steps need, in GB as Finder counts them, and what: her model (unless Ollama lists
    local-worker), Ollama's build (her own: the room step 1 checks, `ollama_build.HER_ROOM`), and calls on the plan's
    yes."""
    parts: list[tuple[float, str]] = []
    choice = choose_model(wizard.mac.memory_gb)
    hers = her_own_planned(wizard)  # her store is empty, whatever 11434 lists (c-7, round 3)
    if choice is not None and (hers or not wizard.mac.model_listed):
        parts.append((choice.size / 1e9, f"her model {choice.size / 1e9:.1f} GB"))
    if hers:
        room = ollama_build.HER_ROOM / 1e9
        parts.append((room, f"her Ollama {room:.0f} GB"))
    else:
        parts.append((OLLAMA_GB, f"Ollama {OLLAMA_GB:.0f} GB"))
    if wizard.given("calls") == "yes":
        parts.append((CALLS_GB, f"calls about {CALLS_GB:.0f} GB"))
    return sum(size for size, _what in parts), ", ".join(what for _size, what in parts)


def _disk_short(wizard: Wizard, args: argparse.Namespace) -> None:
    """Too little free disk for the steps picked (mac-ollama-disk-full, round 1): said right after the plan, before
    anything is downloaded, with how much to free. A dry run, and a Mac whose free disk could not be read, check
    nothing (a download that fills the disk still says so, `SETUP_DISK_FULL`)."""
    free = wizard.mac.free_gb
    if args.dry_run or free is None:
        return
    need, what = disk_needed(wizard)
    if free >= need:
        return
    total, shown = math.ceil(need), math.floor(free)
    raise SystemExit(prompts.SETUP_DISK_SHORT.format(free=shown, need=total, what=what, more=total - shown))


def _nothing_to_redo(wizard: Wizard, args: argparse.Namespace, *, configured: bool) -> bool:
    """On an install set up, a --redo that names only what changes nothing there (her Signal account, in her config;
    About you and the home area, handed over once): its lines, then setup ends, never a whole re-run with the Mac
    password (answers-extras-redo-handed-full-rerun, round 1). Said before the window opens: it has nothing to ask.
    An AI-run's face drops them there, so they are its end line too (setup's fixed words and the questions' titles)."""
    unchanged = set(SIGNAL_REDO) | set(setup_answers.HANDED_OVER)
    if not configured or not args.redo or not set(args.redo) <= unchanged:
        return False
    lines = [prompts.SETUP_REDO_SIGNAL] if set(args.redo) & set(SIGNAL_REDO) else []
    lines += [
        prompts.SETUP_REDO_PROFILE.format(title=question(qid).title)
        for qid in setup_answers.HANDED_OVER
        if qid in args.redo
    ]
    lines.append(prompts.SETUP_REDO_NOTHING)
    for line in lines:
        wizard.say(line)
    if wizard.ai is not None:
        wizard.ai.ending = " ".join(lines)
    return True


def _the_plan(wizard: Wizard, args: argparse.Namespace, *, configured: bool) -> bool:
    """The Mac's answers, read once; the answers kept from an earlier run (or --answers' file) checked again, those
    named by --redo dropped; then every question of the plan. False: nothing to do (`_nothing_to_redo`)."""
    if _nothing_to_redo(wizard, args, configured=configured):
        return False
    found = wizard.layout_found()
    wizard.ollama_base = ollama_build.base_of(found or "older")  # her own address once set up
    listed = wizard.listed_models()
    has_alias = any(item.get("name") in ("local-worker", "local-worker:latest") for item in listed)
    wizard.fixed_mac = Mac.read(
        memory_gb=memory_gb(),
        own_user=wizard.own_user(),
        configured=configured,
        model_listed=has_alias,
        fresh_hers=found is None and ollama_build.NEW_INSTALLS == "hers",
    )
    wizard.face = window_face(args, wizard.mac, wizard) or choose_face(args, wizard.mac, wizard)  # how setup asks
    if args.answers is not None:
        given = Answers.load(args.answers, say=wizard.say, writable=False)
        given.recheck(QUESTIONS, wizard.mac, say=wizard.say)
        wizard.answers.adopt(given)
    wizard.answers.recheck(QUESTIONS, wizard.mac, say=wizard.say)
    wizard.redo = frozenset(args.redo)
    if configured and set(args.redo) & set(SIGNAL_REDO):  # her account is in her config (redo-signal-configured-noop)
        wizard.say(prompts.SETUP_REDO_SIGNAL)
    for qid in args.redo:
        if configured and qid in SIGNAL_REDO:  # no Signal step runs on an install set up: the answers stay
            continue
        if qid == "ollama_app" and wizard.mac.her_ollama:  # her own Ollama stays (0.9.8 switches nothing)
            wizard.say(prompts.SETUP_OLLAMA_APP_REDO_HERS)
            continue
        # About you and the home area reach her once, at her first start: on an install set up, or once handed over,
        # a new answer would never reach her profile, so the mark stays and /profile is named (Fable, 27 Oct)
        if qid in setup_answers.HANDED_OVER and (configured or wizard.answers.get(qid) == setup_answers.HANDED):
            wizard.say(prompts.SETUP_REDO_PROFILE.format(title=question(qid).title))
            continue
        wizard.answers.drop(qid)
    wizard.plan()
    if wizard.answers.path is not None and not args.dry_run:
        wizard.say(prompts.SETUP_ANSWERS_WHERE.format(path=wizard.answers.path))
    return True


if __name__ == "__main__":
    sys.exit(main())
