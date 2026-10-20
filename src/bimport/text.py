import unicodedata
from collections.abc import Iterable

_CYRILLIC = str.maketrans(
    dict(zip("абвгдезиклмнопрстуфыэіїє", "abvgdeziklmnoprstufyeiie", strict=True))
    | {"ж": "zh", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ю": "yu", "я": "ya", "ъ": "", "ь": ""}
)


def norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def hay(parts: Iterable[str]) -> str:
    text = norm(" ".join(parts))
    latin = text.translate(_CYRILLIC)
    return text if latin == text else f"{text} {latin}"
