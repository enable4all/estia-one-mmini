#!/usr/bin/env bash
# Privacy check, lint, format check, strict types, unit tests (the voice service's own-account and offline Pocket suites
# when tools/voice changed): run before every commit.
set -euo pipefail
cd "$(dirname "$0")/.."
# no private files or values in tracked files (also checks against your own config, if there is one)
uv run python scripts/privacy_scan.py
# the ShellCheck step: begin
# every shell script git tracks (a .sh name, or a first line that starts sh or bash), following what each sources;
# without ShellCheck (a contributor's Mac) one line says so and the rest goes on
if command -v shellcheck >/dev/null 2>&1; then
  scripts=()
  while IFS= read -r -d '' file; do
    if [[ "$file" == *.sh ]] || { [[ -f "$file" && ! -L "$file" ]] && head -n 1 "$file" | grep -Eq '^#!([^[:space:]]*/|[^[:space:]]*env )(ba)?sh([[:space:]]|$)'; }; then
      scripts+=("$file")
    fi
  done < <(git ls-files -z)
  shellcheck -x -P SCRIPTDIR "${scripts[@]}"
else
  echo "verify: shellcheck is not installed, so the shell scripts are not checked (brew install shellcheck)" >&2
fi
# the ShellCheck step: end
# no known security hole in the locked packages, offline (one "not checked" line without osv-scanner or its database;
# the release steps refresh that database first: docs/agents/docs-release.md)
scripts/vuln-scan.sh --if-ready
uv run ruff check .
uv run ruff format --check .
uv run mypy
# the documents helper (Swift, Apple's frameworks only) before the tests: the end-to-end test reads PDFs with it
# (tests/test_documents_e2e.py), never skipped here
if [[ -f tools/documents/Package.swift ]]; then
  (cd tools/documents && swift build -c release --quiet)
  export DOCUMENTS_HELPER_REQUIRED=1
fi
uv run pytest -q
# the device helper has its own environment (python-kasa): its tests and strict types
if [[ -f tools/tapo/pyproject.toml ]]; then
  (cd tools/tapo && uv run --frozen -q pytest -q && uv run --frozen -q mypy)
fi
# the calendar helper too (icalendar, recurring-ical-events)
if [[ -f tools/ical/pyproject.toml ]]; then
  (cd tools/ical && uv run --frozen -q pytest -q && uv run --frozen -q mypy)
fi
# and the YouTube helper (youtube-transcript-api, requests)
if [[ -f tools/youtube/pyproject.toml ]]; then
  (cd tools/youtube && uv run --frozen -q pytest -q && uv run --frozen -q mypy)
fi
# and the documents helper's own tests (Swift Testing, offline; a new build's first scan waits for Vision's model),
# one at a time as the helper reads: tests reading scans at once deadlocked a 4-core Mac in Vision (16 Oct)
if [[ -f tools/documents/Package.swift ]]; then
  (cd tools/documents && swift test --quiet --no-parallel)
fi
# the setup window (Swift, Apple's frameworks only; setup builds it from the checkout at the desk, never into her
# release): its release build and its own tests (offline, no window), then setup's own build of it with this Mac's swift
if [[ -f tools/setup-window/Package.swift ]]; then
  (cd tools/setup-window && swift build -c release --quiet && swift test --quiet)
  SETUP_WINDOW_REAL=1 uv run pytest -q tests/test_setup_window.py -k real_build
fi
# the voice step: begin
# her voice service's own-account check (VoiceCore PeerCheck and the service's refusals), Pocket TTS's offline
# suites (its pins, its sandbox, its runs, its six languages; RealPocket needs the network) and the other offline ones
# that hold a promise (call audio only in memory, the 27.0.x warning, a request's "kokoro": false, the service's
# arguments; \.Speech/ is VoiceCore's Speech suite alone, never SpeechDetection's real model): whenever tools/voice
# differs from main, committed or not (the voice package's first build fetches WhisperKit and FluidAudio: minutes)
voice_base="$(git merge-base HEAD main 2>/dev/null || git rev-parse HEAD)"
if [[ -f tools/voice/Package.swift ]] && ! { git diff --quiet "$voice_base" -- tools/voice && [[ -z "$(git ls-files --others --exclude-standard -- tools/voice)" ]]; }; then
  (cd tools/voice && swift test --quiet --filter 'PeerChecking|PeerLookup|RefusalLogging|OnlyItsOwnAccount|PocketPins|PocketRunning|PocketPrograms|PocketRouting|PocketAudio|PocketPacing|PocketSpeaking|PocketLanguageRouting|PocketLanguagePins|PocketLanguagePacing|CallAudioInMemory|KokoroOnThisMac|TranscriptionRequests|HTTPParsing|ServiceArgumentsParsing|\.Speech/')
fi
# the voice step: end
