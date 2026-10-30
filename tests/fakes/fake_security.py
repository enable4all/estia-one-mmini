"""Stand-in for macOS's /usr/bin/security in tests (setup_keychain): one keychain in a JSON file, locked or not.

Environment (the fake's wrapper sets it):
  FAKE_SECURITY_DIR  folder with calls.jsonl (every start's arguments; never what came on stdin), items.json (the
                     keychain: "service account" -> password), asking.json (the items that trust no program, -T "":
                     every read of them asks in a macOS dialog), asked (a line for each such read: over SSH macOS
                     cannot show the dialog, so the read fails), unlocked (there: the keychain is open), and, set by a
                     test, refuse_unlock (unlock-keychain fails, as a wrong Mac password does), refuse_add (no item is
                     added; with services in it, one a line, only theirs), corrupt (what find-generic-password
                     gives back differs from what was stored), desk (at the Mac's own screen: a write to the
                     locked keychain opens macOS's own box, which the person answers; each one a line in `boxed`)
                     and delete_asks (a delete on the locked keychain opens that box too, as macOS may: S06's desk
                     check; without desk it is left unanswered or cancelled, and the item stays); lock-keychain
                     removes unlocked

security -i reads its commands from stdin, one a line, arguments in double quotes, as macOS's does, and ends with the
status of its last command. As macOS 27's did on a scratch keychain (27 Oct): an item there already is refused
without -U; with -U its password changes but its access stays, and -T then fails (changing an item's access asks in a
dialog); delete-generic-password needs no dialog.
"""

import json
import os
import shlex
import sys
from pathlib import Path


def options(words: list[str]) -> dict[str, str]:
    """security's options as -x value pairs (-U, -w at the end of find-generic-password: no value of their own)."""
    given: dict[str, str] = {}
    at = 0
    while at < len(words):
        word = words[at]
        if word in ("-a", "-s", "-l", "-T") or (word == "-w" and at + 2 < len(words)):
            given[word], at = words[at + 1], at + 2
        else:
            at += 1
    return given


def load(path: Path, empty: object) -> object:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else empty


def add(words: list[str], items: dict[str, str], asking: list[str], folder: Path) -> int:
    """One add-generic-password of security -i; its status."""
    given = options(words[1:])
    key = f"{given['-s']} {given['-a']}"
    refused = folder / "refuse_add"
    if refused.exists() and (not refused.read_text() or given["-s"] in refused.read_text().split()):
        sys.stderr.write("security: SecKeychainItemCreateFromContent: refused.\n")
        return 45
    if key in items and "-U" not in words:
        sys.stderr.write("security: SecKeychainItemCreateFromContent: The specified item already exists.\n")
        return 45
    status = 0
    if key in items:  # -U: the password changes, the access stays (asking for -T, which fails over SSH)
        if "-T" in given and key not in asking:
            sys.stderr.write("security: SecKeychainItemSetAccess: User interaction is not allowed.\n")
            status = 45
    elif given.get("-T") == "":
        asking.append(key)
    items[key] = given["-w"]
    return status


def main() -> int:
    folder = Path(os.environ["FAKE_SECURITY_DIR"])
    args = sys.argv[1:]
    with (folder / "calls.jsonl").open("a", encoding="utf-8") as calls:
        calls.write(json.dumps(args) + "\n")
    store, asks = folder / "items.json", folder / "asking.json"
    items: dict[str, str] = load(store, {})  # type: ignore[assignment]
    asking: list[str] = load(asks, [])  # type: ignore[assignment]
    opened = (folder / "unlocked").exists()
    if args[:1] == ["show-keychain-info"]:
        if not opened:
            sys.stderr.write("security: SecKeychainCopySettings: User interaction is not allowed.\n")
            return 36
        return 0
    if args[:1] == ["unlock-keychain"]:
        if (folder / "refuse_unlock").exists():
            return 51
        (folder / "unlocked").touch()
        return 0
    if args[:1] == ["lock-keychain"]:
        (folder / "unlocked").unlink(missing_ok=True)
        return 0
    if args == ["-i"]:
        status = 0
        for line in sys.stdin:
            words = shlex.split(line)
            if words[:1] != ["add-generic-password"]:
                continue
            if not opened and (folder / "desk").exists():  # macOS's own box, answered with the right password
                with (folder / "boxed").open("a", encoding="utf-8") as boxed:
                    boxed.write("add-generic-password\n")
                (folder / "unlocked").touch()
                opened = True
            if not opened:
                sys.stderr.write("security: SecKeychainItemCreateFromContent: User interaction is not allowed.\n")
                status = 36
                continue
            status = add(words, items, asking, folder)
        store.write_text(json.dumps(items), encoding="utf-8")
        asks.write_text(json.dumps(asking), encoding="utf-8")
        return status
    if args[:1] == ["delete-generic-password"]:
        given = options(args[1:])
        key = f"{given.get('-s')} {given.get('-a')}"
        if key not in items:
            return 44
        if not opened and (folder / "delete_asks").exists():  # macOS's own box at the delete
            if not (folder / "desk").exists():  # left unanswered or cancelled: nothing changes
                sys.stderr.write("security: SecKeychainItemDelete: User interaction is not allowed.\n")
                return 36
            with (folder / "boxed").open("a", encoding="utf-8") as boxed:
                boxed.write("delete-generic-password\n")
            (folder / "unlocked").touch()
        del items[key]
        if key in asking:
            asking.remove(key)
        store.write_text(json.dumps(items), encoding="utf-8")
        asks.write_text(json.dumps(asking), encoding="utf-8")
        return 0
    if args[:1] == ["find-generic-password"]:
        given = options(args[1:])
        key = f"{given.get('-s')} {given.get('-a')}"
        found = items.get(key)
        if found is None:
            return 44
        if "-w" in args:  # the password; without it only the item's attributes, as macOS prints them
            if key in asking:  # macOS asks in a dialog first; over SSH it cannot
                with (folder / "asked").open("a", encoding="utf-8") as asked:
                    asked.write(key + "\n")
                sys.stderr.write("security: SecKeychainSearchCopyNext: User interaction is not allowed.\n")
                return 36
            sys.stdout.write(found + ("x" if (folder / "corrupt").exists() else "") + "\n")
        else:
            sys.stdout.write(f'keychain: "login.keychain-db"\nattributes:\n    "svce"<blob>="{given.get("-s")}"\n')
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
