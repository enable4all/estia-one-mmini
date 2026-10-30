#!/bin/bash
# Set Estia up step by step: tools, Ollama's official build, the model for this Mac, her Signal number, who may talk
# to her, the config, always on, then the extras you pick. Safe to run again: done steps are skipped. --dry-run prints
# without doing. Ollama is never Homebrew's (it lacks the MLX engine's JSON-schema library): setup's Python step keeps
# the official build that answers, or installs it, checked, as a background service.
# No key or password passes through this script itself: setup's Python step (started with Homebrew's uv) reads
# Signal's codes and the web keys hidden and writes the keychain copies with /usr/bin/security.
# Setup's questions come first, before anything is installed, each answer saved at once in
# ~/.config/estia-setup/answers.toml (never a code, PIN or key), so a run that stops goes on where it was.
# In your own Terminal window, or started by your AI at the Mac itself (no terminal): every answer is then typed in
# the Estia Setup window, and nothing here ever reads one (no `read` without a terminal).
set -euo pipefail
export HOMEBREW_NO_ANALYTICS=1  # Homebrew sends usage data unless told not to: never from this setup
[[ $EUID -ne 0 ]] || { echo "Run setup as yourself: it asks for admin rights when it needs them." >&2; exit 2; }
cd "$(dirname "$0")/.."
if [[ "$(uname -s)/$(uname -m)" != "Darwin/arm64" ]]; then
  if [[ "$(sysctl -in sysctl.proc_translated 2>/dev/null || true)" == 1 ]]; then  # Apple silicon, Terminal under Rosetta
    echo 'Terminal runs under Rosetta here. Turn off "Open using Rosetta" (Finder: Applications → Utilities, Terminal, Get Info), then quit Terminal, open it again and run setup.' >&2
    exit 1
  fi
  echo "Estia needs a Mac with Apple silicon (M1 or later)." >&2
  exit 1
fi
# 16 GB of memory or more (setup.choose_model: 15 GiB as macOS counts it), said before Homebrew and the tools install
memory="$(sysctl -n hw.memsize 2>/dev/null || true)"
if [[ "$memory" =~ ^[0-9]+$ ]] && ((memory < 15 * 1073741824)); then
  echo "✗ This Mac has $((memory / 1073741824)) GB of memory. Estia needs 16 GB or more." >&2
  exit 1
fi
# macOS 27 or later only (owner, 29 Oct), refused before anything is installed or downloaded
macos="$(sw_vers -productVersion 2>/dev/null || true)"; macos="${macos%%.*}"
if [[ ! "$macos" =~ ^[0-9]+$ ]] || ((macos < 27)); then
  echo "Estia needs macOS 27 or later. Update macOS (System Settings → General → Software Update), then run setup again." >&2
  exit 1
fi
# Homebrew, her own account and the security step need admin rights: a standard account is told first
if [[ " $(id -Gn 2>/dev/null || true) " != *" admin "* ]]; then
  echo "Setup needs an admin account. Log in as an admin, or have an admin make this account one (System Settings → Users and Groups), then run setup again." >&2
  exit 1
fi
if [[ ! -e .git ]]; then  # her releases and every update are built from git: a ZIP download can never be installed
  echo "This folder is not a git clone, and her install and every update are built from git. Clone it, then run setup:" >&2
  echo "  git clone https://github.com/enable4all/estia-one-mmini.git ~/estia && cd ~/estia && scripts/setup.sh" >&2
  exit 1
fi
option() {  # the setup option an argument names, as setup's Python step reads it: a unique prefix, or with "=value"
  local word="${1%%=*}" full
  [[ "$word" == --?* ]] || return 0
  for full in --config --extras --dry-run --redo --answers --plain --terminal; do
    if [[ "$full" == "$word"* ]]; then echo "$full"; return 0; fi
  done
}
given=" "; for a in "$@"; do given+="$(option "$a") "; done
dry=""; [[ "$given" == *" --dry-run "* ]] && dry=1
terminal=""; [[ -t 0 ]] && terminal=1
if [[ -z "$dry" && -z "$terminal" ]]; then  # started by an AI's command tool: its window opens only at the desk
  # the same words as setup's Python step (prompts.SETUP_AI_*, in setup_ai.refused's order), said before anything
  # installs: Homebrew's line in your login profile, Homebrew, brew install, uv python install, uv sync. A sandbox and
  # the Mac's own screen are checked by the Python step, after these tools (bash cannot ask macOS)
  for flag in --plain --terminal; do
    if [[ "$given" == *" $flag "* ]]; then
      echo "✗ Setup: $flag asks in Terminal. Fix: run scripts/setup.sh $flag in your own Terminal window." >&2
      exit 2
    fi
  done
  if [[ "$given" == *" --extras "* ]]; then
    echo "✗ Setup: --extras asks in Terminal. Fix: run scripts/setup.sh again (its window lists the Extra Features), or scripts/setup.sh --extras in your own Terminal window." >&2
    exit 2
  fi
  if [[ "$given" == *" --answers "* || "$given" == *" --config "* ]]; then
    echo "✗ Setup: --answers and --config are for a run in your own Terminal window." >&2
    exit 2
  fi
  if [[ -n "${SSH_CONNECTION:-}${SSH_CLIENT:-}${SSH_TTY:-}" ]]; then
    echo "✗ Setup: its window opens only on this Mac's own screen, and this session has none (a remote login, or no one signed in at the Mac). Fix: start it at the Mac itself, or run scripts/setup.sh in your own Terminal window." >&2
    exit 2
  fi
  if [[ -n "${STY:-}${TMUX:-}${HERDR_ENV:-}" ]]; then
    echo "✗ Setup: inside screen, tmux or herdr its window never opens. Fix: start it outside them at the Mac itself, or run scripts/setup.sh in your own Terminal window." >&2
    exit 2
  fi
  # HOME against your account's home folder in the user database, its 9th field (Homebrew's line would go elsewhere)
  entry="$(id -P 2>/dev/null || true)"; own=""
  if [[ "$entry" == *:*:*:*:*:*:*:*:*:* ]]; then own="${entry%:*}"; own="${own##*:}"; fi
  if [[ -n "$own" && "${HOME:-}" != "$own" && "${HOME:-}" != "$own/" ]]; then
    echo "✗ Setup: HOME is not your account's home folder. Fix: run scripts/setup.sh with your own HOME." >&2
    exit 2
  fi
fi
run() { echo "  \$ $*"; [[ -n "$dry" ]] || "$@"; }

homebrew="${ESTIA_TEST_ROOT:-}/opt/homebrew"  # Homebrew's folder on Apple silicon (a test: its fake tree)
profile="$HOME/.zprofile"; [[ "${SHELL:-}" == */bash ]] && profile="$HOME/.bash_profile"
# shellcheck disable=SC2016  # the line written into the login profile as it is: each new shell expands it
line='eval "$(/opt/homebrew/bin/brew shellenv)"'
on_path() {  # Homebrew on PATH for this run, and its line in the login profile for every new Terminal window
  eval "$("$homebrew/bin/brew" shellenv)"
  grep -qs "brew shellenv" "$profile" && return 0  # a line of its own or of Homebrew's: kept as it is
  if [[ -n "$dry" ]]; then
    echo "Homebrew: would add $line to $profile, so new Terminal windows find brew and uv"
  else
    printf '\n%s\n' "$line" >> "$profile"
    echo "✓ Homebrew: added to new Terminal windows ($line in $profile)"
  fi
}
if ! command -v brew >/dev/null && [[ -x "$homebrew/bin/brew" ]]; then on_path; fi  # installed, not on PATH yet
if ! command -v brew >/dev/null && [[ -n "$dry" ]]; then
  echo "Homebrew: would offer to install it (its installer asks for your Mac password once)"
elif ! command -v brew >/dev/null && [[ -z "$terminal" ]]; then  # its own installer asks for sudo in a terminal
  echo "✗ Homebrew: not installed, and its own installer needs a Terminal window. Fix: download Homebrew.pkg from https://github.com/Homebrew/brew/releases/latest and open it (macOS asks for the Mac password in its own box), then run scripts/setup.sh again." >&2
  exit 1
elif ! command -v brew >/dev/null; then
  # short lines, a blank line between the paragraphs and before the recommendation (owner, 27 Oct)
  echo "Homebrew, the usual installer for Mac tools, installs the two Estia needs:"
  echo "signal-cli (for Signal) and uv (for Python)."
  echo
  echo "Its installer asks for your Mac password once (nothing shows while you type)"
  echo "and may ask you to press Return. No stops setup."
  echo
  echo "Yes (recommended)."  # on a line of its own, never inside the question (owner, 27 Oct; its form, 29 Oct)
  read -r -p "Install Homebrew now? [Y/n] " answer || answer=n  # Enter: the recommended yes (owner, 29 Oct)
  [[ -z "$answer" || "$answer" == [yY]* ]] || { echo "Install Homebrew from https://brew.sh, then run this again." >&2; exit 1; }
  installer="$(mktemp)"
  trap 'rm -f "$installer"' EXIT
  url=https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh
  echo "  \$ /bin/bash -c \"\$(curl -fsSL $url)\""  # the one command, never the installer's own text
  curl -fsSL "$url" -o "$installer" \
    || { echo "Could not download Homebrew's installer: check the internet connection, then run setup again." >&2; exit 1; }
  /bin/bash "$installer"
  rm -f "$installer"
  [[ -x "$homebrew/bin/brew" ]] || { echo "Homebrew's installer did not finish: run setup again." >&2; exit 1; }
  on_path
fi
# the signal-cli releases Estia is written for: the one constant doctor and the bridge share (config.SIGNAL_CLI_SERIES)
series="$(/usr/bin/sed -n 's/^SIGNAL_CLI_SERIES = "\([0-9][0-9]*\.[0-9][0-9]*\)".*/\1/p' src/local_assistant/config.py 2>/dev/null || true)"
[[ -n "$series" ]] || { echo "Could not read SIGNAL_CLI_SERIES in src/local_assistant/config.py: is this copy of Estia whole? (git status)" >&2; exit 1; }
missing=()
for tool in signal-cli uv; do command -v "$tool" >/dev/null || missing+=("$tool"); done
if ((${#missing[@]})); then run brew install "${missing[@]}"; fi
if [[ -n "$dry" ]]; then
  echo "  (signal-cli must be $series.x)"
  run uv python install 3.13
  run uv sync --frozen
  if [[ -x .venv/bin/python ]]; then exec .venv/bin/python -I -m local_assistant.setup "$@"; fi
  # 0.9.8's facts (c-8, round 3): a friend's AI briefs from this first; setup's own dry run shows each screen
  echo "First, setup's questions, each answer saved in ~/.config/estia-setup/answers.toml (never a code, PIN or key)."
  echo "Then: her own macOS account and her own Ollama, made first on a yes to always on (any other Ollama on the Mac"
  echo "stays as it is; or the Ollama app, if you pick it), her model for this Mac's memory, her Signal account (a spare"
  echo "SIM's number, or an account without a number), your account (a one-time word you send her), her config, always"
  echo "on, the security step (each change on your yes) and the Extra Features you pick. Admin rights: your Mac password."
  exit 0
fi
# registration and the bridge use signal-cli $series's requests: another version fails later, with an obscure error
version="$(signal-cli --version 2>/dev/null || true)"
if [[ "$version" != "signal-cli $series."* ]]; then
  echo "Estia is written for signal-cli $series.x, and this Mac has ${version:-a signal-cli that gives no version}." >&2
  have="${version#signal-cli }"; have_major="${have%%.*}"; have_minor="${have#*.}"; have_minor="${have_minor%%.*}"
  if [[ "$have_major" =~ ^[0-9]+$ && "$have_minor" =~ ^[0-9]+$ ]] \
    && (( have_major > ${series%%.*} || (have_major == ${series%%.*} && have_minor > ${series#*.}) )); then
    echo "Homebrew's signal-cli is newer than Estia supports: update Estia first (git pull), then run setup again;" >&2
    echo "if it still says this, wait for the next Estia release." >&2
  elif [[ -z "${ESTIA_SETUP_UPGRADED:-}" && -n "$terminal" ]]; then  # no terminal: never a read, the line below
    # the command shown, then run on Enter (owner, 27 Oct: no command typed); setup again after it, once
    echo "  \$ brew upgrade signal-cli"
    read -r -p "Press Enter to run it (or s to skip) " answer || answer=s
    if [[ -z "$answer" || "$answer" == [yY]* ]] && brew upgrade signal-cli; then
      ESTIA_SETUP_UPGRADED=1 exec scripts/setup.sh "$@"  # from the repo's root (the cd above): any $0 works
    fi
    echo "Install signal-cli $series.x (brew upgrade signal-cli), then run setup again." >&2
  else
    echo "Install signal-cli $series.x (brew upgrade signal-cli), then run setup again." >&2
  fi
  exit 1
fi
echo "✓ Tools: signal-cli, uv"
run uv python install 3.13
run uv sync --frozen --quiet
exec uv run --quiet python -I -m local_assistant.setup "$@"  # -I: PYTHONPATH and the like never reach it
