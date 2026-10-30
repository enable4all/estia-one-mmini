---
paths:
  - "tests/**"
---

# Tests

## Layout

- Tests in `tests/` (fakes for signal-cli and Gemma in `tests/fakes/`; the chords rule book's rows, named by rule, in `tests/test_theory.py`); real-model test set in `evals/gemma_suite.py`.

## Commands

- Hermetic suite: `env -u LANG uv run pytest -q` and `LANG=C uv run pytest -q` pass as the plain run does (`tests/test_hermetic.py` runs the helper-environment tests under both, and greps the code for system paths read outside `Host.path()`)
- Before every commit: `scripts/verify.sh` (in AGENTS.md, Commands). Its voice step runs `tools/voice`'s own-account and offline Pocket suites, and its other offline suites that hold a promise (call audio only in memory, the 27.0.x warning, a request's "kokoro": false), only when `tools/voice` differs from main (the voice package's first build fetches WhisperKit and FluidAudio); the whole `cd tools/voice && swift test` stays a step by hand (`docs/agents/calls.md`).

## Gotchas

- Editing a file twice within a second with the same size can leave stale `.pyc` files: clear `__pycache__` if a test disagrees with the source.
- A test of the owner's shell scripts sets `ESTIA_TEST_ROOT` to a fake tree: they ignore `ESTIA_ROOT` on purpose, and once she is moved the live `/Library/LaunchDaemons` shows her moved: a test without it reads the live system and, where she is moved, runs the real `/usr/bin/sudo` (so `run_script` in `tests/test_scripts.py` always sets one: `/var/empty/estia-test-root`, which can never exist, unless the test names its own). Python tests get their own empty `ESTIA_ROOT` from `tests/conftest.py` (`no_live_system`, which also refuses the live voice service); doctor's "Always on" stat'ed the live service file 117 times before it followed `estia_root()`.
- The setup window's stand-in (`tests/fakes/fake_setup_window.py`) ends itself after 120 s, and `a_window` in `tests/test_setup_window.py` lets setup wait at most 20 s for one window message, then fails the test: a merge that added plan questions left a script's yes or no on a pick, and setup and the stand-in waited on each other for 2 h 44 min (27 Oct). Run tests with a time limit (`timeout 600 uv run pytest …`).
