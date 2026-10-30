"""Setup's lines: what the wizard asks and what it says back (scripts/setup.sh)."""

from __future__ import annotations

from local_assistant.prompts.ollama import OLLAMA_APP_LIMIT_LOGIN, OLLAMA_APP_LIMIT_PROMPTS

# fmt: off
__all__ = [
    "HER_CONFIG_EDIT", "NANO_SAVE", "SETUP_ACCOUNT_ADVICE", "SETUP_ACCOUNT_ASK", "SETUP_ACCOUNT_UNKNOWN",
    "SETUP_ALREADY_MOVED", "SETUP_ANSWERS_FORMAT", "SETUP_ANSWERS_IN_USE", "SETUP_ANSWERS_LARGE", "SETUP_ANSWERS_LINK",
    "SETUP_ANSWERS_NONE",
    "SETUP_ANSWERS_NOT_FILE", "SETUP_ANSWERS_NOT_YOURS", "SETUP_ANSWERS_REFUSED", "SETUP_ANSWERS_UNREAD",
    "SETUP_ANSWERS_WHERE", "SETUP_ASKS", "SETUP_BACK_HOW", "SETUP_BACK_HOW_MENU", "SETUP_BACK_KEPT",
    "SETUP_BACK_OPTION", "SETUP_CAPTCHA", "SETUP_CAPTCHA_ASK", "SETUP_CAPTCHA_STEPS", "SETUP_CHECK_FAILED",
    "SETUP_CHECK_HERS", "SETUP_CHECK_HERS_LEAD", "SETUP_CHECK_NOT_RUN",
    "SETUP_CHORDS_ADDED", "SETUP_CHORDS_HERS", "SETUP_CHORDS_NOT_ADDED", "SETUP_CHORDS_NO_CONFIG", "SETUP_CHORDS_ON",
    "SETUP_DISK_FULL", "SETUP_DISK_SHORT", "SETUP_DONE", "SETUP_DOWNLOAD", "SETUP_EXTRAS", "SETUP_EXTRAS_ASK",
    "SETUP_EXTRAS_ASK_LEFT", "SETUP_EXTRAS_HERS", "SETUP_EXTRAS_LEFT", "SETUP_EXTRAS_RECOMMENDED", "SETUP_EXTRAS_YOURS", "SETUP_GUM_CHANGED", "SETUP_GUM_DOWNLOADING", "SETUP_GUM_DRY_RUN",
    "SETUP_GUM_FAILED", "SETUP_GUM_NOT_FILE", "SETUP_GUM_NOT_PINNED", "SETUP_GUM_NOT_YOURS", "SETUP_GUM_NO_PROGRAM",
    "SETUP_GUM_PLAIN", "SETUP_GUM_READY", "SETUP_HER_OPENS", "SETUP_HER_RESTART", "SETUP_KEPT", "SETUP_KEPT_PROFILE",
    "SETUP_KNOWN_NUMBER", "SETUP_KNOWN_NUMBER_ADVICE", "SETUP_KNOWN_NUMBER_ASK", "SETUP_LANGUAGE", "SETUP_LANGUAGE_ASK",
    "SETUP_LANGUAGE_ASK_MAC", "SETUP_LANGUAGE_CHOSEN", "SETUP_LANGUAGE_DRY_RUN", "SETUP_LANGUAGE_DRY_RUN_MAC",
    "SETUP_LANGUAGE_UNKNOWN", "SETUP_LOCK_AGAIN", "SETUP_LOCK_ASK", "SETUP_LOCK_HOW", "SETUP_LOCK_PIN",
    "SETUP_LOCK_RETRY", "SETUP_LOGIN_AGAIN", "SETUP_LOGIN_AGAIN_LATER", "SETUP_LOGIN_DONE", "SETUP_LOGIN_FAILED",
    "SETUP_LOGIN_HERE", "SETUP_LOGIN_LIMITED", "SETUP_LOGIN_NO_ANSWER", "SETUP_LOGIN_STOPPED", "SETUP_LOOK_AGAIN",
    "SETUP_MACOS_OLD", "SETUP_MAPS_ADDED", "SETUP_MAPS_HERS", "SETUP_MAPS_NOT_ADDED", "SETUP_MAPS_NO_CONFIG",
    "SETUP_MAPS_ON",
    "SETUP_MODEL_AFTER_OLLAMA", "SETUP_MODEL_NOT_MADE", "SETUP_MODEL_OTHER_BUILD", "SETUP_MODEL_PIN_HERS",
    "SETUP_MODEL_PIN_YOURS", "SETUP_MODEL_PROMPT_BY_HAND", "SETUP_MODEL_REMADE", "SETUP_MODEL_REMADE_PIN",
    "SETUP_MODEL_REMADE_UNLISTED", "SETUP_MODEL_REMAKE", "SETUP_MODEL_REMAKE_ADVICE", "SETUP_MODEL_REMAKE_AFTER_OLLAMA",
    "SETUP_MODEL_REMAKE_ASK", "SETUP_MODEL_REMAKE_FAILED", "SETUP_MODEL_REMAKE_LATER",
    "SETUP_MODEL_SMALL_PROMPT", "SETUP_MODEL_STOP_HERS", "SETUP_MODEL_STOP_YOURS", "SETUP_MOVE", "SETUP_MOVE_APP",
    "SETUP_MOVE_ASK", "SETUP_MOVE_ASK_SSH", "SETUP_MOVE_DRY_RUN", "SETUP_MOVE_FAILED", "SETUP_MOVE_MORE",
    "SETUP_MOVE_MORE_APP", "SETUP_MOVE_MORE_TYPED", "SETUP_MOVE_NOT_RUNNING", "SETUP_MOVE_NO_SIGNAL",
    "SETUP_MOVE_OLDER", "SETUP_MOVE_OLDER_APP", "SETUP_MOVE_OLDER_ASK", "SETUP_MOVE_OLDER_ASK_SSH", "SETUP_MOVE_REFUSED",
    "SETUP_MOVE_SKIPPED", "SETUP_MOVE_SSH_ADVICE", "SETUP_NAME",
    "SETUP_NAME_ASK", "SETUP_NAME_BAD", "SETUP_NAME_CHOSEN", "SETUP_NAME_DRY_RUN", "SETUP_NAME_LEAD", "SETUP_NAME_LIST",
    "SETUP_NAME_OTHER", "SETUP_NAME_RULE", "SETUP_NAME_SHOWN", "SETUP_NAME_SHOWN_HE", "SETUP_NO", "SETUP_NOT_ADMIN", "SETUP_NOT_KEPT",
    "SETUP_NOT_ROOT",
    "SETUP_NO_BACK_HERE", "SETUP_NO_QUESTION", "SETUP_NO_TERMINAL", "SETUP_NO_VENV", "SETUP_NUCLEAR",
    "SETUP_NUCLEAR_TYPED", "SETUP_NUMBER", "SETUP_NUMBERLESS_DRY_RUN", "SETUP_NUMBER_ASK", "SETUP_NUMBER_BAD",
    "SETUP_OFFER_HOW", "SETUP_OFFER_NOT_RUN", "SETUP_OFFER_SKIPPED", "SETUP_OLD_PIN_ASK", "SETUP_OLLAMA_GOES_ON",
    "SETUP_OPENING", "SETUP_OS_ERROR", "SETUP_OS_ERROR_FULL", "SETUP_OS_ERROR_HIDDEN",
    "SETUP_OS_ERROR_NO_PATH", "SETUP_OS_ERROR_OTHER", "SETUP_OWNER_IS_HER", "SETUP_PASTE_KEPT", "SETUP_PERSONALITY", "SETUP_PERSONALITY_HERS",
    "SETUP_PINNED", "SETUP_PRESS_ENTER", "SETUP_PROFILE_LATER", "SETUP_PROFILE_NAME", "SETUP_PROJECTS",
    "SETUP_PROJECTS_ADDED", "SETUP_PROJECTS_ASK", "SETUP_PROJECTS_BAD_LEVEL", "SETUP_PROJECTS_CHOSEN",
    "SETUP_PROJECTS_DRY_RUN", "SETUP_PROJECTS_FOLDER", "SETUP_PROJECTS_FOLDER_RECOMMENDED", "SETUP_PROJECTS_HERS",
    "SETUP_PROJECTS_HOME", "SETUP_PROJECTS_LEVEL", "SETUP_PROJECTS_LEVELS", "SETUP_PROJECTS_LEVEL_RECOMMENDED",
    "SETUP_PROJECTS_LINK", "SETUP_PROJECTS_LINK_ON_THE_WAY", "SETUP_PROJECTS_NOT_ADDED", "SETUP_PROJECTS_NO_CONFIG",
    "SETUP_PROJECTS_NO_FOLDER", "SETUP_PROJECTS_ON", "SETUP_PROJECTS_OUTSIDE", "SETUP_PROJECTS_RECOMMENDED",
    "SETUP_PROJECTS_REFUSED", "SETUP_PROJECTS_SHARE", "SETUP_PULLING", "SETUP_PULL_ENDED", "SETUP_PULL_FAILED",
    "SETUP_PULL_LINE_LONG", "SETUP_RECOMMENDED", "SETUP_REDO_NOTHING", "SETUP_REDO_PROFILE", "SETUP_REDO_SIGNAL",
    "SETUP_ROSETTA", "SETUP_SECURITY",
    "SETUP_SECURITY_ASK",
    "SETUP_SECURITY_DRY_RUN", "SETUP_SECURITY_FAILED", "SETUP_SECURITY_MORE", "SETUP_SECURITY_NO",
    "SETUP_SECURITY_PASSWORD",
    "SETUP_SHARE_FAILED", "SETUP_SIGNAL_WAITING", "SETUP_SMALLER_MODEL", "SETUP_SMALLER_MODEL_ADVICE", "SETUP_SMALLER_MODEL_ASK",
    "SETUP_SMALLER_MODEL_NO", "SETUP_SMS_ASK", "SETUP_SMS_SENT", "SETUP_SSH_SCREEN", "SETUP_SSH_SCREEN_STOP",
    "SETUP_STEP", "SETUP_STEPS", "SETUP_STEPS_TIME", "SETUP_STEPS_TIME_CALLS", "SETUP_STEP_CALLS", "SETUP_STILL_WORKING", "SETUP_STOPPED", "SETUP_STYLE",
    "SETUP_STYLE_ASK", "SETUP_STYLE_CHOSEN", "SETUP_STYLE_DRY_RUN", "SETUP_STYLE_HEARTH", "SETUP_STYLE_HERS",
    "SETUP_STYLE_HERS_DRY_RUN", "SETUP_STYLE_LEAD", "SETUP_STYLE_LIST", "SETUP_STYLE_PLAYFUL", "SETUP_STYLE_UNKNOWN",
    "SETUP_TOO_SMALL", "SETUP_TOTP", "SETUP_TOTP_ASK", "SETUP_USERNAME", "SETUP_USERNAME_FAILED", "SETUP_USERNAME_SET",
    "SETUP_USERNAME_STOPPED", "SETUP_VOICE", "SETUP_VOICES", "SETUP_VOICE_ASK", "SETUP_VOICE_UNKNOWN",
    "SETUP_WINDOW_MOVE", "SETUP_WINDOW_MOVE_OLDER", "SETUP_WINDOW_WORD_MISSING", "SETUP_WINDOW_WORD_MISSING_USERNAME",
    "SETUP_WORD", "SETUP_WORD_ASK", "SETUP_WORD_MISSING", "SETUP_WORD_MISSING_USERNAME", "SETUP_WORD_STOP",
    "SETUP_WORD_USERNAME", "SETUP_YES", "SETUP_YES_NO_AGAIN", "SIGNAL_LOGIN_ALLOWED", "SIGNAL_LOGIN_HERE",
    "SIGNAL_LOGIN_KEPT",
    "SIGNAL_LOGIN_LIMITED", "SIGNAL_LOGIN_NEXT", "SIGNAL_LOGIN_NOTHING", "SIGNAL_LOGIN_SWITCHED", "SIGNAL_LOGIN_TOTP",
    "SIGNAL_LOGIN_USERNAME",
]
# fmt: on

# setup (scripts/setup.sh), in the terminal; {name}: her name
SETUP_OPENING = (  # its first lines: the questions, then every step, in order, by SETUP_STEPS' names (round 2)
    "{name} setup: the questions first, before anything is installed.\n"
    "Then the steps: Ollama, her model, her Signal account, her config, always on,\n"
    "the security step, calls and Extra Features when picked, and the check.\n"
)
# Terminal, once, before the plan's first question (owner, 27 Oct: "should be able to go back"); Back holds within the
# plan only (second deep scan: "at any question" was untrue after it, where a b was read as a no)
SETUP_BACK_HOW = "Type b to go back one question, until setup starts installing; your answers are kept."
# gum's menus: typing b there does nothing, so its own words (Back is each list's last item; a typed line takes b)
SETUP_BACK_HOW_MENU = (
    "Back goes back one question until setup starts installing: the last item of a list, or b where you type. "
    "Your answers are kept."
)
# a b typed after the plan, where no Back is offered: said, and the question asked again (never read as an answer)
SETUP_NO_BACK_HERE = "No going back here: the installs have started."
SETUP_BACK_KEPT = "Your answer: {shown}. Enter keeps it."  # a question gone back to: its answer before
SETUP_BACK_OPTION = "Back"  # gum's menus: the last item, at a question of the plan with one before it
# a command the person would type (owner, 27 Oct: "pre-fill the command so that the user does not have to type"): it
# is shown, then this, and setup runs it (a program cannot type into Terminal's command line)
SETUP_PRESS_ENTER = "Press Enter to run it (or s to skip)"
SETUP_OFFER_SKIPPED = "  Skipped."  # an offer skipped (s, skip, n, no, q or the end of input)
SETUP_OFFER_HOW = "Enter runs it; s skips it."  # said after an answer that is neither, before it asks again
SETUP_OFFER_NOT_RUN = "✗ {program}: not run ({why})."  # a command that could not start (no such program)
# a question's recommendation (owner, 29 Oct): the window's pill on its option; in Terminal after the option's label
# ({label}: the option's; {advice}: ", " and its reason, or nothing), in gum's menus and plain lines' line
SETUP_RECOMMENDED = "{label} (recommended{advice})"
# a yes or no's two buttons in the window (a command setup runs for you: SETUP_PRESS_ENTER, in Terminal)
SETUP_YES = "Yes"
SETUP_YES_NO_AGAIN = "Type y or n (the keyboard may be on Greek: switch it to ABC)."  # anything else at a yes or no
SETUP_NO = "No"
# setup run as root (sudo scripts/setup.sh): its own folders would be root's; the same words as scripts/setup.sh's
SETUP_NOT_ROOT = "Run setup as yourself: it asks for admin rights when it needs them."
# scripts/setup.sh's own words, before anything: macOS 27 or later only (owner, 29 Oct; it runs on every Apple silicon
# Mac, M1 included)
# and an admin account (mac-ollama-standard-account, round 1): Homebrew, her own account and the security step need it
SETUP_NOT_ADMIN = (
    "Setup needs an admin account. Log in as an admin, or have an admin make this account one "
    "(System Settings → Users and Groups), then run setup again."
)
# harden.sh, or the security step in the window, ended with an error (stops-reruns-harden-exit-ignored)
SETUP_SECURITY_FAILED = "✗ Security: the step did not finish (its lines say why). Run scripts/harden.sh again."
# Terminal set to "Open using Rosetta" on an Apple silicon Mac (plat-14): it reports an Intel chip
SETUP_ROSETTA = (
    'Terminal runs under Rosetta here. Turn off "Open using Rosetta" (Finder: Applications → Utilities, Terminal, '
    "Get Info), then quit Terminal, open it again and run setup."
)
SETUP_MACOS_OLD = (
    "Estia needs macOS 27 or later. Update macOS (System Settings → General → Software Update), then run setup again."
)
SETUP_NO_TERMINAL = (  # the same words as scripts/setup.sh's
    "Setup asks questions and Signal's one-time codes: run scripts/setup.sh in your own Terminal window."
)
SETUP_STOPPED = "Setup stopped. Run scripts/setup.sh again to go on: steps already done are skipped."
# a file error no step handled (mac-ollama-home-unwritable-crash, round 1): a stop with the path, never a traceback
SETUP_OS_ERROR = (  # a file of your own home your account may not use ({hidden}: SETUP_OS_ERROR_HIDDEN or "")
    "✗ Setup: could not use {path} ({error}).\n"
    "  Fix: give your account Read and Write on it (Finder: Get Info, then Sharing and Permissions{hidden}), then run"
    " setup again: your answers are kept."
)
SETUP_OS_ERROR_HIDDEN = "; Command-Shift-. (period) shows a name that starts with a dot"
# any other file error (review of 30 Oct, P07): her folders never get the Finder fix (it would open them up)
SETUP_OS_ERROR_OTHER = (
    "✗ Setup: could not use {path} ({error}).\n"
    "  Fix: run setup again: your answers are kept. If it says the same, report it (SECURITY.md)."
)
SETUP_OS_ERROR_NO_PATH = "✗ Setup: a file error stopped it ({error}).\n  Fix: run setup again: your answers are kept."
# a full disk at a file no step handled (fix review, dialogue-04: it got the report line); {at}: ": <path>" or ""
SETUP_OS_ERROR_FULL = (
    "✗ Disk: the Mac ran out of space ({error}{at}).\n"
    "  Fix: free space (System Settings → General → Storage), then run setup again: your answers are kept."
)
# the one-time word ({number}: her number; {word}; {name}; {seconds}: RECEIVE_SECONDS)
SETUP_WORD = (
    "Your account: from **your own Signal** on your phone, start a new chat with {number}\n"
    "(not Note to Self) and send **exactly** the word below.\n\n"
    "**Only the account** that sends it gets answers from {name}."
)
SETUP_WORD_USERNAME = (  # her account without a number: found by her username ({username})
    "Your account: from **your own Signal** on your phone, start a new chat,\n"
    "search for her username {username} and send **exactly** the word below.\n\n"
    "**Only the account** that sends it gets answers from {name}."
)
SETUP_WORD_ASK = "Setup looks for it by itself every {seconds} seconds: no Enter needed. To stop: q, then Enter."
# after a minute without it, the usual reasons: in Terminal with its q, and on the word's screen in the window, which
# has its own Stop here (second deep scan: the window's screen had no button, and the reasons stayed in Terminal)
_WORD_NOT_YET = "'{word}' has not arrived yet. The usual reasons, most common first:\n"
_WORD_WRONG_NUMBER = (
    "  1. It went to the wrong number: send it to {number} ({name}'s new number), not to yourself or Note to Self."
)
_WORD_WRONG_CHAT = "  1. It went to the wrong chat: send it in a new chat with her username {username}, not to yourself or Note to Self."
_WORD_REST = """
  2. It is not exactly {word}: send the word alone, as it shows here.
  3. Your phone shows a clock or one tick only: it is not sent yet. It goes once the phone is online.
  4. It came from two accounts: then it counts from neither. Send it again from your own account alone."""
_WORD_KEEPS_LOOKING = "\n  Setup keeps looking (to stop: q, then Enter)."
SETUP_WORD_MISSING = _WORD_NOT_YET + _WORD_WRONG_NUMBER + _WORD_REST + _WORD_KEEPS_LOOKING
SETUP_WORD_MISSING_USERNAME = _WORD_NOT_YET + _WORD_WRONG_CHAT + _WORD_REST + _WORD_KEEPS_LOOKING
SETUP_WINDOW_WORD_MISSING = _WORD_NOT_YET + _WORD_WRONG_NUMBER + _WORD_REST
SETUP_WINDOW_WORD_MISSING_USERNAME = _WORD_NOT_YET + _WORD_WRONG_CHAT + _WORD_REST
SETUP_WORD_STOP = "Stuck? Stop here: running setup again shows a new word."  # the window's way out while it looks
SETUP_LOOK_AGAIN = "Press Enter to look again, or type q to stop."
# no soul.md yet ({soul}: where it goes; {style}: her built-in style, {words}: STYLE_WORDS); no command to run: a
# default Enter would replace her built-in style with the example's
SETUP_PERSONALITY = (
    "Personality: {name}'s built-in style, {style} ({words}).\n"
    "Your own instead, any time: a soul.md in {folder} (setup_assistant.md, 2.5)."
)
# her config and style file are hers alone after the move: nano runs as her, through her wrapper (sudoedit would give
# your account a copy while it is edited, and run the editor as you)
HER_CONFIG_EDIT = "/usr/bin/sudo /usr/local/estia/bin/estia config"
NANO_SAVE = "Ctrl-O, Enter saves; Ctrl-X quits"
SETUP_PERSONALITY_HERS = (
    "Personality: {name}'s built-in style (hearth, the default, or playful: [personality] style in her config)\n"
    "unless you wrote your own. Your own, as her, any time: setup_assistant.md, 2.5."
)
# her config is hers alone: the lines to add, then setup opens it as her on Enter (owner, 27 Oct: no typing of
# commands) and restarts her once, at the end (`restart_her`)
SETUP_HER_OPENS = "Setup opens it as her on Enter ({save}); she restarts once, at the end."
SETUP_HER_RESTART = "Her config was opened: a restart reads any change."  # setup cannot tell whether it changed
# the setup wizard's style question (config.STYLES; {name}: hers): one plain line each, hearth first (Enter)
SETUP_STYLE_LEAD = "How {name} sounds. Her rules **always win** over her style."
# each style's line: the setup window and gum show them by the option, plain lines as the list below the lead
SETUP_STYLE_HEARTH = "steady, kind and frank. One concern on a risky plan; a joke only when asked for fun or on banter."
SETUP_STYLE_PLAYFUL = "warm, curious and playful. The answer first, now and then a light joke, never on a worry."
SETUP_STYLE_LIST = f"  hearth: {SETUP_STYLE_HEARTH}\n  playful: {SETUP_STYLE_PLAYFUL}"
SETUP_STYLE = f"{SETUP_STYLE_LEAD}\n\n{SETUP_STYLE_LIST}"
SETUP_STYLE_ASK = "Which style? (Enter keeps hearth)"
SETUP_STYLE_DRY_RUN = "  (asks which style; Enter keeps hearth)"
SETUP_STYLE_UNKNOWN = '  "{answer}" is not one of them: hearth or playful.'
SETUP_STYLE_CHOSEN = "✓ Style: {style}."
# in her own user setup never writes her config: the lines to add as her ({edit}: HER_CONFIG_EDIT, {save}: NANO_SAVE)
SETUP_STYLE_HERS = (
    "Her config is hers alone, so setup does not write it.\n"
    "For this style, add, or change, these lines:\n\n"
    "    [personality]\n"
    '    style = "{style}"\n\n'
    f"{SETUP_HER_OPENS}"
)
SETUP_STYLE_HERS_DRY_RUN = "  (then prints the lines to add to her config, [personality] and style, and her restart)"
SETUP_EXTRAS_HERS = f"Her config is hers alone: add each section above to it.\n{SETUP_HER_OPENS}"
SETUP_EXTRAS = "Extra Features (all optional): picking one shows its steps; each command runs only on your yes."
SETUP_EXTRAS_RECOMMENDED = "Only the ones you need now (recommended)."  # the line before SETUP_EXTRAS_ASK
SETUP_EXTRAS_ASK = "Which ones? (names, numbers or all; Enter for none)"
# a re-run after a stop before the extras (stops-reruns-extras-pick-lost): the earlier picks setup did not go through
SETUP_EXTRAS_LEFT = "Picked before, not set up yet: {names}."
SETUP_EXTRAS_ASK_LEFT = "Which ones? (names, numbers or all; Enter for those, none for none)"
SETUP_EXTRAS_YOURS = "Her config: {path} (add each section there; she reads it from her next start)."
# a re-run's alias with Ollama's prompt for Gemma 4's small models (made before 0.9.3; empty answers, 17 Oct)
SETUP_MODEL_SMALL_PROMPT = (
    "! Model: local-worker gets Ollama's prompt for Gemma 4's small models, not Google's prompt for 12B and larger: "
    "some answers come back empty and are asked again."
)
SETUP_MODEL_REMAKE = (
    "Making it again fixes that and changes **only the prompt**:\n"
    "the same model ({tag}), the same context.\n\n"
    "Its digest changes: with model_digest in the config, her **answers pause**\n"
    "until the config has the new one (printed after it). No leaves it as it is.\n\n"
    "{stop}"
)
SETUP_MODEL_STOP_HERS = "Yes stops her first and starts her again after it, each on your Enter."
SETUP_MODEL_STOP_YOURS = "Stop her first if she runs (scripts/run.sh: Ctrl-C)."
SETUP_MODEL_REMAKE_ASK = "Make local-worker again now?"
SETUP_MODEL_REMAKE_ADVICE = "once she is stopped"
SETUP_MODEL_REMAKE_LATER = "  Later: scripts/setup.sh again (or by hand: setup_assistant.md, 2.2)."
SETUP_MODEL_REMAKE_AFTER_OLLAMA = "  Setup makes it again once the Ollama fix above is done and setup runs again."
SETUP_MODEL_PROMPT_BY_HAND = (
    "  Setup cannot tell which of its models local-worker is made from, so it leaves it as it is. Make it again by "
    "hand with the line RENDERER gemma4-large (setup_assistant.md, 2.2), then pin the new digest."
)
SETUP_MODEL_REMAKE_FAILED = (
    "✗ Model: local-worker was not made again (the lines above say why); it stays as it was. Check that Ollama runs, "
    "then run scripts/setup.sh again."
)
SETUP_MODEL_REMADE = "✓ Model: local-worker made again with Google's prompt for 12B and larger."
SETUP_MODEL_REMADE_PIN = (
    '  Its new digest: sha256:{digest}. With model_digest in the config: model_digest = "sha256:{digest}" in place '
    "of the old line, {where}. Without it, nothing more to do."
)
SETUP_MODEL_REMADE_UNLISTED = "  Ollama does not list its digest yet: doctor's Model pin line shows it once it does."
SETUP_MODEL_PIN_YOURS = "in {path}, then restart her"
SETUP_MODEL_PIN_HERS = "in her config, which setup opens as her on Enter; then she starts again"
SETUP_MODEL_NOT_MADE = (  # a stop: its area leads the window's title, never a capitalised code name (round 2)
    "✗ Model: local-worker was not made: Ollama's lines say why.\n"
    "  Fix: check that Ollama runs, then run scripts/setup.sh again."
)
# the Registration Lock right after the SMS code (harden.LOCK_NOTE first); Enter turns it on (owner, 19 Oct)
SETUP_LOCK_HOW = (
    "Yes: a **new PIN** at the Signal step, typed twice (4 to 64 characters, no spaces; q skips it).\n"
    "No or q: the security step asks again."
)
SETUP_LOCK_ASK = "Turn on Registration Lock now?"
SETUP_LOCK_PIN = "Registration Lock PIN, a new one (hidden: nothing shows while you type; q skips): "
SETUP_LOCK_AGAIN = "The same PIN again (hidden: nothing shows while you type): "
SETUP_LOCK_RETRY = (
    "Press Enter to try again, or type q to leave it for the security step (scripts/harden.sh offers it again)."
)
# always on: why her own user, and what yes and no do, in plain words ({name}); Enter moves her (owner, 19 Oct)
SETUP_MOVE = (
    "{name} starts **with the Mac**, without a login, as her own hidden macOS account.\n\n"
    "Yes: your Mac password **once** (nothing shows while you type),\n"
    "for admin rights to make her account and copy her files.\n\n"
    "No: she runs only while scripts/run.sh is open; setup asks again at its next run."
)
# the first paragraph with the Ollama app chosen (review W06), its login limit in the card's words (dialogue-03),
# joined to the claim it limits (second fix review, words-move-app-limit-contrast), in short lines (words review, 30
# Oct: at most 100 characters a line)
SETUP_MOVE_APP = f"{{name}} starts **with the Mac** as her own hidden macOS account,\nbut {OLLAMA_APP_LIMIT_LOGIN}"
SETUP_MOVE_OLDER = (
    "{name} runs as you today. In her own hidden macOS account she starts **with the Mac**, without a login.\n\n"
    "Yes: your Mac password **once** (nothing shows while you type);\n"
    "/usr/bin/sudo /usr/local/estia/bin/estia rollback goes back within 7 days.\n\n"
    "No: she stays as she is."
)
# the window's own (owner, 27 Oct: no Mac password in the window; Terminal says it, where macOS asks)
SETUP_WINDOW_MOVE = (  # Yes and No each a paragraph: the page joins a paragraph's lines into one
    "{name} starts **with the Mac**, without a login, as her own hidden macOS account.\n\n"
    "Yes: admin rights **once**, asked by macOS in its own box, to make her account and copy her files.\n\n"
    "No: she runs only while scripts/run.sh is open; setup asks again at its next run."
)
SETUP_WINDOW_MOVE_OLDER = (
    "{name} runs as you today. In her own hidden macOS account she starts **with the Mac**, without a login.\n\n"
    "Yes: admin rights **once**, asked by macOS in its own box;\na rollback goes back within 7 days (setup_assistant.md, "
    "2.7).\n\n"
    "No: she stays as she is."
)
SETUP_MOVE_OLDER_APP = (
    "{name} runs as you today. In her own hidden macOS account she starts **with the Mac**,\n"
    f"but {OLLAMA_APP_LIMIT_LOGIN}"
)
# the details, in the window's More fold; Terminal shows where they are written (owner, 27 Oct: "at most ~3 lines
# before a question")
SETUP_MOVE_MORE_TYPED = "Details: setup_assistant.md, 2.7."
SETUP_MOVE_MORE = (
    "Her account, _estia, never runs as you or as root, the Mac's all-powerful admin account.\n\n"
    "Her data goes to /var/estia, which only that account reads;\n"
    "her code to /usr/local/estia, which only admin rights change.\n\n"
    "No program of yours then reads her data or changes her code without admin rights,\n"
    "as long as no admin rule skips the password."
)
# with the Ollama app chosen: More with its last paragraph replaced, the limit inside the claim (fix review,
# setup-macos-01: "reads her data" stayed, then its contradiction)
SETUP_MOVE_MORE_APP = (
    f"{SETUP_MOVE_MORE.rsplit(chr(10) * 2, 1)[0]}\n\n"
    "No program of yours then reads her files or changes her code without admin rights,\n"
    "as long as no admin rule skips the password, but with the Ollama app\n"
    f"{OLLAMA_APP_LIMIT_PROMPTS}"
)
SETUP_MOVE_ASK = "Install it now?"
SETUP_MOVE_SKIPPED = "Skipped: she runs only while scripts/run.sh is open."  # a no at Always on
SETUP_MOVE_NOT_RUNNING = (  # the closing list's line after that no
    "She does not run yet: start her with scripts/run.sh, or run setup again and answer yes at Always on."
)
SETUP_MOVE_OLDER_ASK = "Move her into her own macOS account now?"
# over SSH without screen: the same question, recommending what SETUP_SSH_SCREEN says (Enter is no there)
SETUP_MOVE_ASK_SSH = SETUP_MOVE_ASK
SETUP_MOVE_OLDER_ASK_SSH = SETUP_MOVE_OLDER_ASK
SETUP_MOVE_SSH_ADVICE = "run setup again inside screen"
SETUP_SECURITY_ASK = "Go through the security step now?"
# the security step: the hardened level, then the nuclear one in a short line (owner, 19 Oct: only for deeply
# technical users; never part of setup)
SETUP_SECURITY = (
    "The security step, each change only on your yes:\n\n"
    "  • **Remote logins** (SSH): keys only.\n"
    "  • **Admin rights**: no admin rule that skips the password.\n"
    "  • **Her files**: private to her.\n"
    "  • **Registration Lock**: on."
)
# Terminal's yes after the bullets: the Mac password (the window says none, owner 27 Oct); then the no, in both
SETUP_SECURITY_PASSWORD = (
    "Yes: your Mac password, if asked (once 5 minutes have passed since admin rights were last used)."
)
SETUP_SECURITY_NO = "No: scripts/harden.sh runs the same step later."
# its More fold: what else it shows, then the nuclear level (setup_plan.nuclear_text); Terminal has no fold, so it
# shows the nuclear level in one line (hostsec.NUCLEAR_HOW's words, with what it is)
SETUP_SECURITY_MORE = "FileVault and the rest only you can change are shown with their fixes."
SETUP_NUCLEAR_TYPED = (
    "The nuclear level: only with scripts/harden.sh --nuclear; it may stop other services on this Mac."
)

# after hostsec.nuclear_line, the owner's words (20 Oct), with her name; setup.nuclear_text fits both to the terminal
SETUP_NUCLEAR = (
    "Only with scripts/harden.sh --nuclear, never part of setup. It may stop other services on this Mac, such as a page "
    "opened from a laptop."
)
SETUP_SSH_SCREEN = (
    "Over a remote login (SSH), a dropped connection stops the move, and it is undone.\n\n"
    "Run it **inside screen**, built into macOS: answer no here (Enter), type screen,\n"
    "then run scripts/setup.sh again (after a drop, screen -r goes back to it)."
)
# a first install's no there: setup ends right after the plan, before anything installs (c-12, confirm-2)
SETUP_SSH_SCREEN_STOP = (
    "Nothing was installed, and setup stops here. Type screen, then run scripts/setup.sh again: your answers are kept."
)
SETUP_SHARE_FAILED = (
    "✓ Always on: {name} runs in her own macOS account {user}. Only her projects folder is not shared yet (the lines above\n"
    "  say why): fix that, then run {share}. A folder outside your home: give {user} read access to it yourself."
)
SETUP_NO_VENV = "✗ No .venv in {repo}: run uv sync --frozen first."  # migrate-to-own-user.sh's own check
# migrate-to-own-user.sh's own guard once she is moved (never this checkout's Python as root), its words
SETUP_ALREADY_MOVED = "✓ already in her own macOS account: use /usr/bin/sudo /usr/local/estia/bin/estia-deploy (it shows the commits first)"
# a stop, as step 1's (stops-reruns-move-failed): the window draws it as its own screen
SETUP_MOVE_FAILED = (
    "✗ Always on: the move did not finish: its own ✗ line says why, and the security step and the extras come "
    "after it.\n  Fix: do what that line asks, then run scripts/setup.sh again ({move} --dry-run shows every step "
    "first)."
)
# her config set up, her Signal account gone from signal-cli's folder: setup runs no Signal step on a set-up install
# (c-5, round 3), so the config is renamed and setup sets her up again
SETUP_MOVE_NO_SIGNAL = (
    "✗ Always on: her config is set up, but her Signal account is not on this Mac any more, so the move cannot take "
    "it to her.\n  Fix: mv ~/.config/local-assistant ~/.config/local-assistant.old, then run scripts/setup.sh "
    "again: it sets up her Signal account again, your answers kept."
)
# the window's box refused, then Not now: the move never started (review ADM-11)
SETUP_MOVE_REFUSED = (
    "✗ Always on: the move did not run: admin rights were not given, and nothing changed.\n"
    "  Fix: run scripts/setup.sh again to move her; the security step and the extras come after the move."
)
# setup's projects question (an extra; recommendation: no)
SETUP_PROJECTS = (  # short paragraphs, lines of 88 characters or less (owner, 27 Oct)
    "Projects (extra, **off by default**): {name} reads the git projects directly in one\n"
    "folder of this Mac but **never changes** them. Only files git tracks, never secret-like\n"
    "names such as .env, never outside the folder you named.\n\n"
    "Yes: her account passes through your home to that folder, so the files in your home\n"
    "that every account may open are **open to her** too (the move's end names them and\n"
    "offers to close them).\n\n"
    "No: she reads no projects."
)
SETUP_PROJECTS_ASK = "Should she see projects on this Mac?"
SETUP_PROJECTS_RECOMMENDED = "No (recommended)."  # the line before SETUP_PROJECTS_ASK
SETUP_PROJECTS_DRY_RUN = '  (dry run: asks "Should she see projects on this Mac?", then the folder and the level)'
SETUP_PROJECTS_FOLDER_RECOMMENDED = (  # the line before SETUP_PROJECTS_FOLDER
    "~/dev (recommended, a folder in your home, which the move shares with her)."
)
SETUP_PROJECTS_FOLDER = "Which folder holds them? (Enter: ~/dev; q: no projects)"
SETUP_PROJECTS_LEVELS = (  # plain lines' list (the window draws each level with its line)
    "  summary: the **README**, open to-do items and recent commits.\n"
    "  docs: also the project's **documents**.\n"
    "  code: also its **source code**."
)
SETUP_PROJECTS_HOME = "Pick a folder inside your home folder, not the home folder itself."  # the input spec's
SETUP_PROJECTS_NO_FOLDER = "No folder {folder}: type another one, or q for no projects."
SETUP_PROJECTS_LEVEL = "How much may she read: summary, docs or code? (Enter: summary)"
SETUP_PROJECTS_LEVEL_RECOMMENDED = "summary (recommended)."  # the line before SETUP_PROJECTS_LEVEL
SETUP_PROJECTS_BAD_LEVEL = "'{answer}' is not a level: summary, docs or code."
SETUP_CHORDS_ON = "✓ Chords: on already ([chords] in the config)."
SETUP_CHORDS_NO_CONFIG = "Chords: no config yet at {path}. scripts/setup.sh writes it; then run --extras again."
SETUP_CHORDS_ADDED = "✓ Chords: [chords] added to {path}; read from her next start (restart her)."
SETUP_CHORDS_NOT_ADDED = "✗ Chords: not added, the config does not load ({why}); {path} is unchanged."
SETUP_CHORDS_HERS = (  # her own user: her config is hers alone
    "Her config is hers alone, so setup does not write it.\n"
    f"To switch chords on, add this line, unless it is there already:\n\n    [chords]\n\n"
    f"{SETUP_HER_OPENS}"
)
SETUP_MAPS_ON = "✓ Places and routes: on already ([maps] in the config)."
SETUP_MAPS_NO_CONFIG = (
    "Places and routes: no config yet at {path}. scripts/setup.sh writes it; then run --extras again."
)
SETUP_MAPS_ADDED = "✓ Places and routes: [maps] added to {path}; read from her next start (restart her)."
SETUP_MAPS_NOT_ADDED = "✗ Places and routes: not added, the config does not load ({why}); {path} is unchanged."
SETUP_MAPS_HERS = (  # her own user: her config is hers alone
    "Her config is hers alone, so setup does not write it.\n"
    f"To switch places and routes on, add this line, unless it is there already:\n\n    [maps]\n\n{SETUP_HER_OPENS}"
)
SETUP_PROJECTS_ON = "✓ Projects: on already ([projects] in the config: root = the folder, default = the level)."
SETUP_PROJECTS_NO_CONFIG = (
    "Projects: no config yet at {path}. scripts/setup.sh writes it and asks about projects there."
)
SETUP_PROJECTS_ADDED = "✓ Projects: [projects] added to {path}; read from her next start (restart her)."
SETUP_PROJECTS_NOT_ADDED = "✗ Projects: not added, the config does not load ({why}); {path} is unchanged."
# in her own user her config is hers alone: the lines, the share and the restart, run by you
SETUP_PROJECTS_HERS = (
    "Her config is hers alone, so setup does not write it.\n"
    "To switch projects on, add these lines, the folder by its absolute path (her ~ is /var/estia):\n\n"
    "    [projects]\n"
    "    root = {root}\n"
    '    default = "{level}"\n\n'
    f"{SETUP_HER_OPENS}"
)
SETUP_PROJECTS_SHARE = "Then the share, no admin rights needed: her own macOS account reads {folder}, read-only."
SETUP_PROJECTS_OUTSIDE = (  # share.py refuses a folder outside your home
    "{folder} is outside your home, so scripts/share-projects.sh does not share it: give _estia read access to it "
    "yourself"
)
# share.refusal's words for a folder outside your home (inside it, share.refusal itself says why)
SETUP_PROJECTS_LINK = "Links are not followed: pick the real folder"  # the input spec's (a link on the way too)
SETUP_PROJECTS_LINK_ON_THE_WAY = "a folder on the way to {folder} is a symbolic link"
SETUP_PROJECTS_REFUSED = "{why}. Type another one, or q for no projects."
SETUP_PROJECTS_CHOSEN = (
    "✓ Projects: {folder}, level {level}. [projects] in the config changes it (per project too: docs = [...], "
    "code = [...])."
)
SETUP_PINNED = (
    "✓ Model pin: the digest Ollama lists for local-worker now is in the config. Answers pause, with a note, when "
    "another model takes that name."
)
SETUP_CHECK_HERS_LEAD = "Her doctor runs as her own macOS account, and it only reads."  # the window's: its box line
SETUP_CHECK_HERS = (
    f"{SETUP_CHECK_HERS_LEAD}\n"
    "  Your Mac password again, if asked (once 5 minutes have passed since admin rights were last used; nothing\n"
    "  shows while you type)."
)
# before SETUP_DONE; no direction: the window draws the log below its words (review W15)
SETUP_CHECK_FAILED = "✗ The check found a problem: its ✗ lines say what to fix."
SETUP_CHECK_NOT_RUN = (  # admin rights not given at her check (round 2): no ✗ line above to point at
    "! The check did not run, as admin rights were not given. Later, in your own Terminal window: "
    "/usr/bin/sudo /usr/local/estia/bin/estia doctor"
)
SETUP_DONE = (
    "\nDone when the check shows **no ✗** and {name}'s welcome has reached your phone (she sends it when she "
    "first runs: always on, or scripts/run.sh). Reply to it to see her answer."
)
# the last step (setup_extras' closing, once her voice service is installed), before the Extra Features picked and
# SETUP_DONE (owner, 26 Oct): the English voices ({female}: heart, jane, eve, vera, heart first on either model, vera
# only where Pocket TTS's larger model runs, owner, 27 Oct: "not af_heart, do heart"; {male}: alba, michael; never by
# the model's size)
# and the Signal commands
SETUP_VOICES = (
    "Voices for voice notes and calls in English: {female} (female); {male} (male).\n"
    "  In Signal: /voices lists them and /voice <name> picks one; /tools lists her tools; /help lists the rest."
)
# her Signal number: a number set up here before ({number}), a new one ({name}), the captcha and the SMS code
SETUP_KNOWN_NUMBER = (
    "{number} finished registering with Signal **on this Mac** before.\n\n"
    "Yes uses it: **no new captcha** or SMS code; the security step offers its Registration Lock.\n"
    "No registers a number again, with a new captcha link and SMS code."
)
SETUP_KNOWN_NUMBER_ASK = "Use the Signal number already set up here ({number})?"
SETUP_KNOWN_NUMBER_ADVICE = "when it is her number"
SETUP_NUMBER = (
    "{name}'s own number: one you control that **no Signal app** uses.\n"
    "Registering it here **logs out** any Signal app on it.\n\n"
    "A new prepaid SIM in any phone is best: used for nothing else and **kept topped up**.\n"
    "A lapsed number can go to someone else."
)
SETUP_NUMBER_ASK = "Her number, with + and the country code (like +306900000000; spaces and dashes are fine):"
SETUP_NUMBER_BAD = "Start with + and the country code, like +30 690 000 0000."
SETUP_SIGNAL_WAITING = (
    "  Waiting for Signal (up to {minutes} minutes)."  # before each request, which may take that long
)
# the captcha's steps (checked 27 Oct 2026 against signal-cli's guide, edited 23 Jan 2026: the page is opened on this
# Mac or on a device with the same internet address; its "Open Signal" link holds the token)
SETUP_CAPTCHA_STEPS = (
    "Signal asks for a captcha (new numbers usually do):\n\n"
    "  1. On this Mac, or a computer or phone on the **same Wi-Fi**, open\n"
    "     https://signalcaptchas.org/registration/generate.html\n"
    "  2. Solve it. A link called **Open Signal** shows under it.\n"
    "  3. Right-click Open Signal (or long-press it) and copy the link.\n"
    "     It starts with signalcaptcha://"
)
SETUP_CAPTCHA = (
    f"{SETUP_CAPTCHA_STEPS}\n"
    "  4. Paste it below and press Enter once. q stops.\n\n"
    "Nothing shows while you paste, and it is **never stored**."
)
SETUP_CAPTCHA_ASK = "captcha link (hidden: nothing shows while you paste): "
SETUP_SMS_SENT = "Signal has sent a code by SMS to {number}: it arrives on the phone **with her SIM**."
SETUP_SMS_ASK = "The code Signal sent by SMS (hidden: nothing shows while you type; q stops): "
SETUP_OLD_PIN_ASK = "Its Registration Lock PIN from before (hidden: nothing shows while you type; q stops): "
# her Signal account, one of two (owner, 23 Oct): a spare number (above) or an account without a number (Signal
# Login, made once in Signal for Android; hostsec has the phone's steps and the hidden questions, in the same words
# as `estia signal-login`)
# (SETUP_ACCOUNT, Terminal's lead, is the cards' own words: prompts/setup_signal.py)
SETUP_ACCOUNT_ASK = "Which one? (1 or 2; Enter: 1)"
SETUP_ACCOUNT_ADVICE = "the tested route"
# the cards' own names everywhere after them (second deep scan): a spare SIM's number, a Signal Account
SETUP_ACCOUNT_UNKNOWN = "  Type 1 for a spare SIM's number or 2 for a Signal Account without a number."
SETUP_NUMBERLESS_DRY_RUN = (
    "Signal Account without a number: shows the phone's steps, asks for her Account ID and her Recovery key (each on\n"
    "  a card laid out as Signal's, or typed hidden in plain lines; the key may take several lines), then runs\n"
    "  $ {rpc}   (register <Account ID> with <Recovery key> on stdin)\n"
    "  keeps both in your login keychain ($ /usr/bin/security -i, both on its input), then runs\n"
    "  $ {one}   (updateProfile with her name, then updateAccount with her username, on stdin)\n"
    "  and asks for her username: a name, a dot and two or more digits."
)
SETUP_LOGIN_FAILED = (
    "That did not work ({reason}).\n"
    "  Check the Account ID and the Recovery key in your password manager or in Signal on the phone."
)
SETUP_LOGIN_AGAIN = "Press Enter to type them again, or type q to stop."
# review S8: -5 is Signal's limit on registrations, never a wrong key; each try is one more registration
SETUP_LOGIN_LIMITED = (
    "Signal limits registrations for now ({reason}).\n"
    "  Each try is one more registration: wait until the time Signal names, if it names one, then try again."
)
SETUP_LOGIN_AGAIN_LATER = "Press Enter once Signal's wait is over to try again, or type q to stop."
SETUP_LOGIN_NO_ANSWER = (
    "That did not work ({reason}): no answer from Signal, so the Account ID and the Recovery key were not checked.\n"
    "  Check this Mac's network."
)
# review S7: signal-cli's register --totp; what the code is and where Signal shows it was not tested here
SETUP_TOTP = (  # signal-cli's own words (its --totp flag) stay off the screen (signal-totp-technical, round 1)
    "Signal asks for a **six-digit code** before it moves this account.\n\n"
    "Type the code Signal gives you for it; where it shows was **not tested** here.\n"
    "It goes to Signal with the key, never shown."
)
SETUP_TOTP_ASK = "The six-digit code (hidden: nothing shows while you type; q stops): "
SIGNAL_LOGIN_TOTP = (
    "✗ Signal asks for a six-digit code (TOTP) before it moves this account. Run\n"
    "  /usr/bin/sudo /usr/local/estia/bin/estia signal-login again and type the code at its last question; where the\n"
    "  code shows was not tested here (and the root-run tools ask for it only after estia-deploy --update-tools)."
)
SETUP_LOGIN_HERE = (
    "✓ Signal: {name}'s Signal Account is on this Mac already (a run before moved it), so it is not\n"
    "  registered again and no Recovery key is needed."
)
SETUP_LOGIN_STOPPED = (
    "Stopped: her account is not on this Mac yet. Run setup again to type the Account ID and the Recovery key again."
)
SETUP_LOGIN_DONE = "✓ Signal: {name}'s Signal Account is on this Mac now; Signal on the Android phone is logged out."
SETUP_PROFILE_NAME = "✓ Profile name: {profile}."
SETUP_PROFILE_LATER = "! Profile name: not set yet ({reason}); she sets it herself at her next start."
SETUP_USERNAME = (
    "Her account has no number, so **her username** is how {name} is found:\n"
    "in Signal, **new chat**, then search for it.\n\n"
    "Signal's form: a name, a dot and two or more digits."
)
SETUP_USERNAME_SET = "✓ Username: {username}. You find her in Signal by it: new chat, then search for {username}."
SETUP_USERNAME_FAILED = "Signal did not take it ({reason}). Try again, type another one, or q to stop."
# `estia signal-login` (admin, as her): an install with a number moves to an account without one
SIGNAL_LOGIN_KEPT = "Nothing was switched: she runs on her account as before."
# review S9: your own Account ID pasted from the password manager would take your own account over
SIGNAL_LOGIN_ALLOWED = (
    "That Account ID is in allowed_acis: an account she answers, never hers. Moving it here would log that Signal app\n"
    "  out. Her Account ID is the one Signal showed on the Android phone for her new account (setup_assistant.md, 2.3, B)."
)
SETUP_OWNER_IS_HER = (
    "The one-time word came from the account just moved here as hers: the Account ID typed for her is your own.\n"
    "  Setup stops here and writes no config. Signal on your phone may show it was logged out: register there again.\n"
    "  Her Signal Account without a number is made on a separate Android phone (setup_assistant.md, 2.3, B); then\n"
    "  run setup again."
)
SIGNAL_LOGIN_NOTHING = (
    "nothing to read: run it as /usr/bin/sudo /usr/local/estia/bin/estia signal-login, which asks for the Account ID,\n"
    "  the Recovery key and her username."
)
SIGNAL_LOGIN_USERNAME = (
    "✗ Username: Signal did not take {username} ({reason}). Nothing was switched: she runs on her account as before.\n"
    "  Her account is on this Mac already: run /usr/bin/sudo /usr/local/estia/bin/estia signal-login again with\n"
    "  another username. It is not registered again (the Recovery key is asked again, but not sent)."
)
SIGNAL_LOGIN_LIMITED = (
    "✗ Signal limits registrations for now ({reason}). Each run is one more registration: run it again once the\n"
    "  time Signal names is over."
)
SIGNAL_LOGIN_HERE = (
    "✓ Signal: her Signal Account is on this Mac already, so it is not registered again (the Recovery key is not\n"
    "  sent)."
)
SIGNAL_LOGIN_SWITCHED = "✓ Config: bot_number names {account} now. The old config: {copy} (only she can read it)."
SIGNAL_LOGIN_NEXT = (
    "Next: on your phone, in Signal, start a new chat, search for her username {username} and send her a message.\n"
    "  Then /usr/bin/sudo /usr/local/estia/bin/estia doctor shows Signal account ✓.\n"
    "  {kept}"
)
SETUP_USERNAME_STOPPED = (  # a run again keeps her Account ID and finds her account here (second deep scan, signal-03)
    "Stopped: her account is on this Mac but has no username yet, so nobody can find her. Run setup again: her\n"
    "  account stays on this Mac, and setup asks only for her username."
)


# the setup wizard's language question ({name}: her name), and the doctor's line about it
SETUP_LANGUAGE = (
    "{name} listens and speaks **one language** at a time, whatever language a message is in.\n"
    "Her notes, /status and /help **stay English**.\n\n"
    "/language in Signal changes it later; it then wins over language in the config."
)
SETUP_LANGUAGE_ASK = "Which language is spoken at home? (a name or the letters in brackets; Enter: English)"
SETUP_LANGUAGE_DRY_RUN = "  (asks which one; Enter: English)"
# the Mac's region names another of the 11 ({language}: its own name): Enter takes it
SETUP_LANGUAGE_ASK_MAC = (
    "Which language is spoken at home? (a name or the letters in brackets; Enter: {language}, this Mac's)"
)
SETUP_LANGUAGE_DRY_RUN_MAC = "  (asks which one; Enter: {language}, this Mac's)"
SETUP_LANGUAGE_UNKNOWN = '  "{answer}" is not one of these.'
SETUP_LANGUAGE_CHOSEN = "✓ Language: {name}."
# the setup wizard's name question (owner, 27 Oct: "be more concise", "only 4 suggestions", a name typed in)
SETUP_NAME_LEAD = "She answers to this name. Her Signal profile shows it with **(AI)** after it."
SETUP_NAME_LIST = "  {names}"  # plain lines: the four, in a line
SETUP_NAME = f"{SETUP_NAME_LEAD}\n\n{SETUP_NAME_LIST}"
SETUP_NAME_ASK = "Pick a name or type your own. (Enter keeps {default})"
SETUP_NAME_OTHER = "Her name"  # the field under the four
SETUP_NAME_DRY_RUN = "  (asks for the name; Enter keeps {default}; a name you type asks for a female or male voice)"
SETUP_NAME_BAD = '  "{answer}" does not fit ({problem}).'
SETUP_NAME_RULE = "1 to 20 letters, spaces or hyphens."  # only as the error (owner, 27 Oct)
SETUP_NAME_SHOWN = "She will answer to {name}."  # the name shown back, as the input spec says it
SETUP_NAME_SHOWN_HE = "He will answer to {name}."  # the male suggestion's (c-17, confirm-2)
SETUP_VOICE = (
    "Her **English voice**, for voice notes and calls once voice is on: the one that fits the name.\n"
    "The other ten languages have **one voice** for now."
)
SETUP_VOICE_ASK = "Female or male voice? (Enter: female)"
SETUP_VOICE_UNKNOWN = "  Type f for female or m for male."
SETUP_NAME_CHOSEN = (  # {voice}: the voice written into the config's [voice] lines (heart or alba, Pocket TTS's)
    "✓ Name: {name}, with a {kind} voice ({voice}, for English voice notes and calls once [voice] is on)."
)
SETUP_TOO_SMALL = "✗ This Mac has {memory:.0f} GB of memory. {name} needs 16 GB or more."
# 16 and 24 GB: Gemma 4 12B, then a question: what it may do for her answers in plain words, never a test or what
# failed in one (owner, 19 Oct; SECURITY.md, known gaps, has the details). The owner's words, 29 Oct ("dont scare the
# users"): the limits, then the safeguards, then 26B; one idea a line, a blank line between them; no "with caution"
SETUP_SMALLER_MODEL = (
    "Gemma 4 12B on this Mac.\n\n"
    "Forwarded audio, mail or calendar may **steer her**.\n\n"
    "She may say a reminder is set when it is not.\n\n"
    "On calls, lights or plugs may switch before you say yes.\n\n"
    "Safeguards in code: audio facts stay out of her memory,\n"
    "and after audio, device changes wait for your yes on Signal.\n\n"
    "Generally, Gemma 26B (32 GB or more) keeps her rules better.\n\n"
    "No stops setup; nothing downloads."
)
SETUP_SMALLER_MODEL_ASK = "Go on with the smaller model?"
SETUP_SMALLER_MODEL_ADVICE = ""  # a plain "Yes (recommended)" (the owner's words, 29 Oct)
SETUP_SMALLER_MODEL_NO = (
    "Nothing was downloaded, and setup stops here. Gemma 26B needs a Mac with 32 GB or more; scripts/setup.sh asks "
    "again when it runs again."
)
# the free disk, checked right after the plan (mac-ollama-disk-full, round 1): {what} names the steps' sizes
SETUP_DISK_SHORT = (
    "✗ Disk: {free:.0f} GB free, and this run needs about {need:.0f} GB ({what}).\n"
    "  Fix: free about {more:.0f} GB (System Settings → General → Storage), then run setup again: your answers are kept."
)
SETUP_DISK_FULL = (  # Ollama's pull ran out of space: the disk, never the network or a resume that loops
    "✗ Disk: the Mac ran out of space during the download ({why}).\n"
    "  Fix: free space (System Settings → General → Storage), then run setup again: the download goes on where it stopped."
)
SETUP_DOWNLOAD = (
    "  Nothing to answer meanwhile. Keep the Mac plugged in, with its lid open: setup keeps it awake until it ends.\n"
    "  If the download stops, run setup again: it goes on where it stopped."
)
# once the plan is answered (the friend's review, ux-21): README's figures, without your typing at the Signal step
SETUP_STEPS_TIME = (
    "Next, the steps: about {minutes} minutes on a 100 Mbit/s line, most of it her model ({size}){calls}."
)
SETUP_STEPS_TIME_CALLS = "; calls add a long build"
# each step after the plan, in one line (owner, 27 Oct: "Step 3 of 9: her model (18 GB, about 25 min)"); {what}: the
# step's name, with its size and time where it downloads
SETUP_STEP = "\nStep {n} of {m}: {what}\n"  # a blank line before and after (owner, 27 Oct)
SETUP_STEPS = {
    "ollama": "Ollama",
    "model": "her model",
    "signal": "her Signal account",
    "config": "her config",
    "always_on": "always on",
    "security": "the security step",
    "calls": "calls",
    "extras": "Extra Features",
    "check": "the check",
}
SETUP_STEP_CALLS = "several GB, a long build"  # once she runs in her own macOS account
# a download's five dots (setup_progress): no percent, no number that ticks; a stall keeps its dots and says this
SETUP_STILL_WORKING = "still working"
SETUP_PULLING = "  Ollama's own pull, {url}: {tag}"
SETUP_PULL_ENDED = "Ollama's pull ended before it said done"
SETUP_PULL_LINE_LONG = "Ollama's answer had a line over 64 KB"
SETUP_PULL_FAILED = "The download did not finish ({why}). Run scripts/setup.sh again to resume it."
SETUP_MODEL_OTHER_BUILD = (
    "✗ Model: {tag} came down as {got}, not the tested build {want}: Ollama's library changed it since. Nothing was "
    "made from it. Update Estia (git pull), then run scripts/setup.sh again."
)
SETUP_OLLAMA_GOES_ON = (
    "  (she is set up already, so setup goes on with the other steps; run the fix, then scripts/setup.sh again)"
)
SETUP_MODEL_AFTER_OLLAMA = (
    "! Model: local-worker is not there yet; setup downloads it once the Ollama fix above is done and setup runs again."
)


# setup's answers file (setup_answers): read back as untrusted; {path}: the file, {why}: one of the reasons below
SETUP_ANSWERS_REFUSED = "Your answers file {path} {why}, so setup starts with no answers and leaves it as it is."
SETUP_ANSWERS_LINK = "is a link"
SETUP_ANSWERS_NOT_FILE = "is not a file"
SETUP_ANSWERS_NOT_YOURS = "is not yours alone"
SETUP_ANSWERS_LARGE = "is over 64 KB"
SETUP_ANSWERS_UNREAD = "is not one setup can read"
SETUP_ANSWERS_FORMAT = "is from another version of setup"
SETUP_NOT_KEPT = "Not kept: {question} no longer fits ({why}). Asked again."  # a kept answer its check now refuses
# a re-run's line for an answer kept from an earlier one ({shown}: the question and the answer, {id}: --redo's)
SETUP_KEPT = "Kept: {shown}. To change it: scripts/setup.sh --redo {id}"
# About you and the home area once handed over: her first start imports them once, so a change goes through /profile
SETUP_KEPT_PROFILE = "Kept: {shown}. To change it: /profile in Signal"
# --redo about_you or home_area on an install set up, or once handed over ({title}: the question's, "About you")
# --redo signal on an install set up: no Signal step runs there (signal-redo-signal-configured-noop)
SETUP_REDO_SIGNAL = (
    "Her Signal account: in her config already, so --redo signal changes nothing on an install set up. "
    "For a Signal Account without a number: /usr/bin/sudo /usr/local/estia/bin/estia signal-login "
    "(setup_assistant.md, 7. Operations)."
)
SETUP_REDO_PROFILE = (
    "{title}: asked only on a first install, and handed over to her only once. To change it: /profile in Signal."
)
SETUP_REDO_NOTHING = "Nothing else to change: setup ends here."  # after those lines, when --redo named only them
SETUP_ANSWERS_IN_USE = "Another setup is running. Finish or stop it first."
SETUP_ANSWERS_WHERE = "Your answers, without any code, PIN or key: {path}."  # after the plan, once it is in the file
SETUP_NO_QUESTION = "No question named {id}. The questions: {ids}."  # --redo with an id setup does not know
SETUP_ANSWERS_NONE = "No answers file at {path}."  # --answers with a file that is not there
# what a dry run prints in place of a question ({ask}: its line, {hint}: [Y/n] or [y/N]); the move's and the security
# step's name the command a yes runs ({move}, {harden}: the scripts' paths)
SETUP_ASKS = '  (asks "{ask}")'
SETUP_MOVE_DRY_RUN = '  $ {move}   (asks "{ask} {hint}"; --dry-run first shows every step)'
SETUP_SECURITY_DRY_RUN = '  $ {harden}   (asks "{ask} {hint}"; {harden} --dry-run shows every check)'


# setup's menus in Terminal (setup_gum): gum, pinned, or plain lines and why ({why}: one of the reasons below)
SETUP_GUM_DOWNLOADING = "Setup's menus: downloading gum {version} (4.7 MB) from GitHub, once (up to 2 minutes)."
SETUP_GUM_READY = "Setup's menus: gum {version}, downloaded once from GitHub and checked by its fingerprint."
SETUP_GUM_DRY_RUN = (
    "Setup's menus: gum {version}, downloaded once from GitHub and checked by its fingerprint:\n  $ {command}"
)
SETUP_GUM_PLAIN = "Setup's menus: plain lines ({why})."
SETUP_GUM_FAILED = "gum's download from GitHub did not finish"
SETUP_GUM_NOT_PINNED = "the download is not gum {version} by its fingerprint"
SETUP_GUM_NO_PROGRAM = "the download holds no single gum program"
SETUP_GUM_CHANGED = "gum's file changed since it was checked"
SETUP_GUM_NOT_YOURS = "gum's file is not yours alone"
SETUP_GUM_NOT_FILE = "gum's file is a link or not a file"
# a paste that brought more than the answer (spec rule 6): what was kept
SETUP_PASTE_KEPT = "Kept {count} characters; {left} spaces or hidden characters were left out."
