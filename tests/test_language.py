import unicodedata
from dataclasses import astuple

import pytest

from local_assistant import language, prompts
from local_assistant.config import DEFAULT_NAME
from local_assistant.language import CODES, LANGUAGES, YES_WORDS, find, is_yes

NAME = DEFAULT_NAME  # her name in these tests (a fresh install's)


def test_the_eleven_languages_english_first_then_by_whisper_error_rate() -> None:
    assert CODES == ("en", "es", "it", "ko", "pt", "nl", "pl", "de", "ja", "ru", "fr")
    assert list(LANGUAGES) == list(CODES)
    assert LANGUAGES["nl"].english == "Dutch" and LANGUAGES["nl"].own == "Nederlands"
    assert LANGUAGES["ja"].own == "日本語" and LANGUAGES["ko"].own == "한국어" and LANGUAGES["ru"].own == "Русский"
    assert all(language.code == code for code, language in LANGUAGES.items())


@pytest.mark.parametrize(
    ("said", "code"),
    [
        ("nl", "nl"),
        ("NL", "nl"),
        ("dutch", "nl"),
        ("Nederlands", "nl"),
        ("español", "es"),
        ("espanol", "es"),  # accents are optional
        ("Français", "fr"),
        ("francais", "fr"),
        ("português", "pt"),
        ("русский", "ru"),
        ("日本語", "ja"),
        ("한국어", "ko"),
        ("  German ", "de"),
    ],
)
def test_a_language_is_found_by_code_english_name_or_own_name(said: str, code: str) -> None:
    found = find(said)
    assert found is not None and found.code == code


@pytest.mark.parametrize("said", ["", "klingon", "greek", "e", "english please"])
def test_anything_else_is_no_language(said: str) -> None:
    assert find(said) is None


def test_the_yes_words_of_every_language() -> None:
    for word in ("yes", "ok", "ja", "sí", "si", "sì", "oui", "tak", "да", "はい", "ええ", "네", "예", "sim"):
        assert word in YES_WORDS, word
    assert all(word == word.casefold() and "," not in word for word in YES_WORDS)  # stored as they are compared
    assert not {"no", "nee", "nein", "non", "nie", "нет", "いいえ", "아니요", "não"} & YES_WORDS


@pytest.mark.parametrize(
    ("said", "code", "expected"),
    [
        ("[call] yes please", "en", True),
        ("[call] Yes.", "en", True),
        ("Ja, graag!", "nl", True),
        ("[call] yes", "nl", True),  # English is code's language: a yes in it counts on any call
        ("はい。", "ja", True),
        ("ja", "en", False),  # another language's yes: not the call's
        ("[call] yes, and what about the weather?", "en", False),  # the whole message must be the yes
        ("[call] not now", "en", False),
        ("[audio file] yes", "en", False),  # someone else's voice is no yes
        ("[call] (cutting in) yes", "en", True),  # said over the end of her answer (review of 0.9.7, C19)
        ("[call] (cutting in) Ja, graag.", "nl", True),
        ("[call] (cutting in) yes, and the weather?", "en", False),
    ],
)
def test_a_yes_in_the_calls_language_or_english(said: str, code: str, expected: bool) -> None:
    assert is_yes(said, code) is expected


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


@pytest.mark.parametrize("calendar", [False, True])
def test_english_adds_nothing_to_the_rules(calendar: bool) -> None:
    every = prompts.rules(name=NAME, calendar=calendar, calls=True, music=True, web=True, maps=True)
    assert (
        prompts.rules(name=NAME, calendar=calendar, calls=True, music=True, web=True, maps=True, language="en") == every
    )
    assert prompts.rules(name=NAME, language="en") == prompts.rules(name=NAME)


def test_another_language_is_one_code_written_line_in_the_rules() -> None:
    english, dutch = prompts.rules(name=NAME).splitlines(), prompts.rules(name=NAME, language="nl").splitlines()
    changed = [(old, new) for old, new in zip(english, dutch, strict=True) if old != new]
    assert changed == [
        (
            "- Reply in the language the user writes in.",
            "- Answer only in Nederlands (Dutch), whatever language the user writes in. Keep names, code and quotes "
            "as they are.",
        )
    ]
    assert "Answer only in 日本語 (Japanese)" in prompts.rules(name=NAME, language="ja", calls=True)


def test_every_language_has_its_twelve_spoken_call_lines() -> None:
    """Eight, since 18 Oct standby's "I'm still here." (owner), and since the review of 0.9.7 (C18) the length cap's
    goodbye and the line after an answer that never came ("I lost my train of thought"), in the call's language; since
    the second deep scan of 0.9.8 (dialogue-call-lost-loop) the one after a second answer in a row that took too long."""
    assert set(prompts.CALL_SPOKEN) == set(CODES)
    for code in CODES:
        spoken = prompts.spoken(code, name=NAME)
        lines = astuple(spoken)
        assert len(lines) == 12 and len(set(lines)) == 12, code
        assert all(line.strip() == line and 0 < len(line) < 90 and "\n" not in line for line in lines), code
        assert _plain(LANGUAGES[code].own)[:5] in _plain(spoken.now_speaking), code  # "from now on I speak Dutch"
    for code in ("es", "it", "pt", "nl", "pl", "de", "fr"):  # Latin letters: the voice can say her name
        assert NAME in prompts.spoken(code, name=NAME).greeting, code


def test_the_portuguese_goodbye_is_one_sentence_that_ends_the_call() -> None:
    """Owner, 25 Oct: jane dropped the "Tchau." of "Até breve! Tchau.": one sentence, still her goodbye."""
    from local_assistant.calls import ends_with_goodbye

    bye = prompts.spoken("pt", name=NAME).bye
    assert bye == "Até breve, tchau!" and prompts.call_lines("pt", name=NAME).bye == (bye,)
    assert ends_with_goodbye(bye, name=NAME, language="pt")


@pytest.mark.parametrize("code", CODES)
def test_the_length_caps_goodbye_and_the_lost_line_are_the_calls_language(code: str) -> None:
    """Review of 0.9.7 (C18): the 60-minute goodbye was always English, and in the ten other languages an answer that
    never came got "could you say that again?", as if she had not heard. Translations need a native speaker's check;
    Polish and Russian stay free of grammatical gender (no past tense of hers)."""
    lines, spoken = prompts.call_lines(code, name=NAME), prompts.spoken(code, name=NAME)
    assert lines.max_bye == spoken.max_bye and lines.lost == spoken.lost
    for line in (lines.max_bye, lines.lost):
        assert " ?" not in line and " !" not in line and NAME not in line, code
    if code == "en":
        assert (lines.max_bye, lines.lost) == (prompts.CALL_MAX_BYE, prompts.CALL_LOST)
    else:
        assert lines.max_bye != prompts.CALL_MAX_BYE and lines.lost != prompts.CALL_LOST, code
        assert lines.lost not in (spoken.again, lines.hearing), code  # it says why: her own thought, not his words


@pytest.mark.parametrize("code", CODES)
def test_call_lost_twice_line_all(code: str) -> None:
    """The second deep scan (dialogue-call-lost-loop): the second answer in a row that takes too long on a call asks
    for a text in the call's language, never "say that again" a second time. Translations need a native speaker's
    check; Polish and Russian stay free of grammatical gender."""
    lines, spoken = prompts.call_lines(code, name=NAME), prompts.spoken(code, name=NAME)
    assert lines.too_long == spoken.too_long and lines.too_long not in (lines.lost, spoken.again, lines.hearing)
    assert " ?" not in lines.too_long and " !" not in lines.too_long and NAME not in lines.too_long, code
    if code == "en":
        assert lines.too_long == prompts.CALL_TOO_LONG
    else:
        assert lines.too_long != prompts.CALL_TOO_LONG, code


def test_her_words_on_a_call_fit_every_caller() -> None:
    """Review of 0.9.7 (C18): the Portuguese greeting "é a {name}" is feminine, for the male names setup offers too;
    the thinking line "Good question." praises the question, which her style forbids; "I got your text" was said for
    a photo, a voice note or a document too."""
    assert prompts.spoken("pt", name="Teo").greeting == "Olá, aqui é Teo. Em que posso ajudar?"
    assert not any("question" in line.casefold() for line in prompts.CALL_THINKING_LINES)
    assert prompts.CALL_TEXT_WAITS_SPOKEN["en"] == "I got your message. I'll answer it after the call."


def test_english_calls_keep_the_richer_lines() -> None:
    english = prompts.call_lines("en", name=NAME)
    assert english.greetings == prompts.call_greetings(NAME) and english.acknowledge == prompts.CALL_ACK_LINES
    assert english.thinking == prompts.CALL_THINKING_LINES and english.checking == prompts.CALL_CHECKING_LINES
    assert (english.idle, english.lost, english.hearing) == (
        prompts.CALL_IDLE.format(name=NAME),
        prompts.CALL_LOST,
        prompts.CALL_HEARING,
    )
    assert english.still_here == prompts.CALL_STILL_HERE
    assert english.bye == prompts.CALL_BYE_LINES


def test_other_languages_use_their_table_and_their_own_got_it() -> None:
    dutch, spoken = prompts.call_lines("nl", name=NAME), prompts.spoken("nl", name=NAME)
    # owner, 25 Oct: a lone "Begrepen." was heard as "Wefrijbe." in Pocket's jane every time
    assert dutch.greetings == (spoken.greeting,) and dutch.acknowledge == ("Oké, begrepen.",)
    assert dutch.thinking == dutch.checking == (spoken.moment,)
    assert (dutch.idle, dutch.lost, dutch.hearing, dutch.bye, dutch.still_here) == (
        spoken.take_your_time,
        spoken.lost,
        spoken.again,
        (spoken.bye,),
        "Ik ben er nog.",
    )


# owner 2026-10-11: the acknowledgement is the language's natural short "got it"; a voice garbles humming sounds
GOT_IT = {
    "en": "Got it.",
    "nl": "Oké, begrepen.",  # owner, 25 Oct: a lone "Begrepen." was garbled in Pocket's jane
    "de": "Verstanden.",
    "fr": "Compris.",
    "es": "Entendido.",
    "it": "Capito.",
    "pt": "Entendi.",
    "pl": "Rozumiem.",
    "ru": "Понятно.",
    "ja": "わかりました。",
    "ko": "알겠어요.",
}
HUMS = {"hmm", "hm", "mm", "mhm", "mm-hm", "uh-huh", "uh", "um", "ah", "äh", "euh", "эм", "мм"}


@pytest.mark.parametrize("code", CODES)
def test_every_language_acknowledges_with_its_own_got_it_never_a_hum(code: str) -> None:
    acknowledge = prompts.call_lines(code, name=NAME).acknowledge
    assert acknowledge[0] == GOT_IT[code]
    assert all(line.strip() == line and 0 < len(line) < 24 for line in acknowledge)
    words = {word.strip(".,!?。") for line in acknowledge for word in line.lower().split()}
    assert not words & HUMS, code
    if code != "en":
        assert not set(acknowledge) & set(prompts.CALL_ACK_LINES), code  # its own language, not English


@pytest.mark.parametrize("code", CODES)
def test_every_language_wakes_from_standby_with_its_own_short_yes_never_a_hum(code: str) -> None:
    """Her wake line when her name ends standby on a call (owner, 22 Oct): short, the call's language, words that
    Kokoro and Supertonic-3 say clearly (no hum), no space before "?" (a line can be spoken through spoken_text)."""
    woken = prompts.call_lines(code, name=NAME).woken
    assert woken and all(line.strip() == line and 0 < len(line) < 24 for line in woken)
    words = {word.strip(".,!?。？") for line in woken for word in line.lower().split()}
    assert not words & HUMS, code
    assert not any(" ?" in line or " !" in line for line in woken)
    if code != "en":
        assert not set(woken) & set(prompts.CALL_WOKEN["en"]), code  # its own language, not English
    else:
        assert woken == ("Yes?", "I'm here.", "Yes, I'm here.", "I'm listening.")


def test_the_examples_variations_are_kept() -> None:
    assert "Alles klar." in prompts.call_lines("de", name=NAME).acknowledge
    assert "D'accord, compris." in prompts.call_lines("fr", name=NAME).acknowledge


@pytest.mark.parametrize("code", CODES)
def test_every_language_opens_a_wake_up_call_and_a_call_reminder_with_the_time(code: str) -> None:
    """The opening lines of the calls she places (spoken in the call's language; translations need a native check)."""
    lines = prompts.call_lines(code, name=NAME)
    for line in (lines.wake, lines.call_reminder):
        assert line.count("{time}") == 1 and line.strip() == line, code
        said = line.format(time="7:00")
        assert "7:00" in said and " ?" not in said and " !" not in said, code
    assert lines.wake != lines.call_reminder


def test_the_english_opening_lines() -> None:
    english = prompts.call_lines("en", name=NAME)
    assert english.wake.format(time="7:00") == "Good morning! It's 7:00."
    assert english.call_reminder.format(time="17:00") == "It's 17:00. You asked me to call you."


@pytest.mark.parametrize("code", CODES)
def test_every_language_says_on_a_call_that_a_text_waits_for_its_end(code: str) -> None:
    """Her line on a call when the caller's text waits for the call's end (owner, 23 Oct): the call's language, as her
    wake line (English the fallback), two short sentences, no space before "?" or "!" (a line can be spoken through
    spoken_text), never her name (Japanese, Korean and Russian voices cannot read Latin letters)."""
    line = prompts.call_lines(code, name=NAME).text_waits
    assert line == prompts.CALL_TEXT_WAITS_SPOKEN[code]
    assert line.strip() == line and 0 < len(line) < 80 and "\n" not in line, code
    assert " ?" not in line and " !" not in line and NAME not in line, code
    if code == "en":
        assert line == "I got your message. I'll answer it after the call."
    else:
        assert line != prompts.CALL_TEXT_WAITS_SPOKEN["en"], code  # its own language, not English


def test_a_text_that_waits_for_the_calls_end_gets_codes_line_in_the_chat() -> None:
    """The chat's line (owner, 23 Oct) is code's English, as code's other notes are."""
    assert prompts.CALL_TEXT_WAITS == "Got it: I'll answer this after the call."
    assert prompts.CallLines((), (), (), (), "", "", "", ()).text_waits == prompts.CALL_TEXT_WAITS_SPOKEN["en"]


# /babble: one practice line replaces the language line; every rules text says practice exists


def _changed(first: str, second: str) -> list[tuple[str, str]]:
    return [(old, new) for old, new in zip(first.splitlines(), second.splitlines(), strict=True) if old != new]


@pytest.mark.parametrize("language", ["en", "de"])
def test_practice_puts_its_line_in_place_of_the_language_line(language: str) -> None:
    chat = prompts.rules(name=NAME, language=language)
    practice = prompts.rules(name=NAME, language=language, practice=("nl", "A2"))
    old = prompts._ANY_LANGUAGE if language == "en" else prompts.LANGUAGE_RULE.format(own="Deutsch", english="German")
    assert old in chat and old not in practice
    [line] = [line for line in practice.splitlines() if line.startswith("- Language practice (Dutch, A2)")]
    assert "Nederlands (Dutch)" in line and prompts.PRACTICE_LEVELS["A2"] in line
    for needed in ("→", "[voice]", "[call]", "one to three short sentences", "exactly one question"):
        assert needed in line, needed


def test_the_practice_line_keeps_the_tested_prompts_lines() -> None:
    # the tutor test measured "→" precision and false corrections with these lines (prompt.txt)
    line = prompts.PRACTICE_RULE
    assert "a warm, patient conversation partner" in line
    assert "Most replies have no such line." in line
    assert (
        "No explanations, grammar terms, translations or lists during the session: explanations come in the recap "
        "at the end." in line
    )
    assert "no joke or pun" in line  # her built-in style ends banter with a joke: never after the question


def test_the_arrow_clause_is_the_tested_one_without_a_placeholder() -> None:
    # the tested clause (prompt.txt); a "<...>" placeholder was copied into replies ("ho andato <sono andato>")
    line = prompts.PRACTICE_RULE.format(english="Italian", own="Italiano", level="A2", level_line="A2.")
    assert "only the user's few words, corrected, in Italiano, and nothing else on that line." in line
    assert "Add it only when you are sure the words are wrong" in line
    assert "and only when the error changes the meaning or the user repeats it" in line
    assert "<" not in line and ">" not in line
    assert "none in a reply to [voice] or [call]" in line and "Never more than one." in line


@pytest.mark.parametrize("on", [False, True])
def test_practice_rules_leave_out_every_tool_instruction(on: bool) -> None:
    practice = prompts.rules(
        name=NAME, practice=("nl", "A2"), calendar=on, web=on, maps=on, calls=on, music=on, reminders=True
    )
    assert practice == prompts.rules(name=NAME, practice=("nl", "A2"))  # no tool section, whatever is configured
    for tool in (
        "call remember",
        "call forget",
        "search_history",
        "learn_link",
        "read_link",
        "mini_status",
        "project_list",
        "mail_digest",
        "device_set",
        "calendar_agenda",
        "web_search",
        "set_reminder",
        "music_play",
        "places_search",
        "end_call",
        '"Noted: ..."',
        "with your tools",
        "while a photo is shown",
    ):
        assert tool not in practice, tool
    for heading in ("Memory:\n", "Photos:\n", "Voice notes:\n", "Safety:\n"):
        assert heading in practice, heading
    for heading in ("The Mac and the projects:", "Mail:", "Lights and plugs:", "Calendar:", "Reminders:"):
        assert heading not in practice, heading
    no_tools = prompts.PRACTICE_NO_TOOLS.format(own="Nederlands")
    assert no_tools in practice and "/babble off" in no_tools
    assert "nothing is saved, switched, read, searched or set" in no_tools
    assert len(practice) < 4_500  # the tested stand-alone prompt was about 1,300 characters; her chat rules 6,000+


def test_each_level_has_its_one_cefr_line() -> None:
    assert set(prompts.PRACTICE_LEVELS) == {"A2", "B1"}
    assert prompts.PRACTICE_LEVELS["A2"].startswith("A2 (elementary): short, simple sentences")
    assert prompts.PRACTICE_LEVELS["B1"].startswith("B1 (intermediate): clear, standard language")
    b1 = prompts.rules(name=NAME, practice=("ja", "B1"))
    assert prompts.PRACTICE_LEVELS["B1"] in b1 and "日本語 (Japanese)" in b1


def test_every_rules_text_says_practice_exists_and_the_last_setting() -> None:
    for text in (
        prompts.rules(name=NAME),
        prompts.rules(name=NAME, language="nl", calendar=True, web=True),
        prompts.rules(name=NAME, practice=("de", "B1")),
    ):
        lines = text.splitlines()
        can = next(index for index, line in enumerate(lines) if line.startswith("- You know what you learned"))
        assert lines[can + 1].startswith("- Language practice")  # the chat rules add that it is one of her things
        assert prompts.PRACTICE_CAN.removeprefix("- Language practice:") in lines[can + 1]
    last = prompts.practice_last(("nl", "A2"))
    assert last == "Last practised: Dutch, A2."
    assert prompts.practice_last(None) == "Not practised yet."
    assert f"{prompts.PRACTICE_CAN.removeprefix('- Language practice:')} {last}" in prompts.rules(
        name=NAME, practice_last=last
    )
    for english in ("English", "Dutch", "German", "Spanish", "French", "Portuguese", "Japanese", "Russian", "Italian"):
        assert english in prompts.PRACTICE_CAN, english
    for typed in ("/babble nl", "/babble nl b1", "/babble off"):
        assert typed in prompts.PRACTICE_CAN, typed


def test_the_practice_line_says_it_is_one_of_the_things_she_can_do() -> None:
    """Right after what she can do, the practice line says it is one of those things (evals, 13 Oct: "What can you
    do?" listed her tools and left practice out; with this wording 25 of 25). The sentence of her tools stays as it
    was: practice named inside it lost her end_call on a goodbye (call_goodbye 16 of 30 and 23 of 30, against 38 of 45
    as it was; this wording 36 of 45)."""
    for text in (
        prompts.rules(name=NAME),
        prompts.rules(name=NAME, projects=True, calendar=True, web=True, maps=True),
        prompts.rules(name=NAME, language="nl", web=True),
    ):
        lines = text.splitlines()
        can = next(index for index, line in enumerate(lines) if line.startswith("- You know what you learned"))
        assert lines[can + 1].startswith("- Language practice (one of the things you can do): the user can practise")
        assert "practise a language" not in lines[can]
    assert "plugs with your tools. You cannot" in prompts.rules(name=NAME)
    practice = prompts.rules(name=NAME, practice=("de", "B1"))  # the practice rules as they were (their evals)
    assert prompts.PRACTICE_CAN.startswith("- Language practice: the user") and prompts.PRACTICE_CAN in practice
    assert "one of the things you can do" not in practice


def test_every_offered_language_has_a_short_fallback_question() -> None:
    from local_assistant.babble import OFFERED

    assert set(prompts.PRACTICE_FALLBACK) == set(OFFERED)
    for code, question in prompts.PRACTICE_FALLBACK.items():
        assert "\n" not in question and question.strip() == question and len(question) < 60, code
        assert question.endswith(("?", "？")) and " ?" not in question, code
    assert prompts.PRACTICE_FALLBACK["en"] == "Can you tell me more about that?"


def test_every_language_has_its_words_that_ask_for_a_code_or_a_password() -> None:
    """initiative.check_text refuses her own message that asks for one, in any of the 11 languages."""
    assert set(language.SECRET_WORDS) == set(CODES)
    assert all(language.SECRET_WORDS[code] for code in CODES)


def test_every_language_has_its_acknowledgements_and_its_but() -> None:
    """On a call a listener's "okay" alone carries on and a "but" alone stops her (owner, 14 Oct), in the 11."""
    assert set(language.ACKNOWLEDGEMENTS) == set(CODES) and set(language.BUT) == set(CODES)
    assert all(language.ACKNOWLEDGEMENTS[code] and language.BUT[code] for code in CODES)
    english = set(language.acknowledgements("en"))
    assert {"ok", "okay", "yes", "yeah", "mm-hm", "uh-huh", "all right", "i see", "got it", "exactly"} <= english
    assert {"ja", "oké", "okee", "klopt", "precies"} <= set(language.acknowledgements("nl"))
    assert {"はい", "うん", "なるほど", "そうですね"} <= set(language.acknowledgements("ja"))
    assert {"alles klar", "genau"} <= set(language.acknowledgements("de"))
    for code in CODES:  # the sounds Whisper writes for a listener's hum, in every language
        assert {"mm-hmm", "uh huh"} <= set(language.acknowledgements(code)), code
    but = {"but", "maar", "aber", "mais", "pero", "mas", "ma", "でも", "но", "ale", "하지만", "그런데"}
    assert but == language.BUT_WORDS
    assert not {"no", "stop", "wait", "but"} & set(language.acknowledgements("en"))


def test_every_language_has_its_leave_taking() -> None:
    """On a call, "I have to go" or "I'm hanging up" ends the call after her reply (owner, 14 Oct): English, Dutch and
    German at least, the others where the phrase is clear."""
    assert set(language.LEAVING) == set(CODES) and set(language.LEAVING_SOFT) == set(CODES)
    assert all(language.LEAVING[code] for code in CODES)
    english = set(language.LEAVING["en"])
    assert {"i'm hanging up", "i'll hang up", "hanging up", "i have to go", "i've got to go", "gotta go"} <= english
    assert {"i need to go", "i'm off"} <= english
    assert {"ik hang op", "ik moet gaan"} <= set(language.LEAVING["nl"])
    assert {"ich lege auf", "ich muss los"} <= set(language.LEAVING["de"])


def test_every_language_has_its_unclear_reasons_and_thanks() -> None:
    """A reason for leaving that also complains or apologises ("something came up") counts only with nothing but soft
    words and thanks before it (review 18 Oct), in each of the 11 languages; never "I have things to do"."""
    assert set(language.LEAVING_UNCLEAR) == set(CODES) and set(language.THANKS) == set(CODES)
    assert all(language.LEAVING_UNCLEAR[code] and language.THANKS[code] for code in CODES)
    assert "something came up" in language.LEAVING_UNCLEAR["en"] and "thank you" in language.THANKS["en"]
    for code in CODES:  # a phrase is clear or unclear, never both
        assert not set(language.LEAVING[code]) & set(language.LEAVING_UNCLEAR[code]), code
    every = {phrase for words in (*language.LEAVING.values(), *language.LEAVING_UNCLEAR.values()) for phrase in words}
    assert not {"i have things to do", "i have to do something", "일이 생겼어요"} & every


def test_every_language_says_more_is_coming_in_words_no_closing_phrase_holds() -> None:
    """On a call, "wait" or "one more thing" after a goodbye, leave-taking or a request to end the call keeps it open
    (review 18 Oct), in each of the 11 languages; no closing phrase holds one, or it could never end a call."""
    assert set(language.MORE_COMING) == set(CODES) and all(language.MORE_COMING[code] for code in CODES)
    assert {"wait", "actually", "one more", "before you go"} <= set(language.MORE_COMING["en"])
    for code in CODES:
        closing = (*language.LEAVING[code], *language.LEAVING_UNCLEAR[code], *language.ENDING[code])
        for more in (*language.MORE_COMING[code], *language.MORE_COMING["en"]):
            assert not [phrase for phrase in closing if f" {more} " in f" {phrase} "], (code, more)


def test_every_language_can_ask_her_to_end_a_call() -> None:
    """On a call, "let's end the call" or "let's call it a day" ends it after her reply (owner, 18 Oct), in each of the
    11 languages; the leads beside it ("let's", "please") are never a yes that answers her."""
    assert set(language.ENDING) == set(CODES) and set(language.ENDING_SOFT) == set(CODES)
    assert all(language.ENDING[code] for code in CODES)
    assert {"end the call", "close the call", "hang up", "call it a day", "let's stop here", "let's wrap up"} <= set(
        language.ENDING["en"]
    )
    assert "stop here" not in language.ENDING["en"] and "wrap it up" not in language.ENDING["en"]  # only after a lead
    assert "end it here" not in language.ENDING["en"] and "let's end it here" in language.ENDING["en"]  # review 18 Oct
    assert "tot zover" not in language.ENDING["nl"] and "kommen wir zum ende" not in language.ENDING["de"]
    soft = {word for words in language.ENDING_SOFT.values() for word in words}
    assert not {"yes", "yeah", "exactly", "true", "fine", "good", "ja", "sí", "oui", "tak", "да", "네", "はい"} & soft


def test_every_language_has_its_stage_directions() -> None:
    """Her "(pause)", "(laughs)" or "*smiles*" is never spoken or sent (owner, 18 Oct), in each of the 11 languages."""
    assert set(language.STAGE_DIRECTIONS) == set(CODES) and set(language.STAGE_SOFT) == set(CODES)
    assert all(language.STAGE_DIRECTIONS[code] for code in CODES)
    assert {"pause", "beat", "laughs", "sighs", "smiles"} <= set(language.STAGE_DIRECTIONS["en"])
    assert {"short", "long"} <= set(language.STAGE_SOFT["en"])
    # between stars only third-person and -ing forms (review 18 Oct: "Tap the *pause* button", "Enjoy the *ride*!")
    assert set(language.STAGE_STARRED) == set(CODES) and all(language.STAGE_STARRED[code] for code in CODES)
    for code in CODES:
        assert set(language.STAGE_STARRED[code]) <= set(language.STAGE_DIRECTIONS[code]), code
    starred = {word for words in language.STAGE_STARRED.values() for word in words}
    assert {"smiles", "laughing"} <= starred and not {"pause", "smile", "beat", "laugh", "ride", "pausa"} & starred


def test_leave_takings_soft_words_are_an_okay_never_a_yes_and_what_goes_on_is_known() -> None:
    """Around leave-taking only a language's "okay", "so" or "now" (review 14 Oct: "Yes, I have to go" answers her);
    a comma part starting with CONTINUING goes on from it ("I have to go, uh, to the dentist")."""
    soft = {word for words in language.LEAVING_SOFT.values() for word in words}
    assert {"ok", "okay", "alright", "right", "oké", "vale", "alles klar"} <= soft
    assert not {"yes", "yeah", "exactly", "true", "fine", "good", "ja", "sí", "oui", "tak", "да", "네", "はい"} & soft
    assert set(language.LEAVING_NOT_ALONE["en"]) <= set(language.LEAVING["en"])
    assert set(language.CONTINUING) <= set(CODES) - {"ja", "ko"}
    assert {"to", "back"} <= set(language.CONTINUING["en"]) and "naar" in language.CONTINUING["nl"]
