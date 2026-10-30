"""Pretend maps for the tool tests and the Gemma test set: canned places and routes, or a failure; Google Maps by
default, OpenStreetMap with `google=False` (the free default without a Google Maps key)."""

from __future__ import annotations

from dataclasses import replace

from local_assistant.maps import MapsError, Place, Route


class FakeMaps:
    def __init__(
        self,
        places: dict[str, list[Place]] | None = None,
        routes: dict[str, Route] | None = None,
        *,
        google: bool = True,
    ) -> None:
        self.google = google
        self.found = places or {}  # a word in the request → places
        self.routes = routes or {}  # a word in the destination → route
        self.asked: list[tuple[str, ...]] = []
        self.quick: list[bool] = []
        self.down = False

    async def places(self, what: str, near: str = "", *, quick: bool = False) -> list[Place]:
        self.asked.append(("places", what, near))
        self.quick.append(quick)
        if self.down:
            raise MapsError(f"{self._name} did not answer in time.")
        text = f"{what} {near}".lower()
        return next((found for word, found in self.found.items() if word in text), [])

    async def places_near(self, near: str = "") -> str:
        return near if near and near.lower() not in ("me", "here", "home") else "home"

    async def route(self, to: str, mode: str, origin: str = "", *, quick: bool = False) -> Route:
        self.asked.append(("route", to, mode, origin))
        self.quick.append(quick)
        if self.down:
            raise MapsError(f"{self._name} did not answer in time.")
        if mode not in ("bike", "walk", "transit", "car"):
            raise ValueError("the way of travel must be bike, walk, transit or car.")
        for word, route in self.routes.items():
            if word in to.lower():
                return replace(route, mode=mode, origin=origin or route.origin)
        raise MapsError(f"{self._name} found no route.", retry=True)

    @property
    def _name(self) -> str:
        return "Google Maps" if self.google else "OpenStreetMap"
