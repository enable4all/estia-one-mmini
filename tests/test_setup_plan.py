"""The plan: the config setup writes for a set of answers (pinned before the questions became a list, and held through
every change since), the order of the steps, the answers kept between runs."""

import re
import tomllib
from pathlib import Path

import pytest

from local_assistant import doctor, prompts, setup, setup_answers, setup_plan
from local_assistant.setup import Wizard, main, render_config
from local_assistant.setup_questions import RECOMMENDED, plain_text
from tests.setup_helpers import her_own_user, recorder, refuse, signal_plan

ROOT = Path(__file__).resolve().parents[1]
BOT = "+306900000000"
ACI = "11111111-2222-4333-8444-555555555555"
NUMBERLESS = "99999999-8888-4777-8666-555555555555"  # her Account ID (made up)
DIGEST = "ab" * 32
pytestmark = pytest.mark.usefixtures("no_real_ollama", "no_system_paths", "a_terminal")
GOOD = f'bot_number = "{BOT}"\nallowed_acis = ["{ACI}"]\n'
REAL_REGION = setup_plan.region


def example() -> str:
    """The example as it is when the test runs (P2 changes its voice lines: the comparison follows)."""
    return (ROOT / "config.example.toml").read_text(encoding="utf-8")


@pytest.fixture
def ollama_made_the_alias(no_real_ollama: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ollama lists local-worker with a digest and shows the context it was made with (read-only, as doctor reads)."""

    def get_json(self: doctor.Probe, url: str) -> object:
        return {"models": [{"name": "local-worker:latest", "digest": f"sha256:{DIGEST}"}]}

    def post_json(self: doctor.Probe, url: str, payload: object) -> object:
        return {"parameters": "num_ctx                        32768"}

    monkeypatch.setattr(doctor.Probe, "get_json", get_json)
    monkeypatch.setattr(doctor.Probe, "post_json", post_json)


def todays_answers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Wizard:
    """The config step with the language nl, the style playful and projects in ~/work at level docs; then Enter."""
    home = tmp_path / "home"
    (home / "work").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    typed = iter(["nl", "playful", "y", "~/work", "docs"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed, ""))
    return Wizard(tmp_path / "cfg" / "config.toml", dry_run=False)


@pytest.mark.usefixtures("ollama_made_the_alias")
@pytest.mark.parametrize("account", [BOT, NUMBERLESS], ids=["number", "no number"])
def test_todays_answers_write_todays_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, account: str) -> None:
    wizard = todays_answers(tmp_path, monkeypatch)
    wizard.config(account, ACI)
    written = wizard.config_path.read_text(encoding="utf-8")
    assert written == render_config(
        example(),
        account,
        ACI,
        "nl",
        name=wizard.name,
        voice=wizard.voice,
        model_digest=DIGEST,
        projects=("~/work", "docs"),
        style="playful",
        context_tokens=32768,
    )


@pytest.mark.usefixtures("ollama_made_the_alias")
@pytest.mark.parametrize("account", [BOT, NUMBERLESS], ids=["number", "no number"])
def test_the_filled_lines_stay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, account: str) -> None:
    """The lines setup fills, as fixed text (never the #voice line, which P2 changes)."""
    wizard = todays_answers(tmp_path, monkeypatch)
    wizard.config(account, ACI)
    written = wizard.config_path.read_text(encoding="utf-8")
    for line in (
        f'bot_number = "{account}"',
        f'allowed_acis = ["{ACI}"]',
        'name = "Estia"',
        'language = "nl"',
        'style = "playful"',
        f'model_digest = "sha256:{DIGEST}"',
        "context_tokens = 32768",
        "[projects]",
        'root = "~/work"',
        'default = "docs"',
    ):
        assert re.search(rf"^{re.escape(line)}", written, re.MULTILINE), line
    for placeholder in ('bot_number = "+300000000000"', 'allowed_acis = ["00000000-', "#[projects]"):
        assert placeholder not in written, placeholder


# ------------------------------------------------------------------ the plan first (phase A), then the steps


class Stopped(Exception):
    """The test's stand-in for a step: setup stops there, so what came before it is what the test looks at."""


def typing(monkeypatch: pytest.MonkeyPatch, *answers: str, signal: bool = True) -> list[str]:
    """`answers` typed in turn at every visible question (then Enter); the questions asked are recorded. With
    `signal`, the plan's Signal questions that Enter cannot answer here get their answers of their own (`signal_plan`:
    Signal on the phone, her number, yes to it shown back) and take none of `answers`."""
    asked: list[str] = []
    given = iter(answers)

    def typed(prompt: str = "") -> str:
        asked.append(prompt.strip())
        planned = signal_plan(prompt) if signal else None
        return planned if planned is not None else next(given, "")

    monkeypatch.setattr("builtins.input", typed)
    return asked


def stop_at_ollama(monkeypatch: pytest.MonkeyPatch, steps: list[str] | None = None) -> None:
    def ollama(self: Wizard) -> None:
        if steps is not None:
            steps.append("ollama")
        raise Stopped

    monkeypatch.setattr(Wizard, "ollama", ollama)


def saved(path: Path | None = None) -> dict[str, object]:
    path = path or setup_answers.default_path()
    return tomllib.loads(path.read_text(encoding="utf-8"))["answers"] if path.exists() else {}


def asked_for(asked: list[str], text: str) -> bool:
    return any(prompt.startswith(text.split("(", 1)[0].strip()) for prompt in asked)


def test_the_plan_asks_her_name_language_and_style_before_any_step_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    steps: list[str] = []
    asked = typing(monkeypatch, "Maya", "nl", "playful", "n", "none", "", "", "", "", "", "n", "n")
    stop_at_ollama(monkeypatch, steps)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    assert steps == ["ollama"]
    wanted = [
        prompts.SETUP_NAME_ASK.format(default="Estia"),
        f"{prompts.SETUP_SIGNAL_APP_ASK} [y/N]",
        prompts.SETUP_ACCOUNT_ASK,
        prompts.SETUP_NUMBER_ASK_REGION.format(code="1"),
        f"{prompts.SETUP_NUMBER_RIGHT.format(found='+30 690 000 0000')} [y/N]",
        f"{prompts.SETUP_LOCK_ASK} [y/N]",
        prompts.SETUP_LANGUAGE_ASK,
        prompts.SETUP_STYLE_ASK,
        prompts.SETUP_CALLS_ASK,
        prompts.SETUP_FEATURES_ASK,
        *(f"{label}:" for _key, label, _example in prompts.ABOUT_YOU_FIELDS),  # no places picked: no home area
        f"{prompts.SETUP_MOVE_ASK} [y/N]",
        f"{prompts.SETUP_SECURITY_ASK} [y/N]",
    ]
    assert asked == wanted  # every one before the first step (pytest's stdin is no terminal: Enter keeps each no)
    assert saved() == {  # a no to the move is not kept
        "name": "Maya",
        "signal_app": True,
        "route": "number",
        "number": BOT,
        "lock": False,  # Enter is a no without a terminal
        "language": "nl",
        "style": "playful",
        "calls": "no",
        "extras": [],
        "about_you": {"name": "", "work": "", "people": "", "interests": "", "day": ""},
    }


def test_the_plan_asks_her_signal_account_before_anything_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 27 Oct ("move them up"): Signal on your phone, the route, her number (shown back) and the Registration
    Lock's yes or no come with the plan, kept before anything installs; a no to Signal on the phone says where to get
    it and asks again. The captcha, the codes and the PIN wait for the Signal step."""
    monkeypatch.setattr(Wizard, "terminal", lambda self: True)
    steps: list[str] = []
    asked = typing(monkeypatch, "", "n", "y", "", "+30 690 000 0000", "", "", signal=False)
    stop_at_ollama(monkeypatch, steps)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    assert steps == ["ollama"]
    assert asked[1:7] == [
        f"{prompts.SETUP_SIGNAL_APP_ASK} [Y/n]",
        f"{prompts.SETUP_SIGNAL_APP_ASK} [Y/n]",  # the no: asked again
        prompts.SETUP_ACCOUNT_ASK,
        prompts.SETUP_NUMBER_ASK_REGION.format(code="1"),  # conftest: en_US
        f"{prompts.SETUP_NUMBER_RIGHT.format(found='+30 690 000 0000')} [Y/n]",
        f"{prompts.SETUP_LOCK_ASK} [Y/n]",
    ]
    assert asked[7] == prompts.SETUP_LANGUAGE_ASK
    kept = saved()
    assert (kept["signal_app"], kept["route"], kept["number"], kept["lock"]) == (True, "number", BOT, True)
    assert prompts.SETUP_SIGNAL_APP_FIRST in capsys.readouterr().out
    assert not any("captcha" in prompt.lower() or "SMS" in prompt or "PIN" in prompt for prompt in asked)


def test_an_account_without_a_number_asks_no_number_and_no_lock_in_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Wizard, "terminal", lambda self: True)
    asked = typing(monkeypatch, "", "", "2", signal=False)
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    assert asked[1:4] == [
        f"{prompts.SETUP_SIGNAL_APP_ASK} [Y/n]",
        prompts.SETUP_ACCOUNT_ASK,
        prompts.SETUP_LANGUAGE_ASK,
    ]
    assert saved()["route"] == "no_number" and "number" not in saved() and "lock" not in saved()


def test_a_pipe_that_repeats_a_refused_answer_is_the_end_of_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No mistake ends setup (spec rule 5), but a stdin that is no terminal and gives the same refused answer again and
    again is no person typing: after setup.REFUSED_MAX of them it is the end of input, never a loop without end."""
    asked = typing(monkeypatch, signal=False)  # Enter, forever
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    assert wizard.answer("number") is None
    assert len(asked) == setup.REFUSED_MAX
    asked.clear()
    with pytest.raises(SystemExit) as stopped:
        wizard.answer("signal_app")  # Enter is a no without a terminal: refused each time
    assert str(stopped.value) == prompts.SETUP_NO_TERMINAL and len(asked) == setup.REFUSED_MAX


def test_the_plan_prints_todays_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Plain lines: each question's explanation and list as setup always printed them, then the line shown back."""
    typing(monkeypatch)
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    out = capsys.readouterr().out
    name = plain_text(prompts.SETUP_NAME.format(default="Estia", names="Estia · Maya · Melina · Socrates"))
    assert f"{name}\n\nEstia (recommended).\nShe will answer to Estia.\n" in out  # shown back in the spec's words
    assert prompts.SETUP_LANGUAGE_CHOSEN.format(name="English (en)") in out
    style = plain_text(prompts.SETUP_STYLE.format(name="Estia"))
    assert f"{style}\n\nhearth (recommended).\n{prompts.SETUP_STYLE_CHOSEN.format(style='hearth')}" in out
    assert (
        plain_text(prompts.SETUP_MOVE.format(name="Estia")) in out and prompts.SETUP_NUCLEAR_TYPED in out
    )  # the fold's line


def test_enter_takes_the_language_of_the_macs_region(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(setup_plan, "region", lambda: "nl_NL")
    asked = typing(monkeypatch)
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    assert prompts.SETUP_LANGUAGE_ASK_MAC.format(language="Nederlands") in asked and saved()["language"] == "nl"


def test_a_rerun_asks_only_what_was_left_and_says_what_it_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"

    def name_then_ctrl_c(prompt: str = "") -> str:
        if prompt.startswith("Pick a name"):
            return "Juno"
        if prompt.startswith("Female or male voice?"):
            return "m"
        raise KeyboardInterrupt

    monkeypatch.setattr("builtins.input", name_then_ctrl_c)
    assert main(["--config", str(config)]) == 130
    assert saved() == {"name": "Juno", "voice": "male"}
    capsys.readouterr()
    asked = typing(monkeypatch)
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(config)])
    out = capsys.readouterr().out
    assert "Kept: Name: Juno. To change it: scripts/setup.sh --redo name" in out
    assert "Kept: Voice: Male. To change it: scripts/setup.sh --redo voice" in out
    assert not asked_for(asked, prompts.SETUP_NAME_ASK) and not asked_for(asked, prompts.SETUP_VOICE_ASK)
    assert asked[0].startswith(prompts.SETUP_SIGNAL_APP_ASK) and prompts.SETUP_LANGUAGE_ASK in asked


def test_redo_asks_one_again_and_an_unknown_id_names_the_known_ones(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    typing(monkeypatch, "Maya")
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(config)])
    asked = typing(monkeypatch, "Melina")
    with pytest.raises(Stopped):
        main(["--config", str(config), "--redo", "name"])
    assert asked[0] == prompts.SETUP_NAME_ASK.format(default="Estia") and not asked_for(asked, "Which one?")
    assert saved()["name"] == "Melina" and saved()["language"] == "en"
    capsys.readouterr()
    asked = typing(monkeypatch)
    assert main(["--config", str(config), "--redo", "nmae"]) == 2
    out = capsys.readouterr().out
    assert asked == [] and "No question named nmae. The questions: " in out and "name, voice" in out


def test_a_dry_run_shows_every_question_of_the_plan_asks_none_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert main(["--dry-run", "--config", str(tmp_path / "cfg" / "config.toml")]) == 0
    out = capsys.readouterr().out
    ollama = out.index("\nOllama: its official build")
    for line in (
        prompts.SETUP_NAME_DRY_RUN.format(default="Estia"),
        prompts.SETUP_LANGUAGE_DRY_RUN,
        prompts.SETUP_STYLE_DRY_RUN,
        f'(asks "{prompts.SETUP_MOVE_ASK} [Y/n]"',
        f'(asks "{prompts.SETUP_SECURITY_ASK} [Y/n]"',
    ):
        assert -1 < out.index(line) < ollama, line
    assert not setup_answers.default_path().exists() and not (tmp_path / "cfg").exists()


def test_a_configured_rerun_asks_no_signal_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "config.toml"
    config.write_text(GOOD, encoding="utf-8")
    config.chmod(0o600)
    for name in ("signal_account", "signal_number", "numberless", "owner", "config", "her_name"):
        monkeypatch.setattr(Wizard, name, refuse)
    asked = typing(monkeypatch)
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(config)])
    assert asked == [f"{prompts.SETUP_MOVE_ASK} [y/N]", f"{prompts.SETUP_SECURITY_ASK} [y/N]"]


@pytest.mark.parametrize(("there", "move"), [(False, "y"), (True, "y"), (False, "n")], ids=["gone", "there", "no-move"])
def test_a_set_up_config_whose_signal_account_is_gone_stops_before_anything_downloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, there: bool, move: str
) -> None:
    """c-16 (confirm-2): her config set up, signal-cli's data without her account, the move's yes. Setup pulled her
    model into today's Ollama, then stopped at always on; the stop's fix (the config moved away) makes the next run a
    first install that pulls her model again into her own. The stop comes right after the plan, before step 1."""
    home = tmp_path / "home"
    (home / ".local/share/signal-cli/data").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Wizard, "her_signal_here", lambda self: there)
    config = tmp_path / "config.toml"
    config.write_text(GOOD, encoding="utf-8")
    config.chmod(0o600)
    steps: list[str] = []
    typing(monkeypatch, move)
    stop_at_ollama(monkeypatch, steps)
    if there or move == "n":  # her account there, or no move: the steps go on as before
        with pytest.raises(Stopped):
            main(["--config", str(config)])
        assert steps == ["ollama"]
        return
    with pytest.raises(SystemExit) as stopped:
        main(["--config", str(config)])
    assert str(stopped.value) == prompts.SETUP_MOVE_NO_SIGNAL and steps == []


def test_a_rerun_in_her_own_user_asks_only_her_style_and_the_security_step(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    her_own_user(no_system_paths)
    for name in ("signal_account", "signal_number", "numberless", "owner", "config", "her_name"):
        monkeypatch.setattr(Wizard, name, refuse)
    asked = typing(monkeypatch)
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    assert asked == [prompts.SETUP_STYLE_ASK, f"{prompts.SETUP_SECURITY_ASK} [y/N]"]


def test_the_smaller_models_no_stops_setup_before_ollama_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(setup, "memory_gb", lambda: 16.0)
    steps: list[str] = []
    asked = typing(monkeypatch, "n")
    stop_at_ollama(monkeypatch, steps)
    with pytest.raises(SystemExit) as stopped:
        main(["--config", str(tmp_path / "config.toml")])
    assert str(stopped.value) == prompts.SETUP_SMALLER_MODEL_NO and steps == []
    assert asked == [f"{prompts.SETUP_SMALLER_MODEL_ASK} [y/N]"] and "smaller_model" not in saved()


# ------------------------------------------------------------------ the Ollama app (owner, 29 Oct: "use it")


def an_ollama_app(root: Path) -> None:
    (root / "Applications/Ollama.app").mkdir(parents=True)


def test_the_mac_says_whether_the_ollama_app_is_there(no_system_paths: Path) -> None:
    """Read with the Mac's facts, before the plan's first question (as the memory is): never a home off the live Mac."""
    assert setup_plan.Mac.read().ollama_app == ""
    an_ollama_app(no_system_paths)
    assert setup_plan.Mac.read().ollama_app == "/Applications/Ollama.app"


@pytest.mark.parametrize(("layout", "enter"), [("hers", "build"), ("older", "app")])
def test_the_ollama_app_on_the_mac_is_asked_first_and_enter_takes_the_recommended_one(
    no_system_paths: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    layout: str,
    enter: str,
) -> None:
    """Owner, 30 Oct (W06): with her own account planned (a new install), Enter takes the background build; with
    none planned, the app."""
    from local_assistant import ollama_build

    monkeypatch.setattr(ollama_build, "NEW_INSTALLS", layout)
    an_ollama_app(no_system_paths)
    seen: list[object] = []

    def ollama(self: Wizard) -> None:
        seen.append(self.given("ollama_app"))
        raise Stopped

    monkeypatch.setattr(Wizard, "ollama", ollama)
    asked = typing(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    ask = prompts.SETUP_OLLAMA_APP_ASK_BUILD if enter == "build" else prompts.SETUP_OLLAMA_APP_ASK
    assert asked[0] == ask and asked.count(ask) == 1
    assert seen == [enter] and saved()["ollama_app"] == enter
    out = capsys.readouterr().out
    for line in (prompts.SETUP_OLLAMA_APP_USE, prompts.SETUP_OLLAMA_APP_BUILD, prompts.SETUP_OLLAMA_APP_USE_LINE):
        assert plain_text(line) in out, line


def test_a_rerun_with_every_other_answer_kept_asks_only_the_ollama_app_question(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's answers file from before the question: every other answer kept, only the new one asked."""
    stop_at_ollama(monkeypatch)
    first = typing(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    before = saved()
    assert "ollama_app" not in before and len(before) > 5
    an_ollama_app(no_system_paths)
    asked = typing(monkeypatch, "2")
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml")])
    nos = (f"{prompts.SETUP_MOVE_ASK} [y/N]", f"{prompts.SETUP_SECURITY_ASK} [y/N]")  # a no is never kept: as before
    # her own account planned (a new install): Enter would take the build (W06)
    assert asked == [prompts.SETUP_OLLAMA_APP_ASK_BUILD, *(prompt for prompt in first if prompt in nos)]
    assert saved() == before | {"ollama_app": "build"}


APP = "/Applications/Ollama.app"
PROMPT_PATH = "her prompts pass through a program your account can change"


def test_setup_ollama_app_limits_named() -> None:
    """Review W06: the app card named the login limit only, and with the app chosen Always on still said she starts
    "without a login" and "No program of yours then reads her data". Its card, the move's lead and its More now say
    both limits, in the card's words (fix review, dialogue-03: "once someone logs in" left out that she stops at a
    logout or when the app quits): she answers only while someone is logged in and the Ollama app runs, and her
    prompts pass through a program your account can change. With the app, More's last paragraph is replaced, the
    limit inside the claim, never the absolute claim then its contradiction (fix review, setup-macos-01). Each line
    stays short (words review, 30 Oct). With the background build the words stay as they were."""
    mac = setup_plan.Mac(ollama_app=APP)
    words = {"name": "Maya"}
    card = setup_plan.question("ollama_app").at(setup_answers.Answers.memory(), mac, words).option("app")
    assert card is not None and PROMPT_PATH in card.line.lower()
    app = setup_plan.question("move").at(setup_answers.Answers(None, {"ollama_app": "app"}), mac, words)
    build = setup_plan.question("move").at(setup_answers.Answers(None, {"ollama_app": "build"}), mac, words)
    # second fix review (words-move-app-limit-contrast): the limit joined to the claim by "but", the app named once
    for lead in (app.lead, app.window_lead):
        flat = " ".join(plain_text(lead).split())
        assert f"as her own hidden macOS account, but {prompts.OLLAMA_APP_LIMIT_LOGIN}" in flat
        assert "with the Ollama app she answers" not in flat
        assert "once someone logs in" not in plain_text(lead)
        assert all(len(line) <= 100 for line in plain_text(lead).splitlines()), lead  # short lines (words-13)
        assert "without a login" not in plain_text(lead).split("\n\n")[0]
    older = " ".join(plain_text(prompts.SETUP_MOVE_OLDER_APP.format(name="Maya")).split())
    assert f"she starts with the Mac, but {prompts.OLLAMA_APP_LIMIT_LOGIN}" in older
    older_move = setup_plan.question("move").at(
        setup_answers.Answers(None, {"ollama_app": "app"}), setup_plan.Mac(ollama_app=APP, legacy=True), words
    )
    for lead in (older_move.lead, older_move.window_lead):  # an older install's: the same limit, the same words
        assert f"she starts with the Mac, but {prompts.OLLAMA_APP_LIMIT_LOGIN}" in " ".join(plain_text(lead).split())
        assert all(len(line) <= 100 for line in plain_text(lead).splitlines()), lead
    more = " ".join(plain_text(app.more).split())
    assert PROMPT_PATH in more.lower() and "reads her data" not in more
    assert f"as long as no admin rule skips the password, but with the Ollama app {PROMPT_PATH}." in more
    paragraphs = prompts.SETUP_MOVE_MORE.split("\n\n")
    assert app.more.startswith("\n\n".join(paragraphs[:-1]))  # her account, her data and her code: as they were
    assert "No program of yours then reads her files or changes her code without admin rights" in more
    assert build.lead == prompts.SETUP_MOVE.format(name="Maya") and build.more == prompts.SETUP_MOVE_MORE
    assert build.window_lead == prompts.SETUP_WINDOW_MOVE.format(name="Maya")
    assert (
        "Ollama's official build" in prompts.SETUP_OLLAMA_APP_LEAD and "own build" not in prompts.SETUP_OLLAMA_APP_LEAD
    )


def test_setup_recommends_her_own_build_when_her_own_account_is_planned() -> None:
    """Owner, 30 Oct (W06): setup recommends the background build whenever her own account is planned (a new
    install): she then starts with the Mac, without a login; the Ollama app stays a choice, its card naming its
    limits. An install with no account of hers planned keeps the app recommended."""
    planned = setup_plan.question("ollama_app").at(
        setup_answers.Answers.memory(), setup_plan.Mac(ollama_app=APP, fresh_hers=True)
    )
    assert planned.recommended == "build" and planned.ask == prompts.SETUP_OLLAMA_APP_ASK_BUILD
    assert [option.id for option in planned.options if option.tag == RECOMMENDED] == ["build"]
    assert planned.advice == prompts.SETUP_OLLAMA_APP_HERS_ADVICE
    older = setup_plan.question("ollama_app").at(setup_answers.Answers.memory(), setup_plan.Mac(ollama_app=APP))
    assert older.recommended == "app" and older.ask == prompts.SETUP_OLLAMA_APP_ASK


def test_the_recommended_build_card_says_she_starts_with_the_mac_once() -> None:
    """n-1 (scenarios on 0.9.8): with her own account planned the window put the advice ("she starts with the Mac,
    without a login") in front of a card line that said it too ("Starts with the Mac, as a background service."). The
    card's line then leaves its first sentence out; Terminal's list keeps the whole line, its hint the advice."""
    planned = setup_plan.question("ollama_app").at(
        setup_answers.Answers.memory(), setup_plan.Mac(ollama_app=APP, fresh_hers=True)
    )
    build = next(option for option in planned.window_options() if option.id == "build")
    assert build.line.lower().count("starts with the mac") == 1
    assert build.line == f"She starts with the Mac, without a login. {prompts.SETUP_OLLAMA_APP_BUILD_HERS_STAYS}"
    over_ssh = setup_plan.question("ollama_app").at(
        setup_answers.Answers.memory(), setup_plan.Mac(ollama_app=APP, fresh_hers=True, ssh=True)
    )
    ssh_build = next(option for option in over_ssh.window_options() if option.id == "build")
    assert ssh_build.line.endswith(prompts.SETUP_OLLAMA_APP_BUILD_LINE_HERS)


@pytest.mark.parametrize("typed", ["x", "3"])
def test_the_ollama_app_question_takes_only_its_two_answers(typed: str) -> None:
    q = setup_plan.question("ollama_app")
    assert q.check is not None
    assert q.check("1", setup_answers.Answers.memory(), setup_plan.Mac()).value == "app"
    assert q.check("build", setup_answers.Answers.memory(), setup_plan.Mac()).value == "build"
    refused = q.check(typed, setup_answers.Answers.memory(), setup_plan.Mac())
    assert refused.value is None and refused.message == prompts.SETUP_OLLAMA_APP_UNKNOWN


def test_a_kept_yes_is_used_at_its_step_without_asking_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The plan's answers reach their steps: the move and the security step run on the yes given first."""
    monkeypatch.setattr(Wizard, "terminal", lambda self: True)
    ran: list[list[str]] = []
    monkeypatch.setattr(Wizard, "run", recorder(ran))
    for name in ("ollama", "model", "extras", "personality"):
        monkeypatch.setattr(Wizard, name, lambda self: None)
    monkeypatch.setattr(Wizard, "signal_account", lambda self: BOT)
    monkeypatch.setattr(Wizard, "owner", lambda self, number: ACI)
    monkeypatch.setattr(Wizard, "config", lambda self, number, aci: None)
    monkeypatch.setattr(Wizard, "check", lambda self: 0)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    asked = typing(monkeypatch)  # Enter: every recommendation
    assert main(["--config", str(tmp_path / "config.toml")]) == 0
    assert len(asked) == 19 and saved() == {
        "name": "Estia",
        "signal_app": True,
        "route": "number",
        "number": BOT,
        "lock": True,
        "language": "en",
        "style": "hearth",
        "calls": "yes",
        "extras": ["web", "maps"],
        "home_area": {},  # Enter on both: none, nothing looked up
        "about_you": {"name": "", "work": "", "people": "", "interests": "", "day": ""},
        "move": True,
        "security": True,
    }
    assert [Path(command[0]).name for command in ran] == ["migrate-to-own-user.sh", "harden.sh"]


def test_a_saved_answers_file_is_read_as_untrusted_and_each_value_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    given = tmp_path / "from-another-run.toml"
    given.write_text('format = 1\n[answers]\nname = "Maya"\nlanguage = "xx"\nshell = "rm -rf ~"\n', encoding="utf-8")
    given.chmod(0o600)
    asked = typing(monkeypatch)
    stop_at_ollama(monkeypatch)
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml"), "--answers", str(given)])
    out = capsys.readouterr().out
    assert (
        "Kept: Name: Maya." in out
        and 'Not kept: Language no longer fits ("xx" is not one of these). Asked again.' in out
    )
    assert prompts.SETUP_LANGUAGE_ASK in asked and not asked_for(asked, prompts.SETUP_NAME_ASK)
    assert saved()["name"] == "Maya" and "shell" not in saved()
    given.chmod(0o644)
    capsys.readouterr()
    with pytest.raises(Stopped):
        main(["--config", str(tmp_path / "config.toml"), "--answers", str(given)])
    assert f"Your answers file {given} is not yours alone" in capsys.readouterr().out


def test_an_answers_file_that_is_not_there_stops_setup_before_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = typing(monkeypatch)
    missing = tmp_path / "not-there.toml"
    assert main(["--config", str(tmp_path / "config.toml"), "--answers", str(missing)]) == 2
    assert asked == [] and f"No answers file at {missing}." in capsys.readouterr().out


def test_one_setup_at_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    asked = typing(monkeypatch)
    held = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    with held.lock():
        assert main(["--config", str(tmp_path / "config.toml")]) == 1
    assert asked == [] and prompts.SETUP_ANSWERS_IN_USE in capsys.readouterr().out


@pytest.mark.parametrize(
    ("locale", "country", "language"),
    [
        ("en_US", "US", "en"),
        ("el_GR", "GR", "en"),  # Greek is not one of the 11 yet
        ("de_DE", "DE", "de"),
        ("nl_NL", "NL", "nl"),
        ("en_US@rg=grzzzz", "GR", "en"),  # the region set apart from the language
        ("zh-Hans_CN", "CN", "en"),  # not among the 11: English
        ("pt_BR", "BR", "pt"),
        ("EN_us", "", "en"),  # not as macOS writes it: no country
        ("", "", "en"),
        ("garbage; rm -rf", "", "en"),
    ],
)
def test_the_macs_region_gives_the_country_and_the_language(locale: str, country: str, language: str) -> None:
    assert setup_plan.from_locale(locale) == (country, language)


def test_the_region_is_read_with_a_fixed_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """/usr/bin/defaults under the tools' root (a test's fake): its output, or nothing when it is not there."""
    monkeypatch.setenv("ESTIA_TEST_ROOT", str(tmp_path))
    assert REAL_REGION() == ""
    tool = tmp_path / "usr/bin/defaults"
    tool.parent.mkdir(parents=True)
    tool.write_text('#!/bin/sh\n[ "$*" = "read -g AppleLocale" ] && echo el_GR\n')
    tool.chmod(0o755)
    assert REAL_REGION() == "el_GR"


def test_the_mac_says_a_remote_login_and_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    assert not setup_plan.Mac.read().ssh
    for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"):
        monkeypatch.setenv(key, "x")
        assert setup_plan.Mac.read().ssh and not setup_plan.Mac.read().kept_session, key
        monkeypatch.delenv(key)
    monkeypatch.setenv("TMUX", "/tmp/t,1,0")
    assert setup_plan.Mac.read().kept_session


@pytest.mark.parametrize(
    ("screen", "set_up", "dry"),
    [(False, False, False), (True, False, False), (False, True, False), (False, False, True)],
    ids=["first-install", "inside-screen", "set-up", "dry-run"],
)
def test_over_ssh_outside_screen_a_first_installs_no_to_the_move_ends_after_the_plan(
    no_system_paths: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    screen: bool,
    set_up: bool,
    dry: bool,
) -> None:
    """c-12 (confirm-2): over SSH outside screen, the Ollama app in /Applications, Enter at every question: the build
    recommended, then the move's no ("run setup again inside screen"); with no move a first install got today's
    layout, which refuses the installed app ("move it to the Trash"), and a run that went on would have fixed today's
    layout for good. Nothing installs in a run its own words say to redo inside screen: setup ends after the plan,
    with every answer kept but the no. Inside screen, on an install set up, and in a dry run, the steps go on."""
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 50000 192.0.2.2 22")
    if screen:
        monkeypatch.setenv("STY", "1.ttys000.mac")
    an_ollama_app(no_system_paths)
    config = tmp_path / "config.toml"
    if set_up:
        config.write_text(GOOD, encoding="utf-8")
        config.chmod(0o600)
    steps: list[str] = []
    typing(monkeypatch, "n" if screen else "")  # inside screen the move's Enter is a yes: a no typed
    stop_at_ollama(monkeypatch, steps)
    if dry:
        for target in ("subprocess.run", "subprocess.Popen"):
            monkeypatch.setattr(target, refuse)
        monkeypatch.setattr("shutil.which", lambda name: None)
        monkeypatch.setattr("time.sleep", lambda seconds: None)
    if screen or set_up or dry:
        with pytest.raises(Stopped):
            main(["--config", str(config), *(["--dry-run"] if dry else [])])
        assert steps == ["ollama"]
        return
    with pytest.raises(SystemExit) as stopped:
        main(["--config", str(config)])
    assert str(stopped.value) == prompts.SETUP_SSH_SCREEN_STOP and steps == []
    kept = saved()
    assert kept["ollama_app"] == "build" and "move" not in kept and kept["name"] == "Estia"
    assert "move it to the Trash" not in capsys.readouterr().out


# ------------------------------------------------------------------ phase B: the steps, in order, and the tracks' hooks

STEPS = (
    "ollama",
    "model",
    "signal_account",
    "owner",
    "config",
    "hand_over",
    "always_on",
    "security",
    "calls_step",
    "extras_setup",
    "personality",
    "check",
    "voice_end",
    "closing",
)


def every_step_recorded(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    order: list[str] = []
    results: dict[str, object] = {"signal_account": BOT, "owner": ACI, "always_on": True, "check": 0}

    def step(name: str) -> object:
        def run(self: Wizard, *args: object, **kwargs: object) -> object:
            order.append(name)
            return results.get(name)

        return run

    for name in STEPS:
        monkeypatch.setattr(Wizard, name, step(name))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    typing(monkeypatch)
    return order


def test_a_first_install_runs_the_steps_in_their_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order = every_step_recorded(monkeypatch)
    assert main(["--config", str(tmp_path / "config.toml")]) == 0
    assert order == list(STEPS)


def test_each_step_says_its_place_in_one_plain_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 27 Oct: "Step 3 of 9: her model (18 GB, about 25 min)": each step after the plan one line, the size and
    the time where it downloads; calls only on the plan's yes, the Extra Features only when some are picked."""
    every_step_recorded(monkeypatch)
    assert main(["--config", str(tmp_path / "config.toml")]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("Step ")]
    assert lines == [
        "Step 1 of 9: Ollama",
        "Step 2 of 9: her model (18.3 GB, about 25 min)",
        "Step 3 of 9: her Signal account",
        "Step 4 of 9: her config",
        "Step 5 of 9: always on",
        "Step 6 of 9: the security step",
        "Step 7 of 9: calls",  # before the move (the steps are stubbed here): no build to name
        "Step 8 of 9: Extra Features",
        "Step 9 of 9: the check",
    ]


def test_a_rerun_in_her_own_user_counts_only_her_steps(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    her_own_user(no_system_paths)
    every_step_recorded(monkeypatch)
    assert main(["--config", str(tmp_path / "config.toml")]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("Step ")]
    assert lines == [
        "Step 1 of 6: Ollama",
        "Step 2 of 6: her model (18.3 GB, about 25 min)",  # Ollama lists no local-worker here
        "Step 3 of 6: always on",
        "Step 4 of 6: the security step",
        "Step 5 of 6: Extra Features",  # a re-run shows every extra
        "Step 6 of 6: the check",
    ]


def test_a_rerun_in_her_own_user_runs_only_hers(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    her_own_user(no_system_paths)
    order = every_step_recorded(monkeypatch)
    assert main(["--config", str(tmp_path / "config.toml")]) == 0
    assert order == [step for step in STEPS if step not in ("signal_account", "owner", "config", "hand_over")]


def test_the_hooks_change_nothing_yet(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Each track fills its own: in the core they do what setup did before them."""
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    assert wizard.extra_sections("[x]\n") == "[x]\n"
    wizard.calls_step()
    wizard.hand_over()
    assert capsys.readouterr().out == ""
    wizard.closing()  # the extras track's last step names her voices and commands before the done line
    assert capsys.readouterr().out.strip().endswith(plain_text(prompts.SETUP_DONE.format(name="Estia")).strip())


@pytest.mark.usefixtures("ollama_made_the_alias")
def test_the_config_step_hands_its_text_to_the_extras_before_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wizard = todays_answers(tmp_path, monkeypatch)
    monkeypatch.setattr(Wizard, "extra_sections", lambda self, text: text + "\n[web]\n")
    wizard.config(BOT, ACI)
    assert wizard.config_path.read_text(encoding="utf-8").endswith("\n[web]\n")


@pytest.mark.parametrize("hers", [False, True], ids=["your account", "her own user"])
def test_redo_signal_on_an_install_set_up_says_where_her_account_changes_and_keeps_the_answers(
    no_system_paths: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    hers: bool,
) -> None:
    """signal-redo-signal-configured-noop: on an install set up, --redo signal asked nothing, ran no Signal step and
    said the config was set up, while her Signal answers were dropped from the file. Her account is in her config:
    setup says so, names the way to change it, and keeps the answers."""
    config = tmp_path / "config.toml"
    if hers:
        her_own_user(no_system_paths)
    else:
        config.write_text(GOOD, encoding="utf-8")
        config.chmod(0o600)
    setup_answers.Answers.load(setup_answers.default_path(), say=print).set("route", "number")
    typing(monkeypatch)
    stop_at_ollama(monkeypatch)
    assert main(["--config", str(config), "--redo", "signal"]) == 0  # nothing else to change: no whole re-run
    out = capsys.readouterr().out
    assert out.count(prompts.SETUP_REDO_SIGNAL) == 1 and saved()["route"] == "number"
    assert out.rstrip().endswith(prompts.SETUP_REDO_NOTHING)
    assert prompts.SETUP_REDO_SIGNAL == (
        "Her Signal account: in her config already, so --redo signal changes nothing on an install set up. "
        "For a Signal Account without a number: /usr/bin/sudo /usr/local/estia/bin/estia signal-login "
        "(setup_assistant.md, 7. Operations)."
    )
