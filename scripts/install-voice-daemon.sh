#!/bin/bash
# Voice, run by the user (asks for sudo): /usr/bin/sudo /usr/local/estia/bin/estia-deploy --voice builds the voice service
# (tools/voice) as you from the commit's own sources, puts it into her release and installs and starts it as an
# always-on LaunchDaemon (runs as her own user _estia, 127.0.0.1:8150). Her own user comes first: scripts/install-daemon.sh.
# Later updates need no extra step: each deploy builds it again when the commit's tools/voice changed.
# The first start downloads the models (~1.7 GB; the move into her own user copies them when they are there) and the
# Neural Engine compiles them (up to ~15 minutes; again after a macOS update): /health says "loading" until then. Her voice
# service answers only programs running as her own macOS account: the wait reads it as her (one more admin command).
# Then turn voice on in her config (take the # off [voice] and its lines):
#   /usr/bin/sudo /usr/local/estia/bin/estia config   (nano as her; Ctrl-O, Enter saves; Ctrl-X quits)
# and restart the bridge: /usr/bin/sudo /usr/local/estia/bin/estia restart
# Undo: /usr/bin/sudo /usr/local/estia/bin/estia stop voice
#       /usr/bin/sudo /bin/rm /Library/LaunchDaemons/com.local-assistant.voice.plist
set -euo pipefail
PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"  # the Mac's own tools first, then your PATH's (uv, Homebrew's)
repo="$(cd "$(dirname "$0")/.." && pwd)"
[[ "$(id -u)" != 0 ]] || { echo "run as your normal user, not root (the script calls sudo itself)" >&2; exit 1; }
source "$(dirname "$0")/own-user.sh"  # moved, and her commands' fixed paths ($estia, $estia_deploy)
moved || { echo "her own macOS account comes first: scripts/install-daemon.sh" >&2; exit 1; }
"$sudo" "$estia_deploy" --voice "$repo"
echo "waiting for the models (first start: download and Neural Engine compile, up to ~15 minutes)"
# her voice service answers only programs running as her own macOS account: the wait reads its health as her, in one
# admin command (after a long build sudo may ask for your Mac password again), with her PATH of root-owned folders;
# one line; setup's window runs it with its input read between looks in the place of the sleep
# (ownuser.VOICE_WAIT_AS_HER_WINDOW, review W19; a test keeps the rest the same)
# shellcheck disable=SC2016  # expanded by her shell, not this one
wait_as_her='state=""; for _ in $(seq 1 240); do state="$(curl -q -s --max-time 2 --max-filesize 10000 http://127.0.0.1:8150/health || true)"; if [[ "$state" == *"\"stt\":\"ready\""* && "$state" == *"\"tts\":\"ready\""* || "$state" == *failed* ]]; then break; fi; sleep 5; done; printf "%s" "$state"'
# the wait always ends with its printf: a failure is the admin command's own (a wrong password three times)
if ! state="$(cd / && "$sudo" -u _estia -H -- /usr/bin/env -i HOME=/var/estia PATH=/usr/bin:/bin /bin/bash -c "$wait_as_her")"; then
  echo "voice: not read as her (admin rights refused); /usr/bin/sudo /usr/local/estia/bin/estia check shows it" >&2
  exit 1
fi
# printable characters only: while her voice service is down, another program may answer at its address
state="${state//[^[:print:]]/?}"
if [[ "$state" == *'"stt":"ready"'* && "$state" == *'"tts":"ready"'* ]]; then echo "voice: ready"; exit 0; fi
if [[ "$state" == *failed* ]]; then
  echo "voice: failed (${state}); see its log: /usr/bin/sudo /usr/local/estia/bin/estia logs voice" >&2
  exit 1
fi
# Pocket TTS resting after failed or late runs (a busy Mac) is tried again on its own: no failure, exit 3 as loading
# (prompts.SETUP_VOICE_RESTING, the same words; the review of 4 Nov)
if [[ "$state" == *'"tts":"resting"'* ]]; then
  echo "voice: resting after failed runs (${state}). It is tried again on its own, and calls work once her voice speaks." >&2
  echo "Check later: /usr/bin/sudo /usr/local/estia/bin/estia check (log: /usr/bin/sudo /usr/local/estia/bin/estia logs voice)" >&2
  exit 3
fi
# still loading is no fault (a slow line, a slow Mac): exit 3, so setup's calls step goes on with the call builds
echo "voice: still loading after 20 minutes (${state:-no answer}). On a slow line the first download takes longer:" >&2
echo "the service keeps going, and calls work once her voice is ready. Check later: /usr/bin/sudo /usr/local/estia/bin/estia check (log: /usr/bin/sudo /usr/local/estia/bin/estia logs voice)" >&2
exit 3
