import re
import unicodedata
from collections.abc import Iterable

from rapidfuzz import fuzz

from bimport.errors import Ambiguous, NotFound
from bimport.library import Book

THRESHOLD = 80.0
MARGIN = 15.0
MIN_FUZZY = 3
ID_MIN = 4

HEX = re.compile(r"[0-9a-f]+")

_CYRILLIC = str.maketrans(
    dict(zip("абвгдезиклмнопрстуфыэіїє", "abvgdeziklmnoprstufyeiie", strict=True))
    | {"ж": "zh", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ю": "yu", "я": "ya", "ъ": "", "ь": ""}
)


def norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _hay(parts: Iterable[str]) -> str:
    text = norm(" ".join(parts))
    latin = text.translate(_CYRILLIC)
    return text if latin == text else f"{text} {latin}"


def haystack(book: Book) -> str:
    return _hay([book.title, *book.authors, *book.tags])


def matches_author(query: str, book: Book) -> bool:
    words = norm(query).split()
    return not words or _score(words, _hay(book.authors)) is not None


def _score(words: list[str], hay: str) -> float | None:
    total = 0.0
    for word in words:
        if word in hay:
            score = 100.0 if re.search(rf"(?<!\w){re.escape(word)}", hay) else 95.0
        elif len(word) < MIN_FUZZY:
            score = 0.0
        else:
            score = min(fuzz.partial_ratio(word, hay), 94.0)
        if score < THRESHOLD:
            return None
        total += score
    return total / len(words)


def rank(query: str, books: Iterable[Book]) -> list[tuple[Book, float]]:
    words = norm(query).split()
    if not words:
        return sorted(((b, 100.0) for b in books), key=lambda pair: sort_key(pair[0]))
    scored: list[tuple[Book, float]] = []
    for book in books:
        score = _score(words, haystack(book))
        if score is not None:
            scored.append((book, score))
    scored.sort(key=lambda pair: (-pair[1], sort_key(pair[0])))
    return scored


def sort_key(book: Book) -> tuple[str, str, str]:
    return (book.lib.casefold(), norm(book.authors[0] if book.authors else ""), norm(book.title))


def by_id(ref: str, books: Iterable[Book]) -> list[Book]:
    ref = ref.strip().lower()
    if len(ref) < ID_MIN or not HEX.fullmatch(ref):
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
