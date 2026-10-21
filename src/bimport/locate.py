import re
from pathlib import Path

from bimport.errors import BimError, NotFound

_SIOYEK_PREFS = Path("~/.config/sioyek/prefs_user.config")
_SIOYEK_EPUB = {"epub_width": 400.0, "epub_height": 700.0, "epub_font_size": 14.0}


def sioyek_layout() -> dict[str, float]:
    values = dict(_SIOYEK_EPUB)
    try:
        text = _SIOYEK_PREFS.expanduser().read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    for line in text.splitlines():
        key, _, value = line.strip().partition(" ")
        if key in values:
            try:
                values[key] = float(value)
            except ValueError:
                continue
    return {"width": values["epub_width"], "height": values["epub_height"], "fontsize": values["epub_font_size"]}


def chapter_page(path: Path, chapter: int) -> int:
    try:
        import pymupdf

        pymupdf.TOOLS.mupdf_display_errors(False)
        with pymupdf.open(path) as doc:
            if doc.is_reflowable:
                doc.layout(**sioyek_layout())
            toc = doc.get_toc()
    except Exception as exc:
        raise BimError(f"{path}: cannot read table of contents: {exc}") from exc
    title = re.compile(rf"(?:chapter\s+)?0*{chapter}(?!\d)", re.IGNORECASE)
    hits = [(level, page) for level, text, page in toc if page > 0 and title.match(text.strip())]
    if not hits:
        raise NotFound(f"chapter {chapter} not found in the table of contents; use --page instead")
    return min(hits)[1]
