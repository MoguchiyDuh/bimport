# bimport

Personal ebook library CLI (`bim`). One folder per book with a `meta.toml` sidecar, fuzzy lookup, two-way rsync between machines. Offline, no database.

```
~/Documents/Books/<lib>/<first author>/<title>/
  <title>.<ext>
  meta.toml
```

## Install

```sh
uv tool install git+https://github.com/MoguchiyDuh/bimport
bim completion zsh > ~/.zfunc/_bim   # any dir in $fpath
```

## Config

`~/.config/bimport/config.toml` (override with `BIM_CONFIG`). Per machine, not synced.

```toml
root = "~/Documents/Books"
default_lib = "CS"
opener = "sioyek"            # macOS default: "open -a sioyek" when sioyek is not on PATH
rsync = "rsync"

[devices.mac]
host = "mac"                 # ssh alias from ~/.ssh/config
root = "~/Documents/Books"
rsync_path = "/opt/homebrew/bin/rsync"   # optional, remote rsync binary
```

## Commands

```
bim add FILE... [-t TITLE] [-a AUTHOR]... [--tag T]... [--lib L] [--force]
bim ls [QUERY] [-a AUTHOR]... [--tag T]... [--lib L]
bim show ID|QUERY
bim open ID|QUERY [-c CHAPTER] [-p PAGE]
bim edit ID|QUERY [-t TITLE] [-a AUTHOR]... [--tag T]... [--untag T]...
bim rm ID|QUERY
bim libs
bim adopt LIB [--dry-run]
bim sync DEVICE [--lib L] [--dry-run]
bim completion zsh
```

Every command takes `--json`.

`ID|QUERY` is an id prefix (4+ hex chars) or a fuzzy query over title, authors and tags. Matching ignores case and diacritics, tolerates typos and matches Cyrillic through Latin transliteration (`zorich` finds `Зорич`). If the top match beats the runner-up by 15+ points it is used. Otherwise a terminal gets a picker, and `--json` or non-TTY callers get exit code 4 with ranked candidates.

`open -c N` jumps to chapter N through the book's table of contents, `-p N` to page N (wins over `-c`). EPUB pages are laid out with sioyek's `epub_width`, `epub_height` and `epub_font_size` from `~/.config/sioyek/prefs_user.config`, so they match what sioyek shows. Jumps need sioyek as the opener.

`rm` in a terminal accepts a query and always asks for confirmation. Non-interactive `rm` requires an exact 8+ char id.

`adopt` writes `meta.toml` into existing `Author/Title/` folders (e.g. a Calibre export) without moving or renaming anything.

## Sync

`bim sync mac` runs `rsync -au` pull, applies incoming deletions, then pushes. The newer file wins and nothing is deleted with `--delete`. `rm` and renames leave a tombstone `meta.toml` (`deleted = true`); each machine removes the stale book file on its next run. Keep clocks in sync (NTP).

## Exit codes

| code | meaning |
|---|---|
| 0 | ok |
| 1 | error / cancelled |
| 2 | usage |
| 3 | not found |
| 4 | ambiguous (candidates in JSON) |
| 5 | duplicate |
| 6 | sync failed |

With `--json`, errors go to stderr as `{"error": ..., "code": ...}`.
