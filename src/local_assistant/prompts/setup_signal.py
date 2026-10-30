"""Setup's words for the Signal steps of setup (setup_signal, setup_keycard): her account, the key card; re-exported
by prompts/__init__.py. {name}: her name."""

from __future__ import annotations

# fmt: off
__all__ = [
    "SETUP_ACCOUNT", "SETUP_ACCOUNT_LEAD", "SETUP_ACCOUNT_LIST", "SETUP_CAPTCHA_NOT_YET", "SETUP_CAPTCHA_REFUSED",
    "SETUP_CAPTCHA_SHOWN", "SETUP_CAPTCHA_STOP", "SETUP_CAPTCHA_STOPPED", "SETUP_CARDS_STOP", "SETUP_CARD_AT",
    "SETUP_CARD_DONE", "SETUP_CARD_KEPT", "SETUP_CARD_KEYS", "SETUP_CARD_LIMITED", "SETUP_CARD_LIMITED_UNTIL",
    "SETUP_CARD_NOT_ID", "SETUP_CARD_NOT_KEY", "SETUP_CARD_NO_ANSWER", "SETUP_CARD_PASTE_LONG", "SETUP_CARD_REFUSED",
    "SETUP_CARD_SHORT", "SETUP_CARD_SYMBOL", "SETUP_CARD_TITLE", "SETUP_CARD_TYPED_AGAIN", "SETUP_CODE_DIGITS",
    "SETUP_CODE_FAILED", "SETUP_CODE_NO_ANSWER", "SETUP_CODE_STOP", "SETUP_CODE_STOPPED", "SETUP_KEYCHAIN_ID_ITEM",
    "SETUP_KEYCHAIN_ID_LABEL", "SETUP_KEYCHAIN_KEEP_BOTH", "SETUP_KEYCHAIN_KEPT", "SETUP_KEYCHAIN_KEY_ITEM",
    "SETUP_KEYCHAIN_KEY_LABEL", "SETUP_KEYCHAIN_LOCKED", "SETUP_KEYCHAIN_NOT_KEPT", "SETUP_KEYCHAIN_ONE_KEPT",
    "SETUP_KEYCHAIN_ONE_NOT_KEPT", "SETUP_KEYCHAIN_REMOVED", "SETUP_NO_BACK_CODE", "SETUP_NUMBER_ASK_REGION",
    "SETUP_NUMBER_DIGITS", "SETUP_NUMBER_HELP", "SETUP_NUMBER_NOT_DIGIT", "SETUP_NUMBER_RIGHT",
    "SETUP_NUMBER_RIGHT_ADVICE", "SETUP_NUMBER_RIGHT_LEAD", "SETUP_NUMBER_STOP", "SETUP_NUMBER_STOPPED",
    "SETUP_NUMBER_STOP_TYPED", "SETUP_OLD_PIN", "SETUP_OLD_PIN_FAILED", "SETUP_OLD_PIN_STOP", "SETUP_OLD_PIN_STOPPED",
    "SETUP_PIN_LONG", "SETUP_PIN_MATCHES", "SETUP_PIN_MISMATCH", "SETUP_PIN_SHORT", "SETUP_PIN_SPACES",
    "SETUP_REGISTER_FAILED", "SETUP_REGISTER_LIMITED", "SETUP_REGISTER_LIMITED_NO_TIME", "SETUP_REGISTER_NO_ANSWER",
    "SETUP_ROUTE_NO_NUMBER", "SETUP_ROUTE_NO_NUMBER_LINE", "SETUP_ROUTE_NO_NUMBER_NEXT", "SETUP_ROUTE_NUMBER",
    "SETUP_ROUTE_NUMBER_LINE", "SETUP_SIGNAL_APP", "SETUP_SIGNAL_APP_ASK", "SETUP_SIGNAL_APP_FIRST", "SETUP_TOTP_STOP",
    "SETUP_USERNAME_ASK_NICK", "SETUP_USERNAME_FOUND", "SETUP_USERNAME_RULE", "SETUP_USERNAME_STOP",
    "SETUP_USERNAME_TAKEN", "SETUP_USERNAME_TRY", "SETUP_WINDOW_CODE_FAILED", "SETUP_WINDOW_CODE_NO_ANSWER",
    "SETUP_WINDOW_OLD_PIN_FAILED", "SETUP_WINDOW_OLD_PIN_STOPPED", "SETUP_WINDOW_USERNAME_FAILED", "SETUP_WORD_DRY_RUN",
    "SETUP_WORD_RECEIVED", "SETUP_WORD_STOPPED",
]
# fmt: on

# the preview's Signal screen (owner's copy, 26 Oct), then the question: setup waits for a yes
# (owner, 27 Oct: one idea a paragraph, a blank line between them; the question alone, "Recommended" under Yes; 28 Oct,
# "yes to both": a Signal Account, as the route's card names it, with nearly the same words in bold)
SETUP_SIGNAL_APP = (
    "{name} gets her own Signal account: the number of a **spare SIM**,\n"
    "or a Signal Account **without a number**. It is hers alone.\n\n"
    "**Your own Signal** stays as it is, and you write and speak to her from it.\n\n"
    "No Signal on your phone yet? Install it first, free: signal.org/install"
)
SETUP_SIGNAL_APP_ASK = "Is Signal on your phone now?"
SETUP_SIGNAL_APP_FIRST = "Install Signal on your phone first, free: signal.org/install. Setup waits here."
# the two routes as cards: the label is all gum's menu shows, so the cost is in it, before the choice (spec); the
# owner's words and bold (28 Oct): "make bold 'spare SIM's' and 'Without Number' then mention 'Signal Account' (not
# Login)", then "Signal Account / Login, then... people think it terms of accounts, login is more technical": here,
# where setup first names it, Login after it, the name on Signal's purchase screen; everywhere else a Signal Account
# (the phone's steps name that "Signal Login" screen, hostsec.NUMBERLESS_STEPS)
SETUP_ROUTE_NUMBER = "A **spare SIM's** number: one SMS code"
SETUP_ROUTE_NUMBER_LINE = "Needs: the phone with that SIM, for the code."
SETUP_ROUTE_NO_NUMBER = "**Without number**: a Signal Account / Login, $2.99 or EUR 3.49 once"
SETUP_ROUTE_NO_NUMBER_LINE = (
    "Needs: a spare or borrowed Android phone with Google Play where Signal is not set up yet,\n"
    "never the phone with your own Signal.\n"
    "Texts and voice notes work both ways; a call she answers on it is untested."
)
# right after route 2 is picked in the plan (signal-numberless-steps-late, round 1): the phone's part, so the account
# is made while the downloads run, not learnt at the Signal step about 25 minutes later (the step shows every step)
SETUP_ROUTE_NO_NUMBER_NEXT = (
    "Meanwhile, on the spare or borrowed Android phone: install Signal, tap **Register without number**, pay once,\n"
    "and keep the Account ID and the Recovery key it shows. Setup asks for both at the Signal step."
)
# Terminal's lead for the two cards, in their own words (the window draws the cards)
SETUP_ACCOUNT_LEAD = "{name}'s Signal account, one of two:"
SETUP_ACCOUNT_LIST = (  # plain lines' list of the cards (gum shows it too: its menu shows only the labels)
    f"  1. {SETUP_ROUTE_NUMBER}\n     {SETUP_ROUTE_NUMBER_LINE}\n"
    f"  2. {SETUP_ROUTE_NO_NUMBER}\n     " + SETUP_ROUTE_NO_NUMBER_LINE.replace("\n", "\n     ")
)
SETUP_ACCOUNT = f"{SETUP_ACCOUNT_LEAD}\n\n{SETUP_ACCOUNT_LIST}"
# her number: the Mac's region gives the country code ({code}); checked, then shown back before it registers
SETUP_NUMBER_HELP = "The spare SIM's number."
SETUP_NUMBER_ASK_REGION = "Her number, the spare SIM's (without a +, +{code} goes in front):"
# a quiet way out at her number (owner, 27 Oct: "imagine that I cannot find the number"): the window's Stop here
SETUP_NUMBER_STOP = "Not at hand? Stop here: your answers are kept, and running setup again goes on from this question."
SETUP_NUMBER_STOP_TYPED = (  # Terminal's: q at the question
    "Not at hand? Type q to stop here: your answers are kept,\nand running setup again goes on from this question."
)
# (the window's button, "Stop here", and its screen after it are the page's own words: page.js WORDS)
SETUP_NUMBER_DIGITS = "A number has 8 to 15 digits with its country code; this has {count}."
SETUP_NUMBER_NOT_DIGIT = "A number holds only digits after the +; {symbol} is not one."
SETUP_NUMBER_RIGHT = "{found}: right?"
SETUP_NUMBER_RIGHT_LEAD = "**No** asks for it again."
SETUP_NUMBER_RIGHT_ADVICE = "when it is the spare SIM's number"
# after the plan, at the SMS code: why there is no Back (owner, 27 Oct: "say why in one line where it matters")
SETUP_NO_BACK_CODE = (
    "Signal has sent the code now: to change her account, start again with scripts/setup.sh --redo signal."
)
SETUP_NUMBER_STOPPED = "Stopped before her number was registered: run scripts/setup.sh again to go on."
# the captcha link (never kept): checked as pasted, shown back by its start and length only ({count})
SETUP_CAPTCHA_NOT_YET = "That is not the link yet. It starts with signalcaptcha://."
SETUP_CAPTCHA_SHOWN = "Captcha link: signalcaptcha://… ({count} characters)."
SETUP_CAPTCHA_REFUSED = "Signal refused this link. Get a new one."
SETUP_CAPTCHA_STOPPED = "Registration stopped: run setup again (a new captcha link for each try)."
# the window's quiet way out at the link and at the code (Terminal's words say q): the page's Stop here
SETUP_CAPTCHA_STOP = "Stuck? Stop here: running setup again asks for a new link."
# Signal's six-digit codes (the SMS code, the six-digit code) and the PINs: never shown, never kept
SETUP_CODE_DIGITS = "The code has 6 digits, like 123-456."
SETUP_CODE_STOP = (  # signal-no-sms-loop: Signal's limit and the way out named at the screen
    "No SMS? Stop here: running setup again asks Signal for a new code; Signal limits how many it sends.\n"
    "A SIM that gets no SMS: scripts/setup.sh --redo signal, for a Signal Account without a number."
)
# a code Signal refused, and no answer from Signal, in Terminal (its q); the stops at the code and at register
SETUP_CODE_FAILED = (
    "That did not work ({reason}).\n"
    "  Check the code (from the latest SMS) and the PIN, if the number had one, then type them again, or type q to stop."
)
SETUP_CODE_NO_ANSWER = (  # no code, or signal-cli's -3 naming the network (review S05): the Wi-Fi, never the code
    "That did not work ({reason}): no answer from Signal, so the code was not checked.\n"
    "  Check the Wi-Fi, then type the code again, or type q to stop."
)
SETUP_CODE_STOPPED = (  # q at the code or the old PIN, before a code was checked or after one was refused
    "Stopped: her number is not registered yet.\n"
    "  Run setup again: it registers the number again, with a new SMS code (and maybe a new captcha link).\n"
    # signal-no-sms-loop (round 1): a SIM that gets no SMS (data-only, blocked, a landline) has a way out
    "  Signal limits how many codes it sends. A SIM that gets no SMS: scripts/setup.sh --redo signal,\n"
    "  for a Signal Account without a number."
)
# a registration Signal refused (signal-register-errors-raw, round 1): by what happened, as route 2 says it, its
# UTC time as the Mac's clock shows it (setup_signal.local_times); each a stop with its Fix
SETUP_REGISTER_LIMITED = (
    "✗ Signal: it limits registrations for now ({reason}).\n"
    "  Fix: wait until then, then run setup again: your answers are kept, and each try is one more registration."
)
SETUP_REGISTER_LIMITED_NO_TIME = (  # its words name no time (review S09): no "until then"
    "✗ Signal: it limits registrations for now ({reason}); it named no time.\n"
    "  Fix: run setup again later: your answers are kept, and each try is one more registration."
)
SETUP_REGISTER_NO_ANSWER = (
    "✗ Signal: no answer from Signal ({reason}): the number was not registered.\n"
    "  Fix: check the Wi-Fi, then run setup again: your answers are kept."
)
SETUP_REGISTER_FAILED = (
    "✗ Signal: the number was not registered ({reason}).\n"
    "  Fix: run setup again (your answers are kept); if it says this again, a Signal Account without a number:\n"
    "  scripts/setup.sh --redo signal."
)
# a code Signal refused, in the window (it has its own Stop here): Terminal's SETUP_CODE_FAILED without its q
SETUP_WINDOW_CODE_FAILED = (
    "That did not work ({reason}).\n"
    "Check the code (from the latest SMS) and the PIN, if the number had one, then type them again."
)
SETUP_WINDOW_CODE_NO_ANSWER = (  # Terminal's SETUP_CODE_NO_ANSWER without its q
    "That did not work ({reason}): no answer from Signal, so the code was not checked.\n"
    "Check the Wi-Fi, then type the code again."
)
# signal-number-is-own and signal-reglock-unknown-dead-end (round 1): the one telling moment, and the ways out
SETUP_OLD_PIN = (
    "Signal says this number is locked with a **Registration Lock PIN** from before.\n\n"
    "If this is your own number, stop here: registering it logs your phone's Signal out.\n\n"
    "A recycled number may hold its last owner's lock: without that PIN,\n"
    "Signal frees it after 7 days without use.\n\n"
    "Meanwhile: another SIM (scripts/setup.sh --redo number),\n"
    "or a Signal Account without a number (scripts/setup.sh --redo signal)."
)
SETUP_OLD_PIN_FAILED = "That PIN did not work ({reason}). Type it again, or q to stop."
# q (or Stop here) at the old PIN: a run again meets the same lock, so the ways out are named
SETUP_OLD_PIN_STOPPED = (
    "Stopped: her number is locked with a Registration Lock PIN from before.\n"
    "  Without it, Signal frees the number after 7 days without use. Meanwhile: another SIM\n"
    "  (scripts/setup.sh --redo number) or a Signal Account without a number (scripts/setup.sh --redo signal)."
)
SETUP_WINDOW_OLD_PIN_FAILED = "That PIN did not work ({reason}). Type it again."  # the window: no q
# Stop here at the old PIN in the window (review S02): the same ways out, each with the window's own (Change on its Kept
# screen at a run again), never only "run setup again", which meets the same lock
SETUP_WINDOW_OLD_PIN_STOPPED = (
    "Stopped: her number is locked with a Registration Lock PIN from before.\n"
    "  Without it, Signal frees the number after 7 days without use. Meanwhile: another SIM (Change at Her number\n"
    "  when setup opens again, or scripts/setup.sh --redo number) or a Signal Account without a number (Change at\n"
    "  Her Signal account, or scripts/setup.sh --redo signal)."
)
# the window's way out where the answer may not be at hand (second deep scan): Terminal's words say q already; each
# says what a run again asks (a stop at the old PIN keeps nothing registered, so a run again registers anew)
SETUP_OLD_PIN_STOP = "Don't know it? Stop here: the lock stays until Signal frees the number."
SETUP_CARDS_STOP = "Not at hand? Stop here: running setup again asks for the Account ID and the Recovery key."
SETUP_TOTP_STOP = "No code? Stop here: running setup again asks for the Account ID, the Recovery key and the code."
SETUP_PIN_SHORT = "At least 4 characters."
SETUP_PIN_LONG = "At most 64."
SETUP_PIN_SPACES = "No spaces."
SETUP_PIN_MISMATCH = "The two did not match."
SETUP_PIN_MATCHES = "✓ matches"
# her username, an account without a number's: her nickname from her name ({nickname}), the digits added
SETUP_USERNAME_ASK_NICK = (
    "Her username in Signal: {nickname}. and 2 to 9 digits, like {nickname}.42; the digits alone are enough (q stops):"
)
SETUP_USERNAME_RULE = (
    "Nickname: 3 to 32 letters, digits or underscores, no digit first. After the dot: 2 to 9 digits, not 00."
)
SETUP_USERNAME_TAKEN = "{username} is taken. Try {other}?"
# one Signal will not take for another reason (no answer, its limit), in the window (review S01): its screen again
# with the reason, and its Stop here (her account stays on this Mac: SETUP_USERNAME_STOPPED); none of those is the
# name, so trying again comes first (fix review, dialogue-05)
SETUP_WINDOW_USERNAME_FAILED = "Signal did not take it ({reason}). Try again, or type another one."
SETUP_USERNAME_STOP = "Stuck? Stop here: running setup again asks only for her username."
SETUP_USERNAME_TRY = "Her username (Enter: {other}; or other digits, or another username; q stops):"
SETUP_USERNAME_FOUND = "✓ People find her as {username}: in Signal, new chat, then search for {username}."
# the one-time word: shown on a line of its own, waited for by itself (no Enter), received from one account
SETUP_WORD_RECEIVED = "✓ Word received from one account."
SETUP_WORD_STOPPED = "'{word}' did not arrive (or came from more than one account). Run setup again."
SETUP_WORD_DRY_RUN = (
    "Your account: shows a one-time word (like {word}) to send from your own Signal, then runs\n"
    "  $ signal-cli {ipv4} -o json -a <number> receive -t 20   and keeps the account that sent the word\n"
    "  (looks by itself every 20 seconds, no Enter; after a minute, the usual reasons; q and Enter stop)"
)
# the key card (setup_keycard): each group checked as typed; a refused character named with its place ({symbol}:
# 'ø', or a look-alike by its kind), then what the card holds ({refusal})
SETUP_CARD_SYMBOL = "Group {group}, character {place}: {symbol} {refusal}"
SETUP_CARD_NOT_KEY = "is not on Signal's card. Signal shows # for o and = for 0."
SETUP_CARD_NOT_ID = "is not in an Account ID. It has 0 to 9 and A to F."
SETUP_CARD_PASTE_LONG = "That paste has {count} characters; {room} fit on the card. Paste only the key."
SETUP_CARD_SHORT = "Group {group} has {count} of {size} characters."
SETUP_CARD_AT = "Group {group} of {count}."
SETUP_CARD_DONE = "All {count} groups. Compare with your phone, then Enter."
SETUP_CARD_REFUSED = "Signal refused it. Compare each group with your phone."
# Signal's limit (-5): never a refusal (c-13, confirm-2); {when}: its next try, in local time. The cards keep every
# group typed; plain lines' hidden read keeps none, so there the line says it is typed again (review W13)
SETUP_CARD_KEPT = "Every group is kept."
SETUP_CARD_TYPED_AGAIN = "Type it again."
SETUP_CARD_LIMITED = f"Signal limits registrations for now, so nothing was checked. {SETUP_CARD_KEPT}"
SETUP_CARD_LIMITED_UNTIL = f"Signal limits registrations until {{when}}, so nothing was checked. {SETUP_CARD_KEPT}"
SETUP_CARD_NO_ANSWER = f"No answer from Signal: check the Wi-Fi. {SETUP_CARD_KEPT}"  # none, or -3 naming the network
SETUP_CARD_TITLE = "Her {label}, typed as Signal shows it on the phone, # and = too."
SETUP_CARD_KEYS = (
    "Type each group; Backspace goes back, the arrow keys move between groups, a paste fills them.\n"
    "Enter once all are typed; Esc stops. This screen is cleared when you leave it."
)
# your login keychain: the Recovery key and the Account ID typed here, once Signal took them (owner, 25 Oct)
SETUP_KEYCHAIN_LOCKED = (
    "Your login keychain is locked: macOS asks for your Mac password in Terminal, hidden, to open it."
)
SETUP_KEYCHAIN_KEPT = "✓ Signal's Recovery key and Account ID: in your login keychain on this Mac."
SETUP_KEYCHAIN_KEEP_BOTH = (
    "  Keep the Account ID and the Recovery key in your password manager: without both, the account cannot move to\n"
    "  another Mac, and no number, SMS code or PIN brings it back."
)
SETUP_KEYCHAIN_NOT_KEPT = (
    "! Signal's Recovery key and Account ID: not kept in your login keychain.\n" + SETUP_KEYCHAIN_KEEP_BOTH
)
# one of the two (review SEC-5): {item} is "Recovery key" or "Account ID"
SETUP_KEYCHAIN_ONE_KEPT = "✓ Signal's {item}: in your login keychain on this Mac."
SETUP_KEYCHAIN_ONE_NOT_KEPT = "! Signal's {item}: not kept in your login keychain."
SETUP_KEYCHAIN_REMOVED = "! Signal's {item}: not kept in your login keychain; the copy from before is removed too."
SETUP_KEYCHAIN_KEY_ITEM = "Recovery key"
SETUP_KEYCHAIN_ID_ITEM = "Account ID"
SETUP_KEYCHAIN_KEY_LABEL = "Estia: Signal Recovery key"
SETUP_KEYCHAIN_ID_LABEL = "Estia: Signal Account ID"
