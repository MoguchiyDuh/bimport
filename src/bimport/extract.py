import re
import xml.etree.ElementTree as ET
from pathlib import Path

_JUNK = re.compile(
    r"^(untitled|unknown|anonymous|none|null|\d+|microsoft word\b.*)$|\.(docx?|html?|pdf|tex|dvi|indd|qxd|rtf|txt)$",
    re.IGNORECASE,
)
_HARD_SEP = re.compile(r"\s*(?:;|&|/|\band\b)\s*")
_GLUED = re.compile(r"(?<=[а-яё])(?=[А-ЯЁ])")
_OPF_ROLE = "{http://www.idpf.org/2007/opf}role"


def _cyrillic(word: str) -> bool:
    return any("а" <= c <= "я" or c == "ё" for c in word.lower())


def normalize_name(name: str) -> str:
    words = []
    for word in _GLUED.sub(" ", name).split():
        if len(word) >= 3 and word.isupper() and _cyrillic(word):
            word = "-".join(part.capitalize() for part in word.split("-"))
        words.append(word)
    return " ".join(words)


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
        cleaned = normalize_name(cleaned) if cleaned else None
        if cleaned and cleaned.casefold() not in seen:
            seen.add(cleaned.casefold())
            authors.append(cleaned)
    return authors


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _text(el: ET.Element) -> str:
    return " ".join("".join(el.itertext()).split())


def _fb2(path: Path) -> tuple[str | None, list[str]]:
    title: str | None = None
    authors: list[str] = []
    depth: list[str] = []
    try:
        for event, el in ET.iterparse(path, events=("start", "end")):
            tag = _local(el.tag)
            if event == "start":
                depth.append(tag)
                continue
            depth.pop()
            if tag == "title-info":
                break
            if depth[-1:] != ["title-info"]:
                continue
            if tag == "book-title":
                title = clean(_text(el))
            elif tag == "author":
                parts = {_local(c.tag): _text(c) for c in el}
                name = " ".join(p for p in (parts.get("first-name"), parts.get("last-name")) if p)
                if cleaned := clean(name or parts.get("nickname")):
                    authors.append(normalize_name(cleaned))
    except (ET.ParseError, OSError, LookupError):
        return None, []
    return title, list(dict.fromkeys(authors))


def _epub(path: Path) -> tuple[str | None, list[str]]:
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            opf_path = next((el.get("full-path") for el in container.iter() if _local(el.tag) == "rootfile"), None)
            if not opf_path:
                return None, []
            opf = ET.fromstring(archive.read(opf_path))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError, ValueError):
        return None, []

    metadata = next((el for el in opf if _local(el.tag) == "metadata"), None)
    if metadata is None:
        return None, []
    roles = {
        (el.get("refines") or "").lstrip("#"): (el.text or "").strip()
        for el in metadata
        if _local(el.tag) == "meta" and el.get("property") == "role"
    }
    title: str | None = None
    authors: list[str] = []
    for el in metadata:
        tag = _local(el.tag)
        if tag == "title" and title is None:
            title = clean(_text(el))
        elif tag == "creator":
            role = el.get(_OPF_ROLE) or roles.get(el.get("id") or "")
            if role in (None, "", "aut"):
                authors.extend(a for a in split_authors(_text(el)) if a not in authors)
    return title, authors


def _mupdf(path: Path) -> tuple[str | None, list[str]]:
    try:
        import pymupdf

        pymupdf.TOOLS.mupdf_display_errors(False)
        with pymupdf.open(path) as doc:
            meta = doc.metadata or {}
    except Exception:
        return None, []
    return clean(meta.get("title")), split_authors(meta.get("author"))


def embedded(path: Path) -> tuple[str | None, list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".fb2":
        return _fb2(path)
    if suffix == ".epub":
        title, authors = _epub(path)
        if title or authors:
            return title, authors
    return _mupdf(path)


def from_filename(path: Path) -> tuple[str, list[str]]:
    stem = " ".join(path.stem.replace("_", " ").split())
    if " - " in stem:
        title, author = stem.rsplit(" - ", 1)
        return title.strip() or stem, split_authors(author)
    return stem, []
