"""Setup's extras (`scripts/setup.sh`, `--extras`): calls first, then the Extra Features, each with its guide; what
each one is, what leaves the Mac and what it needs; the ones setup switches on itself (web search, places and routes,
chords, documents, projects). The Wizard in setup.py runs them.

The plan (phase A) asks whether to set up calls and which Extra Features; their steps come after the security step
(phase B): calls, then one extra at a time, each ending with how you know it worked. The sections that need nothing
from you go into the config at the config step of a first install, before the move, so no line is copied by hand.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import os
import re
import shutil
import subprocess
import time
import tomllib
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from local_assistant import (
    herconfig,
    hostsec,
    mail_http,
    osm,
    ownuser,
    prompts,
    secretfile,
    setup_admin,
    setup_handover,
    setup_keychain,
    share,
    web_search,
)
from local_assistant.config import (
    CALLS_SIGNAL_CLI_VERSION,
    PROJECT_LEVELS,
    Config,
    ConfigError,
    jvm_signal_cli,
    parse_config,
    tunnel_stamp_differs,
)
from local_assistant.hostsec import SUDO
from local_assistant.initiative import has_link
from local_assistant.map_data import MapsError
from local_assistant.maps import WORDS_CHARS
from local_assistant.profile import check_fact
from local_assistant.setup_answers import DONE_EXTRA, HANDED, private_folders
from local_assistant.setup_base import WizardBase
from local_assistant.setup_questions import (
    OTHER,
    RECOMMENDED,
    Checked,
    Field,
    Guide,
    Known,
    Option,
    Question,
    Value,
    Variant,
    clean_paste,
    yes_no,
)

if TYPE_CHECKING:
    from local_assistant.setup_plan import Mac

REPO = Path(__file__).resolve().parents[2]
# the Extra Features, in the preview's order (owner, 26 Oct); web search and places and routes are on by default.
# Voice is part of calls. Each id is a key of EXTRAS below.
FEATURES = ("web", "maps", "mail", "google-maps", "calendar", "music", "documents", "chords", "projects", "lights")
FEATURES_ON = ("web", "maps")
_FEATURE_WORDS = {  # the other words a plain line may name one by
    "places": "maps",
    "routes": "maps",
    "osm": "maps",
    "openstreetmap": "maps",
    "google": "google-maps",
    "calendars": "calendar",
    "spotify": "music",
    "plugs": "lights",
    "search": "web",
}
# the config's section each switches on at the config step of a first install (projects: render_config's [projects])
SECTIONS = {  # an empty [calendar] is one Google calendar, its secret address saved at the extras step
    "web": "web",
    "maps": "maps",
    "google-maps": "maps",
    "chords": "chords",
    "documents": "documents",
    "calendar": "calendar",
}
_SPOTIFY_CLIENT = re.compile(r'^#client_id = "0123456789abcdef0123456789abcdef"$', re.MULTILINE)
_MAIL_ID = r'^#{key} = "00000000-0000-0000-0000-000000000000"$'
_MAIL_ACCOUNT = re.compile(r'^#\[\[mail\.accounts\]\]\n#name = "work".*\n#address = "you@example\.com"$', re.MULTILINE)
_WEB_COUNTRY = re.compile(r'^#country = "NL"( +#.*)?$', re.MULTILINE)
_DOCUMENTS_FOLDER = re.compile(r'^#folder = "/Users/<you>/Estia-KB"( +#.*)?$', re.MULTILINE)
# the calls step's builds: the call helper's binary (tools/call-tunnel/build.sh) and her voice service's service file
# the extras signed in at their step, on Enter: (their section, the script, the plan's question with their IDs)
# the scripts that, in her own macOS account, only run her command as her (`exec sudo estia <command>`): in the window
# (no terminal there for sudo) her command itself, through macOS's own box
AS_HER = {
    "tapo-discover.sh": "tapo-discover",
    "hue-pair.sh": "hue-pair",
    "mail-login.sh": "mail-login",
    "spotify-login.sh": "spotify-login",
}
SIGN_INS = {
    "mail": ("mail", "mail-login.sh", "mail_ids"),
    "calendar": ("calendar", "set-calendar-url.sh", ""),
    "music": ("spotify", "spotify-login.sh", "spotify_id"),
}
# the checkout's vendor folder (gitignored), where tools/call-tunnel's scripts put the call builds and estia-deploy
# copies them from into her release
VENDOR = REPO / "vendor"
_TUNNEL = Path("signal-call-tunnel/signal-call-tunnel/target/release/signal-call-tunnel")
TUNNEL = VENDOR / _TUNNEL
# install-voice-daemon.sh's exit code when her voice is still loading after its wait: no fault, the build goes on
VOICE_STILL_LOADING = 3
RUST = "1.91.1"  # tools/call-tunnel/build.sh's toolchain
CALL_TOOLS = ("openjdk", "gnupg", "rustup", "cmake", "protobuf", "coreutils")  # Homebrew's, for the two builds
# web search's keys, each pasted once: (question, her web.env key, the login keychain's service, its name on screen);
# the services are the ones scripts/set-web-keys.sh reads
WEB_KEYS = (
    ("ollama_key", "OLLAMA_API_KEY", "ollama", "Ollama's key"),
    ("brave_key", "BRAVE_API_KEY", "brave-search", "Brave's key"),
)
TEST_SEARCH = "Signal"  # the one test search a new Ollama key gets: a word that names nothing of yours
REFUSED = (401, 402, 403)  # Ollama refused the key (or its plan)
# a first install's questions asked again on an install set up when `--redo` names them: their step runs there too
# (calls: "Later: scripts/setup.sh --redo calls", review setup-redo-calls-noop); her Signal account's do not
REDO_SET_UP = frozenset({"calls"})


def _config_problem(text: str) -> str:
    """Why a config's text would not load ("" when it loads)."""
    try:
        parse_config(tomllib.loads(text))
    except (tomllib.TOMLDecodeError, ConfigError) as exc:
        return str(exc)
    return ""


def projects_refusal(folder: Path, home: Path) -> str | None:
    """Why setup does not take this projects folder, by the share's rules (`share.refusal`: the move shares it with
    her); None when it takes it. Inside your home: a folder of yours, reached without a symbolic link. Outside it:
    no symbolic link either, the folder or one on the way; the share leaves such a folder to you
    (`prompts.SETUP_PROJECTS_OUTSIDE`)."""
    plain, top = Path(os.path.normpath(folder)), Path(os.path.normpath(home))
    if _through_a_link(plain, top):
        return prompts.SETUP_PROJECTS_LINK.format(folder=folder)
    if plain.is_relative_to(top):
        return share.refusal(plain, top, os.getuid())
    return None


def _through_a_link(plain: Path, top: Path) -> bool:
    """The folder is a symbolic link, or one on the way to it is (inside your home: below the home itself, which may
    sit behind a system link)."""
    if os.path.islink(plain):
        return True
    if plain.is_relative_to(top):
        return os.path.realpath(plain) != os.path.join(os.path.realpath(top), plain.relative_to(top))
    return os.path.realpath(plain) != str(plain)


def _voice_installed() -> bool:
    """Her voice service's service file is in place (under the system root: a test's fake tree)."""
    from local_assistant.hostsec import estia_root

    return (estia_root() / ownuser.DAEMONS.relative_to("/") / f"{ownuser.VOICE}.plist").exists()


def _release_has_calls() -> bool:
    """Her live release holds the call helper built from this checkout's sources (its stamp: second deep scan,
    signal-06) and a Java signal-cli (estia-deploy copied them), under the system root (a test's fake tree)."""
    from local_assistant.hostsec import estia_root

    vendor = estia_root() / ownuser.CURRENT.relative_to("/") / "vendor"
    helper = vendor / _TUNNEL
    return helper.is_file() and not tunnel_stamp_differs(helper) and jvm_signal_cli(vendor) is not None


def _release_reads_answer() -> bool:
    """Her live release's Spotify sign-in asks for the address on a marked line (spotify_auth.read_answer), which the
    window answers; one from before asks with input() and no line end. Read as you: her release is root's, 0755, under
    the system root (a test's fake tree)."""
    from local_assistant.hostsec import estia_root

    source = estia_root() / ownuser.CURRENT.relative_to("/") / "src" / "local_assistant" / "spotify_auth.py"
    try:
        return "def read_answer(" in source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _tunnel_built() -> bool:
    """The checkout's call helper is there, built from its own sources (build.sh's stamp beside it; signal-06): a
    later Estia's local.patch builds it again."""
    return TUNNEL.is_file() and not tunnel_stamp_differs(TUNNEL)


def _macos_version() -> str:
    """This Mac's macOS version (sw_vers by its full path under the tools' root, as the voice script reads it); ""
    when it cannot be read."""
    try:
        done = subprocess.run(
            [str(hostsec.tool_root() / "usr/bin/sw_vers"), "-productVersion"],
            capture_output=True, text=True, timeout=10, check=False,
        )  # fmt: skip
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _shown_command(command: list[str]) -> str:
    """A command as you would type it from the checkout: its script by its path in the repo."""
    first = Path(command[0])
    shown = str(first.relative_to(REPO)) if first.is_relative_to(REPO) else command[0]
    return " ".join([shown, *command[1:]])


def outside_home(folder: Path, home: Path) -> bool:
    """A projects folder the share does not share (it shares only folders in your home): opened to her by hand."""
    return not Path(os.path.normpath(folder)).is_relative_to(Path(os.path.normpath(home)))


@dataclass(frozen=True)
class _Where:
    """The projects folder as typed (~ kept), as an absolute path, and the level."""

    typed: str
    folder: Path
    level: str


@dataclass(frozen=True)
class Extra:
    title: str
    leaves_the_mac: str  # what leaves the Mac, and to which service by name
    how: str
    needs: str  # what it takes from you: an account, a sign-in, a key, a device


EXTRAS: dict[str, Extra] = {  # new ones go last: the numbers of the others stay
    "voice": Extra(
        "Voice notes and spoken answers (speech stays on the Mac)",
        "nothing (the speech models are downloaded once, from Hugging Face)",
        "Take the # off [voice] and its lines in her config (the voice line has the voice that goes with her name),\n"
        "  then run scripts/install-voice-daemon.sh (setup_assistant.md, 3, Voice notes).",
        "about 1.7 GB of disk and your Mac password once",
    ),
    "calendar": Extra(
        "Calendars (Google, Outlook, iCloud, Fastmail or Nextcloud) in the morning digest and on request",
        "each calendar, read-only, from its own service: Google, Microsoft, iCloud, Fastmail or Nextcloud",
        "Google: scripts/set-calendar-url.sh; iCloud, Fastmail or Nextcloud: scripts/set-calendar-password.sh;\n"
        "  Outlook: its mailbox's sign-in (mail); then add [calendar] (setup_assistant.md, 3, Calendar).",
        "a Google calendar's secret address, an app-specific password (iCloud, Fastmail, Nextcloud) or the mail extra",
    ),
    "mail": Extra(
        "Read-only mail from Microsoft 365 mailboxes, sorted for you",
        "mail is read from Microsoft (Microsoft Graph); bodies are never stored",
        "Register an Entra app (Microsoft's admin site), add [mail], then scripts/mail-login.sh <address>\n"
        "  (a code typed at Microsoft's sign-in page) (setup_assistant.md, 3, Mail).",
        "a Microsoft 365 mailbox, an app registered in Microsoft's admin site and a Microsoft sign-in",
    ),
    "lights": Extra(
        "Lights and plugs at home: Kasa, Tapo and Philips Hue",
        "nothing (they are switched over your home network)",
        "Kasa and Tapo: scripts/tapo-discover.sh (newer ones also scripts/set-tapo-credentials.sh, the TP-Link\n"
        "  login); Hue: scripts/hue-pair.sh (press the Hue Bridge's button when asked); then add [[tapo.devices]]\n"
        "  (setup_assistant.md, 3, Lights and plugs).",
        "the devices on your home network; newer Kasa and Tapo: the TP-Link login; Hue: the Bridge's button",
    ),
    "web": Extra(
        "Web search with sources",
        "your search words, to Ollama's web search (Brave Search as backup)",
        "Setup asks for each key, typed hidden (also over SSH), and saves it for her; then add [web], unless it is\n"
        "  there already (setup_assistant.md, 3, Web search).",
        "an API key from your account at ollama.com (and Brave Search's, for the backup)",
    ),
    "maps": Extra(
        "Places and routes from OpenStreetMap: free, with opening hours where the map has them",
        "the place or route you ask about, to OpenStreetMap's free services; 'near me' is rounded to about 1 km",
        "Nothing to install: setup adds [maps] to your config now; in her own macOS account it prints the line to add\n"
        "  (GUIDE.md, Places and routes).",
        "nothing",
    ),
    "music": Extra(
        "Spotify by text message",
        "play and pause commands, to Spotify",
        "Create a Spotify app, add [spotify], then scripts/spotify-login.sh (setup_assistant.md, 3, Spotify).",
        "Spotify Premium, an app on Spotify's developer site and a Spotify sign-in",
    ),
    "projects": Extra(
        "Your git projects in one folder of this Mac, read-only: summary, docs or code",
        "nothing (the files are read on the Mac)",
        "Nothing to install: setup asks the folder and the level now (setup_assistant.md, 3, Projects).",
        "a folder of git projects in your home",
    ),
    "documents": Extra(
        "Your PDFs and text files in ~/Estia-KB and the ones you send her, read on the Mac",
        "nothing (the files are read on the Mac)",
        "Run scripts/share-documents.sh (as you, no admin rights: it makes ~/Estia-KB and lets her account read it\n"
        '  and add files to it), then add [documents] with folder = "/Users/<you>/Estia-KB", unless it is there\n'
        "  already (setup_assistant.md, 3, Documents).",
        "nothing: the share makes ~/Estia-KB",
    ),
    "calls": Extra(
        "Voice calls with her, and wake-up calls (needs voice)",
        "the calls, through Signal (end-to-end encrypted); the build's code comes from GitHub and crates.io",
        "Needs voice first. Calls need no GitHub account: every download is public. Run\n"
        "  tools/call-tunnel/install-signal-cli-jvm.sh and tools/call-tunnel/build.sh (several GB, a long build);\n"
        "  in her own macOS account then /usr/bin/sudo /usr/local/estia/bin/estia-deploy (it copies the builds into her\n"
        "  release; [calls] before it stops her texts too); then add [calls] (setup_assistant.md, 3, Calls).",
        "voice first and several GB of disk for a long build; no GitHub account",
    ),
    "chords": Extra(
        "Chords transposed, moved for a capo, a chord's notes and the key of given chords, worked out on the Mac",
        "nothing (the chords are worked out on the Mac)",
        "Nothing to install: setup adds [chords] to your config now; in her own macOS account it prints the line to add, as\n"
        "  her config is hers alone (GUIDE.md, Transposing chords).",
        "nothing",
    ),
    "google-maps": Extra(
        "Google Maps for places and routes, asked first: ratings, prices, open now, bus and tram lines",
        "the place or route you ask about, to Google; 'near me' is rounded to about 1 km",
        "Run scripts/setup-google-maps.sh (a Google Cloud project with billing; gcloud's own sign-in first); setup\n"
        "  adds [maps] if it is not there (setup_assistant.md, 3, Places and routes).",
        "a Google Cloud project with billing, and a Google sign-in (gcloud)",
    ),
}


def parse_extras(answer: str) -> tuple[list[str], list[str]]:
    """Picked extras (by name or number, in the menu's order; 'all' for every one) and the words that matched none."""
    names = list(EXTRAS)
    words = [w for w in re.split(r"[\s,]+", answer.strip().lower()) if w]
    if words == ["all"]:
        return names, []
    if words == ["none"]:  # Enter's other word, where Enter takes the picks not gone through
        return [], []
    picked: list[str] = []
    unknown: list[str] = []
    for word in words:
        name = names[int(word) - 1] if word.isdigit() and 1 <= int(word) <= len(names) else word
        if name in EXTRAS:
            if name not in picked:
                picked.append(name)
        else:
            unknown.append(word)
    return sorted(picked, key=names.index), unknown


def expanded(typed: str) -> Path | None:
    """A typed folder as an absolute path, ~ expanded; None when ~ and a name ("~dev") name a macOS account this Mac
    does not have (Python cannot expand it: setup ended with a Python error, review setup-projects-tilde-crash)."""
    try:
        return Path(os.path.abspath(Path(typed).expanduser()))
    except RuntimeError:
        return None


def _projects_folder(value: Value, answers: Known, mac: Mac) -> Checked:
    """The projects folder by the share's rules (`projects_refusal`): it is there, it is not your home itself, and no
    link leads to it. Kept as typed when it starts with ~ (her ~ is not yours), else by its absolute path."""
    typed = clean_paste(value if isinstance(value, str) else "", spaces=False)[0] or "~/dev"
    folder = expanded(typed)
    if folder is None:
        return Checked(None, prompts.SETUP_PROJECTS_TILDE.format(folder=typed))
    if folder == Path.home():
        return Checked(None, prompts.SETUP_PROJECTS_HOME)
    if not folder.is_dir():
        return Checked(None, prompts.SETUP_PROJECTS_NO_SUCH.format(folder=folder))
    why = projects_refusal(folder, Path.home())
    if why is not None:
        return Checked(None, f"{why[:1].upper()}{why[1:]}.")
    return Checked(typed if typed.startswith("~") else str(folder))


def _project_folders(answers: Known, mac: Mac) -> Variant:
    """The folders that are there among ~/dev, ~/Developer, ~/Projects and ~/Code, the first one taken by Enter, then
    another folder (typed)."""
    found = [name for name in PROJECT_FOLDERS if (Path.home() / name.removeprefix("~/")).is_dir()]
    first = found[0] if found else PROJECT_FOLDERS[0]
    options = tuple(Option(name, name, tag=RECOMMENDED if name == first else "") for name in found)
    return {
        "options": (*options, Option(OTHER, prompts.SETUP_PROJECTS_ANOTHER)),
        "recommended": first,
        "ask": prompts.SETUP_PROJECTS_PICK.format(folder=first),
        "advice": prompts.SETUP_PROJECTS_ADVICE,
    }


PROJECT_FOLDERS = ("~/dev", "~/Developer", "~/Projects", "~/Code")


def _projects_level(value: Value, answers: Known, mac: Mac) -> Checked:
    """summary, docs or code; shown back with the folder before the move shares it (spec rule 8)."""
    level = clean_paste(value if isinstance(value, str) else "", spaces=True)[0].lower()
    if level not in PROJECT_LEVELS:
        return Checked(None, prompts.SETUP_PROJECTS_BAD_LEVEL.format(answer=level))
    folder = answers.get("projects_folder")
    shown = prompts.SETUP_PROJECTS_CHOSEN.format(folder=folder, level=level) if isinstance(folder, str) else ""
    return Checked(level, shown=shown)


def _said_yes(qid: str) -> Callable[[Known, Mac], bool]:
    return lambda answers, mac: answers.get(qid) is True


def picked(answers: Known) -> tuple[str, ...] | None:
    """The Extra Features picked in the plan (None: not asked, as in a re-run or `--extras`)."""
    value = answers.get("extras")
    return value if isinstance(value, tuple) else None


def extras_left(answers: Known) -> tuple[str, ...]:
    """An earlier run's picks of the Extra Features that setup did not go through (stops-reruns-extras-pick-lost: it
    stopped before them, at the move or at one of them), in the list's order; () when none."""
    kept = answers.get("extras") if answers.kept("extras") else None
    done = getattr(answers, "done", None)  # setup_answers.Answers, or the Wizard's answers so far
    if not isinstance(kept, tuple) or done is None:
        return ()
    return tuple(name for name in FEATURES if name in kept and not done(f"{DONE_EXTRA}{name}"))


def _left_line(left: Sequence[str]) -> str:
    return prompts.SETUP_EXTRAS_LEFT.format(names=", ".join(prompts.SETUP_FEATURE_NAMES[name] for name in left))


def _extras_list_left(answers: Known, mac: Mac) -> Variant:
    """The window's switches after a stop: the picks not gone through on, and named in its lead."""
    left = extras_left(answers)
    if not left:
        return {}
    return {
        "lead": f"{_left_line(left)}\n\n{prompts.SETUP_WINDOW_EXTRAS_LIST}",
        "options": tuple(
            Option(
                name,
                prompts.SETUP_FEATURE_NAMES[name],
                line=prompts.SETUP_FEATURE_LINES[name],
                on=name in left,
                guide=_guide(name),
            )
            for name in FEATURES
        ),
    }


def _wants_projects(answers: Known, mac: Mac) -> bool:
    """Projects picked among the Extra Features, or a yes to the projects question (a config step on its own)."""
    return answers.get("projects") is True or "projects" in (picked(answers) or ())


def _first_install(answers: Known, mac: Mac) -> bool:
    return not mac.configured


def _features(value: Value, answers: Known, mac: Mac) -> Checked:
    """The Extra Features picked: the ids (gum, the window) or a plain line's names and numbers, in the list's order;
    none for none. A word that names none is refused in one sentence."""
    if isinstance(value, tuple):
        unknown = [name for name in value if name not in FEATURES]
        if unknown:  # never a later step's KeyError: a hand edit, or a newer run's file
            shown = ", ".join(name if name.isprintable() else repr(name) for name in unknown)
            return Checked(None, prompts.SETUP_FEATURES_UNKNOWN.format(words=shown))
        chosen = [name for name in FEATURES if name in value]
    else:
        words = [
            w
            for w in re.split(r"[\s,]+", clean_paste(value if isinstance(value, str) else "", spaces=False)[0].lower())
            if w
        ]
        if words in (["none"], ["no"]):
            words = []
        unknown = [w for w in words if _feature(w) is None]
        if unknown:
            return Checked(None, prompts.SETUP_FEATURES_UNKNOWN.format(words=", ".join(unknown)))
        named = {_feature(w) for w in words}
        chosen = [name for name in FEATURES if name in named]
    names = ", ".join(prompts.SETUP_FEATURE_NAMES[name] for name in chosen)
    shown = prompts.SETUP_FEATURES_CHOSEN.format(names=names) if chosen else prompts.SETUP_FEATURES_NONE
    return Checked(tuple(chosen), shown=shown)


def _feature(word: str) -> str | None:
    if word.isdigit() and 1 <= int(word) <= len(FEATURES):
        return FEATURES[int(word) - 1]
    name = _FEATURE_WORDS.get(word, word)
    return name if name in FEATURES else None


def _calls(value: Value, answers: Known, mac: Mac) -> Checked:
    """Set up calls (yes, Enter) or not now (no)."""
    answer = clean_paste(value if isinstance(value, str) else "", spaces=False)[0].lower()
    if answer in ("yes", "y", "1", "set up calls"):
        return Checked("yes")
    if answer in ("no", "n", "2", "not now"):
        return Checked("no", shown=prompts.SETUP_CALLS_NOT_NOW)
    return Checked(None, prompts.SETUP_CALLS_UNKNOWN.format(answer=answer))


def ollama_answers(key: str) -> tuple[int, str]:
    """One test search with a new Ollama key, to ollama.com only (web_search.OLLAMA_URL): its HTTP status, or 0 and
    why when no answer came. The key goes in the request's header, never in a log."""
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {key}"}
    body = json.dumps({"query": TEST_SEARCH, "max_results": 1}).encode()
    try:
        answer = asyncio.run(mail_http.http("POST", web_search.OLLAMA_URL, headers, body, socket_timeout=15.0))
    except (OSError, TimeoutError, ValueError):
        return 0, "no connection"
    return answer.status, ""


def _ollama_key(value: Value, answers: Known, mac: Mac) -> Checked:
    """A pasted Ollama key, cleaned (spaces and hidden characters out); Enter leaves it for later. One test search:
    refused, it is asked again; no answer keeps it (doctor checks it later)."""
    key = clean_paste(value if isinstance(value, str) else "", spaces=True)[0]
    if not key:
        return Checked("")
    status, why = ollama_answers(key)
    if status in REFUSED:
        return Checked(None, prompts.SETUP_OLLAMA_REFUSED)
    if status == 200:
        return Checked(key, shown=prompts.SETUP_OLLAMA_TESTED)
    return Checked(key, shown=prompts.SETUP_OLLAMA_NO_ANSWER.format(why=why or f"HTTP {status}"))


def _literal(text: str) -> Callable[[re.Match[str]], str]:
    """A replacement for re.sub taken as it is (a backslash in it stays a backslash)."""
    return lambda _match: text


def _picked_at_first_install(name: str) -> Callable[[Known, Mac], bool]:
    """An ID's question: asked in a first install's plan, only when its Extra Feature is picked."""
    return lambda answers, mac: not mac.configured and name in (picked(answers) or ())


SPOTIFY_ID = re.compile(r"[0-9a-f]{32}")  # config's own rule for [spotify] client_id
MAIL_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")  # config's own rule for a [[mail.accounts]] address


def _spotify_id(value: Value, answers: Known, mac: Mac) -> Checked:
    """Spotify's Client ID as its app's Settings show it (32 of 0 to 9 and a to f, lowercased); Enter: later."""
    typed = clean_paste(value if isinstance(value, str) else "", spaces=True)[0].lower()
    if not typed:
        return Checked("")
    if not SPOTIFY_ID.fullmatch(typed):
        return Checked(None, prompts.SETUP_SPOTIFY_ID_BAD)
    return Checked(typed, shown=prompts.SETUP_SPOTIFY_ID_SHOWN)


def _tapo_email(value: Value, answers: Known, mac: Mac) -> Checked:
    """The TP-Link account's email, as set-tapo-credentials.sh takes it (any one line); empty: later."""
    return Checked(clean_paste(value if isinstance(value, str) else "", spaces=True)[0])


def _tapo_password(value: Value, answers: Known, mac: Mac) -> Checked:
    """The TP-Link account's password, its spaces at either end left out (as the script's `read` does); empty:
    later. Never shown back."""
    return Checked(value.strip() if isinstance(value, str) else "")


def _spotify_paste(value: Value, answers: Known, mac: Mac) -> Checked:
    """The address the browser could not open, as pasted (the sign-in checks it); empty: later."""
    return Checked(clean_paste(value if isinstance(value, str) else "", spaces=True)[0])


def _calendar_url(value: Value, answers: Known, mac: Mac) -> Checked:
    """A Google calendar's iCal address, as set-calendar-url.sh takes it (secretfile.ICAL); empty: later."""
    typed = clean_paste(value if isinstance(value, str) else "", spaces=True)[0]
    if typed and not secretfile.ICAL.match(typed):
        return Checked(None, prompts.SETUP_CALENDAR_BAD)
    return Checked(typed)


def _mail_ids(value: Value, answers: Known, mac: Mac) -> Checked:
    """The app registration's two IDs (UUIDs, lowercased) and the mailbox's address; all three, or none for later."""
    given = value if isinstance(value, Mapping) else {}
    typed = {
        key: clean_paste(str(given.get(key, "") or ""), spaces=True)[0]
        for key, _label, _example in prompts.SETUP_MAIL_FIELDS
    }
    if not any(typed.values()):
        return Checked({})
    if not all(typed.values()):
        return Checked(None, prompts.SETUP_MAIL_ALL)
    ids: dict[str, str] = {}
    for key, label, _example in prompts.SETUP_MAIL_FIELDS[:2]:
        try:
            ids[key] = str(uuid.UUID(typed[key]))
        except ValueError:
            return Checked(None, prompts.SETUP_MAIL_ID_BAD.format(label=label))
    address = typed["address"].lower()
    if not MAIL_ADDRESS.fullmatch(address):
        return Checked(None, prompts.SETUP_MAIL_ADDRESS_BAD)
    return Checked({**ids, "address": address}, shown=prompts.SETUP_MAIL_SHOWN.format(address=address))


def _brave_key(value: Value, answers: Known, mac: Mac) -> Checked:
    """A pasted Brave key, cleaned; Enter: later (Brave is the backup). No test search: each one counts at Brave."""
    return Checked(clean_paste(value if isinstance(value, str) else "", spaces=True)[0])


def found_home(area: str) -> tuple[bool | None, str]:
    """One lookup at OpenStreetMap (Nominatim) with the street and the city only: (True, the place it matched, by its
    own name, or the words when it gives none), (False, "") when nothing is found there, (None, "") when it did not
    answer."""
    finder = osm.OpenStreetMap(mail_http.http, tz=ZoneInfo("UTC"))
    try:
        name = asyncio.run(finder.named(area))
    except (MapsError, OSError, TimeoutError):
        return None, ""
    return (False, "") if name is None else (True, name or area)


def _places_picked(answers: Known, mac: Mac) -> bool:
    """A first install with places and routes (or Google Maps) picked: a route from home needs the home area."""
    return not mac.configured and bool({"maps", "google-maps"} & set(picked(answers) or ()))


def _home_area(value: Value, answers: Known, mac: Mac) -> Checked:
    """The street and the city, each cleaned of what a paste brings; Enter on both: none. A house number is advice,
    never an error, and never goes to the map. One lookup (spec: "Found: …"); not found is asked again, no answer
    keeps it as typed. An area kept from an earlier run was found then: never looked up again."""
    if value == HANDED:  # written beside her config already: the answers file keeps only this mark
        return Checked(HANDED)
    given = value if isinstance(value, Mapping) else {}
    street, city = (clean_paste(str(given.get(key, "")), spaces=False)[0] for key in ("street", "city"))
    if not street and not city:
        return Checked({})
    if not street or not city:
        return Checked(None, prompts.SETUP_HOME_AREA_ONE)
    for text, label in ((street, "Street"), (city, "City")):
        if not text.isprintable():  # a control character (a terminal's escape) would reach the screen: "Kept: …"
            return Checked(None, prompts.SETUP_ABOUT_YOU_HIDDEN.format(label=label))
    area = f"{street}, {city}"
    if has_link(area):
        return Checked(None, prompts.SETUP_ABOUT_YOU_LINK)
    if web_search.has_private_details(area) or len(area) > WORDS_CHARS:
        return Checked(None, prompts.SETUP_HOME_AREA_PRIVATE)
    kept = {"street": street, "city": city}
    advice = prompts.SETUP_HOME_AREA_HOUSE_NUMBER if osm.has_house_number(area) else ""
    if answers.get("home_area") == kept:
        return Checked(kept, shown=advice)
    words = osm.without_house_number(area)
    found, matched = found_home(words)
    if found is False:
        return Checked(None, prompts.SETUP_HOME_AREA_NOT_FOUND.format(area=words))
    if found:  # the place it matched, by its own name: "Found: …. Right?" (home_found) asks next
        return Checked(kept, shown=advice, confirm=matched)
    no_answer = prompts.SETUP_HOME_AREA_NO_ANSWER.format(area=words)
    return Checked(kept, shown="\n".join(filter(None, (advice, no_answer))))


# a profile fact's length (profile.check_fact), which her first start checks on each line as it stores it
FACT_CHARS = 300


def _fact_room(key: str) -> int:
    """How long an About you line may be: a fact's 300 characters, less what her first start puts in front of it
    (setup_handover.fact_texts: its category and words, "About: Usual day: ")."""
    return FACT_CHARS - (len(setup_handover.fact_texts({key: "x"}, {})[0]) - 1)


ABOUT_ROOM = {key: _fact_room(key) for key, _label, _example in prompts.ABOUT_YOU_FIELDS}

# check_fact's reasons, in setup's sentences (a field's label first)
_FACT_REASONS = (
    ("too long", prompts.SETUP_ABOUT_YOU_LONG),
    ("contains hidden", prompts.SETUP_ABOUT_YOU_HIDDEN),
    ("looks like an instruction", prompts.SETUP_ABOUT_YOU_INSTRUCTION),
    ("looks like a password", prompts.SETUP_ABOUT_YOU_SECRET),
    ("looks like a bank account", prompts.SETUP_ABOUT_YOU_ACCOUNT),
    ("link type", prompts.SETUP_ABOUT_YOU_LINK),
)


def _about_you(value: Value, answers: Known, mac: Mac) -> Checked:
    """The five lines, each optional, each through the profile's own check (as a "remember that …" fact's: at most 300
    characters, hidden characters out), setup's link check, as the profile's lets links through (owner OK, 26 Oct),
    and no phone number or email address (as the home area: the safer option, 29 Oct). The first line that does not
    fit is refused in one sentence; the filled ones are shown back."""
    if value == HANDED:  # written beside her config already: the answers file keeps only this mark
        return Checked(HANDED)
    given = value if isinstance(value, Mapping) else {}
    kept: dict[str, str] = {}
    for key, label, _example in prompts.ABOUT_YOU_FIELDS:
        text = clean_paste(str(given.get(key, "")), spaces=False)[0]
        if text and has_link(text):
            return Checked(None, prompts.SETUP_ABOUT_YOU_LINK)
        room = ABOUT_ROOM[key]
        # checked as her first start stores it: its category and words in front (review PROM-09)
        stored = setup_handover.fact_texts({key: text}, {})
        why = None if not text else "too long" if len(text) > room else check_fact(stored[0]) if stored else None
        if why is not None:
            sentence = next((words for start, words in _FACT_REASONS if why.startswith(start)), "{label}: {why}.")
            return Checked(None, sentence.format(label=label, count=len(text), limit=room, why=why))
        if text and web_search.has_private_details(text):  # as the home area (answers-extras-about-you-phone)
            return Checked(None, prompts.SETUP_ABOUT_YOU_PRIVATE.format(label=label))
        kept[key] = text
    filled = [f"  {label}: {kept[key]}" for key, label, _example in prompts.ABOUT_YOU_FIELDS if kept[key]]
    shown = "\n".join([prompts.SETUP_ABOUT_YOU_SHOWN, *filled]) if filled else prompts.SETUP_ABOUT_YOU_SKIP
    return Checked(kept, shown=shown)


def _guide(name: str) -> Guide | None:
    """An extra's guide, shown only when its switch is on (none for one with nothing to set up)."""
    if name not in prompts.SETUP_GUIDES:
        return None
    needs, steps = prompts.SETUP_GUIDES[name]
    return Guide(
        needs,
        steps,
        check=prompts.SETUP_EXTRA_CHECKS.get(name, ""),
        errors=prompts.SETUP_EXTRA_ERRORS.get(name, ()),
        links=prompts.SETUP_EXTRA_LINKS.get(name, ()),
    )


def _features_list() -> str:
    """The Extra Features as plain lines show them: a number, the name to type and what it is; the line under it (the
    recommended ones are named on the line before the question)."""
    lines = []
    for number, name in enumerate(FEATURES, 1):
        lines += [
            f"  {number:>2}. {name:12} {prompts.SETUP_FEATURE_NAMES[name]}",
            f"{'':19}{prompts.SETUP_FEATURE_LINES[name]}",
        ]
    return "\n".join(lines)


# the extras' questions: calls first, then the Extra Features (switches, each guide only when its switch is on), the
# projects folder and level when projects are picked; asked in the plan of a first install. The projects question is
# the config step's own, for a config step run on its own (no plan).
QUESTIONS: tuple[Question, ...] = (
    Question(
        "calls",
        "cards",
        "Calls with {name}",
        lead=prompts.SETUP_CALLS,
        ask=prompts.SETUP_CALLS_ASK,
        options=(
            Option(
                "yes",
                "Set up calls",
                line=prompts.SETUP_CALLS_CARD,
                tag=RECOMMENDED,
                guide=Guide(prompts.SETUP_CALLS_COSTS, prompts.SETUP_CALLS_STEPS),
                short="yes",
            ),
            Option("no", "Not now"),
        ),
        recommended="yes",  # owner: "calls is the biggest feature"
        check=_calls,
        shows=_first_install,
    ),
    Question(
        "extras",
        "switches",
        "Extra Features",
        lead=prompts.SETUP_FEATURES,
        listing=_features_list(),
        ask=prompts.SETUP_FEATURES_ASK,
        options=tuple(
            Option(
                name,
                prompts.SETUP_FEATURE_NAMES[name],
                line=prompts.SETUP_FEATURE_LINES[name],
                tag=RECOMMENDED if name in FEATURES_ON else "",
                on=name in FEATURES_ON,
                guide=_guide(name),
            )
            for name in FEATURES
        ),
        recommended=" ".join(FEATURES_ON),
        check=_features,
        dry=prompts.SETUP_FEATURES_DRY_RUN,
        shows=_first_install,
    ),
    Question(  # the config step's own, without the plan's pick: asked each time, as Terminal asks it
        "projects",
        "yes_no",
        "Projects",
        lead=prompts.SETUP_PROJECTS,
        ask=prompts.SETUP_PROJECTS_ASK,
        recommended="no",
        check=yes_no,  # Enter is a no: projects stay off
        keep="step",
        plan=False,
    ),
    Question(
        "projects_folder",
        "folder",
        "Projects folder",
        ask=prompts.SETUP_PROJECTS_PICK.format(folder="~/dev"),
        recommended="~/dev",
        advice=prompts.SETUP_PROJECTS_ADVICE,
        check=_projects_folder,
        vary=_project_folders,
        shows=_wants_projects,
    ),
    Question(
        "projects_level",
        "choice",
        "Projects level",
        listing=prompts.SETUP_PROJECTS_LEVELS,
        ask=prompts.SETUP_PROJECTS_LEVEL,
        options=tuple(
            Option(
                level,
                level,
                line=prompts.SETUP_PROJECTS_LEVEL_LINES[level],
                tag=RECOMMENDED if level == "summary" else "",
            )
            for level in ("summary", "docs", "code")
        ),
        recommended="summary",
        check=_projects_level,
        shows=_wants_projects,
    ),
    Question(  # Spotify's Client ID, for [spotify]: its steps on its own screen (owner, 27 Oct)
        "spotify_id",
        "text",
        "Spotify's Client ID",
        lead=prompts.SETUP_SPOTIFY_ID,
        window_lead=prompts.SETUP_WINDOW_SPOTIFY_ID,
        links=prompts.SETUP_EXTRA_LINKS["music"],
        ask=prompts.SETUP_SPOTIFY_ID_ASK,
        check=_spotify_id,
        shows=_picked_at_first_install("music"),
        later=prompts.SETUP_WINDOW_LATER,
    ),
    Question(  # mail's app registration and mailbox, for [mail] and [[mail.accounts]]
        "mail_ids",
        "fields",
        "Mail at Microsoft",
        lead=prompts.SETUP_MAIL_IDS,
        window_lead=prompts.SETUP_WINDOW_MAIL_IDS,
        links=prompts.SETUP_EXTRA_LINKS["mail"],
        fields=tuple(
            Field(key, label, placeholder=example, max_chars=100) for key, label, example in prompts.SETUP_MAIL_FIELDS
        ),
        check=_mail_ids,
        shows=_picked_at_first_install("mail"),
        later=prompts.SETUP_WINDOW_LATER,
    ),
    Question(
        "home_area",
        "fields",
        "Home area",
        lead=prompts.SETUP_HOME_AREA,
        window_lead=prompts.SETUP_WINDOW_HOME_AREA,  # no Enter in the window
        fields=(
            Field("street", "Street", placeholder=prompts.SETUP_HOME_AREA_EXAMPLES[0], max_chars=100),
            Field("city", "City", placeholder=prompts.SETUP_HOME_AREA_EXAMPLES[1], max_chars=100),
        ),
        check=_home_area,
        shows=_places_picked,
        confirm="home_found",
    ),
    Question(  # the place OpenStreetMap matched for the home area, by its own name (spec: "Found: Ermou, Athens. Right?")
        "home_found",
        "yes_no",
        "Home area",
        lead=prompts.SETUP_HOME_FOUND,
        ask=prompts.SETUP_HOME_RIGHT_ASK,
        recommended="yes",  # "yes, when it is your area": the window's main button (walk-through, 27 Oct)
        advice=prompts.SETUP_HOME_RIGHT_ADVICE,
        check=yes_no,
        keep="step",
        plan=False,
    ),
    Question(
        "about_you",
        "fields",
        "About you",
        lead=prompts.SETUP_ABOUT_YOU,
        help=prompts.SETUP_ABOUT_YOU_SKIP,
        listing=prompts.SETUP_ABOUT_YOU_LIST,
        fields=tuple(
            Field(key, label, placeholder=example, max_chars=ABOUT_ROOM[key])
            for key, label, example in prompts.ABOUT_YOU_FIELDS
        ),
        check=_about_you,
        shows=_first_install,
    ),
    Question(
        "ollama_key",
        "hidden",
        "Ollama's key",
        lead=prompts.SETUP_OLLAMA_KEY,
        window_lead=prompts.SETUP_WINDOW_OLLAMA_KEY,
        links=(("ollama.com/settings/keys", "https://ollama.com/settings/keys"),),
        ask=prompts.SETUP_OLLAMA_KEY_ASK,
        check=_ollama_key,
        keep="never",
        plan=False,
        later=prompts.SETUP_WINDOW_LATER,  # as Enter leaves it for later in Terminal (walk-through, 27 Oct)
        live=False,  # its check is the one test search at ollama.com: once the key is given, never while it is typed
    ),
    Question(
        "brave_key",
        "hidden",
        "Brave's key",
        lead=prompts.SETUP_BRAVE_KEY,
        window_lead=prompts.SETUP_WINDOW_BRAVE_KEY,
        links=(("api-dashboard.search.brave.com/register", "https://api-dashboard.search.brave.com/register"),),
        ask=prompts.SETUP_BRAVE_KEY_ASK,
        check=_brave_key,
        keep="never",
        plan=False,
        later=prompts.SETUP_WINDOW_LATER,
    ),
    Question(  # a shared service of the login keychain ("ollama", "brave-search"): never replaced without a yes
        "keychain_replace",
        "yes_no",
        "Your login keychain",
        lead=prompts.SETUP_KEY_REPLACE,
        ask=prompts.SETUP_KEY_REPLACE_ASK,
        check=yes_no,  # Enter is a no: the key already there stays
        keep="step",
        plan=False,
    ),
    # the setup window's screens for what scripts/set-tapo-credentials.sh and scripts/set-calendar-url.sh read on a
    # terminal (Terminal runs the scripts, which ask there): asked each time, the secrets never kept
    Question(
        "tapo_email",
        "text",
        prompts.SETUP_TAPO_TITLE,
        lead=prompts.SETUP_TAPO_LEAD,
        ask=prompts.SETUP_TAPO_EMAIL_ASK,
        check=_tapo_email,
        keep="step",
        plan=False,
        later=prompts.SETUP_WINDOW_LATER,
    ),
    Question(
        "tapo_password",
        "hidden",
        prompts.SETUP_TAPO_TITLE,
        lead=prompts.SETUP_TAPO_PASSWORD_LEAD,
        ask=prompts.SETUP_TAPO_PASSWORD_ASK,
        check=_tapo_password,
        keep="never",
        plan=False,
        later=prompts.SETUP_WINDOW_LATER,
    ),
    Question(  # a re-run without the plan's pick (Terminal: the numbered list and "Which ones?"): none on, or the
        # earlier run's picks that setup did not go through
        "extras_list",
        "switches",
        "Extra Features",
        lead=prompts.SETUP_WINDOW_EXTRAS_LIST,
        ask=prompts.SETUP_WINDOW_EXTRAS_ASK,
        options=tuple(
            Option(
                name,
                prompts.SETUP_FEATURE_NAMES[name],
                line=prompts.SETUP_FEATURE_LINES[name],
                guide=_guide(name),
            )
            for name in FEATURES
        ),
        check=_features,
        keep="step",
        plan=False,
        vary=_extras_list_left,
    ),
    Question(  # spotify-login.sh's paste: its own lines (the link, opened in the browser on a click) and its question
        "spotify_paste",
        "hidden",
        prompts.SETUP_SPOTIFY_PASTE_TITLE,
        lead="{steps}",
        ask="{ask}",
        check=_spotify_paste,
        keep="never",
        plan=False,
        later=prompts.SETUP_WINDOW_LATER,
    ),
    Question(
        "calendar_url",
        "hidden",
        prompts.SETUP_CALENDAR_TITLE,
        lead=prompts.SETUP_CALENDAR_LEAD,
        ask=prompts.SETUP_CALENDAR_ASK,
        check=_calendar_url,
        keep="never",
        plan=False,
        later=prompts.SETUP_WINDOW_LATER,
    ),
)


class ExtrasSteps(WizardBase):
    """Calls, then the Extra Features, one at a time, each with its guide; projects, chords, places and routes, web
    search and documents switched on here."""

    planned: dict[str, Value]  # this run's plan (setup.Wizard keeps it)
    sections_written: frozenset[str] = frozenset()  # the sections the config step of this run wrote
    projects_planned = False  # the config step of this run took the projects folder and level from the plan
    restart_due = False  # her config was opened as her: she restarts once, at the end (`restart_her`)
    done_after_restart: tuple[str, ...] = ()  # steps done once she restarted (Answers.mark_done: a re-run skips them)
    for_terminal: tuple[str, ...] = ()  # what the window names for Terminal after setup: in its closing list too
    calls_not_on = ""  # the calls step's own no in the window (her update not deployed, admin rights not given)
    check_failed = False  # her check found a ✗ (stops-reruns-check-fail-done): the closing list says so
    check_not_run = False  # admin rights not given at her check: the closing list says it did not run

    def her_config(self, lines: str, change: Sequence[str] = ()) -> bool:
        """Her config is hers alone (setup never writes it): the lines to add, then her config opened as her on
        Enter (owner, 27 Oct: no command typed), and her restart once, at the end. In the window (no nano there,
        owner 29 Oct) the lines of `change` on a screen of their own, then on Add them her own release's herconfig adds
        them as her, a copy of the old one kept (`herconfig`); a change it cannot make, or Later, leaves the command
        for Terminal. A window closed on that screen leaves Terminal's way (review of 30 Oct, P03: Terminal has no
        sudo ticket for `sudo -n -u`). True when her config was opened (or changed) as her."""
        if self.in_window:
            return self._her_config_in_window(lines, change)
        self.say(lines)
        opened = self.offer([SUDO, str(ownuser.BIN / "estia"), "config"])
        if opened:
            self.restart_due = True
        return opened

    def _her_config_in_window(self, lines: str, change: Sequence[str]) -> bool:
        if not change:  # sections that need more than a line: their steps by hand, said above
            self.say(prompts.SETUP_WINDOW_HER_CONFIG_LATER)
            return False
        shown = "\n".join(f"• {line}" for line in herconfig.lines(change).splitlines())
        added = self.answer("her_config_add", lines=shown)
        if added is True and not self.in_window:  # closed on its screen, yes in Terminal: Terminal's lines and offer
            return self.her_config(lines, change)
        if added is not True:
            self._her_config_later(change, prompts.SETUP_WINDOW_HER_CONFIG_LATER)
            return False
        env = ["/usr/bin/env", "-i", f"HOME={ownuser.HOME}", f"PATH={ownuser.HER_PATH}", "LANG=en_US.UTF-8"]
        python = str(ownuser.CURRENT / ".venv" / "bin" / "python")
        argv = [SUDO, "-n", "-u", ownuser.ROLE_USER, "-H", "--", *env, python, "-I", "-m", "local_assistant.herconfig"]
        done = self.run([*argv, "apply", *change], why=prompts.ADMIN_WHY_HER, capture_output=True)
        said = [line for line in str(done.stdout or "").splitlines() if line.strip()]
        if done.returncode == 0:  # changed, or the lines there already
            self.say(said[-1] if said else prompts.SETUP_WINDOW_HER_CONFIG_DONE)
            self.restart_due = True
            return True
        if any("No module named" in line for line in said):  # a release from before herconfig
            self._her_config_later(change, prompts.SETUP_WINDOW_HER_CONFIG_OLD)
        else:
            if said:  # herconfig's own why (it would not load, a change it does not make)
                self.say(said[-1])
            self._her_config_later(change, prompts.SETUP_WINDOW_HER_CONFIG_LATER)
        return False

    def _her_config_later(self, change: Sequence[str], command: str) -> None:
        """Her config's lines left for Terminal: said in the log right before the command (the screen that showed them
        is gone), and kept for the closing list (review FRIEND-7)."""
        added = herconfig.lines(change)
        self.say(added)
        self.say(command)
        self.for_terminal = (*self.for_terminal, f"{added}\n{command}")

    def restart_her(self) -> None:
        """Once, before the check, when her config was opened as her: her restart, on Enter (the window: Run it)."""
        if self.restart_due and not self.dry_run:
            self.restart_due = False
            self.say(prompts.SETUP_WINDOW_HER_RESTART if self.in_window else prompts.SETUP_HER_RESTART)
            restart = [SUDO, str(ownuser.BIN / "estia"), "restart"]
            if self.offer(restart, why=prompts.ADMIN_WHY_HER, what=prompts.SETUP_WINDOW_HER_RESTART):
                for step in self.done_after_restart:
                    self.answers.mark_done(step)
            self.done_after_restart = ()

    def given(self, qid: str) -> Value | None:
        """An answer of this run's plan, or one kept from an earlier run; None when neither has it (nothing asked)."""
        if qid in self.planned:
            return self.planned[qid]
        return self.answers.get(qid) if self.answers.kept(qid) else None

    def picked(self) -> tuple[str, ...] | None:
        """The Extra Features this run's plan picked (None: the plan did not ask them, as in a re-run or `--extras`,
        which show the list). Never an earlier run's pick alone: a re-run walked it again, asked the web keys again
        and named the sections to add again (review setup-rerun-extras-rewalk)."""
        value = self.planned.get("extras")
        return value if isinstance(value, tuple) else None

    # the steps run_setup calls in this order around the core's (setup.py, `_run`)
    def extra_sections(self, text: str) -> str:
        """The config's text as the config step wrote it, with the sections of the picked Extra Features that need
        nothing from you: [web] (with the Mac's country for Brave), [maps] (also for Google Maps), [chords] and
        [documents] (your ~/Estia-KB, written in full). Called on a first install, before the move takes the config to
        her, so no line is copied by hand. A text that would not load stays as it was."""
        chosen = self.picked() or ()
        sections = sorted(
            {SECTIONS[name] for name in chosen if name in SECTIONS}
            | ({"spotify"} if "music" in chosen and self.given("spotify_id") else set())
            | ({"mail"} if "mail" in chosen and self.given("mail_ids") else set())
        )
        new = text
        for section in sections:
            new = self._with_extra(new, section)
        if sections and _config_problem(new):
            return text
        self.sections_written = frozenset(sections)
        return new

    def _with_extra(self, text: str, section: str) -> str:
        from local_assistant.setup import with_section  # setup.py's (it imports this module)

        new = with_section(text, section)
        if section == "web" and re.fullmatch(r"[A-Z]{2}", self.mac.country):
            line = f'country = "{self.mac.country}"'
            new = _WEB_COUNTRY.sub(lambda match: line + (match.group(1) or ""), new, count=1)
        if section == "spotify":
            line = f'client_id = "{self.given("spotify_id")}"'
            if _SPOTIFY_CLIENT.search(new):
                new = _SPOTIFY_CLIENT.sub(lambda _: line, new, count=1)
            else:
                new = re.sub(r"^\[spotify\]$", lambda _: f"[spotify]\n{line}", new, count=1, flags=re.MULTILINE)
        if section == "mail":
            new = self._with_mail(new)
        if section == "documents":
            line = f"folder = {json.dumps(str(Path.home() / 'Estia-KB'), ensure_ascii=False)}"
            if _DOCUMENTS_FOLDER.search(new):
                new = _DOCUMENTS_FOLDER.sub(lambda match: line + (match.group(1) or ""), new, count=1)
            else:
                new = re.sub(r"^\[documents\]$", lambda _: f"[documents]\n{line}", new, count=1, flags=re.MULTILINE)
        return new

    def _with_mail(self, text: str) -> str:
        """[mail] with the plan's two IDs, where the example's lines stand, and its one mailbox, named "mail"."""
        given = self.given("mail_ids")
        ids = dict(given) if isinstance(given, Mapping) else {}
        new = text
        for key, field in (("tenant_id", "tenant"), ("client_id", "client")):
            line = f'{key} = "{ids.get(field, "")}"'
            pattern = re.compile(_MAIL_ID.format(key=key), re.MULTILINE)
            if pattern.search(new):
                new = pattern.sub(_literal(line), new, count=1)
            else:
                new = re.sub(r"^\[mail\]$", _literal(f"[mail]\n{line}"), new, count=1, flags=re.MULTILINE)
        account = f'[[mail.accounts]]\nname = "mail"\naddress = {json.dumps(ids.get("address", ""))}'
        if _MAIL_ACCOUNT.search(new):
            return _MAIL_ACCOUNT.sub(lambda _: account, new, count=1)
        return f"{new.rstrip(chr(10))}\n\n{account}\n"

    def hand_over(self) -> None:
        """Right after the config on a first install, before the move takes the config folder to her: About you and
        the home area, beside her config (setup_handover, 0600), for her first start to read once as her. Nothing to
        hand over: no file. A dry run says what it would write."""
        about, home = self.given("about_you"), self.given("home_area")
        about_you = dict(about) if isinstance(about, Mapping) else {}
        home_area = dict(home) if isinstance(home, Mapping) else {}
        if not setup_handover.fact_texts(about_you, home_area):
            return
        path = self.config_path.parent / setup_handover.HANDOVER
        if self.dry_run:
            self.say(prompts.SETUP_HANDED_OVER_DRY_RUN.format(path=path))
            return
        private_folders(self.config_path.parent)
        setup_handover.write(self.config_path.parent, about_you, home_area)
        self.answers.mark_handed()  # their words now live beside her config only
        self.say(prompts.SETUP_HANDED_OVER.format(path=path))

    def calls_step(self) -> None:
        """After the security step, on the plan's yes: the call tools from Homebrew, her voice service, the JVM
        signal-cli and the call helper, each command printed first (Wizard.run); in her own macOS account then the
        deploy that copies the builds into her release, and the lines that turn calls on. Her voice service runs only
        in her own macOS account: before the move, one line says to run setup again there."""
        if self.given("calls") != "yes":
            return
        mine = self.own_user()
        if not mine and not self.dry_run:
            self.say(prompts.SETUP_CALLS_AFTER_MOVE)
            return
        if not self.dry_run and _release_has_calls() and _voice_installed():
            if self.answers.done("calls"):  # a finished install: her lines and her restart after them (29 Oct)
                self.say(prompts.SETUP_CALLS_DONE)
                return
            # her config's lines again: a Ctrl-C during the final deploy lets it finish, then stops setup before them
            # (final review of 0.9.8, host-calls-rerun-lines-lost); "unless it is there already" covers a second time
            self.say(prompts.SETUP_CALLS_BUILT)
            self._calls_lines()
            return
        for command, _admin, done in self._calls_commands(mine):
            if not self.dry_run and done():  # a re-run: what an earlier run built stays (review host-calls-rerun)
                continue
            code = self._call_command(command)
            if code == VOICE_STILL_LOADING and Path(command[0]).name == "install-voice-daemon.sh":
                self.say(prompts.SETUP_CALLS_VOICE_LOADING)  # no fault (review host-voice-wait)
                continue
            if code != 0:
                self.say(self.calls_not_on or prompts.SETUP_CALLS_STOPPED.format(command=_shown_command(command)))
                return
        if not self.dry_run:
            self.say(prompts.SETUP_CALLS_BUILT)
        if mine and not self.dry_run:
            self._calls_lines()
        elif self.dry_run:
            self.say(prompts.SETUP_CALLS_HERS.format(save=prompts.NANO_SAVE))
        else:
            self.say(prompts.SETUP_CALLS_YOURS.format(path=self.config_path))

    def _calls_lines(self) -> None:
        """Her config's calls lines; once they are in and she restarted, calls are done (a re-run says so)."""
        if self.her_config(prompts.SETUP_CALLS_HERS.format(save=prompts.NANO_SAVE), ("calls",)):
            self.done_after_restart = (*self.done_after_restart, "calls")

    def _call_command(self, command: list[str]) -> int:
        """One of the calls step's commands, its exit code: in the window, her voice's install and the deploy run
        their steps there (`_voice_in_window`, `_deploy_in_window`); every other one, and every one in Terminal, as
        it always did."""
        if self.in_window and not self.dry_run:
            if Path(command[0]).name == "install-voice-daemon.sh":
                return self._voice_in_window()
            if command == [SUDO, str(ownuser.BIN / "estia-deploy")]:
                return self._deploy_in_window([*command, "--ask-lines"])
        return self.run(command).returncode

    def _voice_in_window(self) -> int:
        """scripts/install-voice-daemon.sh's steps in the window, with its exit code: its warnings for macOS, the
        deploy with her voice (`--ask-lines`: its review question a screen), then the wait for her voice as her, one
        admin command (ownuser.VOICE_WAIT_AS_HER), read as the script reads it: ready 0, failed 1, still loading
        VOICE_STILL_LOADING, not read 1."""
        if re.fullmatch(r"27\.0(\..*)?", _macos_version()):
            self.say(prompts.SETUP_VOICE_WARN_27)
        if self._deploy_in_window([SUDO, str(ownuser.BIN / "estia-deploy"), "--ask-lines", "--voice", str(REPO)]):
            return 1
        self.say(prompts.SETUP_VOICE_MODELS)
        env = ["/usr/bin/env", "-i", f"HOME={ownuser.HOME}", "PATH=/usr/bin:/bin"]
        wait = [SUDO, "-u", ownuser.ROLE_USER, "-H", "--", *env, "/bin/bash", "-c", ownuser.VOICE_WAIT_AS_HER]
        done = self.run(wait, capture_output=True, why=prompts.ADMIN_WHY_CALLS)
        if done.returncode != 0:
            self.say(prompts.SETUP_VOICE_NOT_READ)
            if done.stderr == setup_admin.REFUSED:  # nothing failed: the friend's own Not now (review W04)
                self.calls_not_on = prompts.SETUP_CALLS_REFUSED
            return 1
        state = "".join(char if char.isprintable() else "?" for char in str(done.stdout or "").strip())
        if '"stt":"ready"' in state and '"tts":"ready"' in state:
            self.say(prompts.SETUP_VOICE_READY)
            return 0
        if "failed" in state:
            self.say(prompts.SETUP_VOICE_FAILED.format(state=state))
            return 1
        self.say(prompts.SETUP_VOICE_STILL_LOADING.format(state=state or "no answer"))
        return VOICE_STILL_LOADING

    def _deploy_in_window(self, argv: list[str]) -> int:
        """Her release's update in the window (`estia-deploy --ask-lines`), macOS's own box asking for the admin
        rights: what it lists in the window's log, its question a yes or no screen (Deploy it or Not now, the list
        in its More fold), the answer on its stdin. Root-run tools from before the flag name the command for
        Terminal. Its exit code (1 when it could not ask here)."""
        from local_assistant.setup_window import WindowFace  # setup_window reads this module's questions

        face = self.face
        if not isinstance(face, WindowFace):
            return self.run([word for word in argv if word != "--ask-lines"]).returncode

        said_no: list[bool] = []
        waited: list[bool] = []  # a Deploy it after deploy's own no for no answer in time (review W05)

        def answer(question: str, before: list[str]) -> str | None:
            q = Question(
                "deploy_review",
                "yes_no",
                prompts.SETUP_WINDOW_DEPLOY_TITLE,
                lead=prompts.SETUP_WINDOW_DEPLOY_LEAD,
                ask=question,
                # the whole list, one item a line (review W07): deploy caps it far under a line's limit
                more="\n".join(f"• {line.strip()}" for line in before if line.strip()),
                options=(Option("yes", prompts.SETUP_WINDOW_DEPLOY_YES), Option("no", prompts.SETUP_WINDOW_NOT_NOW)),
                keep="step",
                plan=False,
            )
            shown = time.monotonic()
            face.show(q, None)
            yes = face.confirm(q, False) is True
            face.checked(q, Checked(yes))  # the window leaves its screen for the update's log (review W03)
            # deploy takes no answer by ASK_SECONDS as a no and ends: an answer after that sends nothing (review W05)
            late = face.admin_ended() or time.monotonic() - shown >= setup_admin.ASK_SECONDS - setup_admin.ASK_MARGIN
            if not yes:
                said_no.append(True)
                return None if late else "n\n"
            if late:
                waited.append(True)
                return None
            return "y\n"

        self.say("  $ " + " ".join(argv))
        ran = face.admin(argv, why=prompts.ADMIN_WHY_CALLS, answer=answer)
        if ran is None:  # the window closed meanwhile: Terminal asks, as it always did
            return self.run([word for word in argv if word != "--ask-lines"]).returncode
        if ran.code != 0 and "unrecognized arguments: --ask-lines" in ran.output:
            shown = " ".join(word for word in argv if word != "--ask-lines")
            self.say(prompts.SETUP_WINDOW_DEPLOY_OLD.format(command=shown))
            return 1
        if ran.code != 0 and waited:  # nothing failed: her update's question waited too long
            self.calls_not_on = prompts.SETUP_CALLS_WAITED
        elif ran.code != 0 and (ran.refused or said_no):  # nothing failed: the friend's own no
            self.calls_not_on = prompts.SETUP_CALLS_REFUSED if ran.refused else prompts.SETUP_CALLS_NOT_DEPLOYED
        return ran.code

    def _calls_commands(self, mine: bool) -> list[tuple[list[str], bool, Callable[[], bool]]]:
        """The calls step's commands, in order, whether each needs admin rights (sudo in Terminal, macOS's own box in
        the window), and whether its result is there already (asked right before it would run): her voice service
        installed, the Java build of Homebrew's version in the checkout's vendor folder, the call helper built, her
        release holding both. Homebrew's signal-cli is pinned after the Java build (`brew pin`: one already pinned is
        a warning, exit 0)."""
        found = shutil.which("brew")  # Homebrew's, on the PATH scripts/setup.sh gives
        rustup = str(Path(found).parent.parent / "opt/rustup/bin/rustup") if found else "rustup"  # keg-only
        brew = found or "brew"
        version = [] if self.dry_run else self._signal_cli_version()
        wanted = version[0] if version else CALLS_SIGNAL_CLI_VERSION  # the installer's own default
        jvm = VENDOR / f"signal-cli-{wanted}-jvm" / f"signal-cli-{wanted}" / "bin" / "signal-cli"

        def never() -> bool:
            return False

        commands: list[tuple[list[str], bool, Callable[[], bool]]] = [
            ([brew, "install", *CALL_TOOLS], False, never),
            ([rustup, "toolchain", "install", RUST, "--profile", "minimal"], False, _tunnel_built),
            ([str(REPO / "scripts" / "install-voice-daemon.sh")], True, _voice_installed),
            ([str(REPO / "tools" / "call-tunnel" / "install-signal-cli-jvm.sh"), *version], False, jvm.is_file),
            ([brew, "pin", "signal-cli"], False, never),  # an upgrade never moves it from the Java build's version
            ([str(REPO / "tools" / "call-tunnel" / "build.sh")], False, _tunnel_built),
        ]
        if mine or self.dry_run:  # her release gets the builds only with a deploy
            commands.append(([SUDO, str(ownuser.BIN / "estia-deploy")], True, _release_has_calls))
        return commands

    def _signal_cli_version(self) -> list[str]:
        """Homebrew's signal-cli version (the JVM build must be the same: one account data), as the one argument of
        the JVM installer: what the signal-cli on the PATH says (`signal-cli --version`, as scripts/setup.sh reads
        it), the keg Homebrew links. `brew list --versions` names every keg kept, and setup took the first (second
        deep scan, signal-05). None when it does not say (the installer's own default)."""
        try:
            answer = subprocess.run(
                [self.signal_cli, "--version"], capture_output=True, text=True, timeout=30, check=False
            )
        except (OSError, subprocess.SubprocessError):
            return []
        found = re.fullmatch(r"signal-cli (\d+(?:\.\d+){2,3})", answer.stdout.strip())
        return [found.group(1)] if answer.returncode == 0 and found else []

    def extras_setup(self) -> None:
        """After the calls: the extras, one at a time."""
        self.extras()

    def closing(self) -> None:
        """Setup's last words: once her voice service is installed (a dry run: with calls picked), her English voices,
        each one /voices lists on this Mac (vera only beside the 100M model), with /voices, /voice, /tools and /help
        (`setup.voices_line`); before it, what she can do and every command; then the Extra Features picked and when it
        is done. The setup window shows them as its closing list."""
        from local_assistant.setup import voices_line  # setup.py's (it imports this module)

        lines: list[str] = []
        if _voice_installed() or (self.dry_run and self.given("calls") == "yes"):
            lines.append(voices_line(self.mac.memory_gb))
        else:
            lines.append(prompts.SETUP_CLOSING_COMMANDS)
        chosen = self.picked()
        if chosen:
            names = ", ".join(prompts.SETUP_FEATURE_NAMES[name] for name in chosen)
            lines.append(prompts.SETUP_CLOSING_EXTRAS.format(names=names))
        lines.extend(self.for_terminal)
        if self.check_not_run:
            lines.append(prompts.SETUP_CHECK_NOT_RUN)
        elif self.check_failed:
            lines.append(prompts.SETUP_CHECK_FAILED)
        lines.append(prompts.SETUP_DONE.format(name=self.name))
        for line in lines:
            self.say(line)
        self.face.done([line.strip() for line in lines])

    def projects(self) -> tuple[str, str] | None:
        """Projects at the config step. Picked among the Extra Features: the plan's folder and level. A config step
        run without the plan asks, never guessing from the Mac's folders (recommendation: no): yes, then the folder (as
        typed, ~ allowed; it must exist, and not be your home itself) and the level. A dry run only shows the
        question."""
        chosen = self.picked()
        if chosen is not None:
            if "projects" not in chosen:
                return None
            folder, level = self.answer("projects_folder"), self.answer("projects_level")
            if not isinstance(folder, str) or not isinstance(level, str):
                return None
            path = expanded(folder)
            if path is not None and outside_home(path, Path.home()):
                self.say(prompts.SETUP_PROJECTS_OUTSIDE.format(folder=folder))
            self.projects_planned = True
            return folder, level
        if self.in_window and not self.dry_run:  # its own screens: the question, the folder picker, the level
            if self.answer("projects") is not True:
                return None
        else:
            self.say(prompts.SETUP_PROJECTS.format(name=self.name))
            if self.dry_run:
                self.say(prompts.SETUP_PROJECTS_DRY_RUN)
                return None
            self.say(prompts.SETUP_PROJECTS_RECOMMENDED)
            if not self.yes(prompts.SETUP_PROJECTS_ASK):
                return None
        where = self._projects_where()
        if where is None:
            return None
        if outside_home(where.folder, Path.home()):  # the move's share leaves it to you
            self.say(prompts.SETUP_PROJECTS_OUTSIDE.format(folder=where.folder))
        return where.typed, where.level

    def _projects_where(self) -> _Where | None:
        """The projects folder (it must exist, not be your home itself, and pass the share's rules,
        `projects_refusal`; q or no terminal: none) and the level. In the window, their own screens (the folder
        picker, the level's choice), with the same rules."""
        if self.in_window:
            typed, level = self.answer("projects_folder"), self.answer("projects_level")
            found = expanded(typed) if isinstance(typed, str) else None
            if not isinstance(typed, str) or found is None or not isinstance(level, str):
                return None
            return _Where(typed, found, level)
        self.say(prompts.SETUP_PROJECTS_FOLDER_RECOMMENDED)
        while True:
            given = self.typed(prompts.SETUP_PROJECTS_FOLDER)
            if given is None or given.lower() == "q":  # no terminal: the default folder may be missing, never a loop
                return None
            answer = given or "~/dev"
            found = expanded(answer)
            if found is None:
                self.say(prompts.SETUP_PROJECTS_TILDE.format(folder=answer))
                continue
            folder = found
            if folder == Path.home():
                self.say(prompts.SETUP_PROJECTS_HOME)
            elif not folder.is_dir():
                self.say(prompts.SETUP_PROJECTS_NO_FOLDER.format(folder=folder))
            elif (why := projects_refusal(folder, Path.home())) is not None:
                self.say(prompts.SETUP_PROJECTS_REFUSED.format(why=why[:1].upper() + why[1:]))
            else:
                break
        self.say(prompts.SETUP_PROJECTS_LEVELS)
        self.say(prompts.SETUP_PROJECTS_LEVEL_RECOMMENDED)
        while True:
            level = self.ask(prompts.SETUP_PROJECTS_LEVEL, "summary").lower()
            if level in PROJECT_LEVELS:
                break
            self.say(prompts.SETUP_PROJECTS_BAD_LEVEL.format(answer=level))
        typed = answer if answer.startswith("~") else str(folder)
        self.say(prompts.SETUP_PROJECTS_CHOSEN.format(folder=typed, level=level))
        return _Where(typed, folder, level)

    def chords_extra(self) -> None:
        """Chords picked from the extras: [chords] added to the config (a copy of the old one kept), nothing asked."""
        self._section_extra(
            "chords",
            lambda cfg: cfg.chords_enabled,
            (
                prompts.SETUP_CHORDS_HERS,
                prompts.SETUP_CHORDS_NO_CONFIG,
                prompts.SETUP_CHORDS_NOT_ADDED,
                prompts.SETUP_CHORDS_ON,
                prompts.SETUP_CHORDS_ADDED,
            ),
        )

    def maps_extra(self) -> None:
        """Places and routes (or Google Maps) picked from the extras: [maps] added to the config, nothing asked."""
        self._section_extra(
            "maps",
            lambda cfg: cfg.maps_enabled,
            (
                prompts.SETUP_MAPS_HERS,
                prompts.SETUP_MAPS_NO_CONFIG,
                prompts.SETUP_MAPS_NOT_ADDED,
                prompts.SETUP_MAPS_ON,
                prompts.SETUP_MAPS_ADDED,
            ),
        )

    def _section_extra(self, section: str, on: Callable[[Config], bool], words: tuple[str, str, str, str, str]) -> None:
        """An extra that is only its section: added to the config (a copy of the old one kept), nothing asked. In her
        own user her config is hers alone: the line to add and the restart, nothing written. A config that has the
        section already, or does not load, is left alone. `words`: hers, no config, not added, on already, added."""
        from local_assistant.setup import with_section  # setup.py's (it imports this module)

        hers, no_config, not_added, already, added = words
        if self.own_user():
            self.her_config(hers.format(save=prompts.NANO_SAVE), ("section", section))
            return
        try:
            text = self.config_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.say(no_config.format(path=self.config_path))
            return
        problem = _config_problem(text)
        if problem:  # the user's work: never changed
            self.say(not_added.format(path=self.config_path, why=problem))
            return
        if on(parse_config(tomllib.loads(text))):
            self.say(already)
            return
        self._write_private(self.config_path, with_section(text, section))
        self.say(added.format(path=self.config_path))

    def projects_extra(self) -> None:
        """Projects picked from the extras: the folder and the level asked, then [projects] in the config (a copy of
        the old one kept). In her own user her config is hers alone: the lines to add and the commands, nothing
        written. A config that has [projects] already is left alone."""
        from local_assistant.setup import with_projects  # setup.py's (it imports this module)

        mine = self.own_user()
        text = ""
        if not mine:
            try:
                text = self.config_path.read_text(encoding="utf-8")
            except FileNotFoundError:
                self.say(prompts.SETUP_PROJECTS_NO_CONFIG.format(path=self.config_path))
                return
            if ownuser.projects_on(text):
                self.say(prompts.SETUP_PROJECTS_ON)
                return
            problem = _config_problem(text)
            if problem:  # the user's work: never changed, and nothing asked for it
                self.say(prompts.SETUP_PROJECTS_NOT_ADDED.format(path=self.config_path, why=problem))
                return
        self.say(prompts.SETUP_PROJECTS.format(name=self.name))
        where = self._projects_where()
        if where is None:
            return
        if mine:  # her ~ is /var/estia: her config names the folder by its absolute path
            root = json.dumps(str(where.folder), ensure_ascii=False)
            self.her_config(
                prompts.SETUP_PROJECTS_HERS.format(root=root, level=where.level, save=prompts.NANO_SAVE),
                ("projects", str(where.folder), where.level),
            )
            if outside_home(where.folder, Path.home()):  # share-projects.sh shares only folders in your home
                self.say(prompts.SETUP_PROJECTS_OUTSIDE.format(folder=where.folder))
            else:
                self.say(prompts.SETUP_PROJECTS_SHARE.format(folder=where.folder))
                self._share_projects(where.folder)
            return
        new = with_projects(text, where.typed, where.level)
        problem = _config_problem(new)
        if problem:
            self.say(prompts.SETUP_PROJECTS_NOT_ADDED.format(path=self.config_path, why=problem))
            return
        self._write_private(self.config_path, new)
        self.say(prompts.SETUP_PROJECTS_ADDED.format(path=self.config_path))

    # 6. extras
    def extras(self) -> None:
        """The Extra Features the plan picked, one at a time: each one's guide, what setup does for it, and how you
        know it worked. Without the plan's pick (a re-run, `--extras`): the list of every extra and each picked one's
        steps."""
        chosen = self.picked()
        if chosen is None:
            self.extras_list()
            return
        hand = [name for name in chosen if self._gone_through(name, chosen)]
        if hand:  # where each section goes, when she reads it
            if self.own_user():
                self.her_config(prompts.SETUP_EXTRAS_HERS.format(save=prompts.NANO_SAVE))
            else:
                self.say(prompts.SETUP_EXTRAS_YOURS.format(path=self.config_path))

    def _gone_through(self, name: str, chosen: tuple[str, ...]) -> bool:
        """One Extra Feature's steps (`_one_extra`), then its mark in the answers file: a re-run after a later stop
        offers it no more. True when a section of it is left for you to add."""
        by_hand = self._one_extra(name, chosen)
        if not self.dry_run:
            self.answers.mark_done(f"{DONE_EXTRA}{name}")
        return by_hand

    def _one_extra(self, name: str, chosen: tuple[str, ...]) -> bool:
        """One Extra Feature: its name and line, its guide, then what setup does (a section, a script, the keys) and
        how you know it worked. True when a section of it is left for you to add."""
        self.say(
            prompts.SETUP_EXTRA_HEADER.format(
                name=prompts.SETUP_FEATURE_NAMES[name], line=prompts.SETUP_FEATURE_LINES[name]
            )
        )
        guide = _guide(name)
        ids = SIGN_INS[name][2] if name in SIGN_INS else ""
        # its IDs given: its steps were on their own screen; a sign-in without the plan's pick (`--extras`): no ID
        # question and no section written, so its steps by hand stand in for a guide that says setup asks for them
        outside_the_plan = name in SIGN_INS and self.picked() is None
        if guide is not None and not outside_the_plan and not (ids and self.given(ids)):
            if guide.needs:
                self.say(prompts.SETUP_EXTRA_NEEDS.format(needs=guide.needs))
            for number, step in enumerate(guide.steps, 1):
                self.say(f"  {number}. {step}")
            self.say("")  # a blank line, then what setup does (owner, 27 Oct)
        by_hand = self._extra_step(name, chosen)
        self.say(prompts.SETUP_EXTRA_CHECK.format(check=prompts.SETUP_EXTRA_CHECKS[name]))
        for says, fix in prompts.SETUP_EXTRA_ERRORS.get(name, ()):
            self.say(prompts.SETUP_EXTRA_ERROR.format(says=says, fix=fix))
        return by_hand

    def _extra_step(self, name: str, chosen: tuple[str, ...]) -> bool:
        """What setup does for one Extra Feature; True when its section is left for you to add."""
        if name in ("maps", "google-maps"):
            if name == "maps" or "maps" not in chosen:  # [maps] once, also for Google Maps
                self._section_on("maps")
            if name == "google-maps":
                self._google_maps()
            return False
        if name in SIGN_INS:
            return self._signed_in(name)
        if name == "lights":
            for script in ("tapo-discover.sh", "set-tapo-credentials.sh", "hue-pair.sh"):
                self._extra_offer(name, [str(REPO / "scripts" / script)], window=self._in_window(script))
            self.say(prompts.SETUP_LIGHTS_DEVICES)
            return True
        if name in ("chords", "web", "documents"):
            by_hand = self._section_on(name)
            if name == "web":
                self.web_keys()
            if name == "documents":
                self._documents_folder()
            return by_hand
        if name == "projects":
            if self.projects_planned:  # this run's config step wrote [projects] from the plan
                self.say(prompts.SETUP_SECTION_ON.format(name=prompts.SETUP_FEATURE_NAMES[name], section=name))
            else:
                self.projects_extra()
            return False
        self.say(f"  {EXTRAS[name].how}")
        return True

    def _google_maps(self) -> None:
        """Google's command-line tool (gcloud) from Homebrew when it is missing, its sign-in, then setup's script for
        the project and its key, each on Enter. Still no gcloud (its install skipped or failed): its steps by hand,
        never a command that cannot start. In the setup window (owner, 29 Oct: its Terminal path in 0.9.8; its
        sign-in and its script ask on a terminal) its commands are named for Terminal, after setup."""
        if self.in_window and not self.dry_run:
            commands = [] if shutil.which("gcloud") else ["brew install --cask gcloud-cli"]
            commands += ["gcloud auth login --no-launch-browser", str(REPO / "scripts" / "setup-google-maps.sh")]
            text = "\n".join([prompts.SETUP_WINDOW_MAPS_TERMINAL, *(f"$ {command}" for command in commands)])
            self.say(text)
            self.for_terminal = (*self.for_terminal, text)
            return
        if shutil.which("gcloud") is None:
            self._extra_offer("google-maps", [shutil.which("brew") or "brew", "install", "--cask", "gcloud-cli"])
        gcloud = shutil.which("gcloud")  # Homebrew's, once installed, on setup.sh's PATH
        if gcloud is None:
            self.say(prompts.SETUP_ID_LATER.format(name=prompts.SETUP_FEATURE_NAMES["google-maps"]))
            self.say(f"  {EXTRAS['google-maps'].how}")
            return
        self._extra_offer("google-maps", [gcloud, "auth", "login", "--no-launch-browser"])
        self._extra_offer("google-maps", [str(REPO / "scripts" / "setup-google-maps.sh")])

    def _signed_in(self, name: str) -> bool:
        """Mail, Spotify or one Google calendar: on from this run's config step (its IDs from the plan), then its
        sign-in or secret address on Enter. Without its IDs: later, and its steps by hand, nothing to add to her
        config yet (False). Its section not written this run: its steps by hand (True)."""
        section, script, ids = SIGN_INS[name]
        given = self.given(ids) if ids else True
        if ids and not given:  # nothing goes into her config without them: no config to open, no restart
            self.say(prompts.SETUP_ID_LATER.format(name=prompts.SETUP_FEATURE_NAMES[name]))
            self.say(f"  {EXTRAS[name].how}")
            return False
        if section not in self.sections_written:
            self.say(f"  {EXTRAS[name].how}")
            return True
        self.say(prompts.SETUP_SECTION_ON.format(name=prompts.SETUP_FEATURE_NAMES[name], section=section))
        address = given.get("address", "") if isinstance(given, Mapping) else ""
        command = [str(REPO / "scripts" / script), *([address] if address else [])]
        self._extra_offer(name, command, window=self._in_window(script))
        return False

    def _extra_offer(self, name: str, command: list[str], *, window: Callable[[], int] | None = None) -> bool:
        """A script an extra needs, shown and run on Enter; one that stops is named with its command. In the window,
        in her own macOS account, a script that only runs her command as her (`AS_HER`) is that command, macOS's own
        box asking for the admin rights; `window`: what the window runs in the script's place (`offer`)."""
        hers = AS_HER.get(Path(command[0]).name)
        if hers is not None and self.in_window and not self.dry_run and self.own_user():
            command = [SUDO, str(ownuser.BIN / "estia"), hers, *command[1:]]
        failed = prompts.SETUP_EXTRA_FAILED.format(
            name=prompts.SETUP_FEATURE_NAMES[name], command=_shown_command(command)
        )
        why = prompts.ADMIN_WHY_HER if command[0] == SUDO else ""
        return self.offer(command, failed=failed, why=why, window=window)

    def _in_window(self, script: str) -> Callable[[], int] | None:
        """What the window runs in the place of a script that reads its answers on a terminal (the window has none):
        its questions on the window's own screens, then what the script does with them, its exit code. None in
        Terminal, and for every other script."""
        if not self.in_window:
            return None
        stand_ins = {
            "set-tapo-credentials.sh": self._tapo_login,
            "set-calendar-url.sh": self._calendar_address,
            "spotify-login.sh": self._spotify_login,
        }
        return stand_ins.get(script)

    def _tapo_login(self) -> int:
        """scripts/set-tapo-credentials.sh in the window: the email, then the password (hidden), each on its screen;
        in her own macOS account each saved by her wrapper (`estia secret set tapo.env`, the value on its stdin only,
        macOS's own box asking), before it in your tapo.env (0600), where her device helper reads it. Later on either
        screen saves nothing."""
        email = self.answer("tapo_email")
        password = self.answer("tapo_password") if isinstance(email, str) and email else None
        if not isinstance(email, str) or not email or not isinstance(password, str) or not password:
            self.say(prompts.SETUP_OFFER_SKIPPED)
            return 0
        values = (("TAPO_USERNAME", email), ("TAPO_PASSWORD", password))
        if self.own_user():
            if not all(self._her_secret("tapo.env", key, value, prompts.SETUP_TAPO_TITLE) for key, value in values):
                return 1
            self.say(prompts.SETUP_TAPO_SAVED_HERS)
            return 0
        folder = Path.home() / ".config" / "local-assistant"
        if not all(
            self._your_secret(folder, "tapo.env", key, value, prompts.SETUP_TAPO_TITLE) for key, value in values
        ):
            return 1
        self.say(prompts.SETUP_TAPO_SAVED.format(file=folder / "tapo.env"))
        return 0

    def _calendar_address(self) -> int:
        """scripts/set-calendar-url.sh in the window, for the calendar named "calendar": its iCal address pasted
        hidden on its screen (the script's check), then saved as the script saves it, with the time it was saved."""
        url = self.answer("calendar_url")
        if not isinstance(url, str) or not url:
            self.say(prompts.SETUP_OFFER_SKIPPED)
            return 0
        key = "CALENDAR_ICS_URL"
        if self.own_user():
            if not self._her_secret("calendar.env", key, url, prompts.SETUP_CALENDAR_TITLE):
                return 1
            self.say(prompts.SETUP_CALENDAR_SAVED_HERS)
            return 0
        folder = Path.home() / ".config" / "local-assistant"
        if not self._your_secret(folder, "calendar.env", key, url, prompts.SETUP_CALENDAR_TITLE):
            return 1
        self.say(prompts.SETUP_CALENDAR_SAVED.format(file=folder / "calendar.env"))
        return 0

    def _spotify_login(self) -> int:
        """scripts/spotify-login.sh in the window: in her own macOS account her sign-in as her (`estia spotify-login`,
        macOS's own box asking), before it the script as you; its link and steps on a screen of the window, where the
        address the browser could not open is pasted hidden, then written to its stdin only. Later ends its input:
        nothing is saved."""
        later: list[bool] = []

        def paste(question: str, before: list[str]) -> str | None:
            first = [i for i, line in enumerate(before) if line.startswith("1. ")]
            steps = "\n".join(before[first[-1] if first else max(len(before) - 3, 0) :])
            value = self.answer("spotify_paste", steps=steps, ask=question)
            if not isinstance(value, str) or not value:
                later.append(True)
                return None
            return f"{value}\n"

        hers = self.own_user()
        command = (
            [SUDO, str(ownuser.BIN / "estia"), "spotify-login"]
            if hers
            else [str(REPO / "scripts" / "spotify-login.sh")]
        )
        if hers and not _release_reads_answer():  # input() with no line end: both sides would wait (FRIEND-8)
            text = prompts.SETUP_WINDOW_SPOTIFY_OLD.format(command=" ".join(command))
            self.say(text)
            self.for_terminal = (*self.for_terminal, text)
            return 0
        done = self.run(command, printed=True, why=prompts.ADMIN_WHY_HER if hers else "", answer=paste)
        if later:
            self.say(prompts.SETUP_OFFER_SKIPPED)
            return 0
        return done.returncode

    def _her_secret(self, file: str, key: str, value: str, label: str) -> bool:
        """One value into her `file` by her wrapper, as her (`estia secret set`), the value on its stdin only."""
        command = [SUDO, str(ownuser.BIN / "estia"), "secret", "set", file, key]
        done = self.run(command, input=f"{value}\n", capture_output=True, why=prompts.ADMIN_WHY_HER)
        if done.returncode != 0:
            why = f"{_shown_command(command)} ended with {done.returncode}"
            self.say(prompts.SETUP_SECRET_NOT_SAVED.format(label=label, why=why))
        return done.returncode == 0

    def _your_secret(self, folder: Path, file: str, key: str, value: str, label: str) -> bool:
        """Before her own macOS account: one value into `file` in your config folder (0600, one rename), where she
        reads it."""
        try:
            private_folders(folder)
            secretfile.save(folder, file, key, value)
        except (secretfile.SecretError, OSError) as exc:
            why = str(exc) if isinstance(exc, secretfile.SecretError) else type(exc).__name__
            self.say(prompts.SETUP_SECRET_NOT_SAVED.format(label=label, why=why))
            return False
        return True

    def share_ask(self) -> Callable[[str], bool]:
        """A share's close question on the window's own screen: Close them (recommended), or Keep them."""
        return lambda question: self.answer("share_close", question=question) is True

    def _share(self, args: list[str]) -> Callable[[], int] | None:
        """In the window, a share script's share inside setup (`share.main`, as you: no admin rights), its close
        question a window screen; None in Terminal, where the script runs and asks as it always did."""
        if not self.in_window:
            return None
        marker = hostsec.tool_root() / "usr/local/estia/migration.json"  # own-user.sh's, as the scripts pass it
        return lambda: share.main([*args, "--marker", str(marker)], ask=self.share_ask(), say=self.say)

    def _share_projects(self, folder: Path) -> bool:
        """scripts/share-projects.sh for the projects folder, shown and run on Enter (the window: in setup)."""
        return self.offer([str(REPO / "scripts" / "share-projects.sh"), str(folder)], window=self._share([str(folder)]))

    def _documents_folder(self) -> None:
        """Your ~/Estia-KB: in her own macOS account the share makes it and lets her read it (as you, no admin
        rights), shown and run on Enter (owner, 27 Oct: every command setup runs for an extra); one that stops is
        named with its command before the next extra starts (review PROM-19). Before it she reads it as you, so it is
        made here, private to you."""
        if self.own_user() or self.dry_run:
            self._extra_offer(
                "documents", [str(REPO / "scripts" / "share-documents.sh")], window=self._share(["--documents"])
            )
            return
        folder = Path.home() / "Estia-KB"
        if not folder.exists():
            folder.mkdir(mode=0o700)
            folder.chmod(0o700)

    def _section_on(self, section: str) -> bool:
        """An extra that is its section: on already from this run's config step or in your config (one line), else
        added now as the extras always did (chords, places and routes), else its steps by hand (web search,
        documents: True)."""
        name = prompts.SETUP_FEATURE_NAMES["maps" if section == "maps" else section]
        if section in self.sections_written:
            self.say(prompts.SETUP_SECTION_ON.format(name=name, section=section))
            return False
        if section == "maps":
            self.maps_extra()
            return False
        if section == "chords":
            self.chords_extra()
            return False
        if self._in_your_config(section):  # added again, it would stop her from starting
            self.say(prompts.SETUP_SECTION_ON_ALREADY.format(name=name, section=section))
            return False
        self.say(f"  {EXTRAS[section].how}")
        return True

    def _in_your_config(self, section: str) -> bool:
        """Your config has this section already (before her own macOS account; hers is hers alone, never read):
        adding it again would stop her from starting."""
        if self.own_user():
            return False
        try:
            return isinstance(tomllib.loads(self.config_path.read_text(encoding="utf-8")).get(section), dict)
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            return False

    def _held_for_her(self, name: str) -> bool:
        """Her web.env holds this key already (before her own macOS account: in your config folder, where she reads
        it; hers is hers alone, never read). Only the key's name is looked for; its value is read past, never kept."""
        folder = Path.home() / ".config" / "local-assistant"
        if self.own_user() or self.config_path.parent != folder:
            return False
        try:
            lines = (folder / "web.env").read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return False
        return any(key.strip() == name and value.strip() for key, _, value in (line.partition("=") for line in lines))

    def web_keys(self) -> bool:
        """Web search's keys, each pasted once at a hidden question (Ollama's tested once at ollama.com): saved for
        her (her web.env, 0600; in her own macOS account through her wrapper, the key on its stdin) and a copy in your
        login keychain, where scripts/set-web-keys.sh finds it. Never in the answers file, a program's arguments or
        on the screen. Enter leaves a key for later; one her web.env holds already is not asked again. True when she
        has a key (or a dry run): with none, one line says she cannot search yet (round 2, ux-10)."""
        opened: list[bool] = []  # setup opened the keychain: locked again after both keys (review SEC-3)
        has_key = self.dry_run
        for qid, name, service, label in WEB_KEYS:
            if self._held_for_her(name):
                self.say(prompts.SETUP_KEY_HELD.format(label=label))
                has_key = True
                continue
            key = self.answer(qid)
            if self.dry_run:
                continue
            if not isinstance(key, str) or not key:
                self.say(prompts.SETUP_KEY_SKIPPED.format(label=label))
                continue
            self._save_key(name, service, key, label, opened)
            has_key = True
        setup_keychain.lock_again(opened)
        if not has_key:
            self.say(prompts.SETUP_WEB_NO_KEY)
        return has_key

    def _save_key(self, name: str, service: str, key: str, label: str, opened: list[bool] | None = None) -> None:
        if self.own_user():  # her web.env is hers alone: her wrapper writes it, the key on its stdin
            command = [SUDO, str(ownuser.BIN / "estia"), "secret", "set", "web.env", name]
            done = self.run(command, input=f"{key}\n", why=prompts.ADMIN_WHY_HER)
            why = "" if done.returncode == 0 else f"{_shown_command(command)} ended with {done.returncode}"
        else:
            why = self._save_for_her(name, key)
        if why:
            self.say(prompts.SETUP_KEY_NOT_SAVED.format(label=label, why=why))
        else:
            self.say(prompts.SETUP_KEY_SAVED.format(label=label, characters=len(key)))
        account = os.environ.get("USER") or getpass.getuser()

        def locked() -> None:  # said once, before macOS asks (Terminal: the Mac password there; the window: its box)
            self.say(prompts.SETUP_WINDOW_KEYCHAIN_LOCKED if self.in_window else prompts.SETUP_KEY_KEYCHAIN_LOCKED)

        # "ollama" and "brave-search" are shared: another program's key there is replaced only on a yes (review SEC-8)
        if setup_keychain.has(service, account) and not self.answer("keychain_replace", service=service):
            self.say(prompts.SETUP_KEY_KEPT_OTHER.format(label=label, service=service))
            return
        saved = setup_keychain.save(
            service, account, key, label=f"Estia: {label}", before_unlock=locked, opened=opened,
            opener=self.keychain_opener(),
        )  # fmt: skip
        if saved:
            self.say(prompts.SETUP_KEY_IN_KEYCHAIN.format(label=label))
        else:
            self.say(prompts.SETUP_KEY_NOT_IN_KEYCHAIN.format(label=label))

    def _save_for_her(self, name: str, key: str) -> str:
        """Before her own macOS account: her web.env in your config folder, the one she reads (0600, one rename);
        why not ("" when saved)."""
        folder = Path.home() / ".config" / "local-assistant"
        if self.config_path.parent != folder:  # she reads web.env only there
            return prompts.SETUP_KEY_NOT_HERS.format(folder=folder)
        try:
            private_folders(folder)
            secretfile.save(folder, "web.env", name, key)
        except (secretfile.SecretError, OSError) as exc:
            return str(exc) if isinstance(exc, secretfile.SecretError) else type(exc).__name__
        return ""

    def extras_list(self) -> None:
        """Every extra, numbered, one line each; then each picked one's steps, one at a time, as the plan's pick has
        them (`_one_extra`: its scripts and sign-ins on Enter, the web keys asked hidden), and calls and voice by their
        steps (`--extras`, and a re-run). Her config is opened once, at the end, only for a section left to add."""
        self.say(prompts.SETUP_EXTRAS)
        for i, (name, extra) in enumerate(EXTRAS.items(), 1):  # its number, the name to type, what it is
            self.say(f"  {i:>2}. {name:12} {prompts.SETUP_FEATURE_LINES.get(name, extra.title + '.')}")
        if self.dry_run:
            self.say("  (asks which ones, then shows how to add each)")
            return
        unknown: list[str] = []
        if self.in_window:  # its screen of switches, none on (after a stop: the picks not gone through)
            picked = self.answer("extras_list")
            named = list(picked) if isinstance(picked, tuple) else []
        elif left := extras_left(self.answers):  # after a stop: Enter takes the picks not gone through
            self.say(_left_line(left))
            named, unknown = parse_extras(self.ask(prompts.SETUP_EXTRAS_ASK_LEFT, " ".join(left)))
        else:
            self.say(prompts.SETUP_EXTRAS_RECOMMENDED)
            named, unknown = parse_extras(self.ask(prompts.SETUP_EXTRAS_ASK))
        if unknown:
            self.say(f"  (not an extra: {', '.join(unknown)})")
        hand: list[str] = []
        for name in named:
            if name in FEATURES:
                if self._gone_through(name, tuple(named)):
                    hand.append(name)
            else:  # calls and voice: their own steps, by hand
                self.say(f"• {name}: {EXTRAS[name].how}")
                hand.append(name)
        if hand:  # where each section goes
            if self.own_user():
                self.her_config(prompts.SETUP_EXTRAS_HERS.format(save=prompts.NANO_SAVE))
            else:
                self.say(prompts.SETUP_EXTRAS_YOURS.format(path=self.config_path))
