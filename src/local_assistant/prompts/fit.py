"""The request within the model's context (context.ContextBuilder.fit_turn): code's line at the end of a tool result
it cut so the request fits (review of 0.9.8, guards-2: on a 16 GB Mac a 3-page read and a long answer passed the
model's context, and Ollama cut the request with nothing logged)."""

from __future__ import annotations

# fmt: off
__all__ = ["TOOL_RESULT_CUT"]
# fmt: on

TOOL_RESULT_CUT = (
    "(Cut here by the bridge: the rest of this result did not fit in what the model can read at once. Answer from "
    "what is here, and say that the rest was not read.)"
)
