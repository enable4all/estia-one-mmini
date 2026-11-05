import array
import asyncio
import hashlib
import http.client
import json
import math
import re
import socket
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from local_assistant import prompts, voice
from local_assistant.config import (
    LANGUAGE_VOICE,
    POCKET_LANGUAGES,
    POCKET_ORDER,
    POCKET_VOICES,
    has_voice,
    pocket_voice,
)
from local_assistant.part_status import PartStatus, Record
from local_assistant.voice import (
    COMMANDS,
    POCKET_FILES,
    POCKET_FILES_100M,
    POCKET_PROGRAM,
    VAD_TIMEOUT,
    Transcript,
    VoiceClient,
    VoiceError,
    VoiceNoLanguageError,
    VoiceNotSaidError,
    VoiceTooLongError,
    VoiceUnavailableError,
    VoiceUnreadableError,
    detector_state,
    peer_is_ours,
    pocket_files,
    sentences,
    sniff,
    spoken_text,
    to_m4a,
    without_stage_directions,
)
from tests.fakes.fake_voice import FakeVoiceService, wav

M4A = b"\x00\x00\x00\x1cftypM4A " + bytes(2000)  # an iPhone voice note starts like this
ADTS = b"\xff\xf1\x50\x80" + bytes(2000)  # an Android one like this


def closed_port_url() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}"


def test_the_first_bytes_decide_what_audio_it_is() -> None:
    assert sniff(M4A) == "m4a"
    assert sniff(ADTS) == "aac"
    assert sniff(b"\xff\xf9\x5c\x60" + bytes(10)) == "aac"
    assert sniff(b"OggS\x00\x02" + bytes(10)) == "ogg"
    assert sniff(b"RIFF\x24\x00\x00\x00WAVEfmt ") == "wav"
    assert sniff(b"ID3\x04\x00" + bytes(10)) == "mp3"
    assert sniff(b"\xff\xfb\x90\x44" + bytes(10)) == "mp3"
    assert sniff(b"%PDF-1.7") is None
    assert sniff(b"\x89PNG\r\n\x1a\n") is None
    assert sniff(b"") is None


async def test_transcribe_sends_the_bytes_and_returns_the_text() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart")
        assert await client.transcribe(M4A) == Transcript("Turn off the lamp.", 2.4)
        method, path, headers, body = service.requests[0]
        assert (method, path, body) == ("POST", "/v1/audio/transcriptions", M4A)
        assert headers["content-type"] == "application/octet-stream"


@pytest.mark.parametrize(
    ("status", "error"),
    [(413, VoiceTooLongError), (415, VoiceUnreadableError), (422, VoiceUnreadableError), (503, VoiceUnavailableError)],
)
async def test_service_refusals_become_errors(status: int, error: type[VoiceError]) -> None:
    with FakeVoiceService() as service:
        service.status["/v1/audio/transcriptions"] = status
        with pytest.raises(error):
            await VoiceClient(service.url, voice="heart").transcribe(M4A)


async def test_a_service_that_is_not_running_is_unavailable() -> None:
    client = VoiceClient(closed_port_url(), voice="heart")
    with pytest.raises(VoiceUnavailableError):
        await client.transcribe(M4A)
    assert await client.health() is None


async def test_speak_sends_the_text_and_the_voice_and_asks_for_no_other_voice() -> None:
    """Owner, 3 Nov 2026: Pocket TTS is her only voice. Every speech request asks for no Kokoro ("kokoro": false), so a
    voice service that still has it never has it say a line; one without it ignores the field. No speed: Pocket TTS
    has none (only Kokoro and Supertonic-3 read it)."""
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="eve")
        assert await client.speak("The lamp is off.") == service.speech
        method, path, headers, body = service.requests[0]
        assert (method, path, headers["content-type"]) == ("POST", "/v1/audio/speech", "application/json")
        assert json.loads(body) == {"input": "The lamp is off.", "voice": "eve", "kokoro": False}
        assert await client.health() == {"stt": "ready", "tts": "ready"}


async def test_speech_that_fails_is_an_error() -> None:
    with FakeVoiceService() as service:
        service.status["/v1/audio/speech"] = 500
        with pytest.raises(VoiceError):
            await VoiceClient(service.url, voice="heart").speak("Hello there.")


def test_spoken_text_flattens_markdown() -> None:
    answer = "## Today\n- Buy **milk**\n- Call [Anna](https://example.com/anna)\n\nThat's all for now 🙂"
    assert spoken_text(answer) == "Today. Buy milk. Call Anna. That's all for now."


def test_spoken_text_never_says_markup_or_a_struck_word() -> None:
    """The same reading as the text styles (signal_text): single *italics* and ~~struck~~ words too; a word crossed
    out is not meant, so it is not said."""
    answer = "**Today**: the *dentist* at 09:30 *(Prinsengracht 12)*, then dinner with ~~Bob~~ Anna."
    assert spoken_text(answer) == "Today: the dentist at 09:30 (Prinsengracht 12), then dinner with Anna."
    assert spoken_text("# Plan\n- *Gym* at 7\n- 2*3 is 6") == "Plan. Gym at 7. 2*3 is 6."


def test_spoken_text_joins_wrapped_lines_and_drops_links_and_paths() -> None:
    answer = "The notes are in ~/dev/notes/todo.md\nand online (https://example.com/x), both current."
    assert spoken_text(answer) == "The notes are in and online, both current."


def test_spoken_text_says_a_slash_command_instead_of_dropping_it_as_a_path() -> None:
    assert spoken_text("Use /reminders to see them.") == "Use slash reminders to see them."
    assert spoken_text("Type /help for the list, or /remind 8:00 call mum.", min_chars=1) == (
        "Type slash help for the list, or slash remind 8:00 call mum."
    )
    assert (
        spoken_text("Start with `/babble nl`, then /babble off.")
        == "Start with slash babble nl, then slash babble off."
    )
    # a path is still left out, also one that starts like a command
    assert spoken_text("It is in /reminders/old.txt and ~/new and /usr/bin/sudo, all three.") == (
        "It is in and and, all three."
    )


def test_music_signs_are_spoken_emoji_are_not() -> None:
    # review, 16 Oct: the emoji range took the flat and sharp signs with it, so "E♭ major" was said "E major"
    assert spoken_text("The key is E♭ major, with a B♭7, an F♯m and a C♮.") == (
        "The key is E♭ major, with a B♭7, an F♯m and a C♮."
    )
    assert spoken_text("Sunny ☀️ today 😀, a ♥ and a ♪ tune.") == "Sunny today, a and a tune."


@pytest.mark.parametrize(
    ("answer", "spoken"),
    [
        # owner, 18 Oct: her "(pause)" between chords was read aloud; the "..." around it stays, and between two
        # chords it is a comma (the chord speech audit, the same day), so the chords stay one run with a pause
        ("Try this one: Am... (pause) ... F... (pause) ... C.", "Try this one: Am, F, C."),
        ("Sure! (laughs) That one is a classic.", "Sure! That one is a classic."),
        ("Okay *smiles* here it comes.", "Okay here it comes."),
        ("[pause] Then the chorus comes in.", "Then the chorus comes in."),
        ("(short pause) Ready for the bridge?", "Ready for the bridge?"),
        ("Well (long pause) maybe next week.", "Well maybe next week."),
        ("So (a beat) that was the intro.", "So that was the intro."),
        ("(sighs) Mondays are long ones.", "Mondays are long ones."),
        ("That is lovely *(smiles)* to hear.", "That is lovely to hear."),
        ("Claro (pausa) seguimos con el coro.", "Claro seguimos con el coro."),
        ("Also (kurze Pause) weiter geht es.", "Also weiter geht es."),
        ("Хорошо (пауза) идём дальше.", "Хорошо идём дальше."),
        ("Oké (lacht) dat was een goeie.", "Oké dat was een goeie."),
        ("Bien sûr (rires) on continue.", "Bien sûr on continue."),
        ("はい（間）次のコードです。", "はい次のコードです。"),
        ("좋아요 (웃음) 다음 코드예요.", "좋아요 다음 코드예요."),
        # brackets that are no stage direction stay
        ("Play C (live version) after that.", "Play C (live version) after that."),
        ("Use the (G shapes) for the verse.", "Use the (G shapes) for the verse."),
        ("Then the chorus again (x2).", "Then the chorus again (x2)."),
        ("Call me back (if you want).", "Call me back (if you want)."),
        ("[Verse 1] Em, C, G and D.", "[Verse 1] Em, C, G and D."),
    ],
)
def test_stage_directions_are_never_spoken(answer: str, spoken: str) -> None:
    assert spoken_text(answer, min_chars=1) == spoken


@pytest.mark.parametrize(
    ("answer", "spoken"),
    [
        # the chord speech audit (18 Oct): "..." became "." and each chord a sentence of its own, so "G." was read
        # "Jay" and "Am." "M"; between two chords "..." is a comma, and the voice service spells them as one run
        ("C... G... Am...", "C, G, Am."),
        ("Then C ... G ... Am.", "Then C, G, Am."),
        ("Do... Sol... Lam...", "Do, Sol, Lam."),
        ("Em7... Cadd9... G/B... D/F#...", "Em7, Cadd9, G/B, D/F#."),
        ("Cm7(b5).... F7(b9)....", "Cm7(b5), F7(b9)."),
        ("C...G...Am", "C,G,Am."),  # without spaces too (the review, 18 Oct: "C, G.Am.")
        ("U.S.A... C is next.", "U.S.A. C is next."),  # an abbreviation's letter is no chord
        # and before a joining word and a chord (the review, 18 Oct: "G... then C" left the lone "G.", read "Jay")
        ("G... then C.", "G, then C."),
        ("Start on G... then C... then D.", "Start on G, then C, then D."),
        ("Play C... then G... then Am.", "Play C, then G, then Am."),
        ("The chords are C... G... and Am...", "The chords are C, G, and Am."),
        ("Empieza en Sol... luego Do... luego Re.", "Empieza en Sol, luego Do, luego Re."),
        ("Joue Do... puis Sol.", "Joue Do, puis Sol."),
        ("Spiel C... dann G.", "Spiel C, dann G."),
        # after words it stays as before, and before a joining word and a word too
        ("I am not sure... maybe later.", "I am not sure. maybe later."),
        ("G... then the chorus.", "G. then the chorus."),
        ("Wait... then C.", "Wait. then C."),
        ("Wait... A moment.", "Wait. A moment."),
        ("Hmm... Am I late?", "Hmm. Am I late?"),
    ],
)
def test_dots_between_chords_are_a_comma(answer: str, spoken: str) -> None:
    assert spoken_text(answer, min_chars=1, rest="") == spoken


@pytest.mark.parametrize(
    ("answer", "spoken"),
    [
        # a comma after a dotted abbreviation's last dot stays: the dot is the abbreviation's, the comma a pause (the
        # chord speech audit, 18 Oct: "N.C., then G" lost its pause before "then")
        ("Intro: N.C., then G.", "Intro: N.C., then G."),
        ("Fruit, e.g., apples.", "Fruit, e.g., apples."),
        # and so does a semicolon (a table's bar line, as the bridge says it: the review, 18 Oct)
        ("Intro: N.C.; G.", "Intro: N.C.; G."),
        # any other run of marks is one mark, as before
        ("Wait.. what?!", "Wait. what?"),
        ("Done!.", "Done!"),
    ],
)
def test_a_comma_after_an_abbreviation_stays(answer: str, spoken: str) -> None:
    assert spoken_text(answer, min_chars=1, rest="") == spoken


def test_chords_with_dots_between_are_spoken_as_one_part() -> None:
    assert sentences(spoken_text("C... G... Am...", min_chars=1, rest="")) == ["C, G, Am."]
    assert sentences(spoken_text("Start on G... then C... then D.", min_chars=1, rest="")) == [
        "Start on G, then C, then D."
    ]
    # a table's bar lines are said as "; " (the voice service makes them a pause between chords, never "CGMF")
    assert sentences(spoken_text("| C | G | Am | F |", min_chars=1, rest="")) == ["C; G; Am; F."]
    # "N.C." in a bar keeps its bar line (the review, 18 Oct: "N.C." was a part of its own, read as letters)
    assert sentences(spoken_text("| N.C. | G | C | D |", min_chars=1, rest="")) == ["N.C.; G; C; D."]
    assert sentences(spoken_text("| N.C. | Sol | Re | Mim |", min_chars=1, rest="")) == ["N.C.; Sol; Re; Mim."]


@pytest.mark.parametrize(
    ("text", "sent"),
    [
        # in a text too (she never needs one there); the rest of the line as written, French spacing kept
        ("Em... (pause) ... C... (pause) ... D.", "Em... C... D."),
        ("That's funny (laughs).", "That's funny."),
        ("C'est drôle (rires) !", "C'est drôle !"),
        ("(laughs) Sure, here it is.", "Sure, here it is."),
        ("*smiles*\nHere is the list.", "Here is the list."),
        ("Hi **Alex**, *(beat)* the plan:", "Hi **Alex**, the plan:"),
        ("Read [pause](https://example.com) first.", "Read [pause](https://example.com) first."),  # a link
        ("Use **bold** and *italic* here.", "Use **bold** and *italic* here."),
        ("Chords (G shapes): G, C, D (x2).", "Chords (G shapes): G, C, D (x2)."),
    ],
)
def test_stage_directions_are_left_out_of_her_text(text: str, sent: str) -> None:
    assert without_stage_directions(text) == sent


@pytest.mark.parametrize(
    "text",
    [
        # review 18 Oct: bold, a starred plain word, code and a title in brackets are never a stage direction
        "1. **Beat** the eggs with the sugar.",
        "Press **Pause** in Spotify to stop.",
        "Tap the *pause* button.",
        "Don't forget to *smile*!",
        "Enjoy the *ride*!",  # Italian's "laughs" is an English word too
        "Drück in Spotify auf **Pause**.",
        "Tippe auf *Pause*.",
        "Pulsa *pausa* para parar.",
        "Appuie sur **Pause** pour arrêter.",
        "```python\ntime.sleep(pause)\nif (silence): pass\n```",
        "Call `player.pause(beat)` first.",
        "Then time.sleep(pause) waits.",
        "(Applause) by Lady Gaga is next.",
        "Play (Smile) next.",
        "Put on (Ride) by Twenty One Pilots.",
    ],
)
def test_what_only_looks_like_a_stage_direction_stays(text: str) -> None:
    assert without_stage_directions(text) == text


@pytest.mark.parametrize(
    ("text", "sent"),
    [
        ("Great question! (Laughs) Let me think.", "Great question! Let me think."),  # capitalised, between sentences
        ("Sure. *Smiles* Here you go.", "Sure. Here you go."),
        ("Okay *laughing* that was a good one.", "Okay that was a good one."),  # a starred -ing form
        ("Run `ls` first. (pause) Then the next one.", "Run `ls` first. Then the next one."),
    ],
)
def test_a_stage_direction_beside_code_or_capitalised_between_sentences_goes(text: str, sent: str) -> None:
    assert without_stage_directions(text) == sent


def test_a_bold_word_is_spoken_as_written() -> None:
    assert spoken_text("Press **Pause** in Spotify to stop.", min_chars=1) == "Press Pause in Spotify to stop."


def test_every_command_in_help_is_spoken_as_a_command() -> None:
    named = set(re.findall(r"(?<![\w/])/([a-z]+)\b", prompts.HELP))
    assert named and named <= set(COMMANDS), named - set(COMMANDS)


def test_spoken_text_reads_tables_and_inline_code() -> None:
    answer = "| Day | Plan |\n|---|---|\n| Mon | Gym |\n\nRun `uv sync` first."
    assert spoken_text(answer) == "Day; Plan. Mon; Gym. Run uv sync first."


def test_no_speech_for_code_answers_or_almost_nothing() -> None:
    assert spoken_text("Here:\n```bash\nls -la ~/dev\ncd local-assistant\ngit status\n```") == ""
    assert spoken_text("Ok 👍") == ""
    assert spoken_text("Okay, done.") == "Okay, done."


def test_long_answers_are_cut_at_a_sentence() -> None:
    answer = " ".join(f"This is sentence number {n} of a long answer." for n in range(60))
    spoken = spoken_text(answer, max_chars=200)
    assert len(spoken) <= 200
    assert spoken.endswith("of a long answer. The rest is in the text.")
    assert spoken.startswith("This is sentence number 0")


async def test_to_m4a_makes_a_private_aac_file(tmp_path: Path) -> None:
    path = await to_m4a(wav(0.5), tmp_path / "voice")
    data = path.read_bytes()
    assert data[4:8] == b"ftyp" and sniff(data) == "m4a"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "voice").stat().st_mode) == 0o700


async def test_to_m4a_failure_leaves_no_files(tmp_path: Path) -> None:
    with pytest.raises(VoiceError):
        await to_m4a(b"RIFF not really a wav", tmp_path / "voice")
    assert list((tmp_path / "voice").iterdir()) == []


def test_spoken_text_on_a_call_keeps_short_answers_and_has_no_rest_note() -> None:
    assert spoken_text("Done.") == ""  # a voice note that short is not worth sending
    assert spoken_text("Done.", min_chars=1) == "Done."
    long = " ".join(f"Sentence number {n} is here." for n in range(100))
    cut = spoken_text(long, max_chars=200, rest="")
    assert len(cut) <= 200 and cut.endswith("is here.") and "text" not in cut


def test_sentences_split_spoken_text_for_speaking_one_by_one() -> None:
    assert sentences("Hi there. It is 3.5 degrees! Shall I check? Okay") == [
        "Hi there.",
        "It is 3.5 degrees!",
        "Shall I check?",
        "Okay",
    ]
    assert sentences("") == []


@pytest.mark.parametrize(
    ("spoken", "parts"),
    [
        ("Dr. Smith is in.", ["Dr. Smith is in."]),
        ("Mr. and Mrs. Example are here. Ms. Example too.", ["Mr. and Mrs. Example are here.", "Ms. Example too."]),
        ("PSV vs. Ajax tonight.", ["PSV vs. Ajax tonight."]),
        ("See St. Mark's. It is old.", ["See St. Mark's.", "It is old."]),
        ("It's 8 a.m. now.", ["It's 8 a.m. now."]),
        ("At 8 a.m. Then we go.", ["At 8 a.m.", "Then we go."]),
        ("We start at 9 p.m. sharp. Or 10 P.M. tomorrow.", ["We start at 9 p.m. sharp.", "Or 10 P.M. tomorrow."]),
        ("e.g. this. Then that.", ["e.g. this.", "Then that."]),
        # before a name too: the voice service would end the first part with a full stop ("…, for example.")
        (
            "Streaming services, e.g. Spotify and YouTube, are popular.",
            ["Streaming services, e.g. Spotify and YouTube, are popular."],
        ),
        ('Use one, e.g. "Spotify" works. I.e. That one.', ['Use one, e.g. "Spotify" works.', "I.e. That one."]),
        ("Fruit, i.e. Apples. E.g. Pears too.", ["Fruit, i.e. Apples.", "E.g. Pears too."]),
        (
            "Apples (e.g. green ones), i.e. sour ones, cost approx. 5 euros.",
            ["Apples (e.g. green ones), i.e. sour ones, cost approx. 5 euros."],
        ),
        ("No. 5 is next. Then 6.", ["No. 5 is next.", "Then 6."]),
        ("No. That's it.", ["No.", "That's it."]),
        ("I said no. 5 people agreed.", ["I said no.", "5 people agreed."]),  # a lower-case "no." is the word
        # "N.C." (no chord) inside a sentence of chords (the chord speech audit, 18 Oct): one part, so its run goes on
        ("Intro: N.C. then G.", ["Intro: N.C. then G."]),
        ("The song ends on N.C. Then the outro.", ["The song ends on N.C.", "Then the outro."]),
        # nor before chords (the review, 18 Oct: "N.C. G C D" was the parts "N.C." and "G C D.")
        ("N.C. G C D.", ["N.C. G C D."]),
        ("Intro: N.C. Am F C G.", ["Intro: N.C. Am F C G."]),
        ("N.C. G.", ["N.C. G."]),
        ("N.C. G, C, D.", ["N.C. G, C, D."]),
        ("N.C. Sol Re Mim.", ["N.C. Sol Re Mim."]),
        # a word after the first chord-like word: a new sentence
        ("N.C. A good start.", ["N.C.", "A good start."]),
        ("Vivo en N.C. La casa es grande.", ["Vivo en N.C.", "La casa es grande."]),
        ("Bonjour. Ça va ? Oui, très bien !", ["Bonjour.", "Ça va ?", "Oui, très bien !"]),
        ("Hallo. Wie geht's? Gut!", ["Hallo.", "Wie geht's?", "Gut!"]),
    ],
)
def test_sentences_never_end_at_a_title_or_an_abbreviation_inside_one(spoken: str, parts: list[str]) -> None:
    # a title, "vs.", "e.g." and "i.e." stand before a name; the rest only before a lower-case letter or a digit
    assert sentences(spoken) == parts


def test_the_voice_can_be_changed_while_running() -> None:
    client = VoiceClient("http://127.0.0.1:1", voice="michael")
    client.set_voice("jane")
    assert client.voice == "jane"


def test_a_kokoro_voice_is_asked_as_the_closest_pocket_voice() -> None:
    """Owner, 3 Nov 2026: Kokoro was removed. A Kokoro voice id that still reaches the client (an old config, a stored
    /voice choice) is never sent: a male one (its second letter m) is michael, any other heart (af_heart's clone)."""
    client = VoiceClient("http://127.0.0.1:1", voice="am_michael")
    assert client.voice == "michael"
    client.set_voice("af_bella")
    assert client.voice == "heart"
    client.set_voice("bm_george")
    assert client.voice == "michael"
    client.set_voice("eve-full")
    assert client.voice == "eve"
    assert [pocket_voice(name) for name in ("af_heart", "bf_emma", "am_puck", "heart", "vera", "zoe")] == [
        "heart",
        "heart",
        "michael",
        "heart",
        "vera",
        "zoe",
    ]


NOW = 1_791_452_400.0


def recorder(tmp_path: Path) -> PartStatus:
    return PartStatus(tmp_path / "status.json", clock=lambda: NOW)


async def test_transcripts_and_speech_record_the_voice_service(tmp_path: Path) -> None:
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", status=status)
        await client.transcribe(M4A)
        assert status.snapshot() == {"voice": Record(ok=NOW)}
        service.status["/v1/audio/speech"] = 500
        with pytest.raises(VoiceError):
            await client.speak("Hello there.")
        assert status.snapshot() == {"voice": Record(ok=NOW, failed=NOW, reason="refused", code=500)}
        del service.status["/v1/audio/speech"]
        await client.speak("Hello there.")
        assert status.snapshot()["voice"] == Record(ok=NOW)
        await client.health()  # a probe, not a real request
        assert status.snapshot()["voice"] == Record(ok=NOW)


@pytest.mark.parametrize(
    ("answer", "record"),
    [
        (503, Record(failed=NOW, reason="loading", code=503)),
        (413, Record(ok=NOW)),  # the service answered as it should: the audio was the problem
        (422, Record(ok=NOW)),
    ],
)
async def test_the_voice_service_answers_are_recorded(tmp_path: Path, answer: int, record: Record) -> None:
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        service.status["/v1/audio/transcriptions"] = answer
        with pytest.raises(VoiceError):
            await VoiceClient(service.url, voice="heart", status=status).transcribe(M4A)
    assert status.snapshot() == {"voice": record}


async def test_a_voice_service_that_is_not_running_is_recorded(tmp_path: Path) -> None:
    status = recorder(tmp_path)
    with pytest.raises(VoiceUnavailableError):
        await VoiceClient(closed_port_url(), voice="heart", status=status).speak("Hello there.")
    assert status.snapshot() == {"voice": Record(failed=NOW, reason="no_answer")}


async def test_english_requests_are_exactly_as_before_the_languages() -> None:
    # an older voice service (no languages) keeps working in English: no query, no field
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", language="en")
        await client.transcribe(M4A)
        await client.speak("Hi.")
        assert service.paths() == ["/v1/audio/transcriptions", "/v1/audio/speech"]
        assert service.requests[1][3] == json.dumps({"input": "Hi.", "voice": "heart", "kokoro": False}).encode()
        assert service.request_languages == ["en", "en"]


async def test_what_she_said_before_goes_with_english_speech_for_the_times() -> None:
    """The Pocket TTS spike, 24 Oct: "Shall I remind you at 7:30?" after "closes at 8 tonight" was said "in the
    morning". On a call each sentence is its own request, so her answer's sentences before it go along (`context`, the
    last 1,000 characters), read only for a time's part of the day; never in another language, never empty, so every
    other request stays byte for byte as before."""
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="eve", language="en")
        await client.speak("Shall I remind you at 7:30?", context="It closes at 8 tonight.")
        await client.speak("Hi.", context="")
        await client.speak("Om half acht?", language="nl", context="Het sluit vanavond.")
        await client.speak("Then?", context="x" * 1500)
        bodies = [json.loads(request[3]) for request in service.requests]
        assert bodies[0] == {
            "input": "Shall I remind you at 7:30?",
            "voice": "eve",
            "context": "It closes at 8 tonight.",
            "kokoro": False,
        }
        assert service.requests[1][3] == json.dumps({"input": "Hi.", "voice": "eve", "kokoro": False}).encode()
        assert "context" not in bodies[2] and bodies[2]["language"] == "nl"
        assert bodies[3]["context"] == "x" * 1000


async def test_a_calls_line_says_it_is_a_calls() -> None:
    """The review of 28 Oct (calls-03): on a call, a Pocket TTS voice resting after failed runs is tried again for each
    line. The request says it is a call's (`"call": true`); every other request goes without it."""
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", language="en")
        await client.speak("Two meetings.", call=True)
        await client.speak("Hi.")
        bodies = [json.loads(request[3]) for request in service.requests]
        assert bodies[0] == {"input": "Two meetings.", "voice": "heart", "call": True, "kokoro": False}
        assert service.requests[1][3] == json.dumps({"input": "Hi.", "voice": "heart", "kokoro": False}).encode()


async def test_a_line_pocket_cannot_say_is_said_by_no_other_voice(tmp_path: Path) -> None:
    """Owner, 3 Nov 2026: no voice replaces Pocket TTS. A line it cannot say (a failed or late run, a refused file, a
    model not downloaded yet, a voice resting after failures) the voice service refuses (409): a VoiceNotSaidError,
    and the voice service stays ok in the status (it did as asked)."""
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", language="en", status=status)
        service.unsaid = True
        with pytest.raises(VoiceNotSaidError):
            await client.speak("One at nine.", call=True)
        with pytest.raises(VoiceNotSaidError):
            await client.speak("Hallo.", language="de")
        assert status.snapshot()["voice"] == Record(ok=NOW)


@pytest.mark.parametrize(
    ("language", "health", "reason"),
    [
        ("en", {"tts": "loading"}, "loading"),  # a first start: the English model downloads
        ("en", {"tts": "resting"}, "not_working"),  # resting after failed or late runs
        ("en", {"tts": "failed"}, "not_working"),  # its program missing or its files refused
        ("de", {"pocket_de": "idle"}, "loading"),  # jane's German model not loaded yet
        ("de", {"pocket_de": "loading"}, "loading"),
        ("de", {"pocket_de": "refused"}, "not_working"),
        ("de", {"pocket_de": "unavailable"}, "not_working"),
    ],
)
async def test_a_line_refused_while_her_voice_cannot_speak_records_why(
    tmp_path: Path, language: str, health: dict[str, str], reason: str
) -> None:
    """The review of 4 Nov: every line Pocket TTS could not say was 409 and recorded ok, so while the English model
    downloaded (or after its files were refused) every voice-note answer came as text while /status's voice part said
    ok. The 409 stays (calls rely on it); the bridge reads the health once on it: speaking still loading or not working
    is recorded so (a single failed run with the model ready stays ok)."""
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        service.unsaid = True
        service.health |= health
        client = VoiceClient(service.url, voice="heart", language=language, status=status)
        with pytest.raises(VoiceNotSaidError):
            await client.speak("Hallo." if language == "de" else "Hello.")
        assert status.snapshot()["voice"] == Record(failed=NOW, reason=reason, code=409)
        service.unsaid = False
        await client.speak("Hallo." if language == "de" else "Hello.")
        assert status.snapshot()["voice"] == Record(ok=NOW)


@pytest.mark.parametrize(("language", "other"), [("en", "af_heart"), ("de", "F1"), ("en", "../not a voice")])
async def test_a_line_an_older_voice_service_had_another_voice_say_is_never_played(
    tmp_path: Path, language: str, other: str
) -> None:
    """A voice service from before 3 Nov 2026 still has Kokoro and Supertonic-3 and marks a line one of them said in
    Pocket TTS's place (X-Voice-Stand-In): that audio is never returned, the line is not said (VoiceNotSaidError), and
    the voice service stays ok in the status."""
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        service.stand_in, service.stands_in_always = other, True
        client = VoiceClient(service.url, voice="eve", language=language, status=status)
        with pytest.raises(VoiceNotSaidError):
            await client.speak("Einen Moment." if language == "de" else "Got it.")
        assert status.snapshot()["voice"] == Record(ok=NOW)


@pytest.mark.parametrize("language", ["ko", "pl", "ru", "ja", "el"])
async def test_a_language_pocket_has_no_model_for_is_never_asked(language: str) -> None:
    """Owner, 3 Nov 2026: Korean, Polish, Russian and Japanese have no voice (Pocket TTS has no model for them; nothing
    replaces Supertonic-3). A line in one of them is refused before any request (VoiceNoLanguageError, a
    VoiceNotSaidError), and so is a language Estia does not have."""
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="eve", language=language)
        with pytest.raises(VoiceNoLanguageError):
            await client.speak("…")
        assert service.requests == []
        assert issubclass(VoiceNoLanguageError, VoiceNotSaidError)
    assert [code for code in ("en", "es", "it", "pt", "nl", "de", "fr", "ko", "pl", "ru", "ja") if has_voice(code)] == [
        "en",
        *POCKET_LANGUAGES,
    ]


async def test_her_name_goes_as_whispers_hint_only_when_given() -> None:
    """On a call in standby the bridge sends her name, so Whisper writes it when it is said (owner, 22 Oct); an older
    voice service reads only the language and ignores it. Never the hint without being asked: voice notes go as before."""
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", language="en")
        await client.transcribe(M4A, hint="Estia")
        await client.transcribe(M4A, language="nl", hint="Mary Jane")
        await client.transcribe(M4A, language="ja", hint="エスティア")
        await client.transcribe(M4A)
        assert service.paths() == [
            "/v1/audio/transcriptions?hint=Estia",
            "/v1/audio/transcriptions?language=nl&hint=Mary%20Jane",
            "/v1/audio/transcriptions?language=ja&hint=%E3%82%A8%E3%82%B9%E3%83%86%E3%82%A3%E3%82%A2",
            "/v1/audio/transcriptions",
        ]
        assert service.request_languages == ["en", "nl", "ja", "en"]


async def test_a_calls_turn_goes_with_the_call_flag() -> None:
    """A call's turn says so (`call=1`): the voice service keeps a bare "Bye." or "Thank you." it erases from a voice
    note (review I23); an older service reads only the language and the hint and ignores it."""
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", language="en")
        await client.transcribe(M4A, call=True)
        await client.transcribe(M4A, language="nl", hint="Estia", call=True)
        await client.transcribe(M4A)
        assert service.paths() == [
            "/v1/audio/transcriptions?call=1",
            "/v1/audio/transcriptions?language=nl&hint=Estia&call=1",
            "/v1/audio/transcriptions",
        ]


async def test_the_fake_service_erases_a_bare_bye_as_the_real_one_does() -> None:
    with FakeVoiceService() as service:
        service.transcript = "Bye."
        client = VoiceClient(service.url, voice="heart", language="en")
        assert (await client.transcribe(M4A)).text == ""
        assert (await client.transcribe(M4A, call=True)).text == "Bye."


async def test_another_language_goes_with_every_request() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", language="nl")
        assert client.language == "nl"
        await client.transcribe(M4A)
        await client.speak("Hoi.")
        await client.speak("Shall I switch heater off?", language="en")  # code's own words: English
        assert service.paths() == ["/v1/audio/transcriptions?language=nl", "/v1/audio/speech", "/v1/audio/speech"]
        assert json.loads(service.requests[1][3]) == {
            "input": "Hoi.",
            "voice": "jane",
            "language": "nl",
            "kokoro": False,
        }
        assert "language" not in json.loads(service.requests[2][3])
        assert service.request_languages == ["nl", "nl", "en"]
        client.set_language("ja")
        with pytest.raises(VoiceNoLanguageError):  # no voice in Japanese: nothing asked
            await client.speak("はい。")
        await client.transcribe(M4A)  # heard all the same
        assert service.request_languages[-1] == "ja" and len(service.requests) == 4
        await client.transcribe(M4A, language="ko")  # a call keeps the language it started with
        assert service.paths()[-1] == "/v1/audio/transcriptions?language=ko"


async def test_the_health_says_which_languages_the_service_has() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart")
        assert await client.health() == {"stt": "ready", "tts": "ready"}  # older than the languages
        service.languages = ["en", "nl"]
        assert await client.health() == {"stt": "ready", "tts": "ready", "languages": ("en", "nl")}


async def test_the_languages_of_the_installed_service() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart")
        assert await client.languages() == ()  # answers, but older than the language feature
        service.languages = ["en", "nl"]
        assert await client.languages() == ("en", "nl")
    assert await VoiceClient("http://127.0.0.1:1", voice="heart").languages() is None  # not answering


async def test_a_language_the_service_does_not_have_is_an_error() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", language="el")
        with pytest.raises(VoiceError, match="400"):
            await client.transcribe(M4A)


def test_japanese_is_spoken_sentence_by_sentence_too() -> None:
    # Japanese ends sentences with 。！？ and no space after them
    assert sentences("こんにちは。元気ですか？はい！") == ["こんにちは。", "元気ですか？", "はい！"]
    assert sentences("こんにちは。 元気？") == ["こんにちは。", "元気？"]
    assert spoken_text("こんにちは。元気ですか？", min_chars=1) == "こんにちは。元気ですか？"  # no "." added
    long = "今日は晴れです。" * 300
    cut = spoken_text(long, max_chars=200, rest="")
    assert len(cut) <= 200 and cut.endswith("晴れです。")


CHUNK = array.array("h", (int(3000 * math.sin(2 * math.pi * 220 * i / 16_000)) for i in range(4096))).tobytes()


async def test_the_speech_detector_gets_one_chunk_and_answers_a_probability() -> None:
    with FakeVoiceService() as service:
        service.vad = "ready"
        client = VoiceClient(service.url, voice="heart")
        assert await client.vad(CHUNK, stream="0a1b2c") == 1.0
        assert await client.vad(bytes(8192), stream="0a1b2c") == 0.02
        method, path, headers, body = service.requests[0]
        assert (method, path, body) == ("POST", "/vad?stream=0a1b2c", CHUNK)
        assert headers["content-type"] == "application/octet-stream"


@pytest.mark.parametrize(
    ("vad", "answer", "chunk"),
    [
        (None, None, CHUNK),  # an older service: no /vad (404)
        ("loading", None, CHUNK),  # 503 until the model is ready
        ("ready", None, CHUNK[:100]),  # 400: not 4096 samples
        ("ready", b"not json", CHUNK),
        ("ready", b'{"p": "high"}', CHUNK),
        ("ready", b'{"p": 1.5}', CHUNK),
        ("ready", b'{"p": NaN}', CHUNK),
        ("ready", b"[1.0]", CHUNK),
    ],
    ids=["older-service", "loading", "short-chunk", "not-json", "not-a-number", "above-1", "nan", "a-list"],
)
async def test_a_speech_detector_that_cannot_answer_is_an_http_failure_never_an_error(
    tmp_path: Path, vad: str | None, answer: bytes | None, chunk: bytes
) -> None:
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        service.vad, service.vad_answer = vad, answer
        client = VoiceClient(service.url, voice="heart", status=status)
        assert await client.vad(chunk, stream="s1") == "http"
    assert status.snapshot() == {}  # the detector is not the voice: a failure never marks the voice as failing


async def test_a_speech_detector_that_does_not_answer_is_an_http_failure() -> None:
    assert await VoiceClient(closed_port_url(), voice="heart").vad(CHUNK, stream="s1") == "http"


def hers(port: int, local: int, seconds: float) -> bool | None:
    return True


async def test_a_slow_speech_detector_is_given_up_on_quickly_as_a_timeout() -> None:
    with FakeVoiceService() as service:
        service.vad, service.vad_delay = "ready", 0.8
        started = time.monotonic()
        assert await VoiceClient(service.url, voice="heart", peer=hers).vad(CHUNK, stream="s1") == "timeout"
        assert time.monotonic() - started < 0.6  # a call never waits for the detector


async def test_the_check_of_whose_program_it_is_counts_within_the_detectors_own_time() -> None:
    def slow_check(port: int, local: int, seconds: float) -> bool | None:
        time.sleep(0.25)
        return True

    with FakeVoiceService() as service:
        service.vad, service.vad_delay = "ready", 0.2
        started = time.monotonic()
        client = VoiceClient(service.url, voice="heart", peer=slow_check)
        assert await client.vad(CHUNK, stream="s1") == "timeout"  # 0.25 checking + 0.2 answering: over 0.3
        assert time.monotonic() - started < 0.5


@pytest.mark.parametrize(
    ("health", "state"),
    [
        ({"stt": "ready", "tts": "ready", "vad": "ready"}, "ready"),
        ({"stt": "ready", "tts": "ready", "vad": "loading"}, "loading"),
        ({"stt": "ready", "tts": "ready", "vad": "unavailable"}, "unavailable"),  # no model: its own fix (doctor)
        ({"stt": "ready", "tts": "ready"}, "old voice service"),  # built before the detector
        (None, "not ready"),  # no answer
        ({"stt": "ready", "tts": "ready", "vad": "something new"}, "not ready"),
    ],
)
def test_the_speech_detectors_state_from_the_health(health: dict[str, str] | None, state: str) -> None:
    assert detector_state(health) == state


# the turn detector (Smart Turn v3.2, POST /turn): the last 8 s of the caller's 16 kHz audio, most recent sample last,
# no query and no state; a call never waits for it longer than the speech detector (VAD_TIMEOUT)
WINDOW = CHUNK * 10  # 2.56 s


async def test_turn_posts_the_window_and_returns_p() -> None:
    with FakeVoiceService() as service:
        service.turn, service.turn_p = "ready", 0.91
        client = VoiceClient(service.url, voice="heart")
        assert await client.turn(WINDOW) == 0.91
        service.turn_p = 0
        assert await client.turn(bytes(256_000)) == 0.0  # 8 s, the whole window
        method, path, headers, body = service.requests[0]
        assert (method, path, body) == ("POST", "/turn", WINDOW)  # no query: the window is all it gets
        assert headers["content-type"] == "application/octet-stream"


@pytest.mark.parametrize(
    ("turn", "answer", "window"),
    [
        (None, None, WINDOW),  # an older service: no /turn (404)
        ("loading", None, WINDOW),  # 503 not_ready while the model loads
        ("unavailable", None, WINDOW),  # 503: its download, check or load failed
        ("ready", None, WINDOW[:101]),  # 400 bad_window: an odd length
        ("ready", None, bytes(256_002)),  # 400: over 8 s
        ("ready", b"not json", WINDOW),
        ("ready", b'{"p": true}', WINDOW),  # a bool is no probability
        ("ready", b'{"p": "high"}', WINDOW),
        ("ready", b'{"p": 1.5}', WINDOW),
        ("ready", b'{"p": -0.1}', WINDOW),
        ("ready", b'{"p": NaN}', WINDOW),
        ("ready", b"[0.9]", WINDOW),
    ],
    ids=["older", "loading", "unavailable", "odd", "too-long", "not-json", "bool", "text", "above-1", "below-0", "nan",
         "a-list"],
)  # fmt: skip
async def test_turn_failures_are_kinds_never_raised(
    tmp_path: Path, turn: str | None, answer: bytes | None, window: bytes
) -> None:
    status = recorder(tmp_path)
    with FakeVoiceService() as service:
        service.turn, service.turn_answer = turn, answer
        client = VoiceClient(service.url, voice="heart", status=status)
        assert await client.turn(window) == "http"
    assert status.snapshot() == {}  # the turn detector is not the voice: a failure never marks the voice as failing


@pytest.mark.parametrize("state", ["loading", "unavailable", "ready"])
def test_the_fake_refuses_a_bad_window_before_its_state_as_the_service_does(state: str) -> None:
    # voice-service's Server.turn: TurnWindow's refusal comes first, 400 bad_window whatever the model's state; a GET
    # is 405 (Route), never 404
    def answer(service: FakeVoiceService, method: str, body: bytes) -> tuple[int, bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", int(service.url.rsplit(":", 1)[1]), timeout=5)
        try:
            connection.request(method, "/turn", body=body)
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    with FakeVoiceService() as service:
        service.turn = state
        for window in (b"", WINDOW[:101], bytes(256_002)):
            assert answer(service, "POST", window) == (400, b'{"error":"bad_window"}')
        assert answer(service, "GET", b"")[0] == 405


async def test_a_turn_detector_that_does_not_answer_is_http_and_a_slow_one_a_timeout() -> None:
    assert await VoiceClient(closed_port_url(), voice="heart").turn(WINDOW) == "http"
    with FakeVoiceService() as service:
        service.turn, service.turn_delay = "ready", 0.8
        started = time.monotonic()
        assert await VoiceClient(service.url, voice="heart", peer=hers).turn(WINDOW) == "timeout"
        assert time.monotonic() - started < 0.6  # a call never waits for the detector: VAD_TIMEOUT
    assert VAD_TIMEOUT == 0.3


async def test_nothing_goes_to_a_turn_detector_that_is_not_hers() -> None:
    def theirs(port: int, local: int, seconds: float) -> bool | None:
        return False

    with FakeVoiceService() as service:
        service.turn = "ready"
        assert await VoiceClient(service.url, voice="heart", peer=theirs).turn(WINDOW) == "http"
        assert service.requests == []


@pytest.mark.parametrize(
    ("health", "state"),
    [
        ({"stt": "ready", "tts": "ready", "vad": "ready", "turn": "ready"}, "ready"),
        ({"stt": "ready", "tts": "ready", "vad": "ready", "turn": "loading"}, "loading"),
        ({"stt": "ready", "tts": "ready", "vad": "ready", "turn": "unavailable"}, "unavailable"),
        ({"stt": "ready", "tts": "ready", "vad": "ready"}, "old voice service"),  # built before the turn detector
        (None, "not ready"),
        ({"stt": "ready", "tts": "ready", "turn": "something new"}, "not ready"),
    ],
)
def test_detector_state_reads_turn(health: dict[str, str] | None, state: str) -> None:
    assert detector_state(health, part="turn") == state
    assert detector_state({"turn": "ready"}) == "old voice service"  # the speech detector stays the default part


# the voice port: in her own user, a program of the owner's could hold 127.0.0.1:8150 whenever her voice service is not
# bound (a deploy, a restart, the start of the Mac). Audio and text go only over a connection whose other end a program
# of her own account holds: lsof, as the account that runs it, lists only that account's processes, so a connection it
# does not show taken is not hers. The connection itself is checked, not only who listens, so a program that took it and
# then left the port to her service still gets nothing


def port_of(url: str) -> int:
    return int(url.rsplit(":", 1)[1])


def test_a_connection_a_program_of_this_account_took_is_ours() -> None:
    with FakeVoiceService() as service, socket.create_connection(("127.0.0.1", port_of(service.url))) as connection:
        assert peer_is_ours(port_of(service.url), connection.getsockname()[1], 1.0) is True


def test_a_listener_of_ours_that_has_not_taken_the_connection_is_not_enough() -> None:
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen(1)  # never accepted: no program holds the other end
        port = listening.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as connection:
            started = time.monotonic()
            assert peer_is_ours(port, connection.getsockname()[1], 0.3) is False
            assert time.monotonic() - started < 1.5  # it waits for the taking about the time it was given, no longer


def lsof_runs(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    runs: list[list[str]] = []
    run = subprocess.run

    def counted(args: list[str], **kwargs: Any) -> Any:
        runs.append(args)
        return run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", counted)
    return runs


def test_no_listener_of_ours_is_not_ours_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = lsof_runs(monkeypatch)
    assert peer_is_ours(port_of(closed_port_url()), 50000, 5.0) is False
    assert len(runs) == 1  # nothing of ours listens: no waiting, no second look


def test_a_listener_that_has_not_taken_the_connection_yet_is_looked_at_again(monkeypatch: pytest.MonkeyPatch) -> None:
    runs = lsof_runs(monkeypatch)
    with socket.socket() as listening:
        listening.bind(("127.0.0.1", 0))
        listening.listen(1)
        port = listening.getsockname()[1]
        with socket.create_connection(("127.0.0.1", port)) as connection:
            assert peer_is_ours(port, connection.getsockname()[1], 0.5) is False
    assert len(runs) >= 2  # a connection waits for its accept: the taking is waited for


def test_a_check_that_cannot_run_says_so(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(voice, "LSOF", str(tmp_path / "no-lsof"))
    with FakeVoiceService() as service, socket.create_connection(("127.0.0.1", port_of(service.url))) as connection:
        assert peer_is_ours(port_of(service.url), connection.getsockname()[1], 1.0) is None


async def test_the_check_is_asked_about_this_very_connection_before_anything_is_sent() -> None:
    asked: list[tuple[int, int, float]] = []

    def checked(port: int, local: int, seconds: float) -> bool | None:
        asked.append((port, local, seconds))
        return peer_is_ours(port, local, seconds)

    with FakeVoiceService() as service:
        service.vad = "ready"
        client = VoiceClient(service.url, voice="heart", peer=checked)
        assert await client.transcribe(M4A) == Transcript("Turn off the lamp.", 2.4)
        assert isinstance(await client.vad(CHUNK, stream="s1"), float)
    assert [port for port, _, _ in asked] == [port_of(service.url)] * 2
    assert all(local != port_of(service.url) for _, local, _ in asked)
    assert asked[1][2] <= VAD_TIMEOUT  # a call's detector check fits the detector's own time


@pytest.mark.parametrize(
    ("verdict", "said", "reason"), [(False, "not hers", "not_hers"), (None, "could not be checked", "no_answer")]
)
async def test_nothing_goes_to_a_program_that_is_not_hers_or_unchecked(
    caplog: pytest.LogCaptureFixture, verdict: bool | None, said: str, reason: str
) -> None:
    """Another program at her voice service's address gets nothing, and the part says so (`not_hers`, finding 41):
    doctor and /status name it, never "no answer"."""
    status = PartStatus(None)
    asked: list[int] = []

    def check(port: int, local: int, seconds: float) -> bool | None:
        asked.append(port)
        return verdict

    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", status=status, peer=check)
        with pytest.raises(VoiceUnavailableError):
            await client.transcribe(M4A)
        with pytest.raises(VoiceUnavailableError):
            await client.speak("Hello there.")
        assert await client.vad(CHUNK, stream="s1") == "http"
        assert await client.health() is None
        await asyncio.sleep(0.2)
        assert service.requests == []  # connected, then nothing sent: no audio, no text, no request
    assert asked == [port_of(service.url)] * 4
    assert status.snapshot()["voice"].reason == reason
    assert len([r for r in caplog.records if said in r.getMessage()]) == 1  # said once, not per request


async def test_her_own_service_again_is_said_once_and_used() -> None:
    verdicts = [False, True]

    def check(port: int, local: int, seconds: float) -> bool | None:
        return verdicts.pop(0)

    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="heart", peer=check)
        with pytest.raises(VoiceUnavailableError):
            await client.transcribe(M4A)
        assert await client.transcribe(M4A) == Transcript("Turn off the lamp.", 2.4)
        assert len(service.requests) == 1


async def test_localhost_is_reached_at_127_0_0_1_where_her_service_listens() -> None:
    with FakeVoiceService() as service:
        client = VoiceClient(service.url.replace("127.0.0.1", "localhost"), voice="heart")
        assert await client.transcribe(M4A) == Transcript("Turn off the lamp.", 2.4)


SWIFT_POCKET = Path(__file__).resolve().parents[1] / "tools/voice/Sources/VoiceCore/Pocket.swift"


def test_pocket_pins_are_the_voice_services() -> None:
    """Doctor checks the English voices' files against the voice service's own pins (VoiceCore `PocketTTS`): the
    release program beside the voice service in her release (the review of 24 Oct: never in her home) and the Mac's
    English model in its models folder (owner, 26 Oct: the 100M beside Gemma 4 26B, the 18M beside 12B), each file's
    folder and sha256 the same in both; the voices the same five in the same order."""
    swift = SWIFT_POCKET.read_text(encoding="utf-8")
    order = re.search(r"public static let order = \[([^\]]*)\]", swift)
    assert order is not None
    assert re.findall(r'"([a-z]+)"', order.group(1)) == list(POCKET_ORDER)
    assert POCKET_ORDER == ("heart", "jane", "eve", "vera", "alba", "michael")  # owner, 27 Oct: heart first
    assert set(POCKET_ORDER) == POCKET_VOICES and "eve-full" not in POCKET_VOICES  # the bridge's other name for eve
    assert "public static let standardMemoryGiB: UInt64 = 30\n" in swift and voice.POCKET_STANDARD_GB == 30
    assert set(POCKET_PROGRAM) == {
        "tools/voice/.build/release/pocket-tts/pocket-tts",
        "tools/voice/.build/release/pocket-tts/LICENSE",
    }
    assert "        path: PocketTTS.folder,\n" in swift and 'public static let folder = "pocket-tts"\n' in swift
    for path, sha in POCKET_PROGRAM.items():
        assert re.fullmatch(r"[0-9a-f]{64}", sha) and f'"{path.rsplit("/", 1)[1]}": "{sha}"' in swift
    assert set(POCKET_FILES) == {
        "pocket-tts/english_18m_refl/weights/model.safetensors",
        "pocket-tts/english_18m_refl/tokenizer/tokenizer.model",  # sentencepiece, not the 100M's tokenizer.json
        "pocket-tts/english_18m_refl/eve/eve.safetensors",
        "pocket-tts/english_18m_refl/jane/jane.safetensors",  # owner, 25 Oct: "could I try Jane also for a bit?"
        "pocket-tts/english_18m_refl/alba/alba.safetensors",  # owner, 26 Oct: "alba & michael for males, both on
        "pocket-tts/english_18m_refl/michael/michael.safetensors",  # small and big"
        # owner, 29 Oct: "just make the small heart"; copied from beside the voice service, never fetched
        "pocket-tts/english_18m_refl/heart/heart-18m.safetensors",
    }
    for pins in (POCKET_FILES, POCKET_FILES_100M):  # the 18M's, the 100M's
        for path, sha in pins.items():
            folder, name = path.removeprefix("pocket-tts/").rsplit("/", 1)
            if name in ("heart.safetensors", "heart-18m.safetensors"):  # heart's: no repository, no commit (bundled)
                assert f'sha256: ["{name}": "{sha}"]' in swift and f'local: "\\(PocketTTS.folder)/{folder}"' in swift
                continue
            assert re.fullmatch(r"[0-9a-f]{64}", sha)
            assert re.search(rf'"{re.escape(name)}",\s*"{sha}",\s*"{re.escape(folder)}"\)', swift), path
    assert set(POCKET_FILES_100M) == {
        "pocket-tts/english/weights/model.safetensors",
        "pocket-tts/english/tokenizer/tokenizer.json",
        "pocket-tts/english/eve/eve.safetensors",
        "pocket-tts/english/jane/jane.safetensors",
        "pocket-tts/english/vera/vera.safetensors",  # the 100M only: no vera on the 18M
        "pocket-tts/english/alba/alba.safetensors",
        "pocket-tts/english/michael/michael.safetensors",
        "pocket-tts/english/heart/heart.safetensors",  # copied from beside the voice service, never fetched
    }
    # jane's two states, as the official download at the weights' own revision (a7056f5, whose commit recomputed the
    # voice states with the weights; eve's are the release program's older ones), sha256 computed from that download
    # (25 Oct); vera's, alba's and michael's the same way (25-26 Oct)
    assert POCKET_FILES["pocket-tts/english_18m_refl/jane/jane.safetensors"] == (
        "796f7860a0a2a0ca0b57fda2c7fa7a4cb1e5cd77517ced8c35177b5686f15af3"
    )
    assert POCKET_FILES_100M["pocket-tts/english/jane/jane.safetensors"] == (
        "f802b7b0ce91578dc5448e1cdf4827695e64a34cc6b2f95989dc335082522887"
    )
    assert POCKET_FILES_100M["pocket-tts/english/vera/vera.safetensors"] == (
        "fe136ebb9c88b65803e73ab8fc04fcc7faaa5aa790559bdab3d2382826eea44a"
    )
    assert POCKET_FILES_100M["pocket-tts/english/alba/alba.safetensors"] == (
        "5850f417028fb2bf6de31e5e395e56ee7a21ac0e52ef31aebd4a6a2847d72e9c"
    )
    assert POCKET_FILES_100M["pocket-tts/english/michael/michael.safetensors"] == (
        "9f6538650ed3b664ab847d05244f51f04db22ce7ea00e2b285f51d91d6308ce0"
    )
    assert POCKET_FILES["pocket-tts/english_18m_refl/alba/alba.safetensors"] == (
        "e1aa12e17245952e044853867deef5e77ec4474df119cbeefa4ed2a3d878a7b1"
    )
    assert POCKET_FILES["pocket-tts/english_18m_refl/michael/michael.safetensors"] == (
        "0c2ce79817211a343f784db121570e66e658f146eabc4ccd68fcdbb01b4556e3"
    )
    revision = "a7056f505a4a8ca72d8b5fb1440071d6f1a35739"  # the weights' own, as jane's
    at_revision = [("english", name) for name in ("vera", "alba", "michael")]
    at_revision += [("english_18m_refl_2026-10", name) for name in ("jane", "alba", "michael")]
    for model, name in at_revision:
        pin = rf'pin\("{revision}", "languages/{model}/embeddings",\s*"{name}\.safetensors"'
        assert re.search(pin, swift), (model, name)
    assert voice.pocket_english_files(True) is POCKET_FILES_100M and voice.pocket_english_files(False) is POCKET_FILES
    # heart's file (owner, 27 Oct: "call the voice Heart"; in the public repository: "yes"), as the voice service
    # ships it: in its own bundle beside its program in her release (the deploy copies the build's bundles), and as
    # the repository has it; the models folder's copy is checked against the same sha256
    heart = "60e5e6cbcac2f61554adcfc1ffd9830308802a3685db49489f5a14e8e65b5425"
    assert POCKET_FILES_100M["pocket-tts/english/heart/heart.safetensors"] == heart
    # both places a build puts it (the review of 27 Oct, swift-voice-1): Contents/Resources with Swift 6.4's default
    # build (swiftbuild), the bundle's top with the native build (Swift 6.2's default); the first that is there counts
    assert {
        "tools/voice/.build/release/voice-service_voice-service.bundle/Contents/Resources/heart.safetensors": heart,
        "tools/voice/.build/release/voice-service_voice-service.bundle/heart.safetensors": heart,
    } == voice.POCKET_SHIPPED
    # the 18M's heart (owner, 29 Oct: "just make the small heart and we test it later"), its own clone of af_heart
    # (27 Oct), shipped beside the 100M's under its own name, in the same two places
    small = "0d9e5334433271a849bfa97b1a8626cb690446a98ea3008ba016704bc51cd2bb"
    assert POCKET_FILES["pocket-tts/english_18m_refl/heart/heart-18m.safetensors"] == small
    assert {
        "tools/voice/.build/release/voice-service_voice-service.bundle/Contents/Resources/heart-18m.safetensors": small,
        "tools/voice/.build/release/voice-service_voice-service.bundle/heart-18m.safetensors": small,
    } == voice.POCKET_SHIPPED_18M
    assert 'public static let bundle = "voice-service_voice-service.bundle"\n' in swift
    voices = Path(__file__).resolve().parents[1] / "tools/voice/Sources/voice-service/Voices"
    for name, sha, size in (("heart.safetensors", heart, 8_652_024), ("heart-18m.safetensors", small, 5_768_848)):
        assert (
            hashlib.sha256((voices / name).read_bytes()).hexdigest() == sha and (voices / name).stat().st_size == size
        )
    package = (Path(__file__).resolve().parents[1] / "tools/voice/Package.swift").read_text(encoding="utf-8")
    assert 'resources: [.copy("Voices/heart.safetensors"), .copy("Voices/heart-18m.safetensors")]' in package


@pytest.mark.parametrize("standard", [True, False], ids=["100M", "18M"])
def test_hearts_file_is_read_where_the_build_put_it(tmp_path: Path, standard: bool) -> None:
    """The review of 27 Oct (swift-voice-1): the voice service's bundle holds heart's file in Contents/Resources (Swift
    6.4's default build, swiftbuild) or at its top (the native build, Swift 6.2's default, which Package.swift
    accepts). Doctor reads the first that is there in her release (`voice.pocket_shipped`), the default build's when
    neither is, so a flat bundle is never "missing"; the file of the Mac's own model (owner, 29 Oct: the 18M's
    beside the 100M's), never the other model's."""
    places = voice.POCKET_SHIPPED if standard else voice.POCKET_SHIPPED_18M
    deep, flat = places
    sha = places[deep]
    other = voice.POCKET_SHIPPED_18M if standard else voice.POCKET_SHIPPED
    for path in other:  # the other model's file, in both places: never read for this one
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(b"the other model's")
    assert voice.pocket_shipped(tmp_path, standard) == {deep: sha}  # none there: the default build's place, missing
    assert voice.pocket_files(tmp_path, voice.pocket_shipped(tmp_path, standard)) == "missing"
    (tmp_path / flat).write_bytes(b"not heart")
    assert voice.pocket_shipped(tmp_path, standard) == {flat: sha}
    assert voice.pocket_files(tmp_path, voice.pocket_shipped(tmp_path, standard)) == "changed"
    (tmp_path / deep).symlink_to(tmp_path / flat)  # a link there is there (and never the pinned file)
    assert voice.pocket_shipped(tmp_path, standard) == {deep: sha}
    assert voice.pocket_files(tmp_path, voice.pocket_shipped(tmp_path, standard)) == "changed"


@pytest.mark.parametrize("memory", [8.0, 16.0, 18.0, 24.0, 29.9, 30.0, 32.0, 36.0, 64.0])
def test_the_pocket_model_follows_the_macs_gemma(memory: float) -> None:
    """Owner, 26 Oct: "the Pocket model follows the Gemma tier": a Mac setup gives Gemma 4 26B speaks the 100M model,
    one it gives 12B (or none: under 16 GB) the 18M, as the voice service decides it (VoiceCore `PocketTTS.english`)."""
    from local_assistant.setup import GEMMA_26B, choose_model

    choice = choose_model(memory)
    assert voice.pocket_standard(memory) is (choice is not None and choice.tag == GEMMA_26B)


def test_the_macs_memory_is_pinned_in_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tests pin the Mac's memory at 32 GB (conftest: never the Mac's own), so the suite passes the same on a 16 GB
    Mac; without a figure, the bridge reads it."""
    assert voice.memory_gb() == 32.0 and voice.pocket_standard() is True
    monkeypatch.setattr(voice, "memory_gb", lambda: 16.0)
    assert voice.pocket_standard() is False


def test_pocket_files_are_checked_against_their_pins(tmp_path: Path) -> None:
    """Every pinned file there with its bytes: "ok"; one not there yet: "missing"; other bytes, a link or a folder in
    its place: "changed" (never run); a folder this user cannot read: "unread"."""
    pins = {
        "pocket-tts/a/x.bin": hashlib.sha256(b"x").hexdigest(),
        "pocket-tts/b/y.bin": hashlib.sha256(b"y").hexdigest(),
    }
    assert pocket_files(tmp_path, pins) == "missing"
    (tmp_path / "pocket-tts/a").mkdir(parents=True)
    (tmp_path / "pocket-tts/a/x.bin").write_bytes(b"x")
    assert pocket_files(tmp_path, pins) == "missing"
    (tmp_path / "pocket-tts/b").mkdir(parents=True)
    (tmp_path / "pocket-tts/b/y.bin").write_bytes(b"Y")
    assert pocket_files(tmp_path, pins) == "changed"
    (tmp_path / "pocket-tts/b/y.bin").write_bytes(b"y")
    assert pocket_files(tmp_path, pins) == "ok"
    (tmp_path / "pocket-tts/b/y.bin").unlink()
    (tmp_path / "pocket-tts/b/y.bin").symlink_to(tmp_path / "pocket-tts/a/x.bin")
    assert pocket_files(tmp_path, pins) == "changed"
    (tmp_path / "pocket-tts/b/y.bin").unlink()
    (tmp_path / "pocket-tts/b/y.bin").mkdir()
    assert pocket_files(tmp_path, pins) == "changed"
    (tmp_path / "pocket-tts/a").chmod(0)
    try:
        assert pocket_files(tmp_path, pins) == "unread"
    finally:
        (tmp_path / "pocket-tts/a").chmod(0o700)


SWIFT_LANGUAGES = Path(__file__).resolve().parents[1] / "tools/voice/Sources/VoiceCore/PocketLanguages.swift"


def test_pocket_languages_are_the_voice_services() -> None:
    """Owner, 25 Oct ("jane jane, lets have her as default for those languages"): jane in Spanish, Italian, Portuguese,
    Dutch, German and French is Pocket TTS's, each language's full model, the same six in both (VoiceCore
    `PocketTTS.languages`); doctor checks the language in use against the voice service's own pins (each file's folder
    and sha256 the same in both), every one at its revision, sha256 computed from the official download."""
    swift = SWIFT_LANGUAGES.read_text(encoding="utf-8")
    spoken = re.findall(
        r'PocketSpeech\(voice: languageVoice, model: \.([a-z]+), speaks: languageVoice, health: "pocket_([a-z]{2})"',
        swift,
    )
    assert [code for _, code in spoken] == list(POCKET_LANGUAGES) == ["es", "it", "pt", "nl", "de", "fr"]
    assert LANGUAGE_VOICE == "jane" and 'public static let languageVoice = "jane"' in swift
    names = dict((code, name) for name, code in spoken)
    assert set(voice.POCKET_LANGUAGE_FILES) == set(POCKET_LANGUAGES)
    for code, pins in voice.POCKET_LANGUAGE_FILES.items():
        name = names[code]
        assert set(pins) == {
            f"pocket-tts/{name}/weights/model.safetensors",
            f"pocket-tts/{name}/tokenizer/tokenizer.json",
            f"pocket-tts/{name}/jane/jane.safetensors",
        }
        block = swift.split(f"public static let {name} = language(\n", 1)[1].split("head:", 1)[0]
        for path, sha in pins.items():
            assert re.fullmatch(r"[0-9a-f]{64}", sha) and f'"{sha}"' in block, path
    assert voice.POCKET_LANGUAGE_FILES["de"]["pocket-tts/german/jane/jane.safetensors"] == (
        "bd1bc91fd38a724b14f5c9cb4d67327f968568beb28d72fe7bb81f0fba75b685"
    )


async def test_jane_speaks_the_six_languages_and_the_english_voice_stays_englishs() -> None:
    """jane goes with every request in Spanish, Italian, Portuguese, Dutch, German and French (Pocket TTS's model of
    each); English keeps her voice of now. Korean, Polish, Japanese and Russian, which Pocket TTS has no model for,
    have no voice (owner, 3 Nov 2026): nothing is asked in them."""
    with FakeVoiceService() as service:
        client = VoiceClient(service.url, voice="eve", language="de")
        await client.speak("Hallo.")
        await client.speak("Shall I switch heater off?", language="en")  # code's own words: English, in eve
        await client.speak("Hoi.", language="nl")
        client.set_language("ko")
        with pytest.raises(VoiceNoLanguageError):
            await client.speak("네.")
        bodies = [json.loads(request[3]) for request in service.requests]
        assert [body["voice"] for body in bodies] == ["jane", "eve", "jane"]
        assert [client.voice_for(code) for code in ("en", "fr", "pl", "ja")] == ["eve", "jane", "eve", "eve"]
        client.set_voice("michael")  # /voice sets the English voice only
        assert client.voice_for("de") == "jane" and client.voice_for("en") == "michael"
