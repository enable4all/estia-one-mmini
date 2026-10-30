"""Setup's answers file, `~/.config/estia-setup/answers.toml`: each answer saved as it is given, so a stopped setup
goes on where it was and a re-run says "Kept: …". Setup's own folder, never in the move's (`ownuser.DATA_MAP` takes
`~/.config/local-assistant` to her and renames yours aside), so it is there for the next run and for the rollback.

Read back as untrusted: a regular file of yours alone, not a link, at most 64 KB, valid TOML of this format;
otherwise one line, no answers, and the file left as it is. Only questions that keep their answer ("saved") can be
set: no captcha link, code, PIN, key or password ever reaches the file. Nothing in it is ever run."""

from __future__ import annotations

import contextlib
import fcntl
import os
import secrets
import stat
import tomllib
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from local_assistant import prompts
from local_assistant.setup_questions import Question, Value

if TYPE_CHECKING:
    from local_assistant.setup_plan import Mac

FORMAT = 1
MAX_BYTES = 64 * 1024
HEADER = "# Estia setup: your answers so far. No captcha link, code, PIN, key or password is ever kept here.\n"
HANDED_OVER = ("about_you", "home_area")  # copied to her config folder before the move (setup_handover)
# what the file keeps of them once handed over (reviews SEC-2, SEC-10): never a second copy; a re-run shows it as kept
HANDED = "handed over to her"
# the steps a finished install marks as done, with the time (stops-reruns-rerun-not-quiet): a re-run skips them and
# says so in one line; `--redo` of the question of the same id forgets the mark
DONE_STEPS = frozenset({"calls"})  # her config's calls lines, then her restart after them
# each Extra Feature setup went through, as "extra_" and its name (stops-reruns-extras-pick-lost): a re-run after a
# stop offers the picks not gone through
DONE_EXTRA = "extra_"
_BARE = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def markable(step: str) -> bool:
    """A step the file marks as done: one of DONE_STEPS, or an Extra Feature's mark (a bare key, written as it is)."""
    extra = step.removeprefix(DONE_EXTRA)
    return step in DONE_STEPS or (step.startswith(DONE_EXTRA) and bool(extra) and set(extra) <= _BARE)


_SHORT = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


class AnswersInUse(Exception):
    """Another setup holds the answers file's lock."""


def default_path() -> Path:
    """Setup's own answers file (tests point it at a folder of their own: tests/conftest.py)."""
    return Path.home() / ".config" / "estia-setup" / "answers.toml"


def saved_ids() -> frozenset[str]:
    """The ids whose answer the file keeps: every question of the list that keeps "saved"."""
    from local_assistant.setup_plan import QUESTIONS  # setup_plan reads every step's questions

    return frozenset(q.id for q in QUESTIONS if q.keep == "saved" and not q.is_secret)


def _uid() -> int:
    return os.getuid()


def _string(text: str) -> str:
    """A TOML basic string: quotes, backslashes and every control character escaped; no lone surrogate."""
    out = []
    for char in text:
        if char in _SHORT:
            out.append(_SHORT[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04X}")
        elif 0xD800 <= ord(char) <= 0xDFFF:
            raise ValueError("a lone surrogate cannot be written")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def _when(moment: datetime) -> str:
    """When an answer was given, as the file keeps it: a TOML date and time in UTC (one with no zone is taken as
    UTC)."""
    utc = moment.astimezone(UTC) if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def _key(key: str) -> str:
    return key if key and set(key) <= _BARE else _string(key)


def _toml(value: Value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, tuple):
        return "[" + ", ".join(_string(item) for item in value) + "]"
    return "{ " + ", ".join(f"{_key(k)} = {_string(v)}" for k, v in value.items()) + " }" if value else "{}"


def _value(raw: object) -> Value | None:
    """A value as the file may hold it: a string, a bool, a list of strings or a table of strings; None otherwise."""
    if isinstance(raw, str | bool):
        return raw
    if isinstance(raw, list) and all(isinstance(item, str) for item in raw):
        return tuple(raw)
    if isinstance(raw, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        return dict(raw)
    return None


def _read(path: Path) -> tuple[dict[str, object] | None, str]:
    """The file's TOML as untrusted (no link followed, no FIFO waited on); (None, "") when there is none; (None, why)
    when it is not one setup takes."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return None, ""
    except OSError:
        return None, prompts.SETUP_ANSWERS_LINK if path.is_symlink() else prompts.SETUP_ANSWERS_UNREAD
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            return None, prompts.SETUP_ANSWERS_NOT_FILE
        if info.st_uid != _uid() or info.st_mode & 0o077:
            return None, prompts.SETUP_ANSWERS_NOT_YOURS
        data = os.read(fd, MAX_BYTES + 1)
    finally:
        os.close(fd)
    if len(data) > MAX_BYTES:
        return None, prompts.SETUP_ANSWERS_LARGE
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return None, prompts.SETUP_ANSWERS_UNREAD
    if parsed.get("format") != FORMAT:
        return None, prompts.SETUP_ANSWERS_FORMAT
    return parsed, ""


def _folder_problem(folder: Path) -> str:
    """Why setup's folder is not one it writes in: a link, or not yours ("" when it is, or when it is not there)."""
    try:
        info = os.lstat(folder)
    except FileNotFoundError:
        return ""
    if stat.S_ISLNK(info.st_mode):
        return prompts.SETUP_ANSWERS_LINK
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != _uid():
        return prompts.SETUP_ANSWERS_NOT_YOURS
    return ""


def private_folders(folder: Path) -> None:
    """Each missing folder up to setup's, made 0700 level by level (mkdir's parents= gives the others the umask's);
    setup's own folder 0700 even when it was there."""
    missing: list[Path] = []
    at = folder
    while not at.exists():
        missing.append(at)
        at = at.parent
    for level in reversed(missing):
        level.mkdir(mode=0o700)
        level.chmod(0o700)
    folder.chmod(0o700)


class Answers:
    """The answers so far: from the file of an earlier run (`kept`), and this run's, each written at once."""

    def __init__(
        self,
        path: Path | None,
        values: Mapping[str, Value] | None = None,
        answered: Mapping[str, str] | None = None,
        *,
        saved: Collection[str] | None = None,
        done: Mapping[str, str] | None = None,
    ) -> None:
        self.path = path  # None: nothing written (a dry run, a direct Wizard, a file setup cannot trust)
        self.saved = frozenset(saved_ids() if saved is None else saved)
        self._values: dict[str, Value] = {k: v for k, v in (values or {}).items() if k in self.saved}
        self._answered: dict[str, str] = {k: v for k, v in (answered or {}).items() if k in self._values}
        self._done: dict[str, str] = {k: v for k, v in (done or {}).items() if markable(k)}
        self._earlier = frozenset(self._values)
        self._lock: int | None = None

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        say: Callable[[str], None],
        saved: Collection[str] | None = None,
        writable: bool = True,
    ) -> Answers:
        """The file's answers, read as untrusted; a file setup cannot trust is said in one line and left as it is,
        and this run's answers are then not written (setup starts with none). `writable=False`: a file only read
        (--answers' file from another run), never written, wherever it is."""
        why = _folder_problem(path.parent) if writable else ""
        parsed, why = (None, why) if why else _read(path)
        home = path if writable else None
        if why:
            say(prompts.SETUP_ANSWERS_REFUSED.format(path=path, why=why))
            return cls(None, saved=saved)
        if parsed is None:
            return cls(home, saved=saved)
        table = parsed.get("answers")
        times = parsed.get("answered")
        values = {
            str(k): value
            for k, raw in (table.items() if isinstance(table, dict) else ())
            if (value := _value(raw)) is not None
        }
        answered = {  # only a time comes back: anything else is left out, never written back as it came
            str(k): _when(v) for k, v in (times.items() if isinstance(times, dict) else ()) if isinstance(v, datetime)
        }
        marks = parsed.get("done")
        done = {
            str(k): _when(v) for k, v in (marks.items() if isinstance(marks, dict) else ()) if isinstance(v, datetime)
        }
        return cls(home, values, answered, saved=saved, done=done)

    @classmethod
    def memory(cls, saved: Collection[str] | None = None) -> Answers:
        """Answers that live only in this run: a dry run, and a Wizard made without a file."""
        return cls(None, saved=saved)

    def get(self, qid: str) -> Value | None:
        return self._values.get(qid)

    def values(self) -> dict[str, Value]:
        """Every answer kept so far, by id (a copy)."""
        return dict(self._values)

    def kept(self, qid: str) -> bool:
        """Answered in an earlier run (and still here): a re-run says "Kept: …" for it."""
        return qid in self._earlier and qid in self._values

    def set(self, qid: str, value: Value) -> None:
        """Keep an answer, written to the file at once. ValueError for an id the file never keeps (a secret, a step's
        answer, an unknown id): a secret can never reach the file."""
        if qid not in self.saved:
            raise ValueError(f"{qid}: not an answer setup keeps")
        _toml(value)  # a value that cannot be written is refused before anything changes
        self._values[qid] = value
        self._answered[qid] = _when(datetime.now(UTC))
        self._earlier -= {qid}
        self._write()

    def drop(self, qid: str) -> None:
        """Forget one answer (`--redo`), so it is asked again, and the step of the same id done before."""
        self._values.pop(qid, None)
        self._answered.pop(qid, None)
        self._done.pop(qid, None)
        self._write()

    def done(self, step: str) -> bool:
        """A step a run finished before (DONE_STEPS): a re-run skips it."""
        return step in self._done

    def mark_done(self, step: str) -> None:
        """Mark a step done, written at once; ValueError for a step setup never marks."""
        if not markable(step):
            raise ValueError(f"{step}: not a step setup marks as done")
        self._done[step] = _when(datetime.now(UTC))
        self._write()

    def adopt(self, other: Answers) -> None:
        """Take another file's answers (--answers) as kept ones, written at once: a re-run shows each as "Kept"."""
        taken = {qid: value for qid, value in other._values.items() if qid in self.saved}
        if not taken:
            return
        for qid, value in taken.items():
            self._values[qid] = value
            self._answered[qid] = other._answered.get(qid) or _when(datetime.now(UTC))
        self._earlier |= set(taken)
        self._write()

    def recheck(self, questions: Iterable[Question], mac: Mac, *, say: Callable[[str], None]) -> None:
        """Each kept answer through its question's check again: one that no longer fits is dropped with one line
        and asked again; one the check cleans (a number's spaces) is kept as the check gives it, still kept, and the
        file written once (review SEC-5: the raw value reached the steps)."""
        cleaned = False
        for q in questions:
            value = self._values.get(q.id)
            if value is None or q.check is None:
                continue
            result = q.check(value, self, mac)
            if result.value is None:
                self.drop(q.id)
                say(prompts.SETUP_NOT_KEPT.format(question=q.title, why=result.message.strip().rstrip(".")))
            elif result.value != value:
                _toml(result.value)  # a value that cannot be written is never kept
                self._values[q.id] = result.value
                cleaned = True
        if cleaned:
            self._write()

    def handed_over(self) -> dict[str, Value]:
        """About you and the home area: what her first start reads (setup_handover writes them for her)."""
        return {qid: self._values[qid] for qid in HANDED_OVER if qid in self._values}

    def mark_handed(self) -> None:
        """About you and the home area, once written beside her config: the file keeps only `HANDED` in their place
        (one written in an earlier run stays kept), so their words live in her files alone. A change goes through
        /profile: `--redo` keeps the mark (her first start imports them once; Fable, 27 Oct)."""
        filled = [
            qid
            for qid in HANDED_OVER
            if isinstance(value := self._values.get(qid), Mapping) and any(str(text) for text in value.values())
        ]
        if not filled:
            return
        for qid in filled:
            self._values[qid] = HANDED
            self._answered.setdefault(qid, _when(datetime.now(UTC)))
        self._write()

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """One setup at a time: an exclusive lock on answers.lock beside the file (AnswersInUse when another setup
        holds it). Answers that live only in this run take none."""
        if self.path is None:
            yield
            return
        private_folders(self.path.parent)
        fd = os.open(self.path.with_name("answers.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise AnswersInUse from None
            yield
        finally:
            os.close(fd)  # closing it lets the lock go

    def _write(self) -> None:
        """The whole file again, in one step: a temporary file beside it (0600, never a link), synced, then renamed
        over it, and the folder synced; a failure leaves the old file."""
        if self.path is None:
            return
        private_folders(self.path.parent)
        lines = [HEADER, f"format = {FORMAT}\n", "\n[answers]\n"]
        lines += [f"{qid} = {_toml(value)}\n" for qid, value in self._values.items()]
        lines += ["\n[answered]\n", *(f"{qid} = {when}\n" for qid, when in self._answered.items() if when)]
        if self._done:
            lines += ["\n[done]\n", *(f"{step} = {when}\n" for step, when in self._done.items())]
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{secrets.token_hex(4)}")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write("".join(lines))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        folder = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)
