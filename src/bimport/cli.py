import argparse
import sys
from collections.abc import Callable
from pathlib import Path

from bimport import library
from bimport.config import Config, load_config
from bimport.errors import Ambiguous, BimError, NotFound, UsageError
from bimport.library import Book, scan
from bimport.match import by_id, filter_author, is_hex, rank, resolve


def book_json(book: Book, score: float | None = None) -> dict[str, object]:
    data: dict[str, object] = {
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


def dumps(data: object, *, indent: int | None = 2) -> str:
    import json

    return json.dumps(data, ensure_ascii=False, indent=indent)


def emit(data: object) -> None:
    print(dumps(data))


def interactive(as_json: bool) -> bool:
    return not as_json and sys.stdin.isatty() and sys.stderr.isatty()


def fail(exc: BimError, as_json: bool) -> int:
    candidates: list[tuple[Book, float]] = exc.extra.get("candidates", [])
    if as_json:
        payload: dict[str, object] = {"error": exc.message, "code": exc.code}
        for key, value in exc.extra.items():
            payload[key] = [book_json(b, s) for b, s in value] if key == "candidates" else value
        print(dumps(payload, indent=None), file=sys.stderr)
    else:
        print(f"bim: {exc.message}", file=sys.stderr)
        for book, _ in candidates[:10]:
            print(f"  {line(book)}", file=sys.stderr)
    return exc.code


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


def cmd_add(args: argparse.Namespace) -> int:
    cfg = load_config()
    target = args.lib or cfg.default_lib
    if target is None:
        raise UsageError(f"no --lib given and no default_lib in {cfg.path}")
    if (args.title or args.author) and len(args.files) > 1:
        raise UsageError("--title/--author apply to a single file")
    known = scan(cfg)
    added: list[Book] = []
    errors: list[tuple[Path, BimError]] = []
    for path in args.files:
        try:
            book = library.add(
                cfg, path, target, title=args.title, authors=args.author, tags=args.tag, force=args.force, known=known
            )
        except BimError as exc:
            errors.append((path, exc))
            continue
        except OSError as exc:
            errors.append((path, BimError(f"{path}: {exc}")))
            continue
        known.append(book)
        added.append(book)

    if args.as_json:
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
    return errors[0][1].code if errors else 0


def cmd_ls(args: argparse.Namespace) -> int:
    books = scan(load_config(), args.lib)
    if args.tag:
        wanted = {t.casefold() for t in args.tag}
        books = [b for b in books if wanted <= {t.casefold() for t in b.tags}]
    for name in args.author:
        books = filter_author(name, books)
    text = " ".join(args.query)
    ranked = rank(text, books)
    if args.as_json:
        emit([book_json(b, s if text else None) for b, s in ranked])
    else:
        sys.stdout.write("".join(f"{line(b)}\n" for b, _ in ranked))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    book = find(load_config(), args.ref, args.as_json)
    if args.as_json:
        emit(book_json(book))
        return 0
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
    return 0


def cmd_open(args: argparse.Namespace) -> int:
    import shlex
    import shutil
    import subprocess

    cfg = load_config()
    book = find(cfg, args.ref, args.as_json)
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
    if args.as_json:
        emit(book_json(book))
    else:
        print(f"opened {line(book)}")
    return 0


def cmd_edit(args: argparse.Namespace) -> int:
    if args.title is None and not args.author and not args.tag and not args.untag:
        raise UsageError("nothing to change: pass --title, --author, --tag or --untag")
    cfg = load_config()
    book = find(cfg, args.ref, args.as_json)
    updated = library.edit(cfg, book, title=args.title, authors=args.author, add_tags=args.tag, remove_tags=args.untag)
    if args.as_json:
        emit(book_json(updated))
    else:
        print(f"updated {line(updated)}")
    return 0


def cmd_rm(args: argparse.Namespace) -> int:
    cfg = load_config()
    text = " ".join(args.ref).strip()
    if interactive(args.as_json):
        book = find(cfg, args.ref, args.as_json)
        if ask(f"Delete {line(book)}? [y/N] ").lower() not in ("y", "yes"):
            raise BimError("cancelled")
    else:
        if len(text) < 8 or not is_hex(text.lower()):
            raise UsageError("non-interactive rm needs an exact id (8+ hex chars); get it from `bim ls --json`")
        hits = by_id(text, scan(cfg))
        if not hits:
            raise NotFound(f"no book with id {text!r}")
        if len(hits) > 1:
            raise Ambiguous(f"id {text!r} matches {len(hits)} books", candidates=[(b, 100.0) for b in hits])
        book = hits[0]
    library.remove(book)
    if args.as_json:
        emit({"deleted": book_json(book)})
    else:
        print(f"deleted {line(book)}")
    return 0


def cmd_libs(args: argparse.Namespace) -> int:
    cfg = load_config()
    counts: dict[str, int] = dict.fromkeys(library.libraries(cfg), 0)
    for book in scan(cfg):
        counts[book.lib] = counts.get(book.lib, 0) + 1
    if args.as_json:
        emit(
            [
                {"name": n, "books": c, "path": str(cfg.root / n), "default": n == cfg.default_lib}
                for n, c in counts.items()
            ]
        )
        return 0
    width = max((len(n) for n in counts), default=0)
    for name, count in counts.items():
        mark = " *" if name == cfg.default_lib else ""
        print(f"{name:<{width}}  {count:>5}{mark}")
    return 0


def cmd_adopt(args: argparse.Namespace) -> int:
    adopted, skipped = library.adopt(load_config(), args.lib, dry_run=args.dry_run)
    if args.as_json:
        emit(
            {
                "dry_run": args.dry_run,
                "adopted": [book_json(b) for b in adopted],
                "skipped": [{"dir": str(d), "reason": r} for d, r in skipped],
            }
        )
        return 0
    verb = "would adopt" if args.dry_run else "adopted"
    for book in adopted:
        print(f"{verb} {line(book)}")
    for directory, reason in skipped:
        print(f"skipped {directory}: {reason}", file=sys.stderr)
    print(f"{len(adopted)} {'to adopt' if args.dry_run else 'adopted'}, {len(skipped)} skipped", file=sys.stderr)
    return 0


def cmd_tidy(args: argparse.Namespace) -> int:
    cfg = load_config()
    moves = library.tidy(cfg, args.lib, dry_run=args.dry_run)
    if args.as_json:
        emit({"dry_run": args.dry_run, "moved": [{**book_json(b), "to": str(p)} for b, p in moves]})
        return 0
    verb = "would move" if args.dry_run else "moved"
    for book, target in moves:
        print(f"{verb} {book.id}  {book.path.relative_to(cfg.root)}  ->  {target.relative_to(cfg.root)}")
    print(f"{len(moves)} {'to move' if args.dry_run else 'moved'}", file=sys.stderr)
    return 0


def cmd_prune(args: argparse.Namespace) -> int:
    if args.days < 0:
        raise UsageError("--days must be >= 0")
    cfg = load_config()
    removed = library.prune(cfg, days=args.days, dry_run=args.dry_run)
    if args.as_json:
        emit({"dry_run": args.dry_run, "removed": [str(d) for d in removed]})
        return 0
    verb = "would remove" if args.dry_run else "removed"
    for directory in removed:
        print(f"{verb} {directory.relative_to(cfg.root)}")
    print(f"{len(removed)} tombstone{'s' * (len(removed) != 1)} {verb.split()[-1]}", file=sys.stderr)
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from bimport.sync import sync

    sync(load_config(), args.device, args.lib, dry_run=args.dry_run, out=sys.stderr if args.as_json else None)
    if args.as_json:
        emit({"device": args.device, "lib": args.lib, "dry_run": args.dry_run, "ok": True})
    return 0


def cmd_completion(args: argparse.Namespace) -> int:
    from bimport.complete import ZSH

    if args.shell != "zsh":
        raise UsageError(f"unsupported shell {args.shell!r} (only zsh)")
    sys.stdout.write(ZSH)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bim", description="Personal ebook library: meta.toml per book, fuzzy lookup, two-way rsync."
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="COMMAND")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json", dest="as_json", action="store_true", help="machine-readable output; errors as JSON on stderr"
    )

    def command(name: str, handler: Callable[[argparse.Namespace], int], text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, parents=[common], help=text, description=text)
        p.set_defaults(handler=handler)
        return p

    def ref(p: argparse.ArgumentParser) -> None:
        p.add_argument("ref", nargs="+", metavar="ID|QUERY", help="id prefix or fuzzy title/author/tag query")

    p = command("add", cmd_add, "copy books into a library; metadata from the file, then filename, flags override")
    p.add_argument("files", nargs="+", type=Path, metavar="FILE")
    p.add_argument("-t", "--title")
    p.add_argument("-a", "--author", action="append", default=[], help="repeatable")
    p.add_argument("--tag", action="append", default=[], help="repeatable")
    p.add_argument("--lib")
    p.add_argument("--force", action="store_true", help="allow a second book with the same title and author")

    p = command("ls", cmd_ls, "list books, ranked by match score when a query is given")
    p.add_argument("query", nargs="*", metavar="QUERY")
    p.add_argument("-a", "--author", action="append", default=[], help="author filter (repeatable)")
    p.add_argument("--tag", action="append", default=[], help="tag filter (repeatable, all must match)")
    p.add_argument("--lib")

    ref(command("show", cmd_show, "show one book"))
    ref(command("open", cmd_open, "open a book with the configured reader"))

    p = command("edit", cmd_edit, "change title, authors or tags; the folder follows title and first author")
    ref(p)
    p.add_argument("-t", "--title")
    p.add_argument("-a", "--author", action="append", default=[], help="replace authors (repeatable)")
    p.add_argument("--tag", action="append", default=[], help="add tag (repeatable)")
    p.add_argument("--untag", action="append", default=[], help="remove tag (repeatable)")

    ref(command("rm", cmd_rm, "delete a book; interactive: any query with confirmation, otherwise exact id only"))
    command("libs", cmd_libs, "list libraries")

    p = command("adopt", cmd_adopt, "write meta.toml into existing book folders in place; never moves or renames")
    p.add_argument("lib")
    p.add_argument("--dry-run", action="store_true")

    p = command("tidy", cmd_tidy, "move books into <lib>/<first author>/<title>/<title>.<ext> to match metadata")
    p.add_argument("--lib")
    p.add_argument("--dry-run", action="store_true")

    p = command(
        "prune", cmd_prune, "remove tombstone folders older than --days; run only after every device has synced"
    )
    p.add_argument("--days", type=float, default=30.0, help="minimum tombstone age (default 30)")
    p.add_argument("--dry-run", action="store_true")

    p = command("sync", cmd_sync, "two-way rsync (pull, clean up, push); newer file wins, nothing deleted remotely")
    p.add_argument("device")
    p.add_argument("--lib")
    p.add_argument("--dry-run", action="store_true")

    p = command("completion", cmd_completion, "print the shell completion script (save as _bim in your fpath)")
    p.add_argument("shell", nargs="?", default="zsh")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_json = bool(getattr(args, "as_json", False))
    try:
        return args.handler(args)
    except BimError as exc:
        return fail(exc, as_json)
    except OSError as exc:
        return fail(BimError(str(exc)), as_json)
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
