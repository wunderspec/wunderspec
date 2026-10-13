"""Service configuration file; messages never include the token or the file's contents."""

import os
import stat
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_CONFIG = "~/.wunderspec/service.yaml"
MAX_CONFIG_BYTES = 64 * 1024
_KEYS = {"service", "token", "ca_file"}
_REQUIRED = ("service", "token")
# POSIX permission bits do not describe access on Windows; there the file must live in the
# user profile (documentation).
_POSIX_MODES = os.name != "nt"
EXAMPLE = """\
service: https://YOUR_SERVICE_HOST
token: YOUR_SAAS_TOKEN
ca_file: test-ca.pem   # optional, relative to this file"""


class ConfigError(ValueError):
    pass


class ConfigNotFound(ConfigError):
    """No configuration file exists at the selected location."""


@dataclass(frozen=True)
class ServiceConfig:
    service: str
    token: str
    ca_file: Path | None


def check_service_url(service: str) -> str:
    """Return the URL if it is https:// without credentials, query, or fragment."""
    message = "service URL must be https:// without credentials, query, or fragment"
    url = urllib.parse.urlsplit(service)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        raise ValueError(message)
    try:
        url.port
    except ValueError:
        raise ValueError(message) from None
    return service


def check_token(token: str) -> str:
    if (
        not token
        or len(token) > 4096
        or any(ord(c) < 33 or ord(c) > 126 for c in token)
    ):
        raise ValueError("token must be 1-4096 printable ASCII characters")
    return token


class _Loader(yaml.SafeLoader):
    pass


def _mapping_without_duplicates(loader, node, deep=False):
    keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in keys:
            raise ConfigError(f"duplicate key {key!r}")
        keys.add(key)
    return loader.construct_mapping(node, deep=deep)


_Loader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_duplicates
)


def _locate(cli_path: str | None, environ: Mapping[str, str]) -> tuple[Path, str]:
    if cli_path is not None:
        return Path(os.path.expanduser(cli_path)), "--config"
    if "WUNDERSPEC_CONFIG" in environ:
        value = environ["WUNDERSPEC_CONFIG"]
        if not value:
            raise ConfigError("WUNDERSPEC_CONFIG is set but empty")
        return Path(os.path.expanduser(value)), "WUNDERSPEC_CONFIG"
    return Path(os.path.expanduser(DEFAULT_CONFIG)), "default"


def _read(path: Path, source: str) -> bytes:
    try:
        # O_NONBLOCK: opening a FIFO must not wait for a writer.
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        raise ConfigNotFound(
            f"{path}: service configuration file not found ({source}); "
            f"create it with mode 600 and:\n{EXAMPLE}"
        ) from None
    except OSError:
        raise ConfigError(
            f"{path}: cannot open the service configuration file"
        ) from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise ConfigError(f"{path}: must be a regular file")
        if _POSIX_MODES:
            if info.st_uid != os.getuid():
                raise ConfigError(f"{path}: must be owned by the current user")
            if info.st_mode & 0o077:
                raise ConfigError(
                    f"{path}: contains your token and must be accessible only to "
                    f"you; run: chmod 600 {path}"
                )
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, "rb") as stream:
        data = stream.read(MAX_CONFIG_BYTES + 1)
    if len(data) > MAX_CONFIG_BYTES:
        raise ConfigError(f"{path}: larger than 64 KiB")
    return data


def _parse(path: Path, data: bytes) -> dict:
    try:
        value = yaml.load(data.decode("utf-8"), Loader=_Loader)  # noqa: S506 (safe)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from None
    except UnicodeDecodeError:
        raise ConfigError(f"{path}: not UTF-8 text") from None
    except yaml.YAMLError as error:
        # Never quote the offending text: it may be the token.
        mark = getattr(error, "problem_mark", None)
        where = f" at line {mark.line + 1}" if mark is not None else ""
        raise ConfigError(f"{path}: invalid YAML{where}") from None
    if not isinstance(value, dict):
        raise ConfigError(f"{path}: must be a YAML mapping of service, token, ca_file")
    unknown = sorted(str(key) for key in value if key not in _KEYS)
    if unknown:
        raise ConfigError(f"{path}: unknown key {unknown[0]!r}; allowed: {EXAMPLE}")
    for key in _REQUIRED:
        if key not in value:
            raise ConfigError(f"{path}: missing required key {key!r}")
    for key, item in value.items():
        if not isinstance(item, str):
            raise ConfigError(f"{path}: {key!r} must be a string")
    return value


def load_config(cli_path: str | None, environ: Mapping[str, str]) -> ServiceConfig:
    """Load the file named by --config, WUNDERSPEC_CONFIG, or the default location."""
    path, source = _locate(cli_path, environ)
    value = _parse(path, _read(path, source))
    try:
        service = check_service_url(value["service"])
    except ValueError as error:
        raise ConfigError(f"{path}: 'service': {error}") from None
    try:
        token = check_token(value["token"])
    except ValueError as error:
        raise ConfigError(f"{path}: 'token': {error}") from None
    ca_file = None
    if "ca_file" in value:
        ca_file = Path(os.path.expanduser(value["ca_file"]))
        if not ca_file.is_absolute():
            ca_file = path.parent / ca_file
        if not ca_file.is_file():
            raise ConfigError(f"{path}: 'ca_file': {ca_file} is not a file")
    return ServiceConfig(service, token, ca_file)
