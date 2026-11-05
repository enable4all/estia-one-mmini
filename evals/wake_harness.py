"""Offline wake test: her name as Whisper hears it on a call in standby, with and without the hint, before standby's
hint is kept (owner, 22 Oct: her name said in standby was missed; the hint's known risk is Whisper writing the prompt
into silence or noise, so wakes AND false wakes are measured).

No call, no microphone and no caller: Pocket TTS (the voice service's English voices; Kokoro until it was removed, 3 Nov
2026) says each phrase; the audio goes
the way a caller's does (48 kHz samples with a moment of quiet before and after, the line's noise mixed in, then
16 kHz to Whisper, the call's `pcm48_to_wav16k`), once without the hint and once with her name as it (the arms in
turn, item by item); `wake.says_name` decides as standby does, after the call's noise filter (`is_noise_transcript`).
Phrases with her name say it at the start, in the middle, at the end and alone; phrases without it are TV-like
sentences, other names and names that sound near hers; and sounds without speech (silence, room noise, a hum,
clicks). Audio lives only in memory (hard rule 7: no WAV is written, but for `--call`'s phone line below, synthetic
audio only); the result file holds counts and, per item, the phrase's id, voice, noise, arm, whether it woke and what
Whisper wrote of the synthetic phrase.

`--call`: standby as a call hears it (owner, 23 Oct: in standby his "hello Tiffany", said with a pause, came to Whisper
as clips of 0.7-0.8 s, chopped by the early end at the pause, and each was heard as no words; the run above never cut a
phrase and gave every clip 0.6 s of quiet). Each phrase is one stream on the caller's line (`--lines`: "clean", or
"phone": a mobile line's band, the level the owner's turns had and Opus at 16 kbit/s through macOS's afconvert, in a
private temporary folder removed at once: synthetic phrases only), cut into turns by the call's own listen
(`CallAudio.listen`: the arms "early end", the real Smart Turn at the short pause, and "full pause"), each turn to
Whisper as calls.py sends it, then standby's decision turn by turn (a click or a bump not sent, the noise filter, the
hold of a turn that ends like more is coming, her name): the item wakes when one of its turns says her name. Its
phrases: the owner's three with a pause after the first part and said whole, her name alone, all the phrases above, and
short sounds. Pocket TTS has no speed, so her name alone is said once, at its own pace: the brisk clips Kokoro said (1.3
and 1.6 times its pace, about 0.6 s) are no longer measured (a known gap). The pause by speech is left out (the frames
go in at once, never in real time, so its answers would come late): the pause is by loudness, which the "loud room"
noise can keep open.

Run it alone: the voice service's Neural Engine and the GPU are shared (the owner's live install, other jobs' evals);
it refuses to start while another evals job runs (`--force` to run anyway). The voice service must be one of your own
macOS account (owner, 25 Oct: her voice service answers only programs running as her own account, so in her own user
hers refuses the harness): this branch's, built (`cd tools/voice && swift build -c release`) and run as you on another
port (`tools/voice/.build/release/voice-service --port 8152`, then `--url http://127.0.0.1:8152`). It must take the
hint (its health says "hints"); `--call` needs its turn detector ("turn": "ready") and the hint only with `--hint` (its
arms added).

    uv run python -m evals.wake_harness                 (her name from the config, default Estia; five voices)
    uv run python -m evals.wake_harness --name Tiffany --voices heart,michael --noises clean,room
    uv run python -m evals.wake_harness --url http://127.0.0.1:8152 --out evals/out
    uv run python -m evals.wake_harness --call --name Tiffany --lines phone --noises clean,room [--hint]

Output: a table (wakes: phrases with her name heard as her name; false wakes: the others heard as her name; for each
arm; with `--call` also the turns, those heard as no words and those of 1 s or less) and evals/out/wake-<UTC
time>.json (`wake-call-` with `--call`). English only (Pocket TTS's English voices): the other ten languages are not
measured.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import http.client
import io
import json
import math
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import wave
import zlib
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from evals.jobs import CALL_HELPER, JOB, WAIT_S, ancestors, call_running, listing, other_jobs
from local_assistant.call_audio import (
    EARLY_KINDS,
    FRAME_BYTES,
    RATE,
    CallAudio,
    TurnEnd,
    pcm48_to_wav16k,
    rms,
    wav_to_pcm48,
)
from local_assistant.calls import MIN_VOICED_S, calling_words, goes_on, is_noise_transcript, percentile
from local_assistant.commands import VOICES
from local_assistant.config import DEFAULT_CONFIG_PATH, DEFAULT_NAME, load_config
from local_assistant.language import ENGLISH
from local_assistant.voice import AFCONVERT
from local_assistant.wake import only_name, says_name

DEFAULT_URL = "http://127.0.0.1:8150"
OTHER_ACCOUNT = (
    "wake harness: that voice service answers only programs of its own macOS account (hers, in her own user): run "
    "this branch's as you on another port, tools/voice/.build/release/voice-service --port 8152, then pass "
    "--url http://127.0.0.1:8152"
)
DEFAULT_VOICES = ("heart", "jane", "eve", "alba", "michael")  # Pocket TTS's on either model (vera: the 100M's only)
ARMS = (("no hint", False), ("hint", True))  # in turn for every item
NOISES = ("clean", "room", "loud room")  # the caller's line: none, a quiet room, a TV or a fan nearby
QUIET_S = 0.3  # quiet before and after a phrase, as a call's clip has around a turn
OUT = Path(__file__).resolve().parent / "out"

# {name}: hers. Said at the start, in the middle, at the end and alone, as a caller wakes her
NAMED = (
    ("start-there", "start", "{name}, are you there?"),
    ("start-question", "start", "{name}, I have a question."),
    ("start-weather", "start", "{name}, what's the weather like tomorrow?"),
    ("start-hey", "start", "Hey {name}, can you help me?"),
    ("middle-ask", "middle", "I wanted to ask you, {name}, about tomorrow."),
    ("middle-okay", "middle", "Okay so {name}, one more thing."),
    ("end-hear", "end", "Can you hear me, {name}?"),
    ("end-back", "end", "I'm back, {name}."),
    ("alone", "alone", "{name}."),
    ("alone-asked", "alone", "{name}?"),
    ("alone-hey", "alone", "Hey {name}."),
)
# never her name: the TV, other people in the room, and names that sound near hers
TV = (
    ("tv-weather", "tv", "And now, the weather for the weekend."),
    ("tv-news", "tv", "The minister said the talks will go on tomorrow."),
    ("tv-break", "tv", "Coming up after the break: the best autumn recipes."),
    ("tv-goal", "tv", "Goal! What a finish in the last minute!"),
    ("tv-thanks", "tv", "Thank you for watching. See you next week."),
    ("room-salt", "room", "Can you pass me the salt, please?"),
)
OTHER_NAMES = (
    ("other-jessica", "other name", "Jessica, are you there?"),
    ("other-michael", "other name", "Hey Michael."),
    ("other-sarah", "other name", "Sarah, I have a question."),
    ("other-daniel", "other name", "Can you hear me, Daniel?"),
)
NEAR: dict[str, tuple[str, ...]] = {  # by her name, as compared (lower case)
    "tiffany": ("Stephanie", "Epiphany", "Tiffin", "Timothy", "Tiff and me went out.", "Daphne", "Tiffy"),
    "estia": ("Esther", "Fiesta", "Asia", "Vesta", "The estate is big."),
}
# `--long`: speech without her name, as a TV, a radio or people in the room go on for a while, so false wakes are
# measured per hour of speech, not per minute of mostly quiet clips (review E4, 0.9.7)
LONG = (
    "Good evening, and welcome to the news at ten, where we start tonight with the weather.",
    "A band of heavy rain moves in from the west overnight, with strong winds along the coast by morning.",
    "Temperatures will stay well below average for the time of year, so keep a warm coat by the door.",
    "In other news, the city council has agreed a new plan for cycle lanes across the old town.",
    "Shoppers queued for hours outside the new market hall when it opened its doors this morning.",
    "The trains between the two cities are running again after the signal fault was finally repaired.",
    "And now the sport, where the home side came back from two goals down to win in the last minute.",
    "The coach said after the match that the team had never stopped believing they could turn it around.",
    "Next week the tennis season moves indoors, with the final tournament of the year in the capital.",
    "Here is a recipe for a simple autumn soup with pumpkin, ginger, a little cream and fresh bread.",
    "Chop the onions finely, fry them gently in butter, then add the stock and let it simmer for twenty minutes.",
    "Season to taste with salt and pepper, and serve it hot with a spoon of yoghurt on top.",
    "Coming up after the break, our gardening expert answers your questions about roses and winter frost.",
    "If your lawn is full of moss, rake it out in spring and give the grass a feed in early summer.",
    "The museum's new exhibition shows paintings from the north of the country that have never been seen before.",
    "Tickets are free for children under twelve, and the gallery stays open late on Thursday evenings.",
    "Scientists have found a new kind of beetle living high in the trees of the rain forest.",
    "The study, published this week, suggests there may be thousands more species still to be named.",
    "Traffic is heavy on the ring road this evening after a lorry lost part of its load near the bridge.",
    "Drivers are asked to leave extra time and to take the inner route through the centre if they can.",
    "Did you remember to buy milk on the way home, or shall I go to the corner shop before it closes?",
    "I think we should paint the hallway a lighter colour, the green makes it feel so small and dark.",
    "Can you turn the music down a little, I am trying to read and it keeps pulling me out of the book.",
    "The kids want pancakes for breakfast tomorrow, so we need eggs, flour and something sweet to go on top.",
    "Your mother called earlier and asked if we are still coming for lunch on Sunday afternoon.",
    "I left the car keys on the kitchen table next to the post, but now I cannot find them anywhere.",
    "Shall we watch the second part of that film tonight, or are you too tired after the long day?",
    "The washing machine is making that strange noise again, I think something is stuck in the drum.",
    "Thank you for listening to the programme this week, and do send us your questions for the next one.",
    "That was the evening news; stay with us for the late film, a comedy set on a small island in the sun.",
    "Our phone lines are open now, so if you have a story about your neighbourhood, call us and tell us.",
    "And finally, a dog in the harbour town has learned to carry the morning paper to every house in its street.",
)


@dataclass(frozen=True)
class Phrase:
    id: str
    kind: str  # start, middle, end, alone; tv, room, other name, near; long (`--long`)
    text: str
    named: bool
    matched: bool = False  # no name of hers, yet the text check (`says_name`) takes it for one: counted apart


@dataclass(frozen=True)
class Item:
    """One phrase or sound through one arm: what is kept (never audio)."""

    id: str
    kind: str
    named: bool
    voice: str
    noise: str
    arm: str
    woke: bool
    heard: str  # what Whisper wrote of the synthetic phrase (no person's words)
    matched: bool = False  # the text check's own false wake when it woke (`Phrase.matched`)
    seconds: float = 0.0  # the audio's length


def phrases(name: str, *, long: bool = False) -> list[Phrase]:
    """Her name's phrases and the others (with `long`, the LONG speech too). A phrase without her name that the text
    check would take for it is kept and marked (`matched`): its wakes are the text check's own, counted apart from
    Whisper's (review E4, 0.9.7: such phrases were left out, so the text check's false wakes were never counted)."""
    named = [Phrase(id_, kind, text.format(name=name), True) for id_, kind, text in NAMED]
    near = NEAR.get(name.casefold(), ())
    near_phrases = [(f"near-{n}", "near", f"{word}, are you there?" if " " not in word else word) for n, word in
                    enumerate(near)]  # fmt: skip
    long_phrases = [(f"long-{n}", "long", text) for n, text in enumerate(LONG)] if long else []
    others = [
        Phrase(id_, kind, text, False, says_name(text, name))
        for id_, kind, text in (*TV, *OTHER_NAMES, *near_phrases, *long_phrases)
    ]
    return named + others


def item_seed(*parts: str) -> int:
    """An item's own noise seed (its phrase, voice, line and noise): another item gets other noise, the same item the
    same in every run (review E4: one seed served every item)."""
    return zlib.crc32("|".join(parts).encode())


def false_wake_bound(wakes: int, seconds: float, confidence: float = 0.95) -> float:
    """The upper bound of the false wakes an hour at `confidence`, from `wakes` in `seconds` of audio (Poisson, exact:
    with none, about 3/T an hour for T hours)."""
    hours = seconds / 3600
    if hours <= 0:
        return math.inf
    target = 1 - confidence

    def at_most(rate: float) -> float:
        return sum(math.exp(-rate) * rate**k / math.factorial(k) for k in range(wakes + 1))

    low, high = 0.0, max(10.0, 10.0 * (wakes + 1))
    for _ in range(200):
        mid = (low + high) / 2
        low, high = (mid, high) if at_most(mid) > target else (low, mid)
    return high / hours


def mcnemar(first_only: int, second_only: int) -> float:
    """The exact two-sided McNemar p: the items one arm woke and the other did not, each way at even odds."""
    moved = first_only + second_only
    if moved == 0:
        return 1.0
    tail = sum(math.comb(moved, k) for k in range(min(first_only, second_only) + 1))
    return min(1.0, 2 * tail / float(1 << moved))


def paired(items: Sequence[Item] | Sequence[CallItem]) -> dict[str, dict[str, float]]:
    """Each two arms compared item by item on her name's items (the same audio through both): those only the first
    woke, those only the second, and McNemar's p (review E4: the arms were compared by their totals)."""
    arms = list(dict.fromkeys(item.arm for item in items))
    woke: dict[str, dict[tuple[str, ...], bool]] = {arm: {} for arm in arms}
    for item in items:
        if item.named:
            woke[item.arm][(item.id, item.voice, item.noise, getattr(item, "line", ""))] = item.woke
    out: dict[str, dict[str, float]] = {}
    for at, first in enumerate(arms):
        for second in arms[at + 1 :]:
            keys = woke[first].keys() & woke[second].keys()
            only_first = sum(woke[first][key] and not woke[second][key] for key in keys)
            only_second = sum(woke[second][key] and not woke[first][key] for key in keys)
            out[f"{first} vs {second}"] = {
                "only the first": only_first,
                "only the second": only_second,
                "p": round(mcnemar(only_first, only_second), 6),
            }
    return out


def _samples(values: Iterable[float]) -> bytes:
    return array.array("h", (max(-32_000, min(32_000, round(v))) for v in values)).tobytes()


def _room(count: int, level: float, seed: int) -> list[float]:
    """`count` samples of soft broadband noise, low-passed (a room, a fan): the same for the same seed. By sample count,
    never seconds: int(RATE * seconds) drops a sample for some clip lengths."""
    rnd = random.Random(seed)
    out, last = [], 0.0
    for _ in range(count):
        last = 0.97 * last + 0.03 * rnd.gauss(0.0, 1.0)
        out.append(last * level * 6)
    return out


def _hum(count: int, level: float) -> list[float]:
    return [level * sum(math.sin(2 * math.pi * hz * i / RATE) / k for k, hz in enumerate((50, 100, 150), 1))
            for i in range(count)]  # fmt: skip


def _clicks(count: int, seed: int) -> list[float]:
    """`count` samples of key clicks: short loud bursts every 0.1 to 0.3 s."""
    rnd = random.Random(seed)
    out = [0.0] * count
    at = 0
    while at < len(out):
        for i in range(at, min(at + round(RATE * 0.004), len(out))):
            out[i] = rnd.uniform(-6_000, 6_000)
        at += round(RATE * rnd.uniform(0.1, 0.3))
    return out


def sounds() -> list[tuple[str, str, bytes]]:
    """Sounds without speech, 2 s each at 48 kHz: (id, kind, samples)."""
    return [
        ("silence", "silence", bytes(2 * RATE * 2)),
        ("room-noise", "room noise", _samples(_room(2 * RATE, 120.0, 7))),
        ("hum", "hum", _samples(_hum(2 * RATE, 900.0))),
        ("clicks", "clicks", _samples(_clicks(2 * RATE, 11))),
    ]


def noisy(pcm: bytes, noise: str, seed: int = 3) -> bytes:
    """48 kHz samples with the line's noise mixed in (`NOISES`), clipped to 16 bits; the same for the same seed."""
    if noise == "clean":
        return pcm
    samples = array.array("h", pcm)
    level = 120.0 if noise == "room" else 600.0
    mix = _room(len(samples), level, seed)
    if noise == "loud room":
        mix = [a + b for a, b in zip(mix, _hum(len(samples), 300.0), strict=True)]
    return array.array(
        "h", (max(-32_767, min(32_767, int(s + m))) for s, m in zip(samples, mix, strict=True))
    ).tobytes()


def woke(text: str, name: str) -> bool:
    """Standby's decision on a transcript: not noise (the call's filter, with sustained voice), and her name in it."""
    return not is_noise_transcript(text, 99.0) and says_name(text, name)


class Voice(Protocol):
    def health(self) -> dict[str, object]: ...

    def speak(self, text: str, voice: str) -> bytes: ...

    def transcribe(self, audio: bytes, hint: str) -> str: ...


class CallVoice(Protocol):
    """What --call needs: the turn detector (Smart Turn) too."""

    def health(self) -> dict[str, object]: ...

    def speak(self, text: str, voice: str) -> bytes: ...

    def transcribe(self, audio: bytes, hint: str) -> str: ...

    def turn(self, window: bytes) -> float | str: ...


class OtherAccountError(RuntimeError):
    """The voice service refused this program (403): it answers only programs of its own macOS account."""


class VoiceService:
    """The voice service over plain HTTP on 127.0.0.1. Not the bridge's VoiceClient: that one sends only to a service
    of her own account (`peer_is_ours`), and this sends nothing of anyone's, only its own synthetic phrases."""

    def __init__(self, url: str = DEFAULT_URL, timeout: float = 60.0) -> None:
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("the voice service on this Mac, like http://127.0.0.1:8150")
        self._host, self._port, self._timeout = parts.hostname, parts.port or 80, timeout

    def _request(self, method: str, path: str, body: bytes | None, kind: str) -> bytes:
        connection = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        try:
            headers = {"Content-Type": kind} if body is not None else {}
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data = response.read()
        finally:
            connection.close()
        if response.status == 403:
            raise OtherAccountError(f"voice service: HTTP 403 on {path.partition('?')[0]}")
        if response.status != 200:
            raise RuntimeError(f"voice service: HTTP {response.status} on {path.partition('?')[0]}")
        return data

    def health(self) -> dict[str, object]:
        answer = json.loads(self._request("GET", "/health", None, ""))
        return answer if isinstance(answer, dict) else {}

    def speak(self, text: str, voice: str) -> bytes:
        body = json.dumps({"input": text, "voice": voice}).encode()
        return self._request("POST", "/v1/audio/speech", body, "application/json")

    def transcribe(self, audio: bytes, hint: str) -> str:
        """As a call's audio goes since 0.9.7 (`voice.transcribe(call=True)`: the service keeps a bare "Bye." or
        "Thank you."; review E4): `call=1`, after the hint."""
        query = [f"hint={urllib.parse.quote(hint, safe='')}"] if hint else []
        path = "/v1/audio/transcriptions?" + "&".join([*query, "call=1"])
        answer = json.loads(self._request("POST", path, audio, "application/octet-stream"))
        return str(answer.get("text", "")) if isinstance(answer, dict) else ""

    def turn(self, window: bytes) -> float | str:
        """Smart Turn's answer for a 16 kHz window (`POST /turn`), or "http" as the bridge's client says it."""
        try:
            answer = json.loads(self._request("POST", "/turn", window, "application/octet-stream"))
        except (OSError, RuntimeError, ValueError):
            return "http"
        p = answer.get("p") if isinstance(answer, dict) else None
        if isinstance(p, bool) or not isinstance(p, int | float) or not 0.0 <= p <= 1.0:
            return "http"
        return float(p)


def _both(service: Voice, pcm48: bytes, name: str, base: dict[str, Any]) -> list[Item]:
    audio = pcm48_to_wav16k(pcm48)
    items = []
    for arm, hinted in ARMS:
        heard = service.transcribe(audio, name if hinted else "")
        items.append(Item(**base, arm=arm, woke=woke(heard, name), heard=heard))
    return items


def run(
    service: Voice,
    name: str,
    *,
    voices: Sequence[str],
    noises: Sequence[str],
    long: bool = False,
    busy: Callable[[], bool] = lambda: False,
    wait: Callable[[float], None] = time.sleep,
) -> list[Item]:
    """Every phrase in every voice and noise, then every sound, each through both arms in turn. While `busy` (a call
    on this Mac: the voice service's one engine queue is the call's) it waits before the next item."""
    quiet = bytes(2 * round(RATE * QUIET_S))
    items: list[Item] = []

    def free() -> None:
        while busy():
            wait(CALL_WAIT_S)

    for phrase in phrases(name, long=long):
        for voice in voices:
            for noise in noises:
                free()
                spoken = quiet + wav_to_pcm48(service.speak(phrase.text, voice)) + quiet
                pcm = noisy(spoken, noise, item_seed(phrase.id, voice, noise))
                base = {"id": phrase.id, "kind": phrase.kind, "named": phrase.named, "voice": voice, "noise": noise}
                items += _both(service, pcm, name, base | {"matched": phrase.matched, "seconds": _seconds(pcm)})
    for id_, kind, pcm in sounds():
        free()
        base = {"id": id_, "kind": kind, "named": False, "voice": "-", "noise": "-", "seconds": _seconds(pcm)}
        items += _both(service, pcm, name, base)
    return items


def _seconds(pcm: bytes) -> float:
    return round(len(pcm) / (2 * RATE), 3)


def _counts(mine: Sequence[Item] | Sequence[CallItem]) -> dict[str, Any]:
    """`summary`'s counts for one arm: her name's items, the others (Whisper's false wakes, per hour with the 95 %
    bound), the text check's own (`matched`), and by kind."""
    kinds: dict[str, dict[str, int]] = {}
    for item in mine:
        counts = kinds.setdefault(item.kind, {"items": 0, "woke": 0})
        counts["items"] += 1
        counts["woke"] += item.woke
    others = [i for i in mine if not i.named and not i.matched]
    seconds = round(sum(i.seconds for i in others), 1)
    false = sum(i.woke for i in others)
    return {
        "named": {"items": sum(i.named for i in mine), "woke": sum(i.woke for i in mine if i.named)},
        "unnamed": {
            "items": len(others),
            "woke": false,
            "seconds": seconds,
            "per hour": round(false * 3600 / seconds, 2) if seconds else 0.0,
            "per hour under": round(false_wake_bound(false, seconds), 2) if seconds else None,
        },
        "matched": {"items": sum(i.matched for i in mine), "woke": sum(i.woke for i in mine if i.matched)},
        "by kind": kinds,
    }


def summary(items: Sequence[Item]) -> dict[str, Any]:
    """Per arm (`_counts`), then the arms compared item by item (`paired`)."""
    out: dict[str, Any] = {arm: _counts([item for item in items if item.arm == arm]) for arm, _ in ARMS}
    return out | {"paired": paired(items)}


def table(result: dict[str, Any]) -> str:
    def rate(counts: dict[str, Any]) -> str:
        share = 100 * counts["woke"] / counts["items"] if counts["items"] else 0.0
        return f"{counts['woke']}/{counts['items']} ({share:.0f} %)"

    arms = {arm: r for arm, r in result.items() if arm != "paired"}
    width = max(9, *(len(arm) for arm in arms))
    cell = max(16, width + 1)
    lines = [f"{'arm':<{width}} {'wakes (her name)':<22} {'false wakes (no name)':<22} {'text check own':<16}"]
    lines += [
        f"{arm:<{width}} {rate(r['named']):<22} {rate(r['unnamed']):<22} {r['matched']['woke']}/{r['matched']['items']}"
        for arm, r in arms.items()
    ]
    lines.append("")
    for arm, r in arms.items():
        unnamed = r["unnamed"]
        minutes = unnamed["seconds"] / 60
        lines.append(
            f"{arm}: {unnamed['woke']} false wakes in {minutes:.1f} min of audio without her name: under"
            f" {unnamed['per hour under']} an hour (95 %)"
        )
    for pair, found in result.get("paired", {}).items():
        lines.append(
            f"{pair}: her name woke only the first {found['only the first']:.0f}, only the second"
            f" {found['only the second']:.0f} (McNemar's exact p = {found['p']:.3f})"
        )
    kinds = sorted({kind for r in arms.values() for kind in r["by kind"]})
    lines.append("")
    lines.append(f"{'kind':<13} " + " ".join(f"{arm:<{cell}}" for arm in arms))
    lines += [f"{kind:<13} " + " ".join(f"{rate(r['by kind'][kind]):<{cell}}" for r in arms.values()) for kind in kinds]
    return "\n".join(lines)


# --call (see the module's text): the caller's line, the owner's phrases, the call's own cut and standby turn by turn
LINES = ("clean", "phone")
PHONE_BAND_HZ = (150.0, 3_800.0)  # a mobile line's band, the narrow (harsher) case
PHONE_BITRATE = 16_000  # Opus at 16 kbit/s, as a mobile Signal call may run
# the loudest 10 ms frame's level (RMS), which the call's log calls a turn's peak: the owner's were 3,763-10,768 (23 Oct)
PHONE_LEVEL = 7_000.0
EARLY_PAUSE_S = 0.6  # the config's defaults: [calls] early_end_pause_seconds and end_of_turn_seconds
FULL_PAUSE_S = 2.3
CALL_QUIET_S = 0.5  # quiet before a stream (the listen measures the floor) and after it, past the full pause
CALL_ARMS = (("early end", True, False), ("full pause", False, False))  # (arm, Smart Turn at the short pause, hint)
HINT_ARMS = (("early end + hint", True, True), ("full pause + hint", False, True))
# the owner's words (23 Oct), with a pause after the first part longer than the short pause, and said whole
OWNER = (
    ("owner-there", ("{name}.", "Are you there?")),
    ("owner-hello", ("Hello.", "{name}.")),
    ("owner-count", ("One, two, three, four, five, six, seven.", "{name}.")),
)
OWNER_PAUSES_S = (0.8, 1.2)
SPOKEN_FLOOR = 200  # the voice's own quiet before and after a part, trimmed so a pause is the one set


@dataclass(frozen=True)
class Spoken:
    """A phrase for --call: its parts said one by one with `pause_s` between them (one part: said whole)."""

    id: str
    kind: str  # owner, owner paused, alone once; or a kind of `phrases`
    parts: tuple[str, ...]
    named: bool
    pause_s: float = 0.0
    matched: bool = False  # `Phrase.matched`


def call_phrases(name: str, *, long: bool = False) -> list[Spoken]:
    """The owner's phrases (paused and whole), her name alone, then every phrase of `phrases`."""
    out: list[Spoken] = []
    for id_, parts in OWNER:
        filled = tuple(part.format(name=name) for part in parts)
        out.append(Spoken(f"{id_}-whole", "owner", (" ".join(filled),), True))
        out += [Spoken(f"{id_}-{pause:g}s", "owner paused", filled, True, pause) for pause in OWNER_PAUSES_S]
    out.append(Spoken("alone-once", "alone once", (f"{name}.",), True))  # at Pocket TTS's own pace: it has no speed
    return out + [
        Spoken(phrase.id, phrase.kind, (phrase.text,), phrase.named, matched=phrase.matched)
        for phrase in phrases(name, long=long)
    ]


def short_sounds() -> list[tuple[str, str, bytes]]:
    """Short loud sounds without speech, each enough to start a turn and cut to under 1 s: (id, kind, samples)."""
    burst = _samples(_room(round(RATE * 0.35), 3_000.0, 13))
    beep = _samples(3_000.0 * math.sin(2 * math.pi * 880 * i / RATE) for i in range(round(RATE * 0.3)))
    return [("burst", "short sound", burst), ("beep", "short sound", beep)]


def _trimmed(pcm: bytes) -> bytes:
    """A part without the voice's own quiet before and after it."""
    samples = array.array("h", pcm)
    loud = [i for i, x in enumerate(samples) if abs(x) > SPOKEN_FLOOR]
    return samples[loud[0] : loud[-1] + 1].tobytes() if loud else b""


def stream(parts: Sequence[bytes], pause_s: float) -> bytes:
    """A phrase's parts (48 kHz) as the caller says them: quiet, the parts with the pause between, quiet past the full
    pause."""
    pause = bytes(2 * round(RATE * pause_s))
    after = bytes(2 * round(RATE * (FULL_PAUSE_S + CALL_QUIET_S)))
    return bytes(2 * round(RATE * CALL_QUIET_S)) + pause.join(parts) + after


def _biquad(samples: list[float], hz: float, *, high: bool) -> list[float]:
    """A second-order Butterworth high- or low-pass at `hz` (the Audio EQ Cookbook's), on 48 kHz samples."""
    w = 2 * math.pi * hz / RATE
    cos, alpha = math.cos(w), math.sin(w) / math.sqrt(2)
    b0 = (1 + cos) / 2 if high else (1 - cos) / 2
    b1 = -(1 + cos) if high else 1 - cos
    a0, a1, a2 = 1 + alpha, -2 * cos, 1 - alpha
    out: list[float] = []
    x1 = x2 = y1 = y2 = 0.0
    for x in samples:
        y = (b0 * x + b1 * x1 + b0 * x2 - a1 * y1 - a2 * y2) / a0
        x2, x1, y2, y1 = x1, x, y1, y
        out.append(y)
    return out


def band(pcm: bytes) -> bytes:
    """48 kHz samples through a mobile line's band (`PHONE_BAND_HZ`; the top edge twice, steeper)."""
    low, high = PHONE_BAND_HZ
    values = [float(x) for x in array.array("h", pcm)]
    for hz, is_high in ((low, True), (high, False), (high, False)):
        values = _biquad(values, hz, high=is_high)
    return _samples(values)


def _wav48(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(RATE)
        file.writeframes(pcm)
    return out.getvalue()


def _opus(pcm: bytes) -> bytes:
    """48 kHz samples through Opus at PHONE_BITRATE and back (macOS's afconvert), the same length. Synthetic phrases
    only, in a private temporary folder removed at once."""
    with tempfile.TemporaryDirectory(prefix="wake-line-") as folder:
        plain, coded, back = (Path(folder) / name for name in ("line.wav", "line.caf", "back.wav"))
        plain.write_bytes(_wav48(pcm))
        for args in (
            ["-f", "caff", "-d", "opus", "-b", str(PHONE_BITRATE), str(plain), str(coded)],
            ["-f", "WAVE", "-d", f"LEI16@{RATE}", str(coded), str(back)],
        ):
            subprocess.run([AFCONVERT, *args], check=True, capture_output=True, timeout=60)
        out = wav_to_pcm48(back.read_bytes())
    return out[: len(pcm)].ljust(len(pcm), b"\0")


def _loudest(pcm: bytes) -> float:
    """The loudest 10 ms frame's RMS (the call's log: a turn's peak)."""
    return max((rms(pcm[at : at + FRAME_BYTES]) for at in range(0, len(pcm), FRAME_BYTES)), default=0.0)


def on_line(pcm: bytes, line: str, noise: str, seed: int = 3) -> bytes:
    """A stream as the call's audio brings it: the room's noise at the caller's phone, then the line (`LINES`): "clean"
    as it is, "phone" band-limited, at the owner's level (`PHONE_LEVEL`) and Opus-coded."""
    mixed = noisy(pcm, noise, seed)
    if line != "phone":
        return mixed
    limited = band(mixed)
    loudest = _loudest(limited)
    if loudest:
        limited = _samples(x * PHONE_LEVEL / loudest for x in array.array("h", limited))
    return _opus(limited)


async def _queued(pcm: bytes) -> CallAudio:
    """A CallAudio whose incoming frames are `pcm`, all queued (no call helper): the call's own listen on them."""
    ours, theirs = socket.socketpair()
    theirs.close()
    reader, writer = await asyncio.open_unix_connection(sock=ours)
    audio = CallAudio(reader, writer)
    for start in range(0, len(pcm), FRAME_BYTES):
        audio.incoming.put_nowait(pcm[start : start + FRAME_BYTES].ljust(FRAME_BYTES, b"\0"))
    audio.closed.set()
    return audio


Judge = Callable[[bytes], float | str]


def _judge(turn: Judge) -> Callable[[bytes], Awaitable[float | str]]:
    async def ask(window: bytes) -> float | str:
        return await asyncio.to_thread(turn, window)

    return ask


async def cut(pcm: bytes, turn: Judge | None) -> list[tuple[bytes, dict[str, Any]]]:
    """`pcm` cut into turns by the call's own listen: with `turn` (Smart Turn) the early end at the short pause, as on
    a call; without it the full pause alone. Every turn's clip and info, as calls.py gets them."""
    audio = await _queued(pcm)
    rules = TurnEnd(_judge(turn), early_pause_s=EARLY_PAUSE_S) if turn is not None else None
    turns: list[tuple[bytes, dict[str, Any]]] = []
    try:
        while True:
            clip, info = await audio.listen(end_silence_s=FULL_PAUSE_S, turn=rules)
            turns.append((clip, info))
    except ConnectionError:  # every frame heard
        pass
    finally:
        audio.close()
    return turns


@dataclass(frozen=True)
class Turn:
    """One turn of a --call item, as standby met it (never audio)."""

    seconds: float  # the clip as the call cut it (a held turn: joined)
    words: int  # what Whisper wrote, in words; -1: not sent (a click or a bump, below MIN_VOICED_S)
    heard: str  # what Whisper wrote of the synthetic phrase
    woke: bool
    # Whisper's time on the turn (a held turn: each try), part of the caller's wait before her first word (review of
    # 26 Oct, eval-methods-6); 0: not sent
    heard_ms: int = 0


async def standby_turns(service: CallVoice, pcm: bytes, name: str, *, early: bool, hint: str) -> list[Turn]:
    """Standby on `pcm`, turn by turn as calls.py's `_converse` meets them: the call's listen (with Smart Turn when
    `early`), a turn with too little voice not sent, Whisper (with `hint`), the noise filter, the hold of an early end
    whose words end like more is coming (`goes_on`) or are her name alone (`only_name`: the rest of the full pause,
    joined, and her name heard alone counts though the joined clip lost it; the hold for words that would end the call
    is left out: no goodbye in standby's phrases), her name."""
    audio = await _queued(pcm)
    rules = TurnEnd(_judge(service.turn), early_pause_s=EARLY_PAUSE_S) if early else None
    turns: list[Turn] = []

    spent = [0.0]  # Whisper's seconds on the turn being heard

    async def hear(clip: bytes) -> str:
        started = time.monotonic()
        try:
            return await asyncio.to_thread(service.transcribe, pcm48_to_wav16k(clip), hint)
        finally:
            spent[0] += time.monotonic() - started

    try:
        while True:
            clip, info = await audio.listen(end_silence_s=FULL_PAUSE_S, turn=rules)
            seconds = round(len(clip) / (2 * RATE), 2)
            if float(info["voiced_s"]) < MIN_VOICED_S:
                turns.append(Turn(seconds, -1, "", False))
                continue
            spent[0] = 0.0
            text = await hear(clip)
            noise = is_noise_transcript(text, float(info["sustained_s"]))  # as calls.py: judged before the hold
            named = False  # her name alone, before a hold joined more to it
            while not noise and rules is not None and info.get("ended") in EARLY_KINDS:
                alone = only_name(text, name, calling_words(ENGLISH))
                if not (alone or goes_on(text)):
                    break
                named = named or alone
                rest = max(0.0, FULL_PAUSE_S - float(info.get("quiet_s", 0.0)))
                more, info = await audio.listen(end_silence_s=FULL_PAUSE_S, no_speech_s=rest, turn=rules)
                if not more:
                    break
                clip += more
                text = await hear(clip)
            seconds = round(len(clip) / (2 * RATE), 2)
            woke = not noise and (named or says_name(text, name))
            turns.append(Turn(seconds, len(text.split()), text, woke, heard_ms=round(spent[0] * 1000)))
    except ConnectionError:  # every frame heard
        pass
    finally:
        audio.close()
    return turns


@dataclass(frozen=True)
class CallItem:
    """One --call stream through one arm: what is kept (never audio)."""

    id: str
    kind: str
    named: bool
    voice: str
    noise: str
    line: str
    arm: str
    woke: bool  # one of its turns said her name: standby over
    turns: list[Turn] = field(default_factory=list)
    matched: bool = False  # `Phrase.matched`
    seconds: float = 0.0  # the stream's length: what standby listened to


def run_call(
    service: CallVoice,
    name: str,
    *,
    voices: Sequence[str],
    noises: Sequence[str],
    lines: Sequence[str],
    arms: Sequence[tuple[str, bool, bool]] = CALL_ARMS,
    long: bool = False,
    busy: Callable[[], bool] = lambda: False,
    wait: Callable[[float], None] = time.sleep,
) -> list[CallItem]:
    """Every --call phrase in every voice, line and noise, then the short sounds, each through every arm in turn. While
    `busy` (a call on this Mac) it waits before the next stream."""
    items: list[CallItem] = []

    def free() -> None:
        while busy():
            wait(CALL_WAIT_S)

    def through(pcm: bytes, base: dict[str, Any]) -> None:
        for arm, early, hinted in arms:
            turns = asyncio.run(standby_turns(service, pcm, name, early=early, hint=name if hinted else ""))
            woke = any(turn.woke for turn in turns)
            items.append(CallItem(**base, arm=arm, woke=woke, turns=turns, seconds=_seconds(pcm)))

    for spoken in call_phrases(name, long=long):
        for voice in voices:
            free()
            parts = [_trimmed(wav_to_pcm48(service.speak(part, voice))) for part in spoken.parts]
            for line in lines:
                for noise in noises:
                    free()
                    base = {"id": spoken.id, "kind": spoken.kind, "named": spoken.named, "voice": voice}
                    heard = on_line(
                        stream(parts, spoken.pause_s), line, noise, item_seed(spoken.id, voice, line, noise)
                    )
                    through(heard, base | {"noise": noise, "line": line, "matched": spoken.matched})
    for id_, kind, pcm in short_sounds():
        for line in lines:
            free()
            base = {"id": id_, "kind": kind, "named": False, "voice": "-", "noise": "-", "line": line}
            through(on_line(stream([pcm], 0.0), line, "clean"), base)
    return items


def call_summary(items: Sequence[CallItem]) -> dict[str, Any]:
    """`summary`'s counts per arm, and the turns: all, heard as no words, and of 1 s or less (WhisperKit's default
    decodes nothing in those); then the arms compared item by item (`paired`)."""
    arms = list(dict.fromkeys(item.arm for item in items))
    out: dict[str, Any] = {}
    for arm in arms:
        mine = [item for item in items if item.arm == arm]
        turns = [turn for item in mine for turn in item.turns]
        heard = [turn.heard_ms for turn in turns if turn.words >= 0] or [0]
        out[arm] = _counts(mine) | {
            "turns": {
                "all": len(turns),
                "no words": sum(turn.words == 0 for turn in turns),
                "1 s or less": sum(turn.seconds <= 1.0 for turn in turns),
                "whisper ms": {"p50": percentile(heard, 0.5), "p90": percentile(heard, 0.9)},
            },
        }
    return out | {"paired": paired(items)}


def call_table(result: dict[str, Any]) -> str:
    lines = [table(result), "", f"{'arm':<18} {'turns':>6} {'no words':>9} {'1 s or less':>12}"]
    for arm, r in result.items():
        if arm == "paired":
            continue
        t = r["turns"]
        whisper = f"whisper p50 {t['whisper ms']['p50']} ms, p90 {t['whisper ms']['p90']} ms"
        lines.append(f"{arm:<18} {t['all']:>6} {t['no words']:>9} {t['1 s or less']:>12}   {whisper}")
    return "\n".join(lines)


# the checks the Gemma test set shares (evals/jobs.py): another evals job, a call on this Mac
_JOB = JOB
_CALL_HELPER = CALL_HELPER
CALL_WAIT_S = WAIT_S
_listing = listing


def _her_name() -> str:
    try:
        return load_config(DEFAULT_CONFIG_PATH).name if DEFAULT_CONFIG_PATH.exists() else DEFAULT_NAME
    except Exception:  # an unreadable config: the default name, as a fresh install
        return DEFAULT_NAME


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.wake_harness", description=__doc__.split("\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--name", default=None, help="her name (default: the config's, else Estia)")
    parser.add_argument("--voices", default=",".join(DEFAULT_VOICES), help="Pocket TTS voices, comma-separated")
    parser.add_argument("--noises", default=",".join(NOISES), help=f"of {', '.join(NOISES)}")
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--force", action="store_true", help="run beside another evals job")
    parser.add_argument("--call", action="store_true", help="standby as a call hears it: the call's cut, turn by turn")
    parser.add_argument("--lines", default="phone", help=f"--call: of {', '.join(LINES)}")
    parser.add_argument("--hint", action="store_true", help="--call: also the arms with her name as the hint")
    parser.add_argument("--long", action="store_true", help="also LONG: speech without her name, for false wakes")
    args = parser.parse_args(argv)
    voices = [v for v in args.voices.split(",") if v]
    noises = [n for n in args.noises.split(",") if n]
    lines = [line for line in args.lines.split(",") if line]
    bad = [v for v in voices if v not in VOICES] + [n for n in noises if n not in NOISES]
    if bad := bad + [line for line in lines if line not in LINES]:
        print(f"wake harness: unknown voice, noise or line: {', '.join(bad)}", file=sys.stderr)
        return 2
    if not args.force:
        if jobs := other_jobs(_listing(_JOB.pattern), own=ancestors(os.getpid())):
            print(
                f"wake harness: another evals job runs (pid {', '.join(jobs)}); wait for it, or --force",
                file=sys.stderr,
            )
            return 3
        if call_running(_listing(_CALL_HELPER)):
            print("wake harness: a call is on: wait until it ends, or --force", file=sys.stderr)
            return 3
    service = VoiceService(args.url)
    try:
        health = service.health()
    except OtherAccountError:
        print(OTHER_ACCOUNT, file=sys.stderr)
        return 2
    if health.get("stt") != "ready" or health.get("tts") != "ready":
        print("wake harness: the voice service is not ready (listening and speaking)", file=sys.stderr)
        return 2
    if args.call:
        return _main_call(service, health, args, voices, noises, lines)
    if not health.get("hints"):
        print("wake harness: this voice service takes no hint: run the voice service from this branch", file=sys.stderr)
        return 2
    name = args.name or _her_name()
    started = time.monotonic()
    busy = (lambda: False) if args.force else (lambda: call_running(_listing(_CALL_HELPER)))
    items = run(service, name, voices=voices, noises=noises, long=args.long, busy=busy)
    result = summary(items)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    record = {
        "name": name,
        "voices": voices,
        "noises": noises,
        "long": args.long,
        "summary": result,
        "items": [asdict(i) for i in items],
    }
    path = out / f"wake-{stamp}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(table(result))
    print(f"\n{len(items)} transcriptions in {time.monotonic() - started:.0f} s; {path}")
    return 0


def _main_call(
    service: VoiceService,
    health: dict[str, object],
    args: argparse.Namespace,
    voices: Sequence[str],
    noises: Sequence[str],
    lines: Sequence[str],
) -> int:
    if health.get("turn") != "ready":
        print("wake harness: --call needs the voice service's turn detector (Smart Turn) ready", file=sys.stderr)
        return 2
    if args.hint and not health.get("hints"):
        print("wake harness: this voice service takes no hint: --hint needs the one from this branch", file=sys.stderr)
        return 2
    name = args.name or _her_name()
    started = time.monotonic()
    busy = (lambda: False) if args.force else (lambda: call_running(_listing(_CALL_HELPER)))
    arms = (*CALL_ARMS, *HINT_ARMS) if args.hint else CALL_ARMS
    items = run_call(service, name, voices=voices, noises=noises, lines=lines, arms=arms, long=args.long, busy=busy)
    result = call_summary(items)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    record = {
        "name": name,
        "voices": list(voices),
        "noises": list(noises),
        "lines": list(lines),
        "long": args.long,
        "summary": result,
        "items": [asdict(item) for item in items],
    }
    path = out / f"wake-call-{stamp}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(call_table(result))
    print(f"\n{len(items)} streams in {time.monotonic() - started:.0f} s; {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
