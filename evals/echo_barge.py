"""Barge-in on her own echo: her call lines, said through the call's own `CallAudio.say` with barge-in on, come back as
her echo on a quiet line, and the call decides as `CallManager._say_checked` does (the review of 26 Oct, R1-F5; checked
in on 27 Oct, CALLS-KNOWN-1, so it can be run again for a new voice).

No call, no microphone and no caller. A fake call helper on a socket pair paces the call in call time (a frame every
10 ms, drift-corrected): each of her outgoing frames comes back `ECHO_FRAMES` later (200 ms with the pump's cushion) at
1/`--level` of her level (3: the 24 Oct method's third; 5: a fifth), over a quiet line floor (seeded noise, `NOISE_RMS`).
After a stop, as on a call: the listen (`INTERRUPTION_END_S`), Whisper, `is_noise_transcript`, `is_own_voice` against
what she played, an acknowledgement, `is_interruption`; her own voice raises the level once (`Barge.her_own_voice`),
no words or her voice carries on from a little before the stop, at most `MAX_FALSE_BARGE` times a clip; a takeover is
her echo reaching Gemma as the caller's words. One `Barge` a voice and rule (one long call): its end line
(`Barge.summary`) per run. Her lines: every English canned call line (`prompts.call_lines`, her name `--name`, default
Estia) and three answers of the 24 Oct spike, 43 in all.

Run it alone, outside her calls: the voice service's engines are shared. It refuses to start beside another evals job
or during a call (`--force` to run anyway) and waits between lines while a call is on. The voice service must be one of
your own macOS account (hers answers only her own programs), never her live one: this branch's, built and run as you on
another port (`cd tools/voice && swift build -c release`, then `tools/voice/.build/release/voice-service --port 8152`,
with `--pocket 100M` for vera and the 100M's heart). Its health must say "vad": "ready" for the rule "speech".

    uv run python -m evals.echo_barge --voices heart,jane,af_heart --rules loudness,speech [--level 3]
    uv run python -m evals.echo_barge --report evals/out/echo-<UTC time>.jsonl

Output: evals/out/echo-<UTC time>.jsonl (a row per line: voice, rule, level, text, stops, verdicts, what Whisper heard
of her synthetic line; a totals row per voice and rule with `Barge.summary`), and a table per voice and rule: clips,
clips stopped, stops, verdicts (her own voice, no words, an acknowledgement, the caller's words), takeovers, and where
the first stop came (median seconds into the clip, the 0.3 s lead in it). English only.
"""

from __future__ import annotations

import argparse
import array
import asyncio
import collections
import functools
import json
import os
import random
import socket
import statistics
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import fields
from pathlib import Path
from typing import Any

from evals.jobs import CALL_HELPER, JOB, WAIT_S, ancestors, call_running, listing, other_jobs
from local_assistant import prompts
from local_assistant.call_audio import FRAME_BYTES, RATE, Barge, CallAudio, faint_noise, pcm48_to_wav16k, wav_to_pcm48
from local_assistant.calls import (
    INTERRUPTION_END_S,
    LEAD_MS,
    MAX_FALSE_BARGE,
    RESUME_BACK_S,
    asks,
    is_acknowledgement,
    is_interruption,
    is_noise_transcript,
    is_own_voice,
    played_part,
)
from local_assistant.voice import VoiceClient

# a voice service of your own, never her live one (8150) nor on her own Ollama's port (8151, ollama_build.HER_PORT;
# release review of 0.9.8, E14)
DEFAULT_URL = "http://127.0.0.1:8152"
OUT = Path(__file__).resolve().parent / "out"
RULES = ("loudness", "speech")
ECHO_FRAMES = 17  # + the pump's 3-frame cushion: her voice back 200 ms later
NOISE_RMS = 30.0
SPIKE = (  # answers of hers, as in the spike of 24 Oct
    "Two meetings tomorrow.",
    "It's 14:35 now. Your dentist appointment is tomorrow at 9:15, and it should be sunny, around 18 degrees.",
    "The pharmacy on Main Street closes at 8 tonight, so you still have about two hours. It's a ten-minute walk, "
    "or four minutes by car. Shall I remind you at 7:30?",
)


def lines(name: str = "Estia") -> list[str]:
    """Her English canned call lines with `name`, each once (a reminder's time as 7:30), then the spike's answers."""
    canned = prompts.call_lines("en", name=name)
    out: list[str] = []
    for field in fields(canned):
        value = getattr(canned, field.name)
        for text in value if isinstance(value, tuple) else (value,):
            out.append(text.replace("{time}", "7:30"))
    return out + list(SPIKE)


class Line:
    """The fake call helper: a frame in every 10 ms of call time (drift-corrected), her frame out each time; what comes
    in is her frame of `ECHO_FRAMES` before at `gain`, over the line's noise."""

    def __init__(self, sock: socket.socket, seed: int, gain: float) -> None:
        self.sock = sock
        self.rng = random.Random(seed)
        self.gain = gain
        self.stopped = False

    def noise(self) -> array.array[int]:
        return array.array("h", (int(self.rng.gauss(0, NOISE_RMS)) for _ in range(FRAME_BYTES // 2)))

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        reader, writer = await asyncio.open_unix_connection(sock=self.sock)
        delay: collections.deque[bytes] = collections.deque([bytes(FRAME_BYTES)] * ECHO_FRAMES)
        start, n = loop.time(), 0
        try:
            while not self.stopped:
                wait = start + n * 0.010 - loop.time()
                if wait > 0:
                    await asyncio.sleep(wait)
                her = array.array("h", delay.popleft())
                frame = self.noise()
                for i, x in enumerate(her):
                    frame[i] = max(-32768, min(32767, frame[i] + int(x * self.gain)))
                writer.write(frame.tobytes())
                await writer.drain()
                delay.append(await reader.readexactly(FRAME_BYTES))
                n += 1
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()


async def say_checked(
    audio: CallAudio, client: VoiceClient, pcm: bytes, text: str, lead: int, name: str
) -> dict[str, Any]:
    """`CallManager._say_checked` for one line of hers (`hers` = the line), its outcome counted."""
    hers = (text,)
    offset, false_alarms, carried_on, resumed = 0, 0, 0, False
    outcome: dict[str, Any] = {"stops": 0, "own": 0, "noise": 0, "ack": 0, "taken": "", "heard": []}
    while True:
        said = await audio.say(pcm[offset:], barge=True, fresh=not resumed)
        stop = offset + 2 * int(said.played_s * RATE)
        fraction = min(1.0, max(0, stop - lead) / max(1, len(pcm) - lead))
        if not said.barged:
            return outcome
        outcome["stops"] += 1
        clip, info = await audio.listen(
            preroll=said.preroll, floor=said.floor, end_silence_s=INTERRUPTION_END_S, max_s=60.0
        )
        heard = (await client.transcribe(pcm48_to_wav16k(clip), language="en", call=True)).text if clip else ""
        noise = is_noise_transcript(heard, float(info.get("sustained_s", 0.0)))
        own = not noise and is_own_voice(heard, played_part(hers, fraction))
        if own:
            audio.barge.her_own_voice(said.level)
        words = not noise and not own
        acknowledged = words and not asks(hers) and is_acknowledgement(heard, "en")
        caller = words and not acknowledged and is_interruption(heard, name=name, language="en", asking=asks(hers))
        verdict = "own" if own else "noise" if noise else "ack" if acknowledged else "caller" if caller else "words"
        outcome["heard"].append(
            {"at_s": round(stop / 2 / RATE, 2), "level": round(said.level), "heard": heard, "verdict": verdict}
        )
        resumed = True
        if acknowledged:
            audio.barge.acknowledgement()
            outcome["ack"] += 1
            carried_on += 1
            back = stop - 2 * int(RESUME_BACK_S * RATE)
            offset = back if back > offset and carried_on <= MAX_FALSE_BARGE else stop
            continue
        if own or noise:
            outcome["own" if own else "noise"] += 1
        if not caller:
            audio.barge.false_stop(said)
        if caller or false_alarms >= MAX_FALSE_BARGE:
            outcome["taken"] = "" if noise or own else heard
            return outcome
        false_alarms += 1
        offset = max(0, stop - 2 * int(RESUME_BACK_S * RATE))


async def run(
    client: VoiceClient,
    voices: Sequence[str],
    rules: Sequence[str],
    *,
    level: float,
    name: str,
    todo: Sequence[str],
    out: Path,
    busy: Callable[[], bool],
) -> list[dict[str, Any]]:
    """Every line of `todo` in each voice, by each rule, as one long call a voice and rule; rows to `out` as they come."""
    rows: list[dict[str, Any]] = []
    with out.open("a", encoding="utf-8") as file:

        def keep(row: dict[str, Any]) -> None:
            rows.append(row)
            file.write(json.dumps(row, ensure_ascii=False) + "\n")
            file.flush()

        for voice in voices:
            client.set_voice(voice)
            clips = {text: wav_to_pcm48(await client.speak(text)) for text in todo}
            for rule in rules:
                ours, theirs = socket.socketpair()
                line = Line(theirs, seed=sum(map(ord, f"{voice}/{rule}")), gain=1 / level)
                reader, writer = await asyncio.open_unix_connection(sock=ours)
                detect = functools.partial(client.vad, stream=uuid.uuid4().hex) if rule == "speech" else None
                audio = CallAudio(reader, writer, Barge(rule, detect))
                tasks = [asyncio.create_task(line.run()), asyncio.create_task(audio.pump())]
                await asyncio.sleep(0.5)  # the line's floor before her first line
                for text in todo:
                    while busy():
                        print("echo barge: a call is on: waiting", flush=True)
                        await asyncio.sleep(WAIT_S)
                    lead = faint_noise(LEAD_MS)
                    outcome = await say_checked(audio, client, lead + clips[text], text, len(lead), name)
                    keep({"voice": voice, "rule": rule, "level": level, "text": text, **outcome})
                    await asyncio.sleep(0.6)  # a pause between her lines: the echo's tail dies
                keep({"voice": voice, "rule": rule, "level": level, "summary": audio.barge.summary()})
                line.stopped = True
                audio.close()
                audio.barge.close()
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.sleep(0.2)
    return rows


def report(rows: Sequence[dict[str, Any]]) -> str:
    """A table per voice, rule and level: clips, clips stopped, stops, verdicts, takeovers, the first stop's place."""
    by: dict[tuple[str, str, float], list[dict[str, Any]]] = collections.defaultdict(list)
    ends: dict[tuple[str, str, float], str] = {}
    for row in rows:
        key = (str(row["voice"]), str(row["rule"]), float(row.get("level", 3)))
        if "summary" in row:
            ends[key] = str(row["summary"])
        elif "text" in row:
            by[key].append(row)
    out = []
    for (voice, rule, level), clips in by.items():
        verdicts = collections.Counter(h["verdict"] for r in clips for h in r["heard"])
        first = [r["heard"][0]["at_s"] for r in clips if r["heard"]]
        taken = sum(1 for r in clips if r["taken"])
        stopped = sum(1 for r in clips if r["stops"])
        where = f"{statistics.median(first):.2f} s" if first else "-"
        out.append(
            f"{voice:9} {rule:8} 1/{level:g}: clips {len(clips)}, stopped {stopped}, stops "
            f"{sum(r['stops'] for r in clips)}, verdicts {dict(sorted(verdicts.items()))}, taken as the caller's "
            f"{taken}, first stop at {where} (median)"
        )
        if (voice, rule, level) in ends:
            out.append(f"{'':9} {ends[(voice, rule, level)]}")
    return "\n".join(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.echo_barge", description=__doc__.split("\n")[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--voices", default="heart,jane,af_heart", help="voices, comma-separated")
    parser.add_argument("--rules", default=",".join(RULES), help=f"of {', '.join(RULES)}")
    parser.add_argument("--level", type=float, default=3.0, help="her echo at 1/LEVEL of her level (3, 5)")
    parser.add_argument("--name", default="Estia", help="her name in her lines")
    parser.add_argument("--limit", type=int, default=0, help="only the first LIMIT lines (0: all)")
    parser.add_argument("--out", default=str(OUT))
    parser.add_argument("--force", action="store_true", help="run beside another evals job or a call")
    parser.add_argument("--report", default="", help="only the table of an earlier run's file")
    args = parser.parse_args(argv)
    if args.report:
        path = Path(args.report)
        print(report([json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]))
        return 0
    voices = [v for v in args.voices.split(",") if v]
    rules = [r for r in args.rules.split(",") if r]
    if bad := [r for r in rules if r not in RULES] + ([] if args.level >= 1 else [f"level {args.level:g}"]):
        print(f"echo barge: unknown rule or level: {', '.join(bad)}", file=sys.stderr)
        return 2
    if not args.force:
        if jobs := other_jobs(listing(JOB.pattern), own=ancestors(os.getpid())):
            print(
                f"echo barge: another evals job runs (pid {', '.join(jobs)}); wait for it, or --force", file=sys.stderr
            )
            return 3
        if call_running(listing(CALL_HELPER)):
            print("echo barge: a call is on: wait until it ends, or --force", file=sys.stderr)
            return 3
    client = VoiceClient(args.url, voice=voices[0], speed=1.05)
    health = asyncio.run(client.health()) or {}
    needed = ("stt", "tts", *(("vad",) if "speech" in rules else ()))
    if not all(health.get(part) == "ready" for part in needed):
        print(f"echo barge: the voice service is not ready ({', '.join(needed)}): {health}", file=sys.stderr)
        return 2
    todo = lines(args.name)
    todo = todo[: args.limit] if args.limit else todo
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"echo-{time.strftime('%Y%m%d-%H%M%S', time.gmtime())}.jsonl"
    busy = (lambda: False) if args.force else (lambda: call_running(listing(CALL_HELPER)))
    started = time.monotonic()
    rows = asyncio.run(run(client, voices, rules, level=args.level, name=args.name, todo=todo, out=path, busy=busy))
    print(report(rows))
    print(f"\n{len(todo)} lines a voice and rule in {time.monotonic() - started:.0f} s; {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
