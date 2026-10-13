"""Validate against the bundled, fixed format-1 schema without runtime dependencies.

This deliberately implements only the vocabulary used by this pinned schema. The
independent jsonschema tests check it against the reference implementation.
"""

import json
import re
from pathlib import Path
from typing import Any, cast

from wunderspec.ast.ast import SourceSpan
from wunderspec.transfer._checks import ContextChecker
from wunderspec.transfer._construct import Decoder
from wunderspec.transfer._parse import _walk, json_pointer
from wunderspec.transfer._semantics import check_names_and_references, check_selection
from wunderspec.transfer.errors import TransferError
from wunderspec.transfer.gather import GatheredSpec
from wunderspec.transfer.limits import MAX_BYTES, MAX_FILES, SUPPORTED_VERSIONS

_SCHEMA = json.loads(
    (Path(__file__).parent / "schema/wunderspec-transfer-1.json").read_text()
)
_PRODUCER = re.compile(r"[A-Za-z0-9._+-]{1,64}")
_SEGMENT = re.compile(r"[^\x00-\x1f\x7f/\\:]+")
_COMMENT = re.compile(r"[\x00-\x08\x0b-\x1f\x7f\u0080-\u009f]")
_TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "boolean": bool,
    "null": type(None),
}


def _resolve(schema: dict) -> dict:
    if "$ref" in schema:
        return cast(dict, _SCHEMA["$defs"][schema["$ref"].rsplit("/", 1)[-1]])
    return schema


def _accepts(value: Any, schema: dict) -> bool:
    schema = _resolve(schema)
    union = schema.get("oneOf", schema.get("anyOf"))
    if union is not None:
        return any(_accepts(value, branch) for branch in union)
    if "type" in schema and type(value) is not _TYPES[schema["type"]]:
        return False
    if isinstance(value, dict) and schema.get("additionalProperties") is False:
        return len(value) == 1 and next(iter(value)) in schema.get("properties", {})
    return True


def _shape(value: Any, schema: dict, path: tuple = ()) -> None:
    schema = _resolve(schema)
    union = schema.get("oneOf", schema.get("anyOf"))
    if union is not None:
        # External constructor tags select one fixed branch; no Python name resolution.
        if isinstance(value, dict):
            if len(value) == 1:
                for branch in union:
                    if _accepts(value, branch):
                        _shape(value, branch, path)
                        return
            code = (
                "unknown_node_kind"
                if schema is _SCHEMA["$defs"]["Node"]
                else "wrong_type"
            )
            raise TransferError(code, json_pointer(path))
        for branch in union:
            if _accepts(value, branch):
                _shape(value, branch, path)
                return
        raise TransferError("wrong_type", json_pointer(path))
    if "type" in schema and type(value) is not _TYPES[schema["type"]]:
        raise TransferError("wrong_type", json_pointer(path))
    if "const" in schema and value != schema["const"]:
        raise TransferError("wrong_format", json_pointer(path))
    if "enum" in schema and value not in schema["enum"]:
        raise TransferError("wrong_type", json_pointer(path))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in value:
            if key not in properties and schema.get("additionalProperties") is False:
                raise TransferError("unknown_field", json_pointer((*path, key)))
        for key in schema.get("required", []):
            if key not in value:
                raise TransferError("missing_field", json_pointer((*path, key)))
        for key, item in value.items():
            if key in properties:
                _shape(item, properties[key], (*path, key))
    elif isinstance(value, list):
        if (
            not schema.get("minItems", 0)
            <= len(value)
            <= schema.get("maxItems", float("inf"))
        ):
            raise TransferError("wrong_type", json_pointer(path))
        prefix = schema.get("prefixItems", [])
        for index, item in enumerate(value):
            subschema = (
                prefix[index] if index < len(prefix) else schema.get("items", {})
            )
            if subschema is False:
                raise TransferError("wrong_type", json_pointer(path))
            _shape(item, subschema, (*path, index))
    elif isinstance(value, str):
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise TransferError("invalid_identifier", json_pointer(path))
        if len(value) > schema.get("maxLength", float("inf")):
            raise TransferError("string_too_long", json_pointer(path))
    elif type(value) is int and value < schema.get("minimum", value):
        raise TransferError("wrong_type", json_pointer(path))


def _extras(document: dict) -> None:
    for key, value in document["producer"].items():
        if not _PRODUCER.fullmatch(value):
            raise TransferError("wrong_format", f"/producer/{key}")
    for i, filename in enumerate(document["files"]):
        if len(filename) > 255 or any(
            p in ("", ".", "..") or not _SEGMENT.fullmatch(p)
            for p in filename.split("/")
        ):
            raise TransferError("wrong_format", f"/files/{i}")
    for i, (_, comment) in enumerate(document["spec"]["operatorComments"]):
        if _COMMENT.search(comment):
            raise TransferError("wrong_format", f"/spec/operatorComments/{i}/1")
    stack: list[tuple[Any, tuple[str | int, ...]]] = [(document["spec"], ("spec",))]
    while stack:
        value, path = stack.pop()
        if isinstance(value, dict):
            if "loc" in value:
                file, line, col, endline, endcol = value["loc"]
                if not (
                    0 <= file < len(document["files"])
                    and line >= 1
                    and endline >= 1
                    and col >= 0
                    and endcol >= 0
                    and (endline, endcol) >= (line, col)
                ):
                    raise TransferError(
                        "invalid_location", json_pointer((*path, "loc"))
                    )
            stack.extend((v, (*path, k)) for k, v in value.items() if k != "loc")
        elif isinstance(value, list):
            stack.extend((v, (*path, i)) for i, v in enumerate(value))


def _location(document: dict, path: str) -> SourceSpan | None:
    current: Any = document
    span = None
    for part in path.split("/")[1:]:
        if isinstance(current, dict) and isinstance(current.get("loc"), list):
            span = current["loc"]
        try:
            current = (
                current[int(part)]
                if isinstance(current, list)
                else current[part.replace("~1", "/").replace("~0", "~")]
            )
        except (KeyError, IndexError, ValueError, TypeError):
            break
    if (
        span is not None
        and len(span) == 5
        and all(type(n) is int for n in span)
        and 0 <= span[0] < len(document.get("files", []))
    ):
        return SourceSpan(document["files"][span[0]], *span[1:])
    return None


def decode_document(
    document: Any, options: dict[str, str] | None = None
) -> GatheredSpec:
    """Check structure before constructing ASTs, then validate their context."""
    try:
        _walk(document)
        if type(document) is dict:
            if (
                isinstance(document.get("files"), list)
                and len(document["files"]) > MAX_FILES
            ):
                raise TransferError("too_many_files", "/files")
            version = document.get("version")
            if isinstance(version, str) and version not in SUPPORTED_VERSIONS:
                raise TransferError("unsupported_format_version", "/version")
        _shape(document, _SCHEMA)
        _extras(document)
        errors = check_names_and_references(document["spec"])
        if options is not None:
            errors += check_selection(document["spec"], options)
        if errors:
            raise errors[0]
        if (
            len(
                json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()
            )
            > MAX_BYTES
        ):
            raise TransferError("document_too_large")
        decoder = Decoder(document)
        gathered = decoder.gathered()
        ContextChecker(decoder).spec(document, gathered)
    except TransferError as exc:
        if type(document) is dict:
            exc.location = _location(document, exc.path)
        raise
    return gathered


def validate_document(document: Any, options: dict[str, str] | None = None) -> dict:
    decode_document(document, options)
    return cast(dict, document)
