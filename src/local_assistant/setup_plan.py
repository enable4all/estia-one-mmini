"""Setup's order: what the Mac answers by itself (`Mac`), the one list of questions every face asks (`QUESTIONS`: the
core's, the Signal steps', the config's, the extras', then the end's), and the plan, asked first.

The list is put together here, so setup_questions imports no step module."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from local_assistant import hostsec, ollama_build, ownuser, prompts
from local_assistant.config import DEFAULT_NAME, DEFAULT_STYLE, STYLES, ConfigError, check_name, has_voice
from local_assistant.language import ENGLISH, LANGUAGES, find
from local_assistant.setup_questions import (
    OTHER,
    RECOMMENDED,
    Checked,
    Known,
    Option,
    Question,
    Value,
    Variant,
    pasted,
    yes_no,
)

SSH_KEYS = ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")  # set by macOS's sshd for a remote login
# set inside screen, tmux and herdr, which keep a session when the connection drops (and keep the environment of the
# session they were started in)
SESSION_KEYS = ("STY", "TMUX", "HERDR_ENV")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
_LANGUAGE = re.compile(r"^[a-z]{2,3}$")


def region() -> str:
    """The macOS region as a locale, like en_US or el_GR ("" when it cannot be read): a fixed command with no
    shell, under the tools' root (a test's fake tree; tests/conftest.py pins it besides)."""
    defaults = hostsec.tool_root() / "usr/bin/defaults"
    try:
        done = subprocess.run(
            [str(defaults), "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def free_disk_gb() -> float | None:
    """The free disk at your home in GB, as Finder counts them (decimal): what a download may take; None when it
    cannot be read."""
    try:
        return shutil.disk_usage(Path.home()).free / 1e9
    except OSError:
        return None


def from_locale(locale: str) -> tuple[str, str]:
    """(country, language) from a macOS locale: en_US gives ("US", "en"); a region set apart from the language
    (en_US@rg=grzzzz) gives its own country; a language that is not one of the 11 gives English; anything else
    ("", "en")."""
    base, _, extra = locale.strip().partition("@")
    parts = re.split(r"[_-]", base)
    language = parts[0] if _LANGUAGE.fullmatch(parts[0]) else ""
    if not language:
        return "", ENGLISH
    country = parts[-1] if len(parts) > 1 and _COUNTRY.fullmatch(parts[-1]) else ""
    override = re.search(r"(?:^|;)rg=([a-z]{2})zzzz(?:;|$)", extra)
    if override is not None:
        country = override.group(1).upper()
    return country, language if language in LANGUAGES else ENGLISH


@dataclass(frozen=True)
class Mac:
    """What the Mac answers, never asked (spec rule 4), read once before the questions."""

    memory_gb: float = 0.0
    country: str = ""  # "GR", from the macOS region; "" when it could not be read
    language: str = ENGLISH  # one of language.CODES: the region's, English when it is not one of the 11
    ssh: bool = False  # setup runs over a remote login (SSH_CONNECTION, SSH_CLIENT or SSH_TTY)
    kept_session: bool = False  # inside screen, tmux or herdr, so a dropped connection does not stop it (SESSION_KEYS)
    own_user: bool = False  # she runs as her own macOS account (ownuser.moved)
    configured: bool = False  # her config is set up: yours with both accounts, or hers in her own user
    legacy: bool = False  # an older install, always on as you (the bridge's service file names you)
    model_listed: bool = False  # Ollama lists local-worker already
    ollama_app: str = ""  # the Ollama app on this Mac (its path; "" when none): setup asks whether to use it
    free_gb: float | None = None  # free disk at your home, in GB as Finder counts them (None: not read, a test's Mac)
    her_ollama: bool = False  # setup's service file runs her own Ollama (as _estia): it stays, the app is not asked
    # a first install whose Ollama is not found: with her own Ollama for new installs her store is empty, whatever
    # 11434 lists (`model_listed`), unless the Ollama app is chosen (c-7, round 3)
    fresh_hers: bool = False

    @classmethod
    def read(
        cls,
        *,
        memory_gb: float = 0.0,
        own_user: bool | None = None,
        configured: bool = False,
        model_listed: bool = False,
        fresh_hers: bool = False,
    ) -> Mac:
        """The Mac's own answers: the region's country and language, a remote login and screen or tmux (from the
        environment), her own user and an older install (the bridge's service file under the system root). What
        setup knows already comes in: the memory, her own user as the Wizard sees it, a config set up, the model. The
        Ollama app in /Applications, or in your own ~/Applications on the live Mac only. The free disk, for the
        check before the first step (mac-ollama-disk-full)."""
        country, language = from_locale(region())
        root = hostsec.estia_root()
        app = ollama_build.app_on_mac(root, ollama_build.owner_home() if root == Path("/") else None)
        return cls(
            memory_gb=memory_gb,
            country=country,
            language=language,
            ssh=any(os.environ.get(key) for key in SSH_KEYS),
            kept_session=any(os.environ.get(key) for key in SESSION_KEYS),
            own_user=ownuser.moved(root) if own_user is None else own_user,
            configured=configured,
            legacy=(root / ownuser.DAEMONS.relative_to("/") / f"{ownuser.BRIDGE}.plist").exists(),
            model_listed=model_listed,
            ollama_app="" if app is None else str(app),
            free_gb=free_disk_gb(),
            her_ollama=ollama_build.layout_found(root) == "hers",
            fresh_hers=fresh_hers,
        )


# ------------------------------------------------------------------ the checks of the core's questions


def _ollama_app(value: Value, answers: Known, mac: Mac) -> Checked:
    """Use the Ollama app (1, Enter) or install the background build (2)."""
    answer = pasted(value if isinstance(value, str) else "", spaces=False)[0].lower()
    if answer in ("1", "app"):
        return Checked("app")
    if answer in ("2", "build"):
        return Checked("build")
    return Checked(None, prompts.SETUP_OLLAMA_APP_UNKNOWN)


def _build_line() -> str:
    """The card's second option: with her own Ollama for new installs, the app stays with her own account (asked
    later in the plan) and must go without it; today's line when the switch says "older"."""
    return (
        prompts.SETUP_OLLAMA_APP_BUILD_LINE_HERS
        if ollama_build.NEW_INSTALLS == "hers"
        else prompts.SETUP_OLLAMA_APP_BUILD_LINE
    )


def _cards_listed() -> str:
    """Terminal's list of the cards by the same rule as `_build_line` (c-1, round 3: plain lines, gum's fallback and
    the dry run said the app must go to the Trash while the window said it stays)."""
    return prompts.SETUP_OLLAMA_APP_LIST_HERS if ollama_build.NEW_INSTALLS == "hers" else prompts.SETUP_OLLAMA_APP_LIST


def _ollama_cards(answers: Known, mac: Mac) -> Variant:
    """Over a remote login the background build is recommended and Enter takes it: the app starts only with someone
    at the Mac's screen (c-4, round 3; round 1's issue 6). So it is with her own account planned (a new install,
    owner, 30 Oct: W06): she then starts with the Mac, without a login, and the app stays a choice, its card naming
    its limits. Otherwise the cards as written."""
    if mac.ssh:
        advice, ask, line = prompts.SETUP_OLLAMA_APP_SSH_ADVICE, prompts.SETUP_OLLAMA_APP_SSH_ASK, _build_line()
    elif mac.fresh_hers and not mac.own_user:
        advice, ask = prompts.SETUP_OLLAMA_APP_HERS_ADVICE, prompts.SETUP_OLLAMA_APP_ASK_BUILD
        line = prompts.SETUP_OLLAMA_APP_BUILD_HERS_STAYS  # the advice before it says she starts with the Mac (n-1)
    else:
        return {}
    return {
        "options": (
            Option("app", prompts.SETUP_OLLAMA_APP_USE, line=prompts.SETUP_OLLAMA_APP_USE_LINE, short="1"),
            Option("build", prompts.SETUP_OLLAMA_APP_BUILD, line=line, tag=RECOMMENDED, short="2"),
        ),
        "recommended": "build",
        "advice": advice,
        "ask": ask,
    }


def _suggested(name: str) -> tuple[str, str] | None:
    from local_assistant.setup import suggested  # setup.py's (it imports this module)

    return suggested(name)


def _name(value: Value, answers: Known, mac: Mac) -> Checked:
    """A suggested name (any case, or 1), as it is written, or any other name that fits the config's rule; shown back
    as the input spec says it ("She will answer to Maya."; the male suggestion "He will answer to Socrates.", c-17,
    confirm-2)."""
    from local_assistant.setup import MALE_VOICE  # setup.py's (it imports this module)

    typed, note = pasted(value if isinstance(value, str) else "", spaces=False)
    known = _suggested(typed)
    try:
        name = known[0] if known is not None else check_name(typed)
    except ConfigError:
        return Checked(None, prompts.SETUP_NAME_RULE)
    he = known is not None and known[1] == MALE_VOICE
    shown = (prompts.SETUP_NAME_SHOWN_HE if he else prompts.SETUP_NAME_SHOWN).format(name=name)
    return Checked(name, shown="\n".join(filter(None, (note, shown))))


def typed_name(answers: Known) -> bool:
    """The name answered is not one of the suggestions, so its voice is asked."""
    name = answers.get("name")
    return isinstance(name, str) and _suggested(name) is None


def _voice(value: Value, answers: Known, mac: Mac) -> Checked:
    """Female or male, for a name that is not a suggestion; shown back with the name and the voice it brings."""
    from local_assistant.setup import MALE_VOICE, female_voice  # setup.py's (it imports this module)

    answer = pasted(value if isinstance(value, str) else "", spaces=True)[0].lower()
    kind = {"f": "female", "female": "female", "m": "male", "male": "male"}.get(answer)
    if kind is None:
        return Checked(None, prompts.SETUP_VOICE_UNKNOWN)
    name = answers.get("name")
    voice = MALE_VOICE if kind == "male" else female_voice(mac.memory_gb)
    shown = prompts.SETUP_NAME_CHOSEN.format(name=name, kind=kind, voice=voice) if isinstance(name, str) else ""
    return Checked(kind, shown=shown)


def _language(value: Value, answers: Known, mac: Mac) -> Checked:
    answer = pasted(value if isinstance(value, str) else "", spaces=False)[0]
    chosen = find(answer)
    if chosen is None:
        return Checked(None, prompts.SETUP_LANGUAGE_UNKNOWN.format(answer=answer))
    return Checked(chosen.code, shown=prompts.SETUP_LANGUAGE_CHOSEN.format(name=prompts.language_name(chosen.code)))


def _style(value: Value, answers: Known, mac: Mac) -> Checked:
    answer = pasted(value if isinstance(value, str) else "", spaces=True)[0].lower()
    chosen = [name for name in STYLES if name.startswith(answer)] if answer else []
    if len(chosen) != 1:
        return Checked(None, prompts.SETUP_STYLE_UNKNOWN.format(answer=answer))
    return Checked(chosen[0], shown=prompts.SETUP_STYLE_CHOSEN.format(style=chosen[0]))


# ------------------------------------------------------------------ what varies with the Mac


def _names(answers: Known, mac: Mac) -> Variant:
    """The name's options (owner, 27 Oct: "only 4 suggestions, allow the user to fill-in a name"): Estia first, the
    three suggestions, then the field for her own (setup.py holds the suggestions)."""
    from local_assistant.setup import FEMALE_NAMES, MALE_NAMES  # setup.py's (it imports this module)

    names = (Option(DEFAULT_NAME, DEFAULT_NAME, tag=RECOMMENDED), *(Option(n, n) for n in (*FEMALE_NAMES, *MALE_NAMES)))
    return {"options": (*names, Option(OTHER, prompts.SETUP_NAME_OTHER))}


def _language_for(answers: Known, mac: Mac) -> Variant:
    """The language the macOS region names, listed first and taken by Enter."""
    first = mac.language if mac.language in LANGUAGES else ENGLISH
    codes = [first, *(code for code in LANGUAGES if code != first)]
    variant: Variant = {  # the question asks for the language spoken at home: Enter takes the Mac's, unrecommended
        "recommended": first,
        "options": tuple(Option(code, _label(code)) for code in codes),
    }
    if first != ENGLISH:  # the line says which one Enter takes
        own = LANGUAGES[first].own
        variant["ask"] = prompts.SETUP_LANGUAGE_ASK_MAC.format(language=own)
        variant["dry"] = prompts.SETUP_LANGUAGE_DRY_RUN_MAC.format(language=own)
    return variant


def _label(code: str) -> str:
    """ "Nederlands, Dutch"; one her voice does not speak marked (prompts.NO_VOICE_MARK; the review of 4 Nov)."""
    language = LANGUAGES[code]
    label = language.own if language.own == language.english else f"{language.own}, {language.english}"
    return label + ("" if has_voice(code) else prompts.NO_VOICE_MARK)


def model_there(answers: Known, mac: Mac) -> bool:
    """Ollama lists local-worker where her model goes: for a first install that gets her own Ollama (her store is
    empty) only when the Ollama app is chosen, whose own store lists it (c-7, round 3)."""
    if not mac.model_listed:
        return False
    return not mac.fresh_hers or (bool(mac.ollama_app) and answers.get("ollama_app") == "app")


def unkept_ssh(mac: Mac) -> bool:
    """Over a remote login outside screen or tmux: a dropped connection would stop the move halfway."""
    return mac.ssh and not mac.kept_session


def _move_words(answers: Known, mac: Mac) -> Variant:
    """The move's lines for this Mac: an older install's, and over SSH without screen the line that says to answer no
    and start screen, where Enter is a no too (and the no is what it recommends). With the Ollama app chosen, its
    first paragraph and its More say the app's limits in the card's words (review W06): she answers only while
    someone is logged in and the Ollama app runs, and her prompts pass through a program your account can change;
    More's last paragraph is replaced by one with that limit inside its claim (fix review, setup-macos-01)."""
    unkept = unkept_ssh(mac)
    lead = prompts.SETUP_MOVE_OLDER if mac.legacy else prompts.SETUP_MOVE
    ask = prompts.SETUP_MOVE_OLDER_ASK if mac.legacy else prompts.SETUP_MOVE_ASK
    window = prompts.SETUP_WINDOW_MOVE_OLDER if mac.legacy else prompts.SETUP_WINDOW_MOVE
    words: Variant = {}
    if bool(mac.ollama_app) and answers.get("ollama_app") == "app":
        first = prompts.SETUP_MOVE_OLDER_APP if mac.legacy else prompts.SETUP_MOVE_APP
        lead, window = (first + text[text.index("\n\n") :] for text in (lead, window))
        words["more"] = prompts.SETUP_MOVE_MORE_APP
    if unkept:
        screen = f"\n\n{prompts.SETUP_SSH_SCREEN}"
        return words | {
            "lead": lead + screen, "window_lead": window + screen, "ask": ask, "recommended": "no",
            "advice": prompts.SETUP_MOVE_SSH_ADVICE,
        }  # fmt: skip
    return words | {"lead": lead, "window_lead": window, "ask": ask, "recommended": "yes"}


# the lines the More fold and Terminal wrap their details to (owner, 27 Oct: "long setup lines cut into short ones")
SHORT_LINE = 88


def nuclear_text(name: str) -> str:
    """The security step's line on the nuclear level: the owner's words with her name, then how it is set, in short
    lines."""
    return textwrap.fill(f"{hostsec.nuclear_line(name)} {prompts.SETUP_NUCLEAR}", SHORT_LINE, break_on_hyphens=False)


# ------------------------------------------------------------------ the core's questions, in the order they come

CORE_START: tuple[Question, ...] = (
    Question(  # owner, 29 Oct: "since I have the Ollama app, use it"; asked first, as Ollama is the first step
        "ollama_app",
        "cards",
        "The Ollama app is on this Mac",
        lead=prompts.SETUP_OLLAMA_APP_LEAD,
        listing=_cards_listed(),  # Terminal's list of the cards; the window draws the cards
        ask=prompts.SETUP_OLLAMA_APP_ASK,
        options=(
            Option(
                "app", prompts.SETUP_OLLAMA_APP_USE, line=prompts.SETUP_OLLAMA_APP_USE_LINE, tag=RECOMMENDED, short="1"
            ),
            Option("build", prompts.SETUP_OLLAMA_APP_BUILD, line=_build_line()),
        ),
        recommended="app",
        advice=prompts.SETUP_OLLAMA_APP_ADVICE,
        check=_ollama_app,
        vary=_ollama_cards,
        shows=lambda answers, mac: bool(mac.ollama_app) and not mac.her_ollama,  # her own Ollama, once set up, stays
    ),
    Question(
        "smaller_model",
        "yes_no",
        "The smaller model",
        lead=prompts.SETUP_SMALLER_MODEL,
        ask=prompts.SETUP_SMALLER_MODEL_ASK,
        recommended="yes",  # the window's main button (walk-through, 27 Oct); Enter in Terminal (29 Oct)
        advice=prompts.SETUP_SMALLER_MODEL_ADVICE,
        check=yes_no,
        forget_no=True,  # a no stops setup; the next run asks again
        shows=lambda answers, mac: 15 <= mac.memory_gb < 30 and not model_there(answers, mac),
    ),
    Question(
        "name",
        "choice",
        "Name",
        lead=prompts.SETUP_NAME_LEAD,
        listing=prompts.SETUP_NAME_LIST,
        ask=prompts.SETUP_NAME_ASK,
        recommended=DEFAULT_NAME,
        check=_name,
        vary=_names,
        dry=prompts.SETUP_NAME_DRY_RUN,
        shows=lambda answers, mac: not mac.configured,
    ),
    Question(
        "voice",
        "choice",
        "Voice",
        lead=prompts.SETUP_VOICE,
        ask=prompts.SETUP_VOICE_ASK,
        options=(Option("female", "Female"), Option("male", "Male")),  # the lead says: the one that fits the name
        recommended="female",
        check=_voice,
        shows=lambda answers, mac: not mac.configured and typed_name(answers),
    ),
)
CORE_CONFIG: tuple[Question, ...] = (
    Question(
        "language",
        "choice",
        "Language",
        lead=prompts.SETUP_LANGUAGE,
        listing="\n".join(f"  {line}" for line in prompts.language_lines()),
        ask=prompts.SETUP_LANGUAGE_ASK,
        recommended=ENGLISH,
        check=_language,
        vary=_language_for,
        dry=prompts.SETUP_LANGUAGE_DRY_RUN,
        shows=lambda answers, mac: not mac.configured,
    ),
    Question(
        "style",
        "choice",
        "Style",
        lead=prompts.SETUP_STYLE_LEAD,
        listing=prompts.SETUP_STYLE_LIST,
        ask=prompts.SETUP_STYLE_ASK,
        options=(
            Option("hearth", "hearth", line=prompts.SETUP_STYLE_HEARTH, tag=RECOMMENDED),
            Option("playful", "playful", line=prompts.SETUP_STYLE_PLAYFUL),
        ),
        recommended=DEFAULT_STYLE,
        check=_style,
        shows=lambda answers, mac: not mac.configured or mac.own_user,
        dry=prompts.SETUP_STYLE_DRY_RUN,
    ),
)
CORE_END: tuple[Question, ...] = (
    Question(
        "move",
        "yes_no",
        "Always on",
        lead=prompts.SETUP_MOVE,
        window_lead=prompts.SETUP_WINDOW_MOVE,
        more=prompts.SETUP_MOVE_MORE,
        more_typed=prompts.SETUP_MOVE_MORE_TYPED,
        ask=prompts.SETUP_MOVE_ASK,
        recommended="yes",
        check=yes_no,
        forget_no=True,  # "No: … setup asks again at its next run"
        vary=_move_words,
        dry=prompts.SETUP_MOVE_DRY_RUN,
        shows=lambda answers, mac: not mac.own_user,
    ),
    Question(
        "security",
        "yes_no",
        "Security",
        lead=f"{prompts.SETUP_SECURITY}\n\n{prompts.SETUP_SECURITY_PASSWORD}\n{prompts.SETUP_SECURITY_NO}",
        # the window's Yes and No, each a paragraph (the page joins a paragraph's lines); no Mac password there
        window_lead=f"{prompts.SETUP_SECURITY}\n\n{prompts.SETUP_WINDOW_SECURITY_YES}\n\n{prompts.SETUP_SECURITY_NO}",
        more=f"{prompts.SETUP_SECURITY_MORE}\n\n{{nuclear}}",
        more_typed=prompts.SETUP_NUCLEAR_TYPED,
        ask=prompts.SETUP_SECURITY_ASK,
        recommended="yes",
        check=yes_no,
        forget_no=True,
        dry=prompts.SETUP_SECURITY_DRY_RUN,
    ),
    Question(
        "remake",
        "yes_no",
        "Model prompt",
        lead=prompts.SETUP_MODEL_REMAKE,
        ask=prompts.SETUP_MODEL_REMAKE_ASK,
        recommended="yes",  # "yes, once she is stopped": the window's main button
        advice=prompts.SETUP_MODEL_REMAKE_ADVICE,
        check=yes_no,
        keep="step",
        plan=False,
    ),
)


def _assembled() -> tuple[Question, ...]:
    from local_assistant import setup_admin, setup_extras, setup_signal

    return (
        *CORE_START,
        *setup_signal.QUESTIONS,
        *CORE_CONFIG,
        *setup_extras.QUESTIONS,
        *CORE_END,
        *setup_admin.QUESTIONS,
    )


QUESTIONS: tuple[Question, ...] = _assembled()
_BY_ID = {q.id: q for q in QUESTIONS}


def question(qid: str) -> Question:
    """The question by its id; KeyError names the known ones."""
    try:
        return _BY_ID[qid]
    except KeyError:
        raise KeyError(f"no question {qid!r}: {', '.join(_BY_ID)}") from None
