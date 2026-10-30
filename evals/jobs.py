"""Whether an evals job may use the model and the voice service now: another evals job or a call on this Mac.

Both are shared with her live install. An eval during a call takes Ollama's cached prompt from the call (her answers
there then take about 20 s), the voice service has one engine queue, and a job's own timings come from a shared GPU.
So the Gemma test set and the wake harness refuse to start beside another evals job or during a call, wait between
tries while a call is on, and keep in their result files what ran beside them; `--force` runs anyway (review E6,
0.9.7: the Gemma test set started beside other eval jobs and during a live call).
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
from collections.abc import Awaitable, Callable, Collection

# an evals module that runs: uv or Python itself is the program, "-m evals.<module>" among its arguments; never a
# shell that waits for one, a log read or a search that only names it (`pgrep -f` reads the same pattern, an ERE)
JOB = re.compile(r"^([^ ]*/)?(uv|[Pp]ython[0-9.]*)( [^ ]+)* -m evals\.")
CALL_HELPER = "signal-call-tunnel"  # the call helper signal-cli starts for each call (tools/call-tunnel)
WAIT_S = 5.0  # between checks while a call is on


def listing(pattern: str) -> str:
    """This Mac's processes whose command line matches `pattern` (`pgrep -fl`: every user's)."""
    return subprocess.run(["/usr/bin/pgrep", "-fl", pattern], capture_output=True, text=True, check=False).stdout


def call_running(found: str) -> bool:
    """Whether a `pgrep -fl` listing shows a call on this Mac: its call helper runs."""
    return any(CALL_HELPER in line.partition(" ")[2] for line in found.splitlines())


def other_jobs(found: str, *, own: Collection[int]) -> list[str]:
    """The pids of other evals jobs in a `pgrep -fl` listing (pid, then the command line), never this run's own."""
    pids = []
    for line in found.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid.isdigit() and int(pid) not in own and JOB.search(command):
            pids.append(pid)
    return pids


def _parent(pid: int) -> int:
    done = subprocess.run(["/bin/ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, check=False)
    text = done.stdout.strip()
    return int(text) if text.isdigit() else 0


def ancestors(pid: int, *, parent_of: Callable[[int], int] = _parent) -> set[int]:
    """`pid` and every process it was started by, up to launchd: the shell or uv that started a job names it in its own
    command line too (`bash -c "uv run python -m evals.gemma_suite"`), and is no other job."""
    found: set[int] = set()
    while pid > 0 and pid not in found:
        found.add(pid)
        pid = parent_of(pid)
    return found


class Watch:
    """What runs beside one job's tries: `refusal` before the first, then `before` and `after` each try (a call on: it
    waits before the try unless `force`). `look` lists processes as `listing` does; `own`: this job's processes."""

    def __init__(
        self,
        *,
        own: Collection[int],
        force: bool = False,
        look: Callable[[str], str] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.force = force
        self._own = own
        self._look = look or listing
        self._sleep = sleep
        self.waited_s = 0.0
        self.with_a_call = 0  # tries with a call on before or after them
        self.beside_jobs = 0  # tries with another evals job running before or after them

    def now(self) -> list[str]:
        """What runs beside this job now: "a call", "evals job <pid>"."""
        found = ["a call"] if call_running(self._look(CALL_HELPER)) else []
        return found + [f"evals job {pid}" for pid in other_jobs(self._look(JOB.pattern), own=self._own)]

    def refusal(self) -> str:
        """Why this job may not start now ("" when it may, or when forced)."""
        if self.force:
            return ""
        if jobs := other_jobs(self._look(JOB.pattern), own=self._own):
            return f"another evals job runs (pid {', '.join(jobs)}); wait for it, or --force"
        if call_running(self._look(CALL_HELPER)):
            return "a call is on: wait until it ends, or --force"
        return ""

    async def before(self) -> list[str]:
        """Before a try: while a call is on (and not forced), wait; then what runs beside it."""
        told = False
        while not self.force and call_running(self._look(CALL_HELPER)):
            if not told:
                print("a call is on: waiting until it ends (--force to go on)", file=sys.stderr)
                told = True
            await self._sleep(WAIT_S)
            self.waited_s += WAIT_S
        return self.now()

    def after(self, before: list[str]) -> list[str]:
        """After a try: what ran beside it, before or after (counted for the result file)."""
        beside = sorted(set(before) | set(self.now()))
        self.with_a_call += "a call" in beside
        self.beside_jobs += any(item.startswith("evals job") for item in beside)
        return beside

    def record(self) -> dict[str, object]:
        """For the result file's run settings."""
        return {
            "forced": self.force,
            "tries with a call": self.with_a_call,
            "tries beside another evals job": self.beside_jobs,
            "waited for a call (s)": self.waited_s,
        }
