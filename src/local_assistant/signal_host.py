"""Her Signal account's state, beside the bridge: the allowed accounts whose safety number changed (until a message of
theirs opens, review S5), signal-cli's connection (since when it is not ready, review S2), Signal's wait on her sending
(review S4), and stored messages from an account no longer allowed (review S3). Kept in her state table and status.json
for doctor and /status. Built by App, which keeps the delegates the tests call; every other part of the app is read
through `self.app`."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from local_assistant.signal_io import RateLimitError, SignalError
from local_assistant.store import Message
from local_assistant.timefmt import local_hhmm

if TYPE_CHECKING:
    from local_assistant.app import App

log = logging.getLogger("local_assistant")  # the app's own logger: its lines keep their name in bridge.log

# the allowed accounts whose safety number changed (comma-separated ACIs), until a message of theirs opens: kept beside
# status.json's time, so after a restart another account's message never clears it (review S5)
IDENTITY_KEY = "identity_changed"
# Signal's wait for a captcha request that names none, and the longest wait kept (a larger one is taken as this)
LIMIT_UNNAMED_SECONDS = 3600.0
LIMIT_MAX_SECONDS = 2 * 86400.0


class SignalHost:
    """What the bridge keeps of her Signal account between messages and restarts."""

    def __init__(self, app: App) -> None:
        """Built once status.json is read: the accounts kept in her state table, and signal-cli watched."""
        self.app = app
        self.identity_changed: set[str] = self._identity_changed_at_start()
        app.signal.watch(self.connection)

    def set_aside_if_not_allowed(self, message: Message) -> bool:
        """A stored message from an account no longer in allowed_acis (taken out in a hurry: a stolen phone) is set
        aside, its files deleted, and never reaches Gemma (hard rule 1; review S3); whether it was."""
        if message.peer_aci.lower() in {aci.lower() for aci in self.app.cfg.allowed_acis}:
            return False
        self.app.store.set_status(message.id, "ignored")
        self.app._drop_attachments(message.id)
        log.warning("message %d set aside: its account is no longer allowed", message.id)
        return True

    def connection(self, ready: bool, failure: str) -> None:
        """signal-cli ready, or ended (logged out by Signal it ends at every start): kept for doctor, as /status
        cannot be reached over Signal then (review S2)."""
        if ready:
            self.app.part_status.signal_ready()
        else:
            self.app.part_status.signal_failed(failure)
            log.warning("signal-cli is not connected (%s)", failure)

    def _identity_changed_at_start(self) -> set[str]:
        """The accounts whose safety number changed, as her state table kept them; for a ✗ that status.json kept from
        a release that named none, every allowed account, each cleared by its own message (review S5)."""
        allowed = {aci.lower() for aci in self.app.cfg.allowed_acis}
        named = self.app.store.get_state(IDENTITY_KEY)
        if named is None:  # none kept: every allowed account while status.json shows the ✗
            return set(allowed) if self.app.part_status.security().identity_changed is not None else set()
        kept = {aci for aci in named.split(",") if aci in allowed}
        if not kept:  # only accounts no longer allowed: nothing of theirs is shown any more
            self.app.store.delete_state(IDENTITY_KEY)
            self.app.part_status.identity_ok()
        return kept

    def _keep_identity_changed(self) -> None:
        if self.identity_changed:
            self.app.store.set_state(IDENTITY_KEY, ",".join(sorted(self.identity_changed)))
        else:
            self.app.store.delete_state(IDENTITY_KEY)

    def mark_identity_changed(self, aci: str) -> None:
        """An allowed account's safety number changed: kept (state and status.json) until a message of theirs opens."""
        self.identity_changed.add(aci.lower())
        self._keep_identity_changed()
        self.app.part_status.identity_changed()

    def identity_opened(self, aci: str) -> None:
        """A message from `aci` opened: its safety number is trusted again; the ✗ goes once no account is left."""
        if aci.lower() in self.identity_changed:
            self.identity_changed.discard(aci.lower())
            self._keep_identity_changed()
        if not self.identity_changed:
            self.app.part_status.identity_ok()

    def wait_after(self, exc: SignalError, pause: float) -> float | None:
        """The pause before the next try of a send that failed (`SignalError.wait`); None for no try now. A captcha
        asked for or a long rate limit is kept as Signal's time (a captcha request without one: LIMIT_UNNAMED_SECONDS)
        in status.json for doctor, and no send or resend is tried until then (review S4: the resend pass sent every
        failed message again every minute)."""
        wait = exc.wait(pause)
        if wait is None and isinstance(exc, RateLimitError):
            seconds = exc.retry_after if exc.retry_after is not None else LIMIT_UNNAMED_SECONDS
            until = self.app._clock() + min(max(seconds, 0.0), LIMIT_MAX_SECONDS)
            self.app.part_status.signal_limited(until, captcha=exc.proof_required)
        return wait

    def waits(self, what: str) -> bool:
        """Whether Signal's wait still lasts (then `what` is not tried: Signal would refuse it); one that is over is
        cleared."""
        until = self.app.part_status.security().limited_until
        if until is None:
            return False
        if self.app._clock() < until:
            log.info("%s not sent: Signal limits her sending until %s", what, local_hhmm(until, self.app.cfg.timezone))
            return True
        self.app.part_status.signal_unlimited()
        return False
