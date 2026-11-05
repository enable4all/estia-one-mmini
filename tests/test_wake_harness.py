"""The offline wake harness (evals/wake_harness.py): its phrases, sounds and scoring, and its talk with the voice
service, on a stand-in; never the real Whisper or Pocket TTS here (the harness itself is run by hand, alone on the
GPU)."""

import array
import dataclasses
import io
import json
import math
import re
import wave
from pathlib import Path

import pytest

from evals import wake_harness
from evals.jobs import call_running, other_jobs
from evals.wake_harness import (
    ARMS,
    CALL_ARMS,
    EARLY_PAUSE_S,
    FULL_PAUSE_S,
    HINT_ARMS,
    NOISES,
    PHONE_LEVEL,
    CallItem,
    Turn,
    VoiceService,
    band,
    call_phrases,
    call_summary,
    call_table,
    cut,
    false_wake_bound,
    item_seed,
    noisy,
    on_line,
    paired,
    phrases,
    run,
    run_call,
    sounds,
    standby_turns,
    stream,
    summary,
    table,
    woke,
)
from local_assistant import ollama_build
from local_assistant.call_audio import FRAME_BYTES, RATE, wav_to_pcm48
from local_assistant.config import Config
from local_assistant.voice import AFCONVERT
from local_assistant.wake import says_name
from tests.fakes.fake_voice import FakeVoiceService, wav


@pytest.mark.parametrize("name", ["Tiffany", "Estia", "Juniper"])
def test_her_name_is_said_at_the_start_in_the_middle_at_the_end_and_alone_and_the_others_never_say_it(
    name: str,
) -> None:
    listed = phrases(name)
    named = [phrase for phrase in listed if phrase.named]
    others = [phrase for phrase in listed if not phrase.named]
    assert {phrase.kind for phrase in named} == {"start", "middle", "end", "alone"}
    assert all(says_name(phrase.text, name) for phrase in named)
    # a phrase without her name that the text check itself wakes on is kept, marked, and counted apart (review E4)
    assert others and all(phrase.matched == says_name(phrase.text, name) for phrase in others)
    assert {"tv", "other name"} <= {phrase.kind for phrase in others}
    assert len({phrase.id for phrase in listed}) == len(listed)


def test_her_own_names_near_sounds_are_in_the_set() -> None:
    near = {phrase.text for phrase in phrases("Tiffany") if phrase.kind == "near"}
    assert any("Stephanie" in text for text in near) and any("Epiphany" in text for text in near)
    assert any(
        "fiesta" in text.lower() for phrase in phrases("Estia") if phrase.kind == "near" for text in [phrase.text]
    )


def test_the_sounds_without_speech_are_the_same_every_run_and_never_clip() -> None:
    made = sounds()
    assert {kind for _, kind, _ in made} == {"silence", "room noise", "hum", "clicks"}
    again = sounds()
    for (one, kind, pcm), (_, _, same) in zip(made, again, strict=True):
        samples = array.array("h", pcm)
        assert pcm == same and len(pcm) % 2 == 0 and len(samples) >= RATE, one
        assert max(abs(x) for x in samples) < 32_000
        if kind == "silence":
            assert not any(samples)


def test_line_noise_is_mixed_in_without_clipping_and_keeps_the_length() -> None:
    speech = array.array("h", [30_000, -30_000] * RATE).tobytes()
    assert noisy(speech, "clean") == speech
    for noise in NOISES:
        mixed = noisy(speech, noise)
        assert len(mixed) == len(speech) and mixed == noisy(speech, noise)
        assert max(abs(x) for x in array.array("h", mixed)) <= 32_767


@pytest.mark.parametrize("count", [27, 49, 101, RATE + 27, 3 * RATE + 1])
def test_line_noise_fits_a_clip_of_any_length(count: int) -> None:
    """A spoken clip of any sample count gets noise of exactly its length (22 Oct: int(RATE * seconds) dropped a sample
    for 27 samples, 49, ..., and the mix stopped on the first room item)."""
    speech = array.array("h", [1_000] * count).tobytes()
    for noise in NOISES:
        assert len(noisy(speech, noise)) == len(speech)


def test_a_wake_is_standbys_own_decision() -> None:
    assert woke("Tiffany, are you there?", "Tiffany") and woke("Tiffanie.", "Tiffany")
    assert not woke("Stephanie, are you there?", "Tiffany")
    assert not woke("*music*", "Tiffany") and not woke("", "Tiffany")


class Scripted:
    """A voice service stand-in: Whisper hears each phrase exactly, and with the hint writes her name into anything
    (the known risk: a prompt written back in silence or noise)."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.said = ""
        self.spoken: list[tuple[str, str]] = []
        self.hints: list[str] = []

    def health(self) -> dict[str, object]:
        return {"stt": "ready", "tts": "ready", "hints": True}

    def speak(self, text: str, voice: str) -> bytes:
        self.said = text
        self.spoken.append((text, voice))
        return wav(0.5, 24_000)

    def transcribe(self, audio: bytes, hint: str) -> str:
        self.hints.append(hint)
        heard, self.said = self.said, ""
        return f"{self.name}. {heard}" if hint else heard


def test_a_run_counts_wakes_and_false_wakes_for_each_arm_in_turn() -> None:
    service = Scripted("Tiffany")
    items = run(service, "Tiffany", voices=("heart", "michael"), noises=("clean", "room"))
    listed = phrases("Tiffany")
    named = len([phrase for phrase in listed if phrase.named])
    each = len(listed) * 2 * 2 + len(sounds())  # phrases by voice and noise; the sounds once
    assert len(items) == each * len(ARMS)
    assert service.hints == ["", "Tiffany"] * each  # the arms in turn, item by item
    result = summary(items)
    plain, hinted = result["no hint"], result["hint"]
    assert plain["named"] == {"items": named * 4, "woke": named * 4}
    assert {key: plain["unnamed"][key] for key in ("items", "woke")} == {"items": each - named * 4, "woke": 0}
    assert hinted["unnamed"]["woke"] == each - named * 4  # every false wake shows
    assert plain["by kind"]["near"]["woke"] == 0 and hinted["by kind"]["silence"]["woke"] == 1
    assert {voice for _, voice in service.spoken} == {"heart", "michael"}


def test_the_client_sends_her_name_as_the_hint_and_the_calls_16k_audio() -> None:
    with FakeVoiceService() as fake:
        fake.transcript = "Tiffany, are you there?"
        service = VoiceService(fake.url)
        spoken = service.speak("Tiffany, are you there?", "eve")
        assert spoken.startswith(b"RIFF")
        assert service.transcribe(wav(0.5), "Tiffany") == "Tiffany, are you there?"
        assert service.transcribe(wav(0.5), "") == "Tiffany, are you there?"
        # call=1, as a call's audio goes since 0.9.7 (the service keeps a bare "Bye."; review E4)
        assert fake.paths()[-2:] == ["/v1/audio/transcriptions?hint=Tiffany&call=1", "/v1/audio/transcriptions?call=1"]
        assert json.loads(fake.requests[0][3]) == {"input": "Tiffany, are you there?", "voice": "eve"}


def test_the_harness_runs_and_keeps_counts_only_never_audio(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with FakeVoiceService() as fake:
        fake.health["hints"] = True
        fake.transcript = "Thank you."
        # --force: never this Mac's own process table (another job's evals may run beside the tests)
        args = ["--url", fake.url, "--name", "Tiffany", "--voices", "heart", "--noises", "clean", "--force"]
        code = wake_harness.main([*args, "--out", str(tmp_path)])
    assert code == 0
    [written] = list(tmp_path.iterdir())
    result = json.loads(written.read_text(encoding="utf-8"))
    assert result["summary"]["no hint"]["named"]["woke"] == 0 and result["name"] == "Tiffany"
    assert not any(key in json.dumps(result) for key in ("RIFF", 'audio":'))
    table = capsys.readouterr().out
    assert "wakes" in table and "false wakes" in table


def test_the_harness_refuses_a_voice_service_without_the_hint(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with FakeVoiceService() as fake:  # an older service: its health has no "hints"
        code = wake_harness.main(["--url", fake.url, "--voices", "heart", "--out", str(tmp_path), "--force"])
    assert code == 2 and list(tmp_path.iterdir()) == []
    assert "hint" in capsys.readouterr().err


def test_a_voice_service_of_another_account_is_named_with_the_way_to_run_one_of_yours(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Owner, 25 Oct: her voice service answers only programs running as her own macOS account (403): the harness,
    run as you, says how to run this branch's voice service as you on another port."""
    with FakeVoiceService() as fake:
        fake.status["/health"] = 403
        code = wake_harness.main(["--url", fake.url, "--voices", "heart", "--out", str(tmp_path), "--force"])
    assert code == 2 and list(tmp_path.iterdir()) == []
    err = capsys.readouterr().err
    assert "answers only programs of its own macOS account" in err
    assert "tools/voice/.build/release/voice-service --port 8152" in err and "--url http://127.0.0.1:8152" in err


def ports(text: str) -> set[int]:
    """The ports a text names: `--port N` or `127.0.0.1:N`."""
    return {int(flag or address) for flag, address in re.findall(r"--port (\d+)|127\.0\.0\.1:(\d+)", text)}


def test_the_harness_default_port_is_not_her_ollamas() -> None:
    """Release review of 0.9.8, E14: the harnesses' voice service of your own ran on 8151, her own Ollama's port on a
    0.9.8 install (ollama_build.HER_PORT): there it cannot start, or keeps her Ollama from starting."""
    from evals import echo_barge

    suggested = ports(echo_barge.DEFAULT_URL) | ports(echo_barge.__doc__ or "") | ports(wake_harness.OTHER_ACCOUNT)
    suggested |= ports(wake_harness.__doc__ or "") - {8150}  # the wake harness's default: the voice service's own
    assert suggested == {8152} and not suggested & {ollama_build.HER_PORT, 8150}


def test_another_real_model_job_is_named_and_its_own_process_is_not() -> None:
    listing = "\n".join(
        [
            "4100 uv run python -m evals.gemma_suite --tries 5",
            "4200 /usr/local/estia/current/.build/release/voice-service",
            "4300 python -m evals.wake_harness --name Tiffany",
        ]
    )
    assert other_jobs(listing, own={4300, 4299}) == ["4100"]
    # its own uv parent too ("uv run python -m evals.wake_harness")
    assert (
        other_jobs("4299 uv run python -m evals.wake_harness\n4300 python -m evals.wake_harness", own={4300, 4299})
        == []
    )


CALL_HELPER = "5100 /var/estia/data/vendor/signal-call-tunnel/signal-call-tunnel/target/release/signal-call-tunnel"


def test_a_call_on_this_mac_is_seen_by_its_call_helper() -> None:
    assert call_running(f"4100 uv run python -m evals.gemma_suite\n{CALL_HELPER} --socket x")
    assert not call_running("4200 /usr/local/estia/current/.build/release/voice-service")


def test_a_run_waits_while_a_call_is_on_and_goes_on_after_it() -> None:
    """The voice service is the owner's live one: its one engine queue would hold a call's turns behind the harness's
    transcriptions. While a call is on (its helper runs) the run waits between items, then goes on."""
    service = Scripted("Tiffany")
    calls_on = iter([True, True])
    waits: list[float] = []
    items = run(
        service,
        "Tiffany",
        voices=("heart",),
        noises=("clean",),
        busy=lambda: next(calls_on, False),
        wait=waits.append,
    )
    assert waits == [5.0, 5.0] and len(items) == (len(phrases("Tiffany")) + len(sounds())) * len(ARMS)


def test_the_harness_refuses_to_start_during_a_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(wake_harness, "_listing", lambda pattern: CALL_HELPER)
    with FakeVoiceService() as fake:
        fake.health["hints"] = True
        code = wake_harness.main(["--url", fake.url, "--voices", "heart", "--out", str(tmp_path)])
    assert code == 3 and list(tmp_path.iterdir()) == [] and fake.requests == []
    assert "call" in capsys.readouterr().err


# --call: standby as a call hears it (owner, 23 Oct: "hello Tiffany" said with a pause came to Whisper as clips of
# 0.7-0.8 s, chopped by the early end, and each was heard as no words)


def tone(seconds: float, hz: float, level: float = 8_000.0) -> bytes:
    count = round(RATE * seconds)
    return array.array("h", (round(level * math.sin(2 * math.pi * hz * i / RATE)) for i in range(count))).tobytes()


def level_of(pcm: bytes) -> float:
    samples = array.array("h", pcm)
    return math.sqrt(sum(x * x for x in samples) / len(samples))


def test_the_phone_lines_band_keeps_a_voice_and_cuts_the_highs_and_the_rumble() -> None:
    voice, high, rumble = tone(0.5, 1_000.0), tone(0.5, 7_000.0), tone(0.5, 50.0)
    assert len(band(voice)) == len(voice) and band(voice) == band(voice)
    assert level_of(band(voice)) > 0.8 * level_of(voice)
    assert level_of(band(high)) < 0.1 * level_of(high) and level_of(band(rumble)) < 0.2 * level_of(rumble)


@pytest.mark.skipif(not Path(AFCONVERT).exists(), reason="macOS's afconvert codes the phone line's Opus")
def test_the_phone_line_is_opus_coded_and_keeps_the_length() -> None:
    voice = tone(0.8, 440.0) + tone(0.4, 1_200.0, 3_000.0)
    coded = on_line(voice, "phone", "clean")
    assert len(coded) == len(voice) and coded != band(voice)
    # the owner's level: the loudest 10 ms frame's RMS, which the call's log calls its peak
    loudest = max(level_of(coded[i : i + FRAME_BYTES]) for i in range(0, len(coded), FRAME_BYTES))
    assert 0.7 * PHONE_LEVEL < loudest < 1.3 * PHONE_LEVEL
    assert on_line(voice, "clean", "clean") == voice


def test_the_owners_phrases_pause_after_the_first_part_longer_than_the_short_pause_and_shorter_than_the_full() -> None:
    listed = call_phrases("Tiffany")
    owner = [spoken for spoken in listed if spoken.kind == "owner paused"]
    assert owner and all(EARLY_PAUSE_S < spoken.pause_s < FULL_PAUSE_S for spoken in owner)
    assert {spoken.parts for spoken in owner} >= {
        ("Tiffany.", "Are you there?"),
        ("Hello.", "Tiffany."),
        ("One, two, three, four, five, six, seven.", "Tiffany."),
    }
    whole = {spoken.parts for spoken in listed if spoken.kind == "owner"}
    assert ("Hello. Tiffany.",) in whole or ("Hello Tiffany.",) in whole
    # her name alone, once, at Pocket TTS's own pace (it has no speed: the brisk ones went with Kokoro, 3 Nov 2026)
    assert [spoken.parts for spoken in listed if spoken.kind == "alone once"] == [("Tiffany.",)]
    assert all(says_name(" ".join(spoken.parts), "Tiffany") == spoken.named for spoken in listed)
    assert len({spoken.id for spoken in listed}) == len(listed)


def test_the_harness_pauses_are_the_configs_defaults() -> None:
    fields = {field.name: field.default for field in dataclasses.fields(Config)}
    assert fields["calls_early_end_pause_seconds"] == EARLY_PAUSE_S
    assert fields["calls_end_of_turn_seconds"] == FULL_PAUSE_S


class Sure:
    """Smart Turn's stand-in: every pause sounds like a finished sentence."""

    def __init__(self) -> None:
        self.windows = 0

    def turn(self, window: bytes) -> float | str:
        self.windows += 1
        return 0.97


async def test_the_calls_early_end_cuts_a_paused_phrase_into_short_clips_and_the_full_pause_keeps_it_whole() -> None:
    word = tone(0.35, 300.0)
    pause = bytes(2 * round(RATE * 0.8))
    quiet = bytes(2 * round(RATE * 0.5))
    pcm = quiet + word + pause + word + quiet + bytes(2 * round(RATE * FULL_PAUSE_S))
    judge = Sure()
    early = await cut(pcm, judge.turn)
    whole = await cut(pcm, None)
    assert len(early) == 2 and all(len(clip) / (2 * RATE) < 1.0 for clip, _ in early) and judge.windows >= 2
    assert [info["ended"] for _, info in early] == ["sure", "sure"]
    assert len(whole) == 1 and len(whole[0][0]) / (2 * RATE) > 1.5


# a level per text, all above 2/3 of the highest: the call's resampling never turns one text's level into another's
LEVELS = tuple(20_011 + 211 * n for n in range(44))


class Line:
    """A voice service stand-in for --call: each text is a square wave of its own level (0.3 s a word), Whisper hears
    the texts whose levels are in a clip, in order, and like WhisperKit's default (its windowClipTime) nothing in a
    clip of 1 s or less unless `short` (the fixed service); with the hint it writes her name into anything."""

    def __init__(self, name: str, *, short: bool = False) -> None:
        self.name, self.short = name, short
        self.levels: dict[int, str] = {}
        self.hints: list[str] = []

    def health(self) -> dict[str, object]:
        return {"stt": "ready", "tts": "ready", "turn": "ready", "hints": True}

    def speak(self, text: str, voice: str) -> bytes:
        level = next((lv for lv, said in self.levels.items() if said == text), LEVELS[len(self.levels)])
        self.levels[level] = text
        count = round(24_000 * 0.3 * max(1, len(text.split())))
        samples = array.array("h", (level if (i // 12) % 2 else -level for i in range(count)))
        out = io.BytesIO()
        with wave.open(out, "wb") as file:
            file.setnchannels(1)
            file.setsampwidth(2)
            file.setframerate(24_000)
            file.writeframes(samples.tobytes())
        return out.getvalue()

    def transcribe(self, audio: bytes, hint: str) -> str:
        self.hints.append(hint)
        with wave.open(io.BytesIO(audio)) as file:
            samples = array.array("h", file.readframes(file.getnframes()))
        if len(samples) / 16_000 <= 1.0 and not self.short:
            return ""
        counts: dict[int, int] = {}
        order: list[int] = []
        for x in samples:
            if abs(x) in self.levels:
                counts[abs(x)] = counts.get(abs(x), 0) + 1
                if counts[abs(x)] == 50:
                    order.append(abs(x))
        heard = " ".join(self.levels[level] for level in order)
        return f"{self.name}. {heard}".strip() if hint else heard

    def turn(self, window: bytes) -> float | str:
        return 0.97


def test_a_call_run_misses_her_name_in_short_clips_and_the_full_pause_or_the_fixed_service_hears_it() -> None:
    service = Line("Tiffany")
    items = run_call(service, "Tiffany", voices=("heart",), noises=("clean",), lines=("clean",))
    by = {(item.id, item.arm): item for item in items}
    hello = [key for key in by if key[0].startswith("owner-hello-") and not key[0].endswith("whole")]  # paused
    assert hello and all(not by[(id_, "early end")].woke for id_, arm in hello if arm == "early end")
    assert all(by[(id_, "full pause")].woke for id_, _ in hello)
    chopped = by[(hello[0][0], "early end")]
    assert [turn.words for turn in chopped.turns] == [0, 0]  # the owner's log: two pieces, no words each
    assert all(turn.seconds < 1.0 for turn in chopped.turns)
    result = call_summary(items)
    assert result["early end"]["turns"]["no words"] > 0 and result["full pause"]["unnamed"]["woke"] == 0
    assert set(service.hints) == {""}
    fixed = run_call(Line("Tiffany", short=True), "Tiffany", voices=("heart",), noises=("clean",), lines=("clean",))
    assert all(item.woke for item in fixed if item.named)
    assert not [item.id for item in fixed if item.woke and not item.named]


async def test_standby_holds_her_name_alone_and_joins_the_request_after_its_pause() -> None:
    """As calls.py (review of 0.9.7, C2a): in standby an early end after her name alone waits out the full pause, and
    what the caller says in it is joined to the name: one turn, her name heard."""
    service = Line("Tiffany", short=True)
    name, request = (wav_to_pcm48(service.speak(text, "heart")) for text in ("Tiffany", "what time is it"))
    turns = await standby_turns(service, stream([name, request], 0.8), "Tiffany", early=True, hint="")
    assert [(turn.heard, turn.woke) for turn in turns] == [("Tiffany what time is it", True)]


def test_a_call_run_with_the_hint_sends_her_name_only_in_its_hint_arms() -> None:
    service = Line("Tiffany", short=True)
    arms = (*CALL_ARMS, *HINT_ARMS)
    items = run_call(service, "Tiffany", voices=("heart",), noises=("clean",), lines=("clean",), arms=arms)
    assert {item.arm for item in items} == {arm for arm, _, _ in arms}
    assert set(service.hints) == {"", "Tiffany"}
    hinted = [item for item in items if item.arm.endswith("hint")]
    assert any(item.woke for item in hinted if not item.named)  # the stand-in writes her name into anything


def test_the_call_harness_keeps_counts_and_synthetic_words_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with FakeVoiceService() as fake:
        fake.turn = "ready"
        fake.transcript = "Tiffany."
        args = ["--call", "--url", fake.url, "--name", "Tiffany", "--voices", "heart", "--noises", "clean"]
        code = wake_harness.main([*args, "--lines", "clean", "--force", "--out", str(tmp_path)])
    assert code == 0
    [written] = list(tmp_path.iterdir())
    assert written.name.startswith("wake-call-")
    result = json.loads(written.read_text(encoding="utf-8"))
    assert result["summary"]["early end"]["named"]["woke"] > 0 and result["lines"] == ["clean"]
    assert not any(key in json.dumps(result) for key in ("RIFF", 'audio":'))
    assert "/turn" in fake.paths()
    assert "no words" in capsys.readouterr().out


def test_the_call_harness_needs_the_turn_detector(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    with FakeVoiceService() as fake:  # no "turn" in its health: no early end to measure
        code = wake_harness.main(["--call", "--url", fake.url, "--voices", "heart", "--out", str(tmp_path), "--force"])
    assert code == 2 and list(tmp_path.iterdir()) == []
    assert "turn" in capsys.readouterr().err


# review E4 (0.9.7): the text check's own false wakes were dropped from the set, one noise seed served every item, 0
# false wakes in some minutes of quiet audio bounded nothing much, the arms were compared by totals, and the harness
# sent no call=1


def test_a_negative_the_text_check_wakes_on_is_kept_and_counted_apart(monkeypatch: pytest.MonkeyPatch) -> None:
    near = {**wake_harness.NEAR, "tiffany": ("Tiffanie",)}  # one letter off: the text check takes it for her name
    monkeypatch.setattr(wake_harness, "NEAR", near)
    [kept] = [phrase for phrase in phrases("Tiffany") if phrase.kind == "near"]
    assert kept.matched and not kept.named
    items = run(Scripted("Tiffany"), "Tiffany", voices=("heart",), noises=("clean",))
    result = summary(items)["no hint"]
    assert result["matched"] == {"items": 1, "woke": 1}  # the text check's own false wake
    assert result["unnamed"]["woke"] == 0  # Whisper's false wakes: none


def test_each_item_gets_its_own_noise_the_same_in_every_run() -> None:
    speech = array.array("h", [1_000] * RATE).tobytes()
    one, two = item_seed("tv-news", "heart", "room"), item_seed("tv-goal", "heart", "room")
    assert one != two and one == item_seed("tv-news", "heart", "room")
    assert noisy(speech, "room", one) != noisy(speech, "room", two)


def test_false_wakes_are_given_per_hour_with_their_upper_bound() -> None:
    """0 false wakes in T hours bounds the rate below about 3/T an hour (95 %); 12 minutes of sound, about 15."""
    assert false_wake_bound(0, 3600) == pytest.approx(2.996, abs=0.01)
    assert false_wake_bound(0, 12 * 60) == pytest.approx(14.98, abs=0.05)
    assert false_wake_bound(2, 3600) == pytest.approx(6.296, abs=0.01)  # Poisson's exact 95 % bound for 2
    items = run(Scripted("Tiffany"), "Tiffany", voices=("heart",), noises=("clean",))
    unnamed = summary(items)["no hint"]["unnamed"]
    assert unnamed["seconds"] > 0 and unnamed["per hour under"] == pytest.approx(3 * 3600 / unnamed["seconds"], 0.01)
    assert "an hour" in table(summary(items))


def test_the_long_negative_set_adds_speech_without_her_name() -> None:
    long = phrases("Tiffany", long=True)
    added = [phrase for phrase in long if phrase.kind == "long"]
    assert len(added) >= 30 and not any(phrase.named for phrase in added)
    assert len(long) == len(phrases("Tiffany")) + len(added)
    assert sum(len(phrase.text.split()) for phrase in added) >= 400


def test_the_arms_are_compared_item_by_item() -> None:
    """McNemar's exact test on the items one arm woke and the other did not: 22 Oct's "Estia" call run, 4 only with
    the early end and 18 only with the full pause."""

    def item(id_: str, arm: str, woke: bool) -> CallItem:
        return CallItem(id_, "start", True, "heart", "clean", "phone", arm, woke)

    items = [item(f"a{n}", "early end", n < 4) for n in range(22)] + [
        item(f"a{n}", "full pause", n >= 4) for n in range(22)
    ]
    found = paired(items)["early end vs full pause"]
    assert (found["only the first"], found["only the second"]) == (4, 18)
    assert found["p"] == pytest.approx(0.004344, abs=1e-5)
    assert "only the first 4, only the second 18" in call_table(call_summary(items))


def test_a_call_run_prints_whispers_share_of_her_wait_at_the_median_and_90th_percentile() -> None:
    """Review of 26 Oct (eval-methods-6): the caller's wait before her first word starts with the end of the turn and
    Whisper's time on it; --call prints Whisper's time per turn at the median and the 90th percentile, per arm."""
    turns = [Turn(1.2, 3, "hello", False, heard_ms=ms) for ms in (100, 200, 300, 400, 900)]
    items = [CallItem("a1", "start", True, "heart", "clean", "phone", "early end", False, turns=turns)]
    result = call_summary(items)
    assert result["early end"]["turns"]["whisper ms"] == {"p50": 300, "p90": 900}
    assert "whisper p50 300 ms, p90 900 ms" in call_table(result)
    assert call_summary([CallItem("a2", "start", True, "heart", "clean", "phone", "full pause", False)])["full pause"][
        "turns"
    ]["whisper ms"] == {"p50": 0, "p90": 0}
