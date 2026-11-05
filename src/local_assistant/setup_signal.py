"""Setup's Signal steps (`scripts/setup.sh`): her account, a spare number or one without a number, her
Registration Lock, and who may talk to her (the one-time word). The Wizard in setup.py runs them in order.

Every code typed here (the captcha link, the SMS code, a PIN, the Account ID and the Recovery key) is read hidden
from the terminal itself through the Wizard's readers and given to signal-cli on stdin, never as an argument.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import secrets
import select
import subprocess
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, tzinfo
from pathlib import Path
from typing import TYPE_CHECKING

from local_assistant import harden, hostsec, ownuser, prompts, setup_keycard, setup_keychain
from local_assistant.setup_base import WizardBase
from local_assistant.setup_keychain import Saved
from local_assistant.setup_questions import (
    RECOMMENDED,
    Check,
    Checked,
    Known,
    Option,
    Question,
    Value,
    Variant,
    bolded,
    clean_paste,
    fill,
    yes_no,
)
from local_assistant.signal_io import RATE_LIMIT_ERROR, signal_data

if TYPE_CHECKING:
    from local_assistant.setup_plan import Mac

# The country calling codes (ITU-T E.164) by the ISO 3166 region macOS names (`Mac.country`), one region and its code
# each; regions that share a code each name it (+1, +7, +44, +47, +61, +262, +590, +599). The codes are prefix-free:
# none starts another, so a number's own code is found by its first one to three digits.
_CALLING = """
AD376 AE971 AF93 AG1 AI1 AL355 AM374 AO244 AR54 AS1 AT43 AU61 AW297 AX358 AZ994 BA387 BB1 BD880 BE32 BF226 BG359 BH973
BI257 BJ229 BL590 BM1 BN673 BO591 BQ599 BR55 BS1 BT975 BW267 BY375 BZ501 CA1 CC61 CD243 CF236 CG242 CH41 CI225 CK682
CL56 CM237 CN86 CO57 CR506 CU53 CV238 CW599 CX61 CY357 CZ420 DE49 DJ253 DK45 DM1 DO1 DZ213 EC593 EE372 EG20 EH212
ER291 ES34 ET251 FI358 FJ679 FK500 FM691 FO298 FR33 GA241 GB44 GD1 GE995 GF594 GG44 GH233 GI350 GL299 GM220 GN224
GP590 GQ240 GR30 GT502 GU1 GW245 GY592 HK852 HN504 HR385 HT509 HU36 ID62 IE353 IL972 IM44 IN91 IO246 IQ964 IR98 IS354
IT39 JE44 JM1 JO962 JP81 KE254 KG996 KH855 KI686 KM269 KN1 KP850 KR82 KW965 KY1 KZ7 LA856 LB961 LC1 LI423 LK94 LR231
LS266 LT370 LU352 LV371 LY218 MA212 MC377 MD373 ME382 MF590 MG261 MH692 MK389 ML223 MM95 MN976 MO853 MP1 MQ596 MR222
MS1 MT356 MU230 MV960 MW265 MX52 MY60 MZ258 NA264 NC687 NE227 NF672 NG234 NI505 NL31 NO47 NP977 NR674 NU683 NZ64 OM968
PA507 PE51 PF689 PG675 PH63 PK92 PL48 PM508 PR1 PS970 PT351 PW680 PY595 QA974 RE262 RO40 RS381 RU7 RW250 SA966 SB677
SC248 SD249 SE46 SG65 SH290 SI386 SJ47 SK421 SL232 SM378 SN221 SO252 SR597 SS211 ST239 SV503 SX1 SY963 SZ268 TC1 TD235
TG228 TH66 TJ992 TK690 TL670 TM993 TN216 TO676 TR90 TT1 TV688 TW886 TZ255 UA380 UG256 US1 UY598 UZ998 VA39 VC1 VE58
VG1 VI1 VN84 VU678 WF681 WS685 XK383 YE967 YT262 ZA27 ZM260 ZW263
"""
CALLING_CODES: Mapping[str, str] = {item[:2]: item[2:] for item in _CALLING.split()}
_CODES = frozenset(CALLING_CODES.values())
# what a number dialled at home starts with and the + form leaves out: 0 in most regions, these apart; a region whose
# numbers keep their 0 after the code (Italy's, and these) keeps it
_TRUNK = {"RU": "8", "KZ": "8", "BY": "80", "HU": "06"}
_KEEPS_ZERO = frozenset({"IT", "SM", "VA", "CI", "CG", "GA"})
_DIALLED = str.maketrans("", "", "-()./")  # what a number is written with besides its digits and spaces


@dataclass(frozen=True)
class Sender:
    name: str
    aci: str
    text: str  # the first message in the look
    texts: tuple[str, ...] = ()  # every message in the look, in order (review S18: a "hi" first hid the word)


def senders(lines: Iterable[str]) -> list[Sender]:
    """Who sent a message, from `signal-cli -o json receive` output (one JSON envelope per line), each person once,
    with every text they sent."""
    from local_assistant.setup import _ACI  # setup.py's (it imports this module)

    found: dict[str, Sender] = {}
    for line in lines:
        try:
            envelope = json.loads(line).get("envelope", {})
        except (ValueError, AttributeError):
            continue
        data = envelope.get("dataMessage") if isinstance(envelope, dict) else None
        aci = str(envelope.get("sourceUuid") or "") if isinstance(envelope, dict) else ""
        if not isinstance(data, dict) or not isinstance(data.get("message"), str) or not _ACI.match(aci):
            continue
        name = str(envelope.get("sourceName") or envelope.get("sourceNumber") or "(no name)")
        text = data["message"].strip()
        before = found.get(aci)
        found[aci] = (
            Sender(name, aci, text, (text,)) if before is None else replace(before, texts=(*before.texts, text))
        )
    return list(found.values())


def one_time_word(name: str, number: int) -> str:
    """The word the owner sends to show which account is theirs: her name and a number, like estia-4821."""
    return "-".join([*name.lower().replace("-", " ").split(), str(number)])


def _bare(text: str) -> str:
    """A word as it is compared: without accents or case (signal-greek-word: "μαρια-3239" for "μαρία-3239")."""
    split = unicodedata.normalize("NFKD", text.strip())
    return "".join(char for char in split if not unicodedata.combining(char)).casefold()


def owner_by_word(found: list[Sender], word: str) -> str | None:
    """The account that sent exactly the one-time word shown to the owner (a profile name can be copied), in any of
    its messages of the look; accents and case do not count."""
    matches = {
        sender.aci for sender in found if any(_bare(text) == _bare(word) for text in (sender.texts or (sender.text,)))
    }
    return matches.pop() if len(matches) == 1 else None


def needed_code(typed: str) -> str:
    """The six-digit code Signal asked for (`hostsec.totp_code`); here one is needed, so none is asked again."""
    code = hostsec.totp_code(typed)
    if not code:
        raise ValueError(hostsec.TOTP_BAD)
    return code


NETWORK_ERROR = -3  # signal-cli's IO_ERROR: any IOException, a network failure or Signal's refusal of a code or key
# a network failure in signal-cli 0.14.9's words (java.net's in its message, libsignal's class name in register's) or
# setup's own: only these make its IO error "no answer from Signal" (fix review, signal-S05-minus3-is-not-only-no-answer)
_NETWORK_WORDS = re.compile(
    r"java\.net\.|javax\.net\.|NetworkException|SocketTimeout|UnknownHost|ConnectException|Network is unreachable|"
    r"No route to host|Connection (?:refused|reset)|timed? ?out\b|no answer within|stopped without an answer",
    re.IGNORECASE,
)


def no_answer(code: int | None, why: str) -> bool:
    """signal-cli gave no answer from Signal: no code at all, or its IO error naming the network
    (signal-route2-network-as-wrong-key). signal-cli 0.14.9 gives every IOException that same -3, a wrong SMS code and
    a wrong Recovery key among them (its VerifyCommand and RegisterCommand): any other -3 is Signal's refusal (fix
    review, S05: a typo in the code read "Check the Wi-Fi")."""
    return code is None or (code == NETWORK_ERROR and _NETWORK_WORDS.search(why) is not None)


_EXPIRED = re.compile(r"\bexpired\b", re.IGNORECASE)


def code_expired(code: int | None, why: str) -> bool:
    """Signal takes this SMS code no more (sms-code-expired): signal-cli 0.14.9's -3, "Verify error: Verification
    code expired, please request a new one by registering again." (its MustRequestNewCodeException). Typed again, it
    fails the same way, each try one more verify: only a new registration sends a new code."""
    return code == NETWORK_ERROR and _EXPIRED.search(why) is not None


_UTC_TIME = re.compile(
    r"\b(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2})?(?:\.\d+)?Z\b"
)  # signal-cli's "2026-10-25T09:40Z"


def _local(found: re.Match[str], tz: tzinfo | None) -> str:
    when = datetime.fromisoformat(f"{found.group(1)}T{found.group(2)}+00:00").astimezone(tz)
    return f"{when:%H:%M} on {when.day} {when:%b} (local time)"


def local_times(text: str, tz: tzinfo | None = None) -> str:
    """signal-cli's UTC times in `text` as the Mac's clock shows them ("11:40 on 25 Oct (local time)"; `tz`: the
    Mac's zone unless a test gives one): Signal's limit names when to try again."""
    return _UTC_TIME.sub(lambda found: _local(found, tz), text)


def signal_wait(why: str, tz: tzinfo | None = None) -> str:
    """The time Signal's limit names for the next try, as the Mac's clock shows it ("" when it names none)."""
    found = _UTC_TIME.search(why)
    return "" if found is None else _local(found, tz)


def login_failed(why: str, code: int | None) -> str:
    """Why moving the account here did not work, in words for what happened: Signal's limit on registrations (-5),
    no answer from Signal (no code, or a -3 naming the network: `no_answer`), or a refusal (check the Account ID and
    the Recovery key; 0.14.9 gives a wrong key -3 too)."""
    reason = why.strip()[:300]
    if code == RATE_LIMIT_ERROR:
        return prompts.SETUP_LOGIN_LIMITED.format(reason=local_times(" ".join(reason.split())))
    if no_answer(code, why):
        return prompts.SETUP_LOGIN_NO_ANSWER.format(reason=reason)
    return prompts.SETUP_LOGIN_FAILED.format(reason=reason)


def register_failed(why: str, code: int | None, tz: tzinfo | None = None) -> str:
    """A number's registration Signal refused, as a stop with its Fix (signal-register-errors-raw, round 1): Signal's
    limit (-5, the wait in local time), no answer (no code, or a -3 naming the network: the Wi-Fi), or any other
    reason (the way without a number)."""
    reason = " ".join(why.split())[:300]
    if code == RATE_LIMIT_ERROR:
        limited = prompts.SETUP_REGISTER_LIMITED if signal_wait(why) else prompts.SETUP_REGISTER_LIMITED_NO_TIME
        return limited.format(reason=local_times(reason, tz))
    if no_answer(code, why):
        return prompts.SETUP_REGISTER_NO_ANSWER.format(reason=reason)
    return prompts.SETUP_REGISTER_FAILED.format(reason=reason)


def limit_line(when: str) -> str:
    """The first paragraph of the window's screen at Signal's limit: its wait (`when`, `signal_wait`), or that Signal
    named none, never a time it did not show (review S09)."""
    return prompts.SETUP_WINDOW_LOGIN_UNTIL.format(when=when) if when else prompts.SETUP_WINDOW_LOGIN_NO_TIME


def card_message(code: int | None, when: str = "", *, why: str = "") -> str:
    """The line the reopened cards show: Signal's limit names its wait (`when`, `signal_wait`: c-13, confirm-2), no
    answer from Signal names the network (`no_answer`, by signal-cli's words `why`); never a refusal for either."""
    if code == RATE_LIMIT_ERROR:
        return prompts.SETUP_CARD_LIMITED_UNTIL.format(when=when) if when else prompts.SETUP_CARD_LIMITED
    return prompts.SETUP_CARD_NO_ANSWER if no_answer(code, why) else prompts.SETUP_CARD_REFUSED


def _route(value: Value, answers: Known, mac: Mac) -> Checked:
    answer = clean_paste(value, spaces=True)[0].lower() if isinstance(value, str) else ""
    if answer in ("1", "number"):
        return Checked("number")
    if answer in ("2", "no_number"):
        return Checked("no_number")
    return Checked(None, prompts.SETUP_ACCOUNT_UNKNOWN)


def country_code(number: str) -> str:
    """A number's own country code (its first one to three digits after the +, `CALLING_CODES`); "" for none known."""
    digits = number.removeprefix("+")
    return next((digits[:size] for size in (3, 2, 1) if digits[:size] in _CODES), "")


def _groups(digits: str) -> list[str]:
    """The digits after the country code in groups of 3, the last one or two of 4, as numbers are read out."""
    count = len(digits)
    if count <= 4:
        return [digits] if digits else []
    if count % 3 == 0:
        sizes = [3] * (count // 3)
    elif count % 3 == 1:
        sizes = [3] * ((count - 4) // 3) + [4]
    else:
        sizes = [3] * ((count - 8) // 3) + [4, 4] if count >= 8 else [2, 3]
    at, parts = 0, []
    for size in sizes:
        parts.append(digits[at : at + size])
        at += size
    return parts


def spaced(number: str) -> str:
    """Her number as it is shown back (spec: "+30 691 234 5678: right?"): the country code, then the digits in groups."""
    code = country_code(number)
    return "+" + " ".join([part for part in (code, *_groups(number.removeprefix("+")[len(code) :])) if part])


def _national(digits: str, country: str, code: str) -> str:
    """A number dialled at home without what home dialling adds before it (`_TRUNK`): +1's 1, most regions' 0."""
    if code == "1":
        return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits
    trunk = "" if country in _KEEPS_ZERO else _TRUNK.get(country, "0")
    return digits[len(trunk) :] if trunk and digits.startswith(trunk) else digits


def _symbol(char: str) -> str:
    return f"'{char}'" if char.isprintable() and not char.isspace() else f"U+{ord(char):04X}"


def _number(value: Value, answers: Known, mac: Mac) -> Checked:
    """Her number in its + form: spaces, dashes, dots and brackets left out, and a paste's hidden characters; 00 is
    the +; without either, the code of the Mac's region goes in front (spec: "Country preselected from the macOS
    region"). 8 to 15 digits with the country code, as Signal and the config take them (E.164; open question 14: the
    spec counts after the code). Refused in one sentence, never ending setup."""
    from local_assistant.setup import _NUMBER  # setup.py's (it imports this module)

    typed = clean_paste(value if isinstance(value, str) else "", spaces=True)[0].translate(_DIALLED)
    if not typed:
        return Checked(None, prompts.SETUP_NUMBER_DIGITS.format(count=0))
    if typed.startswith("00"):
        typed = "+" + typed[2:]
    if not typed.startswith("+"):
        code = CALLING_CODES.get(mac.country, "")
        if not code:
            return Checked(None, prompts.SETUP_NUMBER_BAD)
        typed = f"+{code}{_national(typed, mac.country, code)}"
    digits = typed[1:]
    odd = next((char for char in digits if not (char.isascii() and char.isdigit())), None)
    if odd is not None:
        return Checked(None, prompts.SETUP_NUMBER_NOT_DIGIT.format(symbol=_symbol(odd)))
    number = "+" + digits
    if not _NUMBER.match(number):
        return Checked(None, prompts.SETUP_NUMBER_DIGITS.format(count=len(digits)))
    return Checked(number, confirm=spaced(number))  # shown back before Signal counts it (spec rule 8): "…: right?"


# your login keychain's items for her account without a number (account: your macOS user name)
KEYCHAIN_KEY = "estia-signal-recovery-key"
KEYCHAIN_ID = "estia-signal-account-id"


def keychain_lines(key: Saved, account_id: Saved) -> str:
    """What setup says of the two keychain saves: one line for both when they went the same way, else a line for
    each, and the password manager's advice whenever one was not kept (review SEC-5)."""
    if key is Saved.KEPT and account_id is Saved.KEPT:
        return prompts.SETUP_KEYCHAIN_KEPT
    if {key, account_id} <= {Saved.NOT_KEPT, Saved.LOCKED}:
        return prompts.SETUP_KEYCHAIN_NOT_KEPT
    lines = {
        Saved.KEPT: prompts.SETUP_KEYCHAIN_ONE_KEPT,
        Saved.REMOVED: prompts.SETUP_KEYCHAIN_REMOVED,
        Saved.NOT_KEPT: prompts.SETUP_KEYCHAIN_ONE_NOT_KEPT,
        Saved.LOCKED: prompts.SETUP_KEYCHAIN_ONE_NOT_KEPT,
    }
    said = [
        lines[key].format(item=prompts.SETUP_KEYCHAIN_KEY_ITEM),
        lines[account_id].format(item=prompts.SETUP_KEYCHAIN_ID_ITEM),
        prompts.SETUP_KEYCHAIN_KEEP_BOTH,
    ]
    return "\n".join(said)


CAPTCHA_START = "signalcaptcha://"
CAPTCHA_PAGE = "https://signalcaptchas.org/registration/generate.html"  # where Signal's captcha is solved
WORD_REASONS_SECONDS = 60  # how long setup looks for the one-time word before it lists the usual reasons (spec)
# signal-cli 0.14.9's updateAccount for a username someone has: "Failed to set username: Username is not available"
_TAKEN = re.compile(r"not available|taken", re.IGNORECASE)
# signal-cli 0.14.9's verify (VerifyCommand): "This number is locked with a pin" (no PIN given) and "Invalid pin, tries
# remaining" (a wrong one); "pin data has been deleted" is no question of a PIN
_PIN_NEEDED = re.compile(r"locked with a pin|invalid pin", re.IGNORECASE)
_PIN_RULES = {  # harden.pin_problem's rules (4 to 64 characters, no spaces), in the spec's words
    "at least 4 characters": prompts.SETUP_PIN_SHORT,
    "at most 64 characters": prompts.SETUP_PIN_LONG,
    "no spaces": prompts.SETUP_PIN_SPACES,
}


def _captcha(value: Value, answers: Known, mac: Mac) -> Checked:
    """Signal's captcha link as pasted (a paste's spaces, breaks and hidden characters left out): it starts with
    signalcaptcha://; shown back by its start and length only."""
    link = clean_paste(value if isinstance(value, str) else "", spaces=True)[0]
    if not link.startswith(CAPTCHA_START) or link == CAPTCHA_START:
        return Checked(None, prompts.SETUP_CAPTCHA_NOT_YET)
    return Checked(link, shown=prompts.SETUP_CAPTCHA_SHOWN.format(count=len(link)))


def _code(value: Value, answers: Known, mac: Mac) -> Checked:
    """Signal's six-digit code (the SMS code, the six-digit code), spaces and dashes left out."""
    digits = clean_paste(value if isinstance(value, str) else "", spaces=True)[0].replace("-", "")
    if len(digits) != hostsec.TOTP_DIGITS or not (digits.isascii() and digits.isdigit()):
        return Checked(None, prompts.SETUP_CODE_DIGITS)
    return Checked(digits)


def _pin(value: Value, answers: Known, mac: Mac) -> Checked:
    """A Registration Lock PIN by harden's rules (the same as the security step's), a paste's hidden characters left
    out; never shown back."""
    pin = "".join(char for char in (value if isinstance(value, str) else "") if not hostsec.invisible(char))
    problem = harden.pin_problem(pin)
    return Checked(None, _PIN_RULES.get(problem, problem)) if problem else Checked(pin)


def _read(reader: Callable[[str], str]) -> Check:
    """A check from one of hostsec's key readers: the form it gives back, or its line (never the key)."""

    def check(value: Value, answers: Known, mac: Mac) -> Checked:
        try:
            return Checked(reader(value if isinstance(value, str) else ""))
        except ValueError as exc:
            return Checked(None, str(exc))

    return check


def _number_words(answers: Known, mac: Mac) -> Variant:
    """The number's line says which code goes in front when the Mac's region names one."""
    code = CALLING_CODES.get(mac.country, "")
    return {"ask": prompts.SETUP_NUMBER_ASK_REGION.format(code=code)} if code else {}


def nickname(name: str) -> str:
    """Her username's first part, from her name (spec: "Pre-filled 'estia.' from her name"): in small letters, its
    accents left out, any other run of characters one _, never a digit first; "" when fewer than 3 remain (or none
    is a to z: a Greek name), and the person types it whole."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    kept = re.sub(r"[^a-z0-9]+", "_", plain).strip("_").lstrip("0123456789_")[:32]
    return kept if len(kept) >= 3 else ""


def username_for(typed: str, nick: str) -> Checked:
    """Her username as typed: the digits alone take her nickname in front ("42": estia.42); then Signal's form
    (`hostsec.username`, libsignal's rule), refused in the spec's words."""
    answer = clean_paste(typed, spaces=True)[0]
    if nick and answer.isascii() and answer.isdigit():
        answer = f"{nick}.{answer}"
    try:
        return Checked(hostsec.username(answer))
    except ValueError:
        return Checked(None, prompts.SETUP_USERNAME_RULE)


def _username(value: Value, answers: Known, mac: Mac) -> Checked:
    from local_assistant.config import DEFAULT_NAME

    name = answers.get("name")
    return username_for(
        value if isinstance(value, str) else "", nickname(name if isinstance(name, str) else DEFAULT_NAME)
    )


def another_username(taken: str) -> str:
    """One more to try for a username Signal says is taken: the same nickname, two other digits (10 to 99)."""
    nick, _, digits = taken.rpartition(".")
    while True:
        other = str(secrets.randbelow(90) + 10)
        if other != digits:
            return f"{nick}.{other}"


def typed_stop(tty: str = hostsec.TTY) -> bool:
    """q and Enter typed on the terminal while setup waits by itself (the one-time word): what was typed is read
    without waiting (only whole lines come in the terminal's line mode), and none of it is left for later."""
    try:
        fd = os.open(tty, os.O_RDONLY | os.O_NONBLOCK | os.O_NOCTTY)
    except OSError:
        return False
    data = b""
    try:
        while select.select([fd], [], [], 0)[0]:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            data += chunk
    except OSError:
        pass
    finally:
        os.close(fd)
    return any(line.strip().lower() == b"q" for line in data.splitlines())


def _first_install(answers: Known, mac: Mac) -> bool:
    return not mac.configured


def _by_number(answers: Known, mac: Mac) -> bool:
    """A first install on the spare number's route (or the route not answered yet)."""
    return not mac.configured and answers.get("route") in (None, "number")


def _signal_app(value: Value, answers: Known, mac: Mac) -> Checked:
    """Signal on your phone: a yes goes on; a no says where to get it, and setup waits for the yes."""
    if yes_no(value, answers, mac).value is True:
        return Checked(True)
    return Checked(None, prompts.SETUP_SIGNAL_APP_FIRST)


# harden's Registration Lock note and the phone's steps (`estia signal-login`'s too, printed plain there) with their key
# words in bold on setup's screens (owner, 28 Oct): the thing to keep, and the buttons the phone shows
LOCK_NOTE = bolded(harden.LOCK_NOTE, "password manager")
NUMBERLESS_STEPS = bolded(hostsec.NUMBERLESS_STEPS, "Register without number", "Pay", "password manager")

# her Signal account and who may talk to her: Signal on your phone, the route, her number and the Registration Lock's
# yes or no in the plan (owner, 27 Oct: "move them up"), kept before anything installs; the captcha, the codes, the
# PINs, the key card, her username and the one-time word at the Signal step
QUESTIONS: tuple[Question, ...] = (
    Question(
        "signal_app",
        "yes_no",
        "Signal on your phone",
        lead=prompts.SETUP_SIGNAL_APP,
        ask=prompts.SETUP_SIGNAL_APP_ASK,
        recommended="yes",
        check=_signal_app,  # a no is refused with where to get Signal: setup waits for the yes
        shows=_first_install,
    ),
    Question(
        "route",
        "cards",
        "Her Signal account",
        lead=prompts.SETUP_ACCOUNT_LEAD,
        listing=prompts.SETUP_ACCOUNT_LIST,  # Terminal's list of the cards; the window draws the cards
        ask=prompts.SETUP_ACCOUNT_ASK,
        options=(
            Option(
                "number", prompts.SETUP_ROUTE_NUMBER, line=prompts.SETUP_ROUTE_NUMBER_LINE, tag=RECOMMENDED, short="1"
            ),
            Option("no_number", prompts.SETUP_ROUTE_NO_NUMBER, line=prompts.SETUP_ROUTE_NO_NUMBER_LINE),
        ),
        recommended="number",
        advice=prompts.SETUP_ACCOUNT_ADVICE,
        check=_route,
        shows=_first_install,
    ),
    Question(
        "number",
        "number",
        "Her number",
        lead=prompts.SETUP_NUMBER,
        ask=prompts.SETUP_NUMBER_ASK,
        stop=prompts.SETUP_NUMBER_STOP,  # the quiet way out (owner, 27 Oct): the answers so far are kept
        stop_typed=prompts.SETUP_NUMBER_STOP_TYPED,
        placeholder="+30 690 000 0000",
        check=_number,
        vary=_number_words,
        shows=_by_number,
        confirm="number_right",
    ),
    Question(  # her number shown back once it fits (spec: "+30 691 234 5678: right?"); a no asks it again
        "number_right",
        "yes_no",
        "Her number",
        lead=prompts.SETUP_NUMBER_RIGHT_LEAD,
        ask=prompts.SETUP_NUMBER_RIGHT,
        recommended="yes",
        advice=prompts.SETUP_NUMBER_RIGHT_ADVICE,
        check=yes_no,
        keep="step",
        plan=False,
    ),
    Question(
        "known_number",
        "yes_no",
        "Her number",
        lead=prompts.SETUP_KNOWN_NUMBER,
        ask=prompts.SETUP_KNOWN_NUMBER_ASK,
        recommended="yes",  # "yes, when it is her number": the window's main button (walk-through, 27 Oct)
        advice=prompts.SETUP_KNOWN_NUMBER_ADVICE,
        check=yes_no,
        keep="step",
        plan=False,
    ),
    Question(
        "lock",
        "yes_no",
        "Registration Lock",
        lead=f"{LOCK_NOTE}\n\n{prompts.SETUP_LOCK_HOW}",
        window_lead=f"{LOCK_NOTE}\n\n{prompts.SETUP_WINDOW_LOCK_HOW}",  # no q in the window
        ask=prompts.SETUP_LOCK_ASK,
        recommended="yes",
        check=yes_no,
        shows=_by_number,
    ),
    Question(
        "captcha",
        "hidden",
        "Captcha link",
        lead=prompts.SETUP_CAPTCHA,
        window_lead=f"{prompts.SETUP_CAPTCHA_STEPS}\n{prompts.SETUP_WINDOW_CAPTCHA_PASTE}",
        ask=prompts.SETUP_CAPTCHA_ASK,
        check=_captcha,
        keep="never",
        plan=False,
        stop=prompts.SETUP_CAPTCHA_STOP,  # the window's way out; Terminal's words say q already
        links=(("signalcaptchas.org", CAPTCHA_PAGE),),  # the window's link to it (Terminal's lines name it)
    ),
    Question(
        "sms_code",
        "code",
        "SMS code",
        lead=prompts.SETUP_SMS_SENT,
        note=prompts.SETUP_NO_BACK_CODE,  # after the plan: no Back, and why (owner, 27 Oct)
        ask=prompts.SETUP_SMS_ASK,
        check=_code,
        keep="never",
        plan=False,
        stop=prompts.SETUP_CODE_STOP,  # the window's way out; Terminal's words say q already
    ),
    Question(
        "old_pin",
        "pin",
        "Old PIN",
        lead=prompts.SETUP_OLD_PIN,
        ask=prompts.SETUP_OLD_PIN_ASK,
        check=_pin,
        keep="never",
        plan=False,
        stop=prompts.SETUP_OLD_PIN_STOP,  # the window's way out (each wrong guess uses one of Signal's tries)
    ),
    Question(  # Terminal's q skips it, and the window's Later (signal-lock-pin-window-no-skip): no answer
        "lock_pin",
        "pin",
        "New PIN",
        window_lead=prompts.SETUP_WINDOW_LOCK_PIN,
        ask=prompts.SETUP_LOCK_PIN,
        check=_pin,
        keep="never",
        plan=False,
        later=prompts.SETUP_WINDOW_LATER,
    ),
    Question("lock_pin_again", "pin", "New PIN again", ask=prompts.SETUP_LOCK_AGAIN, keep="never", plan=False),
    Question(  # owner's choice: the Account ID is kept, in the answers file and your keychain; typed on the card
        "account_id",
        "key",
        "Account ID",
        lead=NUMBERLESS_STEPS,
        ask=hostsec.ID_ASK,
        check=_read(hostsec.account_id),
        layout=setup_keycard.ACCOUNT_ID,
        secret=False,
        plan=False,
        stop=prompts.SETUP_CARDS_STOP,  # the window's way out; Terminal's words say q, the key card Esc
    ),
    Question(  # never in the answers file: only in your login keychain, once Signal took it
        "recovery_key",
        "key",
        "Recovery key",
        ask=hostsec.KEY_ASK,
        window_ask=prompts.SETUP_WINDOW_KEY_ASK,  # the card has a box a group: no lines, no Enter, no q
        check=_read(hostsec.recovery_key),
        layout=setup_keycard.RECOVERY_KEY,
        keep="keychain",
        plan=False,
        stop=prompts.SETUP_CARDS_STOP,
    ),
    Question(
        "totp",
        "code",
        "Six-digit code",
        lead=prompts.SETUP_TOTP,
        ask=prompts.SETUP_TOTP_ASK,
        check=_code,
        keep="never",
        plan=False,
        stop=prompts.SETUP_TOTP_STOP,
    ),
    Question(
        "username",
        "text",
        "Username",
        lead=prompts.SETUP_USERNAME,
        ask=hostsec.USERNAME_ASK,
        placeholder="estia.42",
        check=_username,
        plan=False,
        stop=prompts.SETUP_USERNAME_STOP,  # the window's way out (review S01); Terminal's words say q already
    ),
    Question(
        "word",
        "wait",
        "One-time word",
        lead=prompts.SETUP_WORD,
        ask=prompts.SETUP_WORD_ASK,
        keep="step",
        plan=False,
        stop=prompts.SETUP_WORD_STOP,  # the window's way out while setup looks; Terminal's words say q, then Enter
    ),
)


# --redo signal: her Signal account's answers, asked again (the SMS code's line names it: prompts.SETUP_NO_BACK_CODE)
# the window's own screens where Terminal reads Enter or q (a step's own, asked each time)
QUESTIONS += (
    Question(
        "lock_retry",
        "yes_no",
        prompts.SETUP_WINDOW_LOCK_RETRY_TITLE,
        lead=prompts.SETUP_WINDOW_LOCK_RETRY,
        options=(
            Option("yes", prompts.SETUP_WINDOW_TRY_AGAIN, tag=RECOMMENDED),
            Option("no", prompts.SETUP_WINDOW_SKIP),
        ),
        recommended="yes",
        check=yes_no,
        keep="step",
        plan=False,
    ),
    Question(
        "login_again",
        "yes_no",
        prompts.SETUP_WINDOW_LOGIN_AGAIN_TITLE,
        lead=prompts.SETUP_WINDOW_LOGIN_AGAIN,
        options=(  # Signal's limit: each try before its time is one more registration (c-13, confirm-2)
            Option("yes", prompts.SETUP_WINDOW_TRY_AGAIN),
            Option("no", prompts.SETUP_WINDOW_STOP_HERE, tag=RECOMMENDED),
        ),
        recommended="no",
        check=yes_no,
        keep="step",
        plan=False,
    ),
)

SIGNAL_REDO = ("signal_app", "route", "number", "lock", "account_id", "username")


def _question(qid: str) -> Question:
    """One of this module's questions, by its id (setup_plan puts them in the one list)."""
    return next(q for q in QUESTIONS if q.id == qid)


class SignalSteps(WizardBase):
    """Her Signal account and who may talk to her: the steps setup runs on a first install."""

    def signal_question(self, qid: str, **words: str) -> Question:
        """One of the Signal step's questions as it is asked here: her name and `words` filled in."""
        return _question(qid).at(self.answers, self.mac, {"name": self.name, **words})

    # 2. the Signal number
    def registered_numbers(self) -> list[str]:
        """The numbers signal-cli finished registering here: listed in accounts.json, and its own account file says
        registered. signal-cli lists a number as soon as register starts, before the SMS code: a number whose code was
        never typed is registered again, never offered."""
        signal_data = Path.home() / ".local/share/signal-cli"
        try:
            listed = hostsec.signal_listed(signal_data, "")
        except (OSError, ValueError):
            return []
        found: list[str] = []
        for item in listed:
            account = hostsec.account_file(signal_data, item) if item.get("number") else None
            if account is not None and account.get("registered") is True:
                found.append(str(item["number"]))
        return found

    def signal_account(self) -> str:
        """Her Signal account, one of two (owner, 23 Oct): a spare phone number (`signal_number`, as before; Enter) or
        an account without a number (`numberless`), as two cards, once Signal is on your phone. Both answers come from
        the plan (owner, 27 Oct), used quietly here; a step run on its own asks them. The config's `bot_number`: the
        number, or the Account ID."""
        if self.dry_run:  # the plan showed Signal on your phone and the two routes
            self.say("  (what each route runs:)")
            number = self.signal_number()
            self.numberless()
            return number
        if self.answer("signal_app") is not True:  # the end of input: never a yes (`took` stops setup first)
            raise SystemExit(prompts.SETUP_NO_TERMINAL)
        return self.numberless() if self.answer("route") == "no_number" else self.signal_number()

    def her_number(self) -> str:
        """Her number from the plan, where it was shown back before anything counts (spec rule 8: "+30 690 000 0000:
        right?", a no asks it again), or from an earlier run's answers; asked so here only for a step run on its own.
        The end of input stops setup before anything is registered."""
        number = self.answer("number")
        if not isinstance(number, str):
            raise SystemExit(prompts.SETUP_NUMBER_STOPPED)
        return number

    def signal_number(self) -> str:
        from local_assistant.setup import PLACEHOLDER_NUMBER, registration_argv  # setup.py's (it imports this module)

        if self.dry_run:
            rpc = " ".join(registration_argv("signal-cli"))
            self.say(
                f"Signal number: {self.name}'s number from the plan, registered with\n"
                f"  $ {rpc}   (register <number> on stdin; with <captcha link> if Signal asks, typed hidden)\n"
                f"  $ {rpc}   (verify <number> with <SMS code> on stdin; the code is typed hidden)\n"
                f'Then, on the plan\'s yes to "{prompts.SETUP_LOCK_ASK} [Y/n]": a PIN typed hidden twice, set with\n'
                f"  $ {' '.join(harden.set_pin_argv('signal-cli', '<number>'))}   (setPin with <PIN> on stdin)\n"
                f"  ({harden.LOCK_NOTE})"
            )
            return PLACEHOLDER_NUMBER
        numbers = self.registered_numbers()
        # a number set up here is offered only as the plan's, or a kept one's (another SIM's never)
        planned = self.planned.get("number", self.answers.get("number") if self.answers.kept("number") else None)
        same = len(numbers) == 1 and planned in (None, numbers[0])
        if same and self.answer("known_number", number=numbers[0]):  # a run that stopped after the code
            return numbers[0]
        number = self.her_number()
        self.register_number(number)
        self.verify_number(number)
        self.say(f"✓ Signal: {number} is {self.name}'s number.")
        self.registration_lock(number)
        return number

    def register_number(self, number: str) -> None:
        """register on signal-cli's input; when Signal asks for a captcha (new numbers usually do), the link, asked
        hidden and checked, shown back by its start and length only; one Signal refuses is asked again with a new one
        (spec #11: no mistake ends setup). q stops; any other refusal stops setup by what happened (`register_failed`:
        Signal's limit and its wait in local time, no answer, or the reason and the way without a number)."""
        done, why, code = self.registration("register", {"account": number}, f"register {number}", {})
        refused = ""  # why the link before was refused: said on the captcha's screen
        while not done and "captcha" in why.lower():
            link = self.answer("captcha", refused=refused)
            if not isinstance(link, str):
                raise SystemExit(prompts.SETUP_CAPTCHA_STOPPED)
            token = link.removeprefix(CAPTCHA_START)
            done, why, code = self.registration(
                "register",
                {"account": number, "captcha": token},
                f"register {number} with <captcha link>",
                {link: "<captcha link>", token: "<captcha link>"},
            )
            link = token = ""
            refused = prompts.SETUP_CAPTCHA_REFUSED
        if not done:  # by what happened, with its Fix (signal-register-errors-raw)
            raise SystemExit(register_failed(why, code))

    def verify_number(self, number: str) -> None:
        """The SMS code, asked hidden and checked (six digits), sent on signal-cli's input; the Registration Lock PIN
        of the number's past only when Signal says it has one, then asked again until Signal takes it (the code kept).
        A code Signal refuses is asked again, the reason on its screen (no answer from Signal, a -3 naming the network:
        the Wi-Fi, never the code); Signal's limit stops, as a registration's does, its time in local time (review S05: each try was one
        more verify), and so does a code Signal says expired, with the stop's register-again lines (sms-code-expired);
        q stops. Neither is ever shown or kept."""
        refused = ""  # why the code before was refused: said on the code's screen
        while True:
            code = self.answer("sms_code", refused=refused, number=number)
            if not isinstance(code, str):
                raise SystemExit(prompts.SETUP_CODE_STOPPED)
            done, why, status = self.verify_once(number, code, "")
            pin_refused = ""
            while not done and _PIN_NEEDED.search(why):
                pin = self.old_pin(pin_refused)
                if not isinstance(pin, str):
                    raise SystemExit(prompts.SETUP_OLD_PIN_STOPPED)
                done, why, status = self.verify_once(number, code, pin)
                pin = ""
                pin_refused = self._said(prompts.SETUP_OLD_PIN_FAILED, prompts.SETUP_WINDOW_OLD_PIN_FAILED, why)
            code = ""
            if done:
                return
            if status == RATE_LIMIT_ERROR:
                raise SystemExit(register_failed(why, status))
            if no_answer(status, why):
                refused = self._said(prompts.SETUP_CODE_NO_ANSWER, prompts.SETUP_WINDOW_CODE_NO_ANSWER, why)
            elif code_expired(status, why):  # never "Check the code": every retype fails the same way, in either face
                raise SystemExit(prompts.SETUP_CODE_EXPIRED.format(reason=why.strip()[:300]))
            else:
                refused = self._said(prompts.SETUP_CODE_FAILED, prompts.SETUP_WINDOW_CODE_FAILED, why)

    def old_pin(self, refused: str) -> Value | None:
        """The number's old Registration Lock PIN (Signal said it has one). The window's Stop here ends setup with
        the lock's ways out, as q does in Terminal (review S02: a Ctrl-C's "run setup again" met the same lock)."""
        try:
            return self.answer("old_pin", refused=refused)
        except KeyboardInterrupt:
            if not self.in_window:
                raise
            raise SystemExit(prompts.SETUP_WINDOW_OLD_PIN_STOPPED) from None

    def _said(self, typed: str, window: str, why: str) -> str:
        """Signal's refusal in the words of the face that asks: Terminal's name q, the window's (it has its own Stop
        here) do not."""
        return (window if self.face.asking == "window" else typed).format(reason=why.strip()[:300])

    def verify_once(self, number: str, code: str, pin: str) -> harden.RpcAnswer:
        """One verify on signal-cli's input, shown without the code and the PIN (the terminal may be an AI's
        transcript): done, why not, and the error's code (-5 Signal's limit; none, or a -3 naming the network: no
        answer; any other -3, a wrong code among them, Signal's refusal)."""
        params: dict[str, object] = {"account": number, "verificationCode": code, **({"pin": pin} if pin else {})}
        return self.registration(
            "verify",
            params,
            f"verify {number} with <SMS code>{' and <PIN>' if pin else ''}",
            {code: "<SMS code>", pin: "<PIN>"},
        )

    def registration_lock(self, number: str) -> None:
        """Right after registering: a PIN typed hidden twice, set over signal-cli's JSON-RPC on stdin (never argv,
        never printed). Skipped, scripts/harden.sh offers it again."""
        if not self.answer("lock"):  # Enter turns it on (owner, 19 Oct)
            self.say("  Later: scripts/harden.sh offers it again.")
            return
        shown = harden.set_pin_argv(self.signal_cli, number)
        again = self.signal_question("lock_pin_again")
        refused = ""  # the two PINs differed: said on the next New PIN screen, which the window draws (review S03)
        while True:
            pin = self.answer("lock_pin", refused=refused)  # its rules in the spec's words, asked again until it fits
            refused = ""
            if not isinstance(pin, str):  # q, or the window's Later
                self.say("  Skipped: scripts/harden.sh offers it again.")
                return
            if self.answer("lock_pin_again") != pin:
                refused = prompts.SETUP_PIN_MISMATCH
                continue
            self.face.checked(again, Checked(pin, shown=prompts.SETUP_PIN_MATCHES))
            self.command("  setPin with <PIN> on stdin:\n  $ " + " ".join(shown))
            done, why = harden.set_pin(self.signal_cli, number, pin)
            pin = ""
            if done:
                self.say("✓ Registration Lock: on. Keep the PIN in your password manager.")
                return
            self.say(f"✗ Registration Lock: not set ({why}).")
            if not self.again(prompts.SETUP_LOCK_RETRY, qid="lock_retry", reason=why.strip()[:200]):
                return

    def numberless(self) -> str:
        """An account without a number (Signal Login), made once in Signal for Android (`hostsec.NUMBERLESS_STEPS`):
        its Account ID and its Recovery key typed on the key card, laid out as Signal's (gum's face, the window), or
        hidden across lines (plain lines: `hostsec.read_hidden_lines`, a key shown on several lines read whole, never
        left for the shell); checked (`hostsec.account_id`, `recovery_key`) and given to signal-cli's register on its
        input (`recoveryKey`, never an argument; `reregister`, so a run again takes it over again). One Signal refuses
        reopens the cards with every group kept. Once Signal took them, both go into your login keychain and the
        Account ID into the answers file (a run after a stop uses it, asking for the key only). Then her profile
        name and a username, how you find her. The Account ID."""
        from local_assistant.setup import PLACEHOLDER_ACI, registration_argv  # setup.py's (it imports this module)

        if self.dry_run:
            rpc = " ".join(registration_argv("signal-cli"))
            one = " ".join(harden.set_pin_argv("signal-cli", "<Account ID>"))
            self.say(prompts.SETUP_NUMBERLESS_DRY_RUN.format(rpc=rpc, one=one))
            return PLACEHOLDER_ACI
        kept = self.answers.get("account_id") if self.answers.kept("account_id") else None
        if isinstance(kept, str):  # else the Account ID's card shows them (its question's lead)
            self.say(NUMBERLESS_STEPS)
        id_groups: list[str] = []
        key_groups: list[str] = []
        message = ""
        while True:
            if isinstance(kept, str):
                account, kept = kept, None
                self.say(
                    prompts.SETUP_KEPT.format(shown=f"Account ID: {hostsec.shown_account(account)}", id="account_id")
                )
            else:
                account, id_groups = self.card_entry("account_id", setup_keycard.ACCOUNT_CARD, id_groups, message)
            # a run before moved it: never registered again, one more registration while Signal may limit them (S8)
            if ownuser.has_account(Path.home() / ".local/share/signal-cli", account):
                self.say(prompts.SETUP_LOGIN_HERE.format(name=self.name))
                self.answers.set("account_id", account)
                break
            key, key_groups = self.card_entry("recovery_key", setup_keycard.RECOVERY_CARD, key_groups, message)
            params: dict[str, object] = {"account": account, "recoveryKey": key, "reregister": True}
            secrets = {key: "<Recovery key>", account: "<Account ID>"}
            done, why, code = self.registration(  # shown with placeholders: the terminal may be an AI's transcript
                "register", params, "register <Account ID> with <Recovery key>", secrets
            )
            if not done and "totp" in why.lower():  # signal-cli's register --totp (review S7)
                totp = self.answer("totp")
                if not isinstance(totp, str):
                    raise SystemExit(prompts.SETUP_LOGIN_STOPPED)
                done, why, code = self.registration(
                    "register",
                    {**params, "totp": totp},
                    "register <Account ID> with <Recovery key> and <code>",
                    {**secrets, totp: "<code>"},
                )
                totp = ""
            params.clear()  # the key goes as soon as signal-cli has it
            secrets.clear()
            if done:
                self.say(prompts.SETUP_LOGIN_DONE.format(name=self.name))
                self.answers.set("account_id", account)
                self.keep_in_keychain(account, key)
                key = ""
                break
            key = ""
            self.say(login_failed(why, code))
            self.flush()  # an Enter typed after the key, while signal-cli worked, is no answer to this
            asked = (
                code == RATE_LIMIT_ERROR or not self.keys_on_a_card()
            )  # a card reopens by itself; Signal's wait does not
            later = prompts.SETUP_LOGIN_AGAIN_LATER if code == RATE_LIMIT_ERROR else prompts.SETUP_LOGIN_AGAIN
            when = signal_wait(why) if code == RATE_LIMIT_ERROR else ""
            if asked and not self.again(later, qid="login_again", limit=limit_line(when)):
                raise SystemExit(prompts.SETUP_LOGIN_STOPPED)
            message = card_message(code, when, why=why)  # the cards reopen with every group kept
        self.profile_name(account)
        self.username = self.her_username(account)
        return account

    def keys_on_a_card(self) -> bool:
        """The Account ID and the key are typed on a card here: by the face that asks now (the setup window's Terminal
        face once it closed), gum's in a terminal wide enough, or the setup window's own; plain lines read them
        hidden."""
        asking = self.face.asking
        return asking == "window" or (asking == "gum" and setup_keycard.fits(hostsec.TTY))

    def card_entry(self, qid: str, card: setup_keycard.Card, groups: list[str], message: str) -> tuple[str, list[str]]:
        """The Account ID or the Recovery key: its question shown first (the window's card screen; in Terminal its
        lines), then on the card (its groups kept from a try before, and the line why) or typed hidden, the line why
        said first; asked again with the reason until it fits. The form signal-cli takes, and the groups typed.
        q, Esc, Ctrl-D or no terminal stops setup (never stdin)."""
        q = self.signal_question(qid)
        if message:  # asked again: its lines were said the first time
            q = replace(q, lead="")
        self.face.show(q, None)
        while True:
            if message and not self.keys_on_a_card():  # a hidden read keeps no group: it is typed again (W13)
                self.say(f"  {message.replace(prompts.SETUP_CARD_KEPT, prompts.SETUP_CARD_TYPED_AGAIN)}")
            try:
                typed = self.face.key(q, message, tuple(groups))
            except OSError:
                raise SystemExit(prompts.SETUP_NO_TERMINAL) from None
            if typed is None or typed.strip().lower() == "q":
                raise SystemExit(prompts.SETUP_LOGIN_STOPPED)
            try:
                read = card.read(typed), setup_keycard.split(card, typed)
            except ValueError as exc:  # the count, the place and a symbol: never the key, nor a letter or digit of it
                message = str(exc)
                groups = setup_keycard.split(card, typed)
                continue
            # taken: the window's card leaves for its log, never left readable while Signal and the keychain work
            # (review S04: "This screen is cleared when you leave it"); its page keeps the groups for a refusal
            self.face.checked(q, Checked(True))
            return read

    def keep_in_keychain(self, account: str, key: str) -> None:
        """Once Signal took them: the Recovery key and the Account ID typed here, written once into your login
        keychain (owner, 25 Oct), as Signal shows them (its own copy button's form), on macOS's security tool's
        input only. Each trusts no program (owner, 27 Oct: "A"), so every read of it asks in a macOS dialog; setup
        checks each save by the item's attributes only, never reading it back (`setup_keychain.save`, `asks`). A
        locked keychain (over SSH it usually is) is opened first on the terminal: macOS asks for its password there,
        hidden, once (in the window, macOS's own box asks). Each is saved on its own, and a line names each one not kept (review SEC-5: a copy from
        before, deleted first, may be gone too). Kept or not, setup goes on."""

        def locked() -> None:  # Terminal's names the Mac password typed there; the window's, macOS's own box
            self.say(prompts.SETUP_WINDOW_KEYCHAIN_LOCKED if self.in_window else prompts.SETUP_KEYCHAIN_LOCKED)

        owner = pwd.getpwuid(os.getuid()).pw_name
        shown_key = "".join(map(setup_keycard.RECOVERY_CARD.shown, key))
        opened: list[bool] = []  # setup opened the keychain: locked again after both (review SEC-3)
        key_saved = setup_keychain.save(
            KEYCHAIN_KEY,
            owner,
            shown_key,
            label=prompts.SETUP_KEYCHAIN_KEY_LABEL,
            before_unlock=locked,
            opened=opened,
            asks=True,
            opener=self.keychain_opener(),
        )
        shown_key = ""
        id_saved = (
            Saved.LOCKED  # a wrong Mac password: never asked twice
            if key_saved is Saved.LOCKED
            else setup_keychain.save(
                KEYCHAIN_ID,
                owner,
                account.upper(),
                label=prompts.SETUP_KEYCHAIN_ID_LABEL,
                before_unlock=locked,
                opened=opened,
                asks=True,
                opener=self.keychain_opener(),
            )
        )
        setup_keychain.lock_again(opened)
        self.say(keychain_lines(key_saved, id_saved))

    def account_rpc(self, account: str, method: str, params: dict[str, object], shown: str) -> tuple[bool, str]:
        """One request to signal-cli's JSON-RPC for her account (as the PIN's), on its input; printed first with a
        placeholder for her Account ID."""
        from local_assistant.setup import REGISTER_SECONDS  # setup.py's (it imports this module)

        self.command(f"  {shown} on stdin:\n  $ " + " ".join(harden.set_pin_argv(self.signal_cli, "<Account ID>")))
        argv = harden.set_pin_argv(self.signal_cli, account)
        return harden.json_rpc(argv, method, params, secrets={account: "<Account ID>"}, timeout=REGISTER_SECONDS)

    def profile_name(self, account: str) -> None:
        """Her profile name right after the move (signal-cli issue 2124: a move can lose it); the bridge sets it at
        its first start too."""
        profile = prompts.signal_profile_name(self.name)
        done, why = self.account_rpc(
            account, "updateProfile", {"givenName": profile, "familyName": ""}, "updateProfile"
        )
        if done:
            self.say(prompts.SETUP_PROFILE_NAME.format(profile=profile))
        else:
            self.say(prompts.SETUP_PROFILE_LATER.format(reason=why.strip()[:200]))

    def her_username(self, account: str) -> str:
        """Her username: without a number, how you find her (new chat, search), her nickname from her name in front
        (the digits alone are enough). Signal's form is checked first, in the spec's words; one Signal says is taken
        gets another suggested, which Enter takes; q or no terminal stops setup. Kept in the answers file once Signal
        took it: a run after that sets it again without asking. A plain line in Terminal (gum never gets a typed name
        in its arguments); in the window its own screen, the suggestion filled in and Signal's reason on it. What was
        typed while signal-cli worked (an Enter after the key, as a blank line) is dropped first, and an Enter alone is
        asked again."""
        q = self.signal_question("username")
        nick = nickname(self.name)
        self.face.show(q, None)
        kept = self.answers.get("username") if self.answers.kept("username") else None
        if isinstance(kept, str):
            self.face.show(q, kept)
        ask = prompts.SETUP_USERNAME_ASK_NICK.format(nickname=nick) if nick else hostsec.USERNAME_ASK
        suggestion = ""
        while True:
            if isinstance(kept, str):
                typed, kept = kept, None
            else:
                self.flush()
                answer = self.face.text(q, None, "") if self.in_window else self.typed(ask)
                if answer is None or answer.lower() == "q":
                    raise SystemExit(prompts.SETUP_USERNAME_STOPPED)
                typed = answer or suggestion
                if not typed:
                    continue
            result = username_for(typed, nick)
            if not isinstance(result.value, str):
                self.face.checked(q, result)
                continue
            name = result.value
            done, why = self.account_rpc(account, "updateAccount", {"username": name}, f"updateAccount {name}")
            if done:
                self.say(prompts.SETUP_USERNAME_FOUND.format(username=name))
                self.answers.set("username", name)
                return name
            if _TAKEN.search(why):
                suggestion = another_username(name)
                if self.in_window:  # its screen again, the suggestion filled in
                    self.face.previous = suggestion
                    self.face.show(q, None)
                    self.face.previous = None
                self.face.checked(
                    q, Checked(None, prompts.SETUP_USERNAME_TAKEN.format(username=name, other=suggestion))
                )
                ask = prompts.SETUP_USERNAME_TRY.format(other=suggestion)
                continue
            # on its screen, which the window holds until a word for it (review S01: only the log had it)
            refused = self._said(prompts.SETUP_USERNAME_FAILED, prompts.SETUP_WINDOW_USERNAME_FAILED, why)
            self.face.checked(q, Checked(None, refused))

    # 3. who may talk to her: the owner's account ID, found from a message they send
    def word_clock(self) -> float:
        """How long the wait for the one-time word has run (seconds, from any start)."""
        return time.monotonic()

    def word_stopped(self) -> bool:
        """q and Enter typed on the terminal while setup looks for the word by itself."""
        return typed_stop(hostsec.TTY)

    def receive_quietly(self, command: list[str]) -> subprocess.CompletedProcess[str]:
        """A look again for the word: the same fixed command as the first, not printed again at every look."""
        return subprocess.run(command, text=True, check=False, capture_output=True)

    def owner(self, number: str) -> str:
        """Who may talk to her: the account that sends the one-time word shown here (a profile name can be copied).
        The word is shown on a line of its own; setup looks by itself every RECEIVE_SECONDS, no Enter (spec), and
        after WORD_REASONS_SECONDS without it lists the usual reasons, then looks on; q and Enter stop. In the window
        the reasons come on the word's screen, in its own words (second deep scan: they stayed in Terminal, behind
        it), and its Stop here stops (`WindowFace.wait`)."""
        from local_assistant.setup import IPV4, PLACEHOLDER_ACI, RECEIVE_SECONDS  # setup.py's (it imports this module)

        if self.dry_run:
            self.say(prompts.SETUP_WORD_DRY_RUN.format(word=one_time_word(self.name, 4821), ipv4=IPV4))
            return PLACEHOLDER_ACI
        word = one_time_word(self.name, secrets.randbelow(9000) + 1000)
        numberless = hostsec.is_account_id(number)  # found by her username; her Account ID never shown
        words = {"name": self.name, "word": word, "number": number, "username": self.username}
        q = replace(self.signal_question("word", **words), options=(Option(word, word),))  # the window: large, Copy
        if numberless:
            q = replace(q, lead=fill(prompts.SETUP_WORD_USERNAME, words))
        self.face.show(q, None)
        self.say(f"\n    {word}\n")
        ask = prompts.SETUP_WINDOW_WORD_ASK if self.in_window else prompts.SETUP_WORD_ASK  # the window: Stop here
        self.say(ask.format(seconds=RECEIVE_SECONDS))
        receive = [  # her data folder by name, as register's (signal-04)
            self.signal_cli,
            IPV4,
            "--config",
            str(signal_data()),
            *harden.TRUST,
            "-o",
            "json",
            "-a",
            number,
            "receive",
            "-t",
            str(RECEIVE_SECONDS),
        ]
        shown = [("<Account ID>" if part == number else part) for part in receive] if numberless else None
        found: list[str | None] = []

        def look() -> bool:  # the same word on every look: the owner is whoever sent it
            got = self.run(receive, shown=shown, capture_output=True) if not found else self.receive_quietly(receive)
            found.append(owner_by_word(senders(got.stdout.splitlines()), word))
            return found[-1] is not None

        started, reasons = self.word_clock(), False
        while True:
            if self.face.wait(q, look, RECEIVE_SECONDS) and found and (aci := found[-1]) is not None:
                if aci.lower() == number.lower():  # your own account typed as hers (review S9)
                    raise SystemExit(prompts.SETUP_OWNER_IS_HER)
                self.say(prompts.SETUP_WORD_RECEIVED)
                self.say(f"✓ Only your account can talk to {self.name}. Messages from anyone else are ignored.")
                return aci
            if self.word_stopped():
                raise SystemExit(prompts.SETUP_WORD_STOPPED.format(word=word))
            if not reasons and self.word_clock() - started >= WORD_REASONS_SECONDS:
                reasons = True
                if self.face.asking == "window":  # the word's screen again, the reasons on it (its Stop here too)
                    on_screen = (
                        prompts.SETUP_WINDOW_WORD_MISSING_USERNAME if numberless else prompts.SETUP_WINDOW_WORD_MISSING
                    )
                    self.face.show(replace(q, help=on_screen.format(**words)), None)
                missing = prompts.SETUP_WORD_MISSING_USERNAME if numberless else prompts.SETUP_WORD_MISSING
                self.say(missing.format(**words))
