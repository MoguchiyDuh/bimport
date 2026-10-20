import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bimport.errors import BimError


@dataclass(frozen=True, slots=True)
class Device:
    name: str
    host: str
    root: str
    rsync_path: str | None


@dataclass(frozen=True, slots=True)
class Config:
    path: Path
    root: Path
    default_lib: str | None
    opener: str
    rsync: str
    devices: dict[str, Device]


def config_path() -> Path:
    if explicit := os.environ.get("BIM_CONFIG"):
        return Path(explicit).expanduser()
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(base).expanduser() / "bimport" / "config.toml"


def _str(table: dict[str, Any], key: str, where: str, default: str | None = None) -> str | None:
    value = table.get(key, default)
    if value is not None and not isinstance(value, str):
        raise BimError(f"{where}: '{key}' must be a string")
    return value


def load_config() -> Config:
    path = config_path()
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            with path.open("rb") as fh:
                data = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise BimError(f"cannot read config {path}: {exc}") from exc

    where = str(path)
    default_opener = "open -a sioyek" if sys.platform == "darwin" else "sioyek"
    root = _str(data, "root", where, "~/Documents/Books")
    opener = _str(data, "opener", where, default_opener)
    rsync = _str(data, "rsync", where, "rsync")
    assert root is not None and opener is not None and rsync is not None

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
        dev_root = _str(table, "root", dev_where, "~/Documents/Books")
        assert dev_root is not None
        devices[name] = Device(
            name=name,
            host=host,
            root=dev_root,
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
