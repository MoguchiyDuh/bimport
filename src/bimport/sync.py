import shlex
import shutil
import subprocess
from typing import IO

from bimport.config import Config, Device
from bimport.errors import NotFound, SyncError
from bimport.library import check_lib_name, scan


def _remote_path(dev: Device, lib: str | None) -> str:
    root = dev.root.rstrip("/") or "/"
    if root == "~":
        root = "."
    elif root.startswith("~/"):
        root = root[2:]
    return f"{root}/{lib}" if lib else root


def _run(cmd: list[str], out: IO[str] | None, what: str) -> None:
    try:
        result = subprocess.run(cmd, stdout=out, check=False)
    except OSError as exc:
        raise SyncError(f"{what}: {exc}") from exc
    if result.returncode != 0:
        raise SyncError(f"{what} failed with exit code {result.returncode}", command=cmd)


def sync(cfg: Config, device: str, lib: str | None, *, dry_run: bool, out: IO[str] | None) -> None:
    dev = cfg.devices.get(device)
    if dev is None:
        known = ", ".join(sorted(cfg.devices)) or "none configured"
        raise NotFound(f"no device {device!r} in {cfg.path} (known: {known})")
    rsync = shutil.which(cfg.rsync)
    if rsync is None:
        raise SyncError(f"rsync not found: {cfg.rsync!r}")

    local = cfg.root / check_lib_name(lib) if lib else cfg.root
    remote_dir = _remote_path(dev, lib)
    remote = f"{dev.host}:{remote_dir}/"
    base = [rsync, "-au", "--exclude=.DS_Store", "--exclude=.*.tmp"]
    if dry_run:
        base += ["--dry-run", "--itemize-changes"]
    if dev.rsync_path:
        base += ["--rsync-path", dev.rsync_path]

    if not dry_run:
        local.mkdir(parents=True, exist_ok=True)
        _run(["ssh", dev.host, f"mkdir -p -- {shlex.quote(remote_dir)}"], out, f"ssh {dev.host}")
    _run([*base, remote, f"{local}/"], out, f"pull from {device}")
    if not dry_run:
        scan(cfg, lib, gc=True)
    _run([*base, f"{local}/", remote], out, f"push to {device}")
