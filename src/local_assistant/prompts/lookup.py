"""The lookup guard (brain; owner, 22 Oct): her answer promised a lookup ("let me check") and no tool ran. Code's line
for the one retry, in her newest message, and code's true line when the retry still promised one with no tool call."""

from __future__ import annotations

# fmt: off
__all__ = ["LOOKUP_NOT_DONE", "LOOKUP_PHOTO_NOT_DONE", "LOOKUP_RETRY"]
# fmt: on

# the retry's line, right before the user's words in her newest message (the request before it stays the same, for
# the prompt cache); never stored
LOOKUP_RETRY = (
    "(Note from the bridge, not from the user: your first answer to this message said you would look it up, but it "
    "called no tool, so nothing would come back. Call web_search now, or answer without it.)"
)
# the owner's words (22 Oct): after her words, a note in a text and said in English on a call
LOOKUP_NOT_DONE = "I didn't look that up. Ask me again and I'll search."
# with a photo in the request (no retry: "let me take a look" is at the photo): asking again reads the photo again, with
# no search promised (second deep scan, dialogue-photo-promise-line-says-search)
LOOKUP_PHOTO_NOT_DONE = "I didn't answer that yet. Ask me again."
