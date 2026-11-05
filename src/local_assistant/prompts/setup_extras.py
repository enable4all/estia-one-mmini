"""Setup's words for the extras of setup (setup_extras, setup_handover): calls, Extra Features and their guides, the
web keys, the home area, About you and the last step; and her first start's note on what setup handed over
(lifecycle). Re-exported by prompts/__init__.py. The guides' words are the setup guide's (setup_assistant.md, 3), and
the preview's that the owner approved (26 Oct)."""

from __future__ import annotations

__all__ = [
    "ABOUT_YOU_FIELDS",
    "MAIL_INVALID_CLIENT",
    "MAIL_STEPS",
    "SETUP_ABOUT_YOU",
    "SETUP_ABOUT_YOU_ACCOUNT",
    "SETUP_ABOUT_YOU_HIDDEN",
    "SETUP_ABOUT_YOU_INSTRUCTION",
    "SETUP_ABOUT_YOU_LINK",
    "SETUP_ABOUT_YOU_LIST",
    "SETUP_ABOUT_YOU_LONG",
    "SETUP_ABOUT_YOU_PRIVATE",
    "SETUP_ABOUT_YOU_SECRET",
    "SETUP_ABOUT_YOU_SHOWN",
    "SETUP_ABOUT_YOU_SKIP",
    "SETUP_BRAVE_COST",
    "SETUP_BRAVE_KEY",
    "SETUP_BRAVE_KEY_ASK",
    "SETUP_BRAVE_KEY_STEPS",
    "SETUP_CALENDAR_ASK",
    "SETUP_CALENDAR_BAD",
    "SETUP_CALENDAR_LEAD",
    "SETUP_CALENDAR_SAVED",
    "SETUP_CALENDAR_SAVED_HERS",
    "SETUP_CALENDAR_TITLE",
    "SETUP_CALLS",
    "SETUP_CALLS_AFTER_MOVE",
    "SETUP_CALLS_ASK",
    "SETUP_CALLS_BUILT",
    "SETUP_CALLS_CARD",
    "SETUP_CALLS_COSTS",
    "SETUP_CALLS_DONE",
    "SETUP_CALLS_HERS",
    "SETUP_CALLS_NOT_DEPLOYED",
    "SETUP_CALLS_NOT_NOW",
    "SETUP_CALLS_REFUSED",
    "SETUP_CALLS_STEPS",
    "SETUP_CALLS_STOPPED",
    "SETUP_CALLS_UNKNOWN",
    "SETUP_CALLS_VOICE_LOADING",
    "SETUP_CALLS_WAITED",
    "SETUP_CALLS_YOURS",
    "SETUP_CLOSING_COMMANDS",
    "SETUP_CLOSING_EXTRAS",
    "SETUP_EXTRA_CHECK",
    "SETUP_EXTRA_CHECKS",
    "SETUP_EXTRA_ERROR",
    "SETUP_EXTRA_ERRORS",
    "SETUP_EXTRA_FAILED",
    "SETUP_EXTRA_HEADER",
    "SETUP_EXTRA_LINKS",
    "SETUP_EXTRA_NEEDS",
    "SETUP_FACTS_CLOSING",
    "SETUP_FACTS_HEADER",
    "SETUP_FEATURES",
    "SETUP_FEATURES_ASK",
    "SETUP_FEATURES_CHOSEN",
    "SETUP_FEATURES_DRY_RUN",
    "SETUP_FEATURES_NONE",
    "SETUP_FEATURES_UNKNOWN",
    "SETUP_FEATURE_LINES",
    "SETUP_FEATURE_NAMES",
    "SETUP_GUIDES",
    "SETUP_HANDED_OVER",
    "SETUP_HANDED_OVER_DRY_RUN",
    "SETUP_HOME_AREA",
    "SETUP_HOME_AREA_EXAMPLES",
    "SETUP_HOME_AREA_HOUSE_NUMBER",
    "SETUP_HOME_AREA_NOT_FOUND",
    "SETUP_HOME_AREA_NO_ANSWER",
    "SETUP_HOME_AREA_ONE",
    "SETUP_HOME_AREA_PRIVATE",
    "SETUP_HOME_FOUND",
    "SETUP_HOME_RIGHT_ADVICE",
    "SETUP_HOME_RIGHT_ASK",
    "SETUP_ID_LATER",
    "SETUP_KEY_HELD",
    "SETUP_KEY_IN_KEYCHAIN",
    "SETUP_KEY_KEPT_OTHER",
    "SETUP_KEY_KEYCHAIN_LOCKED",
    "SETUP_KEY_NOT_HERS",
    "SETUP_KEY_NOT_IN_KEYCHAIN",
    "SETUP_KEY_NOT_SAVED",
    "SETUP_KEY_REPLACE",
    "SETUP_KEY_REPLACE_ASK",
    "SETUP_KEY_SAVED",
    "SETUP_KEY_SKIPPED",
    "SETUP_LIGHTS_DEVICES",
    "SETUP_MAIL_ADDRESS_BAD",
    "SETUP_MAIL_ALL",
    "SETUP_MAIL_FIELDS",
    "SETUP_MAIL_IDS",
    "SETUP_MAIL_ID_BAD",
    "SETUP_MAIL_SHOWN",
    "SETUP_OLLAMA_KEY",
    "SETUP_OLLAMA_KEY_ASK",
    "SETUP_OLLAMA_KEY_NOTE",
    "SETUP_OLLAMA_KEY_STEPS",
    "SETUP_OLLAMA_NO_ANSWER",
    "SETUP_OLLAMA_REFUSED",
    "SETUP_OLLAMA_TESTED",
    "SETUP_PROJECTS_ADVICE",
    "SETUP_PROJECTS_ANOTHER",
    "SETUP_PROJECTS_LEVEL_LINES",
    "SETUP_PROJECTS_NO_SUCH",
    "SETUP_PROJECTS_PICK",
    "SETUP_PROJECTS_TILDE",
    "SETUP_SECRET_NOT_SAVED",
    "SETUP_SECTION_ON",
    "SETUP_SECTION_ON_ALREADY",
    "SETUP_SPOTIFY_ID",
    "SETUP_SPOTIFY_ID_ASK",
    "SETUP_SPOTIFY_ID_BAD",
    "SETUP_SPOTIFY_ID_SHOWN",
    "SETUP_SPOTIFY_PASTE_TITLE",
    "SETUP_TAPO_EMAIL_ASK",
    "SETUP_TAPO_LEAD",
    "SETUP_TAPO_PASSWORD_ASK",
    "SETUP_TAPO_PASSWORD_LEAD",
    "SETUP_TAPO_SAVED",
    "SETUP_TAPO_SAVED_HERS",
    "SETUP_TAPO_TITLE",
    "SETUP_VOICE_FAILED",
    "SETUP_VOICE_MODELS",
    "SETUP_VOICE_NOT_READ",
    "SETUP_VOICE_READY",
    "SETUP_VOICE_RESTING",
    "SETUP_VOICE_STILL_LOADING",
    "SETUP_WEB_NO_KEY",
    "SETUP_WINDOW_BRAVE_KEY",
    "SETUP_WINDOW_EXTRAS_ASK",
    "SETUP_WINDOW_EXTRAS_LIST",
    "SETUP_WINDOW_MAIL_IDS",
    "SETUP_WINDOW_MAPS_TERMINAL",
    "SETUP_WINDOW_OLLAMA_KEY",
    "SETUP_WINDOW_SPOTIFY_ID",
    "SPOTIFY_STEPS",
    "numbered",
]

# ------------------------------------------------------------------ calls first (the preview's card, owner 26 Oct)

SETUP_CALLS_CARD = "Encrypted Signal calls: have a natural conversation."
SETUP_CALLS_COSTS = (
    "1.7 GB for her voice, several GB and a long wait for the build, admin rights twice (before and after it)."
)
SETUP_CALLS_STEPS = (
    "Nothing to sign up for: setup downloads the call tools and checks each one.",
    "Then the build runs on its own. {name}'s voice is set up with it.",
    "Admin rights twice, each asked by macOS; then the lines for her config, shown after the build.",
)
SETUP_CALLS = (
    f"{SETUP_CALLS_CARD}\n\n"
    "Comes with: {name}'s voice, spoken **on this Mac**.\n"
    "Leaves the Mac: **only the calls**, through Signal, end-to-end encrypted.\n\n"
    "Costs: " + SETUP_CALLS_COSTS.replace(", admin rights twice", ",\n**admin rights twice**")
)
SETUP_CALLS_ASK = "Set up calls? (Enter: yes)"
SETUP_CALLS_UNKNOWN = "'{answer}' is not yes or no: yes sets up calls, no leaves them for later."
SETUP_CALLS_NOT_NOW = "Calls: not now. Later: scripts/setup.sh --redo calls."
SETUP_CALLS_AFTER_MOVE = (
    "Calls: her voice service runs only in her own macOS account. Run scripts/setup.sh again once she runs there."
)
# scripts/install-voice-daemon.sh's own lines, said the same way when setup's window runs its steps (a test pins them)
SETUP_VOICE_MODELS = "waiting for the models (first start: download and Neural Engine compile, up to ~15 minutes)"
SETUP_VOICE_NOT_READ = (
    "voice: not read as her (admin rights refused); /usr/bin/sudo /usr/local/estia/bin/estia check shows it"
)
SETUP_VOICE_READY = "voice: ready"
SETUP_VOICE_FAILED = "voice: failed ({state}); see its log: /usr/bin/sudo /usr/local/estia/bin/estia logs voice"
# Pocket TTS resting after failed or late runs: tried again on its own (scripts/install-voice-daemon.sh, the same words)
SETUP_VOICE_RESTING = (
    "voice: resting after failed runs ({state}). It is tried again on its own, and calls work once her voice speaks.\n"
    "Check later: /usr/bin/sudo /usr/local/estia/bin/estia check (log: /usr/bin/sudo /usr/local/estia/bin/estia logs voice)"
)
SETUP_VOICE_STILL_LOADING = (
    "voice: still loading after 20 minutes ({state}). On a slow line the first download takes longer:\n"
    "the service keeps going, and calls work once her voice is ready. Check later: /usr/bin/sudo "
    "/usr/local/estia/bin/estia check (log: /usr/bin/sudo /usr/local/estia/bin/estia logs voice)"
)
SETUP_CALLS_STOPPED = "✗ Calls: {command} stopped. Fix what its lines say, then run scripts/setup.sh again."
# in the window, nothing failed (round 2, words-05): Not now at her update, or admin rights not given
SETUP_CALLS_NOT_DEPLOYED = "Calls: not on yet, her update was not deployed. Later: run scripts/setup.sh again."
SETUP_CALLS_REFUSED = "Calls: not on yet, admin rights were not given. Later: run scripts/setup.sh again."
# her update takes no answer for 10 minutes as a no (deploy.ASK_SECONDS): a Deploy it after that reaches nothing (W05)
SETUP_CALLS_WAITED = (
    "Calls: not on yet, her update waited over 10 minutes for an answer, so nothing was deployed. "
    "Later: run scripts/setup.sh again."
)
SETUP_CALLS_BUILT = "✓ Calls: her voice service and the call tools are built."
# a re-run on a finished install (stops-reruns-rerun-not-quiet): her lines and her restart are done
SETUP_CALLS_DONE = (
    "✓ Calls: set up already (her voice service, the call tools and her config's lines). "
    "To change them: scripts/setup.sh --redo calls."
)
# install-voice-daemon.sh's wait ended with her voice still loading (a slow line): no fault, the build goes on
SETUP_CALLS_VOICE_LOADING = "Calls: her voice is still loading; the build goes on. Calls work once her voice is ready."
SETUP_CALLS_HERS = (
    "To turn calls on, her config, hers alone: take the # off [voice] and its lines, and add this line, unless it "
    "is there already:\n\n"
    "    [calls]\n\n"
    "Setup opens it as her on Enter ({save}); she restarts once, at the end.\n"
    "Then her doctor shows Calls ✓; call her from the Signal app."
)
SETUP_CALLS_YOURS = (
    "To turn calls on: in {path}, take the # off [voice] and its lines, and add a [calls] line; then restart her.\n"
    "  scripts/doctor.sh then shows Calls ✓; call her from the Signal app."
)

# ------------------------------------------------------------------ Extra Features (the preview's order and words)

# (id, the name in the list, the line under it); the ids are setup_extras.EXTRAS' keys
SETUP_FEATURE_NAMES = {
    "web": "Web search",
    "maps": "Places and routes",
    "mail": "Mail",
    "google-maps": "Google Maps",
    "calendar": "Calendars",
    "music": "Spotify",
    "documents": "Documents",
    "chords": "Chords",
    "projects": "Projects",
    "lights": "Lights and plugs",
}
SETUP_FEATURE_LINES = {
    "web": "Current answers, with their sources. Search words go to Ollama.",
    "maps": "From OpenStreetMap, nothing to set up. Search words go to Nominatim, FOSSGIS and Transitous.",
    "mail": "Microsoft 365, read-only and sorted. Gmail: work in progress.",
    "google-maps": "Open now, ratings, and tram and bus lines. Used first once it is set up.",
    "calendar": "In the morning digest and on request.",
    "music": "Music, by text message.",
    "documents": "Capability to read PDFs and text files.",
    "chords": "Transposed, for a capo, worked out on the Mac.",
    "projects": "Git projects in one folder, read-only.",
    "lights": "Kasa, Tapo and Philips Hue.",
}
# each extra's guide, shown only when its switch is on (the preview's words, owner 26 Oct): (before you start, steps)
# Spotify's and Microsoft's steps, the same in the guide (the plan's switches) and on their own question's screen; the
# sites' words as their guides show them, checked live 27 Oct 2026 (Spotify's "Getting started" and "Apps", and
# Microsoft's "Register an app", dated 14 May 2026; the dashboards themselves need a sign-in, so not seen; Spotify's
# User Management and Microsoft's Authentication and API permissions come from setup_assistant.md, not checked live)
SPOTIFY_STEPS = (  # a line under a step goes on with it
    "At https://developer.spotify.com/dashboard, log in and click Create an app:\n     an App Name and an App Description.",
    "Redirect URI: http://127.0.0.1:8888/callback;\n     tick the Developer Terms of Service, then Create.",
    "Under User Management, add your own name and email.",
    "Open the app's Settings and copy its Client ID.",
)
MAIL_STEPS = (
    "At https://entra.microsoft.com: Entra ID, App registrations, New registration.\n"
    "     A name, Single tenant only, then Register.",
    "Authentication, Advanced settings: turn on Allow public client flows.\n"
    "     API permissions: Mail.Read, User.Read and offline_access.",
    "On Overview, copy Directory (tenant) ID and Application (client) ID.",
)
SETUP_GUIDES = {
    "web": (
        "Needs a key from a free ollama.com account; Brave's key is optional.",
        (
            "Open ollama.com/settings/keys and sign in (a free ollama.com account is enough).",
            "Make a new key there and copy it.",
            "Paste it at setup's question: it is typed hidden and never kept with your answers.",
            "Optional backup: Brave's key, from api-dashboard.search.brave.com (a card needed).",
        ),
    ),
    "mail": (
        "Needs a Microsoft 365 mailbox and an app registered at Microsoft.",
        (
            *MAIL_STEPS,
            "Paste both at setup's question, with the mailbox's address; setup then signs it in once.",
            "Outlook.com: not supported.",
        ),
    ),
    "google-maps": (  # its Terminal path in 0.9.8 (owner's answer 6, 29 Oct): the window names its commands for after
        "Needs a Google Cloud project with billing (a card). Set up in Terminal (from the setup window: after setup).",
        (
            "Google's command-line tool (gcloud) first, then its sign-in: open the link it shows, sign in, paste the "
            "code back.",
            "No billing account yet? At https://console.cloud.google.com/billing:\n     Create account, with a card.",
            "Setup's script then makes the project and a key for places and routes only.",
        ),
    ),
    "calendar": (
        "Needs a Google calendar's secret address (the others: the setup guide, Calendar).",
        (
            "At https://calendar.google.com on a computer: the gear, then Settings;\n"
            "     on the left, the calendar's name under Settings for my calendars.",
            "Integrate calendar: copy Secret address in iCal format.",
            "Setup asks for it on your yes: paste it there, hidden.",
            "iCloud, Fastmail, Nextcloud or Outlook: the setup guide, Calendar.",
        ),
    ),
    "music": (
        "Needs Spotify Premium.",
        (*SPOTIFY_STEPS, "Paste it at setup's question; setup then signs you in to Spotify once."),
    ),
    "documents": (
        "",
        (
            "Setup makes a folder, Estia-KB, in your home folder, next to Documents.",
            "Files put there, and the ones sent to her in Signal, are read on this Mac.",
        ),
    ),
    "projects": (
        "",
        ("Pick the folder that holds your git projects, and how much she may read: summary, docs or code.",),
    ),
    "lights": (
        "",
        (
            "Kasa and Tapo: setup lists them on your home network, on your yes.",
            "Newer ones also need your TP-Link login (the Tapo app's): typed hidden, on your yes.",
            "Philips Hue: setup pairs the Bridge on your yes; press its button when it asks.",
        ),
    ),
}
# the guides' links, opened in the Mac's browser (never inside the setup window)
SETUP_EXTRA_LINKS = {
    "music": (("developer.spotify.com/dashboard", "https://developer.spotify.com/dashboard"),),
    "web": (
        ("ollama.com", "https://ollama.com/signup"),
        ("ollama.com/settings/keys", "https://ollama.com/settings/keys"),
        ("api-dashboard.search.brave.com", "https://api-dashboard.search.brave.com/"),
    ),
    "mail": (("entra.microsoft.com", "https://entra.microsoft.com/"),),
    "google-maps": (("console.cloud.google.com/billing", "https://console.cloud.google.com/billing"),),
    "calendar": (
        ("Google's help", "https://support.google.com/calendar/answer/37648"),
        ("account.apple.com", "https://account.apple.com/"),
        ("Apple's help", "https://support.apple.com/102654"),
        ("Fastmail's help", "https://www.fastmail.help/hc/en-us/articles/360058752854"),
    ),
}
# how you know it worked: doctor's line, then a question to her (the setup guide's words)
SETUP_EXTRA_CHECKS = {
    "web": "doctor shows Web search ○ or ✓; then ask her what is in the news today.",
    "maps": "doctor shows OpenStreetMap ○ or ✓; then ask her the way from home to the nearest bakery.",
    "mail": "doctor shows each mailbox ○ or ✓; then ask her what came in today.",
    "google-maps": "doctor shows Google Maps ○ or ✓; then ask her whether a café near home is open now.",
    "calendar": "doctor shows each calendar ○ or ✓; then ask her what is on tomorrow.",
    "music": 'doctor shows Spotify ○ or ✓; then text her "play some jazz".',
    "documents": "doctor shows Documents ○ or ✓; then send her a PDF and ask what it says.",
    "chords": "ask her for G C D with a capo on 2.",
    "projects": "doctor shows Projects ○ or ✓; then ask her what one of your projects is about.",
    "lights": "doctor shows Lights and plugs ○ or ✓; then ask her to switch a light on.",
}
# mail_auth's device sign-in when Microsoft answers invalid_client (the docs review, 26 Oct: the switch it needs)
MAIL_INVALID_CLIENT = (
    "Microsoft refused the sign-in (invalid_client): turn on Allow public client flows, under Authentication, "
    "Advanced settings, in the app's registration. Then sign in again."
)
# the common errors: (what it says, the fix)
SETUP_EXTRA_ERRORS = {
    "web": (("Ollama refused this key", "copy it again from ollama.com/settings/keys, then scripts/set-web-keys.sh"),),
    "mail": (
        (
            "invalid_client",
            "turn on Allow public client flows (Authentication, Advanced settings) in the app's registration",
        ),
    ),
    "google-maps": (("No open billing account", "link a billing account to the project in console.cloud.google.com"),),
}
SETUP_FEATURES = "Each one's steps come after the **security step**, one at a time."
SETUP_FEATURES_ASK = "Which ones? (names or numbers; Enter: web search and places and routes; none: no extras)"
SETUP_FEATURES_UNKNOWN = "Not on the list: {words}. Names or numbers from the list; none: no extras."
SETUP_FEATURES_DRY_RUN = "  (asks which ones; Enter: web search and places and routes)"
SETUP_FEATURES_CHOSEN = "✓ Extra Features: {names}."
SETUP_FEATURES_NONE = "✓ Extra Features: none."
SETUP_EXTRA_HEADER = "\n{name}: {line}"
SETUP_EXTRA_NEEDS = "  {needs}"
SETUP_EXTRA_CHECK = "  Works when: {check}"
SETUP_EXTRA_ERROR = "  If it says **{says}**:\n    {fix}."  # what it says in bold, as the window's guide shows it
SETUP_EXTRA_FAILED = "✗ {name}: {command} stopped. Fix what its lines say, then run it again."  # its script failed
SETUP_SECTION_ON = "✓ {name}: on ([{section}] in the config)."
SETUP_SECTION_ON_ALREADY = "✓ {name}: on already ([{section}] in the config)."  # as chords' and maps' lines say it
# projects, picked among the Extra Features (spec: the folders that are there, then another folder; Summary first)
SETUP_PROJECTS_PICK = "Which folder holds your git projects? (Enter: {folder})"
SETUP_PROJECTS_ADVICE = "a folder in your home, which the move shares with her"
SETUP_PROJECTS_ANOTHER = "Another folder"
SETUP_PROJECTS_NO_SUCH = "No folder {folder}: pick one that is there."
# "~dev": ~ and a name is that macOS account's home, and this Mac has no such account
SETUP_PROJECTS_TILDE = (
    "No folder {folder}: ~ and a name is that macOS account's home. Start it with ~/ (your home) or /."
)
SETUP_PROJECTS_LEVEL_LINES = {  # the one thing each level adds, in bold (owner, 28 Oct), as Terminal's list has it
    "summary": "The **README**, open to-do items and recent commits.",
    "docs": "Also the project's **documents**.",
    "code": "Also its **source code**.",
}

# ------------------------------------------------------------------ the web keys, pasted once

# each key's steps, as a person would guide you (owner, 27 Oct), checked on the live sites 27 Oct 2026: Ollama's docs
# name the keys page and a free account (the page itself needs a sign-in, so its button was not checked); Brave's
# quickstart names Plans, the Search plan, a card, API Keys and Add API Key
SETUP_OLLAMA_KEY_STEPS = (
    "Ollama's key, for web search:\n\n"
    "  1. Open https://ollama.com/settings/keys and sign in\n"
    "     (a **free** ollama.com account is enough).\n"
    "  2. Make a new key there and copy it."
)
SETUP_OLLAMA_KEY_NOTE = (
    "It is typed hidden, saved for her and in your login keychain, and **never kept** with your answers.\n"
    "**One test search** goes to ollama.com."
)
SETUP_OLLAMA_KEY = (
    f"{SETUP_OLLAMA_KEY_STEPS}\n  3. Paste it below and press Enter. Enter alone: later.\n\n{SETUP_OLLAMA_KEY_NOTE}"
)
SETUP_WINDOW_OLLAMA_KEY = (
    f"{SETUP_OLLAMA_KEY_STEPS}\n  3. Paste it below, then Continue. Later skips it.\n\n{SETUP_OLLAMA_KEY_NOTE}"
)
SETUP_OLLAMA_KEY_ASK = "Ollama's key (hidden; Enter: later): "
SETUP_OLLAMA_REFUSED = "Ollama refused this key. Copy it again from ollama.com/settings/keys."
SETUP_OLLAMA_TESTED = "✓ Web search: Ollama answered a test search."
SETUP_OLLAMA_NO_ANSWER = (
    "! Web search: ollama.com did not answer the test search ({why}); the key is kept, doctor checks it later."
)
SETUP_BRAVE_KEY_STEPS = (
    "Brave's key, the backup (**optional**):\n\n"
    "  1. Make an account at https://api-dashboard.search.brave.com/register\n"
    "  2. On Plans, pick the Search plan and add a card.\n"
    "  3. Under API Keys, click Add API Key, name it Estia and copy it."
)
SETUP_BRAVE_COST = (  # the limit inside the claim (review SEC-4), in the setup guide's words
    "Brave Search: free with a **$5 monthly credit** (about 1,000 searches), needs a card.\n\n"
    "Estia **stops at 900** of its own searches each calendar month, below the credit;\n"
    "a key also used by other programs can still go over it, so a key used only by Estia is recommended."
)
SETUP_BRAVE_KEY = (
    f"{SETUP_BRAVE_KEY_STEPS}\n  4. Paste it below and press Enter. Enter alone: later.\n\n{SETUP_BRAVE_COST}"
)
SETUP_WINDOW_BRAVE_KEY = (
    f"{SETUP_BRAVE_KEY_STEPS}\n  4. Paste it below, then Continue. Later skips it.\n\n{SETUP_BRAVE_COST}"
)
SETUP_BRAVE_KEY_ASK = "Brave's key (hidden; Enter: later): "
SETUP_KEY_SKIPPED = "  {label}: none yet. Later: scripts/setup.sh --extras asks for it again."
SETUP_WEB_NO_KEY = "! Web search: no key yet, so she cannot search the web. Later: scripts/setup.sh --extras."
SETUP_KEY_HELD = "✓ {label}: saved for her already, so not asked again."
SETUP_KEY_SAVED = "✓ {label}: saved for her ({characters} characters, readable only by her)."
SETUP_KEY_NOT_SAVED = "✗ {label}: not saved ({why}). Later: scripts/set-web-keys.sh."
SETUP_KEY_IN_KEYCHAIN = "✓ {label}: also in your login keychain on this Mac."
SETUP_KEY_NOT_HERS = "her config is not in {folder}, where she reads web.env"
SETUP_KEY_NOT_IN_KEYCHAIN = "! {label}: not in your login keychain; she has it, so web search works."
# a key already in your login keychain under the same shared service (another program's, maybe): replaced only on a yes
SETUP_KEY_REPLACE = (
    'Your login keychain already has a key under "{service}", maybe **another program\'s**.\n\n'
    "Yes replaces it with this one: only when it is the **same account's key**.\n"
    "No keeps it there, and this key is saved **for her only**."
)
SETUP_KEY_REPLACE_ASK = 'Replace the key under "{service}"?'
SETUP_KEY_KEPT_OTHER = (
    '  {label}: saved for her only; the key under "{service}" in your login keychain stays as it was.'
)
SETUP_KEY_KEYCHAIN_LOCKED = (
    "Your login keychain is locked: macOS asks for your Mac password in Terminal, hidden, to open it."
)

# ------------------------------------------------------------------ the home area (#43; owner OK 26 Oct)

SETUP_HOME_AREA = (
    "The street and the city, so a route from home starts there.\n"
    "Street and city are enough, **no house number**.\n\n"
    "**One lookup** at OpenStreetMap, with the street and the city only. Enter on both: none."
)
SETUP_HOME_AREA_EXAMPLES = ("Example Street", "Example Town")  # grey, never a real value
SETUP_HOME_AREA_HOUSE_NUMBER = "Street and city are enough, no house number."
SETUP_HOME_AREA_ONE = "Both, please: the street and the city."
SETUP_HOME_AREA_PRIVATE = "Only the street and the city here: no email address or long number."
SETUP_HOME_FOUND = (
    "Found: {found}.\n\n**No** asks for the street and the city again."  # the place OpenStreetMap matched
)
SETUP_HOME_RIGHT_ASK = "Right?"
SETUP_HOME_RIGHT_ADVICE = "when it is your area"
SETUP_HOME_AREA_NOT_FOUND = "No place found for '{area}'. Check the spelling or add the country."
SETUP_HOME_AREA_NO_ANSWER = "! OpenStreetMap did not answer: {area} is kept as typed."

# ------------------------------------------------------------------ About you (wording approved by the owner, 26 Oct)

SETUP_ABOUT_YOU = (
    "**Optional**. A few facts she keeps in mind from her first message. Kept **on this Mac**.\n\n"
    "/profile shows them; each can be **changed or removed** there."
)
SETUP_ABOUT_YOU_SKIP = "Skip: she learns as you talk."
SETUP_ABOUT_YOU_LIST = "  Each line is optional: Enter skips it. Enter on all five: she learns as you talk."
# (field id, label, grey example): never a real value
ABOUT_YOU_FIELDS = (
    ("name", "What she calls you", "Alex. Empty: she uses no name."),
    ("work", "Work or study", "Nurse, night shifts."),
    ("people", "People close to you", "Maria (wife), Nikos (son, 9). First names are enough."),
    ("interests", "Interests", "Jazz, cycling, philosophy."),
    ("day", "Your usual day", "Up at 7, work 9 to 5, gym on Tuesdays."),
)
SETUP_ABOUT_YOU_LINK = "No links here."
SETUP_ABOUT_YOU_LONG = "{label}: at most {limit} characters; this has {count}."  # {limit}: the line's room
SETUP_ABOUT_YOU_HIDDEN = "{label}: a hidden or control character is in it; type it again."
SETUP_ABOUT_YOU_INSTRUCTION = "{label}: a fact about you, not an instruction to her."
SETUP_ABOUT_YOU_SECRET = "{label}: no password or key here."
SETUP_ABOUT_YOU_PRIVATE = "{label}: no phone number or email address here."
SETUP_ABOUT_YOU_ACCOUNT = "{label}: no account number here."
SETUP_ABOUT_YOU_SHOWN = "She will keep in mind:"
SETUP_HANDED_OVER = "✓ About you and your home area: in {path}, for her first start (only she reads it after the move)."
SETUP_HANDED_OVER_DRY_RUN = (
    "(dry run) would write {path} (mode 600): About you and your home area, for her first start."
)

# her first start (lifecycle.setup_facts): code's note, in her history as a code_notes result, in the quiet-time note's
# layout (prompts.fact_note: the heading in bold, one line a fact, then the closing)
SETUP_FACTS_HEADER = "From setup, I keep in mind"
SETUP_FACTS_CLOSING = "/profile shows them; each can be changed or removed there."

# ------------------------------------------------------------------ the last step

# before her voice service is installed (once it is, setup's SETUP_VOICES names her voices and these commands too)
SETUP_CLOSING_COMMANDS = "What she can do: /tools in Signal. Every command: /help."
SETUP_CLOSING_EXTRAS = "Extra Features picked: {names}."

# ------------------------------------------------------------------ the IDs the plan asks for picked extras (owner,
# 27 Oct: each key or ID on its own screen, its steps as a person would guide you)


def numbered(steps: tuple[str, ...], start: int = 1) -> str:
    """Steps as Terminal and the window number them: two spaces in, a step a line."""
    return "\n".join(f"  {at}. {step}" for at, step in enumerate(steps, start))


SETUP_SPOTIFY_ID = (
    "Spotify's Client ID, for music by text message (**Spotify Premium**):\n\n"
    f"{numbered(SPOTIFY_STEPS)}\n"
    "  5. Paste it below and press Enter. Enter alone: later.\n\n"
    "Setup then signs you in to Spotify once, on your yes.\nA Client ID is **no secret**: no password goes with it."
)
SETUP_WINDOW_SPOTIFY_ID = SETUP_SPOTIFY_ID.replace(
    "  5. Paste it below and press Enter. Enter alone: later.", "  5. Paste it below, then Continue. Later skips it."
)
SETUP_SPOTIFY_ID_ASK = "Spotify's Client ID (Enter: later):"
SETUP_SPOTIFY_ID_BAD = "A Client ID has 32 letters and digits (0 to 9, a to f), from the app's Settings."
SETUP_SPOTIFY_ID_SHOWN = "✓ Spotify: Client ID kept; [spotify] goes into her config with it."
SETUP_MAIL_IDS = (
    "Mail needs an app **registered at Microsoft**, once:\n\n"
    f"{numbered(MAIL_STEPS)}\n"
    "  4. Paste both below, with the mailbox's address. All empty: later.\n\n"
    "Setup then signs the mailbox in once, on your yes. Outlook.com: **not supported**."
)
SETUP_WINDOW_MAIL_IDS = SETUP_MAIL_IDS.replace(
    "  4. Paste both below, with the mailbox's address. All empty: later.",
    "  4. Paste both below, with the mailbox's address, then Continue. Later skips it.",
)
# its three fields: (id, label, grey example), never a real value
SETUP_MAIL_FIELDS = (
    ("tenant", "Directory (tenant) ID", "00000000-0000-0000-0000-000000000000"),
    ("client", "Application (client) ID", "00000000-0000-0000-0000-000000000000"),
    ("address", "The mailbox's address", "alex@example.com"),
)
SETUP_MAIL_ID_BAD = "{label}: a code like 00000000-0000-0000-0000-000000000000, from the app's Overview."
SETUP_MAIL_ADDRESS_BAD = "The mailbox's address: an email address, like alex@example.com."
SETUP_MAIL_ALL = "All three, please, or all left empty for later."
SETUP_MAIL_SHOWN = "✓ Mail: {address}; [mail] goes into her config with it."
# the extras' steps that run on your yes
SETUP_LIGHTS_DEVICES = (
    "  Then [tapo] and one [[tapo.devices]] per device: its name as the app shows it, its address,\n"
    "  its kind and its rule (setup_assistant.md, 3, Lights and plugs)."
)
# set-tapo-credentials.sh and set-calendar-url.sh in the setup window: their questions on screens of their own, then
# the scripts' own lines (a test keeps them the same as the scripts')
SETUP_TAPO_TITLE = "TP-Link login"
SETUP_TAPO_LEAD = (
    "The Tapo app's login, for the newer Kasa and Tapo devices.\n"
    "Saved **for her only**; it goes only to the devices in her config."
)
SETUP_TAPO_EMAIL_ASK = "TP-Link account email"
SETUP_TAPO_PASSWORD_LEAD = "Typed hidden: it **never shows**, and never goes into a log."
SETUP_TAPO_PASSWORD_ASK = "TP-Link account password (hidden)"
SETUP_TAPO_SAVED_HERS = "saved in her tapo.env (mode 600, readable only by _estia)"
SETUP_TAPO_SAVED = "saved to {file} (mode 600)"
SETUP_CALENDAR_TITLE = "Google calendar's address"
SETUP_CALENDAR_LEAD = (
    "Its Secret (or Public) address in iCal format, from Integrate calendar in its settings.\nSaved **for her only**."
)
SETUP_CALENDAR_ASK = "The iCal address (nothing shows while you paste)"
SETUP_CALENDAR_BAD = (
    "That is not a Google iCal address (https://calendar.google.com/calendar/ical/…/basic.ics); nothing saved."
)
SETUP_CALENDAR_SAVED_HERS = (
    "saved CALENDAR_ICS_URL in her calendar.env (mode 600, readable only by _estia); the bridge uses it from the next "
    "calendar read on"
)
SETUP_CALENDAR_SAVED = "saved CALENDAR_ICS_URL in {file} (mode 600); the bridge uses it from the next calendar read on"
SETUP_SECRET_NOT_SAVED = "✗ {label}: not saved ({why})."
# Google Maps in the setup window (owner, 29 Oct: its Terminal path in 0.9.8): its commands, for Terminal after setup
SETUP_WINDOW_MAPS_TERMINAL = (
    "Google Maps is set up in Terminal, after setup: its sign-in asks there.\n"
    "Until then, places and routes come from OpenStreetMap. In Terminal:"
)
# a re-run's Extra Features in the setup window (Terminal: the numbered list, then "Which ones?"): switches, none on
SETUP_WINDOW_EXTRAS_LIST = "All **optional**: each one picked gets its steps next, one at a time."
SETUP_WINDOW_EXTRAS_ASK = "Which ones? Only the ones needed now (recommended)."
# spotify-login.sh in the setup window: the sign-in's own lines (its link) and its question, on a screen of its own
SETUP_SPOTIFY_PASTE_TITLE = "Spotify's sign-in"
SETUP_ID_LATER = "  {name}: later, by hand:"  # its steps follow
