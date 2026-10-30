"""Stand-in for gum 2.0.2 in tests (setup's menus): records each start and answers as a person would.

Environment:
  FAKE_GUM_DIR      folder with calls.jsonl (every start: its arguments and its environment) and answers.json (a list
                    of {"out": text, "code": exit code}, taken in turn; once they run out, each start answers as Enter:
                    choose the first option, confirm its --default, input nothing)

Only what gum puts on stdout is printed (gum draws its menu on the terminal itself, stderr); the exit codes are gum's:
0 picked or yes, 1 no or Esc, 130 Ctrl-C.
"""

import json
import os
import sys
from pathlib import Path


def main() -> int:
    folder = Path(os.environ["FAKE_GUM_DIR"])
    args = sys.argv[1:]
    with (folder / "calls.jsonl").open("a", encoding="utf-8") as calls:
        calls.write(json.dumps({"argv": args, "env": dict(os.environ)}) + "\n")
    script = folder / "answers.json"
    answers = json.loads(script.read_text(encoding="utf-8")) if script.exists() else []
    if answers:
        answer = answers.pop(0)
        script.write_text(json.dumps(answers), encoding="utf-8")
        sys.stdout.write(answer.get("out", ""))
        return int(answer.get("code", 0))
    command = args[0] if args else ""
    if command == "choose":
        options = args[args.index("--") + 1 :] if "--" in args else []
        sys.stdout.write((options[0] + "\n") if options else "")
        return 0
    if command == "confirm":
        return 1 if "--default=false" in args else 0
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
