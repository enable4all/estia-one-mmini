"""Admin rights for setup's steps, whichever face asks. In Terminal: today's /usr/bin/sudo, which asks for the Mac
password there. In the Estia Setup window (owner, 29 Oct: "can we have Terminal in the window?"): the window's admin
runner, through macOS's own box ("Estia Setup wants to make changes."): the password goes only to macOS, never
through the page, the window or setup (tools/setup-window, AdminCore).

Every root-touching step keeps one implementation: it names its commands in today's sudo forms, and the face runs
them. `as_root` maps each form to what root runs in the window; any other form is refused, never guessed. Root's
environment and who asked (SUDO_USER) are the window's own, never setup's: the root-run tools find your account as
they do under sudo. A secret goes only on the command's stdin (the window writes it into a 0600 FIFO), never into its
arguments, the box's line, a file or a log.

`WindowHost` is hostsec.Host for a step that runs in-process (the security step): its sudo commands go through the
window, everything else as Host runs it."""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from local_assistant import harden, hostsec, ownuser, prompts
from local_assistant.hostsec import SUDO, Host
from local_assistant.setup_questions import RECOMMENDED, Option, Question, yes_no

if TYPE_CHECKING:
    from local_assistant.setup_window import WindowFace

TRUE = "/usr/bin/true"  # "authorize": what root runs for `sudo -v` (the box when no 5 minutes run)
REFUSED = "admin rights not given"  # a window run's stderr when macOS's box was refused, then Not now: nothing ran
ASK_MARK = "\x1f? "  # a root-run tool's question on a line of its own (estia-deploy --ask-lines): a window screen
ASK_SECONDS = 600.0  # deploy.ASK_SECONDS: no answer by then is its no, and it ends (pinned equal by a test)
ASK_MARGIN = 10.0  # an answer this close to that end is taken as too late: deploy's wait began before the screen
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b[@-Z\\-_]")  # a terminal's colours and cursor moves


# the step questions the window asks where Terminal reads a line (plan=False, keep="step": asked each time, never kept)
QUESTIONS: tuple[Question, ...] = (
    Question(  # Terminal: "Press Enter to run it (or s to skip)" (Wizard.offer)
        "offer_run",
        "yes_no",
        prompts.SETUP_WINDOW_OFFER_TITLE,
        lead=prompts.SETUP_WINDOW_OFFER_LEAD,
        options=(Option("yes", prompts.SETUP_WINDOW_RUN_IT, tag=RECOMMENDED), Option("no", prompts.SETUP_WINDOW_SKIP)),
        recommended="yes",
        check=yes_no,
        keep="step",
        plan=False,
    ),
    Question(  # Terminal: nano as her (estia config); the window: the lines, added as her by herconfig on a yes
        "her_config_add",
        "yes_no",
        prompts.SETUP_WINDOW_HER_CONFIG_TITLE,
        lead=prompts.SETUP_WINDOW_HER_CONFIG_LEAD,
        options=(
            Option("yes", prompts.SETUP_WINDOW_HER_CONFIG_YES, tag=RECOMMENDED),
            Option("no", prompts.SETUP_WINDOW_LATER_BUTTON),
        ),
        recommended="yes",
        check=yes_no,
        keep="step",
        plan=False,
    ),
    Question(  # Terminal: the share's own "[Y/n]" (share.terminal_ask): what every account may open in your home
        "share_close",
        "yes_no",
        prompts.SETUP_WINDOW_SHARE_CLOSE_TITLE,
        lead="{question}",
        more=prompts.SETUP_WINDOW_SHARE_CLOSE_MORE,
        options=(
            Option("yes", prompts.SETUP_WINDOW_SHARE_CLOSE_YES, tag=RECOMMENDED),
            Option("no", prompts.SETUP_WINDOW_SHARE_CLOSE_NO),
        ),
        recommended="yes",
        check=yes_no,
        keep="step",
        plan=False,
    ),
)


def offer_words(command: Sequence[str], what: str = "") -> dict[str, str]:
    """The offer's screen for this command: what it does (`what`, round 2: never a bare command), the command, and for
    an admin one where macOS asks."""
    admin = f"\n\n{prompts.SETUP_WINDOW_ADMIN_BOX}" if command and command[0] == SUDO else ""
    return {"what": f"{what}\n\n" if what else "", "command": " ".join(command), "admin": admin}


class Ran(NamedTuple):
    """An admin command's end: its exit code, its output (kept only where the caller reads it) and whether admin
    rights were refused (macOS's box cancelled, or a wrong password three times: nothing ran)."""

    code: int
    output: str
    refused: bool = False


def as_root(argv: Sequence[str], owner: str) -> list[str] | None:
    """What root runs in the window for one of today's sudo forms (`owner`: your account, whose sudo rules root lists):

    /usr/bin/sudo [-n] /abs/program …           /abs/program …
    /usr/bin/sudo -v                            /usr/bin/true (the box, unless its 5 minutes run)
    /usr/bin/sudo -n -v, /usr/bin/sudo -k       nothing: each command asks by itself
    /usr/bin/sudo -n -l                         /usr/bin/sudo -n -l -U <owner> (your rules, listed by root)
    /usr/bin/sudo [-n] -u _estia -H -- /abs …   the same with -n (root needs no password for her)

    Anything else raises ValueError (`sudo -n -k -l`, read-only, stays yours: never admin)."""
    words = list(argv)
    if not words or words[0] != SUDO:
        raise ValueError(f"not a sudo command: {words[:1]}")
    rest = words[1:]
    if rest == ["-v"]:
        return [TRUE]
    if rest in (["-n", "-v"], ["-k"], ["-n", "-k"]):
        return None
    if rest == ["-n", "-l"]:
        return [SUDO, "-n", "-l", "-U", owner]
    if rest[:1] == ["-n"]:
        rest = rest[1:]
    if rest[:4] == ["-u", ownuser.ROLE_USER, "-H", "--"] and len(rest) > 4 and rest[4].startswith("/"):
        return [SUDO, "-n", *rest]
    if rest and rest[0].startswith("/"):
        return rest
    raise ValueError(f"a sudo form the window does not run: {' '.join(words[:4])}")


def stays_yours(argv: Sequence[str]) -> bool:
    """`sudo -n -k -l` (hostsec.owner_sudo): your rules read as you, read-only; never run as root."""
    return list(argv) == [SUDO, "-n", "-k", "-l"]


def shown(line: str) -> str:
    """A line a command printed, as setup shows it: a terminal's colours and cursor moves left out, every other control
    or format character a "?" (hostsec.clean)."""
    return hostsec.clean(_ANSI.sub("", line))


class WindowHost(Host):
    """hostsec.Host for a step that runs in-process while the window is open: its sudo commands go through the window's
    admin runner (macOS's own box), its output read back as Host gives it; `sudo -n -k -l` stays yours, and every
    other command runs as Host runs it. Once the window is gone, Terminal's sudo does as it always did. Off the live
    Mac (a test's root, a rehearsal) nothing runs as root: Host prints it."""

    def __init__(
        self,
        face: WindowFace,
        root: Path = Path("/"),
        *,
        dry_run: bool = False,
        say: Callable[[str], None] = print,
        why: str = "",
    ) -> None:
        super().__init__(root, dry_run=dry_run, say=say)
        self.face = face
        self.why = why  # the box's line for this step

    @contextlib.contextmanager
    def stops_held(self) -> Iterator[None]:
        """harden's held section (her stop, her PIN, her start): hang-ups, TERMs and Ctrl-Cs ignored, as harden.sh
        ignores them, and the window's Stop here waits for its end too (review FRIEND-5)."""
        with ownuser.interruptions_ignored(), self.face.stops_held(prompts.SETUP_WINDOW_LOCK_FINISHES):
            yield

    def window_open(self) -> bool:
        """The window still runs the step: closed, its sudo is Terminal's (harden's words and sign-in too)."""
        return self.face.terminal_face is None

    def her_start(self, bootstrap: Sequence[str]) -> str:
        """Her start for Terminal, as harden names it (HER_NOT_STARTED): her wrapper's in her own macOS account, else
        the bootstrap as typed."""
        if self.face.wizard.own_user():
            return f"{ownuser.ESTIA} start"
        return " ".join(word for word in bootstrap if word != "-n")

    def run(
        self,
        argv: Sequence[str],
        *,
        shown: Sequence[str] | None = None,
        input: str | None = None,
        read_only: bool = False,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        timeout: float = 600.0,
    ) -> tuple[int, str]:
        through_window = (
            bool(argv) and argv[0] == SUDO and not stays_yours(argv) and not self.dry_run and self.root == Path("/")
        )
        if through_window:
            was_open = self.window_open()
            ran = self.face.admin(argv, input=input, capture=True, why=self.why, seconds=timeout)
            if ran is not None:
                if ran.code != 0 and self.face.closed and "bootstrap" in argv:  # an AI-run's window closed meanwhile
                    self.face.unstarted = self.her_start(argv)
                return ran.code, ran.output
            if was_open and not self.window_open() and self.face.holding and list(argv[1:2]) == ["-n"]:
                self.sign_in_terminal()
        return super().run(argv, shown=shown, input=input, read_only=read_only, cwd=cwd, env=env, timeout=timeout)

    def sign_in_terminal(self) -> None:
        """The window closed inside harden's held section (her stop, her PIN, her start): macOS's box gave no sign-in
        to Terminal's sudo, so the rest (`sudo -n`) would fail there and leave her stopped. Terminal's sudo asks for
        the Mac password once, first, in harden's words (review W20); refused, the rest fails as a run, and harden
        names her start."""
        self.say(harden.PASSWORD_HERS if self.face.wizard.own_user() else harden.PASSWORD_YOURS)
        super().run([SUDO, "-v"])
