# Security policy

## Reporting a vulnerability

Please do not open a public issue for a security problem.

Report it privately with GitHub's private vulnerability reporting: on the repository's **Security** tab, choose
**Report a vulnerability**. Direct link:
<https://github.com/enable4all/estia-one-mmini/security/advisories/new>.

Useful details: the commit you tested, the steps to reproduce, and what an attacker could do. Please leave real
phone numbers, account IDs, mail, calendar entries and keys out of the report; a made-up example is enough.

The maintainer will acknowledge a report as soon as practical.

## Supported versions

Only the `main` branch is supported. Fixes land there.

## Scope

In scope, for this project's code:

- the bridge (`src/local_assistant/`), including its configuration handling and slash commands;
- the voice service (`tools/voice/`);
- the device, calendar, YouTube, documents and call helpers (`tools/tapo/`, `tools/ical/`, `tools/youtube/`,
  `tools/documents/`, and the build and patch in `tools/call-tunnel/`);
- the scripts and the launchd templates in `scripts/` and `launchd/`.

Out of scope, report them upstream:

- signal-cli, RingRTC, the upstream signal-call-tunnel and youtube-transcript-api (see `THIRD_PARTY_NOTICES.md`);
- Ollama and the Gemma model;
- macOS and Apple's frameworks, PDFKit and Vision among them;
- the Signal, Microsoft, Google, YouTube, Spotify, Brave and TP-Link services and apps;
- OpenStreetMap's services: Nominatim, FOSSGIS's routing servers and Transitous.

## What the project tries to protect

The full threat model is in [setup_assistant.md, section 8](setup_assistant.md#8-security). In short:

- Only the configured Signal accounts get answers. Other senders are ignored, never answered or rejected.
- She runs as her own hidden macOS account `_estia` (no password, no login, no remote login), from code in
  `/usr/local/estia` owned by root, the Mac's all-powerful admin account: her code and every program she runs (her
  signal-cli, Java for calls, the call helper, Pocket TTS for her voices) are root's copies, and the folders her
  programs are looked up in are root's only. Her data in `/var/estia` (0700) is out of reach of your user's programs and AI sessions as long as no
  admin rule skips the password; what she sends to Ollama is not, unless Ollama is her own (a new install with her
  own account, since 0.9.8: see the known gaps).
- Her voice service answers only programs running as her own macOS account. A program of yours or of any other
  account, root's too, is refused before anything it sends is read, so a program of yours cannot have her voice
  service turn audio into text, speak text in her voices or download her voice models, as long as no admin rule skips
  the password. macOS shows a program only the connections of its own account's programs, so a connection that no
  program of hers holds is refused, also one whose program cannot be identified, and one its program dropped, even
  when a program of hers took its address since. Her wrapper's `doctor` and `check`, an update, the move and setup
  read her voice's state as her account.
- Projects are an extra, off by default: without `[projects]` her own macOS account reads no folder of yours. With it,
  her account reads one folder of yours, the projects folder named in setup, read-only (`scripts/share-projects.sh`, as
  you, no admin rights); in your home she only passes through to that folder (and to `~/Estia-KB` with Documents on,
  below), by name, and her code never lists or reads anything else (what passing through by name leaves open: known
  gaps).
- Documents are an extra too, off without `[documents]`. With it, her own macOS account lists and reads every file in
  your `~/Estia-KB` and saves the PDFs and text files you send her there (`scripts/share-documents.sh`, as you, no admin
  rights; `--revoke` takes it back, her index of the documents goes at her next look, and a new share starts a fresh
  one). The folder is directly in your home: macOS guards Documents, Desktop and Downloads; a folder in your home needs
  no extra permission. Outside her own folders, her account adds files only to `~/Estia-KB`, and never deletes or
  changes a file it did not add. It adds files to the folder itself, never a folder and never anything below it. The
  share refuses a folder that holds a git checkout (`.git`): her account would add files to code you run. An entry for
  you, inherited by every new file, gives you full control of what she adds. A file there with a second name (a hard
  link, which macOS lets any account make to a file it cannot read) is never shared, and the share goes into no folder
  that is not yours: an access entry belongs to the file, not to the name, so sharing such a link would open your file
  elsewhere. A PDF is read by Apple's PDFKit and Vision in a helper program that runs as her account: a PDF made to
  attack the reader reaches only that program and her account, never yours. The helper runs with fixed arguments, a
  clean environment and bounded output, one PDF at a time, stopped at 75 s (it stops itself at 60 s); a file over 25 MB
  never reaches it, and no link is followed. A text file (`.txt` or `.md`) is read by her own code on the Mac, never by
  the helper program. 1 MB a text file, in pages of about 3,000 characters (about one printed page); at most 667 pages,
  the most 1 MB gives without page breaks. Control characters become spaces; invisible characters (zero-width, direction
  marks) and chat codes are removed before she reads the text; a file with a zero byte, as programs and archives have,
  is not read. Gemma is not used in the look at the folder, the reading, the index or the note on new documents. A
  document's text is outside text, a text file's as a PDF's: an answer that read one saves no fact, sets no reminder,
  runs no web search or map request, and its device changes wait for a yes; the tool log keeps a stub. A file
  from a stranger is never saved, and `/docs send N` goes only to the account that asks. Logs hold counts and codes,
  never a document's name or text.
- A change to your copy of the code goes live only through an update, `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`,
  which shows the list of changes and the files they touch and asks first. It also names each program she runs from
  outside git (signal-cli, the call helper, Java) that differs from the live release's, with the folder it comes from,
  and stops when one changed after that question, or when a folder of signal-cli's Java build has any name but
  `signal-cli-<version>-jvm/signal-cli-<version>`. Each name and folder it shows stays on one line: a character that
  could start a line, move the cursor or hide text is shown as "?". Her voice service is built from the update's own code, never from a
  build in your copy. Root runs only `/usr/local/estia/tools`
  (their own Python, the update code, the service templates), which change only with `--update-tools`, after showing
  what changed in them; the rollback runs from there too. Their Python is kept while the live release's is the same
  version; a new version is taken, named by version first, and never one that holds a file Python runs at every
  start (a `.pth` file, `sitecustomize` or `usercustomize`). New tools that do not start are never put in place.
  Doctor names tools that are not the live release's, and such a file in either Python.
- Only messages from the devices you allowed are answered: your phone (Signal's device 1) from the start; a new linked
  device (Signal Desktop, a tablet) is held, not answered and not read, until your phone sends `/devices ok N`. A note
  says so and names the time. The time is when Signal received the message, by Signal's own clock, never the device's.
  A device linked to your account gets a copy of every message you send, so it can send one in the same minute: a
  matching time alone does not show the device is yours. A held device's texts cannot command her. A call names no
  device, so a call from it is answered like one from your phone, and a text after it gives its start and end time on
  all your devices. A held device still receives what she sends to your account, as Signal delivers each message to
  every device of an account: remove a device you do not know in Signal, Linked devices. Signal gives a new linked
  device the number of a removed one: an allowed device silent for 14 days is held again, and `/devices forget N`
  removes one you unlinked at once.
  `/devices ok N` and `/devices forget N` work only from your phone: a linked device, even an allowed one, never lets
  another in.
- `/lock` from a device she answers stops every answer and call at once, until `/unlock` from the same device or your
  phone, 10 minutes later at the earliest; no other device ends it. Language practice stops too: a practice recap that
  falls due meanwhile is written and sent only after `/unlock`. The lock survives a restart and needs no admin rights;
  with neither device at hand, `/usr/bin/sudo /usr/local/estia/bin/estia unlock` on the Mac ends it.
- A changed safety number is never trusted by itself (`--trust-new-identities on-first-use`, pinned in code): messages
  from that number are dropped, and doctor and `/status` show it with the steps to compare, then trust. Doctor also
  counts the devices on her own Signal account (only the Mac's).
- The model always runs on the Mac. A cloud model, which Ollama runs on its own servers, is refused: by name (a tag
  ending in `-cloud` or `:cloud`) when the config is read, and by Ollama's own entry for her model (`remote_host` or
  `remote_model` set in `/api/tags`) at the start, every 5 minutes and before every chat. Once the check sees one, no
  message goes to it: answers pause, a note says why, and doctor shows ✗.
- With `model_digest` set, a model put in place of hers under the same name gets no message once the pin is checked: at
  the start, every 5 minutes and before every chat. Answers pause, a note says so, and doctor shows the digest. The
  pin cannot stop your own account when it runs Ollama (the Ollama app, an install from before 0.9.8 or without her
  own account), nor any program on the Mac that reaches her own Ollama's address, which has no login (known gaps).
- The Mac's health alerts and their "back to normal" wait until quiet hours end: those of the messages she starts,
  22:00 to 08:00 by default, and still 22:00 to 08:00 with `quiet_hours = ""`. Security notes (a new linked device on
  her account, the model paused or a cloud model) are never held.
- Whisper (speech to text), the speech detector and the turn detector (Smart Turn) on calls load only files of one fixed
  version (one commit on Hugging Face), each checked against its fingerprint (sha256) at every start: a model with a
  changed or extra file is never loaded. Whisper's folder is then set aside (`<folder>.refused-<time>`, only the latest
  kept) and downloaded again at that version; changed again, listening stays off. The turn detector's folder is set
  aside and downloaded again the same way; changed again, every turn on a call waits the full pause. Once downloaded,
  Whisper starts with no network. Smart Turn's files are a community conversion of Daily's model
  (`aufklarer/Smart-Turn-v3.2-CoreML`), not Daily's own: compared once with Daily's file (the same verdict, finished or
  not, on 7,192 of 7,200 test windows), and the model holds only CoreML's built-in operations, no code of its own. If
  that version is removed from Hugging Face, a new install cannot download it: the turn detector stays unavailable and
  every turn waits the full pause.
- Her English voices heart, jane, eve, vera, alba and michael (Pocket TTS, by Kyutai) are fixed to one version too. Its
  program comes from Kyutai's GitHub release v4.0.1 and goes into her release only with an update: the update has the
  new voice service download it as you and check the download's fingerprint (sha256), then the program's and its
  licence's, before root makes the release root's. The voice service only checks it, and never downloads or changes it:
  a program that is not the pinned one is never run. Its model and the voices come from Hugging Face (Kyutai's
  repository without voice cloning, which needs no account), one fixed version each, downloaded at the voice service's
  first start into her home and checked against their fingerprints before the first sentence and whenever a file
  changes: a changed file is never run, and is set aside and downloaded again once. heart's voice files, one for each
  model, are in this repository (made once with Kyutai's voice-cloning model, which Estia never downloads or runs) and
  come in her release with an update, beside the voice service and root's, never downloaded: the voice service copies
  its model's file beside the other voices, readable only by her, and checks it against its fingerprint the same way.
  One model speaks every English voice, chosen by the Mac's memory as setup chooses Gemma: the larger one beside Gemma 4
  26B (32 GB of memory or more), the small one beside Gemma 4 12B (without vera); each has its own fixed files. jane's
  models for Spanish, Italian, Portuguese, Dutch, German and French (Pocket TTS's own model of each, with her voice) are
  fixed and checked the same way. A language's model is downloaded only when she speaks that language (set with
  `/language` or practised with `/babble`), or when another program running as her own macOS account asks her voice
  service for it. Each sentence is one run of the program under macOS's sandbox (`sandbox-exec`): no network, no other
  program started, none of her other programs stopped or signalled (her bridge, her voice service), no request to
  macOS's own services (tested with its notification and preferences services), nothing written, her home closed except
  Pocket's own folder, and a time limit that grows with the text. Pocket TTS is her only voice: a line it cannot say is
  said by no other voice, so a voice note's answer comes as text alone, and on a call the rest of that answer comes as
  a Signal text. Korean, Polish, Russian and Japanese have no voice: her answers in them come as text, and calls in
  them are not picked up.
- Ollama is only its official build: setup downloads one fixed version (0.40.4) from GitHub and checks its fingerprint
  (sha256), every program's signature (Apple's Developer ID certificate for Ollama's team, 3MU9H2V9Y9, not only the
  team's name, which anyone can put in a signature) and Apple's notarization before anything is installed; one failed
  check and nothing is installed. An Ollama that already answers is kept only when its program and every library next
  to it pass the same signature check and it is reachable only from the Mac itself. Homebrew's Ollama is named with its
  fix, never used. The Ollama app is used only when chosen at setup (setup asks when it is on the Mac), through its own
  program, signed by Ollama. With the app, she answers only while someone is logged in and the Ollama app runs. The
  app updates itself: an update can pause her answers until the model check passes again. It runs as you, reachable
  only from the Mac itself; its log is yours, readable only by you. A new install that says yes to her own macOS
  account (since 0.9.8) gets her own Ollama: the same checked build, copied by root into `/usr/local/estia/ollama` (a
  copy only admin rights can change, hashed again by root and checked file by file: root's, writable by no other
  account), run as `_estia` from the Mac's start on 127.0.0.1:8151, her model in `/var/estia/.ollama/models` (hers
  alone), its log hers. Her prompts then go only between her bridge and her Ollama, both her account's. Any other
  Ollama on the Mac stays as it is, beside hers, and is never used for her. An install from before 0.9.8 keeps its
  Ollama as you: an update never moves it.
- Setup's secrets: the Signal captcha link, the SMS code and a Registration Lock PIN, or the Account ID and the Recovery
  key of an account without a number, are typed hidden; with setup's menus the Account ID and the Recovery key are typed
  on its key card instead, visibly, on a screen of its own that is cleared before it is left, so neither stays in the
  terminal's scrollback. The captcha link, the SMS code, the PIN and the Recovery key go to signal-cli on its input
  (JSON-RPC), never as command-line arguments, which every user of the Mac can read; the commands on screen show
  placeholders. The Account ID shows there: setup, `/usr/bin/sudo /usr/local/estia/bin/estia signal-login` and the
  bridge name her account with it on signal-cli's command line, as they name a number (see the known gaps). The Recovery
  key is read across lines until its 64 characters have come, so nothing of a key pasted on several lines is left for
  the shell to run; `/usr/bin/sudo /usr/local/estia/bin/estia signal-login` reads both the same way, as root from the
  root-owned tools (her own macOS account cannot open your terminal), and hands them to her on its input. Signal's
  Recovery key and Account ID: in your login keychain on this Mac. Every read of them asks first, in a macOS dialog,
  unless that program was given Always Allow there. Over a remote login (SSH), that dialog cannot show, so a read
  there fails: they are read only on the Mac's own screen (Keychain Access, or Screen Sharing). Setup never reads them
  back, so a macOS dialog that asks for them comes from another program: deny it. Always Allow for macOS's own
  security tool, which any program can run, lets every program you run read them without asking. A program you run can
  still remove or replace them without asking: keep both in your password manager too. The Account ID also stays in
  setup's answers file, which programs you run can read. Setup writes them there once Signal took them, on the input
  of macOS's own security tool, never as an argument, with no program trusted to read them (that tool included),
  checks each by its name only, never reading it back, and locks the keychain again when it opened it (over SSH).
  Setup runs Homebrew with its analytics off (`HOMEBREW_NO_ANALYTICS=1`).
- The security step (setup's, and `scripts/harden.sh`) sets remote login (SSH) to keys only, removes rules that let
  admin commands skip your password, makes her files private and sets her Registration Lock, each on your yes. The
  nuclear level, only for deeply technical users and best on a Mac used for Estia alone: the Mac's firewall on, with
  each program that other machines reach asked about one by one; python3 and ruby off its list; stealth mode and the
  keychain lock, each asked first. Outgoing connections and remote login (SSH) are never blocked, nor are signed
  programs while macOS's own setting lets signed software through (as on a new Mac).
  `scripts/harden.sh --nuclear` sets it, after saying plainly that it may stop other services on this Mac (programs
  other machines reach stop answering them unless allowed, no answer to ping, more keychain prompts). Without it they
  are one neutral line in harden and doctor, never a warning; nothing turns off what a Mac already has. The Mac's
  security has two levels: hardened, set by the security step (`scripts/harden.sh`), and nuclear, the hardened level
  with the Mac's firewall on and python3 and ruby off its list (`scripts/harden.sh --nuclear`; its stealth mode and
  keychain lock do not count toward the level). Doctor names the level the Mac is at.
- The setup window (Estia Setup) opens only at the Mac's own screen, never over a remote login (SSH) or inside screen,
  tmux or herdr, and is built from your checkout and never built or run in her release (its sources ride along
  unused). It shows only its own pages, each checked by its fingerprint (sha256) before it is shown: no network, no
  open port, no cookies or page data kept; a link opens in your browser, never in the window, and a force click shows
  no preview. Every setup step runs in it: each step's output in its log, each of its questions on a screen. It never
  asks for your Mac password: admin rights come from macOS's own password box ("Estia Setup wants to make changes."),
  and the password goes only to macOS, never to the page, the window's program or setup. The box comes from one fixed
  script inside the window's program (AppleScript's `do shell script … with administrator privileges`, an Apple event
  to the program itself, so no Automation permission is asked); each word of a command is quoted into it, never run as
  shell text, and root's environment is fixed (who asked is the window's own account, as sudo would say). As root it
  makes a fresh folder only root can change (`/private/var/run/estia-setup.<random>`) with the command's output, its
  result and, for its input, a pipe, each readable only by you; a key, PIN, password or secret address (the
  calendar's secret address, the address Spotify's sign-in ends on) goes to the command only through that pipe, never
  in its arguments, a file kept or a log, and the folder is removed afterwards. Mail's sign-in gets the mailbox's
  address as an argument, which every user of the Mac can read while it runs (an address, not a secret). Apple's
  note on this script (TN2065) says the box then waits 5 minutes before it asks again, for that one script; only setup,
  over the window's own pipe, can ask the window for an admin command, and the page can neither start one nor feed or
  end it. Another program of yours could read or write the folder's files while a command runs, a key on its way
  included (Terminal's path hands a key to sudo through a pipe no other program can open), and the folder's name is
  listed in `/private/var/run`; other accounts cannot open them. Such a program can already change the checkout setup
  runs from, so it is no new way to admin rights. While an admin command runs the window does not close, and Stop here
  waits for its end; a crash of the window leaves that command running to its end, its result unknown (setup checks
  every step again on its next run). One that has not read its input yet waits for it only until its time limit (10
  minutes), then ends, and root removes its folder. Known gap: her update's question in the window
  (`estia-deploy --ask-lines`) and Spotify's sign-in as her (`estia spotify-login`) have no time limit, so after a
  crash before their input was opened each waits until the Mac restarts. Terminal comes to the front only when setup
  goes on there (the window closed or broke), through
  macOS's own app switching (NSRunningApplication), never through an Apple event to Terminal; macOS may still keep the
  window in front. Setup keeps every answer and runs every check; a code, PIN or key typed in the window goes only to
  setup, and from setup only into the input of the command that needs it, never back to the window and never into the
  answers file. A typed Recovery key is held only by the page, through the cards, Signal's limit screen and the screen
  for the six-digit code Signal may ask for, until any other question, a stop or setup's end; one Signal refuses comes
  back on the window's card with every group kept. Ollama's key is checked only once given, by its one test search,
  never while it is typed. Untested at a desk: the window with a person, and macOS's box itself (its words, Touch ID,
  the 5 minutes, three wrong passwords); the admin runner is tested as a normal user only, never as root.
- Setup started by an AI (an AI coding assistant's command tool, at the Mac's own screen; since 0.9.8): the AI reads
  only setup's step lines, in its command's output and in `~/.config/estia-setup/ai-run.log` (0600): before the
  window, the tool steps' own lines (Homebrew, uv; in its command's output only), then each step's place, each
  screen's own title and id from setup's list (never filled in), where macOS's own box asks, a download's dots and one
  end line. Never an answer, a code, a PIN, a key, the captcha link, the one-time word, her number, an
  account ID, About you, the home area, a folder or a command's output: setup's own input and output go to nothing
  (`/dev/null`), every answer is typed in the window, and the Mac password goes only into macOS's own box. In such a
  run setup never reads its input, never asks in a terminal and never runs sudo itself (but for its read-only listing,
  `/usr/bin/sudo -n -k -l`, which never asks): each is refused and stops setup, every answer kept. Setup leaves the AI's
  command at once (a session of its own), so the command's time limit or end never stops it halfway. Refused before
  anything runs: a remote login (SSH), screen, tmux or herdr, a HOME other than your account's, and `--plain`,
  `--terminal`, `--extras`, `--answers` or `--config`. A sandbox is refused only after the tool steps (Homebrew, uv),
  before setup's first question: on a fresh Mac their own error may come first. The environment keys only tests set
  are dropped.
  What it cannot stop: an AI that runs as you. Such an AI can read the files setup writes as you before the move (the
  answers file, the config, `web.env`, `tapo.env`, `calendar.env`), read the web keys' copies in your login keychain
  (saved by `/usr/bin/security`, which reads them back without a dialog; Signal's items ask first), change the
  checkout and set setup's environment. SETUP-WITH-YOUR-AI.md tells it never to; the code cannot. An AI tool that
  gives its commands a terminal gets Terminal's route, where setup's lines show in its output: answers shown back,
  her number, the one-time word, the mailbox address, mail's device code, the light devices found and, without the
  window, the Account ID and the Recovery key as typed on the key card; never the captcha link, the SMS code, a PIN,
  a password or a web key.
  SETUP-WITH-YOUR-AI.md tells it to check first and leave that run to the person; the code cannot tell. Untested on a
  real laptop: the window opened from Claude Code and from the Claude desktop app, and a time limit that ends the
  command mid-download.
- With `[privacy] keep_days` set, older messages, pages and tool calls are deleted each night and overwritten in the
  database file; a message an open reminder or check-in points to stays. The summary of the current conversation and
  the profile's facts stay until `/new` or `/forget`, and so does the mark that the current conversation read outside
  text: its checks after outside text stay on.
- The model has no shell. With Projects on, its project tools read only files git tracks, at the level chosen
  (summary, docs or code), never secret-like file names, binaries or files over 200 KB, and never through symlinks.
  A file's text is read on demand and never stored: the tool log keeps a stub. A folder is listed by names only, at
  every level: only names git tracks, secret-like names never, nothing opened.
- An answer that reads mail, a project, a document or the chat history (her earlier answers, from every conversation)
  sends nothing to a search engine or a map: it runs no web search and no map request (refused in code).
  An answer that reads the calendar runs no web search; of the calendar's words, only an event's place may go to
  a map, never a title. A later message may search with words from that answer (a sender's name, a place);
  `/new` starts without them.
- What leaves the Mac during setup, besides the downloads its steps name (Homebrew, signal-cli, Ollama, the model):
  setup's menus, gum, once from GitHub (checked by its fingerprint); your home area, only its street and city, in one
  lookup at OpenStreetMap's Nominatim each time it is typed (no house number, except a number of 4 or 5 digits
  after the first comma, read as a postcode, or one joined to letters there: known gaps); a new Ollama key in one test
  search at ollama.com. Signal gets what registers her: her number or Account ID and Recovery key, the captcha, the
  SMS code and PINs, her profile name and username, encrypted, through signal-cli. Nothing else of your answers
  leaves: About you stays on the Mac, beside her config, for her first start.
- Brave Search, the web search's backup, stops at 900 of Estia's own searches each calendar month in code, below its
  $5 monthly credit; a key also used by other programs, or a Brave billing month that does not start on the 1st, can
  still go over it, so a key used only by Estia is recommended. A higher number in the config is held to 900.
- Places and routes: search words and an area rounded to about 1 km go to Nominatim (the OpenStreetMap Foundation);
  a route's two ends go to FOSSGIS's routing servers (walking, cycling, driving) or to Transitous (public transport).
  Once Google Maps is set up, it is asked first and gets the same, and the home address as written. No account and no
  key goes to OpenStreetMap's services; each request names Estia and estia.one, the same for every install. Each
  service logs what it gets: the OpenStreetMap Foundation the connection's address, the request and the time (its
  privacy policy names no period); FOSSGIS each route request, in its server log; Transitous the address, the request
  with both ends, and the time, kept for up to 2 days. OpenStreetMap gets the home area without a house number,
  except a number of 4 or 5 digits after the first comma, read as a postcode, or one joined to letters there (known
  gaps).
- Her home address stays in the private config: the database keeps only its area, rounded to about 1 km, and a tag of
  the address (HMAC-SHA256 keyed with the Google Maps key, or without one with a random key of her own in a private
  file, neither ever in the database), never the address, so the database alone cannot confirm a guessed address. A
  "Home area:" fact is in the profile, in the database, as every fact is. A Directions link to or from home names that rounded area. Rows an
  earlier version wrote with the address, and the Directions links it sent with it, are rewritten or deleted at the
  next start, and deleted rows are overwritten in the file.
- Mail access is read-only (`Mail.Read`), and mail bodies are not stored.
- Calendar access is read-only: Google calendars through their iCal address, Outlook calendars with `Calendars.Read`
  only (its own token request, never mail's token), CalDAV calendars (iCloud, Fastmail, Nextcloud) with CalDAV's two
  read requests only, PROPFIND and REPORT (any other request is refused in code).
- A Google calendar's secret iCal address is kept in a mode 0600 file, read only by the calendar helper.
- A CalDAV calendar's app-specific password is kept in that same file, read only by the calendar helper; the account's
  server, user name and calendar name reach the helper on its input, never on its command line. Its requests go over
  HTTPS only, to the configured server only (iCloud: Apple's icloud.com servers, as it answers from several), with at
  most 3 redirects, no proxy and no cookies kept, all within 12 seconds; an answer that declares XML entities is
  refused.
- Lights and plugs are reached on the home network only. Philips Hue lights are reached only through the Hue Bridge on
  the home network, never through Philips' cloud (no discovery service, no remote access). The bridge's key is kept in
  a mode 0600 file (`hue.env`), read only by the device helper, never shown and never on a command line. Every
  connection to the bridge checks its certificate against the SHA-256 fingerprint saved at pairing, and nothing is
  sent, the key included, to a bridge that shows another certificate. A device change requested after outside text (a
  mail, a calendar entry, a page, a search or map result, YouTube captions, a project file, a document, a photo or a
  shared audio file) waits for the user's yes. In English, an answer that says a light or plug was switched, dimmed or
  set while it was not changed gets the code's note "Nothing was switched." ("Nothing else was switched." when another
  one was); a plain report of a device's state is never flagged. After outside text, a sentence that tells a light or
  plug change as set for later ("the lamp is scheduled to be switched off at 16:30") is left out, and the code's note
  follows: "I have no light or plug change scheduled: lights and plugs are switched only when you ask, never because a
  calendar entry, a mail or a page says so." English only for now, in these wordings: "scheduled", "set", "due",
  "planned" or "going" to switch, "will switch", a timer or an automation to switch, and a switch told with a time of
  day ("turns off at 16:30", "gets switched off tonight"); another wording goes out as she wrote it.
- Text from outside sources is treated as data, never as instructions. Outside text may hold her back, never start
  anything but a note the code writes: a mail, a calendar entry, a page, a search or map result, YouTube captions, a
  project file, a document, a photo or a shared audio file can pause the music or turn it down, make a device change
  wait for your yes, or fill a note the code writes (the digest; a calendar change, or a new document in the documents
  folder, starts one); it never starts a message the model writes, a call, a reminder or a device change. A note the
  code writes reaches the model's chat history only as the bridge's note, never as her words, and without the hidden
  turn markers that outside text could carry; one that quotes outside text is marked as outside text there. While such
  a note is in her history: a reminder is set only when your own message asks for one, in your words; a fact is saved
  or forgotten only when your message asks for it, never in the note's words; a reminder is cancelled only when your
  message names it or asks to cancel one; a light or plug your message does not name waits for your yes; a link in her
  answers reaches you defanged. A web search, map or Spotify request never takes its words from mail, calendar
  or document text in a note (an event's place read from the calendar in that answer may go to a map); the check
  compares words, so a paraphrase or a translation is not caught (a known gap).
- YouTube: only a video link in the user's current message is read, or the one link of a video she found after the
  user's typed YES, and only its 11-character video ID reaches the YouTube helper, never the link; a video named in
  a web search result is never read. A video she finds (`find_video`) is searched and ranked by code, which texts one
  match with its link; it is read only after the user's next typed message in that chat is a YES (the whole message,
  within 2 minutes; a voice note or a call never counts; anything else ends the match; the list lasts 10 minutes, in
  memory only). Its title and channel are outside text: cleaned, at most 80 characters, shown in code's message; after
  a typed `another`, they reach her only as code's note, marked as outside text. Her tool result never holds them, and
  the first match's message does not reach her, unless her answer to that request failed. Its search words go out as a
  web search's do (Brave, or Ollama as the backup), with the same guards. The helper asks only
  https://www.youtube.com (every request and redirect checked, at most 3 redirects), with no proxy settings, no
  cookie file, no account and no key of its own (it uses the public key YouTube puts in the video's page); cookies
  YouTube sets last only for that one run. It stops itself after 20 s, and the bridge stops it at 30 s. YouTube sees
  the Mac's internet address, the video's ID and a user agent that names this helper (local-assistant youtube
  helper). The captions are outside text (no saved facts, no reminders, device changes wait for a yes, links
  defanged; only the user's own video link stays tappable, never one Gemma passed) and never stored: the tool log
  keeps the link, as the user's message does, and a stub in place of the captions, and the last video's captions stay
  in memory only, for 10 minutes. Her answer never holds the captions word for word: code leaves out each sentence
  that copies 13 or more of their words in a row, or shorter stretches of 6 or more that together make up more than
  30% of them, and adds the video's link. Nothing is transcribed. Logs hold outcome codes, character counts and
  times, never the video's ID or its title.
- Reminders and wake-up calls come only from the user's own words: setting or cancelling one is refused in code in an
  answer that read outside text, and in the message with a shared audio file or a photo. A message with a photo never
  sets a reminder. A later message that still sees the photo (five minutes) sets or cancels one only as after outside
  text (below), never in words only the photo holds. She places a call only to an allowed account and only for a
  reminder the user set; the confirmation is written by code.
- After an answer that read outside text, and until `/new`, the same conversation's later answers change the profile
  and reminders only on your own message: a fact is saved only when your message holds its words, or asks for it and
  names it (a word of it, with every number and link of it); a fact is forgotten only when your message asks for it
  and names it (a word of it, or its number); a reminder is set only when your message asks for one in your words,
  and cancelled only when your message names it or asks to cancel one; a light or plug your message does not name
  waits for your yes. A fact or reminder refused for outside text keeps none of its words, in the tool log or in the
  bridge's notes, and a device change held for it none in the tool log.
- Messages she starts herself are opt-in (off until the user types `/initiative on` or the config turns them on) and
  decided by code (triggers, quiet hours, caps) before the model is asked; the model then sees only the user's own
  words, profile facts and the moment (never mail, calendar, web or pages), and code checks its text before anything is
  sent: no link, on any ending, and no request for a code or a password, in all 11 languages. Each is sent at most
  once, only to allowed accounts; `/quiet` pauses them and `/initiative off` turns them off.
- The evening note is opt-in too (off until the user types `/evening on` or the config sets its time) and written by
  code alone: three fixed questions, no model. It goes only to the allowed account that turned it on, at most once an
  evening, waits for quiet hours, `/quiet` and `/lock`, and reaches the model's chat history only as the bridge's note,
  never as her words; `/evening off` stops it.
- Keys live in mode 0600 files or the login keychain. Logs hold only what happened and when, never message text, and so does
  `status.json` (each part's last use for doctor and `/status`: times and fixed reasons only).
- A privacy check (`scripts/privacy_scan.py`) keeps private files, keys and personal values out of the repo. It runs in
  `scripts/verify.sh` and, if you turn it on, before every commit. In her own macOS account, her config's private values
  are checked through salted hashes of them that her account gives (`~/.config/local-assistant/privacy-values.json`; the
  check's note shows the command), never the values themselves.

## Vulnerability management

- Every Python package is locked to one version and its fingerprint (`uv.lock`, and one for each helper): a changed
  file is refused at install. The voice service's Swift packages are locked to one commit each (`Package.resolved`).
- Updates never install themselves: a new version comes only with a release, after the tests. A version less than 7
  days old comes only when it is its maintainers' fix for a known hole. Python's own patch version (3.13.x) follows the
  uv installed on the Mac, not the release: each update installs the newest 3.13 that uv knows (Homebrew's uv, kept
  current by `brew upgrade`).
- Known security holes in the Python packages are checked against the public OSV database every week and at every
  push on GitHub, and before each release on the Mac, offline (`scripts/vuln-scan.sh`). A release with a known hole is
  not published. The release on GitHub runs the same scan over its own version first, and a known hole stops it there.
  The Mac's scan names the day of its copy of the database, and says so when that copy is over 3 days old. GitHub
  pauses the weekly scan after 60 days without a change to the repository.
- Her Python's own OpenSSL encrypts her connections to mail, calendars, maps and the web. The scan does not cover it:
  before each release, the OpenSSL of the Python a deploy installs then is compared by hand with the newest release
  of its series on OpenSSL's site, openssl-library.org. Doctor names her Python's version and its OpenSSL's.
- Model files are checked by fingerprint before they load: Whisper's, the speech detector's and the turn detector's,
  at every start. With `model_digest` set, Gemma's model is checked against its pin before every chat.
- Not covered: holes nobody has reported yet; the Swift packages, checked by hand at each release; Python itself,
  Ollama, signal-cli's build for calls and the call helper; Homebrew's programs, kept current by Homebrew
  (`brew upgrade`).

## If the Mac is compromised

Assume everything she holds was copied: her Signal identity, the Microsoft sign-ins, the calendar addresses and
passwords, the keys, the TP-Link login and the Hue Bridge's key. The same holds when a program of yours may have read
her folders before the move into her own macOS account: a copy taken then still works. In this order:

1. Stop her: `/usr/bin/sudo /usr/local/estia/bin/estia stop`. She starts again at the next restart of the Mac, with
   the copied keys still valid: finish steps 2 to 8 first.
2. Re-register her number from a clean machine (setup_assistant.md, 2.3): this logs out every copy of her Signal
   identity. Then compare her safety number on your phone and mark it verified. An account without a number has no
   number to re-register, and its Recovery key is in her signal-cli data on the Mac and in your login keychain, so assume
it was copied: whoever
   holds it can take the account over at any time, and nothing takes it back from them. Make a new account without a
   number (setup_assistant.md, 2.3, B) and move her to it only once the Mac is clean (macOS reinstalled), as a number
   is re-registered from a clean machine: on a Mac that may still be compromised, the new Recovery key lands where the
   attacker is. On the clean Mac, setup's second choice moves her to it (or `/usr/bin/sudo
   /usr/local/estia/bin/estia signal-login`, once she runs there again). Stop using the old one (her old chat: the
   known gaps below).
3. Microsoft: at mysignins.microsoft.com choose "Sign out everywhere" for each mailbox, then sign in again with
   `/usr/bin/sudo /usr/local/estia/bin/estia mail-login <address>`.
4. Google calendars: reset each calendar's secret iCal address (Google Calendar, Settings, Integrate calendar), then
   save the new one with `scripts/set-calendar-url.sh [name]`. CalDAV calendars: revoke each app-specific password
   (account.apple.com, Fastmail's or Nextcloud's settings), then save a new one with
   `scripts/set-calendar-password.sh [name]`.
5. Keys: create new Brave, Ollama and Google Maps keys, delete the old ones in each console, then save them with
   `scripts/set-web-keys.sh`.
6. TP-Link: change the account's password (and turn on its two-step check), then save it with
   `scripts/set-tapo-credentials.sh`. Hue: a copied key works from the home network until the bridge forgets it; a
   factory reset of the bridge removes every key (its rooms and scenes too); then pair it again with
   `scripts/hue-pair.sh`.
7. Registration Lock PIN (a number only: an account without a number has none): set a new one for her number with
   `scripts/harden.sh --new-pin` (also while the lock is on;
   it sends signal-cli `setPin` through `jsonRpc` with the PIN on its input), and keep it in your password manager, off
   the Mac. She stays stopped after it: it starts her again only when she was running before it (then it stops her for
   the PIN and starts her after).
8. Check your own phone: Signal, Linked devices; remove any device you do not know, then send her
   `/devices forget N` for it from your phone. Check hers:
   `/usr/bin/sudo /usr/local/estia/bin/estia signal listDevices` (only the Mac).
   She stays stopped after it: it starts her again only when she was running before it. Then start her:
   `/usr/bin/sudo /usr/local/estia/bin/estia start`.

## Known accepted gaps

- Several allowed accounts share one chat history, the profile and its notes, mail, calendars,
  documents, the morning digest and the last location sent, and each one can change or remove the others' facts.
  Allow only people who may see all of it. Doctor names it with a "!" when the config allows more than one.
- signal-cli is an unofficial client, and Signal's terms list automated messaging and dialing as not allowed: Signal can
  disable her account. Keep the volume low (one household), and give her a separate number, never your own.
- Signal's own delivery receipts go to any sender, so a stranger can tell that the number is registered.
- An account without a number has only its Account ID and its Recovery key. Keep the Account ID and the Recovery key
  in your password manager: without both, the account cannot move to another Mac, and no number, SMS code or PIN
  brings it back. After a break-in there is no number to take it back with (If the Mac is compromised, step 2). The
  Recovery key is the secret one: whoever holds it and the Account ID can register the account on another device,
  which then gets her messages and calls and logs this Mac out. The Account ID is no secret by itself: it shows in the
  list of running programs (`ps`), as her number does, and every account she writes to learns it. On such an account
  texts and voice notes were tested both ways (23 Oct); a call she answers on it is untested. signal-cli can lose the
  profile name and the username when it takes an account over (signal-cli issue 2124): setup and `/usr/bin/sudo
  /usr/local/estia/bin/estia signal-login` set both again.
- The keychain copies of Signal's Recovery key and Account ID ask before every read, never before a delete: a program
  you run can still remove or replace them without asking. Keep both in your password manager too. Always Allow for
  macOS's own security tool, which any program can run, lets every program you run read them without asking.
- `/usr/bin/sudo /usr/local/estia/bin/estia signal-login` never unregisters her old account: it stays on this Mac,
  unused and never online. Its last lines say what that leaves open, for a number: "Her old number's Registration Lock
  lapses after 7 days offline: whoever gets that number next can write to you from her old chat. Delete or block that
  chat on your phone and never trust it again; keep the SIM if you can." For an old account without a number: "Whoever
  holds her old account's Recovery key can still write to you from her old chat. Delete or block that chat on your phone
  and never trust it again." An old account's Recovery key and Account ID that setup kept in your login keychain stay
  there under the same names: remove them in Keychain Access.
- signal-cli starts the call helper for every incoming call before the bridge decides. The helper connects only through Signal's
  relay servers, so callers do not learn the Mac's addresses.
- The call helper is built on an old version of WebRTC, the calling library (revision 7444f).
- Prompt injection is reduced by code-level rules, not eliminated. A model can still be talked into a wrong answer.
  Device changes and profile changes have their own checks in code. The music check holds for the answer that read
  outside text: a later message may start music on words from that answer.
- A Mac with 16 or 24 GB of memory gets the smaller model, Gemma 4 12B (`gemma4:12b-mlx`), which failed some of her
  safety tests (15 and 28 Oct): on 15 Oct, run twice, after a forwarded audio file with planted instructions it tried
  to save facts and switch a light (2 of 5 runs passed), and on a call it tried to switch a device before the question
  was answered (3 of 5). Gemma 4 26B, on 32 GB or more, passed both 5 of 5. On 28 Oct (0.9.8) the 12B also missed her
  test for lines planted in mail and calendar: 6 and 8 of 10 tries passed, against 10 of 10 for Gemma 4 26B. She said
  a reminder was set that never was, offered to set a reminder to send a PIN, and retold planted lines as fact. In no
  try was anything set, saved or switched. Most of her other safety tests have not been run on the 12B. Code still
  refuses facts from an audio file and still makes
  a device change wait for your yes after an audio file, for an `ask first` device and in a message that is not about
  devices, but 32 GB with Gemma 26B is the tested setup.
- **Deep Thinking acts on planted text more often.** In tests with Deep Thinking on, she tried to set a reminder from
  words planted in a photo in 9 of 10 tries, and to switch a device or set a reminder from lines planted in mail and
  calendar in 9 of 20, against none of 20 without it. Code refused or held every one. Deep Thinking passed its safety
  tests in one run, on the 26B model only. On Macs with 24 GB, Deep Thinking runs Gemma 4 12B, which has not been
  tested with it.
- One user per Mac is assumed. Ollama, reachable only from the Mac itself (127.0.0.1:11434; her own Ollama:
  127.0.0.1:8151), is a trusted local service; on a Mac shared with other accounts, another user could start a
  look-alike service at the same address first. Setup refuses an Ollama that an account other than yours (for her own
  Ollama: hers) runs, also one beside it on the same port, and doctor shows either with a ✗ where it can list the
  program; the bridge itself does not check before each request: while her own Ollama restarts, another program could
  take its address first. Where macOS's list of network connections (`netstat`) shows none (seen on macOS 27.0.1 on 3
  Nov), setup finds her own Ollama through its background service (doctor, as her, with `lsof`), and neither can see
  a program another account runs beside it on the same port. Doctor's Ollama line then says that other accounts'
  programs on its port could not be listed, or, where it finds no program at all, that its program and account could
  not be read. The voice
  service (127.0.0.1:8150) runs as her: the bridge sends it audio and text only over a connection
  that a program of her own account took, checked after the connection is made and before anything is sent (macOS's
  `lsof`, run as her, lists only her own programs). A program of yours, or of another account, that takes that
  address while her voice service is not running gets nothing, and its words never count as yours; her voice notes
  and calls fail meanwhile, her log says why ("not hers"), and doctor and `/status` say "another program holds its
  address: nothing sent". Doctor's voice lines, the "voice: ready" line at the end of a move, a setup or an update,
  and `/usr/bin/sudo /usr/local/estia/bin/estia check` read that program's health answer without the check. Before
  her own macOS account the bridge and the voice service run as you: the check cannot tell your programs apart, and
  her voice service answers every program of yours. Her wrapper's commands, an update, the move and setup read her
  voice's state as her, through admin rights: untested on a live Mac (docs/e2e-checklist.md, 157-160).
- Her voice service writes one log line for a refused connection, then, while more come, one line a minute with their
  count. A program on this Mac, of any account, that connects to it over and over still slows it a little, as it would
  any service on the Mac: on a call, a speech detector answer later than 0.3 s counts as missed, and after 3 in a row
  the call goes back to loudness and the full pause until it ends. Her voice log is emptied once it passes 10 MB: at
  one line a minute, after about two months. A refused program that sends a large request at once may see its
  connection reset before the refusal (`{"error":"other_account"}`) reaches it.
- A voice note forwarded inside Signal keeps its voice-note flag, so it counts as your own words.
- On a call, every voice the phone picks up counts as the caller's: a TV or another person in the room can set a
  reminder, save or forget a fact, or switch a `normal` device by name, and their yes counts as yours for an `ask
  first` one. Code cannot tell voices apart.
- On a call, her own voice from a loud speaker next to the microphone (a laptop on Signal Desktop) is told apart from
  the caller's by its words (setup_assistant.md, Her own voice). Her own voice that is not told apart counts as the
  caller's words, as another voice in the room does: when she reads a mail or a page aloud, its words can set a
  reminder, save or forget a fact, or switch a `normal` device by name; untested on Signal Desktop with speakers
  (docs/e2e-checklist.md, 52a).
- Kokoro's and Supertonic-3's files from an install before 3 Nov 2026 (downloaded as published, with no check of their
  files) stay in her home until deleted by hand (setup_assistant.md, section 3, Voice notes); nothing loads them.
- Pocket TTS's sandbox (every Pocket voice and language) closes her home, the network, writing, other programs and
  macOS's own services, never reading elsewhere: a flaw in Pocket TTS's program that any text sent to her voice service could reach would
  still read what her account reads outside her home (the projects folder and `~/Estia-KB` when shared, her logs in
  `/var/log/estia`). `sandbox-exec` is deprecated in its man page, and still shipped with macOS.
- If Kyutai removes the v4.0.1 release from GitHub, an update cannot download Pocket TTS's program: it says so and
  goes on, and her answers come as text. If the model's version is removed from Hugging Face, a new install cannot
  download it: her answers come as text, and the voice service tries the download again every 30 minutes. A
  language's model is tried again only when she next speaks that language after the wait (1, 5 or 30 minutes); her
  answers in it come as text meanwhile.
- The Pocket TTS voices need an update whose root-run tools fetch their program: with tools from before it, an update
  goes live without the program and her answers come as text, until `--update-tools` and one more update.
- While Pocket TTS loads, a call is not picked up: it rings out, and a text says her voice is still loading. While it
  rests after failed runs or cannot run, and speech recognition is ready, a call is answered and each of her answers
  comes as a Signal text in the same chat until the call ends; with speech recognition not ready too, the call rings
  out, and a text says her voice is not working. During a call, a line Pocket TTS cannot say is left unsaid, and the
  rest of that answer comes as a Signal text in the same chat.
- Health checks (`[health] extra_checks`) follow redirects; list only services you run.
- CalDAV is untested live with iCloud, Fastmail and Nextcloud (recorded answers only). A server that sends the
  calendar helper on to another host (iCloud's own aside) is not followed: its final address goes in the config.
- YouTube's terms forbid automated access and downloading its content, captions included, with no exception for
  personal use; the YouTube helper does both: accepted for personal use. YouTube blocks heavy use and cloud servers; two reads from a home
  connection worked, repeated use is untested. No proxy is used, so a block stops video links until YouTube lifts it
  (`/status` and doctor show it).
- The YouTube helper relies on an undocumented YouTube endpoint, through youtube-transcript-api 1.2.4 (pinned): it
  reads the video's page, then YouTube's internal player address with the key from that page, naming YouTube's
  Android app as its client, then the caption track. YouTube can change any of these without notice, or ask for a
  token that proves a request comes from its own app (reported for some caption tracks); reads then fail
  ("unreadable answer" in doctor and `/status`) until a newer pinned version. A change at YouTube or a reworded bot
  check can also look like an answer: every video then seems to have no captions, or to be private or removed, and
  `/status` and doctor still show YouTube working. If videos that clearly have captions get either answer, suspect
  YouTube, not the video. The helper also calls two internal steps of that version, to learn which captions YouTube
  shows by default: check them whenever the pin changes. In the EU, YouTube may first show its cookie consent form:
  shown on www.youtube.com, the library accepts it with a cookie for that one run; a redirect to consent.youtube.com
  is refused by the host check and the read fails ("unreadable answer"). Untested live.
- A link quoted inside outside text (a mail, a page, YouTube captions, the calendar, a search or map result, a photo,
  a shared audio file) reaches you only defanged (`example[.]com/login`) unless you sent it yourself: in her answers,
  in language practice, in the code's "Not set" line for a reminder and in the call summary, also a link inside
  another link and an address by its numbers. A bare name counts on every ending Signal makes tappable, so a file
  name on one is defanged too (`setup[.]sh`); a name on an ending Signal does not link (`report.pdf`) stays as
  written, and so does one on an ending added to the internet's list after this release.
  Check a link's address before you open it, as with any message.
- The refusal to search after mail, a project, a document or the chat history holds for the answer that read it: a
  later message may send words from that answer (a sender's name, a place, an amount) to a search engine or a map.
  `/new` starts without them.
- Anyone can edit OpenStreetMap: a place's name or its hours as listed may hold planted words, and its website may
  be anyone's (shown by its name, its link only in the Sources note, as a web result's is). They are outside text
  (no remember, forget or reminder from them, a device change after them waits for your yes, links defanged), and
  opening hours are read only in their common forms: any other form is shown as listed, never read as open or closed;
  public holidays are not counted. A route from or to home starts or ends at the home area, rounded to about 1 km, so
  its time is approximate.
- The home area's house number is told apart from a postcode by its place and its length. After the first comma, a
  number of 4 or 5 digits reads as a postcode: it goes to OpenStreetMap and gets no advice, unless a flat's or a
  building's word comes right before it ("Suite 1200", "Bldg 1234", "Rm 1203"), the part right before its part is only
  a flat's, a building's or a floor's word with its number, a letter label or an ordinal ("Apt 4B", "Building B", "3rd
  Floor", "Second Floor", "Level 2", "#4"), or an English street word follows it ("1234 Main St", "1234-B Main St";
  "St." or "Ter" right before a town's name does not count: "9000 St. Gallen", "9561 TA Ter Apel"). In "Town, 1234
  Example Loop" and "Apt 4B, Example Tower, 1234 Example Loop", 1234 goes. After the first comma, a number joined to
  the letters before it goes too, with or without a dash ("B-7", "No12"), unless a flat's or a building's word comes
  right before it ("Unit A-12").
- FOSSGIS's routing servers and Transitous are for non-commercial use: a commercial install needs other servers. How
  long FOSSGIS keeps its server log was not checked (its privacy policy is in German, and its site refused automated
  reads).
- Calls name no device: a call from a new linked device rings like one from your phone (texts from it are held).
  Such a call is answered too. A text after each call she answers gives its start and end time, on all your
  devices; a device linked to your account can delete that text, and Signal then deletes it on every device. A
  wake-up or reminder call she places rings every device on your account: whichever picks it up hears the
  reminder and can talk with her, and only the summary, after two or more turns, gives its time.
- Whoever has your unlocked phone ends any lock of your account, one sent from your laptop too, 10 minutes later at the
  earliest. With your phone gone, stop her on the Mac: `/usr/bin/sudo /usr/local/estia/bin/estia stop` (she starts
  again at the next restart of the Mac).
- The TP-Link login goes to a configured device's address over the login that device asks for: KLAP, or AES login
  v2 (a hash of the password, never the password itself; AES login v1 is refused, and listing devices sends no login).
  A machine that takes a device's address (a guest's laptop, after the router gives that address away) and asks for
  AES login v2 gets that hash: enough to guess the password offline and to control your TP-Link devices on the home
  network. Use a TP-Link password used nowhere else, with TP-Link's two-step check on, and a fixed address per device
  (a reservation in the router).
- The privacy check's hashed values (`~/.config/local-assistant/privacy-values.json`) are readable by programs of
  yours: a short value (a device's address, a phone number) can be found from its hash by trying every possible one.
- Pairing the Hue Bridge trusts the certificate it sees first (trust on first use): a machine on the home network that
  answered in the bridge's place while you pressed its button would be trusted. Pairing shows the certificate's check
  value, and every later connection checks the same certificate. A certificate that changes (a factory reset, a new
  bridge, perhaps a firmware update) stops the Hue lights until the bridge is paired again; doctor names it.
- Hue lights are untested on a real Hue Bridge so far: the tests use answers in the shape of Philips' published
  API (CLIP v2) and a local test server, not a bridge.
- The local AgentJev service (the optional mail pre-filter) is a separate project: its server does not check the Host
  and Origin headers of a request, so a web page in a browser on this Mac could reach it (DNS rebinding). Run it only
  reachable only from the Mac itself (127.0.0.1), and only while you use it.
- The Ollama app, an Ollama set up before 0.9.8 or without her own account, and AgentJev run as you: every prompt she
  sends (her history, profile, mail summaries, calendar events, call transcripts) and every mail header passes through
  a program your account can change, its logs or a look-alike on its port. Her own macOS account keeps her files and
  keys from your programs, not what she sends there. Her own Ollama (a new install with her own account, since 0.9.8)
  ends this for Ollama: her prompts go only between her bridge and her Ollama, both her account's, its program a copy
  only admin rights can change. An update never moves an older install's Ollama, so there the gap stays.
- Her own Ollama's address (127.0.0.1:8151) has no login, so any program on the Mac, of any account, can send her
  Ollama input: ask her model, delete her model (she pauses until setup puts it back) or make `local-worker` again
  (the digest pin pauses her). A flaw in Ollama itself would run code as her account, with her Signal keys, mail
  sign-ins and memory, not as yours. Not built yet: a check of who holds that address before each request, and an
  account for Ollama alone.
- Root, and any program that gets root, still reads everything. While an admin rule skips the password (`NOPASSWD`,
  in `/etc/sudoers` or `/etc/sudoers.d`), or one admin sign-in counts for every program of yours
  (`timestamp_type=global`) or for every program started by the same program (`timestamp_type=ppid`; Apple and NIST's
  macOS security baseline allows one sign-in per Terminal window only), or one admin sign-in lasts longer than 5
  minutes or never ends (`timestamp_timeout`), every program you run gets root without it (with a long sign-in, for
  that long). Doctor and the security step read what sudo lists for you, and the Mac is then not at the hardened level.
  Doctor run as you, before her own macOS account, reads only the rules (sudo lists the sign-in settings only after
  your password), so its hardened level does not cover them. In `/etc/sudoers.d` the security step removes such a
  rule. One in `/etc/sudoers` is shown with `visudo`, to remove by hand. Even without such a setting, an admin sign-in
  lasts 5 minutes in its Terminal window, and each admin command starts them again: in that time every program in that
  window, one already running in it too, gets root without the password, so run her admin commands in a Terminal
  window of their own and end with `/usr/bin/sudo -k`. An admin's password, caught once, is root. Every
  command after `/usr/bin/sudo` is named by its full path too (`/bin/launchctl`, not `launchctl`): sudo looks a bare name
  up in your Terminal's folders first.
- Typing `/usr/bin/sudo` stops a fake `sudo` in your Terminal's folders, not one written into your shell's startup
  files: zsh, macOS's shell, runs a function or an alias named `/usr/bin/sudo` (in `~/.zshrc` or `~/.zshenv`, which
  programs of yours can change) even when the full path is typed. Before you type the password,
  `whence -va /usr/bin/sudo` must list only the file, never a shell function or an alias. The scripts that handle a
  key or run an admin command (`scripts/set-web-keys.sh`, `set-calendar-url.sh`, `set-calendar-password.sh`,
  `set-tapo-credentials.sh`, `setup-google-maps.sh`, `harden.sh`, `install-daemon.sh`, `install-voice-daemon.sh`,
  `install-ollama.sh`, `migrate-to-own-user.sh`, `hue-pair.sh`, `mail-login.sh`, `spotify-login.sh`,
  `tapo-discover.sh`) run macOS's own bash, look in the Mac's own folders first and ask the keychain only through
  `/usr/bin/security`, so a fake `security` in Homebrew's folder never sees the keychain's password. The four that
  save a key use only the Mac's own folders; the others then look in your Terminal's folders for uv, Homebrew's
  signal-cli and gcloud, which your account can change. `scripts/setup.sh` keeps Homebrew's folder first: it installs
  and runs Homebrew's tools. It also saves web search's keys and the keychain copies (the web keys, Signal's Recovery
  key and Account ID), through a Python started by Homebrew's uv; that Python writes the keychain only through
  `/usr/bin/security`, by its full path (or a test's stand-in for it, only when `ESTIA_TEST_ROOT` is set in your own
  environment).
- The move and each update trust your copy of the code (the checkout) and Homebrew at that moment: what they copy is
  what she runs (the update shows the list of changes and the files first). The first move runs your copy's code as
  root, after your Mac password; after it root runs only `/usr/local/estia/tools`. With her own macOS account
  planned, setup's first step (Ollama) also runs your copy's code as root once, before the move, after your Mac
  password, to make her account. Each release's Python is installed by your account (uv, as you) and only its version is shown: when `--update-tools` takes a new version,
  root then runs a Python your account built. `scripts/harden.sh` runs from your
  copy as you, and its admin sign-in lasts 5 minutes in that Terminal window: run it from a copy you trust.
- After moving an older install (one that already ran always on as you), her old folders, with old copies of her keys,
  are held where only root reaches them until you delete them with `/usr/bin/sudo /usr/local/estia/bin/estia
  remove-old`; the rollback works for 7 days (remove-old ends it sooner). A first install has no rollback: the move
  keeps no copy, and her only copy of her data is in `/var/estia`. A move cut off by a power cut keeps its record of
  each held folder: doctor names them, `remove-old` deletes them, and an older install's rollback still goes back.
  Earlier copies kept aside in `/var/estia/.before-<time>` (a retried move or a move after a rollback) hold older copies
  of her keys and Signal data, readable only by `_estia`: the move lists them, and the same `remove-old` deletes them. A
  copy a program of yours took before the move still works: if one may have, follow "If the Mac is compromised". A
  rehearsal (`--rehearse`) holds a copy of her keys in the folder you name: delete it afterwards.
- A retried move changes her home only as her: root never makes, moves or writes anything there by a path her account
  could have changed. The rollback gives your folders back a name at a time, never through a link in your home, and
  copies back only her data: never her `vendor/` folder, and nothing at all when her data holds a program, a link out
  of its own folder (copied back, a link within her home would lead to your file of that name), a named pipe or a
  device (her newer data then stays in `/var/estia`). What it copies goes into a folder of yours first and is checked
  there again, so one added while the copy runs never reaches your folders either.
- `_estia` has no password: the move sets `Password *` and deletes `AuthenticationAuthority` and `ShadowHashData`, doctor
  checks the record, and once the security step has set SSH to keys only, its drop-in also denies the account. The
  first real move ran on 13 Oct; that macOS 27's `sysadminctl -roleAccount` leaves nothing else to log in with is
  still untested beyond doctor's record check (docs/e2e-checklist.md, 113).
- On macOS 26 and later, with FileVault and remote login (SSH) on, macOS answers remote login after a restart, before
  the disk is unlocked, and takes an account name and its password there to unlock it. The security step's keys-only
  setting is kept on that still-locked disk and does not apply then. Until the disk is unlocked, remote login takes
  passwords: anyone who reaches the Mac can try them. A right password there unlocks the disk but opens no session:
  macOS ends that connection, and the keys-only setting applies again. For a planned restart,
  `/usr/bin/sudo /usr/bin/fdesetup authrestart` unlocks the disk as the Mac starts, so remote login takes no passwords
  after that restart; after a power cut it still does. Read from macOS's own manual pages
  (`man apple_ssh_and_filevault`, `man fdesetup`), not tested on a live Mac.
- launchd opens her services' output files (`launchd.log`, `voice.log`, her own Ollama's `ollama.log`) as her
  account, never as root: with the file missing, launchd did not start a test service run as her account (measured on
  30 Oct, macOS 27.0.1). Each is made hers, 0600, before her service starts, in `/var/log/estia`, a folder only root
  can change, so her account cannot put a link there. An install from before keeps each of the first two in her own
  folder, where her account can put a link in its place until `/usr/bin/sudo /usr/local/estia/bin/estia doctor` shows
  ✓ Services; launchd follows that link as her account, so only into a file her account can change already. An
  update that changes the tools root runs takes
  three steps, in this order: `/usr/bin/sudo /usr/local/estia/bin/estia-deploy`, then
  `/usr/bin/sudo /usr/local/estia/bin/estia-deploy --update-tools`, then
  `/usr/bin/sudo /usr/local/estia/bin/estia-deploy` again.
- A value typed on a `/usr/bin/sudo …` command line lands in sudo's log, which admins can read: `estia secret set` refuses one,
  but the log keeps it; change that key.
- A linked device's number is trusted until the device is silent for 14 days or `/devices forget N`: a device linked
  under a removed one's number within that time is answered. The Registration Lock lapses after 7 days with the Mac
  offline.
- With Projects on, her own macOS account reads every file in your projects folder (`[projects] root`, shared by
  `scripts/share-projects.sh`), also files git does not track, such as `.env.local`: her project tools open only
  tracked files at the level chosen, and she has no shell, so
  only code running as her could read the rest.
- Documents: macOS guards Documents, Desktop and Downloads itself (its privacy protection), whatever the access
  entries allow. On the live Mac (14 Oct) it refused even your own SSH session a change to `~/Documents`' access
  entries ("operation not permitted"), so the documents folder is `~/Estia-KB`, which it does not guard. A `[documents]
  folder` set inside one of those three may be refused to her account: her log names that refusal (`EPERM`, apart from
  a missing share's `EACCES`), and so do `/status` and doctor's last-look line. Doctor's own read runs from your SSH or
  Terminal session, which macOS may judge apart from her background service, so its own line may differ from what she
  gets. macOS refusing `~/Estia-KB` itself is not expected and untested: doctor and `/status` then say "macOS refuses
  ~/Estia-KB itself (unexpected)", and her log has the `EPERM`.
- The share looks for a git checkout (`.git`) only when it runs, and only in `~/Estia-KB` itself: code put there later
  (a `git init`, or scripts without a `.git`) sits beside the files her account adds. Keep no code in `~/Estia-KB`.
- With Projects or Documents on, her account passes through your home by name. Your home is closed to her otherwise,
  and that is what normally keeps the files in it closed: a file directly in your home that lets everyone read it, or
  a folder there that lets everyone in, and every file below such a folder that lets everyone read it, can be opened
  by her account once it knows the name. That is the macOS default for a new file or folder: `~/.zshrc`, `~/.gitconfig`,
  `~/.config` and the settings in it, other folders of code. Her code never does; a program that took over her
  account (a flaw in PDFKit or Vision, below) could. Both share scripts, and doctor in her own macOS account, name what
  directly in your home lets every account in, with the command that closes it (`chmod -h o-rwx`); an `~/opt` that holds
  Ollama's own build keeps only her way through it (doctor reads that build as her), so for it the fix closes its
  listing (`chmod -h o-r`), and what else in it lets every account in is named too. In a terminal the share scripts then
  ask once whether to run it, as you (Enter runs it; a no keeps everything as it is), and say what is closed; a name an
  access rule (an ACL entry) still opens to her is never called closed. Doctor changes nothing of yours.
  Doctor runs as her and cannot list your home: it looks up only the usual names (`.zshrc`, `.config`, `.ssh`,
  `.claude` and the like), and says so. A projects folder deeper than your home's first level (`~/code/work`) passes her through each
  folder on the way too (`~/code`), whatever its own mode: the projects share names what each of those folders holds
  that every account may open, with the same `chmod -h o-rwx`; doctor does not look there.
- Any compromise of her account could also leave files of any kind in `~/Estia-KB`: a PDF that exploits a flaw in
  PDFKit or Vision, or a flaw in signal-cli (which reads every sender's messages) or in the call helper (which starts
  for every incoming call). A `.command` file or an app made on the Mac carries no download mark, so Gatekeeper
  (macOS's check of downloaded apps) does not check it when opened, and nothing looks for such a file there yet: open
  only PDFs from that folder.
- The documents helper runs as her account without a sandbox of its own: a PDF that exploits a flaw in PDFKit or
  Vision could run code as her, with her keys and data. The helper opens no network connection in its own code and
  reads a scan with every connection denied (tested); running it under macOS's `sandbox-exec` with no network is
  planned, not built.
- With Documents on, her own macOS account reads every file in `~/Estia-KB`, not only PDFs and text files, and the
  text of each one stays in her database until the file leaves the folder (`[privacy] keep_days` leaves it alone).
  PDFKit also reads text that does not show on the page, such as white text: an order hidden there is outside text like
  any other.
- A Markdown file's name ends in `.md`, which is also Moldova's web ending: Signal may show the name of one in the code's
  notes and in `/docs` ("2026-10-25 notes.md") as a tappable link to that web name. The notes show a document's name as
  saved, as for a PDF; in her answers the name is defanged (`notes[.]md`) like any name on a web ending.
- The tests set the documents folder's access entries with the current user standing in for her: that `_estia`
  cannot delete or change your files there, and that Finder, Spotlight and Time Machine handle the files she adds
  (hers, readable by you through the inherited entry), is untested on a live Mac.
- Her git trusts your repositories with `-c safe.directory=*` on its own command line (never in a config file), after
  the check of each repository's local git config; `git status` leaves submodules alone, and git 2.44 or later never
  downloads a missing part of a repository. A program of yours that changes a repository's git config between that
  check and her git command could still make git run a program as her: the check narrows that race, never closes it.
- A reminder is delivered at least once: after a crash in the middle of sending, its note is listed again marked "(may be
  a repeat)". A reminder or a wake-up call is not a dependable alarm (one Mac, signal-cli's experimental `startCall`).
- A reminder's text (the user's words) is kept in the database like any message, until it is done or cancelled and 30
  days have passed.
- The checks of a message she starts: the words that ask for a code or a password, and reminder and call claims, are
  checked in all 11 languages; the other word lists (claims of an action, mail and calendar words, guilt) are English,
  and in another language those rely on her rules and the Gemma test set. A link is caught on any ending, but a
  one-letter name with a two-letter ending right before a full stop ("x.co.") reads as an abbreviation. A check-in keeps
  the words of the plan (at most 120 characters) until 30 days after it closes.
- The firewall, stealth mode and the keychain lock are set only by the nuclear option (`scripts/harden.sh --nuclear`):
  without the firewall, a program of yours that listens for other machines can be reached from your network.
- The cloud-model check trusts Ollama's own list, from a program that runs as you (above) or, for her own Ollama, one
  that any program on the Mac can reach (above). When that list cannot be
  read, nothing pauses and the chat goes ahead (with Ollama down, it fails too); the name check at config load still
  holds. The check and the chat are two requests: a name switched in the moment between them is not caught.
- Copies of her data kept aside by the move (her old folders held for the rollback, and `/var/estia/.before-<time>`)
  hold the database as it was then, with the home address if an earlier version kept it (the home-area rows and its
  Directions links). Once the rollback is no longer needed, delete them with
  `/usr/bin/sudo /usr/local/estia/bin/estia remove-old`. A Directions link sent for an address you have since changed in
  the config is not found; `[privacy] keep_days` deletes old messages.
- Only setup's brew commands run with Homebrew's analytics off; brew commands you type keep your own Homebrew setting
  (`brew analytics off` turns it off for all of them).
