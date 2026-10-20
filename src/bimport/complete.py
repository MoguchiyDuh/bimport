import sys

from bimport.config import load_config
from bimport.errors import BimError

LIMIT = 50

ZSH = r"""#compdef bim

_bim_books() {
  local -a vals descs
  local line
  for line in "${(@f)$(bim __complete book "${(Q)PREFIX}${(Q)SUFFIX}" 2>/dev/null)}"; do
    [[ -n $line ]] || continue
    vals+=("${line%%$'\t'*}")
    descs+=("${line%%$'\t'*}  ${line#*$'\t'}")
  done
  (( $#vals )) || return 1
  compstate[insert]=menu
  compadd -U -V bim-books -l -d descs -a vals
}

_bim_simple() {
  local -a vals
  vals=("${(@f)$(bim __complete $1 "" 2>/dev/null)}")
  vals=(${vals:#})
  (( $#vals )) || return 1
  compadd -a vals
}

_bim_libs() { _bim_simple lib }
_bim_tags() { _bim_simple tag }
_bim_devices() { _bim_simple device }

_bim() {
  local curcontext=$curcontext state line
  local -a cmds=(
    'add:copy books into a library'
    'ls:list or search books'
    'show:show one book'
    'open:open a book in the reader'
    'edit:change title, authors or tags'
    'rm:delete a book'
    'libs:list libraries'
    'adopt:write meta.toml into existing book folders'
    'tidy:move folders to match metadata'
    'prune:remove old tombstone folders'
    'sync:two-way rsync with a device'
    'completion:print shell completion script'
  )
  local json='--json[machine-readable output]'

  _arguments -C '(- *)--help[show help]' '1:command:->cmd' '*::arg:->args' && return
  case $state in
    cmd) _describe -t commands 'bim command' cmds ;;
    args)
      curcontext="${curcontext%:*}-$words[1]:"
      case $words[1] in
        add)
          _arguments $json \
            '(-t --title)'{-t,--title}'[title]:title: ' \
            '*'{-a,--author}'[author (repeatable)]:author: ' \
            '*--tag[tag (repeatable)]:tag:_bim_tags' \
            '--lib[library]:library:_bim_libs' \
            '--force[allow same title and author]' \
            '*:book file:_files -g "*.(#i)(pdf|epub|fb2|djvu|mobi|azw3|cbz|xps)(-.)"' ;;
        ls)
          _arguments $json \
            '*'{-a,--author}'[author filter]:author: ' \
            '*--tag[tag filter]:tag:_bim_tags' \
            '--lib[library]:library:_bim_libs' \
            '1::query: ' ;;
        show|open|rm)
          _arguments $json '1:book:_bim_books' ;;
        edit)
          _arguments $json \
            '(-t --title)'{-t,--title}'[new title]:title: ' \
            '*'{-a,--author}'[replace authors (repeatable)]:author: ' \
            '*--tag[add tag]:tag:_bim_tags' \
            '*--untag[remove tag]:tag:_bim_tags' \
            '1:book:_bim_books' ;;
        libs)
          _arguments $json ;;
        adopt)
          _arguments $json '--dry-run[show what would be written]' '1:library:_bim_libs' ;;
        tidy)
          _arguments $json '--dry-run[show what would move]' '--lib[library]:library:_bim_libs' ;;
        prune)
          _arguments $json '--dry-run[show what would be removed]' '--days[minimum age]:days: ' ;;
        sync)
          _arguments $json \
            '--lib[only this library]:library:_bim_libs' \
            '--dry-run[show what would change]' \
            '1:device:_bim_devices' ;;
        completion)
          _arguments '1:shell:(zsh)' ;;
      esac ;;
  esac
}

_bim "$@"
"""


def _flat(text: str) -> str:
    return " ".join(text.split())


def run(args: list[str]) -> int:
    if len(args) != 2:
        return 2
    kind, word = args
    try:
        cfg = load_config()
        if kind == "lib":
            from bimport.library import libraries

            print("\n".join(libraries(cfg)))
            return 0
        if kind == "device":
            print("\n".join(sorted(cfg.devices)))
            return 0

        from bimport.library import scan

        books = scan(cfg, gc=False, quiet=True)
        if kind == "tag":
            tags = {t.casefold(): t for b in books for t in b.tags}
            print("\n".join(sorted(tags.values(), key=str.casefold)))
            return 0
        if kind != "book":
            return 2

        from bimport.match import MIN_FUZZY, by_id, rank, sort_key

        word = word.strip()
        if not word:
            ranked = sorted(books, key=sort_key)
        else:
            ranked = by_id(word, books)
            if len(word) >= MIN_FUZZY:
                seen = {b.sha256 for b in ranked}
                ranked += [b for b, _ in rank(word, books) if b.sha256 not in seen]
        lines = []
        for book in ranked if not word else ranked[:LIMIT]:
            authors = ", ".join(book.authors) or "Unknown"
            lines.append(f"{book.id}\t{_flat(book.title)} — {_flat(authors)} [{book.lib}]")
        if lines:
            print("\n".join(lines))
        return 0
    except (BimError, OSError) as exc:
        print(exc, file=sys.stderr)
        return 1
