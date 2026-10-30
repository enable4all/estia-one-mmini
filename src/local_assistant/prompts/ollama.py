"""Ollama's official build: setup's and doctor's words for its install, checks, service and update (ollama_build)."""

from __future__ import annotations

# fmt: off
__all__ = [
    "OLLAMA_ADMIN_REFUSED", "OLLAMA_APP", "OLLAMA_APP_FIX", "OLLAMA_APP_FIX_IT", "OLLAMA_APP_LIMITS", "OLLAMA_APP_LIMIT_LOGIN",
    "OLLAMA_APP_LIMIT_PROMPTS", "OLLAMA_APP_LIMIT_UPDATE", "OLLAMA_APP_NOT_OPENED", "OLLAMA_APP_NOT_UP", "OLLAMA_APP_NO_PROGRAM",
    "OLLAMA_APP_OTHER", "OLLAMA_APP_OTHER_FIX", "OLLAMA_APP_OURS", "OLLAMA_APP_OURS_FIX", "OLLAMA_APP_OURS_GOES", "OLLAMA_APP_REINSTALL",
    "OLLAMA_APP_STARTING", "OLLAMA_APP_USED", "OLLAMA_APP_WAITING", "OLLAMA_APP_WOULD", "OLLAMA_CHECKS",
    "OLLAMA_CURRENT_NOT_LINK", "OLLAMA_DOWNLOAD_DISK_FULL", "OLLAMA_DOWNLOAD_FAILED", "OLLAMA_DRY_RUN_GOES_ON", "OLLAMA_FOLDER_KEPT",
    "OLLAMA_FOLDER_REFUSED", "OLLAMA_FOUND_ANSWERING", "OLLAMA_FOUND_APP", "OLLAMA_FOUND_HOMEBREW",
    "OLLAMA_GGUF_SOURCE", "OLLAMA_HER_ACCOUNT", "OLLAMA_HER_ACCOUNT_FAILED", "OLLAMA_HER_ACCOUNT_FIX",
    "OLLAMA_HER_ACCOUNT_MADE", "OLLAMA_HER_ADDRESS_FIX", "OLLAMA_HER_ADDRESS_TAKEN", "OLLAMA_HER_APP_NOT_USABLE",
    "OLLAMA_HER_CURRENT_NOT_LINK", "OLLAMA_HER_DISK", "OLLAMA_HER_DISK_FIX", "OLLAMA_HER_FOLDER_REFUSED",
    "OLLAMA_HER_FOUND", "OLLAMA_HER_INSTALL", "OLLAMA_HER_KEPT", "OLLAMA_HER_PASSWORD", "OLLAMA_HER_REPLACES",
    "OLLAMA_HER_SERVICE",
    "OLLAMA_HER_UNSAFE", "OLLAMA_HER_UNTESTED_FIX", "OLLAMA_HER_UP", "OLLAMA_HOMEBREW", "OLLAMA_HOMEBREW_FIX",
    "OLLAMA_HOMEBREW_REMOVE", "OLLAMA_HOMEBREW_THERE", "OLLAMA_INSTALL", "OLLAMA_INSTALLED", "OLLAMA_JOB",
    "OLLAMA_JOB_APP", "OLLAMA_JOB_FIX", "OLLAMA_JOB_HOMEBREW", "OLLAMA_KEPT", "OLLAMA_LOG_EMPTIED",
    "OLLAMA_LOG_PRIVATE", "OLLAMA_LOG_PRIVATE_LEAD", "OLLAMA_NOT_AT_BOOT", "OLLAMA_NOT_AT_BOOT_FIX", "OLLAMA_NOT_A_FOLDER", "OLLAMA_NOT_FOUND",
    "OLLAMA_NOT_FOUND_FIX", "OLLAMA_NOT_UP", "OLLAMA_NO_TERMINAL", "OLLAMA_ON_INPUT", "OLLAMA_OTHER",
    "OLLAMA_OTHER_FIX", "OLLAMA_PASSWORD", "OLLAMA_PATH_ADDED", "OLLAMA_PATH_WOULD", "OLLAMA_REFUSED", "OLLAMA_RESTART_AGENT",
    "OLLAMA_RESTART_SYSTEM", "OLLAMA_ROLLED_BACK", "OLLAMA_SERVICE", "OLLAMA_SERVICE_CHANGED", "OLLAMA_SERVICE_FAILED",
    "OLLAMA_SERVICE_LEAD",
    "OLLAMA_SHARED_NOT_FOUND", "OLLAMA_SHARED_UNREAD", "OLLAMA_UNSAFE", "OLLAMA_UNSAFE_OWNER", "OLLAMA_UNSAFE_UNREAD",
    "OLLAMA_UNSAFE_WRITABLE", "OLLAMA_UNTESTED", "OLLAMA_UNTESTED_FIX", "OLLAMA_UP", "OLLAMA_UPDATE",
    "OLLAMA_WHY_ACCOUNT", "OLLAMA_WHY_ACCOUNT_UNREAD", "OLLAMA_WHY_FINGERPRINT", "OLLAMA_WHY_LIBRARY",
    "OLLAMA_WHY_LINK", "OLLAMA_WHY_MODES", "OLLAMA_WHY_NOT_APPLE", "OLLAMA_WHY_NOT_NOTARIZED", "OLLAMA_WHY_NO_PROGRAM",
    "OLLAMA_WHY_OLD", "OLLAMA_WHY_OPEN", "OLLAMA_WHY_SHARED", "OLLAMA_WHY_TEAM", "OLLAMA_WHY_UNPACKED",
    "OLLAMA_WHY_UNSIGNED", "SETUP_OLLAMA_APP_ADVICE", "SETUP_OLLAMA_APP_ASK", "SETUP_OLLAMA_APP_ASK_BUILD",
    "SETUP_OLLAMA_APP_BUILD",
    "SETUP_OLLAMA_APP_BUILD_HERS_STAYS", "SETUP_OLLAMA_APP_BUILD_LINE", "SETUP_OLLAMA_APP_BUILD_LINE_HERS", "SETUP_OLLAMA_APP_CONFIG",
    "SETUP_OLLAMA_APP_HERS", "SETUP_OLLAMA_APP_HERS_ADVICE", "SETUP_OLLAMA_APP_LEAD", "SETUP_OLLAMA_APP_LIST", "SETUP_OLLAMA_APP_LIST_HERS",
    "SETUP_OLLAMA_APP_REDO_HERS", "SETUP_OLLAMA_APP_SSH_ADVICE", "SETUP_OLLAMA_APP_SSH_ASK", "SETUP_OLLAMA_APP_UNKNOWN",
    "SETUP_OLLAMA_APP_USE", "SETUP_OLLAMA_APP_USE_LINE",
]
# fmt: on

# Ollama's official build (owner, 15 Oct: "use only the official build"): setup's and doctor's words (ollama_build)
OLLAMA_KEPT = "✓ Ollama: its official build {version} answers ({program}); kept."
OLLAMA_HOMEBREW = (
    "Homebrew's Ollama answers ({program}): its build has no JSON-schema library (libollama_xgrammar.dylib), so "
    "Ollama's engine for Apple silicon (MLX) gives no JSON-schema output, which she needs"
)
OLLAMA_HOMEBREW_REMOVE = "brew services stop ollama, then brew uninstall ollama (the models in ~/.ollama stay)"
OLLAMA_HOMEBREW_FIX = f"{OLLAMA_HOMEBREW_REMOVE}, then run scripts/setup.sh again: it installs Ollama's official build"
OLLAMA_HOMEBREW_THERE = (
    "Homebrew's Ollama is installed too ({path}), without the JSON-schema library: setup never uses it"
)
OLLAMA_APP = (
    "the Ollama app {state} ({path}): it updates itself, starts only when someone logs in on the Mac's screen, and "
    "stops any other Ollama when its own cannot start"
)
OLLAMA_APP_FIX = (
    "quit it (osascript -e 'quit app \"Ollama\"'), then move it to the Trash: mv {path} ~/.Trash/; then run "
    "scripts/setup.sh again (it installs Ollama's official build as a background service)"
)
# the Ollama app, when the person chooses it at setup's question (owner, 29 Oct): its limits, said plainly
OLLAMA_APP_LIMIT_LOGIN = "she answers only while someone is logged in and the Ollama app runs."
# the app runs as you: what she is told and answers goes through it (review W06, SECURITY.md)
OLLAMA_APP_LIMIT_PROMPTS = "her prompts pass through a program your account can change."
OLLAMA_APP_LIMIT_UPDATE = "The app updates itself: an update can pause her answers until the model check passes again."
OLLAMA_APP_LIMITS = f"{OLLAMA_APP_LIMIT_LOGIN} {OLLAMA_APP_LIMIT_UPDATE}"
# setup's question when the Ollama app is on the Mac (owner, 29 Oct: "since I have the Ollama app, use it"), asked
# first in the plan; Terminal's list of the two cards (the window draws the cards)
SETUP_OLLAMA_APP_USE = "Use the **Ollama app**"
# the card's small line: the window puts the reason (SETUP_OLLAMA_APP_ADVICE) before it, by its pill (owner, 29 Oct)
SETUP_OLLAMA_APP_USE_LINE = (  # both limits (review W06): the login, and what her prompts pass through
    f"{OLLAMA_APP_LIMIT_LOGIN[:1].upper()}{OLLAMA_APP_LIMIT_LOGIN[1:]} "
    f"{OLLAMA_APP_LIMIT_PROMPTS[:1].upper()}{OLLAMA_APP_LIMIT_PROMPTS[1:]}"
)
SETUP_OLLAMA_APP_BUILD = "Install the **background build**"
SETUP_OLLAMA_APP_BUILD_LINE = (
    "Starts with the Mac, as a background service.\n"
    "The Ollama app must then be quit and moved to the Trash: setup says how."
)
SETUP_OLLAMA_APP_LEAD = (
    "The Ollama app is on this Mac. {name} can use it,\n"
    "or setup installs Ollama's official build as a background service.\n\n"
    f"{OLLAMA_APP_LIMIT_UPDATE}"  # the login limit: the app's card says it
)


def _cards_listed(build_line: str) -> str:
    """Terminal's list of the two cards, with the very lines the window's cards show (c-1, round 3)."""
    return (
        f"  1. {SETUP_OLLAMA_APP_USE}\n     {SETUP_OLLAMA_APP_USE_LINE}\n"
        f"  2. {SETUP_OLLAMA_APP_BUILD}\n     " + build_line.replace("\n", "\n     ")
    )


SETUP_OLLAMA_APP_LIST = _cards_listed(SETUP_OLLAMA_APP_BUILD_LINE)  # NEW_INSTALLS "older": today's line
SETUP_OLLAMA_APP_ASK = "Which Ollama? (1 or 2; Enter: 1)"
SETUP_OLLAMA_APP_ADVICE = "nothing to install"
# her own account planned (a new install: owner, 30 Oct, W06): the background build is recommended, Enter takes it
SETUP_OLLAMA_APP_HERS_ADVICE = "she starts with the Mac, without a login"
SETUP_OLLAMA_APP_ASK_BUILD = "Which Ollama? (1 or 2; Enter: 2)"
# over a remote login (c-4, round 3): the app starts only with someone at the Mac's screen
SETUP_OLLAMA_APP_SSH_ADVICE = "over a remote login: the app needs someone at the Mac's screen"
SETUP_OLLAMA_APP_SSH_ASK = SETUP_OLLAMA_APP_ASK_BUILD
SETUP_OLLAMA_APP_UNKNOWN = "  Type 1 to use the Ollama app or 2 for the background build."
# a re-run that chose the app: her config records it, so doctor knows (a first install writes it itself)
SETUP_OLLAMA_APP_CONFIG = 'Add this line to {path}, at the top, then run scripts/doctor.sh:\n  ollama = "app"'
SETUP_OLLAMA_APP_HERS = 'Add this line to her config, at the top:\n  ollama = "app"'
OLLAMA_APP_USED = "✓ Ollama: the Ollama app {version} answers ({program}); used, as you chose."
OLLAMA_APP_WOULD = "(dry run) would use the Ollama app ({path}), started first when it does not run."
OLLAMA_APP_STARTING = (
    "The Ollama app does not run: setup opens it and waits up to 3 minutes. "
    "If macOS asks whether to open Ollama, click Open."
)
OLLAMA_APP_WAITING = (
    "  Still waiting for the Ollama app ({seconds} s). If macOS asks whether to open Ollama, click Open."
)
OLLAMA_APP_NOT_UP = (
    "✗ Ollama: the Ollama app did not answer within 3 minutes. Open it from Applications (click Open if macOS "
    "asks), then run scripts/setup.sh again."
)
OLLAMA_APP_NOT_OPENED = (
    "✗ Ollama: macOS did not open the Ollama app ({app}; open ended with {code}). Open it from Applications, then "
    "run scripts/setup.sh again."
)
OLLAMA_APP_NO_PROGRAM = "the Ollama app has no ollama program of its own inside it ({path})"
OLLAMA_APP_REINSTALL = "install the Ollama app again from ollama.com, then run scripts/setup.sh again"
OLLAMA_APP_OTHER = "another Ollama answers ({program}), not the Ollama app you chose"
OLLAMA_APP_OTHER_FIX = "stop it, then run scripts/setup.sh again: the Ollama app must answer here"
OLLAMA_APP_FIX_IT = (
    'the Ollama app must run in your own login, alone on 127.0.0.1:11434, for this Mac only (its setting "Expose '
    'Ollama to the network" off), at {needed} or later: fix that, then run scripts/setup.sh again'
)
OLLAMA_APP_OURS = (
    "setup's background service for Ollama is set up too ({daemon}): it would fight the app for 127.0.0.1:11434"
)
OLLAMA_APP_OURS_GOES = (  # an earlier try's (no config, no move): setup removes it itself (c-2, round 3)
    "Setup's Ollama service from an earlier try ({daemon}) goes, so the Ollama app has 127.0.0.1:11434 alone: "
    "admin rights next (your Mac password, if asked)."
)
OLLAMA_APP_OURS_FIX = (
    "/usr/bin/sudo /bin/launchctl bootout system/{label}, then /usr/bin/sudo /bin/rm {daemon}; then run "
    "scripts/setup.sh again"
)
OLLAMA_NOT_FOUND = "something answers at 127.0.0.1:11434, and its program could not be found"
OLLAMA_NOT_FOUND_FIX = (
    "/usr/bin/sudo /usr/sbin/lsof -nP -iTCP:11434 -sTCP:LISTEN names it; stop it, then run scripts/setup.sh again"
)
OLLAMA_OTHER = "the Ollama that answers ({program}) is not one setup can keep: {why}"
OLLAMA_OTHER_FIX = "stop it, then run scripts/setup.sh again: it installs Ollama's official build"
OLLAMA_WHY_UNSIGNED = "its signature does not check out"
OLLAMA_WHY_TEAM = "signed by {team}, not Ollama's ({want})"
OLLAMA_WHY_NOT_APPLE = (
    "its signature names Ollama's team ID but is not Apple's Developer ID certificate for it (made without Apple, as "
    "anyone can)"
)
OLLAMA_WHY_OPEN = (
    "it listens for other machines too ({addresses}), and Ollama has no login: set OLLAMA_HOST=127.0.0.1:{port} in "
    "its service, so only the Mac itself reaches it"
)
OLLAMA_WHY_ACCOUNT = "it runs as {account}, another account on this Mac, not you: that account controls her answers"
OLLAMA_WHY_ACCOUNT_UNREAD = "the account it runs as could not be read"
OLLAMA_WHY_SHARED = "{program}, run by {account}, also listens on its port: her requests may go to that one instead"
OLLAMA_SHARED_NOT_FOUND = "a program that could not be found"
OLLAMA_SHARED_UNREAD = "an account that could not be read"
OLLAMA_WHY_OLD = "its version {version} is older than {needed}, the first with JSON-schema output on the MLX engine"
OLLAMA_WHY_LIBRARY = "no {library}: no JSON-schema output on the MLX engine"
OLLAMA_WHY_FINGERPRINT = "its fingerprint is sha256:{got}, not the pinned sha256:{want}"
OLLAMA_WHY_UNPACKED = "it could not be unpacked"
OLLAMA_WHY_LINK = "{name} links outside its folder"
OLLAMA_WHY_NO_PROGRAM = "no ollama program in it"
OLLAMA_WHY_NOT_NOTARIZED = "ollama: Apple's notarization check did not accept it"
OLLAMA_NOT_A_FOLDER = "not a folder"
# step 1's line, as short as its facts allow (round 2, words-15): the checks in GUIDE's words; the fingerprint and
# the developer ID by name in the dry run (OLLAMA_CHECKS)
OLLAMA_INSTALL = (
    "Ollama: its official build {version} ({size} MB, from GitHub), checked before anything is installed: its "
    "fingerprint, every program's signature by Ollama and Apple's notarization. It goes into {folder} (no admin "
    "rights), then runs as a background service that starts with the Mac."
)
OLLAMA_CHECKS = (
    "  (then every program's signature, codesign --verify --strict -R: Apple's Developer ID certificate for Ollama's "
    "team {team}; Apple's notarization, spctl; both JSON-schema libraries)"
)
OLLAMA_INSTALLED = "✓ Ollama: {version} checked and installed in {folder}."
OLLAMA_FOLDER_KEPT = "✓ Ollama: {folder} is there and checks out; kept."
OLLAMA_FOLDER_REFUSED = (
    "✗ Ollama: {folder} is there but does not check out ({why}). Move it aside (mv {folder} {folder}.refused), then "
    "run scripts/setup.sh again."
)
OLLAMA_REFUSED = (
    "✗ Ollama: the download did not check out, so nothing was installed: {why}. Run scripts/setup.sh again; if it "
    "says the same, report it (SECURITY.md)."
)
OLLAMA_DOWNLOAD_DISK_FULL = "the Mac ran out of space during Ollama's download"  # curl's exit 23 (c-11, round 3)
OLLAMA_DOWNLOAD_FAILED = (
    "✗ Ollama: the download did not finish: check the internet connection, then run scripts/setup.sh again."
)
OLLAMA_CURRENT_NOT_LINK = "✗ Ollama: {path} is not a link: move it aside, then run scripts/setup.sh again."
OLLAMA_PATH_ADDED = "✓ Ollama: added to new Terminal windows ({line} in {profile})"
OLLAMA_PATH_WOULD = "Ollama: would add {line} to {profile}, so new Terminal windows find ollama"
# Terminal's password lines; in the window only macOS's box line (round 2, words-03: no password typed where nothing
# shows there)
OLLAMA_SERVICE_LEAD = (
    "Ollama's background service: it runs as you, starts with the Mac (no login needed) and is reachable only from the\n"
    "  Mac itself. Its file: {daemon}; its log: {log}."
)
OLLAMA_PASSWORD = "Your Mac password next, if asked: admin rights for this service only (nothing shows while you type)."
OLLAMA_SERVICE = f"{OLLAMA_SERVICE_LEAD}\n{OLLAMA_PASSWORD}"
OLLAMA_ON_INPUT = "the service file on its input"
OLLAMA_SERVICE_CHANGED = (
    "Ollama: its background service file ({daemon}) is not the one this version of setup writes: it is written "
    "again, then Ollama starts again (a few seconds without answers)."
)
# the setup window: macOS's box refused, then Not now (round 2, words-06): nothing ran, so no lines above say why
OLLAMA_ADMIN_REFUSED = (
    "✗ Ollama: not installed: admin rights were not given.\n"
    "  Fix: run scripts/setup.sh again, and type your Mac password in macOS's own box."
)
OLLAMA_SERVICE_FAILED = (
    "✗ Ollama: its background service was not installed (its lines say why). Run scripts/setup.sh again; "
    "/usr/bin/sudo /bin/launchctl print system/{label} shows its state."
)
OLLAMA_NOT_UP = (
    "✗ Ollama: no answer within {seconds} s. Look at /usr/bin/sudo /bin/launchctl print system/{label} and {log}, "
    "then run scripts/setup.sh again."
)
OLLAMA_UP = "✓ Ollama: {version} answers, always on (it starts with the Mac, no login needed)."
OLLAMA_UNTESTED = "{version} answers, not {pin}, the version she is tested with"
OLLAMA_UNTESTED_FIX = (
    "install {pin} the way this Ollama was installed; or remove it and its service, then run scripts/setup.sh again"
)
OLLAMA_NOT_AT_BOOT = (
    "no background service starts it with the Mac ({program}): after a restart she has no model until someone starts "
    "it again"
)
OLLAMA_NOT_AT_BOOT_FIX = (
    "stop it, then run scripts/setup.sh again: it installs Ollama's official build as a background service that "
    "starts with the Mac"
)
OLLAMA_JOB = (
    "another background service runs Ollama ({label}, {plist}) and nothing answers now: a second one from setup would "
    "fight it for 127.0.0.1:11434 forever"
)
OLLAMA_JOB_FIX = "start it again: {restart}; or remove it; then run scripts/setup.sh again"
OLLAMA_JOB_HOMEBREW = (
    "Homebrew's background service for Ollama is set up ({plist}): at a login it can take 127.0.0.1:11434 first, "
    "with Homebrew's build (no JSON-schema output on Apple silicon)"
)
OLLAMA_JOB_APP = "the Ollama app starts at each login ({plist}) and stops any other Ollama when its own cannot start"
OLLAMA_RESTART_SYSTEM = (
    "/usr/bin/sudo /bin/launchctl kickstart -k system/{label} (when launchctl cannot find it: /usr/bin/sudo "
    "/bin/launchctl bootstrap system {plist})"
)
OLLAMA_RESTART_AGENT = (
    "/bin/launchctl kickstart -k gui/{uid}/{label} (when launchctl cannot find it: /bin/launchctl bootstrap gui/{uid} "
    "{plist})"
)
OLLAMA_UPDATE = (
    "Ollama: {version} answers; the tested version is now {pin}. It is downloaded and checked while {version} keeps "
    "running; then her Ollama switches to it and starts again (a few seconds without answers)."
)
OLLAMA_ROLLED_BACK = (
    "✗ Ollama: {pin} did not take over, so {folder} is in use again ({version}) and its service was started again. "
    "{new} stays for a look; scripts/setup.sh tries again."
)
OLLAMA_DRY_RUN_GOES_ON = "  (setup stops here until this is fixed; the rest as it would run after)"
OLLAMA_UNSAFE = (
    "✗ Ollama: {path} is not yours alone ({why}), and its background service would run the program there as you at "
    "every start of the Mac. Fix: /bin/chmod go-w {path} when it is yours, else move it aside; then run "
    "scripts/setup.sh again."
)
OLLAMA_UNSAFE_OWNER = "another account owns it"
OLLAMA_UNSAFE_WRITABLE = "other accounts can change it"
OLLAMA_UNSAFE_UNREAD = "it could not be read"
OLLAMA_LOG_EMPTIED = "✓ Ollama: its log was over 10 MB and is now empty ({log})."
OLLAMA_LOG_PRIVATE_LEAD = "Ollama: its log ({log}) made yours and private, used from its next start."
OLLAMA_LOG_PRIVATE = f"{OLLAMA_LOG_PRIVATE_LEAD}\n  Your Mac password next, if asked (nothing shows while you type)."
# scripts/install-ollama.sh with no terminal (an AI's command tool): sudo asks only in Terminal, and it has no window
OLLAMA_NO_TERMINAL = (
    "✗ Ollama: scripts/install-ollama.sh asks for admin rights only in Terminal. Fix: run it in your own Terminal "
    "window, or run scripts/setup.sh at the Mac's own screen: its Ollama step asks in the Estia Setup window. Its "
    "--dry-run shows every step and changes nothing."
)
OLLAMA_GGUF_SOURCE = "a llama.cpp (GGUF) build of Gemma 4"

# her own Ollama (owner, 29 Oct: "Her own, in _estia"): a new install with her own macOS account (ollama_build, hers)
OLLAMA_HER_ACCOUNT = "Her own macOS account first ({user}), so her model goes straight into her own folder."
OLLAMA_HER_ACCOUNT_MADE = "✓ Her own macOS account: {user}, hidden, no password, no login."
OLLAMA_HER_ACCOUNT_FAILED = "her own macOS account ({user}) could not be made, so nothing was downloaded or installed"
OLLAMA_HER_ACCOUNT_FIX = (  # a wrong Mac password three times stops here too: running again comes first
    "run scripts/setup.sh again; if it stops here again, scripts/setup.sh --redo move and answer No: she then runs "
    "in your own account"
)
OLLAMA_HER_INSTALL = (
    "Ollama: its official build {version} ({size} MB, from GitHub), for her alone, checked before anything is "
    "installed: its fingerprint, every program's signature by Ollama and Apple's notarization. It goes into {folder}, "
    "which only admin rights can change."
)
OLLAMA_HER_SERVICE = (
    "Her Ollama's background service: it runs as her own macOS account {user}, starts with the Mac (no login needed)\n"
    "  and is reachable only from the Mac itself (port {port}). Her model goes into {models}, which only her account\n"
    "  reads. Its file: {daemon}; its log: {log}."
)
OLLAMA_HER_PASSWORD = (
    "Your Mac password next, if asked: admin rights for her own account and her Ollama only (nothing shows while you "
    "type)."
)
OLLAMA_HER_KEPT = "✓ Ollama: her own, {version}, answers ({program}); kept."
OLLAMA_HER_UP = "✓ Ollama: her own, {version}, answers, always on (as {user}, from the Mac's start, no login needed)."
OLLAMA_HER_UNTESTED_FIX = "her own Ollama stays as it is; a later Estia release updates it"
OLLAMA_HER_FOUND = "Found {what}: it stays as it is, and {name} gets her own Ollama beside it."
OLLAMA_HER_REPLACES = "Setup's Ollama service from an earlier try ({daemon}) is replaced by hers."
OLLAMA_FOUND_ANSWERING = "an Ollama that answers on port 11434 ({program})"
OLLAMA_FOUND_APP = "the Ollama app ({path})"
OLLAMA_FOUND_HOMEBREW = "Homebrew's Ollama ({path})"
OLLAMA_HER_APP_NOT_USABLE = (
    "The Ollama app cannot be used here ({why}): {name} gets her own Ollama instead; the app stays as it is."
)
OLLAMA_HER_ADDRESS_TAKEN = (
    "another program holds her Ollama's address on the Mac itself (port {port}): {program}, run by {account}"
)
OLLAMA_HER_ADDRESS_FIX = "stop it, then run scripts/setup.sh again"
OLLAMA_HER_DISK = "only {free} GB free on this Mac; her Ollama and her model need about {need} GB"
OLLAMA_HER_DISK_FIX = "free about {more} GB (System Settings → General → Storage), then run scripts/setup.sh again"
OLLAMA_HER_UNSAFE = "{path} can be changed by an account other than root, and with it her Ollama"
OLLAMA_HER_FOLDER_REFUSED = (
    "✗ Ollama: {folder} is there but does not check out ({why}). Move it aside (/usr/bin/sudo /bin/mv {folder} "
    "{folder}.refused), then run scripts/setup.sh again."
)
OLLAMA_HER_CURRENT_NOT_LINK = (
    "✗ Ollama: {path} is not a link: move it aside (/usr/bin/sudo /bin/mv {path} {path}.refused), then run "
    "scripts/setup.sh again."
)
OLLAMA_WHY_MODES = "{name} {why}"
# the card's second option with her own Ollama (asked before the move: her own account is asked later)
SETUP_OLLAMA_APP_BUILD_HERS_STAYS = (
    "With her own macOS account (asked later) the app stays; without it, the app must be quit and moved to the Trash."
)
SETUP_OLLAMA_APP_BUILD_LINE_HERS = f"Starts with the Mac, as a background service.\n{SETUP_OLLAMA_APP_BUILD_HERS_STAYS}"
SETUP_OLLAMA_APP_LIST_HERS = _cards_listed(SETUP_OLLAMA_APP_BUILD_LINE_HERS)
SETUP_OLLAMA_APP_REDO_HERS = "Her own Ollama stays as it is: this setup does not switch it to the Ollama app."
