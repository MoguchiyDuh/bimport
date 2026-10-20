import re
from pathlib import Path

_JUNK = re.compile(
    r"^(untitled|unknown|anonymous|none|null|\d+|microsoft word\b.*)$|\.(docx?|html?|pdf|tex|dvi|indd|qxd|rtf|txt)$",
    re.IGNORECASE,
)
_HARD_SEP = re.compile(r"\s*(?:;|&|/|\band\b)\s*")


def clean(value: str | None) -> str | None:
    if not value:
        return None
    value = " ".join(value.split())
    if not value or _JUNK.search(value):
        return None
    return value


def split_authors(raw: str | None) -> list[str]:
    raw = clean(raw)
    if raw is None:
        return []
    parts: list[str] = []
    for chunk in _HARD_SEP.split(raw):
        pieces = [p.strip() for p in chunk.split(",")]
        if len(pieces) > 1 and all(len(p.split()) >= 2 for p in pieces):
            parts.extend(pieces)
        else:
            parts.append(chunk.strip())
    seen: set[str] = set()
    authors: list[str] = []
    for part in parts:
        cleaned = clean(part)
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            authors.append(cleaned)
    return authors


def embedded(path: Path) -> tuple[str | None, list[str]]:
    try:
        import pymupdf

        pymupdf.TOOLS.mupdf_display_errors(False)
        with pymupdf.open(path) as doc:
            meta = doc.metadata or {}
    except Exception:
        return None, []
    return clean(meta.get("title")), split_authors(meta.get("author"))


def from_filename(path: Path) -> tuple[str, list[str]]:
    stem = " ".join(path.stem.replace("_", " ").split())
    if " - " in stem:
        title, author = stem.rsplit(" - ", 1)
        return title.strip() or stem, split_authors(author)
    return stem, []
