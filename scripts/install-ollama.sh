#!/bin/bash
# Ollama's official build: setup's Ollama step on its own. An official Ollama that answers is kept; else the pinned
# release is downloaded from GitHub, checked before anything is installed (its sha256, every program's signature by
# Ollama's developer ID, Apple's notarization, both JSON-schema libraries), and run as a background service from the
# Mac's start (sudo asks for your Mac password once). With her own macOS account (_estia) there already: her own
# Ollama, root's copy in /usr/local/estia/ollama, run as her on 127.0.0.1:8151 (it never makes her account). Else
# ~/opt/ollama, run as you on 127.0.0.1:11434; Homebrew's Ollama and the Ollama app are named with their fix, never
# used.
#   scripts/install-ollama.sh            check, then install what is missing
#   scripts/install-ollama.sh --dry-run  read and show every step; install and start nothing
set -euo pipefail
PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"  # the Mac's own tools first, then your PATH's (uv, Homebrew's)
repo="$(cd "$(dirname "$0")/.." && pwd)"
python="$repo/.venv/bin/python"
[[ $EUID -ne 0 ]] || { echo "run it as yourself: it asks for sudo itself, only for the background service" >&2; exit 2; }
[[ -x "$python" ]] || { echo "no .venv in $repo: run uv sync --frozen first" >&2; exit 1; }
exec "$python" -I -m local_assistant.ollama_build "$@"
