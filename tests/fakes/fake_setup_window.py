"""Stand-in for the Estia Setup window in tests: speaks the window's protocol on stdin and stdout, as a person at the
desk would answer.

Folder FAKE_WINDOW_DIR (written into its wrapper: the window gets a clean environment):
  calls.jsonl     every start: its arguments and its environment
  received.jsonl  every line setup sent, as it came
  script.json     what to answer, a list taken in turn at each screen setup sends (and at each refusal of an answer):
                    {"answer": value}                   the answer to that screen
                    {"check": value, "answer": value}   a check while typing first, then the answer
                    {"check": value}                    only a check while typing (the next entry answers)
                    {"send": [message, ...]}            these lines as they are
                    {"skip": true}                      nothing (a refusal setup shows before an answer: the reason
                                                          a card comes back with)
                    {"close": true} / {"stop": true}    the window closed / stopped; {"exit": true}: gone, no word
                    {"back": true}                      Back: one question back
                  once it runs out, each screen is answered as Enter would: the recommended or first option, the
                  default yes or no, the switches that are on, empty fields, empty text
  silent          when this file exists, the window never says it is ready
It never outlives its test: gone after LIFETIME_SECONDS, whatever it waits on (setup then reads its end).
"""

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

LIFETIME_SECONDS = 120.0  # every test's window is done in seconds


def enter(screen: dict[str, Any]) -> object:
    """What Enter answers on this screen."""
    options = [option for option in screen.get("options", []) if isinstance(option, dict)]
    kind = screen.get("kind")
    if kind == "yes_no":
        return screen.get("recommended") == "yes"
    if kind in ("choice", "cards", "folder"):
        recommended = screen.get("recommended")
        return recommended if isinstance(recommended, str) else (options[0]["id"] if options else "")
    if kind in ("switches", "kept"):
        return [option["id"] for option in options if option.get("on")]
    if kind == "fields":
        return {}
    return ""


def main() -> int:
    watchdog = threading.Timer(LIFETIME_SECONDS, os._exit, args=(3,))
    watchdog.daemon = True
    watchdog.start()
    folder = Path(os.environ["FAKE_WINDOW_DIR"])
    with (folder / "calls.jsonl").open("a", encoding="utf-8") as calls:
        calls.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ)}) + "\n")
    script_path = folder / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8")) if script_path.exists() else []
    screen: dict[str, Any] = {}

    def write(message: dict[str, object]) -> None:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    def turn() -> bool:
        """The next entry of the script for the screen shown; False: the window is gone."""
        entry: dict[str, Any] = script.pop(0) if script else {"answer": enter(screen)}
        if entry.get("exit"):
            return False
        if entry.get("skip"):
            return True
        if entry.get("back"):
            write({"t": "back"})
            return True
        if entry.get("close") or entry.get("stop"):
            write({"t": "closed" if entry.get("close") else "stop"})
            return not entry.get("close")
        for message in entry.get("send", []):
            write(message)
        if "check" in entry:
            write({"t": "check", "id": screen["id"], "value": entry["check"]})
            if "answer" not in entry:
                return turn()
        if "answer" in entry:
            write({"t": "answer", "id": screen["id"], "value": entry["answer"]})
        return True

    for line in sys.stdin:
        with (folder / "received.jsonl").open("a", encoding="utf-8") as received:
            received.write(line)
        message = json.loads(line)
        if message["t"] == "hello":
            if not (folder / "silent").exists():
                write({"t": "ready", "v": 2})
        elif message["t"] == "screen":
            screen = message
            if not turn():
                return 0
        elif message["t"] == "checked" and not message["live"] and not message["ok"] and screen and not turn():
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
