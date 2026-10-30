"""Setup's words for the Estia Setup window (setup_window): what setup says about the window, in Terminal and in the
window itself. Re-exported by prompts/__init__.py."""

from __future__ import annotations

# fmt: off
__all__ = [
    "ADMIN_WHY", "ADMIN_WHY_CALLS", "ADMIN_WHY_HER", "ADMIN_WHY_MOVE", "ADMIN_WHY_OLLAMA", "ADMIN_WHY_OLLAMA_HERS",
    "ADMIN_WHY_SECURITY",
    "SETUP_ADMIN_UNKNOWN", "SETUP_AI_ADMIN", "SETUP_AI_ANSWERS_REFUSED", "SETUP_AI_AT_STEP", "SETUP_AI_BEFORE_STEPS",
    "SETUP_AI_BUG",
    "SETUP_AI_BUG_STOP", "SETUP_AI_CLOSED", "SETUP_AI_CLOSED_HELD", "SETUP_AI_CONFIG", "SETUP_AI_DENIED",
    "SETUP_AI_DENIED_HIDDEN", "SETUP_AI_DENIED_LINE", "SETUP_AI_DENIED_QUESTION", "SETUP_AI_DENIED_SUDO",
    "SETUP_AI_DONE", "SETUP_AI_DONE_FAILS", "SETUP_AI_DONE_FAILS_HERS", "SETUP_AI_DONE_NOT_RUN",
    "SETUP_AI_ENDED", "SETUP_AI_ENDED_OUTSIDE", "SETUP_AI_EXTRAS",
    "SETUP_AI_FILES", "SETUP_AI_FORM", "SETUP_AI_FOR_TERMINAL", "SETUP_AI_HOME", "SETUP_AI_IN_SESSION",
    "SETUP_AI_IN_USE", "SETUP_AI_NOT_AT_DESK", "SETUP_AI_NOT_BUILT", "SETUP_AI_NO_BOX", "SETUP_AI_NO_BOX_WINDOW",
    "SETUP_AI_NO_LAST_WORD",
    "SETUP_AI_NO_QUESTION", "SETUP_AI_NO_TOOLS", "SETUP_AI_NO_WINDOW", "SETUP_AI_NO_WINDOW_SAYS", "SETUP_AI_SANDBOX",
    "SETUP_AI_SCREEN", "SETUP_AI_SCREEN_AGAIN", "SETUP_AI_SCREEN_OTHER", "SETUP_AI_STARTED", "SETUP_AI_STEP_STOP",
    "SETUP_AI_STOPPED", "SETUP_AI_STOP_HERE", "SETUP_AI_TERMINAL_FLAG", "SETUP_AI_UNFIT", "SETUP_AI_WINDOW_SAYS",
    "SETUP_WINDOW_ADMIN_BOX",
    "SETUP_WINDOW_ADMIN_LATER", "SETUP_WINDOW_ADMIN_LATER_SETUP", "SETUP_WINDOW_ADMIN_NOTHING_RAN",
    "SETUP_WINDOW_ADMIN_NOT_NOW_STOPS",
    "SETUP_WINDOW_ADMIN_REFUSED",
    "SETUP_WINDOW_ADMIN_REFUSED_TITLE", "SETUP_WINDOW_BUILDING", "SETUP_WINDOW_BUILD_FAILED",
    "SETUP_WINDOW_CAPTCHA_PASTE", "SETUP_WINDOW_CLOSED", "SETUP_WINDOW_DEPLOY_LEAD", "SETUP_WINDOW_DEPLOY_OLD", "SETUP_WINDOW_DEPLOY_TITLE",
    "SETUP_WINDOW_DEPLOY_YES", "SETUP_WINDOW_DRY_RUN", "SETUP_WINDOW_ENDED_OUTSIDE", "SETUP_WINDOW_FINISHES_FIRST",
    "SETUP_WINDOW_HANDED", "SETUP_WINDOW_HER_CONFIG", "SETUP_WINDOW_HER_CONFIG_DONE",
    "SETUP_WINDOW_HER_CONFIG_LATER", "SETUP_WINDOW_HER_CONFIG_LEAD", "SETUP_WINDOW_HER_CONFIG_OLD",
    "SETUP_WINDOW_HER_CONFIG_TITLE", "SETUP_WINDOW_HER_CONFIG_YES", "SETUP_WINDOW_HER_RESTART",
    "SETUP_WINDOW_HOME_AREA", "SETUP_WINDOW_KEPT", "SETUP_WINDOW_KEPT_LEAD", "SETUP_WINDOW_KEYCHAIN_LOCKED",
    "SETUP_WINDOW_KEY_ASK", "SETUP_WINDOW_LATER", "SETUP_WINDOW_LATER_BUTTON", "SETUP_WINDOW_LOCK_FINISHES",
    "SETUP_WINDOW_LOCK_HOW", "SETUP_WINDOW_LOCK_PIN", "SETUP_WINDOW_LOCK_RETRY", "SETUP_WINDOW_LOCK_RETRY_TITLE", "SETUP_WINDOW_LOGIN_AGAIN",
    "SETUP_WINDOW_LOGIN_AGAIN_TITLE", "SETUP_WINDOW_LOGIN_NO_TIME", "SETUP_WINDOW_LOGIN_UNTIL", "SETUP_WINDOW_NOT_BUILT", "SETUP_WINDOW_NOT_NOW", "SETUP_WINDOW_NOT_OPENED",
    "SETUP_WINDOW_NO_BOX", "SETUP_WINDOW_NO_BOX_TERMINAL", "SETUP_WINDOW_NO_PROGRAM", "SETUP_WINDOW_NO_TOOLS",
    "SETUP_WINDOW_OFFER_LEAD", "SETUP_WINDOW_OFFER_TITLE", "SETUP_WINDOW_OPEN", "SETUP_WINDOW_OPENING",
    "SETUP_WINDOW_PAGE_UNREAD", "SETUP_WINDOW_RUN_IT", "SETUP_WINDOW_SECURITY_YES", "SETUP_WINDOW_SHARE_CLOSE_MORE",
    "SETUP_WINDOW_SHARE_CLOSE_NO", "SETUP_WINDOW_SHARE_CLOSE_TITLE", "SETUP_WINDOW_SHARE_CLOSE_YES",
    "SETUP_WINDOW_SKIP", "SETUP_WINDOW_SPOTIFY_OLD", "SETUP_WINDOW_STOPPED_HERE", "SETUP_WINDOW_STOP_AGAIN",
    "SETUP_WINDOW_STOP_AGAIN_AI", "SETUP_WINDOW_STOP_HERE", "SETUP_WINDOW_TRY_AGAIN", "SETUP_WINDOW_WORD_ASK",
]
# fmt: on

# building it: once, from the checkout, at the desk only
SETUP_WINDOW_BUILDING = "Building the setup window, once (about a minute)."
SETUP_WINDOW_DRY_RUN = (
    "The setup window, at the desk only: built once from tools/setup-window, then opened:\n  $ {command}"
)
SETUP_WINDOW_NOT_BUILT = "The setup window could not be built ({why}): setup goes on here, in Terminal."
SETUP_WINDOW_NO_TOOLS = "the Command Line Tools are not installed"  # xcode-select -p: none (never its install prompt)
SETUP_WINDOW_BUILD_FAILED = "its build failed"
SETUP_WINDOW_NO_PROGRAM = "its build made no program"
SETUP_WINDOW_PAGE_UNREAD = "a page file could not be read"
# opening it, and what Terminal says while it is open
SETUP_WINDOW_OPEN = (
    "Setup's questions and steps: in the Estia Setup window (each line shows here too). Closing it goes on here, in "
    "Terminal."
)
SETUP_WINDOW_OPENING = "Opening the setup window (up to 20 seconds)."  # before the wait for it (READY_SECONDS)
SETUP_WINDOW_NOT_OPENED = "The setup window did not open: setup goes on here, in Terminal."
SETUP_WINDOW_CLOSED = "The window closed: setup goes on here, in Terminal."
# in the window, once setup is done with it (an answer that does not fit its screen): its last screen, its first
# paragraph the heading (owner, 29 Oct: "you have to say that!!"; the window brings that Terminal window to the front,
# yet macOS may keep the window in front, SECURITY.md: so the screen names its button, never "It is in front now.")
SETUP_WINDOW_HANDED = (
    "Setup goes on in the Terminal window you started it in.\n\n"
    "It asks the **same question** there. Not in front? **Switch to Terminal**."
)
# a stop's screen in the window, after what was found, why and the fix: how to go on, the command with Copy
# the window could not run the command with admin rights (macOS's box could not ask, or the wrapper failed before it:
# review W10): nothing ran, and Terminal's sudo goes on (review ADM-4); the window's last screen, its first paragraph
# the heading, and Terminal's line
SETUP_WINDOW_NO_BOX = (
    "Setup goes on in the Terminal window you started it in.\n\n"
    "The window could not run this command with admin rights: Terminal asks for your Mac password there. "
    "Not in front? **Switch to Terminal**."
)
SETUP_WINDOW_NO_BOX_TERMINAL = (
    "The window could not run this command with admin rights: setup goes on here, in Terminal."
)
SETUP_WINDOW_STOP_AGAIN = (
    "Setup stopped here; every answer is kept. To go on, run it again in Terminal:\n$ scripts/setup.sh"
)
# every step in the window (owner, 29 Oct: "can we have Terminal in the window?"): admin rights from macOS's own box,
# never a password typed in the window (owner, 27 Oct)
SETUP_WINDOW_ADMIN_BOX = "Admin rights: asked by macOS in its own box, never in this window."
SETUP_WINDOW_ADMIN_REFUSED_TITLE = "Admin rights: not given"
# its lead: what the rights were for (the step's ADMIN_WHY_* line), that nothing ran, and for Terminal later only a
# command that runs there as typed (the step's own script, or one you would type): never a `sudo -n` form
SETUP_WINDOW_ADMIN_NOTHING_RAN = "Nothing ran. Try again asks macOS again; Not now leaves this command undone."
# a step setup cannot go on without (Ollama's, the move): its Not now is a stop (round 2)
SETUP_WINDOW_ADMIN_NOT_NOW_STOPS = (
    "Nothing ran. Try again asks macOS again; Not now stops setup here, every answer kept."
)
SETUP_WINDOW_ADMIN_REFUSED = "{why}\n\n{nothing_ran}{later}"
# run by the friend, never by their AI (round 2, words-20): it asks for the Mac password where it runs
SETUP_WINDOW_ADMIN_LATER = "\n\nLater, in your own Terminal window (it asks for your Mac password there):\n$ {command}"
# setup itself (calls, her own Ollama: review W04), which the friend's AI may start too, and at the desk asks in
# macOS's own box (fix review, setup-code-03), in the window's one form of what it asks (second fix review,
# words-admin-later-asks-what); {command}: W12's form, `setup_window.setup_again_command`
SETUP_WINDOW_ADMIN_LATER_SETUP = (
    "\n\nLater, start setup again (admin rights, asked by macOS in its own box): ask your AI, or run this in Terminal:"
    "\n$ {command}"
)
SETUP_WINDOW_TRY_AGAIN = "Try again"
SETUP_WINDOW_NOT_NOW = "Not now"
# the line under macOS's own box ("Estia Setup wants to make changes."), one per step: what the admin rights are for
ADMIN_WHY = "Admin rights for this setup step."
ADMIN_WHY_OLLAMA = "Admin rights for Ollama's background service."
ADMIN_WHY_OLLAMA_HERS = "Admin rights for her own macOS account and her Ollama."
ADMIN_WHY_MOVE = "Admin rights to make her own macOS account and copy her files."
# true at its first box too (round 2, words-04): the security step reads first; calls also wait for her voice (read
# as her), in plain words (words review, 30 Oct: no "her release")
ADMIN_WHY_SECURITY = (
    "Admin rights for the security step: to read the Mac's settings and change only what you say yes to."
)
ADMIN_WHY_CALLS = (
    "Admin rights to add her voice and the call tools to her install, then to check that her voice is ready."
)
ADMIN_WHY_HER = "Admin rights to run her commands as her own macOS account."
# Stop here while a command runs with admin rights: root's command cannot be stopped halfway (only the command: the
# rest of its step does not run, as after a Ctrl-C in Terminal)
SETUP_WINDOW_FINISHES_FIRST = "This command finishes first; setup stops after it."
# Stop here between her stop and her start (the security step's Registration Lock): she always runs again first
SETUP_WINDOW_LOCK_FINISHES = "Her stop, her PIN and her start finish first; setup stops after them."
# the window ended (a crash: it refuses to close meanwhile) while root ran a command: its result was never read
SETUP_ADMIN_UNKNOWN = (
    "✗ Setup window: it ended while a command ran with admin rights: that command's result is not known.\n"
    "  Fix: run scripts/setup.sh again; it checks every step first."
)
# a command setup offers (Terminal: "Press Enter to run it"), with Copy; an admin one says where macOS asks
SETUP_WINDOW_OFFER_TITLE = "Run this command?"
SETUP_WINDOW_OFFER_LEAD = "{what}$ {command}{admin}"
SETUP_WINDOW_RUN_IT = "Run it"
SETUP_WINDOW_SKIP = "Skip"
SETUP_WINDOW_STOP_HERE = "Stop here"
# her config in her own macOS account, in the window (no nano there): the lines on a screen, added as her on a yes
SETUP_WINDOW_HER_CONFIG_TITLE = "Her config"
SETUP_WINDOW_HER_CONFIG = "Her config is hers alone: these lines are added for her, with a copy of the old one kept."
SETUP_WINDOW_HER_CONFIG_LEAD = "{lines}\n\n" + SETUP_WINDOW_HER_CONFIG
SETUP_WINDOW_HER_CONFIG_YES = "Add them"
SETUP_WINDOW_LATER_BUTTON = "Later"
SETUP_WINDOW_HER_CONFIG_DONE = "✓ Her config: the lines are in."
SETUP_WINDOW_HER_CONFIG_LATER = (
    "Her config: add the lines above as her later, in Terminal:\n$ /usr/bin/sudo /usr/local/estia/bin/estia config"
)
SETUP_WINDOW_HER_CONFIG_OLD = (
    "Her release cannot add them for her yet (an update brings it). Add them as her, in Terminal:\n"
    "$ /usr/bin/sudo /usr/local/estia/bin/estia config"
)
SETUP_WINDOW_HER_RESTART = "Her config changed: a restart reads it."
# her release's update (estia-deploy --ask-lines): its review question on a screen, what it lists in the More fold
SETUP_WINDOW_DEPLOY_TITLE = "Her update"
SETUP_WINDOW_DEPLOY_YES = "Deploy it"
SETUP_WINDOW_DEPLOY_LEAD = "The list of what changes is under More."  # its question says "the list above" (round 2)
# root-run tools from before --ask-lines (no --update-tools since): the deploy asks only in Terminal
SETUP_WINDOW_DEPLOY_OLD = (
    "Her root-run tools ask only in Terminal (an update of them brings the window's way). Run this there, then "
    "scripts/setup.sh again:\n$ {command}"
)
# her release from before the window's Spotify sign-in (its address asked with no line end): Terminal only
SETUP_WINDOW_SPOTIFY_OLD = (
    "Her release asks for Spotify's address only in Terminal (an update brings the window's way). Run this there "
    "after setup:\n$ {command}"
)
# the share's close question (share.py's CLOSE_ASK and its kin, the screen's lead); what closing changes, in its fold
SETUP_WINDOW_SHARE_CLOSE_TITLE = "Folders in your home every account may open"
SETUP_WINDOW_SHARE_CLOSE_YES = "Close them"
SETUP_WINDOW_SHARE_CLOSE_NO = "Keep them"
SETUP_WINDOW_SHARE_CLOSE_MORE = (
    "Closed, her account and the Mac's own accounts that programs run as can no longer open them;\n"
    "a guest file share or a web page served from them stops too.\n\n"
    "You keep full access, and people who log in on this Mac keep what they can open today.\n"
    "Nothing in them changes; her share and Ollama keep working."
)
# Signal's step, where Terminal reads Enter or q: the PIN refused by Signal, and Signal's wait between registrations
SETUP_WINDOW_LOCK_RETRY_TITLE = "Registration Lock: not set"
SETUP_WINDOW_LOCK_RETRY = (  # {reason}: signal-cli's, which only the log had (review S03)
    "Signal did not set it ({reason}).\n\nTry again now, or skip it: the **security step** offers the lock again."
)
SETUP_WINDOW_LOGIN_AGAIN_TITLE = "Signal limits registrations for now"
# {limit}: Signal's wait, in local time (c-13, confirm-2), or that it named none (review S09); then what Stop here
# keeps: never the Account ID and the Recovery key, which a run again asks for (review S07, SETUP_LOGIN_STOPPED)
SETUP_WINDOW_LOGIN_AGAIN = (
    "{limit}\n\nStop here keeps your other answers; the Account ID and the Recovery key are **typed again**."
)
SETUP_WINDOW_LOGIN_UNTIL = "Each try is one more registration: **wait** until {when}, then try again."
SETUP_WINDOW_LOGIN_NO_TIME = "Signal named no time. Each try is one more registration: run setup again later."
# a locked login keychain (a web key, Signal's Recovery key), at the desk: macOS asks in its own box when setup's write
# reaches it (Terminal's own line names the Mac password typed there)
SETUP_WINDOW_KEYCHAIN_LOCKED = "Your login keychain is **locked**: macOS asks for its password in its own box."
# in the window, before the plan: the answers kept from an earlier run, each with Change beside it
SETUP_WINDOW_KEPT = "Kept from your last run"
SETUP_WINDOW_KEPT_LEAD = "**Change** asks one again; the others are not asked."
# each face its own words (walk-through, 27 Oct): what Terminal's keys do there, the window's buttons and boxes do here
SETUP_WINDOW_LATER = "Later"  # a key or an ID left for later, as Enter leaves it in Terminal
SETUP_WINDOW_CAPTCHA_PASTE = "  4. Paste it below, then Continue. It is **never stored**."
SETUP_WINDOW_KEY_ASK = "Her Recovery key, typed as Signal shows it, # and = too."
SETUP_WINDOW_WORD_ASK = "Setup looks for it by itself every {seconds} seconds."  # Stop here is on its screen
SETUP_WINDOW_HOME_AREA = (
    "The street and the city, so a route from home starts there.\n"
    "Street and city are enough, **no house number**.\n\n"
    "**One lookup** at OpenStreetMap, with the street and the city only. Both left empty: none."
)
# the security step's Yes in the window (second deep scan: only its No line was there); Terminal's Yes line names the
# Mac password, which no window screen does (owner, 27 Oct): macOS's own box asks (owner, 29 Oct)
SETUP_WINDOW_SECURITY_YES = "Yes: admin rights, asked by macOS in its own box when needed."
# the new PIN's screen (signal-lock-pin-window-no-skip): its rules; its Later leaves the lock to the security step
SETUP_WINDOW_LOCK_PIN = "4 to 64 characters, no spaces. Keep it in your password manager."
SETUP_WINDOW_LOCK_HOW = (  # Yes and No each a paragraph: the page joins a paragraph's lines into one
    "Yes: a **new PIN** at the Signal step, typed twice (4 to 64 characters, no spaces).\n\n"
    "No: the security step asks again."
)

# setup started by an AI's command tool (no terminal): its window is the only face, so it runs only at the desk; the
# same words in scripts/setup.sh, said there before anything installs
SETUP_AI_NOT_AT_DESK = (
    "✗ Setup: its window opens only on this Mac's own screen, and this session has none (a remote login, or no one "
    "signed in at the Mac). Fix: start it at the Mac itself, or run scripts/setup.sh in your own Terminal window."
)
SETUP_AI_IN_SESSION = (
    "✗ Setup: inside screen, tmux or herdr its window never opens. Fix: start it outside them at the Mac itself, or run "
    "scripts/setup.sh in your own Terminal window."
)
# what the AI reads of a run it started (setup_ai's channel): setup's own fixed words, never an answer, a code, a PIN,
# a key, her number, an account ID, About you, the home area, a folder or a command's output; one line each
SETUP_AI_STARTED = (
    "Setup runs in the Estia Setup window on this Mac's screen, often for an hour or more (the first time it builds "
    "the window first: about a minute). Every answer is typed there, the Mac password only in macOS's own box. Only setup's steps "
    "show here, and in ~/.config/estia-setup/ai-run.log. If this command stops waiting, setup goes on in its window: "
    "never start it again meanwhile."
)
SETUP_AI_SCREEN = "In the window: {title} [{id}]"  # the question list's own title, never filled in
SETUP_AI_SCREEN_AGAIN = "In the window: {title} [{id}], asked again"
SETUP_AI_SCREEN_OTHER = "a screen of this step"  # a screen whose title is not one of the list's fixed ones
SETUP_AI_ADMIN = "In macOS's own box: {why}"  # the step's ADMIN_WHY line, once a step
SETUP_AI_AT_STEP = "at step {n} of {m} ({step})"
SETUP_AI_BEFORE_STEPS = "before its first step"
SETUP_AI_WINDOW_SAYS = "the window shows why."
SETUP_AI_NO_WINDOW_SAYS = "its window was not open yet, so nothing shows why; a run in your own Terminal window does."
SETUP_AI_STOPPED = "✗ Setup stopped {where}: {why} Every answer is kept; to go on, run scripts/setup.sh again."
SETUP_AI_ENDED = (
    "✗ Setup ended {where} (exit code {code}): {why} Every answer is kept; to go on, run scripts/setup.sh again."
)
SETUP_AI_CLOSED = (
    "✗ The Estia Setup window was closed: setup stopped, every answer is kept. To go on, run scripts/setup.sh again."
)
# closed between her stop and her start (the security step's Registration Lock): a re-run leaves a stopped bridge
# stopped, so her start comes first
SETUP_AI_CLOSED_HELD = (
    "✗ The Estia Setup window was closed during her Registration Lock: she stays stopped. Fix: start her in your own "
    "Terminal window ({start}), then run scripts/setup.sh again; every answer is kept."
)
SETUP_AI_STOP_HERE = (
    "Setup stopped in its window (Stop here): every answer is kept. To go on, run scripts/setup.sh again."
)
SETUP_AI_ENDED_OUTSIDE = (
    "✗ Setup was stopped from outside its window (a logout, or a program that ended it): every answer is kept. To go "
    "on, run scripts/setup.sh again."
)
SETUP_AI_NO_LAST_WORD = (
    "✗ Setup ended without its last word: the Estia Setup window shows where it was. Every answer is kept; to go on, "
    "run scripts/setup.sh again."
)
SETUP_AI_DONE = "✓ Setup ended: its check shows no ✗."
SETUP_AI_DONE_FAILS = "✗ Setup ended: its check shows a ✗, listed in the window; scripts/doctor.sh names it again."
# once she moved, scripts/doctor.sh only points on to her doctor; admin rights not given: no ✗ listed (c-3, round 3)
SETUP_AI_DONE_FAILS_HERS = (
    "✗ Setup ended: its check shows a ✗, listed in the window; /usr/bin/sudo /usr/local/estia/bin/estia doctor, in "
    "your own Terminal window, names it again."
)
SETUP_AI_DONE_NOT_RUN = (
    "! Setup ended; its check did not run, as admin rights were not given. Later, in your own Terminal window: "
    "/usr/bin/sudo /usr/local/estia/bin/estia doctor"
)
SETUP_AI_FOR_TERMINAL = " Commands left for Terminal after setup: {k}, listed in the window."
SETUP_AI_BUG = (
    "✗ Setup: an error in its own code ({kind} at {where}); every answer is kept. Fix: run scripts/setup.sh again; if "
    "it comes back, report it."
)
# the window cannot go on (no Terminal to hand over to): its stop screen, and the AI's line in the same words
SETUP_AI_BUG_STOP = (
    "✗ Setup: an error in its own code stopped it; every answer is kept.\n"
    "  Fix: run scripts/setup.sh again; if it comes back, report it."
)
# a step ended setup with its own ✗ line in the window's log and no screen of its own (a return, an exit)
SETUP_AI_STEP_STOP = (
    "✗ Setup: this step did not finish, and its own ✗ line says why; every answer is kept.\n"
    "  Fix: do what that line asks, then run scripts/setup.sh again."
)
SETUP_AI_UNFIT = (
    "✗ Setup window: it gave an answer that does not fit its screen, so setup stopped; every answer is kept.\n"
    "  Fix: run scripts/setup.sh again."
)
SETUP_AI_NO_BOX = (
    "✗ Setup window: it could not run this command with admin rights, so nothing ran and setup stopped; every "
    "answer is kept.\n"
    "  Fix: run scripts/setup.sh again, or this in your own Terminal window: {command}"
)
# the AI's line for a command that holds an answer (her mail sign-in's mailbox address): the window shows it whole
SETUP_AI_NO_BOX_WINDOW = (
    "✗ Setup window: it could not run this command with admin rights, so nothing ran and setup stopped; every "
    "answer is kept.\n"
    "  Fix: run scripts/setup.sh again, or the command its window shows in your own Terminal window."
)
SETUP_AI_FORM = (
    "✗ Admin rights: a command in a form the window does not run ({program}), so setup stopped; every answer is "
    "kept.\n  Fix: run scripts/setup.sh in your own Terminal window, and report it."
)
SETUP_AI_DENIED = (
    "✗ Setup: {what}, which a run started without a terminal never does; every answer is kept.\n"
    "  Fix: run scripts/setup.sh in your own Terminal window, and report it."
)
SETUP_AI_DENIED_LINE = "it asked for a line in Terminal"
SETUP_AI_DENIED_HIDDEN = "it asked for a hidden answer in Terminal"
SETUP_AI_DENIED_SUDO = "it would have run sudo itself"
SETUP_AI_DENIED_QUESTION = "it asked a question before its window opened"
# a run setup cannot take without a terminal: one line, before anything runs (exit 2)
SETUP_AI_TERMINAL_FLAG = (
    "✗ Setup: {flag} asks in Terminal. Fix: run scripts/setup.sh {flag} in your own Terminal window."
)
SETUP_AI_EXTRAS = (
    "✗ Setup: --extras asks in Terminal. Fix: run scripts/setup.sh again (its window lists the Extra Features), or "
    "scripts/setup.sh --extras in your own Terminal window."
)
SETUP_AI_FILES = "✗ Setup: --answers and --config are for a run in your own Terminal window."
SETUP_AI_SANDBOX = (
    "✗ Setup: it runs inside a sandbox, which keeps it from its own folders and its window. Fix: run scripts/setup.sh "
    "outside the sandbox."
)
SETUP_AI_HOME = "✗ Setup: HOME is not your account's home folder. Fix: run scripts/setup.sh with your own HOME."
SETUP_AI_IN_USE = (
    "✗ Setup is already running on this Mac (in its window, or in a Terminal window): it goes on there. After it, run "
    "scripts/doctor.sh."
)
SETUP_AI_CONFIG = "✗ Config: it does not load, so setup changes nothing. Fix: scripts/doctor.sh names the problem."
# in Terminal a refused answers file is one line and setup goes on keeping nothing; an AI-run stops instead (nothing
# would be kept, and no one told): its path names the account, so neither it nor the reason is said here
SETUP_AI_ANSWERS_REFUSED = (
    "✗ Setup: its answers file cannot be used, so nothing of this run would be kept "
    "(~/.config/estia-setup/answers.toml). Fix: run scripts/setup.sh in your own Terminal window: it says why."
)
SETUP_AI_NO_QUESTION = "✗ Setup: --redo names a question setup does not know. The questions: {ids}."
SETUP_AI_NO_TOOLS = (
    "✗ Setup window: it could not be built (the Command Line Tools are not installed).\n"
    "  Fix: xcode-select --install (macOS asks on its own screen), then run scripts/setup.sh again."
)
SETUP_AI_NOT_BUILT = (
    "✗ Setup window: it could not be built ({why}).\n  Fix: run scripts/setup.sh in your own Terminal window."
)
SETUP_AI_NO_WINDOW = (
    "✗ Setup window: it did not open.\n  Fix: run scripts/setup.sh again, or in your own Terminal window."
)
# in the window, an AI-run's stop screens: how to go on (in place of SETUP_WINDOW_STOP_AGAIN), Stop here, and a stop
# from outside the window (SIGTERM or SIGHUP to setup itself); a new Terminal window opens in the home folder, so the
# command names the folder, as page.js's own stop line does (words review, 30 Oct)
SETUP_WINDOW_STOP_AGAIN_AI = (  # the end line's words and command (page.js WORDS.ended, review W12; dialogue-06)
    "Setup stopped here; every answer is kept. To go on, start it again: ask your AI, or run this in Terminal:\n"
    "$ {command}"
)
SETUP_WINDOW_STOPPED_HERE = "✗ Setup: stopped here, as you asked; every answer is kept."
SETUP_WINDOW_ENDED_OUTSIDE = "✗ Setup: it was stopped from outside the window; every answer is kept."
