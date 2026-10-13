"""Translation key, cache entries, locks, and local builds (spec 005 R4-R8, R11;
FR-009 to FR-019, FR-032 to FR-036).

A cache entry holds one translation: `entry.json` (its metadata) next to `project/` (the
unpacked archive and, after building, Cargo's `target/`). With `--to DIR`, `DIR` is the
project and the metadata lives in `DIR/.wunderspec/`.
"""

import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from wunderspec.remote.unpack import unpack

KEY_VERSION = 1
ENTRY_VERSION = 1
MANIFEST = "wunderspec-manifest.json"
ENTRY_FILE = "entry.json"
LOCK_FILE = "lock"
PROJECT_META = ".wunderspec"
MIN_RUST = (1, 85)


class CacheError(ValueError):
    """The cache directory cannot be used safely."""


class ToolchainError(RuntimeError):
    """Cargo is missing or too old (FR-035)."""


class BuildError(RuntimeError):
    """`cargo build` failed (FR-036)."""


def now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


# --- translation key (R4) -----------------------------------------------------------------


def normalize_service(service: str) -> str:
    """Lower-case scheme and host, no default port, no trailing slash."""
    url = urllib.parse.urlsplit(service)
    scheme = url.scheme.lower()
    host = (url.hostname or "").lower()
    port = url.port
    netloc = (
        host
        if port is None or port == {"https": 443, "http": 80}.get(scheme)
        else (f"{host}:{port}")
    )
    return f"{scheme}://{netloc}{url.path.rstrip('/')}"


def translation_key(document: Any, options: Mapping[str, Any], service: str) -> str:
    """SHA-256 of the document, the translation options, and the service (R4)."""
    payload = {
        "key_version": KEY_VERSION,
        "document": document,
        "options": {
            "init": options.get("init"),
            "step": options.get("step"),
            "property": options.get("property"),
        },
        "service": normalize_service(service),
    }
    data = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


# --- cache root (R6) ----------------------------------------------------------------------


def cache_root_path(environ: Mapping[str, str], platform: str, home: Path) -> Path:
    if environ.get("WUNDERSPEC_CACHE_DIR"):
        return Path(os.path.expanduser(environ["WUNDERSPEC_CACHE_DIR"]))
    if platform == "win32":
        base = environ.get("LOCALAPPDATA") or str(home / "AppData" / "Local")
        return Path(base) / "wunderspec" / "cache" / "rust"
    if platform == "darwin":
        return home / "Library" / "Caches" / "wunderspec" / "rust"
    if environ.get("XDG_CACHE_HOME"):
        return Path(environ["XDG_CACHE_HOME"]) / "wunderspec" / "rust"
    return home / ".cache" / "wunderspec" / "rust"


def ensure_private_dir(path: Path) -> Path:
    """Create `path` with mode 0700 or check that it is a private directory (FR-014)."""
    if path.is_symlink():
        raise CacheError(f"{path}: the cache directory must not be a symlink")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name == "nt":
        # POSIX modes do not describe access on Windows; the user profile's ACL applies.
        return path
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode):
        raise CacheError(f"{path}: the cache must be a directory")
    if info.st_uid != os.getuid():
        raise CacheError(
            f"{path}: the cache directory must be owned by the current user"
        )
    if info.st_mode & 0o077:
        raise CacheError(
            f"{path}: the cache directory must be accessible only to you; "
            f"run: chmod 700 {path}"
        )
    return path


def cache_root(environ: Mapping[str, str]) -> Path:
    return ensure_private_dir(cache_root_path(environ, sys.platform, Path.home()))


# --- entries (R7) -------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryPaths:
    """Where an entry's metadata, lock, and project live."""

    meta: Path
    project: Path

    @classmethod
    def in_cache(cls, root: Path, key: str) -> "EntryPaths":
        base = root / "entries" / key
        return cls(base, base / "project")

    @classmethod
    def in_directory(cls, directory: Path) -> "EntryPaths":
        return cls(directory / PROJECT_META, directory)

    @property
    def entry_file(self) -> Path:
        return self.meta / ENTRY_FILE

    @property
    def lock_file(self) -> Path:
        return self.meta / LOCK_FILE


def new_entry(
    key: str, service: str, options: Mapping[str, Any], spec_path: Path | None
) -> dict[str, Any]:
    return {
        "entry_version": ENTRY_VERSION,
        "key": key,
        "service": normalize_service(service),
        "options": {
            "init": options.get("init"),
            "step": options.get("step"),
            "property": options.get("property"),
        },
        "spec_path": str(spec_path) if spec_path is not None else None,
        "state": None,
        "job_id": None,
        "translator_version": None,
        "runtime_version": None,
        "format_version": None,
        "translated_at": None,
        "last_used": now(),
        "builds": {},
    }


def mark_pending(data: dict[str, Any], job_id: str) -> None:
    data.update(state="pending", job_id=job_id)


def mark_complete(
    data: dict[str, Any],
    manifest: Mapping[str, Any],
    files: Mapping[str, int] | None = None,
) -> None:
    data.update(
        files=dict(files or {}),
        state="complete",
        job_id=manifest.get("job_id"),
        translator_version=manifest.get("translator_version"),
        runtime_version=manifest.get("runtime_version"),
        format_version=manifest.get("format_version"),
        translated_at=manifest.get("generated_at"),
        builds={},
    )


def touch(data: dict[str, Any]) -> None:
    data["last_used"] = now()


def read_entry(paths: EntryPaths) -> dict[str, Any] | None:
    try:
        data = json.loads(paths.entry_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_entry(paths: EntryPaths, data: Mapping[str, Any]) -> None:
    """Write `entry.json` atomically: a temporary file in the same directory, renamed."""
    paths.meta.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".entry.", suffix=".tmp", dir=paths.meta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(temporary, paths.entry_file)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def read_manifest(project: Path) -> dict[str, Any] | None:
    try:
        manifest = json.loads((project / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return manifest if isinstance(manifest, dict) else None


def is_valid_complete(paths: EntryPaths, data: Mapping[str, Any], key: str) -> bool:
    """A complete, consistent entry for `key` (FR-015)."""
    if data.get("entry_version") != ENTRY_VERSION or data.get("key") != key:
        return False
    if data.get("state") != "complete" or not data.get("job_id"):
        return False
    manifest = read_manifest(paths.project)
    if manifest is None or manifest.get("job_id") != data.get("job_id"):
        return False
    files = data.get("files", {})
    if not isinstance(files, dict):
        return False
    for relative, size in files.items():
        try:
            if (paths.project / relative).stat().st_size != size:
                return False
        except OSError:
            return False
    return True


def project_files(project: Path) -> dict[str, int]:
    """Relative path -> size of the archive's files in `project` (not builds or metadata)."""
    files = {}
    for path in sorted(project.rglob("*")):
        relative = path.relative_to(project)
        if relative.parts[0] in ("target", PROJECT_META) or not path.is_file():
            continue
        files[relative.as_posix()] = path.stat().st_size
    return files


class EntryLock:
    """An advisory lock on an entry; the kernel releases it if the holder dies (R7).

    Shared locks let several processes run the same model; translating, unpacking,
    building, and evicting take the lock exclusively.
    """

    def __init__(
        self, path: Path, *, shared: bool = False, blocking: bool = True
    ) -> None:
        self.path = path
        self.shared = shared
        self.blocking = blocking
        self.acquired = False
        self._stream: IO[bytes] | None = None

    def __enter__(self) -> "EntryLock":
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._stream = open(self.path, "a+b")
        if self._try(blocking=False):
            self.acquired = True
        elif self.blocking:
            print(
                f"waiting for another wunderspec rust process using {self.path.parent}",
                file=sys.stderr,
                flush=True,
            )
            self.acquired = self._try(blocking=True)
        return self

    def _try(self, *, blocking: bool) -> bool:
        assert self._stream is not None
        fd = self._stream.fileno()
        if sys.platform == "win32":  # pragma: no cover - exercised on Windows only
            import msvcrt

            self._stream.seek(0)
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(fd, mode, 1)
            except OSError:
                return False
            return True
        import fcntl

        operation = fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX
        if not blocking:
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(fd, operation)
        except BlockingIOError:
            return False
        return True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self._stream is not None:
            if self.acquired and sys.platform != "win32":
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_UN)
            self._stream.close()
            self._stream = None


def install_archive(
    paths: EntryPaths, data: bytes, job_id: str, *, in_directory: bool
) -> tuple[dict[str, Any], dict[str, int]]:
    """Unpack a job's archive safely and put it in place (R7, R11); return the manifest
    and the installed files.

    The archive is unpacked next to the project with the 003 unpacker and moved in only
    when complete. In a cache entry the project is replaced; in a `--to` directory every
    file of the previous translation goes, but Cargo's `target/` and `.wunderspec/` stay.
    """
    project = paths.project
    parent = project.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for stale in parent.glob(f".{project.name}.tmp-*"):
        remove_tree(stale)
    staging = parent / f".{project.name}.tmp-{os.getpid()}"
    try:
        manifest = unpack(data, staging, job_id)
        if in_directory:
            project.mkdir(exist_ok=True)
            for child in project.iterdir():
                if child.name not in ("target", PROJECT_META):
                    remove_tree(child)
            for child in staging.iterdir():
                os.replace(child, project / child.name)
        else:
            remove_tree(project)
            os.replace(staging, project)
    finally:
        remove_tree(staging)
    return manifest, project_files(project)


def find_any_service(
    root: Path, document: Any, options: Mapping[str, Any]
) -> list[str]:
    """Services with a complete entry for this document and these options (R5)."""
    services: list[str] = []
    entries = root / "entries"
    if not entries.is_dir():
        return services
    for entry_dir in sorted(entries.iterdir()):
        paths = EntryPaths(entry_dir, entry_dir / "project")
        data = read_entry(paths)
        if data is None or not isinstance(data.get("service"), str):
            continue
        key = translation_key(document, options, data["service"])
        if entry_dir.name == key and is_valid_complete(paths, data, key):
            services.append(data["service"])
    return sorted(set(services))


DEFAULT_MAX_SIZE = "2G"
_SIZE_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(text: str) -> int:
    """`2G`, `512M`, `1K`, or a number of bytes."""
    match = re.fullmatch(r"\s*(\d+)\s*([KMGT]?)B?\s*", text, re.IGNORECASE)
    if match is None:
        raise CacheError(f"invalid size {text!r}; use a number with K, M, or G")
    return int(match.group(1)) * _SIZE_UNITS[match.group(2).upper()]


def tree_size(path: Path) -> int:
    total = 0
    for directory, _, names in os.walk(path, followlinks=False):
        for name in names:
            try:
                total += (Path(directory) / name).lstat().st_size
            except OSError:
                pass
    return total


def evict(root: Path, limit: int, keep: str | None = None) -> list[str]:
    """Remove least recently used entries until the cache is under `limit` (FR-014).

    Never removes the entry `keep` or an entry another process holds a lock on. `--to`
    directories are not in the cache and are never touched. Returns the removed keys."""
    entries = root / "entries"
    if not entries.is_dir():
        return []
    candidates = []
    for entry_dir in entries.iterdir():
        if not entry_dir.is_dir() or entry_dir.name.startswith("."):
            continue
        data = read_entry(EntryPaths(entry_dir, entry_dir / "project")) or {}
        last_used = str(data.get("last_used") or "")
        candidates.append((last_used, entry_dir.name, entry_dir, tree_size(entry_dir)))
    total = sum(size for *_, size in candidates)
    removed = []
    for _, key, entry_dir, size in sorted(candidates):
        if total <= limit:
            break
        if key == keep:
            continue
        with EntryLock(entry_dir / LOCK_FILE, blocking=False) as lock:
            if not lock.acquired:
                continue
            remove_tree(entry_dir)
        total -= size
        removed.append(key)
    return removed


def remove_tree(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


# --- toolchain and builds (R8) ------------------------------------------------------------

_TOOLCHAIN_HINT = (
    "Rust 1.85 or newer is needed to build models (https://rustup.rs); "
    "compile --no-build, export, submit, and job work without it"
)


def check_toolchain() -> str:
    """Return `cargo --version` if it is Rust 1.85 or newer (FR-035)."""
    cargo = shutil.which("cargo")
    if cargo is None:
        raise ToolchainError(f"cargo was not found on PATH. {_TOOLCHAIN_HINT}")
    try:
        result = subprocess.run(
            [cargo, "--version"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise ToolchainError(f"cannot run cargo --version. {_TOOLCHAIN_HINT}") from None
    version = result.stdout.strip()
    match = re.match(r"cargo (\d+)\.(\d+)\.", version)
    if result.returncode != 0 or match is None:
        raise ToolchainError(f"cannot determine the cargo version. {_TOOLCHAIN_HINT}")
    if (int(match.group(1)), int(match.group(2))) < MIN_RUST:
        raise ToolchainError(f"found {version}. {_TOOLCHAIN_HINT}")
    return version


def binary_relative(profile: str) -> str:
    name = "wunderspec-model.exe" if sys.platform == "win32" else "wunderspec-model"
    return f"target/{'release' if profile == 'release' else profile}/{name}"


def _cargo_build_command(profile: str) -> list[str]:
    if profile == "release":
        return ["cargo", "build", "--release"]
    return ["cargo", "build", "--profile", profile]


def build(
    project: Path, profile: str, data: dict[str, Any], cargo_version: str
) -> Path:
    """Build `profile` with Cargo, sending its output to stderr, and record the build."""
    data.setdefault("builds", {}).pop(profile, None)
    process = subprocess.Popen(
        _cargo_build_command(profile),
        cwd=str(project),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    fd = process.stdout.fileno()
    for chunk in iter(lambda: os.read(fd, 65536), b""):
        sys.stderr.write(chunk.decode("utf-8", errors="replace"))
        sys.stderr.flush()
    status = process.wait()
    if status in (-signal.SIGINT, 128 + signal.SIGINT):
        raise KeyboardInterrupt  # Ctrl-C reached cargo too; exit as interrupted
    if status != 0:
        raise BuildError(f"cargo build --profile {profile} failed")
    relative = binary_relative(profile)
    binary = project / relative
    info = binary.stat()
    data["builds"][profile] = {
        "binary": relative,
        "size": info.st_size,
        "mtime_ns": info.st_mtime_ns,
        "cargo_version": cargo_version,
        "built_at": now(),
    }
    return binary


def is_current(project: Path, data: Mapping[str, Any], profile: str) -> bool:
    record = data.get("builds", {}).get(profile)
    if not isinstance(record, dict):
        return False
    try:
        info = (project / record["binary"]).stat()
    except (OSError, KeyError, TypeError):
        return False
    return bool(
        info.st_size == record.get("size")
        and info.st_mtime_ns == record.get("mtime_ns")
    )
