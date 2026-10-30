"""Her config changed as her, from the setup window (owner, 29 Oct: every step in the window, no nano): only the fixed
changes setup names, a 0600 copy of the old one kept, the result loaded as she loads it and written in one rename
only when it loads; a hand-edited config keeps every other line."""

import os
import stat
import tomllib
from pathlib import Path

import pytest

from local_assistant import configtext, herconfig
from local_assistant.setup import render_config

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (ROOT / "config.example.toml").read_text(encoding="utf-8")
ACI = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
DIGEST = "ab" * 32


def hers(tmp_path: Path, text: str | None = None) -> Path:
    """Her config as setup's first install wrote it (or `text`), 0600, in a folder of the test's own."""
    folder = tmp_path / ".config" / "local-assistant"
    folder.mkdir(parents=True, mode=0o700)
    path = folder / "config.toml"
    path.write_text(text if text is not None else render_config(EXAMPLE, "+306900000000", ACI), encoding="utf-8")
    path.chmod(0o600)
    return path


def loaded(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("change", "check"),
    [
        (["calls"], lambda data: isinstance(data.get("calls"), dict) and isinstance(data.get("voice"), dict)),
        (["section", "chords"], lambda data: isinstance(data.get("chords"), dict)),
        (["section", "maps"], lambda data: isinstance(data.get("maps"), dict)),
        (["projects", "/Users/alex/dev", "summary"], lambda data: data["projects"]["root"] == "/Users/alex/dev"),
        (["style", "playful"], lambda data: data["personality"]["style"] == "playful"),
        (["ollama-app"], lambda data: data.get("ollama") == "app"),
        (["model-digest", DIGEST], lambda data: data.get("model_digest") == f"sha256:{DIGEST}"),
    ],
)
def test_each_change_is_made_on_the_config_setup_wrote(tmp_path: Path, change: list[str], check: object) -> None:
    path = hers(tmp_path, render_config(EXAMPLE, "+306900000000", ACI, model_digest="cd" * 32))
    before = path.read_text(encoding="utf-8")
    assert herconfig.main(["apply", *change], config=path) == 0
    assert check(loaded(path))  # type: ignore[operator]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    [copy] = list(path.parent.glob("config.toml.bak-*"))
    assert copy.read_text(encoding="utf-8") == before and stat.S_IMODE(copy.stat().st_mode) == 0o600


def test_calls_turns_on_voice_with_its_own_lines_kept(tmp_path: Path) -> None:
    path = hers(tmp_path, render_config(EXAMPLE, "+306900000000", ACI, voice="alba"))
    assert herconfig.main(["apply", "calls"], config=path) == 0
    voice = loaded(path)["voice"]
    assert isinstance(voice, dict) and voice["voice"] == "alba" and voice["url"] == "http://127.0.0.1:8150"


def test_a_hand_edited_config_keeps_every_other_line(tmp_path: Path) -> None:
    text = render_config(EXAMPLE, "+306900000000", ACI) + "\n# my own note\n"
    path = hers(tmp_path, text.replace('style = "hearth"', 'style = "hearth"   # mine'))
    assert herconfig.main(["apply", "style", "playful"], config=path) == 0
    new = path.read_text(encoding="utf-8")
    assert "# my own note" in new and 'style = "playful"' in new and new.count("style = ") == 1


def test_a_change_there_already_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = hers(tmp_path)
    assert herconfig.main(["apply", "section", "chords"], config=path) == 0
    assert herconfig.main(["apply", "section", "chords"], config=path) == 0
    assert len(list(path.parent.glob("config.toml.bak-*"))) == 1
    assert capsys.readouterr().out.strip().splitlines()[-1] == herconfig.ALREADY


def test_a_change_that_would_not_load_changes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A hand edit that stops her from loading (a number of another shape here): the change is not made, so it never
    looks like setup's doing."""
    path = hers(tmp_path, render_config(EXAMPLE, "+306900000000", ACI).replace("+306900000000", "not a number"))
    before = path.read_text(encoding="utf-8")
    assert herconfig.main(["apply", "section", "chords"], config=path) == 1
    assert path.read_text(encoding="utf-8") == before and not list(path.parent.glob("config.toml.bak-*"))
    assert capsys.readouterr().out.startswith("✗ Her config: not changed")
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".config")]  # no temporary file left


@pytest.mark.parametrize(
    "change",
    [
        ["section", "tapo"],  # only the sections setup turns on
        ["section", "x]\n[y"],
        ["style", "evil"],
        ["model-digest", "xyz"],
        ["projects", "/Users/alex/dev", "everything"],
        ["projects", "relative/folder", "summary"],  # her ~ is /var/estia: a folder by its absolute path
        ["rm", "-rf"],
        [],
    ],
)
def test_only_setups_own_changes_are_made(tmp_path: Path, change: list[str]) -> None:
    path = hers(tmp_path)
    before = path.read_text(encoding="utf-8")
    assert herconfig.main(["apply", *change], config=path) != 0
    assert path.read_text(encoding="utf-8") == before


def test_a_config_others_can_read_is_never_changed(tmp_path: Path) -> None:
    path = hers(tmp_path)
    path.chmod(0o644)
    assert herconfig.main(["apply", "section", "chords"], config=path) == 1
    assert "[chords]\n" not in path.read_text(encoding="utf-8").replace("#[chords]", "")


def test_a_link_in_place_of_her_config_is_never_followed(tmp_path: Path) -> None:
    other = tmp_path / "elsewhere.toml"
    other.write_text(render_config(EXAMPLE, "+306900000000", ACI), encoding="utf-8")
    other.chmod(0o600)
    folder = tmp_path / ".config" / "local-assistant"
    folder.mkdir(parents=True)
    (folder / "config.toml").symlink_to(other)
    assert herconfig.main(["apply", "section", "chords"], config=folder / "config.toml") == 1
    assert "\n[chords]\n" not in other.read_text(encoding="utf-8")


def test_each_change_shows_its_lines_for_the_screen() -> None:
    assert herconfig.lines(["section", "chords"]) == "[chords]"
    assert herconfig.lines(["style", "playful"]) == '[personality]\nstyle = "playful"'
    assert herconfig.lines(["ollama-app"]) == 'ollama = "app"'
    assert herconfig.lines(["projects", "/Users/alex/dev", "summary"]) == (
        '[projects]\nroot = "/Users/alex/dev"\ndefault = "summary"'
    )
    assert herconfig.lines(["model-digest", DIGEST]) == f'model_digest = "sha256:{DIGEST}"'
    assert "[calls]" in herconfig.lines(["calls"]) and "[voice]" in herconfig.lines(["calls"])


def test_setups_text_changes_live_in_one_module_both_use() -> None:
    from local_assistant import setup

    assert setup.with_section is configtext.with_section and setup.with_projects is configtext.with_projects


def test_her_config_is_her_homes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Run as her (HOME=/var/estia): her own config, never a path from the command line."""
    monkeypatch.setenv("HOME", str(tmp_path))
    path = hers(tmp_path)
    assert herconfig.main(["apply", "section", "chords"]) == 0
    assert "[chords]" in path.read_text(encoding="utf-8")
    assert os.environ["HOME"] == str(tmp_path)


def test_projects_on_already_are_never_changed_here(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = hers(tmp_path)
    assert herconfig.main(["apply", "projects", "/Users/alex/dev", "summary"], config=path) == 0
    assert herconfig.main(["apply", "projects", "/Users/alex/other", "code"], config=path) == 1
    assert '"/Users/alex/dev"' in path.read_text(encoding="utf-8")
    assert "it has [projects] already" in capsys.readouterr().out


def test_a_new_model_pin_replaces_her_pin_and_never_adds_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = hers(tmp_path)  # setup wrote no pin (Ollama listed no digest then)
    before = path.read_text(encoding="utf-8")
    assert herconfig.main(["apply", "model-digest", DIGEST], config=path) == 0
    assert path.read_text(encoding="utf-8") == before
    assert capsys.readouterr().out.strip() == herconfig.NO_PIN
