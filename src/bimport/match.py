from collections.abc import Callable, Iterable

from bimport.errors import Ambiguous, NotFound
from bimport.library import Book
from bimport.text import hay, norm

THRESHOLD = 80.0
MARGIN = 15.0
MIN_FUZZY = 3
ID_MIN = 4

_HEX = frozenset("0123456789abcdef")


def is_hex(text: str) -> bool:
    return bool(text) and set(text) <= _HEX


def _word_start(hay: str, word: str, at: int) -> bool:
    while at >= 0:
        if at == 0 or not hay[at - 1].isalnum():
            return True
        at = hay.find(word, at + 1)
    return False


def _exact(words: list[str], hay: str) -> float | None:
    total = 0.0
    for word in words:
        at = hay.find(word)
        if at < 0:
            return None
        total += 100.0 if _word_start(hay, word, at) else 95.0
    return total / len(words)


def _fuzzy(words: list[str], hay: str) -> float | None:
    from rapidfuzz.fuzz import partial_ratio

    total = 0.0
    for word in words:
        if word in hay:
            score = 100.0 if _word_start(hay, word, hay.find(word)) else 95.0
        elif len(word) < MIN_FUZZY:
            return None
        else:
            score = min(partial_ratio(word, hay), 94.0)
        if score < THRESHOLD:
            return None
        total += score
    return total / len(words)


def _tiered(query: str, items: list[Book], text: Callable[[Book], str]) -> list[tuple[Book, float]]:
    words = norm(query).split()
    if not words:
        return [(b, 100.0) for b in items]
    for scorer in (_exact, _fuzzy):
        scored = [(b, s) for b in items if (s := scorer(words, text(b))) is not None]
        if scored:
            return scored
    return []


def sort_key(book: Book) -> tuple[str, str, str]:
    return (book.lib.casefold(), norm(book.authors[0] if book.authors else ""), norm(book.title))


def rank(query: str, books: Iterable[Book]) -> list[tuple[Book, float]]:
    scored = _tiered(query, list(books), lambda b: b.hay)
    scored.sort(key=lambda pair: (-pair[1], sort_key(pair[0])))
    return scored


def filter_author(query: str, books: list[Book]) -> list[Book]:
    return [b for b, _ in _tiered(query, books, lambda b: hay(b.authors))]


def by_id(ref: str, books: Iterable[Book]) -> list[Book]:
    ref = ref.strip().lower()
    if len(ref) < ID_MIN or not is_hex(ref):
        return []
    return [b for b in books if b.sha256.startswith(ref)]


def resolve(ref: str, books: list[Book]) -> Book:
    hits = by_id(ref, books)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        raise Ambiguous(f"id prefix {ref!r} matches {len(hits)} books", candidates=[(b, 100.0) for b in hits])
    if len(ref.strip()) < MIN_FUZZY:
        raise NotFound(f"no book with id {ref!r} (queries need at least {MIN_FUZZY} characters)")
    ranked = rank(ref, books)
    if not ranked:
        raise NotFound(f"no book matches {ref!r}")
    if len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= MARGIN:
        return ranked[0][0]
    raise Ambiguous(f"{ref!r} matches {len(ranked)} books", candidates=ranked)
