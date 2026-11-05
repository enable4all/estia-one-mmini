"""Deep Thinking (thinking.py; owner, 4 Nov 2026): code's replies to /thinking and its /status line, in code's own words
(English, as /quiet's and /babble's are). One switch for each allowed account, so each reply is about the asker's own.
The words are the owner's copy of 4 Nov, approved: her replies speak as "I", like her other command replies (the docs
say "she"). The /help line is in HELP (prompts/__init__.py)."""

from __future__ import annotations

# fmt: off
__all__ = [
    "THINKING_NO_CONTEXT", "THINKING_NO_ROOM", "THINKING_OFF", "THINKING_OFF_STOPPED", "THINKING_ON",
    "THINKING_STATE_OFF", "THINKING_STATE_ON", "THINKING_STATUS_OFF", "THINKING_STATUS_ON", "THINKING_USAGE",
]
# fmt: on

# {time}: when it ends by itself, as HH:MM in the user's time zone
THINKING_ON = (
    "Deep Thinking is on for your texts and voice notes until {time}: I think before I answer, so an answer can take a "
    "few minutes, the first one longer. Not on calls. Off by itself after 30 minutes without a message from you; "
    "/thinking off stops it sooner."
)
THINKING_OFF = "Deep Thinking is off."
# /thinking off while one of the asker's answers is thinking: it is stopped and comes again at once, without the thought
THINKING_OFF_STOPPED = "Deep Thinking is off. The answer on its way comes without it."
THINKING_STATE_ON = "Deep Thinking is on for your texts and voice notes until {time}. /thinking off stops it."
THINKING_STATE_OFF = (
    "Deep Thinking is off. /thinking on: I think before I answer your texts and voice notes, off by itself after 30 "
    "minutes without a message from you."
)
THINKING_USAGE = "Usage: /thinking on, /thinking off or /thinking (to see which)"
# a Mac whose model keeps under 32,768 tokens of conversation (the 16 GB tier): the thought would take the whole chat.
# The reply names the Mac's memory, not the tokens: setup gives 32,768 tokens or more to the Macs with 24 GB of memory or
# more and 16,384 under 20 GB (`setup.choose_model`); a bigger Mac whose config was lowered by hand gets the same line
THINKING_NO_ROOM = "Deep Thinking stays off on this Mac: it runs only on Macs with 24 GB of memory or more."
# a config that does not say the model's context (no context_tokens line: from before 0.9.7, or setup could not read it)
THINKING_NO_CONTEXT = (
    "Deep Thinking stays off here: her config does not say how much of a conversation her model keeps. If doctor shows "
    "no warning for Model context, add context_tokens = 32768 at the top of her config and restart her."
)
# /status, in its first section: the asker's own
THINKING_STATUS_ON = "Deep Thinking: on until {time}"
THINKING_STATUS_OFF = "Deep Thinking: off"
