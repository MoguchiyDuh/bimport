import os
import shutil
import sys
from pathlib import Path

from bimport import cache
from bimport.errors import BimError


class Device:
    __slots__ = ("host", "name", "root", "rsync_path")

    def __init__(self, name: str, host: str, root: str, rsync_path: str | None) -> None:
        self.name = name
        self.host = host
        self.root = root
        self.rsync_path = rsync_path


class Config:
    __slots__ = ("default_lib", "devices", "opener", "path", "root", "rsync")

    def __init__(
        self,
        path: Path,
        root: Path,
        default_lib: str | None,
        opener: str,
        rsync: str,
        devices: dict[str, Device],
    ) -> None:
        self.path = path
        self.root = root
        self.default_lib = default_lib
        self.opener = opener
        self.rsync = rsync
        self.devices = devices


def config_path() -> Path:
    if explicit := os.environ.get("BIM_CONFIG"):
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "bimport" / "config.toml"


def _str(table: dict[str, object], key: str, where: str, default: str | None = None) -> str | None:
    value = table.get(key, default)
    if value is not None and not isinstance(value, str):
        raise BimError(f"{where}: '{key}' must be a string")
    return value


def _read(path: Path) -> dict[str, object]:
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise BimError(f"cannot read config {path}: {exc}") from exc
    key = (str(path), st.st_mtime_ns, st.st_size, st.st_ino)
    cached = cache.load("config")
    if isinstance(cached, tuple) and len(cached) == 2 and cached[0] == key and isinstance(cached[1], dict):
        return cached[1]

    import tomllib

    try:
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise BimError(f"cannot read config {path}: {exc}") from exc
    cache.save("config", (key, data))
    return data


def load_config() -> Config:
    path = config_path()
    data = _read(path)
    where = str(path)
    default_opener = "open -a sioyek" if sys.platform == "darwin" and not shutil.which("sioyek") else "sioyek"
    root = _str(data, "root", where, "~/Documents/Books") or "~/Documents/Books"
    opener = _str(data, "opener", where, default_opener) or default_opener
    rsync = _str(data, "rsync", where, "rsync") or "rsync"

    raw_devices = data.get("devices", {})
    if not isinstance(raw_devices, dict):
        raise BimError(f"{where}: 'devices' must be a table")
    devices: dict[str, Device] = {}
    for name, table in raw_devices.items():
        dev_where = f"{where}: devices.{name}"
        if not isinstance(table, dict):
            raise BimError(f"{dev_where} must be a table")
        host = _str(table, "host", dev_where)
        if not host:
            raise BimError(f"{dev_where}: 'host' is required")
        devices[name] = Device(
            name=name,
            host=host,
            root=_str(table, "root", dev_where, "~/Documents/Books") or "~/Documents/Books",
            rsync_path=_str(table, "rsync_path", dev_where),
        )

    return Config(
        path=path,
        root=Path(root).expanduser(),
        default_lib=_str(data, "default_lib", where),
        opener=opener,
        rsync=rsync,
        devices=devices,
    )
