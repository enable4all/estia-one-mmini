"""Whether her answer promises a lookup it never made ("let me check", "I'll look that up", "one moment", "let me
search"), in the 11 languages (claims.promises_a_lookup): brain's lookup guard asks her once more when such an answer
came with no tool call (owner, 22 Oct: "yes, do the fix"). Real phrasings, and the near misses that must stay
answers: "let me know", a lookup done earlier, a question back, an offer, a condition and a negation."""

import pytest

from local_assistant.claims import (
    LOOKUP_LATER,
    LOOKUP_PROMISE,
    lookup_clauses,
    only_promises_a_lookup,
    promises_a_lookup,
)
from local_assistant.language import CODES

PROMISES = [
    # English
    "Let me check that for you.",
    "Let me look that up.",
    "I'll look it up for you.",
    "I’ll check the opening hours.",
    "Sure, I'll search for tomorrow's weather in Lyon.",
    "I will check the score now.",
    "I'm going to look into it.",
    "Let me find out.",
    "Let me just check the forecast.",
    "Let me have a quick look.",
    "Let me see what I can find.",
    "I'm checking now.",
    "I'm looking it up.",
    "Checking the latest score now.",
    "Okay, looking that up for you.",
    "One moment.",
    "One moment, please.",
    "Just a moment while I check.",
    "Give me a second to look.",
    "Hold on, let me check.",
    "Good question. Let me search.",
    "Let me double check the time.",  # review L3: the misses
    "Let me double-check.",
    "Let me pull that up for you.",
    "Let me pull up the forecast.",
    "Hold on a second.",
    "Hang on a moment, I'll look.",
    "Just a minute.",
    "One minute while I check.",
    "One minute please.",
    "Give me a minute.",
    "Bear with me a moment.",
    "Bear with me while I check.",
    "**Let me check** the score.",  # as the phone shows it: no markers
    "- I'll look up the hours.",  # a list line's start
    # Dutch
    "Ik zoek het even voor je op.",
    "Laat me even kijken.",
    "Momentje, ik kijk het na.",
    "Ik ga het opzoeken.",
    "Ik check het even.",
    # German
    "Ich schaue mal nach.",
    "Lass mich kurz nachsehen.",
    "Einen Moment bitte.",
    "Ich werde das gleich prüfen.",
    # French
    "Je vais vérifier.",
    "Laisse-moi chercher.",
    "Un instant, je regarde ça.",
    "Je me renseigne tout de suite.",
    # Spanish
    "Déjame buscarlo.",
    "Voy a comprobarlo.",
    "Un momento, lo busco.",
    "Lo consulto ahora mismo.",
    # Italian
    "Fammi controllare.",
    "Controllo subito.",
    "Un attimo, lo cerco.",
    "Vado a cercare.",
    # Portuguese
    "Deixa-me verificar.",
    "Vou pesquisar isso.",
    "Um momento, vou procurar.",
    # Polish
    "Sprawdzę to.",
    "Chwileczkę, poszukam.",
    # Russian
    "Сейчас проверю.",
    "Минутку, поищу.",
    # Japanese
    "調べてみます。",
    "少々お待ちください。確認します。",
    # Korean
    "찾아볼게요.",
    "잠시만요, 확인해 볼게요.",
]


@pytest.mark.parametrize("answer", PROMISES)
def test_a_promised_lookup_is_read_in_every_language(answer: str) -> None:
    assert promises_a_lookup(answer), answer


NEAR_MISSES = [
    # English
    "Let me know if you want more.",
    "I checked earlier and it was sunny.",
    "I've checked: it opens at 9.",  # a lookup told as done (another guard's question)
    "Shall I look it up?",  # a question back
    "Do you want me to check the forecast?",
    "Which Lyon do you mean? Then I'll check.",  # she waits for the user's answer
    "If you like, I can look it up.",  # an offer
    "I can look it up if you want.",
    "I'll look it up if you'd like.",
    "When you tell me the city, I'll check the forecast.",  # a condition: she waits
    "Once you send the name, I'll look it up.",
    "I can't check live scores.",
    "I won't look it up.",
    "I'll look forward to hearing how it went.",
    "I'll check in with you tomorrow.",
    "Checking the weather before you leave is a good habit.",
    "One moment of calm can help.",
    "Take a moment to check the address.",
    "Let me explain how it works.",
    "Lyon is usually mild in October, around 15 degrees.",
    "Hold on to that idea.",
    "One minute.",  # review L3: a plain answer ("How long do I steep it?"), no wait of hers
    "A minute.",
    "About a minute.",
    "Hang on to the receipt.",
    "Hold on tight.",
    "Let me pull over first.",
    "Bear with me, this takes a while to explain.",
    # Dutch
    "Laat me weten of je meer wilt.",
    "Ik heb het gisteren al opgezocht.",
    "Ik zoek het niet op.",
    "Zal ik het opzoeken?",
    # German
    "Lass mich wissen, ob du mehr willst.",
    "Ich habe vorhin nachgesehen.",
    "Soll ich nachsehen?",
    # French
    "Fais-moi savoir si tu veux plus.",
    "J'ai vérifié hier.",
    "Je ne vais pas vérifier.",
    # Spanish
    "Déjame saber si necesitas algo.",
    "Ayer lo comprobé.",
    "¿Quieres que lo busque?",
    # Italian
    "Fammi sapere se vuoi altro.",
    "Cercherò di aiutarti.",
    "Sto cercando di capire.",
    "Ho controllato ieri.",
    # Portuguese
    "Deixe-me saber se precisar.",
    "Verifiquei ontem.",
    # Polish
    "Daj mi znać, jeśli chcesz więcej.",
    "Sprawdziłam wczoraj.",
    "Nie sprawdzę tego.",
    # Russian
    "Дай мне знать, если нужно.",
    "Я проверила вчера.",
    # Japanese
    "昨日確認しました。",
    "調べましょうか。",  # a question by its か
    "教えていただければ調べます。",  # a condition: she waits
    # Korean
    "어제 확인했어요.",
    "알려주시면 찾아볼게요.",  # a condition: she waits
    "찾아볼까요?",
]


@pytest.mark.parametrize("answer", NEAR_MISSES)
def test_an_answer_that_only_sounds_like_a_lookup_is_none(answer: str) -> None:
    assert not promises_a_lookup(answer), lookup_clauses(answer)


def test_every_language_has_its_promises() -> None:
    assert set(LOOKUP_PROMISE) == set(CODES) == set(LOOKUP_LATER)


def test_the_clauses_that_promise_are_named() -> None:
    assert lookup_clauses("Lyon is lovely. Let me check the forecast for tomorrow.") == [
        "Let me check the forecast for tomorrow"
    ]
    assert lookup_clauses("") == [] and lookup_clauses("Sunny, 16 degrees.") == []


ANSWERED_AFTER_THE_PROMISE = [
    # review of 0.9.8, guards-3: the promise with the answer right after it in the same reply
    "Let me take a look: it's a monstera, a tropical plant that likes bright, indirect light.",
    "Let me check. The capital of Australia is Canberra.",
    "Let me check: 366 days.",
    "One moment. Lyon tomorrow: 16°C and sunny.",
    "Laat me even kijken. Het is een monstera.",
    "確認します。キャンベラです。",
    # a wait word inside an answer stays an answer (final review of 0.9.8, guards-3-lookup-guard-weakened)
    "Let me check. At the moment, the museum is closed for repairs.",
    "Let me take a look: water it every second week, in bright light.",
    "Let me check: hang on to the receipt, returns are taken within a month.",
    "Lass mich nachsehen. Warte nicht zu lange mit dem Gießen.",
]
# final review of 0.9.8, guards-3-lookup-guard-weakened: a wait or a later answer after the promise is more promise,
# in the 11 languages
WAITED_AFTER_THE_PROMISE = [
    "Let me check the latest news for you. It might take a moment.",
    "I'll look up the opening hours now. Please hold on a moment.",
    "Let me look that up. Stay tuned, Alex!",
    "Let me search for that. I'll be right back with the answer.",
    "Let me check the forecast. Bear with me, Alex.",
    "Laat me even kijken. Dit kan even duren.",
    "Ich schaue das kurz nach. Einen Moment bitte, gleich habe ich es.",
    "Je vais vérifier. Ça peut prendre un instant.",
    "Voy a comprobarlo. Ahora vuelvo con la respuesta.",
    "Fammi controllare. Torno subito con la risposta.",
    "Vou pesquisar isso. Já volto com a resposta.",
    "Sprawdzę to. Zaraz wracam z odpowiedzią.",
    "Сейчас проверю. Подождите немного, пожалуйста.",
    "調べてみます。少し時間がかかるかもしれません。",
    "확인해 볼게요. 시간이 좀 걸릴 수 있어요.",
]
LEFT_ALONE = [
    "Let me check. I'll get back to you.",
    "Let me look that up and let you know.",
    "Let me check. One moment.",
    "It's a monstera. Let me check how often to water it.",  # an answer before it, the promise last
    "Let me check that for you!",
]


@pytest.mark.parametrize("answer", PROMISES + LEFT_ALONE + WAITED_AFTER_THE_PROMISE)
def test_a_promise_with_nothing_after_it_is_a_promise_alone(answer: str) -> None:
    assert only_promises_a_lookup(answer), answer


@pytest.mark.parametrize("answer", ANSWERED_AFTER_THE_PROMISE)
def test_a_promise_with_the_answer_after_it_is_none_alone(answer: str) -> None:
    assert promises_a_lookup(answer) and not only_promises_a_lookup(answer), answer


@pytest.mark.parametrize("answer", NEAR_MISSES)
def test_no_promise_is_no_promise_alone(answer: str) -> None:
    assert not only_promises_a_lookup(answer), answer
