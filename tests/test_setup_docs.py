"""The setup guide's extras (setup_assistant.md, section 3), one shape each, and the facts the docs review (26 Oct)
found wrong, held as they are now: Brave's free credit and Estia's 900, Fastmail's paid plan and menu path, one route
for keys over SSH, the mail switch that Microsoft names only "invalid_client". The guides setup shows use the same
words (prompts/setup_extras.py)."""

import re
from pathlib import Path

import pytest

from local_assistant import prompts
from local_assistant.setup_questions import plain_text

ROOT = Path(__file__).resolve().parents[1]
GUIDE = (ROOT / "setup_assistant.md").read_text(encoding="utf-8")
EXTRAS = (
    "Calendar (once)",
    "Mail (once)",
    "Lights and plugs (once)",
    "Voice notes (once)",
    "Calls (once)",
    "Spotify (once)",
    "Web search (once)",
    "Places and routes (once)",
    "Projects (extra, off by default)",
    "Documents (extra, off by default)",
    "Transposing chords (extra, off by default)",
)
SHAPE = ("**Before you start:**", "**Steps", "**How you know it worked:**", "**Common errors:**")
BRAVE = (  # the limit inside the claim (review SEC-4): Estia counts only its own searches, by calendar month
    "Brave Search: free with a $5 monthly credit (about 1,000 searches), needs a card. Estia stops at 900 of its own "
    "searches each calendar month, below the credit; a key also used by other programs can still go over it, so a key "
    "used only by Estia is recommended."
)


def flat(text: str) -> str:
    return " ".join(text.split())


def section(title: str) -> str:
    """One part of section 3, from its heading to the next one of its level or higher."""
    start = GUIDE.index(f"\n### {title}\n")
    found = re.search(r"\n#{2,3} ", GUIDE[start + 1 :])
    return GUIDE[start : start + 1 + found.start()] if found else GUIDE[start:]


@pytest.mark.parametrize("title", EXTRAS)
def test_every_extra_has_one_shape(title: str) -> None:
    """Before you start (account, rights, cost, time), the steps, how you know it worked, the common errors: in this
    order, each once."""
    text = section(title)
    places = [text.find(heading) for heading in SHAPE]
    assert all(place >= 0 for place in places), (title, places)
    assert places == sorted(places), title
    assert all(text.count(heading) == 1 for heading in SHAPE), title


@pytest.mark.parametrize("title", EXTRAS)
def test_how_you_know_it_worked_names_doctor_or_a_question_to_her(title: str) -> None:
    worked = flat(section(title).split("**How you know it worked:**", 1)[1].split("**Common errors:**", 1)[0])
    assert "ask her" in worked or "call her" in worked or "send her" in worked or "text her" in worked, title
    if title != "Transposing chords (extra, off by default)":  # chords have no doctor line: nothing to set up
        assert "doctor" in worked and ("○ or ✓" in worked or "✓" in worked), title


def test_the_menu_paths_say_when_they_were_checked_and_against_what() -> None:
    for title in ("Calendar (once)", "Mail (once)", "Spotify (once)"):
        steps = flat(section(title).split("**Steps", 1)[1].split("\n\n", 1)[0])
        assert "checked 27 Oct 2026" in steps and "on a live" in steps and "not" in steps, title


def test_brave_is_free_with_its_credit_and_estia_stops_at_900() -> None:
    for name in ("setup_assistant.md", "GUIDE.md"):
        text = flat((ROOT / name).read_text(encoding="utf-8"))
        assert BRAVE in text, name
        assert "2,000 searches" not in text and "2,000 free searches" not in text and "allows 2,000" not in text, name
    assert "$5" in prompts.SETUP_BRAVE_KEY and "900" in prompts.SETUP_BRAVE_KEY
    assert flat(plain_text(prompts.SETUP_BRAVE_KEY)).endswith(BRAVE)  # setup's question says it in the same words
    web = flat(section("Web search (once)"))
    assert "its own index, not Google's or Bing's, and does not profile searches" in web
    assert '"Customer may provide attribution", section 4(d)' in web  # Brave's terms: attribution is optional


def test_fastmail_needs_a_paid_plan_the_full_address_and_its_real_menu_path() -> None:
    calendar = flat(section("Calendar (once)"))
    assert "Fastmail's Basic plan has no CalDAV" in calendar
    assert (
        "**Settings** → **Privacy & Security** → **Connected apps & API tokens** → **Manage app passwords and access** → "
        "**New app password**"
    ) in calendar
    assert "Fastmail: the full address you signed up with, not an alias" in calendar
    assert (
        "https://caldav.fastmail.com/" in calendar and "Settings → Privacy & Security → App passwords" not in calendar
    )


def test_mail_names_the_rights_the_click_path_and_the_switch() -> None:
    mail = flat(section("Mail (once)"))
    assert "at least the Application Developer role" in mail
    assert "**Entra ID** → **App registrations** → **New registration**" in mail
    assert "**Allow public client flows**" in mail and "**Advanced settings**" in mail
    assert flat(prompts.MAIL_INVALID_CLIENT) in mail  # the message the sign-in says, word for word
    assert "made on or after 1 July 2026" in mail  # Microsoft's security defaults block the device-code sign-in there


def test_keys_over_ssh_have_one_route_the_hidden_prompt() -> None:
    """Over SSH the login keychain is not the default one and stays locked: every doc gives the hidden prompt for a key
    over SSH, and none tells to unlock the keychain there (the docs review: three docs gave three routes)."""
    for name in ("setup_assistant.md", "GUIDE.md", "README.md", "SETUP-WITH-YOUR-AI.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "security unlock-keychain" not in text and "unlock the keychain over SSH" not in text, name
    web = flat(section("Web search (once)"))
    assert "each typed once at a hidden prompt (also over SSH)" in web
    assert "/usr/bin/sudo /usr/local/estia/bin/estia secret set web.env OLLAMA_API_KEY" in web
    places = flat(section("Places and routes (once)"))
    assert "at a hidden prompt (also over SSH)" in places and "secret set web.env GOOGLE_MAPS_API_KEY" in places


def test_calls_give_disk_space_time_and_no_github_account() -> None:
    calls = flat(section("Calls (once)").split("**Steps", 1)[0])
    assert "several GB of disk and a long first build" in calls and "no GitHub account" in calls
    assert "admin rights once" in calls


def test_voice_notes_name_both_admin_commands_and_the_scripts_still_loading_words() -> None:
    """Review of 30 Oct (D11): the section said "admin rights once" and "not ready", while
    scripts/install-voice-daemon.sh runs two admin commands (her update, then the wait that reads her voice as her)
    and says "voice: still loading after 20 minutes" (prompts.SETUP_VOICE_STILL_LOADING)."""
    script = (ROOT / "scripts/install-voice-daemon.sh").read_text(encoding="utf-8")
    assert script.count('"$sudo"') == 2
    voice = flat(section("Voice notes (once)"))
    assert "admin rights once" not in voice and '"not ready"' not in voice
    assert "admin rights twice: her update, then reading her voice as her" in voice
    opening = prompts.SETUP_VOICE_STILL_LOADING.split(" (", 1)[0]
    assert opening == "voice: still loading after 20 minutes" and f'"{opening}"' in voice


def test_google_maps_gives_its_billing_its_free_caps_and_the_way_past_no_billing_account() -> None:
    places = flat(section("Places and routes (once)"))
    assert "a billing account and a card" in places and "1,000 free a month" in places
    assert "30 a day for each, so under 1,000 a month" in places
    assert '"No open billing account"' in places and "run the script again" in places


def test_the_tuning_and_agentjev_are_in_the_appendix() -> None:
    appendix = GUIDE.split("### Appendix: tuning and developer options", 1)[1].split("\n## 4.", 1)[0]
    assert "**When your turn ends.**" in appendix and "**What stops her when you talk over her.**" in appendix
    assert "AgentJev is a small local yes/no model" in appendix
    calls = section("Calls (once)")
    assert "**When your turn ends.**" not in calls and "Appendix, A" in calls


def test_the_guides_setup_shows_are_the_guides_words() -> None:
    """A guide in setup (the window and Terminal) says what the guide says: the same facts, in the same words."""
    assert "Outlook.com: not supported." in prompts.SETUP_GUIDES["mail"][1]
    assert prompts.SETUP_FEATURE_LINES["maps"].endswith("Search words go to Nominatim, FOSSGIS and Transitous.")
    for name in ("web", "mail", "google-maps", "calendar", "music", "documents", "projects", "lights"):
        _needs, steps = prompts.SETUP_GUIDES[name]
        assert steps and all(len(line) <= 135 for step in steps for line in step.splitlines()), name  # as drawn
    assert flat(prompts.MAIL_INVALID_CLIENT) in flat(GUIDE)


ONCE_AT_THE_END = "Doctor checks them all once, at the end."


@pytest.mark.parametrize("name", ["setup_assistant.md", "SETUP-WITH-YOUR-AI.md", "GUIDE.md"])
def test_the_doctor_checks_the_extras_once_at_the_end(name: str) -> None:
    """Owner, 27 Oct ("at the end"): setup's doctor runs once, after every extra, and the docs say so in one sentence;
    none asks for a ✓ in doctor before the next extra. A script that stops is named with its command before the next
    extra starts (prompts.SETUP_EXTRA_FAILED)."""
    text = flat((ROOT / name).read_text(encoding="utf-8"))
    assert ONCE_AT_THE_END in text, name
    for before in ("before the next", "before you add the next", "before you go on"):
        doctor_gate = re.search(rf"doctor[^.]*{before}", text)
        assert doctor_gate is None, (name, doctor_gate.group() if doctor_gate else "")
    assert "{name}: {command} stopped" in prompts.SETUP_EXTRA_FAILED


def test_the_guide_names_a_stopped_script_before_the_next_extra() -> None:
    assert "named with its command before the next extra starts" in flat(GUIDE)
