import errno
import io
import plistlib
import re
import stat
import subprocess
import sys
import termios
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

import pytest

from local_assistant import doctor, prompts, setup_ai
from local_assistant import setup as wizard_module
from local_assistant.config import parse_config
from local_assistant.setup import (
    Wizard,
    choose_model,
    main,
    modelfile,
    read_hidden,
    render_config,
)
from local_assistant.setup_questions import plain_text
from tests.setup_helpers import (
    ACI,
    BOT,
    EXAMPLE,
    TERMINAL,
    enter_but_signal,
    fake_signal_cli,
    filled,
    her_own_user,
    noted,
    recorder,
    refuse,
    rerun,
    typed_once,
)
from tests.setup_helpers import Pty as Pty  # tests/test_signal_login.py reads it here

pytestmark = pytest.mark.usefixtures("no_real_ollama", "no_system_paths", "a_terminal")


@pytest.mark.parametrize(
    ("memory_gb", "tag", "context"),
    [
        (16, "gemma4:12b-mlx", 16384),  # Mac mini M4, 16 GB
        (24, "gemma4:12b-mlx", 32768),  # 24 GB: the 26b would not fit next to macOS and the voice models
        (32, "gemma4:26b-mlx", 32768),  # the tested setup
        (48, "gemma4:26b-mlx", 65536),
        (64, "gemma4:26b-mlx", 65536),
    ],
)
def test_the_model_fits_the_macs_memory(memory_gb: float, tag: str, context: int) -> None:
    choice = choose_model(memory_gb)
    assert choice is not None and (choice.tag, choice.num_ctx) == (tag, context)


def test_the_models_are_the_mlx_builds_pinned_by_digest_with_the_ollama_they_need() -> None:
    """Owner, 15 Oct: the explicit -mlx tags, never the bare ones (lists whose pick depends on Ollama's version); each
    needs Ollama 0.33.1 or later (JSON-schema output on the MLX engine). The 12b's line claims no tests until the
    owner's tests on this Mac pass."""
    big, small = choose_model(32), choose_model(16)
    assert big is not None and small is not None
    assert (big.tag, big.digest[:12], small.tag, small.digest[:12]) == (
        "gemma4:26b-mlx",
        "f0fc7e0ae494",
        "gemma4:12b-mlx",
        "ded7a2735003",
    )
    assert all(len(choice.digest) == 64 for choice in (big, small))
    assert big.ollama == small.ollama == "0.33.1"
    assert "the tested model" in big.note and "the tested model" not in small.note
    # owner, 15 Oct: 12B failed some safety tests twice; setup says so on a line of its own and asks first (model())
    assert big.tested and not small.tested and "failed" not in small.note


@pytest.mark.parametrize("memory_gb", [16, 24, 29.9, 30, 32, 64])
def test_a_calls_limits_follow_the_model_setup_chose(memory_gb: float) -> None:
    """The review of 28 Oct (calls-01): the bridge reads the Mac's tier by its memory (`voice.pocket_standard`), as
    setup chose the model by it; a call's answer cap and wait go with that model (owner: shorter beside 12B)."""
    from local_assistant.calls import call_limits
    from local_assistant.voice import pocket_standard

    choice = choose_model(memory_gb)
    assert choice is not None
    small = choice.tag == "gemma4:12b-mlx"
    assert call_limits(standard=pocket_standard(memory_gb)) == ((300, 60.0) if small else (500, 45.0))
    assert call_limits(90.0, standard=pocket_standard(memory_gb))[1] == 90.0  # [calls] turn_timeout_seconds wins


def test_a_mac_with_8_gb_is_too_small() -> None:
    assert choose_model(8) is None


def test_the_modelfile_gives_the_alias_its_context_and_googles_prompt_for_its_size() -> None:
    """Empty answers (17 Oct): Ollama tells Gemma 4's sizes apart by the model's name, and local-worker names none, so
    it got the prompt for the small models (E2B, E4B), without the empty thought block Google's prompt for 12B and
    larger ends with when thinking is off. Both of setup's models are 12B or larger: their alias says so."""
    for memory, tag, context in ((32, "gemma4:26b-mlx", 32768), (16, "gemma4:12b-mlx", 16384)):
        choice = choose_model(memory)
        assert choice is not None
        assert modelfile(choice) == f"FROM {tag}\nPARAMETER num_ctx {context}\nRENDERER gemma4-large\n"


@pytest.mark.parametrize(
    ("tag", "large"),
    [
        ("gemma4:26b-mlx", True),
        ("gemma4:12b-mlx", True),
        ("gemma4:31b", True),
        ("gemma4:e4b", False),  # the small models: Ollama's own pick stays
        ("gemma4:e2b-mlx", False),
        ("qwen3:32b", False),  # not Gemma 4: no line
    ],
)
def test_only_gemma_4_tags_of_12b_or_more_get_the_large_prompt(tag: str, large: bool) -> None:
    from local_assistant.setup import ModelChoice

    assert ("RENDERER gemma4-large" in modelfile(ModelChoice(tag, "0" * 64, 8192, ""))) is large


def test_the_config_is_the_example_with_the_bot_and_the_owner_filled_in() -> None:
    text = render_config(EXAMPLE, BOT, ACI)
    cfg = parse_config(tomllib.loads(text))
    assert cfg.bot_number == BOT and cfg.allowed_acis == frozenset({ACI})
    assert 'bot_number = "+300000000000"' not in text and 'allowed_acis = ["00000000-' not in text


@pytest.mark.parametrize("number", ["0612345678", "+31 6 1234", ""])
def test_a_bad_number_is_refused(number: str) -> None:
    with pytest.raises(ValueError, match="number"):
        render_config(EXAMPLE, number, ACI)


def ollama_lists(monkeypatch: pytest.MonkeyPatch, *names: str) -> list[str]:
    """Ollama's model list as the read-only GET (doctor's) returns it; the URLs asked are recorded."""
    asked: list[str] = []

    def get_json(self: doctor.Probe, url: str) -> object:
        asked.append(url)
        return {"models": [{"name": name} for name in names]}

    monkeypatch.setattr(doctor.Probe, "get_json", get_json)
    return asked


def test_a_dry_run_asks_nothing_runs_nothing_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """setup --dry-run describes every step (model, Signal, account, config, always on, extras) without prompts,
    commands or a missing Ollama, and says what it would write instead of a ✓."""
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    monkeypatch.setattr("shutil.which", lambda name: None)  # a fresh Mac: no ollama, no signal-cli yet
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ollama_lists(monkeypatch)  # Ollama has no model yet
    config = tmp_path / "cfg" / "config.toml"
    assert main(["--dry-run", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert "/api/pull" in out and "register" in out and "verify" in out and "one-time word" in out
    assert "Extra Features" in out and "(asks which ones" in out
    assert "Estia · Maya · Melina · Socrates" in out and "Enter keeps Estia" in out  # the name question, shown
    assert prompts.SETUP_STYLE_DRY_RUN in out and "  hearth: steady, kind and frank." in out
    assert f"(dry run) would write config {config}" in out and "✓ Config" not in out
    assert not (tmp_path / "cfg").exists() and not (tmp_path / "home").exists()


@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_the_model_step_skips_an_existing_local_worker_in_a_dry_run_too(
    dry: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The preview reads the same model list (read-only) as a real run, so it shows the same skip."""
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    asked = ollama_lists(monkeypatch, "gemma4:26b", "local-worker:latest")
    Wizard(tmp_path / "config.toml", dry_run=dry).model()
    out = capsys.readouterr().out
    assert out == "✓ Model: local-worker is there.\n"
    assert asked == ["http://127.0.0.1:11434/api/tags"]


# Setup's two tags as Ollama 0.40.4 lists and shows them (17 Oct, read-only): (architecture, parameter count, size
# shown, bytes in /api/tags). An MLX build's manifest has no model layer, so Ollama names no parent for it, nor for an
# alias made from it, and /api/tags gives no parameter size.
TAGS = {
    "gemma4:26b-mlx": ("gemma4", 26_225_562_962, "26.2B", 18_334_412_658),
    "gemma4:12b-mlx": ("gemma4_unified", 12_382_568_756, "12.4B", 7_714_091_504),
}
GEMMA_PARAMETERS = [
    "temperature                    1",
    "top_k                          64",
    "top_p                          0.95",
]


def show_of(
    tag: str, *, name: str = "", renderer: str = "gemma4", parameters: list[str] | None = None
) -> dict[str, Any]:
    """Ollama's /api/show answer for one of setup's tags, or for an alias made from it (`name`), as it comes."""
    architecture, count, size, _bytes = TAGS[tag]
    modelfile = (
        f'# Modelfile generated by "ollama show"\nFROM {name or tag}\nTEMPLATE {{{{ .Prompt }}}}\nRENDERER {renderer}\n'
        "PARSER gemma4\nCAPABILITY completion\n"
    )
    return {
        "modelfile": modelfile,
        "parameters": "\n".join(GEMMA_PARAMETERS if parameters is None else parameters),
        "template": "{{ .Prompt }}",
        "details": {
            "parent_model": "",
            "format": "safetensors",
            "family": architecture,
            "families": None,
            "parameter_size": size,
            "quantization_level": "nvfp4",
        },
        "model_info": {"general.architecture": architecture, "general.parameter_count": count},
        "requires": "0.31.0",
    }


class OldAlias:
    """Ollama with local-worker made before 0.9.3 (read-only answers as doctor's Probe gets them): its entry in
    /api/tags, its Modelfile in /api/show, and after `ollama create` its new digest. By default the owner's: a copy
    of the 26b (`ollama cp`, the tag's digest). With `num_ctx`: one setup made, FROM `base` and PARAMETER num_ctx,
    which has its own digest and a few bytes more; `changes` then alters what its /api/show answer holds."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        base: str = "gemma4:26b-mlx",
        renderer: str = "gemma4",
        num_ctx: int | None = None,
        digest: str = "",
        extra_bytes: int = 16,  # "num_ctx":32768, in its parameters
        changes: dict[str, Any] | None = None,
        listed: dict[str, str] | None = None,
    ) -> None:
        from local_assistant.setup import GEMMA_12B, GEMMA_12B_DIGEST, GEMMA_26B, GEMMA_26B_DIGEST

        tested = {GEMMA_26B: GEMMA_26B_DIGEST, GEMMA_12B: GEMMA_12B_DIGEST}
        copy = num_ctx is None and not digest
        self.digest = tested[base] if copy else digest or "ef" * 32
        self.bytes = TAGS[base][3] + (0 if copy else extra_bytes)
        parameters = [*GEMMA_PARAMETERS[::-1], *([f"num_ctx                        {num_ctx}"] if num_ctx else [])]
        self.alias = show_of(base, name="local-worker:latest", renderer=renderer, parameters=parameters)
        for key, value in (changes or {}).items():  # "details.quantization_level": a key inside a part
            part, _, inner = key.partition(".")
            if inner and part in ("details", "model_info"):
                self.alias[part] = {**self.alias[part], inner: value}
            else:
                self.alias[key] = value
        self.tags = listed if listed is not None else tested
        self.shown: list[object] = []
        monkeypatch.setattr(doctor.Probe, "get_json", lambda probe, url: self.list())
        monkeypatch.setattr(doctor.Probe, "post_json", lambda probe, url, payload: self.show(url, payload))
        monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)

    def list(self) -> object:
        details = {"parent_model": "", "format": "safetensors", "parameter_size": "", "quantization_level": "nvfp4"}
        alias = {"name": "local-worker:latest", "digest": self.digest, "size": self.bytes, "details": details}
        tags = (
            {"name": name, "digest": digest, "size": TAGS[name][3], "details": details}
            for name, digest in self.tags.items()
        )
        return {"models": [alias, *tags]}

    def show(self, url: str, payload: object) -> object:
        self.shown.append((url, payload))
        name = payload.get("model") if isinstance(payload, dict) else None
        if name in ("local-worker", "local-worker:latest"):
            return self.alias
        if isinstance(name, str) and name in self.tags:
            return show_of(name)
        raise OSError("model not found")


def remake(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, *, dry: bool = False, made: bool = True
) -> tuple[Wizard, list[list[str]], list[str], list[str]]:
    """The model step on a re-run, the person answering `answer`: what ran, what was asked, and each Modelfile."""
    ran: list[list[str]] = []
    asked: list[str] = []
    specs: list[str] = []

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        command = [str(arg) for arg in args[-1]] if isinstance(args[-1], list) else []
        ran.append(command)
        if "-f" in command:
            specs.append(Path(command[command.index("-f") + 1]).read_text(encoding="utf-8"))
        return subprocess.CompletedProcess(command, 0 if made else 1, "", "")

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        return answer

    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    monkeypatch.setattr("local_assistant.harden.in_terminal", lambda: True)  # the person's own Terminal: Enter is yes
    wizard = Wizard(tmp_path / "config.toml", dry_run=dry)
    wizard.ollama_program = "/opt/example/ollama"
    if not dry:
        monkeypatch.setattr(wizard, "run", run)
    monkeypatch.setattr("builtins.input", refuse if dry else type_answer)
    return wizard, ran, asked, specs


def test_a_rerun_makes_an_alias_with_the_small_models_prompt_again_on_a_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Empty answers (17 Oct): an alias from before 0.9.3 has the small models' prompt. On a re-run setup reads its
    Modelfile (read-only), says so, and on a yes makes it again from the same model with only the prompt changed: its
    context stays as it was (none here: the Ollama service's). The new digest is printed with the pin's line."""
    ollama = OldAlias(monkeypatch)
    wizard, ran, asked, specs = remake(tmp_path, monkeypatch, "y")
    original = wizard.run

    def create_then_new_digest(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        done = original(command)
        ollama.digest = "cd" * 32
        return done

    monkeypatch.setattr(wizard, "run", create_then_new_digest)
    wizard.model()
    out = capsys.readouterr().out
    assert asked == [f"{prompts.SETUP_MODEL_REMAKE_ASK} [Y/n] "]
    assert ran == [["/opt/example/ollama", "create", "local-worker", "-f", str(tmp_path / ".local-worker.Modelfile")]]
    assert specs == ["FROM gemma4:26b-mlx\nRENDERER gemma4-large\n"]
    assert not (tmp_path / ".local-worker.Modelfile").exists()
    assert ollama.shown == [("http://127.0.0.1:11434/api/show", {"model": "local-worker"})]
    assert prompts.SETUP_MODEL_SMALL_PROMPT in out and "✓ Model: local-worker is there." not in out
    assert (
        plain_text(prompts.SETUP_MODEL_REMAKE.format(tag="gemma4:26b-mlx", stop=prompts.SETUP_MODEL_STOP_YOURS)) in out
    )
    assert prompts.SETUP_MODEL_REMADE in out and f"Its new digest: sha256:{'cd' * 32}." in out
    pin = prompts.SETUP_MODEL_REMADE_PIN.format(
        digest="cd" * 32, where=prompts.SETUP_MODEL_PIN_YOURS.format(path=tmp_path / "config.toml")
    )
    assert pin in out and f'model_digest = "sha256:{"cd" * 32}"' in pin


@pytest.mark.parametrize(
    ("base", "num_ctx"),
    [("gemma4:26b-mlx", 32768), ("gemma4:26b-mlx", 65536), ("gemma4:12b-mlx", 16384), ("gemma4:12b-mlx", 32768)],
)
def test_an_alias_setup_made_is_traced_by_what_it_holds_and_keeps_its_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, base: str, num_ctx: int
) -> None:
    """Review, 17 Oct (I1): setup before 0.9.3 made local-worker FROM its tag with PARAMETER num_ctx, so the alias has
    a digest of its own, and Ollama names no parent for an MLX build (parent_model ""). It is traced by what it holds:
    the tag's weights (architecture, parameter count, format, quantization, read from /api/show), its template and
    parameters but num_ctx, and a size within a few bytes of the tag's. Only the RENDERER line is new."""
    ollama = OldAlias(monkeypatch, base=base, num_ctx=num_ctx)
    wizard, ran, asked, specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    assert asked == [f"{prompts.SETUP_MODEL_REMAKE_ASK} [Y/n] "] and len(ran) == 1
    assert specs == [f"FROM {base}\nPARAMETER num_ctx {num_ctx}\nRENDERER gemma4-large\n"]
    assert ("http://127.0.0.1:11434/api/show", {"model": base}) in ollama.shown


@pytest.mark.parametrize(
    ("changes", "extra_bytes"),
    [
        ({"model_info.general.parameter_count": 25_805_933_000}, 16),  # another model of the same family
        ({"model_info.general.architecture": "gemma3"}, 16),
        ({"details.quantization_level": "bf16"}, 16),  # the same weights at another precision
        ({"details.format": "gguf"}, 16),
        ({"parameters": "temperature 0.7\ntop_k 64\ntop_p 0.95\nnum_ctx 32768"}, 16),  # a setting of its own: lost
        ({"system": "You are a pirate."}, 40),  # a system prompt of its own: lost
        ({"template": "{{ .System }} {{ .Prompt }}"}, 16),
        ({}, 52_428_800),  # 50 MB more: an adapter or a draft model, which the tag does not have
        ({"model_info": {}}, 16),  # weights that cannot be read
    ],
    ids=["count", "architecture", "quantization", "format", "parameter", "system", "template", "bytes", "unread"],
)
def test_an_alias_that_holds_more_or_other_than_the_tag_is_left_with_the_way_by_hand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changes: dict[str, Any],
    extra_bytes: int,
) -> None:
    """Never guessed: an alias with other weights, or with settings a remake from the tag would drop, is not made
    again by setup."""
    OldAlias(monkeypatch, num_ctx=32768, changes=changes, extra_bytes=extra_bytes)
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    out = capsys.readouterr().out
    assert ran == [] and asked == []
    assert prompts.SETUP_MODEL_SMALL_PROMPT in out and prompts.SETUP_MODEL_PROMPT_BY_HAND in out


def test_an_alias_whose_tag_does_not_answer_show_is_left_with_the_way_by_hand(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ollama = OldAlias(monkeypatch, num_ctx=32768)
    alias = ollama.show

    def only_the_alias(url: str, payload: object) -> object:
        if isinstance(payload, dict) and payload.get("model") != "local-worker":
            raise OSError("connection refused")
        return alias(url, payload)

    monkeypatch.setattr(doctor.Probe, "post_json", lambda probe, url, payload: only_the_alias(url, payload))
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    assert ran == [] and asked == [] and prompts.SETUP_MODEL_PROMPT_BY_HAND in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["n", "no"])  # Enter is the recommended yes (owner's call, 29 Oct)
def test_no_leaves_the_alias_as_it_is(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    OldAlias(monkeypatch)
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, answer)
    wizard.model()
    assert ran == [] and len(asked) == 1 and prompts.SETUP_MODEL_REMAKE_LATER in capsys.readouterr().out


def test_a_dry_run_shows_the_question_and_the_command_and_makes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for target in ("local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    OldAlias(monkeypatch)
    wizard, _ran, _asked, _specs = remake(tmp_path, monkeypatch, "", dry=True)
    wizard.model()
    out = capsys.readouterr().out
    assert prompts.SETUP_ASKS.format(ask=f"{prompts.SETUP_MODEL_REMAKE_ASK} [Y/n]") in out
    assert "  $ /opt/example/ollama create local-worker -f" in out
    assert not (tmp_path / ".local-worker.Modelfile").exists()


@pytest.mark.parametrize(("num_ctx", "extra_bytes"), [(None, 0), (32768, 16)], ids=["copy", "own context"])
def test_an_alias_whose_tag_is_no_longer_the_tested_build_is_left_with_the_way_by_hand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    num_ctx: int | None,
    extra_bytes: int,
) -> None:
    """Its tag listed with another digest (Ollama's library changed it since): never made again from it."""
    OldAlias(
        monkeypatch, num_ctx=num_ctx, digest="ab" * 32, extra_bytes=extra_bytes, listed={"gemma4:26b-mlx": "ab" * 32}
    )
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    out = capsys.readouterr().out
    assert ran == [] and asked == []
    assert prompts.SETUP_MODEL_SMALL_PROMPT in out and prompts.SETUP_MODEL_PROMPT_BY_HAND in out


def test_an_alias_with_googles_prompt_is_simply_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    OldAlias(monkeypatch, renderer="gemma4-large")
    wizard, ran, _asked, _specs = remake(tmp_path, monkeypatch, "y")
    monkeypatch.setattr("builtins.input", refuse)
    wizard.model()
    assert capsys.readouterr().out == "✓ Model: local-worker is there.\n" and ran == []


def test_in_her_own_user_the_pin_line_says_how_to_edit_her_config(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    her_own_user(no_system_paths)
    OldAlias(monkeypatch)
    wizard, _ran, _asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.model()
    out = capsys.readouterr().out
    assert (
        plain_text(prompts.SETUP_MODEL_REMAKE.format(tag="gemma4:26b-mlx", stop=prompts.SETUP_MODEL_STOP_HERS)) in out
    )
    assert prompts.SETUP_MODEL_PIN_HERS in out


def test_through_an_ollama_setup_refused_nothing_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    OldAlias(monkeypatch)
    wizard, ran, asked, _specs = remake(tmp_path, monkeypatch, "y")
    wizard.ollama_refused = True
    wizard.model()
    out = capsys.readouterr().out
    assert ran == [] and asked == [] and prompts.SETUP_MODEL_REMAKE_AFTER_OLLAMA in out


def test_a_failed_remake_leaves_the_alias_and_setup_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    OldAlias(monkeypatch)
    wizard, ran, _asked, _specs = remake(tmp_path, monkeypatch, "y", made=False)
    wizard.model()  # no SystemExit: the old alias still answers
    out = capsys.readouterr().out
    assert len(ran) == 1 and prompts.SETUP_MODEL_REMAKE_FAILED in out and "Its new digest" not in out
    assert not (tmp_path / ".local-worker.Modelfile").exists()


GOOD = f'bot_number = "+306900000000"\nallowed_acis = ["{ACI}"]\n'
HAND_WRITTEN = '\n[mail]\ntenant_id = "example-tenant-kept-by-hand"\n'


@pytest.mark.parametrize(
    ("text", "mode", "error"),
    [
        (GOOD + 'timezone = "Europe/Amsterdm"\n' + HAND_WRITTEN, 0o600, "unknown timezone"),  # one typo
        (GOOD + HAND_WRITTEN, 0o644, "must be private"),  # readable by others
    ],
    ids=["typo", "readable"],
)
@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_setup_stops_before_anything_when_the_config_is_there_but_does_not_load(
    text: str,
    mode: int,
    error: str,
    dry: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A hand-edited config with one mistake is never replaced by the example: setup names the error and stops."""
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)
    steps: list[str] = []

    def model(self: Wizard) -> None:
        steps.append("model")

    def signal_number(self: Wizard) -> str:
        steps.append("signal number")
        return BOT

    def owner(self: Wizard, number: str) -> str:
        steps.append("owner")
        return ACI

    def later(self: Wizard) -> None:
        steps.append("later step")

    def check(self: Wizard) -> int:
        steps.append("check")
        return 0

    for name, step in [("model", model), ("signal_number", signal_number), ("owner", owner), ("check", check)]:
        monkeypatch.setattr(Wizard, name, step)
    monkeypatch.setattr(Wizard, "always_on", later)
    monkeypatch.setattr(Wizard, "extras", later)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert main(["--config", str(path), *(["--dry-run"] if dry else [])]) == 1
    assert steps == []  # not the model, not Signal, nothing written
    assert path.read_text(encoding="utf-8") == text and stat.S_IMODE(path.stat().st_mode) == mode
    assert [p.name for p in tmp_path.iterdir()] == ["config.toml"]
    out = capsys.readouterr().out
    assert error in out and "scripts/doctor.sh" in out


def test_replacing_a_config_keeps_a_private_copy_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The second guard: a config that loads but still has the example's placeholders is copied before it is
    replaced (a dry run only says it would)."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # the language question: Enter keeps English
    path = tmp_path / "config.toml"
    path.write_text(EXAMPLE, encoding="utf-8")
    path.chmod(0o600)
    Wizard(path, dry_run=True).config(BOT, ACI)
    assert "would keep a copy" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["config.toml"]
    Wizard(path, dry_run=False).config(BOT, ACI)
    copies = list(tmp_path.glob("config.toml.bak-*"))
    assert len(copies) == 1 and copies[0].read_text(encoding="utf-8") == EXAMPLE
    assert stat.S_IMODE(copies[0].stat().st_mode) == 0o600
    assert f'bot_number = "{BOT}"' in path.read_text(encoding="utf-8")
    assert str(copies[0]) in capsys.readouterr().out


@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_the_config_step_leaves_soul_md_to_the_user(
    dry: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No soul.md is written: her built-in style is the default until the user writes their own."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # the language question: Enter keeps English
    path = tmp_path / "config.toml"
    Wizard(path, dry_run=dry).config(BOT, ACI)
    assert not (tmp_path / "soul.md").exists()
    assert "soul.example.md" not in capsys.readouterr().out  # said at the end, where her folder is known


def test_setup_ends_with_what_done_means(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The check in the terminal is not the end: done is her welcome on the phone, and a reply to it."""
    from local_assistant import setup

    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr(Wizard, "check", lambda self: 0)  # doctor's real probes are tested in test_doctor
    monkeypatch.setattr(Wizard, "always_on", lambda self: True)  # it looks at /Library/LaunchDaemons
    assert setup.main(["--dry-run", "--config", str(tmp_path / "config.toml")]) == 0
    last = capsys.readouterr().out.strip().splitlines()[-1]
    assert "welcome" in last and "no ✗" in last and "Reply to it" in last


def test_ctrl_c_at_a_question_stops_setup_with_one_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C at a question (seen in a fresh-install test, 16 Oct): one plain line on how to go on and the shell's
    usual 130, never Python's traceback."""
    from local_assistant import setup

    def ctrl_c(self: Wizard) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(Wizard, "ollama", ctrl_c)
    monkeypatch.setattr("builtins.input", enter_but_signal)  # the plan's questions come first: Enter
    assert setup.main(["--config", str(tmp_path / "config.toml")]) == 130
    captured = capsys.readouterr()
    assert captured.out.strip().splitlines()[-1] == prompts.SETUP_STOPPED
    assert "Traceback" not in captured.out + captured.err
    assert "scripts/setup.sh" in prompts.SETUP_STOPPED and "skipped" in prompts.SETUP_STOPPED


def test_the_terminal_window_closed_ends_setup_the_way_ctrl_c_does(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """stops-reruns-terminal-closed: at the desk the setup window looks like the installer, so the friend closes
    Terminal, and macOS's hang-up killed setup with no word and no cleanup. The hang-up takes Ctrl-C's way out (the
    terminal's mode, the answers file's lock and the window put back), and the handler is put back after."""
    import os
    import signal as signals

    from local_assistant import setup

    before = signals.getsignal(signals.SIGHUP)

    def terminal_closed(self: Wizard) -> None:
        os.kill(os.getpid(), signals.SIGHUP)
        time.sleep(2)  # the handler runs here, in the main thread
        raise AssertionError("the hang-up did not end the step")

    monkeypatch.setattr(Wizard, "ollama", terminal_closed)
    monkeypatch.setattr("builtins.input", enter_but_signal)
    assert setup.main(["--config", str(tmp_path / "config.toml")]) == 130
    assert signals.getsignal(signals.SIGHUP) is before
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err and "did not end the step" not in captured.out + captured.err


@pytest.mark.parametrize("profile", [True, False], ids=["path", "no-path"])
def test_a_file_setup_cannot_use_is_a_stop_never_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], profile: bool
) -> None:
    """mac-ollama-home-unwritable-crash (round 1): a login profile owned by root, or a read-only folder, ended setup
    with a Python traceback at the same place on every run. Any file error a step did not handle is a stop with the
    path and the fix, the answers kept."""
    from local_assistant import setup

    path = str(Path.home() / ".zprofile") if profile else None

    def unwritable(self: Wizard) -> None:
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(Wizard, "ollama", unwritable)
    monkeypatch.setattr("builtins.input", enter_but_signal)
    with pytest.raises(SystemExit) as stopped:
        setup.main(["--config", str(tmp_path / "config.toml")])
    words = str(stopped.value)
    if path is not None:
        assert words.startswith(f"✗ Setup: could not use {path} (Permission denied).")
        assert "Fix: give your account Read and Write on it (Finder: Get Info, then Sharing and Permissions; " in words
    else:
        assert words.startswith("✗ Setup: a file error stopped it (Permission denied).")
    assert "run setup again: your answers are kept." in words
    out = capsys.readouterr().out
    assert "Traceback" not in out and "✗ Setup:" in out  # said once, by the face


def test_setup_os_error_outside_home_no_finder_fix(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Review of 30 Oct (P07): every file error got "give your account Read and Write in Finder", her folders too
    (advice that weakens her account's isolation), a missing file or a full disk too. The Finder fix is only for a
    file of your own home your account may not use, with the key that shows a dot name (Finder hides ~/.zprofile)."""
    from local_assistant.setup import os_error_stop

    monkeypatch.setenv("HOME", str(tmp_path))
    hers = os_error_stop(PermissionError(13, "Permission denied", "/var/estia/x"))
    assert hers.startswith("✗ Setup: could not use /var/estia/x (Permission denied).")
    assert "Read and Write" not in hers and "Finder" not in hers
    assert "Fix: run setup again: your answers are kept. If it says the same, report it (SECURITY.md)." in hers
    for other in (FileNotFoundError(2, "No such file or directory", str(tmp_path / "x")),
                  OSError(28, "No space left on device", str(tmp_path / "x"))):  # fmt: skip
        assert "Read and Write" not in os_error_stop(other)
    dotted = os_error_stop(PermissionError(13, "Permission denied", str(tmp_path / ".zprofile")))
    assert "Read and Write" in dotted and "Command-Shift-. (period)" in dotted
    plain = os_error_stop(PermissionError(1, "Operation not permitted", str(tmp_path / "Projects/x")))
    assert "Read and Write" in plain and "Command-Shift" not in plain
    escape = os_error_stop(PermissionError(13, "Permission denied", f"{tmp_path}/../../var/estia/x"))
    assert "Read and Write" not in escape


@pytest.mark.parametrize("folder", ["Documents", "Desktop", "Downloads", "documents"])
def test_setup_os_error_guarded_folder_names_privacy_and_security(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, folder: str
) -> None:
    """setup-macos-04: EPERM in Documents, Desktop or Downloads is macOS's privacy guard (doctor's _GUARDED), which
    Finder's Sharing and Permissions does not change; it got the Finder fix. It names Privacy & Security; EPERM
    elsewhere in your home (a root-owned ~/.zprofile) and EACCES there keep the Finder fix."""
    from local_assistant.setup import os_error_stop

    monkeypatch.setenv("HOME", str(tmp_path))
    path = str(tmp_path / folder / "x")
    guarded = os_error_stop(PermissionError(1, "Operation not permitted", path))
    assert guarded == (
        f"✗ Setup: could not use {path} (Operation not permitted).\n"
        "  Fix: System Settings → Privacy & Security → Files & Folders: let the app setup runs in (Terminal, or your"
        " AI tool) use that folder, then run setup again: your answers are kept."
    )
    assert "Read and Write" not in guarded and "Finder" not in guarded
    dotted = os_error_stop(PermissionError(1, "Operation not permitted", str(tmp_path / ".zprofile")))
    assert "Read and Write" in dotted and "Command-Shift-. (period)" in dotted and "Privacy" not in dotted
    access = os_error_stop(PermissionError(13, "Permission denied", path))
    assert "Read and Write" in access and "Privacy" not in access


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT], ids=["ENOSPC", "EDQUOT"])
def test_setup_os_error_full_disk_says_free_space(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, code: int) -> None:
    """Fix review (dialogue-04): a full disk got "run setup again … If it says the same, report it (SECURITY.md)":
    running again cannot free space, and SECURITY.md is the route for a vulnerability. It gets the storage fix, in
    setup's disk words (System Settings → General → Storage), with a path or without."""
    from local_assistant.setup import os_error_stop

    monkeypatch.setenv("HOME", str(tmp_path))
    for full in (OSError(code, "No space left on device", str(tmp_path / ".config/estia-setup/answers.toml")),
                 OSError(code, "No space left on device")):  # fmt: skip
        said = os_error_stop(full)
        assert said.startswith("✗ Disk: the Mac ran out of space (No space left on device")
        assert "Fix: free space (System Settings → General → Storage), then run setup again" in said
        assert "SECURITY.md" not in said and "report it" not in said and "Read and Write" not in said


def test_setup_says_the_configured_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A config that names her (the owner's Tiffany): the banner and the last line use that name; none: Estia."""
    from local_assistant import setup

    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "check", lambda self: 0)
    monkeypatch.setattr(Wizard, "always_on", lambda self: True)
    monkeypatch.setattr(Wizard, "extras", lambda self: None)
    path = tmp_path / "config.toml"
    path.write_text(GOOD + 'name = "Tiffany"\n', encoding="utf-8")
    path.chmod(0o600)
    assert setup.main(["--dry-run", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Tiffany setup: the questions first") and "Tiffany's welcome" in out
    assert "Estia" not in out
    path.unlink()
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("builtins.input", refuse)
    assert setup.main(["--dry-run", "--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Estia setup:") and "Estia's number" in out and "like estia-4821" in out
    assert "Estia listens and answers" in out and "Tiffany setup" not in out and "Tiffany's" not in out


def test_the_config_gets_the_chosen_language() -> None:
    assert parse_config(tomllib.loads(render_config(EXAMPLE, BOT, ACI))).language == "en"
    text = render_config(EXAMPLE, BOT, ACI, language="nl")
    assert parse_config(tomllib.loads(text)).language == "nl" and 'language = "en"' not in text
    with pytest.raises(ValueError, match="language"):
        render_config(EXAMPLE.replace('language = "en"', ""), BOT, ACI, language="nl")


@pytest.mark.parametrize(("answers", "language"), [([""], "en"), (["dutch"], "nl"), (["klingon", "ja"], "ja")])
def test_the_wizard_asks_for_the_language_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    answers: list[str],
    language: str,
) -> None:
    from local_assistant.setup import Wizard

    given = iter(answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(given, ""))  # then Enter: no projects
    path = tmp_path / "config" / "config.toml"
    Wizard(path, dry_run=False).config(BOT, ACI)
    assert parse_config(tomllib.loads(path.read_text(encoding="utf-8"))).language == language
    out = capsys.readouterr().out
    assert "Nederlands, Dutch (nl)" in out and "日本語, Japanese (ja)" in out and "/language" in out


def test_the_config_names_the_context_local_worker_was_made_with() -> None:
    """The 16 GB tier's 16,384 tokens (review I24): each request is kept within it; left out, none."""
    text = render_config(EXAMPLE, BOT, ACI, context_tokens=16_384)
    assert parse_config(tomllib.loads(text)).context_tokens == 16_384
    assert "\ncontext_tokens = 16384" in text and "#context_tokens" not in text
    assert parse_config(tomllib.loads(render_config(EXAMPLE, BOT, ACI))).context_tokens is None
    with pytest.raises(ValueError, match="context_tokens"):
        render_config(EXAMPLE.replace("#context_tokens = 32768", ""), BOT, ACI, context_tokens=16_384)


def test_the_wizard_reads_local_workers_context_from_ollama(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant.setup import Wizard

    def show(probe: doctor.Probe, url: str, payload: object) -> object:
        assert url.endswith("/api/show") and payload == {"model": "local-worker"}
        return {"parameters": 'num_ctx                        16384\nstop                           "<eos>"'}

    monkeypatch.setattr(doctor.Probe, "post_json", show)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    path = tmp_path / "config" / "config.toml"
    Wizard(path, dry_run=False).config(BOT, ACI)
    assert parse_config(tomllib.loads(path.read_text(encoding="utf-8"))).context_tokens == 16_384


def test_the_config_gets_the_chosen_style() -> None:
    """[personality] style: the example's hearth line, or playful when chosen (the rest of the example as it is)."""
    assert parse_config(tomllib.loads(render_config(EXAMPLE, BOT, ACI))).personality_style == "hearth"
    text = render_config(EXAMPLE, BOT, ACI, style="playful")
    assert parse_config(tomllib.loads(text)).personality_style == "playful" and 'style = "hearth"' not in text
    assert text.replace('style = "playful"', 'style = "hearth"') == render_config(EXAMPLE, BOT, ACI)
    with pytest.raises(ValueError, match="style"):
        render_config(EXAMPLE, BOT, ACI, style="warm")
    with pytest.raises(ValueError, match="style"):
        render_config(EXAMPLE.replace('style = "hearth"', ""), BOT, ACI, style="playful")


@pytest.mark.parametrize(
    ("answers", "style"),
    [([""], "hearth"), (["playful"], "playful"), (["Playful "], "playful"), (["p"], "playful"), (["x", "h"], "hearth")],
)
def test_the_wizard_asks_her_style_once_after_the_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answers: list[str], style: str
) -> None:
    given = iter(["", *answers])  # the language first: Enter keeps English; then Enter: no projects
    asked: list[str] = []

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        return next(given, "")

    monkeypatch.setattr("builtins.input", type_answer)
    path = tmp_path / "config" / "config.toml"
    wizard = Wizard(path, dry_run=False)
    wizard.config(BOT, ACI)
    assert parse_config(tomllib.loads(path.read_text(encoding="utf-8"))).personality_style == style == wizard.style
    out = capsys.readouterr().out
    assert "  hearth: steady, kind and frank." in out and "  playful: warm, curious and playful." in out
    assert out.index("\nLanguage\n") < out.index("\nStyle\n") < out.index("Projects (extra")
    assert f"✓ Style: {style}." in out and sum("Enter keeps hearth" in prompt for prompt in asked) == len(answers)
    assert not (tmp_path / "config" / "soul.md").exists()  # owner's D2: her style file stays the user's to write


def test_each_style_is_one_plain_line() -> None:
    lines = prompts.SETUP_STYLE.format(name="Estia").splitlines()[2:]
    assert [line.split(":")[0].strip() for line in lines] == ["hearth", "playful"]
    for line in lines:
        assert len(line) <= 120 and "you" not in line.lower().split() and "&" not in line, line
    assert "hearth: steady, kind and frank." in lines[0] and "a joke only when asked for fun or on banter" in lines[0]
    assert "playful: warm, curious and playful." in lines[1] and "never on a worry" in lines[1]


def test_a_dry_run_shows_the_style_question_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", refuse)
    wizard = Wizard(tmp_path / "config.toml", dry_run=True)
    assert wizard.her_style() == "hearth"
    out = capsys.readouterr().out
    assert "\nStyle\n" in out and prompts.SETUP_STYLE_DRY_RUN in out and "✓ Style" not in out


def test_in_her_own_user_setup_asks_her_style_and_prints_the_line_to_add(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Her config is hers alone: a re-run in her own user asks the style, then prints the lines to add as her and
    offers to open her config as her (Enter runs it, owner 27 Oct); it writes nothing."""
    her_own_user(no_system_paths)
    config = tmp_path / "config.toml"
    rerun(monkeypatch, config, typed="playful")
    out = capsys.readouterr().out
    lines = prompts.SETUP_STYLE_HERS.format(style="playful", save=prompts.NANO_SAVE)
    assert lines in out and '    [personality]\n    style = "playful"\n' in out
    assert f"  $ {prompts.HER_CONFIG_EDIT}\n" in out and "/usr/bin/sudo" not in lines
    assert out.index("\nStyle\n") < out.index("Personality:") < out.index(": the check\n")
    assert not config.exists() and not (tmp_path / "soul.md").exists()


def test_in_her_own_user_a_dry_run_shows_the_style_question_and_the_lines_it_would_print(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    her_own_user(no_system_paths)
    monkeypatch.setattr("builtins.input", refuse)
    Wizard(tmp_path / "config.toml", dry_run=True).personality()
    out = capsys.readouterr().out
    assert prompts.SETUP_STYLE_DRY_RUN in out and prompts.SETUP_STYLE_HERS_DRY_RUN in out


def test_a_first_setup_asks_her_style_once_even_after_the_move(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The config step asked it and wrote it, and the move took that config with her: the end asks nothing again."""
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.config(BOT, ACI)
    her_own_user(no_system_paths)  # always on: the move
    monkeypatch.setattr("builtins.input", refuse)
    wizard.personality()
    out = capsys.readouterr().out
    assert out.count("How Estia sounds.") == 1 and 'style = "' not in out


def test_the_ai_briefing_takes_the_style_question_from_the_list() -> None:
    """Owner, 29 Oct: the AI's guide borrows setup's screens; the style question's place and words are the list's."""
    from local_assistant import setup_plan

    briefing = (Path(__file__).resolve().parents[1] / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")
    briefing = briefing.split("## 3. Brief each screen from the source", 1)[1].split("\n## ", 1)[0]
    assert "`setup_plan.QUESTIONS`" in briefing and "**Her style**" not in briefing
    # in setup's plan, in its order (review PROM-05): the language, her style, calls, then the Extra Features
    ids = [q.id for q in setup_plan.QUESTIONS]
    assert ids.index("language") < ids.index("style") < ids.index("calls") < ids.index("extras")
    style = setup_plan.question("style")
    assert [option.id for option in style.options] == ["hearth", "playful"] and style.recommended == "hearth"


def named_wizard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str], *, dry: bool = False
) -> tuple[Wizard, list[str]]:
    """The name step with a person who types `answers` in order (then no terminal); the prompts asked are recorded."""
    typed = iter(answers)
    asked: list[str] = []

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        try:
            return next(typed)
        except StopIteration:
            raise EOFError from None

    monkeypatch.setattr("builtins.input", type_answer)
    return Wizard(tmp_path / "config.toml", dry_run=dry), asked


def voice_on(text: str) -> str:
    """The rendered config with its [voice] block turned on (the # taken off [voice] and its voice line)."""
    return re.sub(r"^#(\[voice\]|voice = )", r"\1", text, flags=re.MULTILINE)


def test_enter_keeps_estia_with_a_female_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """One question with the suggestions; Enter keeps Estia (and heart, Pocket TTS's: the default English voice for a
    female name where its 100M model runs, 32 GB in the tests; owner, 27 Oct: "do heart, and make it default"), written
    into the config."""
    # the name, the language, her style, then whether she sees projects: Enter for each
    wizard, asked = named_wizard(tmp_path, monkeypatch, ["", "", "", ""])
    wizard.her_name()
    assert (wizard.name, wizard.voice) == ("Estia", "heart")
    wizard.config(BOT, ACI)
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert parse_config(tomllib.loads(text)).name == "Estia"
    assert parse_config(tomllib.loads(voice_on(text))).voice_name == "heart"
    out = capsys.readouterr().out
    assert "Estia · Maya · Melina · Socrates" in out and "Estia (recommended)." in out  # four names, owner 27 Oct
    assert "Female or male" not in " ".join(asked) and len(asked) == 4


@pytest.mark.parametrize(
    ("answer", "name", "voice"),
    # a male name Pocket TTS's alba (owner, 26 Oct: "alba & michael for males"), a female one heart (27 Oct)
    [("Socrates", "Socrates", "alba"), ("socrates", "Socrates", "alba"), ("Melina", "Melina", "heart")],
)
def test_a_suggested_name_brings_its_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, name: str, voice: str
) -> None:
    wizard, asked = named_wizard(tmp_path, monkeypatch, [answer, ""])
    wizard.her_name()
    assert (wizard.name, wizard.voice) == (name, voice)
    wizard.config(BOT, ACI)
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert parse_config(tomllib.loads(text)).name == name
    assert parse_config(tomllib.loads(voice_on(text))).voice_name == voice
    assert not any("Female or male" in prompt for prompt in asked)  # a suggestion needs no voice question


@pytest.mark.parametrize(("choice", "voice"), [("m", "alba"), ("male", "alba"), ("", "heart")])
def test_a_typed_name_asks_for_the_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, choice: str, voice: str
) -> None:
    wizard, asked = named_wizard(tmp_path, monkeypatch, ["Mary-Jane Zoë", choice])
    wizard.her_name()
    assert (wizard.name, wizard.voice) == ("Mary-Jane Zoë", voice)
    assert asked[-1] == f"{prompts.SETUP_VOICE_ASK} " and asked[-1].endswith("(Enter: female) ")


def test_a_bad_name_is_asked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The config's name rule: 1-20 letters, spaces or hyphens. A name that breaks it is named and asked again."""
    wizard, asked = named_wizard(tmp_path, monkeypatch, ["R2-D2", "x" * 21, "Juno", "f"])
    wizard.her_name()
    assert (wizard.name, wizard.voice) == ("Juno", "heart")
    out = capsys.readouterr().out
    assert "1 to 20 letters, spaces or hyphens." in out  # the owner's words (27 Oct), only as the error
    assert sum("Enter keeps Estia" in prompt for prompt in asked) == 3


def test_a_dry_run_shows_the_name_question_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", refuse)
    wizard = Wizard(tmp_path / "config.toml", dry_run=True)
    wizard.her_name()
    assert (wizard.name, wizard.voice) == ("Estia", "heart")
    out = capsys.readouterr().out
    assert "Estia · Maya · Melina · Socrates" in out and "Enter keeps Estia" in out


@pytest.mark.parametrize("answers", [["", "", "", ""], ["Melina", ""], ["Juno", "f"]], ids=["estia", "melina", "typed"])
def test_a_female_name_on_a_mac_with_the_small_model_brings_heart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: list[str]
) -> None:
    """Owner, 29 Oct: "on 16gb make Heart the default as well" (its own clone on the 18M; this replaces 27 Oct's "for
    small, do Jane"): a female name on a 16 GB Mac writes heart, as on a Mac with the 100M model."""
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 16.0)
    wizard, _ = named_wizard(tmp_path, monkeypatch, answers)
    wizard.her_name()
    assert wizard.voice == "heart"
    wizard.config(BOT, ACI)
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert parse_config(tomllib.loads(voice_on(text))).voice_name == "heart"
    from local_assistant import setup

    assert setup.female_voice(16.0) == "heart" and setup.female_voice(32.0) == "heart"
    assert setup.female_voice(29.9) == "heart" and setup.female_voice(30.0) == "heart"
    with pytest.raises(ValueError, match="the voice must be heart or alba"):  # jane is no longer setup's to write
        render_config(EXAMPLE, BOT, ACI, voice="jane")


def test_the_config_gets_the_chosen_name_and_voice() -> None:
    text = render_config(EXAMPLE, BOT, ACI, name="Perikles", voice="alba")
    assert parse_config(tomllib.loads(text)).name == "Perikles" and 'name = "Estia"' not in text
    assert parse_config(tomllib.loads(voice_on(text))).voice_name == "alba"
    assert parse_config(tomllib.loads(text)).voice_url == ""  # voice stays off until you turn it on
    with pytest.raises(ValueError, match="name"):
        render_config(EXAMPLE, BOT, ACI, name='Bad"name')


@pytest.mark.parametrize(
    # owner, 27 Oct: "not af_heart, do heart": heart, jane, eve, vera (female) on the 100M; 29 Oct, heart, jane, eve on
    # the 18M (heart first: "just make the small heart")
    ("memory", "female"),
    [(32.0, "heart, jane, eve, vera"), (16.0, "heart, jane, eve")],
    ids=["26b", "12b"],
)
def test_the_last_step_lists_the_voices_and_the_commands_in_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], memory: float, female: str
) -> None:
    """Owner, 26 Oct: setup's last step lists the voices (never by the model's size; 27 Oct: "heart, jane, eve, vera
    (female); alba, michael (male)", vera only where Pocket TTS's 100M model runs, beside Gemma 4 26B; "not
    af_heart, do heart") and how to get /voices, /tools and /help in Signal, right before what done means."""
    from local_assistant import setup

    assert setup.voices_line(memory) == prompts.SETUP_VOICES.format(female=female, male="alba, michael")
    assert prompts.SETUP_VOICES.format(female=female, male="alba, michael") == (
        f"Voices for voice notes and calls in English: {female} (female); alba, michael (male).\n"
        "  In Signal: /voices lists them and /voice <name> picks one; /tools lists her tools; /help lists the rest."
    )
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: memory)
    monkeypatch.setattr("local_assistant.setup_extras._voice_installed", lambda: True)  # said once it is installed
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ollama_lists(monkeypatch)
    assert main(["--dry-run", "--config", str(tmp_path / "cfg" / "config.toml")]) == 0
    out = capsys.readouterr().out.rstrip("\n")
    assert out.count(setup.voices_line(memory)) == 1  # one last step: P2's voices line inside P5's closing
    last = out[out.index(setup.voices_line(memory)) :]
    assert last.endswith(plain_text(prompts.SETUP_DONE.format(name="Estia")))  # done: last


# ------------------------------------------------------------------ her own user, the security step, Registration Lock


def test_always_on_moves_her_into_her_own_user(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import setup

    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(wizard, "run", recorder(ran))
    wizard.always_on()
    assert ran == [[str(setup.REPO / "scripts" / "migrate-to-own-user.sh")]]
    out = capsys.readouterr().out
    assert "hidden macOS account" in out and prompts.SETUP_MOVE_MORE_TYPED in out  # the details: the window's fold
    assert "_estia" in prompts.SETUP_MOVE_MORE and "never runs as you or as root" in prompts.SETUP_MOVE_MORE


def test_setup_run_as_root_stops_before_it_writes_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review MAC-7: root's setup would make setup's own folders root's (the answers file, gum's folder)."""
    from local_assistant import setup_answers

    monkeypatch.setattr("os.geteuid", lambda: 0)
    monkeypatch.setattr("builtins.input", refuse)
    assert main(["--config", str(tmp_path / "config.toml")]) == 2
    assert capsys.readouterr().out.strip() == prompts.SETUP_NOT_ROOT
    folder = setup_answers.default_path().parent
    assert not folder.exists() or list(folder.iterdir()) == []  # no answers file, no lock
    assert not (tmp_path / "config.toml").exists()


@pytest.mark.parametrize("code", [0, 3])
def test_a_kept_yes_to_the_move_is_forgotten_once_she_moved(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """Review MAC-4: a kept yes would move her again, with no question, at any later run where she is not moved, as
    after a deliberate rollback. Once the move went through, the yes is forgotten: a later run asks again."""
    import subprocess

    from local_assistant import setup_answers

    answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False, answers=answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    def run(self: Wizard, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        her_own_user(no_system_paths)  # the move went through
        return subprocess.CompletedProcess(command, code, "", "")

    monkeypatch.setattr(Wizard, "run", run)
    assert wizard.always_on()
    assert setup_answers.Answers.load(setup_answers.default_path(), say=print).get("move") is None


def test_a_kept_yes_stays_while_the_move_did_not_go_through(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    from local_assistant import setup_answers

    answers = setup_answers.Answers.load(setup_answers.default_path(), say=print)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False, answers=answers)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(Wizard, "run", lambda self, command, **kwargs: subprocess.CompletedProcess(command, 1, "", ""))
    assert not wizard.always_on()
    assert setup_answers.Answers.load(setup_answers.default_path(), say=print).get("move") is True


def test_a_set_up_install_whose_signal_account_is_gone_stops_before_the_moves_password(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """c-5 (round 3): her config set up, signal-cli's data gone. The move would stop after macOS's box with "setup
    registers it", which a set-up install never does (no Signal step there): a dead end on every run. Setup stops
    before the move with the fix that works, and nothing runs."""
    import subprocess

    home = tmp_path / "home"
    (home / ".local/share/signal-cli/data").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    config = tmp_path / "config.toml"
    config.write_text('bot_number = "+306900000000"\n', encoding="utf-8")
    wizard = Wizard(config, dry_run=False)
    wizard.first_install = False
    ran: list[list[str]] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(
        Wizard, "run", lambda self, command, **kwargs: noted(ran, command, subprocess.CompletedProcess(command, 0))
    )
    assert not wizard.always_on()
    assert wizard.move_stop == prompts.SETUP_MOVE_NO_SIGNAL and ran == []
    assert "mv ~/.config/local-assistant ~/.config/local-assistant.old" in prompts.SETUP_MOVE_NO_SIGNAL


def test_always_on_is_done_once_she_has_her_own_user(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    her_own_user(no_system_paths)
    monkeypatch.setattr("builtins.input", refuse)
    Wizard(tmp_path / "config.toml", dry_run=False).always_on()
    assert "✓ Always on: as her own macOS account _estia" in capsys.readouterr().out


def test_the_security_step_runs_harden_and_shows_the_phone_checklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import setup

    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(wizard, "run", recorder(ran))
    wizard.security()
    assert ran == [[str(setup.REPO / "scripts" / "harden.sh")]]
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    wizard.security()
    out = capsys.readouterr().out
    assert "Linked devices" in out and "port-out" in out  # declined: the checklist still comes
    assert "The security step, each change only on your yes:" in out and "default" not in out.lower()


def test_a_security_step_that_did_not_finish_is_said_and_named_at_the_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """stops-reruns-harden-exit-ignored: harden.sh ended with an error and the next line was the calls step, the end
    read as a success. Its exit code is read: one line, and the closing list names it again."""
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(wizard, "run", lambda command, **kwargs: subprocess.CompletedProcess(command, 1, "", ""))
    wizard.security()
    assert prompts.SETUP_SECURITY_FAILED in capsys.readouterr().out
    assert prompts.SETUP_SECURITY_FAILED in wizard.for_terminal
    assert prompts.SETUP_SECURITY_FAILED == (
        "✗ Security: the step did not finish (its lines say why). Run scripts/harden.sh again."
    )


def test_a_rerun_in_her_own_user_never_registers_or_writes_the_config(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import setup

    her_own_user(no_system_paths)
    ran: list[list[str]] = []
    for name in ("signal_number", "owner", "config", "her_name"):
        monkeypatch.setattr(Wizard, name, refuse)
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "security", lambda self: None)
    monkeypatch.setattr(Wizard, "extras", lambda self: None)
    monkeypatch.setattr(Wizard, "run", recorder(ran))
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # her style: Enter keeps hearth (the lines printed)
    config = tmp_path / "config.toml"
    assert setup.main(["--config", str(config)]) == 0
    estia = "/usr/local/estia/bin/estia"  # the fixed path, never ESTIA_ROOT's
    # her style's lines: her config opened as her and her restart, each on Enter; then her doctor
    assert ran == [
        ["/usr/bin/sudo", estia, "config"],
        ["/usr/bin/sudo", estia, "restart"],
        ["/usr/bin/sudo", estia, "doctor"],
    ]
    assert not config.exists()
    assert "her own macOS account _estia" in capsys.readouterr().out


def test_the_end_of_setup_says_when_calls_work(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import ownuser

    her_own_user(no_system_paths)
    (no_system_paths / "Library/LaunchDaemons/com.local-assistant.voice.plist").write_bytes(b"x")
    monkeypatch.setattr(Wizard, "voice_health", lambda self: {"stt": "loading", "tts": "ready"})
    rerun(monkeypatch, tmp_path / "config.toml")
    out = capsys.readouterr().out.splitlines()
    # her config is hers alone: setup cannot see [calls], so it promises no text
    assert ownuser.VOICE_LOADING_NO_CALLS in out and ownuser.VOICE_LOADING not in out
    check = next(at for at, line in enumerate(out) if line.endswith(": the check"))
    assert out.index(ownuser.VOICE_LOADING_NO_CALLS) > check  # at the end, after the check
    monkeypatch.setattr(Wizard, "voice_health", lambda self: {"stt": "ready", "tts": "ready"})
    rerun(monkeypatch, tmp_path / "config.toml")
    assert "voice: ready" in capsys.readouterr().out.splitlines()


def test_with_calls_the_end_of_setup_promises_the_text_only_after_the_long_load_wait(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import ownuser

    (no_system_paths / "Library/LaunchDaemons").mkdir(parents=True, exist_ok=True)
    (no_system_paths / "Library/LaunchDaemons/com.local-assistant.voice.plist").write_bytes(b"x")
    monkeypatch.setattr(Wizard, "voice_health", lambda self: {"stt": "loading", "tts": "loading"})
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    now = [0.0]
    wizard.clock = lambda: now[0]
    wizard.sleep = lambda seconds: now.__setitem__(0, now[0] + seconds)
    wizard.voice_end(calls=True)
    assert capsys.readouterr().out.splitlines()[-1] == ownuser.VOICE_LOADING
    assert now[0] >= ownuser.VOICE_WAIT_SECONDS


def test_setup_reads_her_voice_as_her_once_she_runs_as_her_own_user(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Her voice service answers only her own account: in her own user setup (as you) reads it as her, through
    sudo without a password (the admin sign-in of the check just before); before the move it is yours."""
    from local_assistant import ownuser

    asked: list[bool] = []
    monkeypatch.setattr(ownuser, "voice_health", lambda live, *, as_her=False: asked.append(as_her))
    Wizard(tmp_path / "config.toml", dry_run=False).voice_health()
    her_own_user(no_system_paths)
    Wizard(tmp_path / "config.toml", dry_run=False).voice_health()
    assert asked == [False, True]


def test_setup_says_when_her_voice_was_not_read_as_her(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review R2-F4: with sudo asking every time (timestamp_timeout=0) the read as her finds no admin sign-in running:
    setup says so, never "loading"."""
    from local_assistant import ownuser

    her_own_user(no_system_paths)
    (no_system_paths / "Library/LaunchDaemons/com.local-assistant.voice.plist").write_bytes(b"x")
    monkeypatch.setattr(
        ownuser, "voice_health", lambda live, *, as_her=False: ownuser.VoiceNotRead(ownuser.VOICE_NOT_SIGNED_IN)
    )
    rerun(monkeypatch, tmp_path / "config.toml")
    out = capsys.readouterr().out.splitlines()
    assert ownuser.voice_line(ownuser.VoiceNotRead(ownuser.VOICE_NOT_SIGNED_IN)) in out
    assert ownuser.VOICE_LOADING_NO_CALLS not in out and ownuser.VOICE_LOADING not in out


def test_a_ctrl_c_in_setups_voice_wait_before_her_own_user_names_the_checkouts_check(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review R2-F7: before her own macOS account her wrapper refuses; scripts/check.sh shows her voice."""
    from local_assistant import ownuser

    (no_system_paths / "Library/LaunchDaemons").mkdir(parents=True, exist_ok=True)
    (no_system_paths / "Library/LaunchDaemons/com.local-assistant.voice.plist").write_bytes(b"x")
    monkeypatch.setattr(Wizard, "voice_health", lambda self: {"stt": "loading", "tts": "loading"})
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)

    def stopped(_seconds: float) -> None:
        raise KeyboardInterrupt

    wizard.sleep = stopped
    wizard.voice_end(calls=True)
    assert capsys.readouterr().out.splitlines()[-1] == ownuser.VOICE_STOPPED_YOURS
    monkeypatch.setattr(Wizard, "own_user", lambda self: True)  # in her own user: her wrapper's check
    wizard.voice_end(calls=True)
    assert capsys.readouterr().out.splitlines()[-1] == ownuser.VOICE_STOPPED


def test_without_a_voice_service_setup_says_nothing_about_calls(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from local_assistant import ownuser

    her_own_user(no_system_paths)
    assert Wizard(tmp_path / "config.toml", dry_run=False).voice_health() is None  # a test root: never the live one
    rerun(monkeypatch, tmp_path / "config.toml")
    out = capsys.readouterr().out
    assert ownuser.VOICE_LOADING not in out and "voice: ready" not in out


def test_the_dry_run_describes_her_own_user_the_lock_and_the_security_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    ollama_lists(monkeypatch)
    assert main(["--dry-run", "--config", str(tmp_path / "cfg" / "config.toml")]) == 0
    out = capsys.readouterr().out
    assert "Registration Lock" in out and "setPin with <PIN> on stdin" in out
    assert "scripts/migrate-to-own-user.sh" in out and "hidden macOS account" in out
    assert "scripts/harden.sh" in out and "Linked devices" in out
    flat = " ".join(out.split())  # the step's lines are fitted to the terminal
    assert "scripts/harden.sh --nuclear" in flat and "other services on this Mac" in flat  # never part of setup
    # the two levels by name (owner, 14 Oct): setup's step is the hardened level, --nuclear the nuclear one
    assert "• Remote logins (SSH): keys only." in out and prompts.SETUP_NUCLEAR_TYPED in out
    # a dry run asks nothing (input() refuses above); it names the question and its Enter default (review, 19 Oct)
    assert f'asks "{prompts.SETUP_SECURITY_ASK} [Y/n]"' in out


def test_after_a_rollback_setup_takes_your_path_again(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bin/estia stays after a rollback; the bridge's service file names you again."""
    her_own_user(no_system_paths, user="alex")
    assert not Wizard(tmp_path / "config.toml", dry_run=False).own_user()


def test_a_new_config_pins_the_model_ollama_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Setup just made the alias: its digest goes into the new config, so doctor has no pin to ask for."""
    digest = "ab" * 32

    def get_json(self: doctor.Probe, url: str) -> object:
        return {"models": [{"name": "local-worker:latest", "digest": f"sha256:{digest}"}]}

    monkeypatch.setattr(doctor.Probe, "get_json", get_json)
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    path = tmp_path / "config.toml"
    Wizard(path, dry_run=False).config(BOT, ACI)
    assert parse_config(tomllib.loads(path.read_text(encoding="utf-8"))).model_digest == digest
    assert "Model pin" in capsys.readouterr().out
    text = render_config(EXAMPLE, BOT, ACI)
    assert "#model_digest" in text and not parse_config(tomllib.loads(text)).model_digest  # none given: no pin


# ------------------------------------------------------------------ hidden answers of any length, only from a terminal


REAL_HAS_TERMINAL = wizard_module.has_terminal


def hidden_in_a_pty(typed: str) -> tuple[str | BaseException, Pty]:
    """read_hidden on a real terminal (a pty), with `typed` typed once the prompt shows."""
    pty = Pty()
    got: list[str | BaseException] = []

    def read() -> None:
        try:
            got.append(read_hidden("captcha link: ", pty.path))
        except (EOFError, OSError) as exc:
            got.append(exc)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    pty.wait_for(b"captcha link: ")
    pty.type(typed)
    reader.join(10)
    assert got, "the hidden prompt never returned"
    return got[0], pty


def test_a_captcha_link_longer_than_a_terminal_line_is_read_whole() -> None:
    """macOS keeps a typed line to 1,023 characters and drops the rest with the Enter: Signal's captcha link is longer
    (findings 1, 37). Read with the line mode off, any length arrives, never echoed, and the terminal is put back."""
    link = "signalcaptcha://signal-hcaptcha.00000000-0000-4000-8000-000000000000.registration.P1_" + "a1B2" * 750
    assert len(link) > 3000
    got, pty = hidden_in_a_pty(link + "\r")
    try:
        assert got == link
        assert b"aaa" not in pty.seen and b"a1B2" not in pty.seen  # nothing echoed
        mode = termios.tcgetattr(pty.slave)[3]
        assert mode & termios.ICANON and mode & termios.ECHO  # the line mode and the echo are back
    finally:
        pty.close()


def test_a_hidden_answer_can_be_corrected_and_ctrl_d_stops() -> None:
    got, pty = hidden_in_a_pty("12x\x7f3\x15456\n")  # a typo erased, then the line cleared with Ctrl-U
    pty.close()
    assert got == "456"
    got, pty = hidden_in_a_pty("\x04")
    pty.close()
    assert isinstance(got, EOFError)


def test_a_hidden_answer_never_comes_from_stdin_without_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """No terminal (an AI's command tool, a pipe): setup stops and says where to run it, and never reads a code from
    its input, which may be an AI's transcript (finding 38)."""
    monkeypatch.setattr(wizard_module, "TTY", "/nonexistent/tty")
    monkeypatch.setattr("sys.stdin", io.StringIO("fake-captcha-value\n"))
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    with pytest.raises(SystemExit) as stopped:
        wizard.hidden("The code Signal sent by SMS (hidden): ")
    assert str(stopped.value) == prompts.SETUP_NO_TERMINAL
    assert sys.stdin.read() == "fake-captcha-value\n"  # left unread


def test_setup_without_a_terminal_away_from_the_desk_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under pytest stdin is no terminal: the real check says so. Started that way (an AI's command tool) setup's only
    face is its window, which opens only at the Mac's own screen: away from it one line says where to run it (exit 2),
    before anything runs; a --config of its own is for Terminal (setup_ai.refused). setup's run called on its own
    without main's route still says where to run it."""
    monkeypatch.setattr(wizard_module, "has_terminal", REAL_HAS_TERMINAL)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert not wizard_module.has_terminal()
    for name in ("model", "her_name", "signal_number", "always_on", "security", "extras", "check"):
        monkeypatch.setattr(Wizard, name, refuse)
    monkeypatch.setattr(setup_ai, "detach", refuse)
    assert main([]) == 2  # no launchctl that says Aqua under the tests' tools root: not the desk
    assert capsys.readouterr().out == prompts.SETUP_AI_NOT_AT_DESK + "\n"
    assert main(["--config", str(tmp_path / "config.toml")]) == 2
    assert capsys.readouterr().out == prompts.SETUP_AI_FILES + "\n"
    assert wizard_module.run_setup(["--config", str(tmp_path / "config.toml")]) == 1
    assert prompts.SETUP_NO_TERMINAL in capsys.readouterr().out


def test_the_documented_by_hand_route_reads_a_long_line_with_the_line_mode_off() -> None:
    """setup_assistant.md, 2.3: `stty -icanon` before signal-cli, so a pasted request with the captcha link (over
    1,023 characters) arrives whole; a program reading a line, as signal-cli's jsonRpc does, gets all of it."""
    pty = Pty()
    reader = "import sys; print('ready', flush=True); line = sys.stdin.readline(); print('got', len(line), flush=True)"
    proc = subprocess.Popen(
        ["/bin/sh", "-c", 'stty -icanon && exec "$0" -I -c "$1"', sys.executable, reader],
        stdin=pty.slave,
        stdout=pty.slave,
        stderr=pty.slave,
    )
    try:
        pty.wait_for(b"ready")
        pty.type('{"jsonrpc":"2.0","method":"register","params":{"captcha":"' + "x" * 2500 + '"},"id":2}\r')
        pty.wait_for(b"got ")
        pty.wait_for(b"\n", 5)
        assert re.search(rb"got (\d+)", pty.seen) and int(re.search(rb"got (\d+)", pty.seen).group(1)) > 2500  # type: ignore[union-attr]
    finally:
        proc.kill()
        proc.wait()
        pty.close()


# ------------------------------------------------------------------ a step that failed stops setup (findings 10, 59)


def pulls(monkeypatch: pytest.MonkeyPatch, wizard: Wizard, ran: list[list[str]], why: str = "") -> None:
    """Ollama's own pull, as setup asks it (Wizard.pull: its /api/pull stream), recorded as ["pull", tag]."""

    def pull(tag: str, *, size: int = 0) -> str:
        ran.append(["pull", tag])
        return why

    monkeypatch.setattr(wizard, "pull", pull)


def ollama_has_pulled(monkeypatch: pytest.MonkeyPatch, digests: dict[str, str] | None = None) -> None:
    """Ollama's list after the pull: both MLX builds with their tested digests (or the ones given)."""
    from local_assistant.setup import GEMMA_12B, GEMMA_12B_DIGEST, GEMMA_26B, GEMMA_26B_DIGEST

    listed = digests or {GEMMA_26B: GEMMA_26B_DIGEST, GEMMA_12B: GEMMA_12B_DIGEST}

    def get_json(self: doctor.Probe, url: str) -> object:
        return {"models": [{"name": name, "digest": digest} for name, digest in listed.items()]}

    monkeypatch.setattr(doctor.Probe, "get_json", get_json)
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)


def test_a_failed_ollama_create_stops_setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    create = [wizard.ollama_program, "create"]

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        ran.append(command)
        return subprocess.CompletedProcess(command, 1 if command[:2] == create else 0, "", "")

    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    monkeypatch.setattr(wizard, "run", run)
    pulls(monkeypatch, wizard, ran)
    ollama_has_pulled(monkeypatch)
    with pytest.raises(SystemExit, match="local-worker was not made"):
        wizard.model()
    assert ran[-1][:2] == create
    assert "✓ Model" not in capsys.readouterr().out
    assert not (tmp_path / ".local-worker.Modelfile").exists()


def test_the_model_is_pulled_by_its_mlx_tag_with_the_ollama_in_use_and_checked_by_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.ollama_program = "/opt/example/ollama"  # the one the Ollama step kept or installed
    monkeypatch.setattr(wizard, "run", recorder(ran))
    pulls(monkeypatch, wizard, ran)
    ollama_has_pulled(monkeypatch)
    wizard.model()
    assert ran[0] == ["pull", "gemma4:26b-mlx"]  # through Ollama's own pull, its dots lit (setup_progress)
    assert ran[1][:3] == ["/opt/example/ollama", "create", "local-worker"]
    out = capsys.readouterr().out
    assert "Model: gemma4:26b-mlx for this Mac's 32 GB (an 18.3 GB download, about 25 minutes at 100 Mbit/s" in out
    assert "✓ Model: local-worker = gemma4:26b-mlx" in out


def test_a_pull_that_stops_says_why_and_how_to_go_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    pulls(monkeypatch, wizard, ran, why="connection reset")
    ollama_has_pulled(monkeypatch)
    with pytest.raises(SystemExit) as stopped:
        wizard.model()
    assert str(stopped.value) == prompts.SETUP_PULL_FAILED.format(why="connection reset")


def test_her_models_dots_count_against_the_pinned_builds_whole_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review PROM-06: Ollama names each of the 1073 layers of gemma4:26b-mlx only when its turn comes, so the dots
    count against the model's whole size: every blob of its pinned manifest (the layers and the config), measured on
    the live Mac's copy of each pinned build (Ollama 0.40.4, 27 Oct)."""
    from local_assistant import setup_progress

    big, small = choose_model(32), choose_model(16)
    assert big is not None and small is not None
    assert (big.size, small.size) == (18_334_652_006, 7_714_253_126)
    asked: list[tuple[str, int]] = []

    def pull(base: str, tag: str, watch: object, *, total: int = 0, post: object = None) -> str:
        asked.append((tag, total))
        return ""

    monkeypatch.setattr(setup_progress, "pull", pull)
    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", recorder([]))
    ollama_has_pulled(monkeypatch)
    wizard.model()
    assert asked == [("gemma4:26b-mlx", 18_334_652_006)]


def test_setups_own_pull_never_reaches_the_macs_ollama_in_tests(tmp_path: Path) -> None:
    """tests/conftest.py's guard: Wizard.pull's stream is the Mac's Ollama, which no test may ask."""
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    assert "a test asked the Mac's Ollama to pull" in wizard.pull("gemma4:26b-mlx")


def smaller_mac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory: float, answer: str, *, dry: bool = False
) -> tuple[Wizard, list[list[str]], list[str]]:
    """The model step on a Mac with `memory` GB, the person answering `answer`: what ran and what was asked."""
    ran: list[list[str]] = []
    asked: list[str] = []

    def type_answer(prompt: str = "") -> str:
        asked.append(prompt)
        return answer

    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    monkeypatch.setattr("local_assistant.harden.in_terminal", lambda: True)  # the person's own Terminal: Enter is yes
    wizard = Wizard(tmp_path / "config.toml", dry_run=dry)
    monkeypatch.setattr(wizard, "run", recorder(ran))
    if not dry:
        pulls(monkeypatch, wizard, ran)
    ollama_has_pulled(monkeypatch)
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: memory)
    monkeypatch.setattr("builtins.input", refuse if dry else type_answer)
    return wizard, ran, asked


@pytest.mark.parametrize("typed", ["y", ""], ids=["y", "enter"])
@pytest.mark.parametrize("memory", [16.0, 24.0])
def test_on_16_and_24_gb_setup_says_what_12b_failed_and_asks_before_the_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], memory: float, typed: str
) -> None:
    """Owner, 15 Oct: setup still picks 12B there, and only a yes starts the 7.7 GB download; owner, 19 Oct: "use with
    caution", in plain words, never what failed in a test."""
    wizard, ran, asked = smaller_mac(tmp_path, monkeypatch, memory, typed)
    wizard.model()
    # owner's call, 29 Oct: Enter takes the recommended answer wherever a line says it is recommended (12B: yes)
    assert asked == [f"{prompts.SETUP_SMALLER_MODEL_ASK} [Y/n] "]
    assert ran[0] == ["pull", "gemma4:12b-mlx"]
    out = capsys.readouterr().out
    lines = out.splitlines()
    model = next(line for line in lines if line.startswith("Model: gemma4:12b-mlx"))
    assert "a 7.7 GB download" in model and "SECURITY.md" not in model
    warning = next(line for line in lines if line.startswith("Gemma 4 12B"))
    assert warning == "Gemma 4 12B on this Mac."
    assert plain_text(prompts.SETUP_SMALLER_MODEL) in out
    assert lines.index(model) < lines.index(warning) < lines.index(prompts.SETUP_DOWNLOAD.splitlines()[0])


@pytest.mark.parametrize("answer", ["n", "no"])
def test_no_to_the_smaller_model_downloads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], answer: str
) -> None:
    wizard, ran, _asked = smaller_mac(tmp_path, monkeypatch, 16.0, answer)
    with pytest.raises(SystemExit, match="Nothing was downloaded"):
        wizard.model()
    assert ran == [] and prompts.SETUP_DOWNLOAD not in capsys.readouterr().out


def test_a_dry_run_shows_the_smaller_models_question_and_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wizard, _ran, _asked = smaller_mac(tmp_path, monkeypatch, 16.0, "", dry=True)
    wizard.model()
    out = capsys.readouterr().out
    assert "Gemma 4 12B on this Mac" in out
    assert prompts.SETUP_ASKS.format(ask=f"{prompts.SETUP_SMALLER_MODEL_ASK} [Y/n]") in out
    assert prompts.SETUP_PULLING.format(url="http://127.0.0.1:11434/api/pull", tag="gemma4:12b-mlx") in out


def test_the_tested_model_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    wizard, ran, _asked = smaller_mac(tmp_path, monkeypatch, 32.0, "", dry=False)
    monkeypatch.setattr("builtins.input", refuse)
    wizard.model()
    assert ran[0] == ["pull", "gemma4:26b-mlx"] and "smaller model" not in capsys.readouterr().out


def test_the_ai_briefing_names_the_smaller_model_question() -> None:
    from local_assistant import setup_plan

    briefing = (Path(__file__).resolve().parents[1] / "SETUP-WITH-YOUR-AI.md").read_text(encoding="utf-8")
    briefing = briefing.split("## 3. Brief each screen from the source", 1)[1].split("\n## ", 1)[0]
    smaller = '"The smaller model" ("Go on with the smaller model?")'
    assert f"- 16 or 24 GB of memory: {smaller}." in briefing
    assert (
        briefing.index(smaller) < briefing.index("**The steps, in one sentence each") < briefing.index("**Her model:**")
    )
    q = setup_plan.question("smaller_model")  # asked in setup's plan, before anything is installed
    assert q.plan and q.recommended == "yes" and q.ask == "Go on with the smaller model?"  # Enter: yes (29 Oct)


def test_a_pulled_model_with_another_digest_is_never_made_into_local_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registry changed the tag since the tests: setup says so and makes nothing from it."""
    ran: list[list[str]] = []
    monkeypatch.setattr("local_assistant.setup.REPO", tmp_path)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", recorder(ran))
    pulls(monkeypatch, wizard, ran)
    ollama_has_pulled(monkeypatch, {"gemma4:26b-mlx": "ab" * 32})
    with pytest.raises(SystemExit, match="came down as abababababab, not the tested build f0fc7e0ae494"):
        wizard.model()
    assert ran == [["pull", "gemma4:26b-mlx"]]  # pulled, nothing made from it
    assert not (tmp_path / ".local-worker.Modelfile").exists()


def test_the_ollama_step_comes_first_and_off_the_live_mac_only_prints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Under a test's root nothing is read or run: the step shows the download, the checks and the service."""
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    monkeypatch.setattr("local_assistant.ollama_build.owner_home", lambda: tmp_path / "home")
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.ollama()
    out = capsys.readouterr().out
    assert "Ollama: its official build 0.40.4" in out and "/usr/bin/curl -q -fsSL" in out  # its five dots, no %
    assert (
        "/usr/bin/sudo /bin/launchctl bootstrap system /Library/LaunchDaemons/com.local-assistant.ollama.plist" in out
    )
    assert wizard.ollama_program == str(tmp_path / "home/opt/ollama/current/ollama")
    assert not (tmp_path / "home").exists()


def test_the_ollama_step_prints_its_download_as_a_command_that_asks_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review PROM-08: the Ollama step's download line goes through the Wizard's command (the window keeps its
    screen during the download)."""
    from local_assistant import ollama_build

    given: dict[str, object] = {}

    class Install(ollama_build.Install):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            given.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ollama_build, "Install", Install)
    monkeypatch.setattr("local_assistant.ollama_build.owner_home", lambda: tmp_path / "home")
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.ollama()
    assert given["command"] == wizard.command


@pytest.mark.parametrize("chosen", ["app", "build", None])
def test_the_ollama_step_uses_the_app_only_when_the_person_chose_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, chosen: str | None
) -> None:
    """Owner, 29 Oct: yes uses the Ollama app (nothing installed); no, or the app not on this Mac, the official build."""
    from local_assistant import ollama_build, setup_plan

    given: dict[str, object] = {}

    class Install(ollama_build.Install):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            given.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(ollama_build, "Install", Install)
    monkeypatch.setattr("local_assistant.ollama_build.owner_home", lambda: tmp_path / "home")
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)
    for target in ("builtins.input", "local_assistant.setup.read_hidden", "subprocess.run", "subprocess.Popen"):
        monkeypatch.setattr(target, refuse)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.fixed_mac = setup_plan.Mac(memory_gb=32.0, ollama_app="" if chosen is None else "/Applications/Ollama.app")
    if chosen is not None:
        wizard.planned["ollama_app"] = chosen
    wizard.ollama()
    app = Path("/Applications/Ollama.app") if chosen == "app" else None
    assert given["app"] == app and wizard.ollama_app == app
    if app is not None:
        assert wizard.ollama_program == "/Applications/Ollama.app/Contents/Resources/ollama"


def test_a_config_written_after_the_chosen_app_records_it(tmp_path: Path) -> None:
    """ollama = "app" in her config: doctor, and every later run, know the person chose the app."""
    text = render_config(EXAMPLE, BOT, ACI, ollama="app")
    assert re.search(r'^ollama = "app"$', text, re.MULTILINE)
    assert parse_config(tomllib.loads(text)).ollama == "app"
    assert parse_config(tomllib.loads(render_config(EXAMPLE, BOT, ACI))).ollama == "build"


def test_setup_runs_the_ollama_step_before_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []
    monkeypatch.setattr("local_assistant.setup.has_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", enter_but_signal)  # the plan's questions come first: Enter

    def model(self: Wizard) -> None:
        order.append("model")
        raise SystemExit(3)

    monkeypatch.setattr(Wizard, "ollama", lambda self: order.append("ollama"))
    monkeypatch.setattr(Wizard, "model", model)
    with pytest.raises(SystemExit):
        main(["--config", str(tmp_path / "config.toml")])
    assert order == ["ollama", "model"]


def test_a_mac_too_small_stops_before_ollama_is_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 8.0)
    with pytest.raises(SystemExit):
        Wizard(tmp_path / "config.toml", dry_run=False).ollama()
    out = capsys.readouterr().out
    assert "This Mac has 8 GB of memory. Estia needs 16 GB or more." in out and "curl" not in out


def test_a_failed_move_stops_before_the_security_step_and_the_extras(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(GOOD, encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "her_signal_here", lambda self: True)  # her Signal account there: the move runs
    for name in ("security", "extras", "check"):
        monkeypatch.setattr(Wizard, name, refuse)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(Wizard, "run", lambda self, command, **kwargs: subprocess.CompletedProcess(command, 1, "", ""))
    stopped: list[str] = []
    monkeypatch.setattr("local_assistant.setup.PlainFace.stopped", lambda self, text: stopped.append(text))
    with pytest.raises(SystemExit) as ended:  # a stop, as step 1's are drawn (stops-reruns-move-failed)
        main(["--config", str(path)])
    assert ended.value.code == 1
    assert stopped == [prompts.SETUP_MOVE_FAILED.format(move=wizard_module.REPO / "scripts/migrate-to-own-user.sh")]
    assert "the move did not finish" in stopped[0] and "scripts/migrate-to-own-user.sh --dry-run" in stopped[0]


def test_a_move_that_succeeds_while_the_share_fails_goes_on_to_the_security_step(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The move script ends 3 when only its last step, the projects share, fails (a folder outside your home):
    she runs in her own user, so setup says only the share failed, with its command, and goes on."""
    from local_assistant import setup

    path = tmp_path / "config.toml"
    path.write_text(GOOD, encoding="utf-8")
    path.chmod(0o600)
    reached: list[str] = []
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "her_signal_here", lambda self: True)  # her Signal account there: the move runs
    monkeypatch.setattr(Wizard, "security", lambda self: reached.append("security"))
    monkeypatch.setattr(Wizard, "extras", lambda self: reached.append("extras"))
    monkeypatch.setattr(Wizard, "personality", lambda self: None)
    monkeypatch.setattr(Wizard, "check", lambda self: 0)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def run(self: Wizard, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        her_own_user(no_system_paths)  # the move went through: her service file names _estia; then the share failed
        return subprocess.CompletedProcess(command, 3, "", "")

    monkeypatch.setattr(Wizard, "run", run)
    assert main(["--config", str(path)]) == 0
    out = capsys.readouterr().out
    assert "The move did not finish" not in out and reached == ["security", "extras"]
    assert "✓ Always on: Estia runs in her own macOS account _estia. Only her projects folder is not shared yet" in out
    assert f"then run {setup.REPO / 'scripts' / 'share-projects.sh'}" in out


def test_a_failed_move_whose_restore_left_her_service_file_still_stops(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """SEC1 re-check: a move that fails and whose restore fails too can leave her service file naming _estia.
    Only the share's own exit code (3) means she was moved: any other failure stops setup before the security step."""
    path = tmp_path / "config.toml"
    path.write_text(GOOD, encoding="utf-8")
    path.chmod(0o600)
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "her_signal_here", lambda self: True)  # her Signal account there: the move runs
    for name in ("security", "extras", "check"):
        monkeypatch.setattr(Wizard, name, refuse)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    def run(self: Wizard, command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        her_own_user(no_system_paths)  # left behind by a restore that failed
        return subprocess.CompletedProcess(command, 1, "", "")

    monkeypatch.setattr(Wizard, "run", run)
    with pytest.raises(SystemExit):
        main(["--config", str(path)])
    out = capsys.readouterr().out
    assert "the move did not finish" in out and "Only her projects folder is not shared yet" not in out


# ------------------------------------------------------------------ over SSH, the move inside screen (finding 51)


@pytest.mark.parametrize(("env", "warned"), [({}, True), ({"STY": "1.x"}, False), ({"TMUX": "/tmp/t,1,0"}, False)])
def test_over_ssh_the_move_is_offered_inside_screen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    env: dict[str, str],
    warned: bool,
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 50000 192.0.2.2 22")
    for name in ("STY", "TMUX"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    Wizard(tmp_path / "config.toml", dry_run=False).always_on()
    out = capsys.readouterr().out
    assert ("screen" in out and "dropped connection" in out) is warned


# ------------------------------------------------------------------ the personality, where her files are (finding 11)


def test_the_personality_hint_names_her_folder_once_she_has_her_own_user(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt="": "")  # her style, in her own user: Enter keeps hearth
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", recorder([]))  # her config opened as her: on Enter, never run here
    wizard.personality()
    out = capsys.readouterr().out
    assert f"Your own instead, any time: a soul.md in {tmp_path} (setup_assistant.md, 2.5)." in out
    assert "Personality: Estia's built-in style, hearth (steady, kind and frank).\n" in out
    assert "cp soul.example.md" not in out  # never a command a default Enter would run: it replaces her style
    wizard.style = "playful"  # a config that chose it
    wizard.personality()
    assert "built-in style, playful (warm, curious and playful)." in capsys.readouterr().out
    her_own_user(no_system_paths)
    wizard.personality()
    out = capsys.readouterr().out
    assert "Your own, as her, any time: setup_assistant.md, 2.5." in out  # the guide has the command
    assert "built-in style (hearth, the default, or playful: [personality] style in her config)" in out
    assert "sudo -u _estia -e" not in out
    assert "cp soul.example.md" not in out


def test_setup_gives_the_personality_hint_after_the_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    order: list[str] = []
    monkeypatch.setattr(Wizard, "model", lambda self: None)
    monkeypatch.setattr(Wizard, "her_name", lambda self: None)
    monkeypatch.setattr("builtins.input", enter_but_signal)  # the plan's questions come first: Enter
    monkeypatch.setattr(Wizard, "signal_account", lambda self: BOT)
    monkeypatch.setattr(Wizard, "owner", lambda self, number: ACI)
    monkeypatch.setattr(Wizard, "config", lambda self, number, aci: order.append("config"))

    def always_on(self: Wizard) -> bool:
        order.append("always on")
        return True

    monkeypatch.setattr(Wizard, "always_on", always_on)
    monkeypatch.setattr(Wizard, "security", lambda self: order.append("security"))
    monkeypatch.setattr(Wizard, "extras", lambda self: order.append("extras"))
    monkeypatch.setattr(Wizard, "personality", lambda self: order.append("personality"))
    monkeypatch.setattr(Wizard, "check", lambda self: 0)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    assert main(["--config", str(tmp_path / "config.toml")]) == 0
    assert order == ["config", "always on", "security", "extras", "personality"]


# Enter picks the safe answer (owner, 19 Oct: "have the defaults as safe but not nuclear"): the Registration Lock, the
# move into her own user and the security step; the end of input, Ctrl-C and no terminal are never a yes


def in_a_terminal(monkeypatch: pytest.MonkeyPatch, on: bool = True) -> None:
    """The person's own terminal answers (pytest's stdin is none: there Enter keeps every no)."""
    monkeypatch.setattr(Wizard, "terminal", lambda self: on)


@pytest.mark.parametrize(
    ("answer", "terminal", "said_yes", "hint"),
    [
        ("", True, True, "[Y/n]"),
        ("y", True, True, "[Y/n]"),
        ("n", True, False, "[Y/n]"),
        ("maybe", False, False, "[y/N]"),  # from a pipe, anything but a yes is a no (a terminal asks again)
        (None, True, False, "[Y/n]"),  # the end of input is never a yes
        ("", False, False, "[y/N]"),  # no terminal: Enter keeps the no
    ],
    ids=["enter", "y", "n", "other", "end-of-input", "no-terminal"],
)
def test_enter_takes_the_safe_answer_only_from_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str | None, terminal: bool, said_yes: bool, hint: str
) -> None:
    in_a_terminal(monkeypatch, terminal)
    asked = typed_once(monkeypatch, answer)
    assert Wizard(tmp_path / "config.toml", dry_run=False).yes("Safe?", default=True) is said_yes
    assert asked == [f"Safe? {hint} "]


def test_a_steps_yes_or_no_asks_again_in_a_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """answers-extras-yes-no-other-words: "ok" or "1" was a no; "ναι" too. Asked again: "ok" holds no Greek letter,
    so without the keyboard's hint (P06)."""
    in_a_terminal(monkeypatch)
    typed = iter(["ok", "ναι"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    assert Wizard(tmp_path / "config.toml", dry_run=False).yes("Safe?", default=False) is True
    assert capsys.readouterr().out.count(prompts.SETUP_YES_NO_AGAIN_PLAIN) == 1


@pytest.mark.parametrize(("first", "greek"), [("ja", False), ("υεσ", True)])
def test_a_steps_yes_or_no_names_the_greek_keyboard_only_for_greek_letters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], first: str, greek: bool
) -> None:
    """P06, the Wizard's own yes or no: "ja" got "the keyboard may be on Greek"; only a Greek letter gets it now."""
    in_a_terminal(monkeypatch)
    typed = iter([first, "y"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(typed))
    assert Wizard(tmp_path / "config.toml", dry_run=False).yes("Safe?", default=False) is True
    out = capsys.readouterr().out
    assert out.count(prompts.SETUP_YES_NO_AGAIN if greek else prompts.SETUP_YES_NO_AGAIN_PLAIN) == 1
    assert ("Greek" in out) is greek


def test_the_wizard_takes_its_terminal_from_both_ends(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Wizard.terminal unpatched: `scripts/setup.sh > setup.log` hides each question, so Enter keeps every no."""
    from local_assistant import harden

    for both in (True, False):
        monkeypatch.setattr(harden, "in_terminal", lambda both=both: both)
        assert Wizard(tmp_path / "config.toml", dry_run=False).terminal() is both


def test_a_question_without_a_safe_default_keeps_enter_as_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    in_a_terminal(monkeypatch)
    asked = typed_once(monkeypatch, "")
    assert Wizard(tmp_path / "config.toml", dry_run=False).yes("Other?") is False
    assert asked == ["Other? [y/N] "]


def test_enter_turns_registration_lock_on_and_asks_the_pin_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from local_assistant import harden

    in_a_terminal(monkeypatch)
    hidden = iter(["111-111", "", "731904", "731904"])
    pins: list[str] = []
    asked: list[str] = []

    def answer(prompt: str = "") -> str:
        asked.append(prompt)
        return "" if "Registration Lock" in prompt or "right?" in prompt else BOT

    def set_pin(signal_cli: str, number: str, pin: str, **kwargs: object) -> tuple[bool, str]:
        pins.append(pin)
        return True, ""

    monkeypatch.setattr(harden, "set_pin", set_pin)
    monkeypatch.setattr("builtins.input", answer)
    monkeypatch.setattr("local_assistant.setup.read_hidden", lambda prompt="": next(hidden))
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli, _folder = fake_signal_cli(tmp_path, monkeypatch)
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert pins == ["731904"]
    lock = next(prompt for prompt in asked if prompt.startswith("Turn on Registration Lock now?"))
    assert lock == "Turn on Registration Lock now? [Y/n] "  # its recommendation on the line before (owner, 27 Oct)


@pytest.mark.parametrize("legacy", [False, True], ids=["new", "older-install"])
def test_enter_moves_her_into_her_own_user(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    from local_assistant import setup

    for key in ("SSH_CONNECTION", "STY", "TMUX"):
        monkeypatch.delenv(key, raising=False)
    if legacy:  # an older install, always on as you
        daemons = no_system_paths / "Library/LaunchDaemons"
        daemons.mkdir(parents=True)
        (daemons / "com.local-assistant.bridge.plist").write_bytes(plistlib.dumps({"UserName": "alex"}))
    in_a_terminal(monkeypatch)
    asked = typed_once(monkeypatch, "")
    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", recorder(ran))
    assert wizard.always_on()
    assert ran == [[str(setup.REPO / "scripts" / "migrate-to-own-user.sh")]]
    assert asked[-1] in ("Install it now? [Y/n] ", "Move her into her own macOS account now? [Y/n] ")


def test_over_ssh_without_screen_enter_keeps_the_move_for_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dropped connection stops the move halfway (it is undone): the line before says to answer no and start screen,
    so Enter says no there too."""
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 50000 192.0.2.2 22")
    for key in ("STY", "TMUX"):
        monkeypatch.delenv(key, raising=False)
    in_a_terminal(monkeypatch)
    asked = typed_once(monkeypatch, "")
    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", recorder(ran))
    assert wizard.always_on() and ran == []
    out = capsys.readouterr().out
    assert asked[-1].endswith("[y/N] ") and "screen" in out
    # the question agrees with the line before it: no recommended yes there
    assert asked[-1] == "Install it now? [y/N] " and "No (recommended, run setup again inside screen)." in out


def test_over_ssh_without_screen_the_dry_run_shows_the_same_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 50000 192.0.2.2 22")
    for key in ("STY", "TMUX"):
        monkeypatch.delenv(key, raising=False)
    wizard = Wizard(tmp_path / "config.toml", dry_run=True)
    assert wizard.always_on()
    out = capsys.readouterr().out
    assert "screen" in out and "No (recommended, run setup again inside screen)." in out
    assert '(asks "Install it now? [y/N]"' in out


def test_enter_goes_through_the_security_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from local_assistant import setup

    in_a_terminal(monkeypatch)
    asked = typed_once(monkeypatch, "")
    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", recorder(ran))
    wizard.security()
    assert ran == [[str(setup.REPO / "scripts" / "harden.sh")]]
    assert asked == ["Go through the security step now? [Y/n] "]


@pytest.mark.parametrize("answer", [None, "n"], ids=["end-of-input", "no"])
def test_the_end_of_input_or_a_no_never_runs_the_move_or_the_security_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str | None
) -> None:
    for key in ("SSH_CONNECTION", "STY", "TMUX"):
        monkeypatch.delenv(key, raising=False)
    in_a_terminal(monkeypatch)
    typed_once(monkeypatch, answer)
    ran: list[list[str]] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", recorder(ran))
    assert wizard.always_on()
    wizard.security()
    assert ran == []


def test_a_no_at_always_on_says_she_does_not_run_yet(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """answers-extras-move-no-unsaid: Step 5 printed nothing on a no, and the end promised a welcome "when she first
    runs": the friend waited for one that never came. The step says so, and so does the closing list."""
    for key in ("SSH_CONNECTION", "STY", "TMUX"):
        monkeypatch.delenv(key, raising=False)
    in_a_terminal(monkeypatch)
    typed_once(monkeypatch, "n")
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", refuse)
    assert wizard.always_on()
    assert prompts.SETUP_MOVE_SKIPPED in capsys.readouterr().out
    assert prompts.SETUP_MOVE_NOT_RUNNING in wizard.for_terminal
    assert prompts.SETUP_MOVE_SKIPPED == "Skipped: she runs only while scripts/run.sh is open."
    assert prompts.SETUP_MOVE_NOT_RUNNING == (
        "She does not run yet: start her with scripts/run.sh, or run setup again and answer yes at Always on."
    )


def test_the_projects_question_keeps_enter_as_no(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """It recommends nothing: Enter keeps no (projects stay off). A number set up here before is a recommended yes:
    Enter takes it (owner's call, 29 Oct; tests/test_setup_signal.py)."""
    in_a_terminal(monkeypatch)
    asked = typed_once(monkeypatch, "")
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    assert wizard.projects() is None
    assert asked[-1].endswith("[y/N] ")


# each question says what it is for and what yes and no do (owner, 19 Oct: never "Most people"); setup's own keep their
# recommendation out of the question, on a line of its own (owner, 27 Oct), and harden.sh's own say "Yes
# (recommended…)" on the line before the question (owner, 29 Oct); every line fits a terminal, counted with the name Estia and the number +306900000000


def setup_questions() -> dict[str, str]:
    from local_assistant import harden

    return {
        "smaller model": prompts.SETUP_SMALLER_MODEL_ASK,
        "remake": prompts.SETUP_MODEL_REMAKE_ASK,
        "name": prompts.SETUP_NAME_ASK,
        "voice": prompts.SETUP_VOICE_ASK,
        "known number": prompts.SETUP_KNOWN_NUMBER_ASK,
        "number": prompts.SETUP_NUMBER,
        "registration lock": prompts.SETUP_LOCK_ASK,
        "language": prompts.SETUP_LANGUAGE_ASK,
        "style": prompts.SETUP_STYLE_ASK,
        "projects": prompts.SETUP_PROJECTS_ASK,
        "folder": prompts.SETUP_PROJECTS_FOLDER,
        "level": prompts.SETUP_PROJECTS_LEVEL,
        "move": prompts.SETUP_MOVE_ASK,
        "older move": prompts.SETUP_MOVE_OLDER_ASK,
        "security step": prompts.SETUP_SECURITY_ASK,
        "keys only": harden.QUESTIONS["ssh_password"],
        "sudo rule": harden.QUESTIONS["sudoers"],
        "other sudo rule": harden.QUESTIONS["sudoers_others"],
        "her files": harden.QUESTIONS["data_modes"],
        "her lock": harden.QUESTIONS["registration_lock"],
        "extras": prompts.SETUP_EXTRAS_ASK,
    }


@pytest.mark.parametrize("key", list(setup_questions()))
def test_every_question_says_its_recommendation_where_its_face_shows_it(key: str) -> None:
    text = filled(setup_questions()[key])
    hardens = key in ("keys only", "sudo rule", "other sudo rule", "her files", "her lock")
    if hardens:  # the recommendation on the line right before the bare question, never inside it (review words-08)
        *_lead, recommended, asked = text.splitlines()
        assert recommended.startswith("Yes (recommended") and "ecommend" not in asked, key
        assert asked.endswith("?") and "Recommendation" not in text, key
    else:
        assert "Recommend" not in text, key
    assert "most people" not in text.lower(), key
    words = f" {text.lower()} ".replace("/usr/bin/sudo", "")
    assert " sudo " not in words and "passwordless" not in words, key  # "admin rights", "without your password"
    assert all(len(line) <= TERMINAL for line in text.splitlines()), key


def explanations() -> dict[str, str]:
    from local_assistant import harden, hostsec, setup

    return {
        "smaller model": prompts.SETUP_SMALLER_MODEL,
        "download": prompts.SETUP_DOWNLOAD,
        "name": prompts.SETUP_NAME,
        "voice": prompts.SETUP_VOICE,
        "known number": prompts.SETUP_KNOWN_NUMBER,
        "captcha": prompts.SETUP_CAPTCHA,
        "sms": prompts.SETUP_SMS_SENT,
        "lock": harden.LOCK_NOTE + "\n" + prompts.SETUP_LOCK_HOW,
        "word": prompts.SETUP_WORD,
        "language": prompts.SETUP_LANGUAGE,
        "projects": prompts.SETUP_PROJECTS,
        "levels": prompts.SETUP_PROJECTS_LEVELS,
        "move": prompts.SETUP_MOVE,
        "older move": prompts.SETUP_MOVE_OLDER,
        "security": prompts.SETUP_SECURITY + "\n" + setup.nuclear_text("Maria-Christina Ann"),  # a long name fits
        "extras": prompts.SETUP_EXTRAS,
        "check": prompts.SETUP_CHECK_HERS,
        "harden password": harden.PASSWORD_HERS + "\n" + harden.PASSWORD_YOURS,
        "ssh done": harden.SSH_DONE.format(undo=f"{hostsec.SUDO} {hostsec.RM} {hostsec.SSH_DROP_IN_PATH}"),
        "remake": prompts.SETUP_MODEL_REMAKE,
        "code stopped": prompts.SETUP_CODE_STOPPED,
        "code failed": prompts.SETUP_CODE_FAILED,
        "opening": prompts.SETUP_OPENING,
        "ssh screen": prompts.SETUP_SSH_SCREEN,
        "projects share": prompts.SETUP_PROJECTS_SHARE,
        "signal account": prompts.SETUP_ACCOUNT,
        "phone's steps": hostsec.NUMBERLESS_STEPS,
        "key": hostsec.KEY_ASK,
    }


@pytest.mark.parametrize("key", list(explanations()))
def test_every_explanation_fits_the_terminal_in_plain_words(key: str) -> None:
    text = filled(explanations()[key])
    for line in text.splitlines():
        assert len(line) <= TERMINAL, (key, len(line), line)
    words = text.lower()
    assert "most people" not in words and "secure" not in words and "seamless" not in words, key
    assert " sudo " not in f" {words} ".replace("/usr/bin/sudo", ""), key  # "admin rights", except in a command


def test_the_smaller_model_is_for_use_with_caution_and_never_names_tests() -> None:
    """Owner, 19 Oct: "smaller model, use with caution - dont say it failed tests": shorter than the 490 characters
    before, what caution means for her answers in plain words, and no tests, test set, scores or failures."""
    said = plain_text(
        " ".join([prompts.SETUP_SMALLER_MODEL, prompts.SETUP_SMALLER_MODEL_ASK, prompts.SETUP_SMALLER_MODEL_NO])
    )  # as shown: the bold words' marks left out
    assert len(plain_text(prompts.SETUP_SMALLER_MODEL)) < 400
    for word in ("test", "fail", "score", "safety", "caution"):
        assert word not in said.lower(), word
    # the owner's words (29 Oct, 12b-gap-words: "dont scare the users"): the limits, then the safeguards, then 26B;
    # one idea a paragraph (the safeguards on two short lines), a blank line between them; a plain "Yes (recommended)"
    assert prompts.SETUP_SMALLER_MODEL == (
        "Gemma 4 12B on this Mac.\n\n"
        "Forwarded audio, mail or calendar may **steer her**.\n\n"
        "She may say a reminder is set when it is not.\n\n"
        "On calls, lights or plugs may switch before you say yes.\n\n"
        "Safeguards in code: audio facts stay out of her memory,\n"
        "and after audio, device changes wait for your yes on Signal.\n\n"
        "Generally, Gemma 26B (32 GB or more) keeps her rules better.\n\n"
        "No stops setup; nothing downloads."
    )
    assert prompts.SETUP_SMALLER_MODEL_ADVICE == ""
    shown = plain_text(prompts.SETUP_SMALLER_MODEL)
    assert all(len(part.splitlines()) <= 2 for part in shown.split("\n\n"))
    # review, 19 Oct: "before you have answered" (answered what?) and "after one wait" confused
    assert "before you say yes" in said and "have answered" not in said and "after one wait" not in said


def test_every_hidden_prompt_says_nothing_shows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The captcha link, the SMS code, the PINs: typed hidden, and each prompt says that nothing shows."""
    from local_assistant import harden

    token = "signal-hcaptcha.00000000-0000-4000-8000-000000000000.registration.fake-token-777"
    program, _folder = fake_signal_cli(
        tmp_path, monkeypatch, FAKE_SIGNAL_CAPTCHA=token, FAKE_SIGNAL_CODE="222222", FAKE_SIGNAL_PIN="9876"
    )
    typed = iter([f"signalcaptcha://{token}", "222222", "9876", "731904", "731904"])
    prompted: list[str] = []

    def hidden(prompt: str = "") -> str:
        prompted.append(prompt)
        return next(typed)

    monkeypatch.setattr(
        "builtins.input", lambda prompt="": "y" if "Registration Lock" in prompt or "right?" in prompt else BOT
    )
    monkeypatch.setattr("local_assistant.setup.read_hidden", hidden)
    monkeypatch.setattr(harden, "set_pin", lambda *args, **kwargs: (True, ""))
    wizard = Wizard(Path("/nonexistent/config.toml"), dry_run=False)
    wizard.signal_cli = program
    monkeypatch.setattr(wizard, "registered_numbers", list)
    assert wizard.signal_number() == BOT
    assert len(prompted) == 5 and all("nothing shows" in prompt for prompt in prompted), prompted
    assert prompted[0].startswith("captcha link") and "q skips" in prompted[3]

    def quit_at(prompt: str) -> str:
        prompted.append(prompt)
        return "q"

    step = harden.Harden(
        hostsec_fake_host(tmp_path),
        owner="alex",
        owner_home=tmp_path,
        own_user=False,
        ask=lambda question, default: False,
        hidden=quit_at,
        say=lambda text: None,
    )
    assert step.read_pin() is None and "nothing shows" in prompted[-1]


def hostsec_fake_host(tmp_path: Path) -> Any:
    from local_assistant.hostsec import Host

    return Host(tmp_path / "root", dry_run=True, say=lambda text: None)


def test_the_move_says_why_in_plain_words(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Why her own user, what yes and no do, in plain words: no sudo, no "owned by root", and no count of service
    files (a new install has one; the voice service comes later)."""
    for key in ("SSH_CONNECTION", "STY", "TMUX"):
        monkeypatch.delenv(key, raising=False)
    typed_once(monkeypatch, "n")
    Wizard(tmp_path / "config.toml", dry_run=False).always_on()
    out = capsys.readouterr().out
    assert "admin rights" in out and "scripts/run.sh" in out and prompts.SETUP_MOVE_MORE_TYPED in out
    more = prompts.SETUP_MOVE_MORE  # the details, in the window's fold and the setup guide (2.7)
    assert "as long as no admin rule skips the password" in more and "never runs as you or as root" in more
    assert "sudo" not in out and "owned by root" not in out and "service files" not in out


def test_the_security_step_names_the_nuclear_level_for_deeply_technical_users(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    typed_once(monkeypatch, "n")
    Wizard(tmp_path / "config.toml", dry_run=False).security()
    out = capsys.readouterr().out
    from local_assistant import hostsec, setup

    # Terminal has no fold (owner, 27 Oct: at most ~3 lines before a question): the nuclear level in one line
    assert prompts.SETUP_NUCLEAR_TYPED in out and hostsec.NUCLEAR in prompts.SETUP_NUCLEAR_TYPED
    assert "once 5 minutes have passed since admin rights were last used" in out
    # the owner's words (20 Oct), the same as harden's, doctor's and the docs', in the window's More fold
    from local_assistant.setup_answers import Answers
    from local_assistant.setup_plan import Mac, question

    filled = {"nuclear": setup.nuclear_text("Estia")}
    fold = " ".join(question("security").at(Answers.memory(), Mac(), filled).more.split())
    assert hostsec.NUCLEAR_LINE in fold
    assert all(len(line) <= TERMINAL for line in setup.nuclear_text("Estia").splitlines())
    for words in ("never part of setup", "a page opened from a laptop"):
        assert words in fold, words


def test_the_last_check_says_when_the_password_is_asked(
    no_system_paths: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    her_own_user(no_system_paths)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "run", recorder([]))
    wizard.check()
    out = capsys.readouterr().out
    assert "if asked (once 5 minutes have passed since admin rights were last used" in out and "it only reads." in out


# the smaller frictions (audit, 19 Oct, section 4)


def test_the_remaining_lines_say_their_facts_in_plain_words() -> None:
    """Review, 19 Oct: each says what a no does, nothing shows at a hidden prompt, and no internal words."""
    assert "No: she reads no projects." in prompts.SETUP_PROJECTS and "project tools" not in prompts.SETUP_PROJECTS
    assert "the files in your home that every account may" in " ".join(prompts.SETUP_PROJECTS.split())
    assert "No or q: the security step asks again." in prompts.SETUP_LOCK_HOW
    for move in (prompts.SETUP_MOVE, prompts.SETUP_MOVE_OLDER):
        assert "your Mac password once (nothing shows while you type)" in plain_text(move)
    assert "hidden: nothing shows while you type" in prompts.SETUP_OLD_PIN_ASK
    assert prompts.SETUP_LANGUAGE_DRY_RUN == "  (asks which one; Enter: English)"
    assert "Changed later only" not in prompts.SETUP_LANGUAGE and "wins over language in the config" in " ".join(
        prompts.SETUP_LANGUAGE.split()
    )
    assert prompts.SETUP_NAME_ASK == "Pick a name or type your own. (Enter keeps {default})"  # owner, 27 Oct
    assert "after a drop, screen -r goes back to it" in prompts.SETUP_SSH_SCREEN
    assert "no sudo" not in prompts.SETUP_PROJECTS_SHARE and "no admin rights needed" in prompts.SETUP_PROJECTS_SHARE
    opening = " ".join(prompts.SETUP_OPENING.split())
    assert "the questions first" in opening and "her Signal account" in opening
    # round 2 (words-13): the opening named seven steps in other words; it names the steps as the run numbers them
    for step in prompts.SETUP_STEPS.values():
        assert step in opening, step


def test_ollamas_step_line_is_as_short_as_its_facts_allow() -> None:
    """Round 2 (words-15, owner 29 Oct "be concise"): step 1's line was one 60-word paragraph with "sha256" and the
    developer ID; the checks stay, in GUIDE's words, and the IDs stay in the dry run (OLLAMA_CHECKS)."""
    checked = "checked before anything is installed: its fingerprint, every program's signature by Ollama and Apple's notarization"
    for text in (prompts.OLLAMA_INSTALL, prompts.OLLAMA_HER_INSTALL):
        assert checked in text and "{team}" not in text and "sha256" not in text, text
    assert "{team}" in prompts.OLLAMA_CHECKS
    guide = (Path(__file__).resolve().parent.parent / "GUIDE.md").read_text(encoding="utf-8")
    assert (
        "checked before anything is installed: its fingerprint, every program's signature by Ollama and Apple's notarization"
        in guide
    )
    assert "up to an hour" not in prompts.SETUP_AI_STARTED  # with calls it takes longer (round 2, ai-4)
    assert "Her notes, /status and /help **stay English**." in prompts.SETUP_LANGUAGE  # words-18: no "What code writes"


def test_setup_says_which_languages_have_a_voice_before_one_is_chosen() -> None:
    """The review of 4 Nov: setup said the other ten languages have one voice, and the language list marked none as
    text only, so a Mac set to Japanese took Japanese by Enter and its first call rang out. The voice screen names
    the six with jane and the four with none; the language list marks those four; she listens and answers in one."""
    from local_assistant import setup_plan

    english, others = prompts.SETUP_VOICE.split("\n\n")  # two paragraphs: a paragraph holds at most two lines
    assert english.startswith("Her **English voice**") and "\n" not in english
    assert others.splitlines() == [
        "Spanish, Italian, Portuguese, Dutch, German and French have **one voice**, jane.",
        "Korean, Polish, Russian and Japanese have **no voice**: text only.",
    ]
    assert "{name} listens and answers in **one language** at a time" in prompts.SETUP_LANGUAGE
    listing = setup_plan.CORE_CONFIG[0].listing
    assert "  • 日本語, Japanese (ja) (text only: no voice)" in listing.splitlines()
    assert "  • Español, Spanish (es)" in listing.splitlines()


def test_the_dry_runs_show_the_enter_default_where_it_is_yes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review, 19 Oct: the AI briefs from the dry run, which said "if you say yes" where Enter now takes yes."""
    wizard = Wizard(tmp_path / "config.toml", dry_run=True)
    wizard.signal_number()
    wizard.security()
    out = capsys.readouterr().out
    assert f'"{prompts.SETUP_LOCK_ASK} [Y/n]"' in out and f'"{prompts.SETUP_SECURITY_ASK} [Y/n]"' in out
    assert "if you say yes" not in out


# ------------------------------------------------------------------ Ctrl-C while a step runs (review setup-ctrl-c-kills-harden)


def _press_ctrl_c_once(started: Path) -> threading.Thread:
    """Ctrl-C at setup once the step's program says it started (the terminal sends it to every program in the
    foreground; the step's own program decides for itself)."""
    import os
    import signal
    import time

    def press() -> None:
        deadline = time.monotonic() + 10
        while not started.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        os.kill(os.getpid(), signal.SIGINT)

    thread = threading.Thread(target=press, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize("step", ["harden.sh", "migrate-to-own-user.sh", "estia-deploy"])
def test_a_ctrl_c_during_a_step_lets_it_end_then_stops_setup(tmp_path: Path, step: str) -> None:
    """A Ctrl-C while the security step, the move to her own account or her deploy runs: setup lets the step end
    (harden ignores it from her stop to her start again, the move restores her) and then stops with its line. Before,
    setup's own Ctrl-C killed the step halfway, and she stayed stopped."""
    import signal

    started, finished = tmp_path / "started", tmp_path / "finished"
    program = tmp_path / step
    program.write_text(f"#!/bin/sh\ntrap '' INT\n: > '{started}'\nsleep 1\n: > '{finished}'\n")
    program.chmod(0o755)
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    before = signal.getsignal(signal.SIGINT)
    pressed = _press_ctrl_c_once(started)
    with pytest.raises(KeyboardInterrupt):
        wizard.run([str(program)])
    pressed.join(timeout=10)
    assert finished.exists()  # the step ended by itself
    assert signal.getsignal(signal.SIGINT) is before  # setup's own Ctrl-C is back


def test_without_a_ctrl_c_a_step_runs_as_before(tmp_path: Path) -> None:
    import signal

    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    before = signal.getsignal(signal.SIGINT)
    done = wizard.run(["/bin/sh", "-c", "exit 3"])
    assert done.returncode == 3 and signal.getsignal(signal.SIGINT) is before


# ------------------------------------------------------------------ a Mac too small, said first (review setup-small-mac-after-plan)


@pytest.mark.parametrize("hers", [False, True], ids=["first install", "her own account"])
def test_a_mac_too_small_says_so_before_any_question(
    no_system_paths: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    hers: bool,
) -> None:
    """Below 15 GB setup stopped only after the whole plan (her number, About you): now before its first question,
    with the same line, and nothing is opened, asked or saved."""
    from local_assistant import setup_answers, setup_window
    from tests.setup_helpers import her_own_user

    if hers:
        her_own_user(no_system_paths)
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 8.0)
    monkeypatch.setattr(setup_window, "face", refuse)  # the window is never built or opened
    monkeypatch.setattr("builtins.input", refuse)
    for name in ("ollama", "model"):
        monkeypatch.setattr(Wizard, name, refuse)
    assert main(["--config", str(tmp_path / "config.toml")]) == 1
    out = capsys.readouterr().out
    assert prompts.SETUP_TOO_SMALL.format(memory=8.0, name="Estia") in out
    assert not setup_answers.default_path().exists()


@pytest.mark.parametrize(
    ("calls", "move", "need", "ollama"),
    [("n", "", 20, "Ollama 1 GB"), ("y", "", 27, "Ollama 1 GB"), ("n", "y", 22, "her Ollama 3 GB")],
    ids=["no-calls", "calls", "her-own-ollama"],
)
def test_too_little_free_disk_is_said_before_the_first_step(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    calls: str,
    move: str,
    need: int,
    ollama: str,
) -> None:
    """mac-ollama-disk-full (round 1): free disk was never checked; a 12B download stopped at 3 of 5 dots with "no
    space left on device ... run again to resume it", which loops. The space the steps picked need (her model,
    Ollama, calls) is checked right after the plan, before anything is downloaded, with how much to free."""
    from local_assistant import setup_plan

    monkeypatch.setattr(setup_plan, "free_disk_gb", lambda: 5.0)
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt="": (
            calls
            if prompt.startswith("Set up calls?")
            else move
            if prompt.startswith(prompts.SETUP_MOVE_ASK)
            else enter_but_signal(prompt)
        ),
    )
    for name in ("ollama", "model", "signal_number", "always_on", "security", "extras"):
        monkeypatch.setattr(Wizard, name, refuse)
    with pytest.raises(SystemExit) as stopped:
        main(["--config", str(tmp_path / "config.toml")])
    words = str(stopped.value)
    # her own Ollama on a new install with her own account: the room step 1 checks too (c-6, round 3)
    assert words.startswith(f"✗ Disk: 5 GB free, and this run needs about {need} GB (her model 18.3 GB, {ollama}")
    assert ("calls about 7 GB" in words) is (calls == "y")
    assert f"Fix: free about {need - 5} GB (System Settings → General → Storage), then run setup again" in words
    assert "Traceback" not in capsys.readouterr().out


@pytest.mark.parametrize(
    ("memory", "calls", "line"),
    [
        (16.0, None, "Next, the steps: about 15 minutes on a 100 Mbit/s line, most of it her model (7.7 GB)."),
        (
            32.0,
            "yes",
            "Next, the steps: about 30 minutes on a 100 Mbit/s line, most of it her model (18.3 GB); calls add a long "
            "build.",
        ),
    ],
)
def test_the_steps_time_is_said_once_the_plan_is_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, memory: float, calls: str | None, line: str
) -> None:
    """The friend's review (round 2, ux-21): no screen said how long the steps take. Once the plan is answered, one
    line says it, README's figures; nothing when her model is there already (a re-run: short steps)."""
    from local_assistant import setup_plan

    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: memory)
    said: list[str] = []
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    monkeypatch.setattr(wizard, "say", said.append)
    wizard.fixed_mac = setup_plan.Mac(memory_gb=memory)
    if calls:
        wizard.planned["calls"] = calls
    wizard.steps_time()
    assert said == [line]
    said.clear()
    wizard.fixed_mac = setup_plan.Mac(memory_gb=memory, model_listed=True)
    wizard.steps_time()
    assert said == []


def test_a_download_that_fills_the_disk_names_the_disk_not_the_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pull's "no space left on device" was "The download did not finish (...). Run scripts/setup.sh again to
    resume it", which loops on a full disk; it names the disk and what to free."""
    from local_assistant import setup_plan

    monkeypatch.setattr(setup_plan, "free_disk_gb", lambda: 500.0)
    monkeypatch.setattr(
        Wizard, "pull", lambda self, tag, size=0: "write blobs/sha256-ab-partial: no space left on device"
    )
    wizard = Wizard(tmp_path / "config.toml", dry_run=False)
    wizard.fixed_mac = setup_plan.Mac(memory_gb=32.0)
    with pytest.raises(SystemExit) as stopped:
        wizard.model()
    assert str(stopped.value) == prompts.SETUP_DISK_FULL.format(
        why="write blobs/sha256-ab-partial: no space left on device"
    )
    assert str(stopped.value).startswith("✗ Disk: the Mac ran out of space during the download (")
    assert "Fix: free space (System Settings → General → Storage), then run setup again: the download goes on" in (
        str(stopped.value)
    )


def test_a_dry_run_on_a_mac_too_small_still_shows_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 8.0)
    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(SystemExit):
        main(["--dry-run", "--config", str(tmp_path / "config.toml")])
    out = capsys.readouterr().out
    assert prompts.SETUP_NAME_DRY_RUN.format(default="Estia") in out  # the plan, as a dry run shows it
    assert prompts.SETUP_TOO_SMALL.format(memory=8.0, name="Estia") in out


@pytest.mark.parametrize("dry", [False, True], ids=["real", "dry"])
def test_the_mac_stays_awake_for_the_whole_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry: bool) -> None:
    """plat-09: only the model download kept the Mac awake; the voice build, the voice service's model wait and the
    calls build had nothing, while the screen said "the Mac stays awake". Setup holds macOS's caffeinate for the whole
    run (a dry run changes nothing and holds none); the screen says to keep it plugged in (a MacBook, its lid open)."""
    import contextlib
    from collections.abc import Iterator

    from local_assistant import setup_progress

    held: list[str] = []

    @contextlib.contextmanager
    def awake() -> Iterator[None]:
        held.append("on")
        yield None
        held.append("off")

    monkeypatch.setattr(setup_progress, "awake", awake)
    monkeypatch.setattr(wizard_module, "_run", lambda wizard, args: noted(held, "run", 0))
    argv = ["--config", str(tmp_path / "config.toml"), *(["--dry-run"] if dry else [])]
    assert wizard_module.run_setup(argv) == 0
    assert held == (["run"] if dry else ["on", "run", "off"])
    assert prompts.SETUP_DOWNLOAD.splitlines()[0] == (
        "  Nothing to answer meanwhile. Keep the Mac plugged in (a MacBook with its lid open): setup keeps it awake"
        " until it ends."
    )


class AtThePull(Exception):
    """The test's stand-in for the model's download: setup stops there, its lines said."""


@pytest.mark.parametrize(("route", "phone"), [("2", True), ("", False)], ids=["route-2", "route-1"])
def test_route_2_says_the_phones_part_after_the_plan_and_never_nothing_to_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], route: str, phone: bool
) -> None:
    """W14, in Terminal: route 2's "Meanwhile, on the spare or borrowed Android phone" came the moment the route was
    picked, mid-plan, and the model step then said "Nothing to answer meanwhile.". It comes once the plan is
    answered, before Step 1, and the model step leaves that sentence out; route 1 keeps SETUP_DOWNLOAD whole."""

    def pull(self: Wizard, tag: str, *, size: int = 0) -> str:
        raise AtThePull

    monkeypatch.setattr(Wizard, "ollama", lambda self: None)
    monkeypatch.setattr(Wizard, "pull", pull)
    monkeypatch.setattr("local_assistant.setup.memory_gb", lambda: 32.0)
    number = [] if phone else ["+30 690 000 0000", "y", "n"]  # her number, right, no Registration Lock
    nothing = [""] * 8  # Extra Features as recommended; no home area; About you left out
    answers = iter(["Juno", "m", "y", route, *number, "nl", "playful", "n", *nothing, "n", "n"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    with pytest.raises(AtThePull):
        main(["--config", str(tmp_path / "config.toml"), "--plain"])
    out = capsys.readouterr().out
    heads_up = plain_text(prompts.SETUP_ROUTE_NO_NUMBER_NEXT)
    assert out.count(heads_up) == (1 if phone else 0)
    assert ("Nothing to answer meanwhile" in out) is not phone
    assert prompts.SETUP_DOWNLOAD.splitlines()[1] in out  # the download's own line stays on both routes
    if phone:
        plan_end = out.index(prompts.SETUP_ANSWERS_WHERE.split("{", 1)[0])  # said once the plan is in the file
        assert plan_end < out.index(heads_up) < out.index("Step 1 of")
    else:
        assert prompts.SETUP_DOWNLOAD in out
