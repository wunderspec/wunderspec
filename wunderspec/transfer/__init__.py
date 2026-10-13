"""Versioned, declarative Wunderspec AST transfer.

Only export_spec evaluates Python, locally. Reading and importing documents never
load user code. JSON is the only supported AST interchange format.
"""

import json
import re
import sys
import threading
from pathlib import Path
from typing import IO, Any

from wunderspec.api import load_module
from wunderspec.source_tracking import enable_source_tracking
from wunderspec.transfer._parse import parse_strict
from wunderspec.transfer._validate import validate_document
from wunderspec.transfer.decode import import_spec
from wunderspec.transfer.encode import document_json
from wunderspec.transfer.errors import TransferError
from wunderspec.transfer.gather import GatheredSpec, gather

__all__ = [
    "export_spec",
    "write_document",
    "read_document",
    "import_spec",
    "TransferError",
]
_EXPORT_LOCK = threading.RLock()


_INSTANCE_VALUE = re.compile(r"/spec/instances/(\d+)/params/(\d+)/1(/.*)?")
_ACTION_NODES = frozenset(
    {"assume", "assign", "nondetChoice", "actionChoice", "actionAnd", "actionCall"}
    | {"actionLet"}
)
_TEMPORAL_NODES = frozenset(
    {"toTemporal", "always", "eventually", "enabled", "fairness"}
)


def _resolve(document: Any, pointer: str) -> Any:
    for part in pointer.split("/")[1:]:
        document = document[int(part) if isinstance(document, list) else part]
    return document


def _reason(document: dict, error: TransferError, inner: str) -> str:
    """Why an instance value cannot be carried (rule 3a), for the export warning."""
    if error.problem == "unbound_variable":
        var = _resolve(document, error.path)
        return f"unbound variable {var['name']}"
    kind = inner.rsplit("/", 1)[-1] if inner else ""
    if kind in _ACTION_NODES:
        return f"action node {kind}"
    if kind in _TEMPORAL_NODES:
        return f"temporal node {kind}"
    if error.problem == "ill_sorted":
        return "the value does not have the parameter's sort"
    return error.problem


def _validated(
    gathered: GatheredSpec, options: dict[str, str]
) -> tuple[GatheredSpec, dict]:
    """Encode and validate; drop, with a warning, each instance whose value the transfer
    format cannot carry (wunderspec-saas spec 006, FR-005). No value that the Python API builds
    is known to be rejected; this is a safeguard."""
    while True:
        document = document_json(gathered)
        try:
            return gathered, validate_document(document, options)
        except TransferError as exc:
            match = _INSTANCE_VALUE.fullmatch(exc.path or "")
            if match is None:
                raise
            index, value = int(match.group(1)), int(match.group(2))
            instance = document["spec"]["instances"][index]
            param = instance["params"][value][0]
            reason = _reason(document, exc, match.group(3) or "")
            print(
                f"wunderspec.transfer: skipping instance '{instance['name']}' "
                f"(param '{param}': {reason})",
                file=sys.stderr,
            )
            gathered.instances.pop(index)


def export_spec(path: str | Path, *, init: str = "init", step: str = "step") -> dict:
    """Evaluate a local Python specification and export transfer format 1.1.

    Source gathering uses Wunderspec's shared name counter; serialize that part.
    Encoding and declarative imports keep all state per operation.
    """
    path = Path(path).resolve()
    try:
        with _EXPORT_LOCK, enable_source_tracking():
            gathered = gather(load_module(path))
        _, document = _validated(gathered, {"init": init, "step": step})
        return document
    except TransferError as exc:
        if exc.location is not None:
            exc.location = type(exc.location)(
                str(path.parent / (exc.location.filename or path.name)),
                exc.location.lineno,
                exc.location.col_offset,
                exc.location.end_lineno,
                exc.location.end_col_offset,
            )
        raise
    except Exception as exc:
        raise TransferError("export_failed", location=None) from exc


def read_document(data: bytes) -> dict:
    """Parse and fully validate UTF-8 JSON, rejecting unsupported formats."""
    return validate_document(parse_strict(data))


def write_document(document: dict, file: str | Path | IO[str]) -> None:
    """Write stable, pretty JSON. The compact document must fit submission limits."""
    validate_document(document)
    text = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if isinstance(file, (str, Path)):
        Path(file).write_text(text, encoding="utf-8")
    else:
        file.write(text)
