# Changelog

Every release of Estia, newest first. The form is [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html), except that a release between 0.9.8 and 0.9.9 carries
a fourth number: 0.9.8.3. From 0.9.4 on, a release's page on GitHub holds its section here, word for word, under the
tagline and above what is in 0.9 (`.github/release-page.md`). The pages of 0.9.1 to 0.9.3 were written on GitHub and say
more (security, requirements, install, tests); 0.9.0 has no release page.

## [Unreleased]

## [0.9.8.3] - 2026-11-05

### Added

- **Deep Thinking:** `/thinking on` makes her think before she answers your texts and voice notes; off by itself after
  30 minutes without a message from you. One switch per allowed account; only on Macs with 24 GB of memory or more.
- **A video found by name:** asked for a video by name, she texts one match with its link; it is read only after
  your typed YES.
- **A text after each call she answers gives its start and end time** on all your devices, also after a call from a
  device you do not know; the call summary names them in place of its minutes.
- **Doctor names her Python and its OpenSSL,** which encrypts her connections to mail, calendars, maps and the web, and
  how a newer one comes.
- **Project folders are listed at every level:** names only, nothing opened; only names git tracks, never a
  secret-like name such as `.env`.

### Changed

- **Pocket TTS is her only voice:** Kokoro and Supertonic-3 are removed and no longer downloaded or loaded; the voice
  install, its log and the docs no longer warn about macOS 27.0.x, which was about Kokoro.
- **A line Pocket TTS cannot say is said by no other voice:** a voice note's answer comes as text alone; on a call the
  line is left unsaid, the call goes on, and the rest of that answer comes as a text.
- **Korean, Polish, Russian and Japanese have no voice:** answers come as text, and calls in them are not picked up:
  the call gets a text, at most once in 10 minutes; during language practice in one of them, the text names
  /babble off.
- **The four with no voice are named:** language lists mark them "(text only: no voice)"; setup, the welcome, /voice,
  call reminders, /babble and doctor (during practice) say so; no call is offered in them, and an answer that would be
  spoken gets a note once.
- **A call is answered while her voice is not working:** a note says so and her answers come as texts until the call
  ends; with her speech recognition not ready either, the call is not answered and a text says why.
- **/status, the voice install and setup name her voice's state:** loading, resting after failed runs (tried again on
  its own) or not working.
- **"My voice is ready" waits for Pocket TTS** and goes to no account in a language with no voice.
- **A language's voice loads as a call rings,** after the voice service restarts, before the greeting; a call she
  places keeps its language.
- **A check-in left unsaid starts no standby:** the next words are answered without her name.
- **Settings from before still load:** a Kokoro voice as heart or michael, `language_voice = ""` as jane; `speed` is
  no longer read.
- **The held-device note names when Signal received the message,** by Signal's own clock, and says how to remove
  a device that is not yours.

### Fixed

- **A device question never heard on a call is dropped:** her voice could not say it, or it came as a text; a yes said
  on the call never answers it, and a text says the device was not switched, and why.
- **No goodbye before a device question on a call:** with a light or plug waiting for your yes, her goodbye is left out
  of what you hear, and the call stays open for the yes.
- **Her own reasoning is never said as an answer:** when the model writes its notes about the bridge's tool results and
  instructions where the answer belongs, you get the device question, or a request to say it again, instead.
- **Python on the firewall's list is caught whatever its letter case:** an allow for macOS's framework Python
  ("Python") no longer passes as the nuclear level, and `scripts/harden.sh --nuclear` offers to take it off.
- **Doctor never looks all clear about listeners it cannot see:** where macOS's list of network connections shows none
  (seen on macOS 27.0.1), or the firewall's list cannot be read, doctor's firewall and Ollama lines say what was not
  checked.
- **The nuclear level's firewall question no longer says nothing listens:** it names your own listening programs and
  says other accounts' are not listed; paths from the firewall's list are shown without control characters.
- **More goodbyes end a call after outside text** (a page, mail or the calendar read on it): "Na razie, dzięki!",
  "じゃあ、またあとで", "Gracias, buenas noches.", "Ну пока!", "That is enough for today."
- **Doctor warns on a macOS before 27:** an install made before setup refused one showed a green Mac line.
- **Setup names the lid only for a MacBook:** "Keep the Mac plugged in (a MacBook with its lid open)."
- **An expired SMS code stops setup with the register-again steps,** where it asked for the code again.
- **A folder macOS blocks names the right setting:** a refused Documents, Desktop or Downloads says System Settings,
  Privacy and Security, Files and Folders.
- **Stop here, or a Ctrl-C, ends the setup window's wait for her voice** after its next look, not up to about 20
  minutes later.
- **The phone steps of the Signal route without a number come after the plan,** not the moment the route is picked.

### Security

- **Only your phone allows or forgets a linked device:** `/devices ok N` and `/devices forget N` from any other
  device are refused.
- **Your phone ends a lock sent from any device of your account,** 10 minutes later at the earliest, so a lock sent
  from a lost laptop ends without admin rights on the Mac. Besides your phone, only the device that sent `/lock` ends
  it.
- **A paragraph of hers that starts like code's note** on a held device or a call is dropped before it is sent;
  in a call summary, also a line that does.
- **A call limit, named in SECURITY.md:** on a laptop's speakers, her own voice that is not told apart counts as your
  words; untested on Signal Desktop with speakers.
- **Typed words stay typed:** a text or caption that starts with a word in square brackets ("[call] yes", "[todo]") is
  kept with a backslash before the bracket, so code never takes it for words said on a call, a voice note or a photo.
- **A macOS limit, named in SECURITY.md:** on macOS 26 and later, with FileVault and remote login (SSH) on, remote login
  takes passwords after a restart until the disk is unlocked; a right password unlocks the disk but opens no session.

## [0.9.8] - 2026-10-30

No 0.9.7 was published: its work is in 0.9.8.

### Added

- **The Estia Setup window, at the desk:** every step in a window of its own, after her spoken intro, Google Maps alone
  in Terminal; no network, no open port. Over a remote login (SSH) or inside screen, tmux or herdr, setup stays in
  Terminal.
- **The setup window never asks for the Mac password:** macOS's own box does, for admin rights and for a locked login
  keychain; Terminal keeps its own password prompt.
- **Setup started by your AI:** at the Mac's own screen, an AI coding assistant such as Claude Code starts
  `scripts/setup.sh` and follows its step lines; every answer is typed in the Estia Setup window, the Mac password only
  in macOS's own box, and setup shows the AI none of them; closing the window stops setup, every answer kept.
- **Setup asks first, then installs:** every question that needs no code from Signal comes first, and each answer is
  kept (never a captcha link, code, PIN, key or password), so a run that stops goes on where it was.
- **Checked before anything installs:** an admin account, Terminal not opened using Rosetta and 16 GB of memory; the
  free disk right after the questions, before any download.
- **Back and Stop here:** b (or Back) goes back one question until setup starts installing, every answer kept; at
  Signal's questions, q (or Stop here) stops setup, its answers kept.
- **A stop has its own screen:** what was found, why it matters, the fix and the command to run setup again, with Copy
  in the window.
- **Menus in Terminal:** gum 2.0.2, checked by its fingerprint before every run; plain lines with `--plain` or
  VoiceOver. Codes, PINs and keys never go through it.
- **Five dots for her model and Ollama's download:** ○○○○○ to ●●●●●, never a percent; each step says its place.
- **Her own Ollama:** a new install with her own macOS account runs Ollama's official build as `_estia`, from a copy
  only admin rights can change, on port 8151 (never 11434), her model in her own folder; any other Ollama on the Mac
  stays as it is, beside hers. An older install keeps its Ollama as it is.
- **The Ollama app, when chosen:** with the app on the Mac, setup asks first: use it (nothing to install) or the
  background build, recommended on a new install and over a remote login. With the app, she answers only while someone
  is logged in and the Ollama app runs, and her prompts pass through a program your account can change; the choice
  holds after the move into her own macOS account.
- **Four names, or your own:** Estia (recommended), Maya, Melina or Socrates; a name you type asks for a female or male
  voice.
- **Calls first, recommended:** "Set up calls?" comes before the other extras, its yes recommended; a yes installs
  everything calls need.
- **Extra Features as switches:** web search and places and routes on, the others off; each one's guide shows when it is
  picked, and each is set up in turn.
- **Each key's own screen:** 3 to 5 numbered steps, the site's link and Later.
- **The extras' sign-ins on your yes:** mail, Spotify, a Google calendar and lights; Google Maps (gcloud too) in
  Terminal.
- **Web search's keys, pasted once:** typed hidden, tested with one search, kept for her with a copy in your login
  keychain.
- **Your home area, as street and city:** one lookup at OpenStreetMap, shown back for a yes; a house number stays out
  of it, except a number of 4 or 5 digits after the first comma, or one joined to letters there (known gaps).
- **About you, optional:** five short lines in your own words, links, phone numbers and email addresses refused; at her
  first start they become your facts in `/profile`.
- **Setup's last step names her voices and commands,** and how to get them again in Signal: `/voices`, `/tools`,
  `/help`.
- **A Signal account without a number:** a Signal Account (Signal for Android calls it Signal Login, $2.99 or EUR 3.49
  once); setup moves it to the Mac, and `estia signal-login` moves an install with a number to one.
- **The key card:** the Recovery key and the Account ID typed on a screen laid out as Signal shows them, each group
  checked as it is typed; the screen is cleared after.
- **Signal's Recovery key and Account ID: in your login keychain on this Mac.** Every read of them asks first, in a
  macOS dialog, unless that program was given Always Allow there. Over a remote login (SSH), that dialog cannot show, so
  a read there fails: they are read only on the Mac's own screen (Keychain Access, or Screen Sharing). The Account ID
  also stays in setup's answers file, which programs you run can read.
- **Pocket TTS voices, made on the Mac's processor:** heart (the new default), jane, eve, alba and michael, and vera on
  32 GB or more; `/voice <name>` switches at once.
- **Heart, the new default voice, on every Mac:** on a 16 GB or 24 GB Mac it runs on the small model: its questions do
  not rise at the end, and its volume and long answers are not yet tested there.
- **jane in six more languages:** Spanish, Italian, Portuguese, Dutch, German and French, each downloaded only when in
  use; until it is ready, answers come as text.
- **If her voice fails on a call, Kokoro says at most one line of each answer** and the rest comes as a text; the call
  stays up.
- **A call wakes with a word:** after the check-in, her name gets "Yes?" or "I'm here." Measured in English only: the
  name "Estia" said alone woke her 35 times in 72.
- **A text during a call is confirmed at once:** "Got it: I'll answer this after the call.", in the chat and on the
  call.
- **`/tools`:** every tool she has in this install, one line each. Her instructions carry the same list on texts, for
  questions about what she can do; calls leave it out.
- **Places and routes with nothing to install and no account:** from OpenStreetMap; Google Maps, once set up, is asked
  first, and an answer from OpenStreetMap in its place says so.
- **Text files beside PDFs:** `.txt` and `.md` files in `~/Estia-KB` and sent on Signal, up to 1 MB each.
- **`/profile new`:** what she noted in the last quiet time.
- **Doctor names a signal-cli over three months old, and several allowed accounts:** those share one chat history, the
  profile, mail, calendars and documents.

### Changed

- **Estia needs macOS 27 or later:** setup refuses an older macOS at once, before anything is installed.
- **Enter takes the recommended answer, listed first,** wherever setup says it is recommended; the Mac's region picks
  the language. A name that does not fit is refused in one sentence, "1 to 20 letters, spaces or hyphens."
- **A recommendation is a mark on its answer:** a small "Recommended" on the option in the setup window, its reason in
  the small line under it; in Terminal, "Yes (recommended)" in setup's menus or on a line of its own right before the
  question, the security step and the home share too; never a "Recommended:" line.
- **Setup's screens, shorter:** key words in bold; each command is shown before it runs, and s (or Skip) skips it.
- **The smaller model's screen, in fewer words:** what it may do, the safeguards in code, then Gemma 26B.
- **The Signal step asks again instead of stopping:** a number, captcha link, code or PIN that does not fit is refused
  in one sentence; the one-time word needs no Enter and ignores accents and case; her username starts from her name,
  and one Signal does not take for a reason other than "taken" can be tried again.
- **The Signal step says what happened:** Signal's limit with its time, no answer from Signal (only on a network
  failure), Signal's refusal of a code or a Recovery key, or a refused registration, each with its fix; no SMS names
  the way without a number.
- **Calls need no GitHub account:** signal-cli's Java build and its maintainer's key are public downloads, fetched with
  the Mac's own `curl`.
- **A Recovery key pasted with extra characters is read:** spaces and invisible characters are left out; any other
  symbol is refused and named, never guessed.
- **Longer answers on a call:** the full answer to a deep question (the main views, where they clash, then, often, her
  own view), up to 500 tokens, an estimated 2 to 3 minutes of speech, with no question back after it; 300 tokens on a
  Mac under 32 GB, not measured there yet.
- **Her own view on a call:** after the main views of a question people disagree on, the view she finds strongest and
  why: in about 4 of 10 such answers on Gemma 4 26B, on some questions rarely.
- **Estia's mark is Hearth,** in place of the "e.". Her Signal picture is Estia's mark, whatever her name: a silver ring
  around an orange rounded square. The letter pictures are gone.
- **Setup picks a Pocket TTS voice:** heart for a female name, alba for a male one; a config that names `af_heart` keeps
  it.
- **`/voice` answers in plain words,** in any case, names a voice she does not have, and switches a call's set lines at
  once.
- **Her note after learning names up to three notes** she added or updated; an empty `/profile` shows what is worth
  telling her, and the welcome invites "remember that …".
- **Project summaries come from the README,** in plain words, without file names or code unless asked; a README of only
  a title, links or commands counts as none.
- **After the check-in, a "yes" starts nothing.**
- **Brave Search stops at 900 searches a month,** below its $5 monthly credit; a key other programs also use can still
  go over it.
- **"Her own macOS account"** for "her own user" and "her user", everywhere.
- **The nuclear level in one sentence, the same everywhere:** best on a Mac used for her alone; outgoing connections and
  remote login (SSH) are never blocked, nor are signed programs while macOS lets signed software through.
- **The setup guide, one shape per extra:** before you start, the steps with the day they were checked, how you know it
  worked, and the common errors. "Outlook.com: not supported."; forwarding Gmail to a mailbox she reads works.
- **Her log says why an answer was slow,** and how far a missed name was from hers: counts and times, never a word.
- **For contributors:** a stricter real-model test set (10 tries a safety case, all must pass, in both of a release's
  two runs, but 0.9.8 passed one run and its second was stopped; `--compare`;
  `EVAL_TIER=small` for some of a 16 GB Mac's limits), ShellCheck in `scripts/verify.sh`, and tests that never run
  setup's real commands.
- **The first deploy of this update may end with the voice loading line:** the older admin tools cannot read her voice
  any more. `/usr/bin/sudo /usr/local/estia/bin/estia check` shows her voice's real state.

### Fixed

- **Short words on a call are heard:** "yes", "stop" or her name alone, also with an "h" in front ("Hestia"); a bare
  "Bye." ends the call.
- **Only goodbyes end a call:** the caller's own after hers, in every language; never a sentence that starts like her
  closing wish.
- **A pause after "and", "the" or "because" is waited out,** and so is the rest of a cut-in that ends open.
- **Call answers start sooner:** the Sources, device and reminder notes go out beside her answer.
- **A device question is her last sentence on a call, and her hang-up waits for your answer;** one dropped when the
  call goes quiet is said so, and a light switched as a call ends is told.
- **A request that only sounds like a wrap-up no longer ends a call:** "Make sure that all the lights are off" or
  "That's all right."; a wrap-up alone, or with closing words or a thanks, does ("That's all, thanks!", "That'll do.").
- **"I'm here" no longer ends a call in Spanish, Portuguese or Russian:** only her offer to wait does ("Aquí estoy si me
  necesitas.", "Estou aqui se precisar.", "Я всегда на связи.").
- **A call turn that runs out of time is told;** after two in a row, she asks for a text.
- **Her call lines are in the call's language,** said whole, and a cut answer ends at a whole sentence, in Japanese too.
- **A wake-up call's opening goes on over "Hello?",** and one answered over "Good morning!" gets the check-in after a
  silence.
- **Her voice's warm-up runs the whole listening model;** a chord-like letter before a long number never stalls it.
- **A repeat-back asked over her answer reaches her;** a yes over the end of her rundown offer reads the day.
- **A lookup promised and left undone:** she is asked once more to search, else the bridge says "I didn't look that up.
  Ask me again and I'll search." An answer that also asks a question is never caught; on a call, one ready only after 15
  seconds (beside Gemma 4 26B) gets that line without the second try.
- **"Can you call me?" gets a yes** with wake-up calls on.
- **Reminders:** one held by `/lock` or a call says it is late, a cancelled one stays cancelled, one asked twice is set
  once, and one set before the model failed is told.
- **After a photo, a reminder in your own words is set,** never one in words only the photo holds.
- **"Remember that …" of a fact she keeps gets "Already kept #N",** and one that saves nothing gets "Nothing was saved."
  with no "try again".
- **A fact saved earlier in a long chat stays in what she sees.**
- **"I've noted that …" with nothing saved is corrected,** in English so far.
- **Notes from before the update are named once** at the first start.
- **"At 7:30" after "tonight" is no morning.**
- **A long chat fits a 16 GB Mac's model:** the oldest messages leave first, and a long read is cut to fit with her
  told, never lost unseen.
- **The morning digest waits for `/unlock` and a call's end,** until noon.
- **Mail:** mail not sorted yet is counted, archived mail is never called old, and "read 2" never opens a newer mail.
- **"Needs a new sign-in" and "address no longer works" come once,** not again after a restart.
- **Sign-in errors say what to do:** mail's invalid_client names the switch to turn on; a CalDAV calendar's fix names
  where her config is.
- **Doctor names a lost connection to Signal;** it stayed green while she reached nobody.
- **Signal's limits are named and kept:** nothing is sent or tried again before Signal's time, and doctor shows it.
- **A message that could not be sent is tried again,** every minute for 10 minutes, and a wake-up text every 5 minutes
  for an hour.
- **A large PDF from `/docs send` gets time to upload,** and sent PDFs are saved once, also across a restart.
- **A call or `/lock` stops a PDF being read.**
- **Chords from a web page no longer stop her,** and an answer of only "---" or "…" is never sent.
- **A slow site is cut off at 30 s.**
- **One database error no longer restarts her.**
- **`[privacy] keep_days` deletes her own messages too.**
- **She never names a model size the Mac does not run,** and a copy of her own code in the projects folder is read only
  when you name that project.
- **The busy note says what is known:** "Estia is still working on your answer; this one takes a while."
- **The welcome names all that goes out:** links you send and, with mail, mail search, beside web search, places and
  music.
- **Places:** a network error no longer blocks "near me" for the day, places within about 1 km come first, and "home"
  and "near me" work in her 11 languages.
- **A search with a long number is refused only for an IBAN whose check digits add up.**
- **The move into her own macOS account checks what macOS made:** her account and her own group, never your staff group;
  only macOS 27 is tested.
- **A failed move shows her logs before her old setup comes back,** and a second Ctrl-C no longer stops that.
- **A Ctrl-C lets a running step end first:** the security step, the move and an update never leave her stopped.
- **Closing Terminal during setup ends it as Ctrl-C does,** with the command it was running.
- **Setup keeps the Mac awake for the whole run,** the voice and calls builds too.
- **Admin rights that ran out during the questions are asked for again** in the security step and at the Registration
  Lock; they failed after 5 minutes.
- **Updates and the move never leave her half done:** a cut power or connection keeps the move's record, a failed update
  puts the voice service back, and a rollback removes services set up after the move.
- **An update's list of changes says what it leaves out,** and names a downgrade.
- **After `estia stop`, she stays stopped;** a config she cannot start with is named, and `estia restart` refuses it.
- **`estia signal` and the new PIN work in her own macOS account.**
- **Calls run the Java build of signal-cli at Homebrew's version;** an update copies only that build, about 120 MB less
  for each older one.
- **The call helper is built again when Estia changes its sources;** a re-run of the calls step goes on where it
  stopped.
- **On a MacBook, doctor's Power line reads the power adapter and the battery apart,** and says a closed lid without an
  external display still stops her.
- **Kokoro can crash on macOS 27.0.x (an Apple bug):** the voice install, its log and the docs say what to do.
- **Setup's smaller fixes:** "~dev" as the projects folder is refused in one sentence, `./setup.sh` from `scripts/`
  starts again, a yes or no in Greek is taken, a file setup cannot use is a stop with its fix, a full disk says to free
  space (System Settings → General → Storage), and the one-time word is found after another message.

### Security

- **After outside text, a sentence that tells a light or plug change as set for later is left out (English only for now,
  in the wordings SECURITY.md lists):** a planted calendar title can no longer say a lamp is scheduled off, also while
  a change waits for your yes. Only a sentence that ties that same change to your yes, with no time of day, stays;
  never "no need for your confirmation".
- **A light or plug your message does not name waits for your yes after outside text;** while it waits, the bridge's
  question comes last, so a yes answers only the switch.
- **After a page, a mail or a web result, her hang-up needs your own closing:** it counts only when you say goodbye
  ("Danke, tschüss!", "Grazie, ciao!"), take leave, ask to end the call or wrap up in English ("Thanks, that's all.");
  "Okay." or "Thanks." alone never does, and "Ciao!" (also a hello) only after a thanks. Her own goodbye still ends the
  call after 3 seconds of quiet, as on any call but a practice call.
- **After a photo, music and lights wait and say why;** a call or a repeat comes only from your own words.
- **After outside text, nothing changes without your own words:** a profile word alone saves nothing, a later "thanks"
  cannot act on a planted line, and the checks last until `/new`.
- **A sent file's name and a sent audio file are never your words:** no reminder, fact or device change comes from them.
- **A hidden part of your message is never your words:** a spoiler reaches her as "(hidden part left out)".
- **The word checks after outside text read Japanese, Chinese and Korean.**
- **No planted links:** a fact from a page holds only the link you sent, a message she starts never holds a web address
  after a hyphen, and links from outside text arrive defanged in more places.
- **Learning a page saves only that page's facts.**
- **Less goes out after mail or a calendar:** an answer that read the chat history sends nothing to a search engine or
  Google Maps, and after a calendar read only an event's place goes to Google Maps.
- **More house numbers stay out of OpenStreetMap:** "123-H", "12/A", "12bis", "nº12", "A-12 Example Road" and "Example
  Loop No12", in any script, and a floor with its door.
- **After the first comma, a number of 4 or 5 digits stays out of OpenStreetMap** right after a flat's or a building's
  word ("Suite 1200", "Bldg 1234"), in the part right after one that is only a flat's, a building's or a floor's word
  with its number, a letter label or a digit ordinal ("Apt 4B", "3rd Floor", "#4"), or before an English street word
  ("1234 Main St"); any other such number there, or one joined to letters there, goes (known gaps).
- **Captions, documents and project files are cleaned** of hidden turn markers, and a health check's status is shown
  only as a short word.
- **Outside text never skips a track.**
- **`/lock` ends her own calls too,** and stops an answer already on its way.
- **An account taken out of `allowed_acis` is never answered again,** and an account she answers can never be moved in
  as hers.
- **Her log never holds signal-cli's words,** and her tool log never keeps a project file's text.
- **No program from a project's git settings.**
- **Call audio never touches the disk.**
- **Her voice service answers only programs running as her own macOS account.**
- **Pocket TTS's program cannot stop or signal her other programs.**
- **signal-cli's Java build for calls is checked by its fingerprint (sha256),** beside its signature.
- **The tools that run as root stay current and whole,** and calls use root's own copy of Java.
- **The rollback refuses a link that leaves its folder.**
- **The scripts that save keys or run admin commands use only the Mac's own programs.**
- **Doctor and the security step name a shared admin sign-in (`timestamp_type=ppid`).**
- **Known security holes in the Python packages are checked** every week and at every push on GitHub, and before each
  release on the Mac; a release with one is not published.
- **SECURITY.md says what an AI tool that gives its commands a terminal sees:** setup's lines, with her number, the
  one-time word and, without the window, the key card's Account ID and Recovery key; never the captcha link, the SMS
  code, a PIN, a password or a web key.
- **Three more known gaps, in SECURITY.md:** after a crash of the window, Spotify's sign-in can wait until the Mac
  restarts, a typed Recovery key is held by the window's page through Signal's limit and code screens, and where
  macOS's list of network connections shows none, setup and doctor cannot see a program another account runs beside
  her Ollama on the same port.
- **Known gaps, now in SECURITY.md:** an admin sign-in's 5 minutes give every program in that Terminal window root, and
  any voice on a call counts as the caller's.
- **Her own Ollama's known gaps, in SECURITY.md:** any program on the Mac can send it input, and a flaw in Ollama itself
  would run code as her account, with her Signal keys.
- **The smaller model's limits, in setup and SECURITY.md:** on 16 or 24 GB, with Gemma 4 12B, forwarded audio, mail or
  calendar may steer her, and she may say a reminder is set when it is not; in her mail and calendar test (28 Oct)
  nothing was set, saved or switched. Generally, Gemma 26B (32 GB or more) keeps her rules better.

## [0.9.6] - 2026-10-22

### Added

- **An architecture diagram** in the README: what runs on the Mac, and what it talks to.

### Changed

- **She knows the whole chat:** reminders set, the digest and her other notes reach her as the bridge's notes, never as
  her own words; mail, calendar and page text in them stays data, with the same guards.

### Security

- **No outside links in her notes:** the digest, the calendar notes and the other notes leave out links from mail,
  calendars, Spotify, Google Maps, audio files and pages.

## [0.9.5] - 2026-10-19

### Changed

- **New addresses:** the code is at github.com/enable4all/estia-one-mmini and the website at estia.one; the old
  addresses still lead there. Her default name stays Estia.
- **For contributors:** `app.py`'s call, documents, start-up and status sides are files of their own (`call_host.py`,
  `documents_host.py`, `lifecycle.py`, `status_host.py`); `prompts.py` is the package `prompts/`, each area's lines in
  a file of its own, every name still read as `prompts.<name>`; `AGENTS.md` is a short map, and each area's rules,
  commands and known gaps are in `docs/agents/` (Claude Code loads them by the files they cover). No change in what she
  does.
- **Setup's questions explain themselves:** each says what it is for, what yes and no do, and "Recommendation: …"
  before its Enter hint, in plain words, in lines that fit the terminal. Every hidden prompt (the captcha link, the SMS
  code, the PINs) says nothing shows while you type, and the Mac password lines say it is asked again only once 5
  minutes have passed since admin rights were last used (and once more in the security step after it removes a rule
  that skips the password). On 16 or 24 GB the smaller model's question says to use it with caution, and what that
  means for her answers.
- **Enter takes the safe answer:** yes at the Registration Lock, the move into her own user (not over a remote login
  without `screen` or `tmux`, where setup says to answer no), the security step and its offers (SSH with keys only,
  removing your own admin rule that skips your password, her files private, her Registration Lock; another account's
  admin rule, or one for some programs only, keeps Enter as a no) and the question that closes what every account may
  open in your home; never at the nuclear level's settings. The end of input, a Ctrl-C, no terminal and output sent to
  a file are never a yes; every other question keeps Enter as a no.
- **The close question in plain words:** why closing matters (her code never opens those names, but a program that
  took over her account could), what closing changes (her account and the Mac's own accounts that programs run as can
  no longer open them, so a guest file share or a web page served from them stops too) and what it never breaks (you
  keep full access, people who log in on this Mac keep what they can open today, her share and Ollama keep working),
  with the command that runs (`chmod -h o-rwx`; for an `~/opt` that holds Ollama's build, `chmod -h o-r`).
- **The installing AI asks first:** SETUP-WITH-YOUR-AI.md has it show the list of extras (what each does, what leaves
  the Mac and to which service, what it needs) and ask which ones, and ask whether she should see projects and which
  folder in your home holds them; the briefing gives each question's recommendation, and holds the list of extras as
  setup prints it. The extras list in setup says what each needs (an account, a sign-in, a key) and names the service
  each sends to. The security step names the nuclear level in one line, only for deeply technical users
  (`scripts/harden.sh --nuclear`).

### Fixed

- **Setup's smaller frictions:** her number typed with spaces or dashes no longer stops setup; q at the SMS code no
  longer says "Verification failed"; the move's end no longer prints "Her projects folder" with projects off;
  "Install it now?" no longer counts two service files on a new install; the model's size is said one way everywhere
  (7.7 GB, 18.3 GB); README.md says to run setup inside `screen` over a remote login.
- **The security step's Mac password:** asked again right after a rule that skips it is removed, now with a line that
  says why first.
- **The guide on transposing chords:** in her own user, `scripts/setup.sh --extras` prints the line to add to her
  config, `[chords]`, and the restart; it writes nothing (her config is hers alone). The guide said setup adds it.

### Security

- **What every account may open in your home, closed with one yes:** both share scripts (`scripts/share-projects.sh`,
  `scripts/share-documents.sh`) name it as before, then ask once whether to close it to her account, with no admin
  rights needed (`chmod -h o-rwx`; people who log in on this Mac keep what they can open today). Enter closes it (the
  recommendation); a no or Ctrl-C keeps everything as it is; without a terminal and in a dry run nothing is asked. An
  `~/opt` that holds Ollama's own build keeps only her way through it, which doctor needs to read that build as her
  (`chmod -h o-r`). A name an access rule still opens to her is never called closed.
- **Doctor looks up more names in your home:** the notes and settings of Claude Code, Codex and Gemini CLI
  (`~/.claude`, `~/.claude.json`, `~/.codex`, `~/.gemini`), Ollama's models (`~/.ollama`) and the Rust, Swift and
  matplotlib folders (`~/.cargo`, `~/.rustup`, `~/.swiftpm`, `~/.matplotlib`). Before, an open `~/.claude` went
  unnamed.
- **SSH with keys only is offered only once a key login is set up:** a key in `~/.ssh/authorized_keys` that SSH would
  take (keys on, read from that file, and the file, the file a link there leads to, `~/.ssh` and your home not
  changeable by other accounts). Before, a key SSH ignores still got the offer, and a yes locked every remote login
  out. After the change, the step says how to take it back from the session that is still open.

## [0.9.4] - 2026-10-19

### Added

- **Transposing chords** (an extra, off until set up: `[chords]` in the config): chords transposed or moved for a
  capo, a chord's notes and the likely key of given chords, worked out on the Mac and given in a note from the code,
  not from the AI. On texts and calls. Names as written (A to G, do re mi, German and Dutch names), written back the
  way they came. A song's chords from a web search are worked out the same way, with their Sources message; after a
  call, one message from the code holds the exact chords and the page they came from. A chord that cannot be read is
  named and never guessed. A sentence of hers with a chord or a capo fret that neither the code worked out nor a
  source holds is left out.
  Known gaps, in GUIDE.md: a capo question that names two keys (the recording's and the singer's) may get the
  recording's key as the key to sound in; a fret in words ("the third fret") is not checked.
- **Standby on calls:** after 90 seconds of silence, a check-in that names her ("Take your time, I'm here. Say Estia
  when you need me."), then only a turn with her name is answered. Other talk, a TV or kitchen noise is heard on the
  Mac and dropped: never answered, never kept. Her name ends standby. In standby, "I'm still here." every ten
  minutes. Known limits: a word that sounds like her name ends standby too; in Japanese, Korean and Russian the
  check-in asks to call her by name; not yet tested on a real call.
- **`/profile notes`:** her notes from chats, by heading, with short dates ("16 Oct"; the year only when it is not
  this year); last, the facts of yours moved there to make room.

### Changed

- **Her goodbye ends the call:** her own goodbye ("Bye!", "Talk soon!"), closing wish ("Enjoy your evening!") or offer
  to wait ("I'll be here when you're ready.") ends the call once the caller has been quiet for 3 seconds, whatever the
  caller said before; anything said in those 3 seconds keeps the call open and is answered. Not on a practice call.
  Known limit: her closing wishes in the languages other than English and Dutch are untested by a native speaker.
- **The default style, hearth, is calmer and shorter:** calm and plain, no gushing. The answer first, in one to four
  sentences unless more is asked for; no cheerleading and no recap of what was just said; concrete suggestions over
  hedging. In a brainstorm, she builds on the idea, adds one new angle, then asks one question. A goodbye gets one
  short line, no summary, also on a call. An install with its own soul.md is unchanged.
- **`/profile`:** your facts, with how full your part is in characters and percent, then one line on her notes: how
  many, how full, the 1,500 characters a message carries, and `/profile notes`. `/status` gives the same percent.
- **Silence never ends a call:** the goodbye after ten minutes of silence is gone. A call ends at its length limit (an
  hour by default) or when either side hangs up. `idle_hangup_seconds` is no longer read; a config that sets it keeps
  working.

### Fixed

- **A call ends when the caller asks to end it or leaves:** a request to end the call ("let's end the call", "we can
  stop here", "let's call it a day") or leaving with a reason ("I have something to do", "something came up") ends it
  once her answer is spoken, in each of the 11 languages. Before, such a call stayed open. "Wait" or "one more thing"
  keeps it open, in every call language.
- **Chord charts read aloud right,** on calls and in voice notes: chords with ";", " / ", "…", "‖" or "⏸" between
  them, "C... G..." and chords with only spaces between them are said as a list, in each of the 11 languages; "N.C."
  is "no chord", "x2" "twice" and "%" "repeat" among chords; C-7, Cm(maj7), C7(b9), Cm7(b5) and C(no3) are said in
  full. In tests, 126 of 127 chord readings right, against 106 before. Known limits, in GUIDE.md.
- **Stage directions never spoken:** "(pause)", "(laughs)" or "*smiles*" in her answers is never said on a call or in
  a voice note, and never sent in a text. Bold words, code and a title in brackets ("(Applause) by Lady Gaga") stay.
  Known limit: a capitalised one inside a sentence ("Also (Pause) weiter") is still said.
- **Her notes that come with a message** are matched by shared words, small English words such as "is" or "to" left
  out. Before, such a word alone could bring a note along.
- **A text typed during a practice call is a practice turn,** also when the call ends the moment the text arrives:
  it is answered as practice, and the recap reads it. Before, now and then the recap came first, and the text was
  answered after practice as an ordinary message.
- **The test suite no longer depends on the Mac it runs on** for the shell's locale, where uv was installed, an
  installed Ollama app or Tailscale, and the git version. Before, a few tests failed on such a Mac.

## [0.9.3] - 2026-10-18

### Added

- **A turn that sounds finished ends sooner:** after a short pause (0.6 seconds), Smart Turn, a small model on the
  Mac, hears the last 8 seconds and judges only whether the sentence sounds finished. Sure, the turn ends there;
  fairly sure, after 1.5 seconds; unsure, after the full pause, so there is still time to think. A turn that ends like
  it goes on ("and…", "but", a comma) or with a goodbye always waits the full pause. In tests on 2,640 clips in the 11
  languages, a finished sentence ended about 1.6 seconds sooner. `early_end = false` in `[calls]` turns it off.
- **The other ten languages read aloud as people say them:** times, dates, money, numbers, units and chord names in
  each language's own words: Spanish, Italian, Portuguese and French with do-re-mi chord names ("Fa#m y Sib" as "fa
  sostenido menor y si bemol"), Dutch and German with German letter names ("Hm und C#m" as "H-Moll und Cis-Moll"),
  Polish and Russian in the case the sentence asks for, Japanese and Korean with times, big numbers, phone numbers,
  temperatures and chords in words. Every reading: GUIDE.md, "Read aloud in the other ten languages".
- **Doctor and `/status`:** a "Turn detector" line beside the speech detector's, and a "Model prompt" line.

### Changed

- **Noise in a pause no longer keeps a turn open:** typing, dishes or traffic that goes on count as a pause, judged
  by the speech detector (Silero) on the Mac. In tests a turn ended 1.9 to 4.7 seconds sooner in such noise, and no
  speech was taken for noise, whispers included. A short noise right after the last word and other voices still keep
  it open. `pause = "quiet"` in `[calls]` brings back the old rule.
- **The first answer on a call comes sooner:** while a call rings, the model loads and reads her instructions and the
  conversation so far. On a test call with the model unloaded, the first answer came 11 seconds after the question,
  against 32 seconds before.
- **The full pause is 2.3 seconds** (was 2.8): still time to think. A config that sets `end_of_turn_seconds` under
  `[calls]` keeps its value.
- **Proposals opened by the project:** a feature proposal is opened, and now also updated, as an issue by GitHub
  Actions, so it names the project rather than a person's account.

### Fixed

- **Empty answers:** `local-worker` gets Google's own prompt for Gemma 4 12B and larger (`RENDERER gemma4-large` in
  its Modelfile). Before, Ollama gave it the small models' prompt, and now and then a hidden thought took the whole
  answer: a silence of about ten seconds before a second try. Every request also sends Google's `top_p` of 0.95. The
  second try's answer is checked too, and on a call she says a short acknowledgement while it runs. Each empty answer
  is logged, without words. A `local-worker` made before 0.9.3 keeps the small prompt until it is made again: doctor
  shows "! Model prompt" with the steps (`setup_assistant.md`, 2.2).
- **Single chord letters said as chords:** "E", "D" alone in a run of chords are "E major", "D major", in English.

## [0.9.2] - 2026-10-16

### Added

- **English read aloud as people say it:** on calls and in spoken answers, numbers, money, dates, units, phone
  numbers, mail addresses and chord names are said in words: "€12.99" is "twelve euros and ninety nine cents", "Fri
  16 Oct" "Friday the sixteenth of October", "C G7 Am F" "C, G seven, A minor, F". Before, "2nd" came out "twond"
  and "E7" "Eseven". Written answers show them as they are; the other ten languages are spoken as written. Every
  reading: GUIDE.md, "Read aloud in English".

### Changed

- **Ten short acknowledgements on calls, never on two requests in a row:** once a request of five words or more is
  taken in, one of ten lines such as "Got it.", "On it." or "Sure thing.", none with "okay". A request right after an
  acknowledged one gets none, and a line is never one of the last two said. Before, one of four lines came on every
  such request. The other languages keep their own lines.
- **"Take your time, I'm here." once, then at most every ten minutes:** the first 90 seconds of silence get the line,
  as before; a later silence within ten minutes of it gets none.

### Fixed

- **English hours with am/pm said as times of the day:** "7pm" is "seven in the evening", "12 PM" "noon", "3-5pm"
  "three to five in the afternoon". Before, "7pm" came out "sevenpm".

## [0.9.1] - 2026-10-16

### Fixed

- **English times said as times of the day:** "13:00" is "one in the afternoon", "7:30 PM" "seven thirty in the
  evening", "14:00–15:00" "two to three in the afternoon"; noon and midnight by name. Before, a time came out digit by
  digit. The other ten languages read times as before.
- **The Mac's memory in her health report:** "Memory: 32 GB, pressure normal (23% free), swap (disk space used as
  memory) 2.9 of 4.0 GB used". Before, the line named only swap, and she read it as the Mac's memory.
- **GUIDE.md:** the `/lock` troubleshooting row gives the same 10 minutes as the rest of the guide.

## [0.9.0] - 2026-10-16

The first public release.

### Added

- **Core, works once setup is done:** chat, memory in two parts (yours in every message, notes from your chats when
  they matter), reminders, language practice in eight languages (`/babble`), saved lists, the Mac's health, photos
  (deleted after the first answer), YouTube summaries from captions, and an emergency stop (`/lock`).
- **Opt-in, off until turned on:** messages she starts (at most one a day, never between 22:00 and 08:00) and a
  three-question evening note written by the code.
- **Extras, each off until set up:** calls that can be interrupted, wake-up calls, voice notes transcribed on the Mac,
  calendars (read-only), Microsoft 365 mail (read-only), the 09:15 morning digest, Spotify, Kasa and Philips Hue
  lights, web search, chords, Google places and routes, projects on the Mac, and answers from your PDFs that name the
  document and the page.
- **Two built-in styles:** hearth or playful, picked at setup. Her name is the user's choice; her Signal picture is
  her name's first letter with Estia's orange full stop.

### Security

- Gemma, Google's open model, runs on the Mac through Ollama's official build, checked before it is installed (its
  fingerprint, every program's signature and Apple's notarization). A cloud model is refused. No cloud AI service, no
  account with us, no telemetry.
- Reached only through Signal. No web app and no open port: the bridge listens on nothing.
- Mail and calendars are read, never changed. Each light and plug is `normal`, `ask first` or `never off`; a device
  change after outside text waits for your yes.
- She runs as her own hidden macOS user, `_estia`. Only that account reads the keys.
- Known gaps, in plain words: SECURITY.md, "Known accepted gaps". The biggest: prompt injection is reduced by
  code-level rules, not eliminated; signal-cli is an unofficial Signal client; on 16 or 24 GB the smaller model,
  Gemma 4 12B, failed some of her safety tests.

[Unreleased]: https://github.com/enable4all/estia-one-mmini/compare/v0.9.8.3...HEAD
[0.9.8.3]: https://github.com/enable4all/estia-one-mmini/compare/v0.9.8...v0.9.8.3
[0.9.8]: https://github.com/enable4all/estia-one-mmini/compare/v0.9.6...v0.9.8
[0.9.6]: https://github.com/enable4all/estia-one-mmini/compare/v0.9.5...v0.9.6
[0.9.5]: https://github.com/enable4all/estia-one-mmini/compare/v0.9.4...v0.9.5
[0.9.4]: https://github.com/enable4all/estia-one-mmini/compare/v0.9.3...v0.9.4
[0.9.3]: https://github.com/enable4all/estia-one-mmini/compare/v0.9.2...v0.9.3
[0.9.2]: https://github.com/enable4all/estia-one-mmini/compare/v0.9.1...v0.9.2
[0.9.1]: https://github.com/enable4all/estia-one-mmini/compare/v0.9.0...v0.9.1
[0.9.0]: https://github.com/enable4all/estia-one-mmini/releases/tag/v0.9.0
