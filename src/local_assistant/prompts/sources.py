"""The Sources note after an answer (brain): the web results' and the maps' links, the map's credit lines, and code's
line when OpenStreetMap answered in Google Maps' place (owner, 26 Oct)."""

from __future__ import annotations

# fmt: off
__all__ = ["MAPS_INSTEAD", "sources_note"]
# fmt: on

# Google Maps is set up but failed, or its daily cap is reached, and OpenStreetMap answered (`Place.instead`,
# `Route.instead`): the owner's words, one line for both. Code can tell the cap apart (`MapsError.fallback`); a line of
# its own for the cap waits for his words
MAPS_INSTEAD = "Google Maps did not answer; this is from OpenStreetMap."


def sources_note(links: list[tuple[str, str]], *, instead: bool = False) -> str:
    """The links of the web results Gemma saw, as a code-written note (Gemma never writes links itself). After Google
    Maps failed and OpenStreetMap answered in this answer (`instead`), code's line comes first, under the heading."""
    if not links:
        return ""
    lines = [MAPS_INSTEAD] if instead else []
    return "\n".join(["🔎 **Sources**", *lines, *(f"• {site}: {url}" for site, url in links)])
