import functools
import json
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer

from bimport import library
from bimport.complete import ZSH
from bimport.config import Config, load_config
from bimport.errors import Ambiguous, BimError, NotFound, UsageError
from bimport.library import Book, scan
from bimport.match import HEX, by_id, matches_author, rank, resolve
from bimport.sync import sync as run_sync

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Personal ebook library: meta.toml per book, fuzzy lookup, two-way rsync.",
)

Json = Annotated[bool, typer.Option("--json", help="Machine-readable output; errors as JSON on stderr.")]
Ref = Annotated[list[str], typer.Argument(metavar="ID|QUERY", help="Id prefix or fuzzy title/author/tag query.")]
Lib = Annotated[str | None, typer.Option("--lib", help="Library name.")]
Tags = Annotated[list[str] | None, typer.Option("--tag", help="Tag (repeatable).")]
Authors = Annotated[list[str] | None, typer.Option("--author", "-a", help="Author (repeatable).")]
Title = Annotated[str | None, typer.Option("--title", "-t", help="Title.")]


def book_json(book: Book, score: float | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": book.id,
        "lib": book.lib,
        "title": book.title,
        "authors": book.authors,
        "tags": book.tags,
        "path": str(book.path),
        "sha256": book.sha256,
        "added": book.added.isoformat(),
    }
    if score is not None:
        data["score"] = round(score, 1)
    return data


def line(book: Book) -> str:
    text = f"{book.id}  {book.title} — {', '.join(book.authors) or 'Unknown'}"
    if book.tags:
        text += f"  [{', '.join(book.tags)}]"
    return f"{text}  ({book.lib})"


def emit(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def interactive(as_json: bool) -> bool:
    return not as_json and sys.stdin.isatty() and sys.stderr.isatty()


def fail(exc: BimError, as_json: bool) -> None:
    candidates: list[tuple[Book, float]] = exc.extra.get("candidates", [])
    if as_json:
        payload: dict[str, Any] = {"error": exc.message, "code": exc.code}
        for key, value in exc.extra.items():
            payload[key] = [book_json(b, s) for b, s in value] if key == "candidates" else value
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    else:
        print(f"bim: {exc.message}", file=sys.stderr)
        for book, _ in candidates[:10]:
            print(f"  {line(book)}", file=sys.stderr)
    raise typer.Exit(exc.code)


def handled[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        as_json = bool(kwargs.get("as_json", False))
        try:
            return fn(*args, **kwargs)
        except BimError as exc:
            fail(exc, as_json)
        except OSError as exc:
            fail(BimError(str(exc)), as_json)
        except KeyboardInterrupt:
            print(file=sys.stderr)
            raise typer.Exit(130) from None
        raise AssertionError("unreachable")

    return wrapper


def ask(prompt: str) -> str:
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().strip()


def pick(candidates: list[tuple[Book, float]]) -> Book:
    shown = candidates[:10]
    print("Which book did you mean?", file=sys.stderr)
    for i, (book, _) in enumerate(shown, 1):
        print(f"  {i:>2}) {line(book)}", file=sys.stderr)
    while True:
        answer = ask(f"[1-{len(shown)}, empty to cancel]: ")
        if not answer:
            raise BimError("cancelled")
        if answer.isdigit() and 1 <= int(answer) <= len(shown):
            return shown[int(answer) - 1][0]


def find(cfg: Config, ref: list[str], as_json: bool) -> Book:
    books = scan(cfg)
    try:
        return resolve(" ".join(ref), books)
    except Ambiguous as exc:
        if interactive(as_json):
            return pick(exc.extra["candidates"])
        raise


@app.command()
@handled
def add(
    files: Annotated[list[Path], typer.Argument(metavar="FILE...", help="Book files to copy in.")],
    title: Title = None,
    author: Authors = None,
    tag: Tags = None,
    lib: Lib = None,
    force: Annotated[bool, typer.Option("--force", help="Allow a second book with the same title and author.")] = False,
    as_json: Json = False,
) -> None:
    """Copy books into a library; metadata from the file, then filename, flags override."""
    cfg = load_config()
    target = lib or cfg.default_lib
    if target is None:
        raise UsageError(f"no --lib given and no default_lib in {cfg.path}")
    if (title or author) and len(files) > 1:
        raise UsageError("--title/--author apply to a single file")
    known = scan(cfg)
    added: list[Book] = []
    errors: list[tuple[Path, BimError]] = []
    for path in files:
        try:
            book = library.add(
                cfg, path, target, title=title, authors=author or [], tags=tag or [], force=force, known=known
            )
        except BimError as exc:
            errors.append((path, exc))
            continue
        except OSError as exc:
            errors.append((path, BimError(f"{path}: {exc}")))
            continue
        known.append(book)
        added.append(book)

    if as_json:
        emit(
            {
                "added": [book_json(b) for b in added],
                "errors": [{"file": str(p), "error": e.message, "code": e.code, **e.extra} for p, e in errors],
            }
        )
    else:
        for book in added:
            print(f"added {line(book)}")
        for _, exc in errors:
            print(f"bim: {exc.message}", file=sys.stderr)
    if errors:
        raise typer.Exit(errors[0][1].code)


@app.command("ls")
@handled
def list_books(
    query: Annotated[list[str] | None, typer.Argument(metavar="[QUERY]", help="Fuzzy title/author/tag query.")] = None,
    author: Authors = None,
    tag: Tags = None,
    lib: Lib = None,
    as_json: Json = False,
) -> None:
    """List books, ranked by match score when a query is given."""
    cfg = load_config()
    books = scan(cfg, lib)
    if tag:
        wanted = {t.casefold() for t in tag}
        books = [b for b in books if wanted <= {t.casefold() for t in b.tags}]
    for name in author or []:
        books = [b for b in books if matches_author(name, b)]
    text = " ".join(query or [])
    ranked = rank(text, books)
    if as_json:
        emit([book_json(b, s if text else None) for b, s in ranked])
        return
    for book, _ in ranked:
        print(line(book))


@app.command()
@handled
def show(ref: Ref, as_json: Json = False) -> None:
    """Show one book."""
    book = find(load_config(), ref, as_json)
    if as_json:
        emit(book_json(book))
        return
    rows = [
        ("id", book.id),
        ("lib", book.lib),
        ("title", book.title),
        ("authors", "; ".join(book.authors) or "Unknown"),
        ("tags", ", ".join(book.tags)),
        ("added", book.added.isoformat()),
        ("path", str(book.path)),
    ]
    for key, value in rows:
        print(f"{key:<8} {value}")


@app.command("open")
@handled
def open_book(ref: Ref, as_json: Json = False) -> None:
    """Open a book with the configured reader."""
    cfg = load_config()
    book = find(cfg, ref, as_json)
    if not book.path.is_file():
        raise NotFound(f"{book.path}: file missing")
    cmd = shlex.split(cfg.opener)
    if not cmd or shutil.which(cmd[0]) is None:
        raise BimError(f"opener not found: {cfg.opener!r}")
    subprocess.Popen(
        [*cmd, str(book.path)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    if as_json:
        emit(book_json(book))
    else:
        print(f"opened {line(book)}")


@app.command()
@handled
def edit(
    ref: Ref,
    title: Title = None,
    author: Annotated[list[str] | None, typer.Option("--author", "-a", help="Replace authors (repeatable).")] = None,
    tag: Annotated[list[str] | None, typer.Option("--tag", help="Add tag (repeatable).")] = None,
    untag: Annotated[list[str] | None, typer.Option("--untag", help="Remove tag (repeatable).")] = None,
    as_json: Json = False,
) -> None:
    """Change title, authors or tags; the folder follows title and first author."""
    if title is None and not author and not tag and not untag:
        raise UsageError("nothing to change: pass --title, --author, --tag or --untag")
    cfg = load_config()
    book = find(cfg, ref, as_json)
    updated = library.edit(cfg, book, title=title, authors=author or [], add_tags=tag or [], remove_tags=untag or [])
    if as_json:
        emit(book_json(updated))
    else:
        print(f"updated {line(updated)}")


@app.command()
@handled
def rm(ref: Ref, as_json: Json = False) -> None:
    """Delete a book. Interactive: any query, with confirmation. Otherwise: exact id only."""
    cfg = load_config()
    text = " ".join(ref).strip()
    if interactive(as_json):
        book = find(cfg, ref, as_json)
        if ask(f"Delete {line(book)}? [y/N] ").lower() not in ("y", "yes"):
            raise BimError("cancelled")
    else:
        if len(text) < 8 or not HEX.fullmatch(text.lower()):
            raise UsageError("non-interactive rm needs an exact id (8+ hex chars); get it from `bim ls --json`")
        hits = by_id(text, scan(cfg))
        if not hits:
            raise NotFound(f"no book with id {text!r}")
        if len(hits) > 1:
            raise Ambiguous(f"id {text!r} matches {len(hits)} books", candidates=[(b, 100.0) for b in hits])
        book = hits[0]
    library.remove(book)
    if as_json:
        emit({"deleted": book_json(book)})
    else:
        print(f"deleted {line(book)}")


@app.command()
@handled
def libs(as_json: Json = False) -> None:
    """List libraries."""
    cfg = load_config()
    counts: dict[str, int] = {name: 0 for name in library.libraries(cfg)}
    for book in scan(cfg):
        counts[book.lib] = counts.get(book.lib, 0) + 1
    if as_json:
        emit(
            [
                {"name": n, "books": c, "path": str(cfg.root / n), "default": n == cfg.default_lib}
                for n, c in counts.items()
            ]
        )
        return
    width = max((len(n) for n in counts), default=0)
    for name, count in counts.items():
        mark = " *" if name == cfg.default_lib else ""
        print(f"{name:<{width}}  {count:>5}{mark}")


@app.command()
@handled
def adopt(
    lib: Annotated[str, typer.Argument(help="Library whose Author/Title/ folders lack meta.toml.")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would be written.")] = False,
    as_json: Json = False,
) -> None:
    """Write meta.toml into existing book folders in place; never moves or renames."""
    adopted, skipped = library.adopt(load_config(), lib, dry_run=dry_run)
    if as_json:
        emit(
            {
                "dry_run": dry_run,
                "adopted": [book_json(b) for b in adopted],
                "skipped": [{"dir": str(d), "reason": r} for d, r in skipped],
            }
        )
        return
    for book in adopted:
        print(f"{'would adopt' if dry_run else 'adopted'} {line(book)}")
    for directory, reason in skipped:
        print(f"skipped {directory}: {reason}", file=sys.stderr)
    print(f"{len(adopted)} {'to adopt' if dry_run else 'adopted'}, {len(skipped)} skipped", file=sys.stderr)


@app.command()
@handled
def sync(
    device: Annotated[str, typer.Argument(help="Device name from config.")],
    lib: Lib = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show what would change.")] = False,
    as_json: Json = False,
) -> None:
    """Two-way rsync (pull, clean up, push); newer file wins, nothing is deleted remotely."""
    run_sync(load_config(), device, lib, dry_run=dry_run, out=sys.stderr if as_json else None)
    if as_json:
        emit({"device": device, "lib": lib, "dry_run": dry_run, "ok": True})


@app.command()
@handled
def completion(shell: Annotated[str, typer.Argument(help="Shell name.")] = "zsh") -> None:
    """Print the shell completion script (save as _bim in your fpath)."""
    if shell != "zsh":
        raise UsageError(f"unsupported shell {shell!r} (only zsh)")
    sys.stdout.write(ZSH)
