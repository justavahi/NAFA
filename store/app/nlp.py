"""Text preparation (stage II of the classic B0 pipeline): lowercase, stop-words, lemmas.
pymorphy3 gives Russian lemmas; if it is unavailable we degrade to plain lowercase tokens (search still works)."""
from __future__ import annotations

import re
from functools import lru_cache

try:  # pragma: no cover - import guard
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:  # noqa: BLE001
    _MORPH = None

_TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)

# Small built-in list (avoids a runtime NLTK corpus download inside Docker). Extend in config if needed.
STOPWORDS = frozenset("""
и в во не что он на я с со как а то все она так его но да ты к у же вы за бы по только ее мне было вот от меня еще нет о из ему
теперь когда даже ну вдруг ли если уже или ни быть был него до вас нибудь опять уж вам сказал ведь там потом себя ничего ей может
они тут где есть надо ней для мы тебя их чем была сам чтоб без будто чего раз тоже себе под будет ж тогда кто этот того потому
этого какой совсем ним здесь этом один почти мой тем чтобы нее сейчас были куда зачем всех никогда можно при наконец два об другой
хоть после над больше тот через эти нас про всего них какая много разве три эту моя впрочем хорошо свою этой перед иногда лучше
чуть том нельзя такой им более всегда конечно всю между это также который которые которая которого
the a an of to in and or for on at by with from is are was were be as it its this that
""".split())


# Dependency-free fallback stemmer. Full lemmatisation uses pymorphy3 when installed;
# the fallback keeps inflected Russian words searchable in minimal / offline test installs.
_RU_SUFFIXES = tuple(sorted((
    "иями", "ями", "ами", "иями", "ившись", "ывшись", "ившись", "евшись",
    "иями", "ого", "ему", "ому", "ыми", "ими", "ее", "ие", "ые", "ое",
    "ей", "ий", "ый", "ой", "ая", "яя", "ую", "юю", "ых", "их", "ым", "им",
    "ом", "ем", "ам", "ям", "ах", "ях", "ов", "ев", "ей", "иям", "ием",
    "ью", "ию", "ья", "ия", "а", "я", "ы", "и", "у", "ю", "е", "о", "ь",
), key=len, reverse=True))

def _fallback_ru_stem(token: str) -> str:
    # Avoid reducing short words to empty stems. This is intentionally modest, not
    # a substitute for a morphological analyser; it is a robust search fallback.
    word = token.lower().replace("ё", "е")
    if len(word) < 4 or not re.fullmatch(r"[а-я]+", word):
        return word
    for suffix in _RU_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[:-len(suffix)]
    return word

@lru_cache(maxsize=50000)
def _lemma(token: str) -> str:
    if not re.search(r"[а-яё]", token):
        return token
    if _MORPH is not None:
        try:
            return _MORPH.parse(token)[0].normal_form
        except Exception:  # dictionary/environment failure -> safe search fallback
            pass
    return _fallback_ru_stem(token)


def tokens(text: str | None) -> list[str]:
    if not text:
        return []
    return [t.lower().replace("ё", "е") for t in _TOKEN.findall(text)]


def lemmatize(text: str | None) -> list[str]:
    out = []
    for t in tokens(text):
        if t in STOPWORDS:
            continue
        if len(t) < 2 and not t.isdigit():
            continue
        lem = _lemma(t).replace("ё", "е")
        if lem in STOPWORDS:
            continue
        out.append(lem)
    return out


def lemma_text(text: str | None) -> str:
    return " ".join(lemmatize(text))


def detect_language(text: str | None) -> str:
    if not text:
        return "unknown"
    cyr = len(re.findall(r"[а-яёА-ЯЁ]", text))
    lat = len(re.findall(r"[a-zA-Z]", text))
    if cyr == 0 and lat == 0:
        return "unknown"
    return "ru" if cyr >= lat else "en"


def fts_query(query: str) -> str | None:
    """Lemmatised OR-query for FTS5. Tokens are [a-z0-9а-я] only, so quoting is injection-safe."""
    toks = list(dict.fromkeys(lemmatize(query)))
    if not toks:
        return None
    return " OR ".join(f'"{t}"' for t in toks)
