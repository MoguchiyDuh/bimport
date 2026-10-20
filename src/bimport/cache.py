import contextlib
import marshal
from typing import Any
import os


def _path(name: str) -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "bimport", f"{name}.marshal")


def load(name: str) -> object:
    try:
        with open(_path(name), "rb") as fh:
            return marshal.load(fh)
    except (OSError, EOFError, ValueError, TypeError):
        return None


def save(name: str, value: dict[str, Any] | tuple[Any, ...]) -> None:
    path = _path(name)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(tmp, "wb") as fh:
            marshal.dump(value, fh)
        os.replace(tmp, path)
    except (OSError, ValueError):
        with contextlib.suppress(OSError):
            os.unlink(tmp)
