"""Validate an artifact and install it transactionally (FR-025a)."""

import io
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path
from typing import Any

MAX_ENTRIES = 5000
MAX_UNCOMPRESSED = 200 * 1024 * 1024
MAX_RATIO = 100
MANIFEST = "wunderspec-manifest.json"
_UNSAFE = re.compile(r"[\x00-\x1f\x7f\\:]")


class ArchiveError(ValueError):
    """The downloaded archive is invalid; no output was installed."""


def check_output(out: Path, force: bool = False) -> None:
    """Reject invalid output destinations before making a network request."""
    if out.is_symlink() or (out.exists() and not out.is_dir()):
        raise ValueError("output must be a directory, without a symlink")
    if out.exists() and not force and any(out.iterdir()):
        raise ValueError(
            "output directory is not empty; use --force to replace generated files"
        )


def _existing_files(out: Path) -> None:
    for directory, dirs, files in os.walk(out, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise ArchiveError("existing output contains symlinks or special files")


def _entries(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, Path]]:
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ENTRIES:
        raise ArchiveError("archive entry limit")
    seen: set[str] = set()
    top = None
    total = 0
    result = []
    for entry in entries:
        name = entry.filename
        parts = name.split("/")
        mode = entry.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if (
            entry.orig_filename != name
            or _UNSAFE.search(name)
            or len(parts) < 2
            or any(p in ("", ".", "..") for p in parts)
            or kind not in (0, stat.S_IFREG)
            or entry.is_dir()
            or entry.external_attr & 0x10
            or entry.flag_bits & 1
        ):
            raise ArchiveError("unsafe archive entry")
        if top is None:
            top = parts[0]
        if parts[0] != top:
            raise ArchiveError("archive must have one top-level directory")
        relative = "/".join(parts[1:])
        key = relative.casefold()
        if key in seen:
            raise ArchiveError("duplicate archive entry")
        seen.add(key)
        total += entry.file_size
        if (
            total > MAX_UNCOMPRESSED
            or entry.file_size > MAX_RATIO * entry.compress_size
        ):
            raise ArchiveError("archive size or compression ratio limit")
        result.append((entry, Path(*parts[1:])))
    # Files cannot also act as directories, including case-insensitive collisions.
    for key in seen:
        parts = key.split("/")
        if any("/".join(parts[:i]) in seen for i in range(1, len(parts))):
            raise ArchiveError("archive file/directory collision")
    if MANIFEST not in {str(path) for _, path in result}:
        raise ArchiveError("missing wunderspec-manifest.json")
    return result


def unpack(
    data: bytes, out: str | Path, job_id: str, *, force: bool = False
) -> dict[str, Any]:
    """Strip the common top directory and install only fully validated regular files.

    With --force a staged copy preserves unrelated files. The directory swap has
    rollback, so CRC failures and filesystem errors never leave partial output.
    """
    out = Path(out).absolute()
    check_output(out, force)
    if out.exists():
        _existing_files(out)
    staging: Path | None = None
    backup: Path | None = None
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = _entries(archive)
            manifest_entry = next(
                entry for entry, path in entries if str(path) == MANIFEST
            )
            if manifest_entry.file_size > 65536:
                raise ArchiveError("manifest too large")
            manifest = json.loads(archive.read(manifest_entry))
            if not isinstance(manifest, dict) or manifest.get("job_id") != job_id:
                raise ArchiveError("manifest job ID does not match")
            out.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(tempfile.mkdtemp(prefix=f".{out.name}-", dir=out.parent))
            if out.exists():
                shutil.copytree(out, staging, dirs_exist_ok=True)
            for entry, relative in entries:
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(entry) as source, destination.open("wb") as target:
                    shutil.copyfileobj(source, target)
            if out.exists():
                backup = Path(
                    tempfile.mkdtemp(prefix=f".{out.name}-backup-", dir=out.parent)
                )
                backup.rmdir()
                os.replace(out, backup)
            try:
                os.replace(staging, out)
                staging = None
            except OSError:
                if backup is not None:
                    os.replace(backup, out)
                    backup = None
                raise
        if backup is not None:
            shutil.rmtree(backup)
            backup = None
        return manifest
    except (
        OSError,
        ValueError,
        RuntimeError,
        zipfile.BadZipFile,
        EOFError,
        NotImplementedError,
    ) as exc:
        if isinstance(exc, ArchiveError):
            raise
        raise ArchiveError("invalid archive or output could not be installed") from None
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if backup is not None and backup.exists():
            if not out.exists():
                os.replace(backup, out)
            else:
                shutil.rmtree(backup, ignore_errors=True)
