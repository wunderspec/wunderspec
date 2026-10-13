"""Strict JSON parsing and size limits for transfer documents (research R3 steps 6–7).

contracts/transfer-format.md, "Encoding" and "Limits": UTF-8 without a byte order mark, no
duplicate keys, integers only (no fractions, exponents, ``NaN``, or ``Infinity``), and bounded
depth, size, and string and integer lengths. Anything that is not JSON, including Python pickles,
fails here; nothing is ever deserialized into objects other than dicts, lists, strings, integers,
booleans, and ``None`` (FR-008, Principle VI).

Problems found while ``json.loads`` runs are recorded as marker values and reported by the walk
that follows, so every error carries the JSON Pointer of the offending value.
"""

import json
from collections.abc import Sequence

from wunderspec.transfer.errors import TransferError as TransferInvalid
from wunderspec.transfer.limits import (
    MAX_BYTES,
    MAX_DEPTH,
    MAX_FILES,
    MAX_INT_DIGITS,
    MAX_STRING,
    MAX_TAGGED,
)


class _Marker:
    """Stands in for a value ``json.loads`` must not produce; reported by ``_walk``."""

    def __init__(self, problem: str) -> None:
        self.problem: str = problem


_DUPLICATE_KEYS = _Marker("duplicate_key")
_NON_FINITE = _Marker("non_finite_number")
_NOT_INTEGER = _Marker("wrong_type")
_INTEGER_TOO_LARGE = _Marker("integer_too_large")


def json_pointer(path: Sequence[str | int]) -> str:
    return "".join(
        "/" + str(part).replace("~", "~0").replace("/", "~1") for part in path
    )


def _object(pairs: list[tuple[str, object]]) -> object:
    result = dict(pairs)
    if len(result) != len(pairs):
        return _DUPLICATE_KEYS
    return result


def _integer(text: str) -> object:
    # Checked before int(): a long digit string costs quadratic time to convert.
    if len(text.lstrip("-")) > MAX_INT_DIGITS:
        return _INTEGER_TOO_LARGE
    return int(text)


def _decode(body: bytes) -> object:
    if body.startswith(b"\xef\xbb\xbf"):
        raise TransferInvalid("not_utf8")
    try:
        text = body.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise TransferInvalid("not_utf8") from None
    try:
        return json.loads(
            text,
            object_pairs_hook=_object,
            parse_constant=lambda _: _NON_FINITE,
            parse_float=lambda _: _NOT_INTEGER,
            parse_int=_integer,
        )
    except RecursionError:
        raise TransferInvalid("too_deep") from None
    except ValueError:
        raise TransferInvalid("not_json") from None


def _check_string(value: str, path: tuple[str | int, ...]) -> None:
    if len(value) > MAX_STRING:
        raise TransferInvalid("string_too_long", json_pointer(path))
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:  # lone surrogates from \ud800-style escapes
        raise TransferInvalid("not_utf8", json_pointer(path)) from None


def _walk(root: object) -> None:
    """Depth-first, with an explicit stack: deep documents cannot exhaust the Python stack."""
    tagged = 0
    stack: list[tuple[object, tuple[str | int, ...]]] = [(root, ())]
    while stack:
        value, path = stack.pop()
        if isinstance(value, _Marker):
            raise TransferInvalid(value.problem, json_pointer(path))
        if type(value) in (dict, list) and len(path) + 1 > MAX_DEPTH:
            raise TransferInvalid("too_deep", json_pointer(path))
        if type(value) is dict:
            if len(value) == 1 and isinstance(next(iter(value.values())), dict):
                tagged += 1
                if tagged > MAX_TAGGED:
                    raise TransferInvalid("too_many_nodes")
            for key in reversed(list(value)):
                if type(key) is not str:
                    raise TransferInvalid("wrong_type", json_pointer(path))
                _check_string(key, path)
                stack.append((value[key], (*path, key)))
        elif type(value) is list:
            for index in range(len(value) - 1, -1, -1):
                stack.append((value[index], (*path, index)))
        elif type(value) is str:
            _check_string(value, path)
        elif type(value) is int:
            if abs(value) >= 10**MAX_INT_DIGITS:
                raise TransferInvalid("integer_too_large", json_pointer(path))
        elif value is not None and type(value) is not bool:
            raise TransferInvalid("wrong_type", json_pointer(path))


def parse_strict(body: bytes, *, max_bytes: int = MAX_BYTES) -> object:
    """Parse ``body`` as a transfer document's JSON and enforce the size limits."""
    if len(body) > max_bytes:
        raise TransferInvalid("document_too_large")
    value = _decode(body)
    _walk(value)
    if type(value) is dict:
        files = value.get("files")
        if isinstance(files, list) and len(files) > MAX_FILES:
            raise TransferInvalid("too_many_files", "/files")
    return value
