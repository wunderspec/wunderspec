# Wunderspec transfer format

`wunderspec.transfer` provides format **1.1**, a declarative UTF-8 JSON encoding
of gathered Wunderspec ASTs. The [bundled JSON Schema](../../wunderspec/transfer/schema/wunderspec-transfer-1.json)
is shared with the translation service. No Python class names, imports, or
executable payloads appear in the document.

The envelope has `format: "wunderspec-transfer"`, `version: "1.1"`, a
`producer` name/version, a `files` array, and a `spec`. The specification contains
machine parameters, state variables, definitions, extracted actions, instances, pure
helper names, and operator comments. All 53 AST node kinds and all 12 sort kinds are
represented.

An instance lists every machine parameter in `params` as a `[name, node]` pair: the
expression the instance factory built, such as a `lit` node for `N=6`, a `setEnum` for
`CORRECT=Set(0, 1, 2)`, or an `interval` for `ROUNDS=Set(Val(1), ..., Val(3))`. A value
must be closed: its variables are bound inside it (by a lambda,
quantifier, or helper call), it contains no actions or temporal formulas, and it has the
parameter's sort. The exporter skips, with a warning naming the reason, an instance whose
value breaks these rules; values built with the Python API never do. Format 1.0, which
held literal parameters only, is no longer read or written.

Nodes and composite sorts are single-key constructor objects such as
`{"lit": {"value": {"int": {"n": 42}}}}` or `{"set": {"elem": "int"}}`.
Scalar sorts use `int`, `bool`, `str`, `action`, and `temporal`. Records and
unions retain their nominal names. Enumerations are data; importing reconstructs
an Enum without importing its original module.

## Python API

<!-- name: test_transfer_api -->
```python
import json
from wunderspec.transfer import export_spec, read_document, import_spec
from wunderspec.transfer.encode import document_json

document = export_spec("examples/sloppy_counter.py")
assert document["format"] == "wunderspec-transfer"
assert document["version"] == "1.1"
parsed = read_document(json.dumps(document).encode("utf-8"))
gathered = import_spec(parsed)
assert document_json(gathered) == document
```

`export_spec(path, *, init="init", step="step")` evaluates Python locally and
checks the selected operators. `write_document(document, path_or_text_stream)`
validates and writes stable, pretty JSON. `read_document(bytes)` strictly parses
and validates a document. `import_spec(document)` validates again and constructs
public AST objects, returning `GatheredSpec`. `TransferError.problem` is a
stable problem code; `.path` is an RFC 6901 JSON Pointer, and `.location` is a
Python `SourceSpan` when available. The [AST API](../../AST-API.md) documents the
gathered fields used by translator integrations.

## Locations and validation

A node's optional `loc` is `[file_index, line, column, end_line, end_column]`.
Lines start at one; columns start at zero. File names are relative to the root
specification's directory. Library spans outside that directory are omitted.
Imported locations retain the relative file names. Source tracking is available
on CPython; GraalPy exports omit locations.

Validation rejects unknown fields and constructors, missing fields, duplicate
JSON keys, non-integer numbers (including exponents, NaN and Infinity), a BOM,
invalid UTF-8, unbound variables, inconsistent nominal sorts, and sort mismatches.
Identifiers use ASCII `[A-Za-z_][A-Za-z0-9_]{0,127}`. File paths have no absolute
prefixes, drive letters, backslashes, empty segments, `.` or `..`. Comments may
contain newline and tab but no other control characters.

| Limit | Maximum |
|---|---|
| Submitted document | 5 MiB |
| JSON container depth | 256 |
| Tagged objects | 100,000 |
| String length | 4,096 characters |
| Integer literal | 78 decimal digits |
| Files | 64 relative paths, each at most 255 characters |

The schema captures the structural rules; the importer also checks scope,
nominal names, source locations, and sort consistency. It never evaluates code.
`wunderspec.ast.serialization`, `save_ast`, and `load_ast` were removed in
**0.138.0**. JSON is the only supported AST transfer format. Existing pickle
files cannot be imported; export a new JSON document from the Python specification.
