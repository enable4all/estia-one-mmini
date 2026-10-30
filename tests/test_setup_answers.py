"""The answers file: saved after each answer, read back as untrusted, never a secret in it."""

import os
import stat
import tomllib
from pathlib import Path

import pytest

from local_assistant import setup_answers
from local_assistant.setup_answers import FORMAT, Answers, AnswersInUse
from local_assistant.setup_plan import QUESTIONS, Mac
from local_assistant.setup_questions import Checked, Question, Value

SAVED = frozenset({"name", "language", "extras", "home_area", "about_you", "move"})


def load(path: Path) -> tuple[Answers, list[str]]:
    said: list[str] = []
    return Answers.load(path, say=said.append, saved=SAVED), said


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "home/.config/estia-setup/answers.toml"


def test_the_answers_come_back_as_they_were_given(path: Path) -> None:
    """Strings of any kind, yes or no, lists and fields go through TOML and come back the same."""
    answers, said = load(path)
    values: dict[str, Value] = {
        "name": 'Zoë "the" \\ one\x7f\u2028Μαρία',  # quotes, a backslash, DEL, a line separator, Greek
        "language": "nl",
        "extras": ("web", "maps"),
        "home_area": {"street": "Example Street", "city": "Athens"},
        "about_you": {"name": "Alex", "work": "", "people": "Maria (wife)", "interests": "", "day": ""},
        "move": True,
    }
    for qid, value in values.items():
        answers.set(qid, value)
    again, said = load(path)
    assert said == [] and {qid: again.get(qid) for qid in values} == values
    assert all(again.kept(qid) for qid in values) and not answers.kept("name")  # kept: from an earlier run
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["format"] == FORMAT and set(data["answers"]) == set(values) and set(data["answered"]) == set(values)
    assert path.read_text(encoding="utf-8").startswith("# Estia setup: your answers so far. No captcha link, code")


def test_the_folder_and_the_file_are_yours_alone(path: Path) -> None:
    """Each folder setup makes is 0700, level by level (mkdir's parents= gives the others the umask's), the file 0600."""
    answers, _said = load(path)
    answers.set("name", "Maya")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    for folder in (path.parent, path.parent.parent, path.parent.parent.parent):
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, folder


def test_a_write_that_fails_half_way_leaves_the_old_file(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers, _said = load(path)
    answers.set("name", "Maya")
    before = path.read_bytes()

    def no_room(*args: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", no_room)
    with pytest.raises(OSError):
        answers.set("language", "nl")
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["answers.toml"]  # no temporary file left


@pytest.mark.parametrize("why", ["link", "group", "other", "large", "toml", "format", "folder"])
def test_a_file_setup_cannot_trust_is_left_as_it_is_and_setup_starts_with_no_answers(
    path: Path, tmp_path: Path, why: str
) -> None:
    answers, _said = load(path)
    answers.set("name", "Maya")
    target = path
    if why == "link":
        elsewhere = tmp_path / "planted.toml"
        elsewhere.write_bytes(path.read_bytes())
        path.unlink()
        path.symlink_to(elsewhere)
    elif why in ("group", "other"):
        path.chmod(0o640 if why == "group" else 0o604)
    elif why == "large":
        path.write_text(path.read_text(encoding="utf-8") + "#" * 70_000 + "\n", encoding="utf-8")
    elif why == "toml":
        path.write_text('[answers]\nname = "unfinished\n', encoding="utf-8")
    elif why == "format":
        path.write_text(path.read_text(encoding="utf-8").replace("format = 1", "format = 2"), encoding="utf-8")
    elif why == "folder":  # the folder itself a link to another one
        moved = tmp_path / "elsewhere"
        path.parent.rename(moved)
        path.parent.symlink_to(moved)
    before = os.lstat(target), target.read_bytes() if not target.is_symlink() else b""
    again, said = load(path)
    assert again.get("name") is None and len(said) == 1 and str(path) in said[0], said
    assert "so setup starts with no answers and leaves it as it is" in said[0]
    again.set("name", "Juno")  # this run's answers are not written over it
    assert os.lstat(target) == before[0]
    assert (target.read_bytes() if not target.is_symlink() else b"") == before[1]


def test_a_folder_of_yours_that_others_can_open_is_closed_first(path: Path) -> None:
    answers, _said = load(path)
    answers.set("name", "Maya")
    path.parent.chmod(0o755)
    again, said = load(path)
    again.set("language", "nl")
    assert said == [] and stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_another_owners_file_is_never_read(path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answers, _said = load(path)
    answers.set("name", "Maya")
    monkeypatch.setattr(setup_answers, "_uid", lambda: os.getuid() + 1)
    again, said = load(path)
    assert again.get("name") is None and "is not yours alone" in said[0]


def test_unknown_keys_and_values_of_another_shape_are_left_out_and_dropped_at_the_next_write(path: Path) -> None:
    answers, _said = load(path)
    answers.set("name", "Maya")
    text = path.read_text(encoding="utf-8").replace("[answers]\n", '[answers]\nshell = "rm -rf ~"\nlanguage = 3\n')
    path.write_text(text, encoding="utf-8")
    again, said = load(path)
    assert said == [] and again.get("shell") is None and again.get("language") is None and again.get("name") == "Maya"
    again.set("move", True)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert set(data["answers"]) == {"name", "move"}


def test_a_secret_or_a_steps_answer_can_never_be_set() -> None:
    """By construction: an id the answers file does not keep is refused, whatever its value."""
    answers = Answers.memory()
    for q in QUESTIONS:
        if q.keep != "saved":
            with pytest.raises(ValueError, match=q.id):
                answers.set(q.id, "anything")
    with pytest.raises(ValueError):
        answers.set("no_such_question", "x")


def test_a_kept_answer_that_no_longer_fits_is_dropped_with_one_line(path: Path) -> None:
    """Read back, each answer goes through its question's check again: one that fails is asked again."""
    answers, _said = load(path)
    answers.set("name", "Maya")
    answers.set("language", "nl")
    again, _said = load(path)
    refuse = Question("language", "choice", "Language", check=lambda value, known, mac: Checked(None, "gone"))
    said: list[str] = []
    again.recheck((refuse,), Mac(), say=said.append)
    assert again.get("language") is None and again.get("name") == "Maya"
    assert said == ["Not kept: Language no longer fits (gone). Asked again."]


def crafted(path: Path, lines: str) -> Answers:
    """An answers file as a hand edit or another run's --answers may leave it (0600, yours), read as setup reads it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"format = 1\n\n[answers]\n{lines}", encoding="utf-8")
    path.chmod(0o600)
    return Answers.load(path, say=print)


def test_an_extra_setup_does_not_know_is_refused_never_left_for_a_later_step(path: Path) -> None:
    """Review SEC-5: an id no Extra Feature has (a hand edit, a newer run's file) is refused and asked again; kept, the
    steps after the config and the move named it and stopped (KeyError), at every run again."""
    answers = crafted(path, 'extras = ["web", "future-id"]\n')
    said: list[str] = []
    answers.recheck(QUESTIONS, Mac(), say=said.append)
    assert answers.get("extras") is None
    assert said == [
        "Not kept: Extra Features no longer fits (Not on the list: future-id. Names or numbers from the list; none: no "
        "extras). Asked again."
    ]


def test_a_street_with_a_terminal_escape_is_refused(path: Path) -> None:
    """Review SEC-5: the home area is shown back ("Kept: …"), so a control character in it would reach the terminal."""
    answers = crafted(path, 'home_area = { street = "Ermou\\u001b]52;c;eA==\\u0007", city = "Athens" }\n')
    said: list[str] = []
    answers.recheck(QUESTIONS, Mac(), say=said.append)
    assert answers.get("home_area") is None
    assert said == [
        "Not kept: Home area no longer fits (Street: a hidden or control character is in it; type it again). Asked again."
    ]
    assert "\x1b" not in "".join(said)


def test_a_kept_answer_is_used_as_its_check_cleans_it(path: Path) -> None:
    """Review SEC-5: the check's cleaned value is the one kept and used, written back once; a kept answer stays kept."""
    answers = crafted(path, 'number = "+30 690 000 0001"\nname = "  Maya "\n')
    answers.recheck(QUESTIONS, Mac(), say=print)
    assert answers.get("number") == "+306900000001" and answers.kept("number")
    assert answers.get("name") == "Maya" and answers.kept("name")
    again = tomllib.loads(path.read_text(encoding="utf-8"))["answers"]
    assert again["number"] == "+306900000001" and again["name"] == "Maya"


def test_drop_forgets_one_answer_for_redo(path: Path) -> None:
    answers, _said = load(path)
    answers.set("name", "Maya")
    answers.drop("name")
    assert load(path)[0].get("name") is None


def test_one_setup_at_a_time(path: Path) -> None:
    first, _said = load(path)
    with first.lock():
        second, _said = load(path)
        with pytest.raises(AnswersInUse), second.lock():
            pass
    with second.lock():  # once the first is done
        pass


def test_a_dry_run_writes_nothing(tmp_path: Path) -> None:
    answers = Answers.memory()
    answers.set("name", "Maya")
    with answers.lock():
        pass
    assert answers.get("name") == "Maya" and list(tmp_path.iterdir()) == []


def test_the_handover_holds_only_about_you_and_the_home_area() -> None:
    answers = Answers.memory(saved=SAVED)  # home_area and about_you: questions the extras track adds
    answers.set("name", "Maya")
    answers.set("home_area", {"street": "Ermou", "city": "Athens"})
    assert answers.handed_over() == {"home_area": {"street": "Ermou", "city": "Athens"}}


def test_the_default_place_is_setups_own_folder_never_hers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The move takes ~/.config/local-assistant to her and renames yours aside: setup's answers live elsewhere."""
    monkeypatch.undo()  # conftest's own answers path for the suite
    monkeypatch.setenv("HOME", str(tmp_path))
    assert setup_answers.default_path() == tmp_path / ".config/estia-setup/answers.toml"


def test_only_a_time_comes_back_in_answered(path: Path) -> None:
    """[answered] holds when each was answered: anything else there is left out, never written back as it came."""
    answers, _said = load(path)
    answers.set("name", "Maya")
    text = path.read_text(encoding="utf-8")
    planted = text.replace("[answered]\n", '[answered]\nlanguage = "x\\n[answers]\\nshell = 1"\n')
    path.write_text(planted.replace("[answers]\n", '[answers]\nlanguage = "nl"\n', 1), encoding="utf-8")
    again, said = load(path)
    again.set("move", True)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert said == [] and set(data["answers"]) == {"name", "language", "move"} and "shell" not in data["answers"]
    assert all(not isinstance(when, str) for when in data["answered"].values())


def test_a_step_done_is_kept_and_its_redo_forgets_it(path: Path) -> None:
    """stops-reruns-rerun-not-quiet: a step a finished install has done (calls: her config's lines, then her restart)
    is marked in the file with its time, so a re-run skips it; `--redo` of its question forgets the mark."""
    answers, _ = load(path)
    assert not answers.done("calls")
    answers.mark_done("calls")
    again, said = load(path)
    assert again.done("calls") and said == []
    assert set(tomllib.loads(path.read_text(encoding="utf-8"))["done"]) == {"calls"}  # with its time
    again.drop("calls")
    assert not load(path)[0].done("calls")
    with pytest.raises(ValueError):
        again.mark_done("name")  # only a step setup knows as done


def test_an_extra_gone_through_is_marked_done(path: Path) -> None:
    """stops-reruns-extras-pick-lost: each Extra Feature setup went through is marked, so a re-run after a stop
    offers only the ones picked and not gone through; a name setup would not write as it is is refused."""
    answers, _ = load(path)
    answers.mark_done("extra_web")
    answers.mark_done("extra_google-maps")
    again, said = load(path)
    assert again.done("extra_web") and again.done("extra_google-maps") and not again.done("extra_lights")
    assert said == []
    for bad in ("extra_", "extra_a b", "extra_x\n[answers]"):
        with pytest.raises(ValueError):
            again.mark_done(bad)
