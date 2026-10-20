import hashlib
import itertools
import os
import re
import shutil
import sys
import tomllib
import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import tomli_w

from bimport import extract
from bimport.config import Config
from bimport.errors import BimError, Duplicate, NotFound, UsageError

META = "meta.toml"
BOOK_EXTS = frozenset({".pdf", ".epub", ".fb2", ".djvu", ".mobi", ".azw3", ".cbz", ".xps"})
NAME_MAX_BYTES = 150

_UNSAFE = re.compile(r'[/\\:*?"<>|\x00-\x1f\x7f]')
_CALIBRE_SUFFIX = re.compile(r"\s\(\d+\)$")


@dataclass(slots=True)
class Book:
    sha256: str
    title: str
    authors: list[str]
    tags: list[str]
    file: str
    added: datetime
    lib: str
    dir: Path
    deleted: bool = False

    @property
    def id(self) -> str:
        return self.sha256[:8]

    @property
    def path(self) -> Path:
        return self.dir / self.file


def warn(message: str) -> None:
    print(f"bim: warning: {message}", file=sys.stderr)


def sanitize(name: str, fallback: str) -> str:
    name = unicodedata.normalize("NFC", name)
    name = " ".join(_UNSAFE.sub("_", name).split()).strip(" .")
    encoded = name.encode()
    if len(encoded) > NAME_MAX_BYTES:
        name = encoded[:NAME_MAX_BYTES].decode(errors="ignore").rstrip(" .")
    return name or fallback


def check_lib_name(lib: str) -> str:
    if sanitize(lib, "") != lib or lib.startswith("."):
        raise UsageError(f"invalid library name {lib!r}")
    return lib


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def _strs(value: Any, key: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"'{key}' must be a list of strings")
    return value


def read_meta(directory: Path, lib: str) -> Book:
    with (directory / META).open("rb") as fh:
        data = tomllib.load(fh)
    sha = data["sha256"]
    title = data["title"]
    file = data["file"]
    added = data["added"]
    deleted = data.get("deleted", False)
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
        raise ValueError("'sha256' must be 64 hex characters")
    if not isinstance(title, str) or not isinstance(file, str) or "/" in file or file in ("", META):
        raise ValueError("'title' and 'file' must be valid strings")
    if not isinstance(added, datetime) or not isinstance(deleted, bool):
        raise ValueError("'added' must be a datetime and 'deleted' a bool")
    return Book(
        sha256=sha,
        title=title,
        authors=_strs(data.get("authors", []), "authors"),
        tags=_strs(data.get("tags", []), "tags"),
        file=file,
        added=added,
        lib=lib,
        dir=directory,
        deleted=deleted,
    )


def write_meta(book: Book) -> None:
    data = {
        "id": book.id,
        "sha256": book.sha256,
        "title": book.title,
        "authors": book.authors,
        "tags": book.tags,
        "file": book.file,
        "added": book.added,
        "deleted": book.deleted,
    }
    tmp = book.dir / f".{META}.tmp"
    tmp.write_bytes(tomli_w.dumps(data).encode())
    os.replace(tmp, book.dir / META)


def libraries(cfg: Config) -> list[str]:
    if not cfg.root.is_dir():
        return []
    return sorted(
        (p.name for p in cfg.root.iterdir() if p.is_dir() and not p.name.startswith(".")),
        key=str.casefold,
    )


def _collect(tomb: Book) -> None:
    target = tomb.path
    if not target.is_file():
        return
    try:
        if sha256_file(target) == tomb.sha256:
            target.unlink()
    except OSError as exc:
        warn(f"cannot clean up {target}: {exc}")


def scan(cfg: Config, lib: str | None = None, *, gc: bool = True, quiet: bool = False) -> list[Book]:
    if lib is not None and not (cfg.root / check_lib_name(lib)).is_dir():
        raise NotFound(f"no library {lib!r} in {cfg.root}")
    books: list[Book] = []
    for name in [lib] if lib else libraries(cfg):
        for meta in (cfg.root / name).glob(f"*/*/{META}"):
            try:
                book = read_meta(meta.parent, name)
            except (OSError, tomllib.TOMLDecodeError, KeyError, ValueError) as exc:
                if not quiet:
                    warn(f"skipping {meta}: {exc}")
                continue
            if book.deleted:
                if gc:
                    _collect(book)
                continue
            books.append(book)
    return books


def _tombstone_at(directory: Path) -> Book | None:
    try:
        book = read_meta(directory, "")
    except (OSError, tomllib.TOMLDecodeError, KeyError, ValueError):
        return None
    return book if book.deleted else None


def _child(parent: Path, name: str) -> Path:
    if parent.is_dir():
        folded = name.casefold()
        for entry in parent.iterdir():
            if entry.name.casefold() == folded:
                return entry
    return parent / name


def place(lib_dir: Path, title: str, authors: list[str], *, force: bool, current: Path | None = None) -> Path:
    author_dir = _child(lib_dir, sanitize(authors[0] if authors else "", "Unknown"))
    base = sanitize(title, "Untitled")
    for n in itertools.count(1):
        candidate = _child(author_dir, base if n == 1 else f"{base} ({n})")
        if candidate == current or not candidate.exists() or _tombstone_at(candidate) is not None:
            return candidate
        if not force:
            raise Duplicate(f"{candidate} already exists (use --force to add as a separate book)")
    raise AssertionError("unreachable")


def _prepare(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    if (tomb := _tombstone_at(directory)) is not None:
        _collect(tomb)


def _now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _check_format(path: Path) -> None:
    if path.suffix.lower() not in BOOK_EXTS:
        raise UsageError(f"{path}: unsupported format (expected one of {', '.join(sorted(BOOK_EXTS))})")


def _clean_list(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        value = " ".join(value.split())
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            out.append(value)
    return out


def add(
    cfg: Config,
    source: Path,
    lib: str,
    *,
    title: str | None,
    authors: list[str],
    tags: list[str],
    force: bool,
    known: list[Book],
) -> Book:
    check_lib_name(lib)
    if not source.is_file():
        raise NotFound(f"{source}: no such file")
    _check_format(source)
    digest = sha256_file(source)
    for book in known:
        if book.sha256 == digest:
            raise Duplicate(f"{source}: already in library as {book.id} ({book.lib}: {book.title})", id=book.id)

    meta_title, meta_authors = extract.embedded(source)
    name_title, name_authors = extract.from_filename(source)
    final_title = " ".join((title or meta_title or name_title).split())
    final_authors = _clean_list(authors) or meta_authors or name_authors
    if not final_title:
        raise UsageError("title must not be empty")

    directory = place(cfg.root / lib, final_title, final_authors, force=force)
    _prepare(directory)
    book = Book(
        sha256=digest,
        title=final_title,
        authors=final_authors,
        tags=_clean_list(tags),
        file=sanitize(final_title, "Untitled") + source.suffix.lower(),
        added=_now(),
        lib=lib,
        dir=directory,
    )
    shutil.copy2(source, book.path)
    write_meta(book)
    return book


def _link_or_copy(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def edit(
    cfg: Config,
    book: Book,
    *,
    title: str | None,
    authors: list[str],
    add_tags: list[str],
    remove_tags: list[str],
) -> Book:
    new_title = book.title if title is None else " ".join(title.split())
    if not new_title:
        raise UsageError("title must not be empty")
    new_authors = _clean_list(authors) or book.authors
    dropped = {t.casefold() for t in remove_tags}
    new_tags = _clean_list([t for t in book.tags if t.casefold() not in dropped] + add_tags)
    updated = replace(book, title=new_title, authors=new_authors, tags=new_tags)

    def first(names: list[str]) -> str:
        return sanitize(names[0] if names else "", "Unknown")

    same_title = sanitize(new_title, "Untitled") == sanitize(book.title, "Untitled")
    if same_title and first(new_authors) == first(book.authors):
        write_meta(updated)
        return updated

    directory = place(cfg.root / book.lib, new_title, new_authors, force=True, current=book.dir)
    if directory == book.dir:
        write_meta(updated)
        return updated

    _prepare(directory)
    updated = replace(updated, dir=directory, file=sanitize(new_title, "Untitled") + Path(book.file).suffix)
    if book.path.is_file():
        _link_or_copy(book.path, updated.path)
    write_meta(updated)
    write_meta(replace(book, deleted=True))
    book.path.unlink(missing_ok=True)
    return updated


def remove(book: Book) -> Book:
    book.path.unlink(missing_ok=True)
    tomb = replace(book, deleted=True)
    write_meta(tomb)
    return tomb


def adopt(cfg: Config, lib: str, *, dry_run: bool) -> tuple[list[Book], list[tuple[Path, str]]]:
    lib_dir = cfg.root / check_lib_name(lib)
    if not lib_dir.is_dir():
        raise NotFound(f"no library {lib!r} in {cfg.root}")
    known = {b.sha256: b for b in scan(cfg, lib, gc=False)}
    adopted: list[Book] = []
    skipped: list[tuple[Path, str]] = []
    for directory in sorted(p for p in lib_dir.glob("*/*") if p.is_dir()):
        if (directory / META).exists():
            continue
        files = [f for f in directory.iterdir() if f.is_file() and f.suffix.lower() in BOOK_EXTS]
        if len(files) != 1:
            skipped.append((directory, "no book file" if not files else f"{len(files)} book files"))
            continue
        source = files[0]
        try:
            digest = sha256_file(source)
        except OSError as exc:
            skipped.append((directory, str(exc)))
            continue
        if (other := known.get(digest)) is not None:
            skipped.append((directory, f"duplicate of {other.id} ({other.dir})"))
            continue
        meta_title, meta_authors = extract.embedded(source)
        folder_author = extract.clean(directory.parent.name)
        book = Book(
            sha256=digest,
            title=meta_title or _CALIBRE_SUFFIX.sub("", directory.name),
            authors=meta_authors or ([folder_author] if folder_author else []),
            tags=[],
            file=source.name,
            added=datetime.fromtimestamp(source.stat().st_mtime, UTC).replace(microsecond=0),
            lib=lib,
            dir=directory,
        )
        known[digest] = book
        if not dry_run:
            try:
                write_meta(book)
            except OSError as exc:
                raise BimError(f"cannot write {directory / META}: {exc}") from exc
        adopted.append(book)
    return adopted, skipped
