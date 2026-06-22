"""Quint-to-Wunderspec conversion.

This module intentionally consumes Quint's JSON IR instead of parsing Quint
source directly.  The first implementation is conservative: it emits real
Wunderspec code for constructs it understands and leaves explicit diagnostics
and placeholders for unsupported Quint IR nodes.
"""

from __future__ import annotations

import ast
import json
import keyword
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from wadler_lindig import AbstractDoc, BreakDoc, GroupDoc, NestDoc, TextDoc

from wunderspec.doc_format import HardLine, render_doc


class Reporter(Protocol):
    def info(self, msg: str) -> None: ...

    def success(self, msg: str) -> None: ...

    def warn(self, msg: str) -> None: ...

    def out(self, msg: str = "") -> None: ...


class QuintConvertError(Exception):
    """Raised for user-facing Quint conversion failures."""


@dataclass(frozen=True)
class SourceLocation:
    path: Path
    line: int
    column: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}:{self.column}"


@dataclass(frozen=True)
class ConversionDiagnostic:
    source: Path
    module: str
    definition: str
    node_id: object
    kind: object
    opcode: object
    message: str
    location: SourceLocation | None = None

    def __str__(self) -> str:
        op_text = f", opcode={self.opcode}" if self.opcode is not None else ""
        source = str(self.location) if self.location is not None else str(self.source)
        return (
            f"{source}: unsupported Quint construct in module {self.module}, "
            f"definition {self.definition}, node {self.node_id} "
            f"(kind={self.kind}{op_text}): {self.message}"
        )

    def comment(self) -> str:
        op_text = f", opcode={self.opcode}" if self.opcode is not None else ""
        loc_text = f" at {self.location}" if self.location is not None else ""
        return (
            f"TODO(wunderspec-quint): unsupported Quint construct{loc_text} in "
            f"definition {self.definition}, node {self.node_id} "
            f"(kind={self.kind}{op_text}): {self.message}"
        )


@dataclass(frozen=True)
class QuintConvertOptions:
    source: Path
    output: Path
    main: str | None = None
    quint: str = "quint"
    run_seed: int = 0
    run_samples: int = 1
    text_width: int = 80
    text_indent: int = 2


@dataclass(frozen=True)
class QuintConvertResult:
    output_module: str
    state_class_name: str
    definition_names: list[str]
    artifacts: list[str]
    error_count: int = 0
    diagnostics: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _MatchBranch:
    py_tag: str
    body: dict[str, Any]
    payload_type: Any
    param: str | None


@dataclass(frozen=True)
class _QuintInstance:
    proto_name: str
    overrides: dict[str, dict[str, Any]]


def convert_quint(
    options: QuintConvertOptions, reporter: Reporter
) -> QuintConvertResult:
    """Convert a Quint file to a Wunderspec Python module."""

    driver = _QuintDriver(options.quint, options.source)
    source_map = driver.source_map()
    reporter.info(f"Running Quint typecheck: {options.source}")
    typed = driver.typecheck()
    main = options.main or _select_main_module(typed, options.source)
    reporter.info(f"Using Quint module: {main}")

    try:
        reporter.info("Running Quint compile --target=json")
        ir = driver.compile(main=main, flatten=True)
    except QuintConvertError as flattened_error:
        reporter.warn(
            "Flattened Quint compile failed; retrying without flattening: "
            f"{flattened_error}"
        )
        try:
            ir = driver.compile(main=main, flatten=False)
        except QuintConvertError:
            reporter.warn("Non-flattened compile failed; using typecheck JSON IR")
            ir = typed

    generator = _PythonGenerator(
        ir,
        source=driver.source,
        source_locations=source_map,
        main=main,
        options=options,
    )
    output = generator.render()

    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(output)
    if generator.diagnostics:
        reporter.warn(
            f"Converted with {len(generator.diagnostics)} Quint conversion errors"
        )
        for diagnostic in generator.diagnostics:
            reporter.warn(str(diagnostic))
    reporter.success(f"Wrote Wunderspec module to: {options.output}")
    return QuintConvertResult(
        output_module=options.output.stem,
        state_class_name=generator.state_class_name,
        definition_names=generator.definition_names,
        artifacts=[options.output.name],
        error_count=len(generator.diagnostics),
        diagnostics=[str(d) for d in generator.diagnostics],
    )


class _QuintDriver:
    def __init__(self, quint: str, source: Path) -> None:
        self.quint = _resolve_quint_executable(quint)
        self.source = source.resolve()
        self.cwd = self.source.parent
        self.source_arg = self.source.name

    def source_map(self) -> dict[str, SourceLocation]:
        with tempfile.NamedTemporaryFile(suffix=".map", delete=True) as map_tmp:
            try:
                self._run_json(
                    [
                        "parse",
                        "--source-map",
                        map_tmp.name,
                        "--out",
                        "{out}",
                        self.source_arg,
                    ]
                )
                payload = json.loads(Path(map_tmp.name).read_text())
            except Exception:
                return {}
        return _parse_source_locations(payload)

    def typecheck(self) -> dict[str, Any]:
        return self._run_json(["typecheck", "--out", "{out}", self.source_arg])

    def compile(self, *, main: str, flatten: bool) -> dict[str, Any]:
        cmd = [
            "compile",
            "--target=json",
            f"--main={main}",
            "--out",
            "{out}",
        ]
        if not flatten:
            cmd.insert(2, "--flatten=false")
        cmd.append(self.source_arg)
        return self._run_json(cmd)

    def _run_json(self, args: list[str]) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=True) as tmp:
            cmd = [self.quint] + [tmp.name if a == "{out}" else a for a in args]
            proc = subprocess.run(
                cmd,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if proc.returncode != 0:
                msg = (proc.stderr or proc.stdout).strip()
                if not msg:
                    try:
                        payload = json.loads(Path(tmp.name).read_text())
                        errors = payload.get("errors")
                        if errors:
                            msg = json.dumps(errors, indent=2)
                    except Exception:
                        pass
                raise QuintConvertError(
                    msg or f"Quint command failed in {self.cwd}: {' '.join(cmd)}"
                )
            try:
                return cast(dict[str, Any], json.loads(Path(tmp.name).read_text()))
            except json.JSONDecodeError as e:
                raise QuintConvertError(
                    f"Quint produced invalid JSON for {' '.join(cmd)}: {e}"
                ) from e


def _resolve_quint_executable(quint: str) -> str:
    if "/" not in quint and "\\" not in quint:
        return quint
    return str(Path(quint).expanduser().resolve())


def _select_main_module(ir: dict[str, Any], source: Path) -> str:
    modules = list(ir.get("modules", []))
    if not modules:
        raise QuintConvertError("Quint JSON contains no modules")

    source_stem = source.stem
    for module in modules:
        if module.get("name") == source_stem:
            return source_stem
    if len(modules) == 1:
        return str(modules[0].get("name"))

    instance_candidates: list[str] = []
    for module in modules:
        decls = module.get("declarations", [])
        if any(
            isinstance(d, dict) and d.get("kind") == "instance" and not d.get("hidden")
            for d in decls
        ):
            instance_candidates.append(str(module.get("name")))

    unique_instances = sorted(set(instance_candidates))
    if len(unique_instances) == 1:
        return unique_instances[0]
    if len(unique_instances) > 1:
        raise QuintConvertError(
            "Could not infer --main module; pass --main. Candidates: "
            + ", ".join(unique_instances)
        )

    candidates: list[str] = []
    for module in modules:
        decls = module.get("declarations", [])
        if any(
            d.get("kind") in {"var", "const"}
            or (d.get("kind") == "def" and d.get("qualifier") in {"action", "run"})
            for d in decls
        ):
            candidates.append(str(module.get("name")))

    unique = sorted(set(candidates))
    if len(unique) == 1:
        return unique[0]
    if not unique:
        names = ", ".join(str(m.get("name")) for m in modules)
        raise QuintConvertError(f"Could not infer --main module. Candidates: {names}")
    raise QuintConvertError(
        "Could not infer --main module; pass --main. Candidates: " + ", ".join(unique)
    )


def _parse_source_locations(payload: Any) -> dict[str, SourceLocation]:
    if not isinstance(payload, dict):
        return {}
    source_index = payload.get("sourceIndex")
    source_map = payload.get("map")
    if not isinstance(source_index, dict) or not isinstance(source_map, dict):
        return {}

    locations: dict[str, SourceLocation] = {}
    for node_id, entry in source_map.items():
        if (
            not isinstance(entry, list)
            or len(entry) < 2
            or not isinstance(entry[1], dict)
        ):
            continue
        source_id = str(entry[0])
        path = source_index.get(source_id)
        if not isinstance(path, str):
            continue
        start = entry[1]
        line = start.get("line")
        col = start.get("col")
        if not isinstance(line, int) or not isinstance(col, int):
            continue
        locations[str(node_id)] = SourceLocation(
            path=Path(path),
            line=line + 1,
            column=col + 1,
        )
    return locations


_RESERVED = set(keyword.kwlist) | {
    "None",
    "True",
    "False",
    "match",
    "case",
}


def _py_name(name: str) -> str:
    name = name.replace("::", "__")
    name = re.sub(r"\W", "_", name)
    if not name:
        name = "_"
    if name[0].isdigit():
        name = "_" + name
    if name in _RESERVED:
        name += "_"
    return name


def _class_name(name: str) -> str:
    parts = re.split(r"[^0-9A-Za-z]+", name)
    result = "".join(p[:1].upper() + p[1:] for p in parts if p)
    if not result:
        result = "Spec"
    if result[0].isdigit():
        result = "Q" + result
    return result


class _PythonGenerator:
    def __init__(
        self,
        ir: dict[str, Any],
        *,
        source: Path,
        source_locations: dict[str, SourceLocation],
        main: str,
        options: QuintConvertOptions,
    ) -> None:
        self.ir = ir
        self.source = source
        self.source_locations = source_locations
        self.main = main
        self.options = options
        self.module = self._find_module(main)
        self.module_name = str(self.module.get("name"))
        self.state_class_name = f"{_class_name(self.module_name)}State"
        self.definition_names: list[str] = []
        self._current_state_ref = "s"
        main_decls = [
            d
            for d in self.module.get("declarations", [])
            if isinstance(d, dict) and not d.get("hidden") and "importedFrom" not in d
        ]
        self._all_decls = self._collect_declarations()
        self._instance = self._select_direct_instance(main_decls)
        self._decls = self._effective_decls(main_decls)
        self._main_decl_ids = {id(d) for d in self._decls}
        self._raw_type_aliases = {
            str(d.get("name")): d.get("type")
            for d in self._all_decls
            if d.get("kind") == "typedef"
            and isinstance(d.get("type"), dict)
            and not d.get("params")
            and d.get("type", {}).get("kind") not in {"rec", "sum"}
        }
        self._anonymous_union_names: dict[str, str] = {}
        self._anonymous_union_types: dict[str, Any] = {}
        self._predeclare_anonymous_unions()
        self._type_aliases = {
            str(d.get("name")): d.get("type")
            for d in self._all_decls
            if d.get("kind") == "typedef"
            and isinstance(d.get("type"), dict)
            and not d.get("params")
            and d.get("type", {}).get("kind") not in {"rec", "sum"}
        }
        self._named_types = {
            self._type_key(d.get("type")): _class_name(str(d.get("name")))
            for d in self._all_decls
            if d.get("kind") == "typedef"
            and isinstance(d.get("type"), dict)
            and not d.get("params")
            and d.get("type", {}).get("kind") in {"rec", "sum"}
        }
        self._named_types.update(self._anonymous_union_names)
        self._typedef_class_names = {
            str(d.get("name")): _class_name(str(d.get("name")))
            for d in self._all_decls
            if d.get("kind") == "typedef"
            and isinstance(d.get("type"), dict)
            and not d.get("params")
            and d.get("type", {}).get("kind") in {"rec", "sum"}
        }
        self._record_type_names_by_fields = {
            tuple(
                sorted(
                    field_name
                    for field_name, _ in _row_fields(
                        d.get("type", {}).get("fields", {})
                    )
                )
            ): _class_name(str(d.get("name")))
            for d in self._all_decls
            if d.get("kind") == "typedef"
            and isinstance(d.get("type"), dict)
            and not d.get("params")
            and d.get("type", {}).get("kind") == "rec"
        }
        self._union_ctors_by_name: dict[str, list[tuple[str, str, Any]]] = {}
        for decl in self._all_decls:
            if decl.get("kind") != "typedef":
                continue
            if decl.get("params"):
                continue
            typ = decl.get("type")
            if not isinstance(typ, dict) or typ.get("kind") != "sum":
                continue
            type_name = _class_name(str(decl.get("name")))
            for field_name, field_type in _row_fields(typ.get("fields", {})):
                self._union_ctors_by_name.setdefault(field_name, []).append(
                    (
                        type_name,
                        _py_name(field_name),
                        self._variant_payload_type(field_type),
                    )
                )
        for key, type_name in self._anonymous_union_names.items():
            typ = self._anonymous_union_types[key]
            for field_name, field_type in _row_fields(typ.get("fields", {})):
                self._union_ctors_by_name.setdefault(field_name, []).append(
                    (
                        type_name,
                        _py_name(field_name),
                        self._variant_payload_type(field_type),
                    )
                )
        self._union_ctor_names = set(self._union_ctors_by_name)
        self._decl_by_name = {
            str(d.get("name")): d for d in self._all_decls if "name" in d
        }
        self.diagnostics: list[ConversionDiagnostic] = []
        self._pending_comments: list[str] = []
        self._vars = [str(d["name"]) for d in self._decls if d.get("kind") == "var"]
        self._consts = [str(d["name"]) for d in self._decls if d.get("kind") == "const"]
        self._action_names = {
            str(d["name"])
            for d in self._decls
            if d.get("kind") == "def" and d.get("qualifier") == "action"
        }
        self._run_names = {
            str(d["name"])
            for d in self._decls
            if d.get("kind") == "def" and d.get("qualifier") == "run"
        }
        self._helper_names = self._collect_helper_names()
        self._expr_names = {
            str(d["name"])
            for d in self._all_decls
            if d.get("kind") == "def"
            and d.get("qualifier") not in {"action", "run", "nondet"}
            and str(d["name"]) not in self._union_ctor_names
            and (d in self._decls or str(d["name"]) in self._helper_names)
        }
        self._local_expr_name_stack: list[set[str]] = []
        self._local_action_name_stack: list[set[str]] = []
        self._local_type_stack: list[dict[str, Any]] = []

    def _find_module(self, name: str) -> dict[str, Any]:
        for module in self.ir.get("modules", []):
            if module.get("name") == name:
                return cast(dict[str, Any], module)
        names = ", ".join(str(m.get("name")) for m in self.ir.get("modules", []))
        raise QuintConvertError(f"Module '{name}' not found in Quint JSON: {names}")

    def _collect_declarations(self) -> list[dict[str, Any]]:
        decls: list[dict[str, Any]] = []
        seen: set[tuple[object, object, object]] = set()
        for module in self.ir.get("modules", []):
            module_name = module.get("name")
            for decl in module.get("declarations", []):
                if not isinstance(decl, dict):
                    continue
                key = (module_name, decl.get("kind"), decl.get("name", decl.get("id")))
                if key in seen:
                    continue
                seen.add(key)
                decls.append(decl)
        return decls

    def _select_direct_instance(
        self, main_decls: list[dict[str, Any]]
    ) -> _QuintInstance | None:
        instances = [
            d
            for d in main_decls
            if d.get("kind") == "instance"
            and d.get("identityOverride") is True
            and isinstance(d.get("protoName"), str)
        ]
        if len(instances) != 1:
            return None

        overrides: dict[str, dict[str, Any]] = {}
        for entry in instances[0].get("overrides", []):
            if not isinstance(entry, list) or len(entry) != 2:
                continue
            target, value = entry
            if (
                not isinstance(target, dict)
                or not isinstance(value, dict)
                or not isinstance(target.get("name"), str)
            ):
                continue
            overrides[str(target["name"])] = value
        return _QuintInstance(
            proto_name=str(instances[0]["protoName"]), overrides=overrides
        )

    def _effective_decls(
        self, main_decls: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if self._has_state_decls(main_decls):
            return main_decls

        if self._instance is not None:
            module = self._find_module(self._instance.proto_name)
            return (
                self._machine_decls(
                    [d for d in module.get("declarations", []) if isinstance(d, dict)]
                )
                + main_decls
            )

        candidates: list[list[dict[str, Any]]] = []
        imported_groups: dict[str, list[dict[str, Any]]] = {}
        for decl in self.module.get("declarations", []):
            if not isinstance(decl, dict):
                continue
            imported_from = decl.get("importedFrom")
            if not isinstance(imported_from, dict):
                continue
            key = str(imported_from.get("protoName") or imported_from.get("fromSource"))
            imported_groups.setdefault(key, []).append(decl)
        for decls in imported_groups.values():
            if self._has_state_decls(decls):
                candidates.append(decls)

        for module in self.ir.get("modules", []):
            if module.get("name") == self.module_name:
                continue
            decls = [d for d in module.get("declarations", []) if isinstance(d, dict)]
            if self._has_state_decls(decls):
                candidates.append(decls)
        if len(candidates) != 1:
            return main_decls

        return self._machine_decls(candidates[0]) + main_decls

    def _machine_decls(self, decls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            d
            for d in decls
            if d.get("kind") in {"const", "var"}
            or (d.get("kind") == "def" and d.get("qualifier") in {"action", "run"})
        ]

    def _has_state_decls(self, decls: list[dict[str, Any]]) -> bool:
        return any(d.get("kind") in {"const", "var"} for d in decls)

    def _type_key(self, typ: Any) -> str:
        return _type_key(self._expand_type_aliases(typ))

    def _expand_type_aliases(self, typ: Any) -> Any:
        if isinstance(typ, list):
            return [self._expand_type_aliases(item) for item in typ]
        if not isinstance(typ, dict):
            return typ
        if typ.get("kind") == "const":
            alias = self._raw_type_aliases.get(str(typ.get("name")))
            if isinstance(alias, dict):
                return self._expand_type_aliases(alias)
        return {
            key: self._expand_type_aliases(value) if key != "id" else value
            for key, value in typ.items()
        }

    def _declared_type(self, typ: Any) -> Any:
        if not isinstance(typ, dict):
            return typ
        if typ.get("kind") == "const":
            decl = self._decl_by_name.get(str(typ.get("name")))
            if decl is not None and decl.get("kind") == "typedef":
                declared = decl.get("type")
                if isinstance(declared, dict):
                    return declared
        return typ

    def _type_hint(self, node_id: object, expected_type: Any | None = None) -> Any:
        if isinstance(expected_type, dict) and not self._type_contains_var(
            expected_type
        ):
            return self._declared_type(expected_type)
        typ = self._type_from_table(node_id)
        if not isinstance(typ, dict) or self._type_contains_var(typ):
            typ = expected_type
        return self._declared_type(typ)

    def _decl_result_type(self, decl: dict[str, Any]) -> Any:
        typ = decl.get("typeAnnotation") or self._type_from_table(decl.get("id"))
        typ = self._declared_type(typ)
        if isinstance(typ, dict) and typ.get("kind") == "oper":
            return self._declared_type(typ.get("res"))
        return typ

    def _opdef_result_type(self, opdef: dict[str, Any]) -> Any:
        typ = self._decl_result_type(opdef)
        expr = opdef.get("expr")
        expr_type = self._expr_value_type(expr) if isinstance(expr, dict) else None
        if isinstance(expr_type, dict) and not self._type_contains_var(expr_type):
            if (
                not isinstance(typ, dict)
                or typ.get("kind")
                in {
                    "int",
                    "bool",
                    "str",
                    "var",
                }
                or self._type_contains_var(typ)
            ):
                return expr_type
        if isinstance(typ, dict) and not self._type_contains_var(typ):
            return typ
        if isinstance(expr_type, dict):
            return expr_type
        return typ

    def _local_type(self, name: str) -> Any:
        py_name = _py_name(name)
        for scope in reversed(self._local_type_stack):
            if py_name in scope:
                return scope[py_name]
        return None

    def _expr_value_type(self, expr: dict[str, Any]) -> Any:
        if expr.get("kind") == "name":
            local_type = self._local_type(str(expr.get("name")))
            if local_type is not None:
                return local_type
        if expr.get("kind") == "app":
            decl = self._decl_by_name.get(str(expr.get("opcode")))
            if decl is not None and decl.get("kind") == "def":
                decl_type = self._decl_result_type(decl)
                node_type = self._type_hint(expr.get("id"))
                if isinstance(node_type, dict) and not self._type_contains_var(
                    node_type
                ):
                    if (
                        not isinstance(decl_type, dict)
                        or decl_type.get("kind")
                        in {
                            "int",
                            "bool",
                            "str",
                            "var",
                        }
                        or self._type_contains_var(decl_type)
                    ):
                        return node_type
                return decl_type
        return self._type_hint(expr.get("id"))

    def _predeclare_anonymous_unions(self) -> None:
        named_sum_keys = {
            self._type_key(decl.get("type"))
            for decl in self._all_decls
            if decl.get("kind") == "typedef"
            and not decl.get("params")
            and isinstance(decl.get("type"), dict)
            and decl.get("type", {}).get("kind") == "sum"
        }

        def consider(typ: Any) -> None:
            if isinstance(typ, list):
                for item in typ:
                    consider(item)
                return
            if not isinstance(typ, dict):
                return
            if typ.get("kind") == "sum":
                key = self._type_key(typ)
                if (
                    key not in named_sum_keys
                    and key not in self._anonymous_union_names
                    and not self._type_contains_var(typ)
                ):
                    self._anonymous_union_names[key] = (
                        f"_QuintUnion{len(self._anonymous_union_names) + 1}"
                    )
                    self._anonymous_union_types[key] = typ
            for child in typ.values():
                consider(child)

        for entry in self.ir.get("types", {}).values():
            if isinstance(entry, dict):
                consider(entry.get("type"))
        for decl in self._all_decls:
            consider(decl.get("type"))
            consider(decl.get("typeAnnotation"))

    def _collect_helper_names(self) -> set[str]:
        direct_ops = {"empty", "range", "sortList", "Some", "None"}
        main_names = {str(d.get("name")) for d in self._decls if "name" in d}
        helpers: set[str] = set()
        pending: list[str] = []

        def consider(name: str) -> None:
            if name in direct_ops or name in main_names or name in helpers:
                return
            decl = self._decl_by_name.get(name)
            if decl is None or decl.get("kind") != "def":
                return
            if decl.get("qualifier") in {"action", "run", "nondet"}:
                return
            if decl.get("params"):
                return
            helpers.add(name)
            pending.append(name)

        for decl in self._decls:
            for name in self._referenced_names(decl.get("expr")):
                consider(name)

        while pending:
            helper_decl = self._decl_by_name.get(pending.pop())
            if helper_decl is None:
                continue
            for name in self._referenced_names(helper_decl.get("expr")):
                consider(name)
        return helpers

    def _helper_decls(self) -> list[dict[str, Any]]:
        decls: list[dict[str, Any]] = []
        seen: set[str] = set()
        for decl in self._all_decls:
            name = str(decl.get("name"))
            if name in self._helper_names and name not in seen:
                decls.append(decl)
                seen.add(name)
        return decls

    def _referenced_names(self, expr: Any) -> set[str]:
        names: set[str] = set()

        def walk(value: Any) -> None:
            if isinstance(value, list):
                for item in value:
                    walk(item)
                return
            if not isinstance(value, dict):
                return
            kind = value.get("kind")
            if kind == "name":
                name = value.get("name")
                if isinstance(name, str):
                    names.add(name)
            elif kind == "app":
                opcode = value.get("opcode")
                if isinstance(opcode, str):
                    names.add(opcode)
            for child in value.values():
                walk(child)

        walk(expr)
        return names

    def render(self) -> str:
        chunks = [
            '"""Generated from Quint by `wunderspec convert`."""',
            "",
            "from __future__ import annotations",
            "",
            "import os",
            "import random",
            "import unittest",
            "",
            "from wunderspec import *",
            "from wunderspec.machine import MachineStateBase",
            "",
            "",
        ]
        chunks.extend(self._render_prelude_helpers())
        chunks.extend(self._render_types())
        chunks.append(self._render_state())
        chunks.append(self._render_instance())
        chunks.extend(self._render_defs())
        chunks.append(self._render_run_tests())
        source = "\n".join(chunks).rstrip() + "\n"
        source = _hoist_nested_python_lambdas(source)
        return _format_python_source(
            source,
            text_width=self.options.text_width,
            text_indent=self.options.text_indent,
        )

    def _render_prelude_helpers(self) -> list[str]:
        return [
            "def _quint_sort_list(list_expr, lt):",
            "    elem_sort = list_expr.sort.elem_sort",
            "",
            "    def insert_in_order(sorted_list, item):",
            "        insertion = sorted_list.reduce(",
            "            lambda acc, current: Ite(",
            "                acc[1],",
            "                Tuple(acc[0] + List(current), Val(True)),",
            "                Ite(",
            "                    lt(current, item),",
            "                    Tuple(acc[0] + List(current), Val(False)),",
            "                    Tuple(acc[0] + List(item) + List(current), Val(True)),",
            "                ),",
            "            ),",
            "            Tuple(List(elem_sort), Val(False)),",
            "        )",
            "        return Ite(insertion[1], insertion[0], insertion[0] + List(item))",
            "",
            "    return list_expr.reduce(lambda sorted_list, item: insert_in_order(sorted_list, item), List(elem_sort))",
            "",
            "",
        ]

    def _render_types(self) -> list[str]:
        chunks: list[str] = []
        emitted: set[str] = set()

        def emit_type(name: str, typ: Any) -> None:
            if name in emitted:
                return
            emitted.add(name)
            if typ.get("kind") == "rec":
                chunks.append("@record")
                chunks.append(f"class {name}:")
                fields = _row_fields(typ.get("fields", {}))
                if fields:
                    for field_name, field_type in fields:
                        chunks.append(
                            f"    {_py_name(field_name)}: Field[{self._type_expr(field_type)}]"
                        )
                else:
                    chunks.append("    pass")
                chunks.append("")
            elif typ.get("kind") == "sum":
                chunks.append("@union")
                chunks.append(f"class {name}:")
                fields = _row_fields(typ.get("fields", {}))
                if fields:
                    for field_name, field_type in fields:
                        type_expr = self._type_expr(field_type)
                        if type_expr == "()":
                            type_expr = "Unit"
                        chunks.append(
                            f"    {_py_name(field_name)}: Variant[{type_expr}]"
                        )
                else:
                    chunks.append("    pass")
                chunks.append("")
            else:
                chunks.append(f"{name} = {self._type_expr(typ)}")
                chunks.append("")

        typedefs = [
            decl
            for decl in self._all_decls
            if decl.get("kind") == "typedef" and not decl.get("params")
        ]

        type_blocks = [
            (_class_name(str(decl.get("name"))), decl.get("type", {}))
            for decl in typedefs
            if isinstance(decl.get("type"), dict)
            and decl.get("type", {}).get("kind") in {"rec", "sum"}
        ]
        type_blocks.extend(
            (name, self._anonymous_union_types[key])
            for key, name in self._anonymous_union_names.items()
        )
        for name, typ in self._ordered_type_blocks(type_blocks):
            emit_type(name, typ)

        for decl in typedefs:
            typ = decl.get("type", {})
            if isinstance(typ, dict) and typ.get("kind") not in {"rec", "sum"}:
                emit_type(_class_name(str(decl.get("name"))), typ)
        return chunks

    def _ordered_type_blocks(
        self, blocks: list[tuple[str, Any]]
    ) -> list[tuple[str, Any]]:
        by_name: dict[str, Any] = {}
        for name, typ in blocks:
            by_name.setdefault(name, typ)

        ordered: list[tuple[str, Any]] = []
        emitted: set[str] = set()
        remaining = dict(by_name)
        while remaining:
            progressed = False
            for name, typ in list(remaining.items()):
                deps = self._type_dependencies(typ, name, set(by_name))
                if deps <= emitted:
                    ordered.append((name, typ))
                    emitted.add(name)
                    del remaining[name]
                    progressed = True
            if not progressed:
                ordered.extend(remaining.items())
                break
        return ordered

    def _type_dependencies(
        self, typ: Any, current: str, known_type_names: set[str]
    ) -> set[str]:
        deps: set[str] = set()

        def walk(typ: Any) -> None:
            if isinstance(typ, list):
                for item in typ:
                    walk(item)
                return
            if not isinstance(typ, dict):
                return
            if typ.get("kind") == "const":
                dep = self._typedef_class_names.get(str(typ.get("name")))
                if dep in known_type_names and dep != current:
                    deps.add(dep)
            named = self._named_types.get(self._type_key(typ))
            if named in known_type_names and named != current:
                deps.add(named)
            for child in typ.values():
                walk(child)

        walk(typ)
        return deps

    def _type_uses_anonymous_union(self, typ: Any) -> bool:
        if isinstance(typ, list):
            return any(self._type_uses_anonymous_union(item) for item in typ)
        if not isinstance(typ, dict):
            return False
        if (
            typ.get("kind") == "sum"
            and self._type_key(typ) in self._anonymous_union_names
        ):
            return True
        return any(self._type_uses_anonymous_union(child) for child in typ.values())

    def _type_contains_var(self, typ: Any) -> bool:
        if isinstance(typ, list):
            return any(self._type_contains_var(item) for item in typ)
        if not isinstance(typ, dict):
            return False
        if typ.get("kind") == "var":
            return True
        return any(self._type_contains_var(child) for child in typ.values())

    def _render_state(self) -> str:
        lines = [
            "@state",
            f"class {self.state_class_name}(MachineStateBase):",
        ]
        fields = [
            (decl, "Param") for decl in self._decls if decl.get("kind") == "const"
        ] + [(decl, "StateVar") for decl in self._decls if decl.get("kind") == "var"]
        if not fields:
            lines.append("    pass")
        else:
            for decl, wrapper in fields:
                typ = decl.get("typeAnnotation") or self._type_from_table(
                    decl.get("id")
                )
                lines.append(
                    f"    {_py_name(str(decl['name']))}: {wrapper}[{self._type_expr(typ)}]"
                )
        lines.append("")
        return "\n".join(lines)

    def _render_instance(self) -> str:
        if self._instance is None:
            return ""

        const_decls = [d for d in self._decls if d.get("kind") == "const"]
        args: list[str] = []
        for decl in const_decls:
            name = str(decl.get("name"))
            value = self._instance.overrides.get(name)
            if value is None:
                continue
            typ = decl.get("typeAnnotation") or self._type_from_table(decl.get("id"))
            code = self._expr(
                value,
                env=set(),
                def_name=f"instance {self.module_name}",
                expected_type=typ,
            )
            args.append(f"{_py_name(name)}={code}")

        if not args:
            return ""

        lines = [
            "@instance",
            f"def {_py_name(self.module_name)}() -> {self.state_class_name}:",
            f"    return {self.state_class_name}(",
        ]
        lines.extend(f"        {arg}," for arg in args)
        lines.extend(["    )", ""])
        return "\n".join(lines)

    def _render_defs(self) -> list[str]:
        chunks: list[str] = []
        for decl in self._helper_decls():
            chunks.append(self._render_expr_def(decl, exported=False))
        for decl in self._decls:
            if decl.get("kind") != "def":
                continue
            qualifier = decl.get("qualifier")
            name = str(decl.get("name"))
            if name.startswith("q::") or qualifier == "nondet":
                continue
            if name in self._union_ctor_names:
                continue
            if qualifier == "action":
                chunks.append(self._render_action(decl))
            elif qualifier == "run":
                continue
            else:
                chunks.append(self._render_expr_def(decl))
        return chunks

    def _render_action(self, decl: dict[str, Any]) -> str:
        name = str(decl["name"])
        expr = decl["expr"]
        params = self._lambda_param_decls(expr)
        py_params = [_py_name(str(p.get("name"))) for p in params]
        sig_tail = "".join(
            f", {py}: {self._param_type_expr(param)}"
            for py, param in zip(py_params, params)
        )
        decorator = "@action(init=True)" if name == "init" else "@action(inline=False)"
        lines = [
            decorator,
            f"def {_py_name(name)}(c: Context[{self.state_class_name}]{sig_tail}):",
            "    s = c.state",
        ]
        body_expr = expr.get("expr") if expr.get("kind") == "lambda" else expr
        self._emit_action(
            body_expr, lines, indent="    ", env=set(py_params), def_name=name
        )
        if len(lines) == 3:
            lines.append("    pass")
        lines.append("")
        self.definition_names.append(name)
        return "\n".join(lines)

    def _render_expr_def(self, decl: dict[str, Any], *, exported: bool = True) -> str:
        name = str(decl["name"])
        expr = decl["expr"]
        params = self._lambda_params(expr)
        py_params = [_py_name(p) for p in params]
        sig_tail = "".join(f", {p}: Expr" for p in py_params)
        lines = [
            f"def {_py_name(name)}(_state: {self.state_class_name}{sig_tail}) -> Expr:",
        ]
        body_expr = expr.get("expr") if expr.get("kind") == "lambda" else expr
        old_state_ref = self._current_state_ref
        self._current_state_ref = "_state"
        try:
            self._emit_expr_return(
                body_expr,
                lines,
                indent="    ",
                env=set(py_params),
                def_name=name,
                expected_type=self._decl_result_type(decl),
            )
        finally:
            self._current_state_ref = old_state_ref
        lines.append("")
        if exported:
            self.definition_names.append(name)
        return "\n".join(lines)

    def _render_run_tests(self) -> str:
        runs = [
            d
            for d in self._decls
            if d.get("kind") == "def" and d.get("qualifier") == "run"
        ]
        if not runs:
            return ""
        lines = [
            f"class Test{_class_name(self.module_name)}Runs(unittest.TestCase):",
            "    def _state(self):",
        ]
        if self._consts:
            lines.append("        params = getattr(self, 'QUINT_PARAMS', None) or {}")
            for const in self._consts:
                py = _py_name(const)
                lines.append(f"        if {const!r} not in params:")
                lines.append(
                    f"            self.fail('missing Quint const for run test: {const}')"
                )
                lines.append(f"        {py} = params[{const!r}]")
            args = ", ".join(f"{_py_name(c)}={_py_name(c)}" for c in self._consts)
            lines.append(f"        return {self.state_class_name}({args})")
        else:
            lines.append(f"        return {self.state_class_name}()")
        lines.extend(
            [
                "",
                "    def _scheduler(self, sample_index: int, attempt: int = 0):",
                f"        seed = int(os.environ.get('WUNDERSPEC_QUINT_RUN_SEED', {self.options.run_seed!r}))",
                "        return RandomScheduler(random.Random(seed + sample_index * 1000003 + attempt))",
                "",
                "    def _run_action(self, state, action, sample_index: int, *args):",
                "        retries = 100",
                "        attempt = 0",
                "        while retries > 0:",
                "            c = ExecContext(state, self._scheduler(sample_index, attempt))",
                "            try:",
                "                c.step(action, *args)",
                "                return c.state",
                "            except AssumptionViolated:",
                "                c.revert()",
                "                retries -= 1",
                "                attempt += 1",
                "        self.fail(f'action stayed disabled after retries: {action.__name__}')",
                "",
                "    def _expect_fail(self, fn):",
                "        try:",
                "            fn()",
                "        except (AssumptionViolated, AssertionError):",
                "            return",
                "        self.fail('expected Quint run fragment to fail')",
                "",
            ]
        )
        for decl in runs:
            name = str(decl["name"])
            py_name = _py_name(f"test_{name}")
            lines.append(f"    def {py_name}(self):")
            lines.append(
                f"        samples = int(os.environ.get('WUNDERSPEC_QUINT_RUN_SAMPLES', {self.options.run_samples!r}))"
            )
            lines.append("        for sample_index in range(samples):")
            lines.append("            s = self._state()")
            self._emit_run(
                decl["expr"],
                lines,
                indent="            ",
                env=set(),
                state_var="s",
                sample_expr="sample_index",
                def_name=name,
            )
            lines.append("")
            self.definition_names.append(name)
        return "\n".join(lines)

    def _emit_expr_return(
        self,
        expr: dict[str, Any],
        lines: list[str],
        *,
        indent: str,
        env: set[str],
        def_name: str,
        expected_type: Any | None = None,
    ) -> None:
        if expr.get("kind") == "let":
            opdef = expr.get("opdef", {})
            local = _py_name(str(opdef.get("name")))
            if opdef.get("qualifier") == "nondet":
                placeholder = self._unsupported(
                    expr, def_name, "nondet let is only supported in actions/runs"
                )
                self._emit_pending_comments(lines, indent)
                lines.append(f"{indent}return {placeholder}")
                return
            if self._is_local_function_def(opdef):
                if self._is_action_def(opdef):
                    placeholder = self._unsupported(
                        expr, def_name, "local action definition in expression position"
                    )
                    self._emit_pending_comments(lines, indent)
                    lines.append(f"{indent}return {placeholder}")
                    return
                self._emit_local_expr_def(
                    opdef, lines, indent=indent, env=env, def_name=def_name
                )
                self._local_expr_name_stack.append({local})
                try:
                    self._emit_expr_return(
                        self._child_expr(expr, "expr", def_name),
                        lines,
                        indent=indent,
                        env=env,
                        def_name=def_name,
                        expected_type=expected_type,
                    )
                finally:
                    self._local_expr_name_stack.pop()
                return
            value_code = self._expr(
                opdef.get("expr"),
                env=env,
                def_name=def_name,
                expected_type=self._opdef_result_type(opdef),
            )
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}{local} = {value_code}")
            self._local_type_stack.append({local: self._opdef_result_type(opdef)})
            try:
                self._emit_expr_return(
                    self._child_expr(expr, "expr", def_name),
                    lines,
                    indent=indent,
                    env=env | {local},
                    def_name=def_name,
                    expected_type=expected_type,
                )
            finally:
                self._local_type_stack.pop()
            return
        value_code = self._expr(
            expr, env=env, def_name=def_name, expected_type=expected_type
        )
        self._emit_pending_comments(lines, indent)
        lines.append(f"{indent}return {value_code}")

    def _emit_action(
        self,
        expr: dict[str, Any],
        lines: list[str],
        *,
        indent: str,
        env: set[str],
        def_name: str,
    ) -> None:
        kind = expr.get("kind")
        if kind == "name" and str(expr.get("name")) in self._action_names:
            lines.append(f"{indent}{_py_name(str(expr.get('name')))}(c)")
            return
        if kind == "let":
            opdef = expr.get("opdef", {})
            local = _py_name(str(opdef.get("name")))
            if opdef.get("qualifier") == "nondet":
                choices = self._one_of_arg(
                    opdef.get("expr"), env=env, def_name=def_name
                )
                self._emit_pending_comments(lines, indent)
                lines.append(f'{indent}with c.one_of({choices}, "{local}") as {local}:')
                self._emit_action(
                    self._child_expr(expr, "expr", def_name),
                    lines,
                    indent=indent + "    ",
                    env=env | {local},
                    def_name=def_name,
                )
            else:
                if self._is_local_function_def(opdef):
                    if self._is_action_def(opdef):
                        self._emit_local_action_def(
                            opdef, lines, indent=indent, env=env, def_name=def_name
                        )
                        self._local_action_name_stack.append({local})
                    else:
                        self._emit_local_expr_def(
                            opdef, lines, indent=indent, env=env, def_name=def_name
                        )
                        self._local_expr_name_stack.append({local})
                    try:
                        self._emit_action(
                            self._child_expr(expr, "expr", def_name),
                            lines,
                            indent=indent,
                            env=env,
                            def_name=def_name,
                        )
                    finally:
                        if self._is_action_def(opdef):
                            self._local_action_name_stack.pop()
                        else:
                            self._local_expr_name_stack.pop()
                    return
                value_code = self._expr(
                    opdef.get("expr"),
                    env=env,
                    def_name=def_name,
                    expected_type=self._opdef_result_type(opdef),
                )
                self._emit_pending_comments(lines, indent)
                lines.append(f"{indent}{local} = {value_code}")
                self._local_type_stack.append({local: self._opdef_result_type(opdef)})
                try:
                    self._emit_action(
                        self._child_expr(expr, "expr", def_name),
                        lines,
                        indent=indent,
                        env=env | {local},
                        def_name=def_name,
                    )
                finally:
                    self._local_type_stack.pop()
            return

        if kind != "app":
            cond = self._expr(expr, env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}c.assume({cond})")
            return

        op = expr.get("opcode")
        args = expr.get("args", [])
        if op == "actionAll":
            for arg in args:
                self._emit_action(arg, lines, indent=indent, env=env, def_name=def_name)
        elif op == "assign":
            target = self._assign_target(args[0], def_name)
            value_code = self._expr(
                args[1],
                env=env,
                def_name=def_name,
                expected_type=self._assignment_type(args[0]),
            )
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}{target} = {value_code}")
        elif op == "actionAny":
            labels = [self._alternative_label(arg, i) for i, arg in enumerate(args)]
            lines.append(
                f"{indent}alts = iter(c.alternatives({', '.join(repr(x) for x in labels)}))"
            )
            for arg in args:
                lines.append(f"{indent}with next(alts):")
                self._emit_action(
                    arg, lines, indent=indent + "    ", env=env, def_name=def_name
                )
        elif op == "ite":
            then_name = f"_then_{expr.get('id', 'q')}"
            else_name = f"_else_{expr.get('id', 'q')}"
            cond = self._expr(args[0], env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}({then_name}, {else_name}) = c.split({cond})")
            lines.append(f"{indent}with {then_name}:")
            self._emit_action(
                args[1], lines, indent=indent + "    ", env=env, def_name=def_name
            )
            lines.append(f"{indent}with {else_name}:")
            self._emit_action(
                args[2], lines, indent=indent + "    ", env=env, def_name=def_name
            )
        elif op == "matchVariant":
            self._emit_action_match_variant(
                expr, lines, indent=indent, env=env, def_name=def_name
            )
        elif op == "assert":
            cond = self._expr(args[0], env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}assert value({cond}) == BoolValue(True)")
        elif op == "require":
            cond = self._expr(args[0], env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}c.assume({cond})")
        elif self._is_local_action_name(str(op)):
            call_args = ", ".join(
                self._expr(a, env=env, def_name=def_name) for a in args
            )
            suffix = f", {call_args}" if call_args else ""
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}{_py_name(str(op))}(c{suffix})")
        elif op in self._action_names:
            call_args = ", ".join(
                self._expr(a, env=env, def_name=def_name) for a in args
            )
            suffix = f", {call_args}" if call_args else ""
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}{_py_name(str(op))}(c{suffix})")
        else:
            cond = self._expr(expr, env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}c.assume({cond})")

    def _emit_run(
        self,
        expr: dict[str, Any],
        lines: list[str],
        *,
        indent: str,
        env: set[str],
        state_var: str,
        sample_expr: str,
        def_name: str,
    ) -> None:
        if expr.get("kind") == "let":
            opdef = expr.get("opdef", {})
            local = _py_name(str(opdef.get("name")))
            if opdef.get("qualifier") == "nondet":
                lines.append(
                    f"{indent}c = ExecContext({state_var}, self._scheduler({sample_expr}))"
                )
                choices = self._one_of_arg(
                    opdef.get("expr"), env=env, def_name=def_name
                )
                self._emit_pending_comments(lines, indent)
                lines.append(f'{indent}with c.one_of({choices}, "{local}") as {local}:')
                self._emit_run(
                    self._child_expr(expr, "expr", def_name),
                    lines,
                    indent=indent + "    ",
                    env=env | {local},
                    state_var=state_var,
                    sample_expr=sample_expr,
                    def_name=def_name,
                )
                return
            if self._is_local_function_def(opdef):
                if self._is_action_def(opdef):
                    self._emit_local_action_def(
                        opdef, lines, indent=indent, env=env, def_name=def_name
                    )
                    self._local_action_name_stack.append({local})
                else:
                    self._emit_local_expr_def(
                        opdef, lines, indent=indent, env=env, def_name=def_name
                    )
                    self._local_expr_name_stack.append({local})
                try:
                    self._emit_run(
                        self._child_expr(expr, "expr", def_name),
                        lines,
                        indent=indent,
                        env=env,
                        state_var=state_var,
                        sample_expr=sample_expr,
                        def_name=def_name,
                    )
                finally:
                    if self._is_action_def(opdef):
                        self._local_action_name_stack.pop()
                    else:
                        self._local_expr_name_stack.pop()
                return
            value_code = self._expr(
                opdef.get("expr"),
                env=env,
                def_name=def_name,
                expected_type=self._opdef_result_type(opdef),
            )
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}{local} = {value_code}")
            self._emit_run(
                self._child_expr(expr, "expr", def_name),
                lines,
                indent=indent,
                env=env | {local},
                state_var=state_var,
                sample_expr=sample_expr,
                def_name=def_name,
            )
            return

        if expr.get("kind") == "name":
            name = str(expr.get("name"))
            if name in self._action_names:
                lines.append(
                    f"{indent}{state_var} = self._run_action({state_var}, {_py_name(name)}, {sample_expr})"
                )
                return
            if name in self._run_names:
                self._unsupported(
                    expr, def_name, f"run call '{name}' is not supported yet"
                )
                self._emit_pending_comments(lines, indent)
                lines.append(
                    f"{indent}self.fail({f'unsupported Quint run call: {name}'!r})"
                )
                return

        if expr.get("kind") != "app":
            cond = self._expr(expr, env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}self.assertEqual(value({cond}), BoolValue(True))")
            return

        op = expr.get("opcode")
        args = expr.get("args", [])
        if op == "then":
            self._emit_run(
                args[0],
                lines,
                indent=indent,
                env=env,
                state_var=state_var,
                sample_expr=sample_expr,
                def_name=def_name,
            )
            self._emit_run(
                args[1],
                lines,
                indent=indent,
                env=env,
                state_var=state_var,
                sample_expr=sample_expr,
                def_name=def_name,
            )
        elif op == "reps":
            count = self._expr(args[0], env=env, def_name=def_name)
            param = "_i"
            body = args[1]
            if body.get("kind") == "lambda" and body.get("params"):
                param = _py_name(str(body["params"][0]["name"]))
                body = body.get("expr")
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}for {param} in range(int(value({count}).value)):")
            self._emit_run(
                body,
                lines,
                indent=indent + "    ",
                env=env | {param},
                state_var=state_var,
                sample_expr=sample_expr,
                def_name=def_name,
            )
        elif op == "fail":
            fn = f"_run_fail_{expr.get('id', 'q')}"
            lines.append(f"{indent}def {fn}():")
            lines.append(f"{indent}    nonlocal {state_var}")
            self._emit_run(
                args[0],
                lines,
                indent=indent + "    ",
                env=env,
                state_var=state_var,
                sample_expr=sample_expr,
                def_name=def_name,
            )
            lines.append(f"{indent}self._expect_fail({fn})")
        elif op == "expect":
            self._emit_run(
                args[0],
                lines,
                indent=indent,
                env=env,
                state_var=state_var,
                sample_expr=sample_expr,
                def_name=def_name,
            )
            cond = self._expr(args[1], env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}self.assertEqual(value({cond}), BoolValue(True))")
        elif op == "assert":
            cond = self._expr(args[0], env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}self.assertEqual(value({cond}), BoolValue(True))")
        elif op == "assign":
            fn = f"_run_assign_{expr.get('id', 'q')}"
            lines.append(f"{indent}def {fn}(c):")
            lines.append(f"{indent}    s = c.state")
            target = self._assign_target(args[0], def_name)
            value_code = self._expr(args[1], env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent + "    ")
            lines.append(f"{indent}    {target} = {value_code}")
            lines.append(
                f"{indent}{state_var} = self._run_action({state_var}, {fn}, {sample_expr})"
            )
        elif (
            op in {"actionAll", "actionAny", "ite", "matchVariant"}
            or op in self._action_names
        ):
            fn = f"_run_action_{expr.get('id', 'q')}"
            lines.append(f"{indent}def {fn}(c):")
            lines.append(f"{indent}    s = c.state")
            self._emit_action(
                expr, lines, indent=indent + "    ", env=env, def_name=def_name
            )
            lines.append(
                f"{indent}{state_var} = self._run_action({state_var}, {fn}, {sample_expr})"
            )
        elif self._is_local_action_name(str(op)):
            call_args = ", ".join(
                self._expr(a, env=env, def_name=def_name) for a in args
            )
            suffix = f", {call_args}" if call_args else ""
            self._emit_pending_comments(lines, indent)
            lines.append(
                f"{indent}{state_var} = self._run_action({state_var}, {_py_name(str(op))}, {sample_expr}{suffix})"
            )
        elif op in self._run_names:
            self._unsupported(
                expr, def_name, f"parameterized run call '{op}' is not supported yet"
            )
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}self.fail({f'unsupported Quint run call: {op}'!r})")
        else:
            cond = self._expr(expr, env=env, def_name=def_name)
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}self.assertEqual(value({cond}), BoolValue(True))")

    def _expr(
        self,
        expr: Any,
        *,
        env: set[str],
        def_name: str,
        expected_type: Any | None = None,
    ) -> str:
        if not isinstance(expr, dict):
            return self._unsupported(
                {"id": "?", "kind": type(expr).__name__}, def_name, "invalid expression"
            )
        kind = expr.get("kind")
        if kind == "int":
            return f"Val({expr.get('value')!r})"
        if kind == "str":
            return f"Val({expr.get('value')!r})"
        if kind == "bool":
            return f"Val({expr.get('value')!r})"
        if kind == "name":
            return self._name_expr(
                str(expr.get("name")), env=env, node_id=expr.get("id")
            )
        if kind == "lambda":
            params = [_py_name(str(p["name"])) for p in expr.get("params", [])]
            body_type = self._declared_type(expected_type)
            if isinstance(body_type, dict) and body_type.get("kind") == "oper":
                body_type = body_type.get("res")
            param_types = {
                _py_name(str(p.get("name"))): p.get("typeAnnotation")
                or self._type_from_table(p.get("id"))
                for p in expr.get("params", [])
                if isinstance(p, dict)
            }
            self._local_type_stack.append(param_types)
            try:
                body = self._expr(
                    expr.get("expr"),
                    env=env | set(params),
                    def_name=def_name,
                    expected_type=body_type,
                )
            finally:
                self._local_type_stack.pop()
            return f"lambda {', '.join(params)}: {body}"
        if kind == "let":
            opdef = expr.get("opdef", {})
            if opdef.get("qualifier") == "nondet":
                return self._unsupported(
                    expr, def_name, "nondet let is only supported in actions/runs"
                )
            local = _py_name(str(opdef.get("name")))
            value_code = self._expr(
                opdef.get("expr"),
                env=env,
                def_name=def_name,
                expected_type=self._opdef_result_type(opdef),
            )
            self._local_type_stack.append({local: self._opdef_result_type(opdef)})
            try:
                body_code = self._expr(
                    expr.get("expr"),
                    env=env | {local},
                    def_name=def_name,
                    expected_type=expected_type,
                )
            finally:
                self._local_type_stack.pop()
            return f"(lambda {local}: {body_code})({value_code})"
        if kind != "app":
            return self._unsupported(
                expr, def_name, f"unsupported expression kind '{kind}'"
            )

        op = str(expr.get("opcode"))
        args = expr.get("args", [])
        if op in self._action_names:
            return self._unsupported(
                expr, def_name, f"action call '{op}' in expression position"
            )
        if self._is_local_action_name(op):
            return self._unsupported(
                expr, def_name, f"local action call '{op}' in expression position"
            )
        if self._is_local_expr_name(op):
            call_args = ", ".join(
                self._expr(a, env=env, def_name=def_name) for a in args
            )
            return f"{_py_name(op)}({call_args})"
        if _py_name(op) in env:
            call_args = ", ".join(
                self._expr(a, env=env, def_name=def_name) for a in args
            )
            return f"{_py_name(op)}({call_args})"
        union_ctor = self._union_ctor_call(
            op, args, expr.get("id"), env=env, def_name=def_name
        )
        if union_ctor is not None:
            return union_ctor
        binary = {
            "eq": "==",
            "neq": "!=",
            "iadd": "+",
            "isub": "-",
            "imul": "*",
            "idiv": "/",
            "imod": "%",
            "ilt": "<",
            "ilte": "<=",
            "igt": ">",
            "igte": ">=",
            "in": ".contains",
        }
        if op in binary and len(args) == 2:
            left = self._expr(args[0], env=env, def_name=def_name)
            right = self._expr(args[1], env=env, def_name=def_name)
            if op == "in":
                return f"{right}.contains({left})"
            return f"({left} {binary[op]} {right})"
        if op in {"ipow", "pow"} and len(args) == 2:
            return f"({self._expr(args[0], env=env, def_name=def_name)} ** {self._expr(args[1], env=env, def_name=def_name)})"
        if op.endswith("::pow") and len(args) == 2:
            return f"({self._expr(args[0], env=env, def_name=def_name)} ** {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "iff" and len(args) == 2:
            return f"({self._expr(args[0], env=env, def_name=def_name)} == {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "and":
            return f"And({', '.join(self._expr(a, env=env, def_name=def_name) for a in args)})"
        if op == "or":
            return f"Or({', '.join(self._expr(a, env=env, def_name=def_name) for a in args)})"
        if op == "not":
            return f"~({self._expr(args[0], env=env, def_name=def_name)})"
        if op == "implies":
            return f"Implies({self._expr(args[0], env=env, def_name=def_name)}, {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "iuminus":
            return f"(-{self._expr(args[0], env=env, def_name=def_name)})"
        if op == "ite":
            result_type = self._type_hint(expr.get("id"), expected_type)
            return f"Ite({self._expr(args[0], env=env, def_name=def_name)}, {self._expr(args[1], env=env, def_name=def_name, expected_type=result_type)}, {self._expr(args[2], env=env, def_name=def_name, expected_type=result_type)})"
        if op == "always" and len(args) == 1:
            return f"Always({self._expr(args[0], env=env, def_name=def_name)})"
        if op == "eventually" and len(args) == 1:
            return f"Eventually({self._expr(args[0], env=env, def_name=def_name)})"
        if op == "enabled" and len(args) == 1:
            return f"Enabled({self._expr(args[0], env=env, def_name=def_name)})"
        if op == "weakFair" and args:
            action = self._expr(args[0], env=env, def_name=def_name)
            return f"WeakFair({action})"
        if op == "strongFair" and args:
            action = self._expr(args[0], env=env, def_name=def_name)
            return f"StrongFair({action})"
        if op == "leadsTo" and len(args) == 2:
            return f"ImpliesT(Always({self._expr(args[0], env=env, def_name=def_name)}), Eventually({self._expr(args[1], env=env, def_name=def_name)}))"
        if op == "require" and len(args) == 1:
            return self._expr(args[0], env=env, def_name=def_name)
        if op == "assert" and len(args) == 1:
            return self._expr(args[0], env=env, def_name=def_name)
        if op == "range" and len(args) == 2:
            return f"Range({self._expr(args[0], env=env, def_name=def_name)}, {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "empty" and len(args) == 1:
            return f"{self._expr(args[0], env=env, def_name=def_name)}.is_empty"
        if op == "sortList" and len(args) == 2:
            return f"_quint_sort_list({self._expr(args[0], env=env, def_name=def_name)}, {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "Set":
            if not args:
                typ = self._type_hint(expr.get("id"), expected_type)
                if isinstance(typ, dict) and typ.get("kind") == "set":
                    return f"Set({self._type_expr(typ.get('elem'))})"
                return "Set(int)"
            return f"Set({', '.join(self._expr(a, env=env, def_name=def_name) for a in args)})"
        if op == "List":
            if not args:
                typ = self._type_hint(expr.get("id"), expected_type)
                if isinstance(typ, dict) and typ.get("kind") == "list":
                    return f"List({self._type_expr(typ.get('elem'))})"
                return "List(int)"
            return f"List({', '.join(self._expr(a, env=env, def_name=def_name) for a in args)})"
        if op == "Map":
            if not args:
                typ = self._type_hint(expr.get("id"), expected_type)
                if isinstance(typ, dict) and typ.get("kind") in {"map", "fun"}:
                    key_type = typ.get("from") or typ.get("arg")
                    value_type = typ.get("to") or typ.get("res")
                    return f"Map({self._type_expr(key_type)}, {self._type_expr(value_type)})"
                return "Map(int, int)"
            pairs = []
            for arg in args:
                if (
                    arg.get("kind") == "app"
                    and arg.get("opcode") == "Tup"
                    and len(arg.get("args", [])) == 2
                ):
                    pair_args = arg["args"]
                    pairs.append(
                        f"({self._expr(pair_args[0], env=env, def_name=def_name)}, "
                        f"{self._expr(pair_args[1], env=env, def_name=def_name)})"
                    )
                else:
                    placeholder = self._unsupported(
                        expr, def_name, "Map expects tuple key/value pairs"
                    )
                    pairs.append(f"({placeholder}, {placeholder})")
            return f"Map({', '.join(pairs)})"
        if op == "Tup":
            if not args:
                return "Val(())"
            return f"Tuple({', '.join(self._expr(a, env=env, def_name=def_name) for a in args)})"
        if op == "variant" and len(args) == 2:
            tag_node = args[0]
            if tag_node.get("kind") != "str":
                return self._unsupported(
                    expr, def_name, "variant tag is not a string literal"
                )
            tag = _py_name(str(tag_node.get("value")))
            typ = self._type_from_table(expr.get("id"))
            type_name = self._named_types.get(self._type_key(typ))
            payload = args[1]
            payload_args = payload.get("args", []) if isinstance(payload, dict) else []
            if type_name is None:
                return f"Val({str(tag_node.get('value'))!r})"
            if (
                payload.get("kind") == "app"
                and payload.get("opcode") == "Tup"
                and not payload_args
            ):
                return f"{type_name}.{tag}()"
            return (
                f"{type_name}.{tag}({self._expr(payload, env=env, def_name=def_name)})"
            )
        if op == "matchVariant" and len(args) >= 3:
            scrutinee = self._union_expr(
                self._expr(args[0], env=env, def_name=def_name)
            )
            match_branches = self._match_branches(expr, def_name)
            if not match_branches:
                return self._placeholder_expr_for_node(expr)
            branches = [
                self._match_branch_callback(branch, env=env, def_name=def_name)
                for branch in match_branches
            ]
            return f"{scrutinee}.match({', '.join(branches)})"
        if op == "Rec":
            if len(args) % 2 != 0:
                return self._unsupported(
                    expr, def_name, "record constructor with odd argument count"
                )
            typ = self._type_hint(expr.get("id"), expected_type)
            field_types = {}
            if isinstance(typ, dict) and typ.get("kind") == "rec":
                field_types = {
                    field_name: field_type
                    for field_name, field_type in _row_fields(typ.get("fields", {}))
                }
            fields = []
            for i in range(0, len(args), 2):
                key = args[i]
                if key.get("kind") != "str":
                    return self._unsupported(
                        expr, def_name, "record field name is not a string literal"
                    )
                field_name = str(key.get("value"))
                fields.append(
                    f"{_py_name(field_name)}={self._expr(args[i + 1], env=env, def_name=def_name, expected_type=field_types.get(field_name))}"
                )
            return f"Record({', '.join(fields)})"
        if op == "get" and len(args) == 2:
            return f"{self._expr(args[0], env=env, def_name=def_name)}[{self._expr(args[1], env=env, def_name=def_name)}]"
        if op == "item" and len(args) == 2:
            index = args[1]
            if isinstance(index, dict) and index.get("kind") == "int":
                return (
                    f"{self._expr(args[0], env=env, def_name=def_name)}"
                    f"[{int(index['value']) - 1}]"
                )
            return f"{self._expr(args[0], env=env, def_name=def_name)}[{self._expr(index, env=env, def_name=def_name)}]"
        if op == "field" and len(args) == 2:
            field = args[1].get("value") if isinstance(args[1], dict) else None
            if (
                field is None
                and isinstance(args[0], dict)
                and args[0].get("kind") == "str"
            ):
                return self._unsupported(
                    expr, def_name, "unexpected field opcode shape"
                )
            return f"{self._expr(args[0], env=env, def_name=def_name)}.{_py_name(str(field))}"
        if op == "set" and len(args) == 3:
            result_type = self._type_hint(expr.get("id"), expected_type)
            result_type = self._map_update_type(result_type, args[1], args[2])
            value_type = None
            if isinstance(result_type, dict) and result_type.get("kind") in {
                "map",
                "fun",
            }:
                value_type = result_type.get("to") or result_type.get("res")
            return f"{self._expr(args[0], env=env, def_name=def_name, expected_type=result_type)}.replace({self._expr(args[1], env=env, def_name=def_name)}, {self._expr(args[2], env=env, def_name=def_name, expected_type=value_type)})"
        if op == "put" and len(args) == 3:
            result_type = self._type_hint(expr.get("id"), expected_type)
            result_type = self._map_update_type(result_type, args[1], args[2])
            value_type = None
            if isinstance(result_type, dict) and result_type.get("kind") in {
                "map",
                "fun",
            }:
                value_type = result_type.get("to") or result_type.get("res")
            return f"{self._expr(args[0], env=env, def_name=def_name, expected_type=result_type)}.replace({self._expr(args[1], env=env, def_name=def_name)}, {self._expr(args[2], env=env, def_name=def_name, expected_type=value_type)})"
        if op == "setBy" and len(args) == 3:
            base = self._expr(args[0], env=env, def_name=def_name)
            key = self._expr(args[1], env=env, def_name=def_name)
            lam = self._expr(args[2], env=env, def_name=def_name)
            return f"{base}.replace({key}, ({lam})({base}[{key}]))"
        if op == "with" and len(args) == 3:
            field = args[1]
            if field.get("kind") != "str":
                return self._unsupported(
                    expr, def_name, "with field is not a string literal"
                )
            return f"{self._expr(args[0], env=env, def_name=def_name)}.replace({_py_name(str(field.get('value')))}={self._expr(args[2], env=env, def_name=def_name)})"
        if op == "union":
            return f"({self._expr(args[0], env=env, def_name=def_name)} | {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "exclude":
            return f"({self._expr(args[0], env=env, def_name=def_name)} - {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "intersect":
            return f"({self._expr(args[0], env=env, def_name=def_name)} & {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "subseteq":
            return f"({self._expr(args[0], env=env, def_name=def_name)} <= {self._expr(args[1], env=env, def_name=def_name)})"
        if op in {"contains", "has"} and len(args) == 2:
            base = self._expr(args[0], env=env, def_name=def_name)
            item = self._expr(args[1], env=env, def_name=def_name)
            base_type = self._type_from_table(args[0].get("id"))
            if self._is_map_type(base_type):
                return f"{base}.keys.contains({item})"
            return f"{base}.contains({item})"
        if op == "powerset" and len(args) == 1:
            return f"AllSubsets({self._expr(args[0], env=env, def_name=def_name)})"
        if op == "setOfMaps" and len(args) == 2:
            return f"AllMaps({self._expr(args[0], env=env, def_name=def_name)}, {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "tuples":
            return f"AllTuples({', '.join(self._expr(a, env=env, def_name=def_name) for a in args)})"
        if op == "size" and len(args) == 1:
            return f"{self._expr(args[0], env=env, def_name=def_name)}.size"
        if op == "length" and len(args) == 1:
            return f"{self._expr(args[0], env=env, def_name=def_name)}.size"
        if op == "keys" and len(args) == 1:
            return f"{self._expr(args[0], env=env, def_name=def_name)}.keys"
        if op == "append" and len(args) == 2:
            return (
                f"({self._expr(args[0], env=env, def_name=def_name)}"
                f" + List({self._expr(args[1], env=env, def_name=def_name)}))"
            )
        if op == "concat" and len(args) == 2:
            return f"({self._expr(args[0], env=env, def_name=def_name)} + {self._expr(args[1], env=env, def_name=def_name)})"
        if op == "flatten" and len(args) == 1:
            return f"{self._expr(args[0], env=env, def_name=def_name)}.flattened"
        if op == "head" and len(args) == 1:
            return f"{self._expr(args[0], env=env, def_name=def_name)}[0]"
        if op == "tail" and len(args) == 1:
            return f"{self._expr(args[0], env=env, def_name=def_name)}[1:]"
        if op == "slice" and len(args) == 3:
            return f"{self._expr(args[0], env=env, def_name=def_name)}[{self._expr(args[1], env=env, def_name=def_name)}:{self._expr(args[2], env=env, def_name=def_name)}]"
        if op == "nth" and len(args) == 2:
            return f"{self._expr(args[0], env=env, def_name=def_name)}[{self._expr(args[1], env=env, def_name=def_name)}]"
        if op == "indices" and len(args) == 1:
            return f"Set(0, ..., {self._expr(args[0], env=env, def_name=def_name)}.size - Val(1))"
        if op == "to" and len(args) == 2:
            return f"Set({self._expr(args[0], env=env, def_name=def_name)}, ..., {self._expr(args[1], env=env, def_name=def_name)})"
        if op in {"forall", "exists", "filter", "map", "mapBy"} and len(args) == 2:
            base = self._expr(args[0], env=env, def_name=def_name)
            result_type = self._type_hint(expr.get("id"), expected_type)
            lambda_result_type = None
            if isinstance(result_type, dict):
                if op == "mapBy" and result_type.get("kind") in {"map", "fun"}:
                    lambda_result_type = result_type.get("to") or result_type.get("res")
                elif op == "map" and result_type.get("kind") in {"set", "list"}:
                    lambda_result_type = result_type.get("elem")
            lam = self._expr(
                args[1],
                env=env,
                def_name=def_name,
                expected_type=lambda_result_type,
            )
            method = {
                "forall": "forall",
                "exists": "exists",
                "filter": "filter",
                "map": "map",
                "mapBy": "map_to",
            }[op]
            return f"{base}.{method}({lam})"
        if op in {"fold", "foldl"} and len(args) == 3:
            base = self._expr(args[0], env=env, def_name=def_name)
            initial = self._expr(args[1], env=env, def_name=def_name)
            lam = self._expr(args[2], env=env, def_name=def_name)
            return f"{base}.reduce({lam}, {initial})"
        if op in self._expr_names:
            call_args = ", ".join(
                self._expr(a, env=env, def_name=def_name) for a in args
            )
            suffix = f", {call_args}" if call_args else ""
            return f"{_py_name(op)}({self._current_state_ref}{suffix})"
        if op == "actionAll":
            return f"And({', '.join(self._expr(a, env=env, def_name=def_name) for a in args)})"
        return self._unsupported(expr, def_name, f"unsupported opcode '{op}'")

    def _union_expr(self, code: str) -> str:
        return f"UnionExpr({code}.node)"

    def _union_ctor_call(
        self,
        op: str,
        args: list[dict[str, Any]],
        node_id: object,
        *,
        env: set[str],
        def_name: str,
    ) -> str | None:
        candidates = self._union_ctors_by_name.get(op)
        if not candidates:
            return None

        result_type = self._type_from_table(node_id)
        type_name = self._named_types.get(self._type_key(result_type))
        if type_name is not None:
            candidates = [c for c in candidates if c[0] == type_name]
        if len(candidates) != 1:
            return None

        type_name, py_tag, payload_type = candidates[0]
        if payload_type is None:
            return f"{type_name}.{py_tag}()"
        if len(args) == 1:
            payload = self._expr(args[0], env=env, def_name=def_name)
        else:
            payload = (
                "Tuple("
                + ", ".join(self._expr(a, env=env, def_name=def_name) for a in args)
                + ")"
            )
        return f"{type_name}.{py_tag}({payload})"

    def _union_ctor_name_expr(
        self, name: str, *, node_id: object | None = None
    ) -> str | None:
        candidates = self._union_ctors_by_name.get(name)
        if candidates is None:
            return None
        if node_id is not None:
            typ = self._type_from_table(node_id)
            type_name = self._named_types.get(self._type_key(typ))
            if type_name is not None:
                candidates = [c for c in candidates if c[0] == type_name]
        if len(candidates) != 1:
            return None
        type_name, py_tag, payload_type = candidates[0]
        if payload_type is not None:
            return None
        return f"{type_name}.{py_tag}()"

    def _emit_action_match_variant(
        self,
        expr: dict[str, Any],
        lines: list[str],
        *,
        indent: str,
        env: set[str],
        def_name: str,
    ) -> None:
        args = expr.get("args", [])
        if not args:
            self._unsupported(expr, def_name, "matchVariant has no scrutinee")
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}c.assume(Val(False))")
            return

        scrutinee = self._union_expr(self._expr(args[0], env=env, def_name=def_name))
        match_name = f"_match_{expr.get('id', 'q')}"
        branches = self._match_branches(expr, def_name)
        if not branches:
            self._unsupported(expr, def_name, "matchVariant has no branches")
            self._emit_pending_comments(lines, indent)
            lines.append(f"{indent}c.assume(Val(False))")
            return

        labels = [branch.py_tag for branch in branches]
        self._emit_pending_comments(lines, indent)
        lines.append(f"{indent}{match_name} = {scrutinee}")
        lines.append(
            f"{indent}alts = iter(c.alternatives({', '.join(repr(x) for x in labels)}))"
        )
        for branch in branches:
            lines.append(f"{indent}with next(alts):")
            branch_indent = indent + "    "
            lines.append(
                f"{branch_indent}c.assume({match_name}.tag == Val({branch.py_tag!r}))"
            )
            branch_env = set(env)
            if branch.payload_type is not None and branch.param is not None:
                dummy = self._dummy_expr_for_type(branch.payload_type)
                lines.append(
                    f"{branch_indent}{branch.param} = "
                    f"{match_name}.match({branch.py_tag}=lambda {branch.param}: "
                    f"{branch.param}, default={dummy})"
                )
                branch_env.add(branch.param)
            self._emit_action(
                branch.body,
                lines,
                indent=branch_indent,
                env=branch_env,
                def_name=def_name,
            )

    def _match_branch_callback(
        self, branch: _MatchBranch, *, env: set[str], def_name: str
    ) -> str:
        if branch.payload_type is None:
            body = self._expr(branch.body, env=env, def_name=def_name)
            return f"{branch.py_tag}=lambda: {body}"

        param = branch.param or f"_{branch.py_tag}_payload"
        body = self._expr(branch.body, env=env | {param}, def_name=def_name)
        return f"{branch.py_tag}=lambda {param}: {body}"

    def _match_branches(
        self, expr: dict[str, Any], def_name: str
    ) -> list[_MatchBranch]:
        args = expr.get("args", [])
        if len(args) < 3:
            self._unsupported(expr, def_name, "matchVariant expects branches")
            return []
        if len(args) % 2 == 0:
            self._unsupported(expr, def_name, "matchVariant has dangling branch")
            return []

        scrutinee_type = self._type_from_table(args[0].get("id"))
        variants = self._union_variants(scrutinee_type, expr, def_name)
        variant_by_py_tag = {py_tag: payload for py_tag, payload in variants}

        explicit: dict[str, _MatchBranch] = {}
        default_body: dict[str, Any] | None = None
        default_param: str | None = None
        for i in range(1, len(args), 2):
            tag_node = args[i]
            if tag_node.get("kind") != "str":
                self._unsupported(
                    expr, def_name, "matchVariant tag is not a string literal"
                )
                continue
            tag = str(tag_node.get("value"))
            body, param = self._match_branch_body(args[i + 1])
            if tag == "_":
                default_body = body
                default_param = param
                continue

            py_tag = _py_name(tag)
            if py_tag not in variant_by_py_tag:
                self._unsupported(
                    expr, def_name, f"matchVariant tag '{tag}' is not in union type"
                )
                continue
            explicit[py_tag] = _MatchBranch(
                py_tag=py_tag,
                body=body,
                payload_type=variant_by_py_tag[py_tag],
                param=param,
            )

        branches: list[_MatchBranch] = []
        for py_tag, payload_type in variants:
            branch = explicit.get(py_tag)
            if branch is not None:
                branches.append(branch)
            elif default_body is not None:
                branches.append(
                    _MatchBranch(
                        py_tag=py_tag,
                        body=default_body,
                        payload_type=payload_type,
                        param=default_param if payload_type is not None else None,
                    )
                )

        missing = [
            py_tag
            for py_tag, _ in variants
            if py_tag not in {b.py_tag for b in branches}
        ]
        if missing:
            self._unsupported(
                expr,
                def_name,
                "matchVariant is not exhaustive: " + ", ".join(missing),
            )
        return branches

    def _match_branch_body(
        self, branch: dict[str, Any]
    ) -> tuple[dict[str, Any], str | None]:
        if branch.get("kind") != "lambda":
            return branch, None
        params = branch.get("params", [])
        param = _py_name(str(params[0]["name"])) if params else None
        return cast(dict[str, Any], branch.get("expr")), param

    def _union_variants(
        self, typ: Any, expr: dict[str, Any], def_name: str
    ) -> list[tuple[str, Any]]:
        if not isinstance(typ, dict):
            self._unsupported(expr, def_name, "matchVariant scrutinee has no type")
            return []
        if typ.get("kind") == "const":
            name = str(typ.get("name"))
            decl = self._decl_by_name.get(name)
            if decl is not None and isinstance(decl.get("type"), dict):
                typ = decl["type"]
        if typ.get("kind") != "sum":
            self._unsupported(
                expr, def_name, "matchVariant scrutinee is not a union type"
            )
            return []
        return [
            (_py_name(field_name), self._variant_payload_type(field_type))
            for field_name, field_type in _row_fields(typ.get("fields", {}))
        ]

    def _variant_payload_type(self, typ: Any) -> Any:
        if (
            isinstance(typ, dict)
            and typ.get("kind") == "tup"
            and not _row_fields(typ.get("fields", {}))
        ):
            return None
        return typ

    def _dummy_expr_for_type(self, typ: Any) -> str:
        if not isinstance(typ, dict):
            return "Val(0)"
        kind = typ.get("kind")
        if kind == "int":
            return "Val(0)"
        if kind == "bool":
            return "Val(False)"
        if kind == "str":
            return "Val('')"
        if kind == "var":
            return "Val(0)"
        if kind == "set":
            return f"Set({self._type_expr(typ.get('elem'))})"
        if kind == "list":
            return f"List({self._type_expr(typ.get('elem'))})"
        if kind in {"map", "fun"}:
            key_type = typ.get("from") or typ.get("arg")
            value_type = typ.get("to") or typ.get("res")
            return f"Map({self._type_expr(key_type)}, {self._type_expr(value_type)})"
        if kind == "tup":
            fields = _row_fields(typ.get("fields", {}))
            if not fields:
                return "Val(())"
            values = [self._dummy_expr_for_type(field_type) for _, field_type in fields]
            return f"Tuple({', '.join(values)})"
        if kind == "rec":
            fields = _row_fields(typ.get("fields", {}))
            values = [
                f"{_py_name(field_name)}={self._dummy_expr_for_type(field_type)}"
                for field_name, field_type in fields
            ]
            return f"Record({', '.join(values)})"
        if kind == "sum":
            type_name = self._named_types.get(self._type_key(typ))
            fields = _row_fields(typ.get("fields", {}))
            if type_name is not None and fields:
                tag, payload = fields[0]
                py_tag = _py_name(tag)
                payload_type = self._variant_payload_type(payload)
                if payload_type is None:
                    return f"{type_name}.{py_tag}()"
                return (
                    f"{type_name}.{py_tag}({self._dummy_expr_for_type(payload_type)})"
                )
            return "Val(0)"
        if kind == "const":
            name = str(typ.get("name"))
            if name in self._type_aliases:
                return self._dummy_expr_for_type(self._type_aliases[name])
            decl = self._decl_by_name.get(name)
            if decl is not None and isinstance(decl.get("type"), dict):
                return self._dummy_expr_for_type(decl["type"])
            return "Val(0)"
        return "Val(0)"

    def _emit_local_expr_def(
        self,
        opdef: dict[str, Any],
        lines: list[str],
        *,
        indent: str,
        env: set[str],
        def_name: str,
    ) -> None:
        name = _py_name(str(opdef.get("name")))
        expr = opdef.get("expr")
        params = self._lambda_params(expr) if isinstance(expr, dict) else []
        py_params = [_py_name(p) for p in params]
        sig = ", ".join(f"{p}: Expr" for p in py_params)
        lines.append(f"{indent}@expr")
        lines.append(f"{indent}def {name}({sig}) -> Expr:")
        body_expr = (
            expr.get("expr")
            if isinstance(expr, dict) and expr.get("kind") == "lambda"
            else expr
        )
        self._emit_expr_return(
            self._as_expr(body_expr),
            lines,
            indent=indent + "    ",
            env=env | set(py_params),
            def_name=def_name,
        )

    def _emit_local_action_def(
        self,
        opdef: dict[str, Any],
        lines: list[str],
        *,
        indent: str,
        env: set[str],
        def_name: str,
    ) -> None:
        name = _py_name(str(opdef.get("name")))
        expr = opdef.get("expr")
        params = self._lambda_params(expr) if isinstance(expr, dict) else []
        py_params = [_py_name(p) for p in params]
        sig_tail = "".join(f", {p}: Expr" for p in py_params)
        lines.append(f"{indent}@action")
        lines.append(
            f"{indent}def {name}(c: Context[{self.state_class_name}]{sig_tail}):"
        )
        lines.append(f"{indent}    s = c.state")
        body_expr = (
            expr.get("expr")
            if isinstance(expr, dict) and expr.get("kind") == "lambda"
            else expr
        )
        self._emit_action(
            self._as_expr(body_expr),
            lines,
            indent=indent + "    ",
            env=env | set(py_params),
            def_name=def_name,
        )

    def _is_local_function_def(self, opdef: dict[str, Any]) -> bool:
        expr = opdef.get("expr")
        return isinstance(expr, dict) and (
            expr.get("kind") == "lambda" or opdef.get("qualifier") == "action"
        )

    def _is_action_def(self, opdef: dict[str, Any]) -> bool:
        expr = opdef.get("expr")
        return opdef.get("qualifier") == "action" or (
            isinstance(expr, dict) and expr.get("qualifier") == "action"
        )

    def _is_local_expr_name(self, name: str) -> bool:
        py = _py_name(name)
        return any(py in names for names in reversed(self._local_expr_name_stack))

    def _is_local_action_name(self, name: str) -> bool:
        py = _py_name(name)
        return any(py in names for names in reversed(self._local_action_name_stack))

    def _one_of_arg(self, expr: dict[str, Any], *, env: set[str], def_name: str) -> str:
        if expr.get("kind") == "app" and expr.get("opcode") == "oneOf":
            return self._expr(expr.get("args", [None])[0], env=env, def_name=def_name)
        self._unsupported(expr, def_name, "expected oneOf expression")
        return "Set(Val(0))"

    def _assign_target(self, target: dict[str, Any], def_name: str) -> str:
        if target.get("kind") == "name":
            name = str(target.get("name"))
            if name in self._vars:
                return f"s.{_py_name(name)}"
            return _py_name(name)
        self._unsupported(
            target, def_name, "assignment target is not a state variable name"
        )
        return "_unsupported_assignment_target"

    def _assignment_type(self, target: dict[str, Any]) -> Any:
        if target.get("kind") != "name":
            return None
        name = str(target.get("name"))
        for decl in self._decls:
            if decl.get("name") == name and decl.get("kind") in {"const", "var"}:
                return decl.get("typeAnnotation") or self._type_from_table(
                    decl.get("id")
                )
        return None

    def _alternative_label(self, expr: dict[str, Any], index: int) -> str:
        if expr.get("kind") == "name":
            return _py_name(str(expr.get("name")))
        if expr.get("kind") == "app":
            return _py_name(str(expr.get("opcode")))
        return f"alt_{index}"

    def _name_expr(
        self, name: str, *, env: set[str], node_id: object | None = None
    ) -> str:
        py = _py_name(name)
        if py in env:
            return py
        if name in self._vars or name in self._consts:
            return f"{self._current_state_ref}.{py}"
        union_ctor = self._union_ctor_name_expr(name, node_id=node_id)
        if union_ctor is not None:
            return union_ctor
        if name in self._expr_names:
            return f"{py}({self._current_state_ref})"
        if name in {"true", "false"}:
            return f"Val({name == 'true'})"
        return py

    def _lambda_params(self, expr: dict[str, Any]) -> list[str]:
        if expr.get("kind") != "lambda":
            return []
        return [str(p["name"]) for p in expr.get("params", [])]

    def _lambda_param_decls(self, expr: dict[str, Any]) -> list[dict[str, Any]]:
        if expr.get("kind") != "lambda":
            return []
        return [p for p in expr.get("params", []) if isinstance(p, dict)]

    def _param_type_expr(self, param: dict[str, Any]) -> str:
        typ = param.get("typeAnnotation") or self._type_from_table(param.get("id"))
        if isinstance(typ, dict):
            return self._type_expr(typ)
        return "Expr"

    def _child_expr(
        self, expr: dict[str, Any], key: str, def_name: str
    ) -> dict[str, Any]:
        child = expr.get(key)
        if isinstance(child, dict):
            return child
        self._unsupported(expr, def_name, f"missing {key} expression")
        return self._placeholder_bool_expr()

    def _as_expr(self, expr: Any) -> dict[str, Any]:
        if isinstance(expr, dict):
            return expr
        return self._placeholder_bool_expr()

    def _placeholder_bool_expr(self) -> dict[str, Any]:
        return {"kind": "bool", "value": False, "type": {"kind": "bool"}}

    def _type_from_table(self, node_id: object) -> dict[str, Any] | None:
        if node_id is None:
            return None
        entry = self.ir.get("types", {}).get(str(node_id))
        if isinstance(entry, dict):
            return cast(dict[str, Any] | None, entry.get("type"))
        return None

    def _type_expr(self, typ: Any) -> str:
        if not isinstance(typ, dict):
            return "int"
        kind = typ.get("kind")
        if kind == "int":
            return "int"
        if kind == "bool":
            return "bool"
        if kind == "str":
            return "str"
        if kind == "var":
            return "int"
        if kind == "set":
            return f"set[{self._type_expr(typ.get('elem'))}]"
        if kind == "list":
            return f"list[{self._type_expr(typ.get('elem'))}]"
        if kind == "map":
            return f"dict[{self._type_expr(typ.get('from'))}, {self._type_expr(typ.get('to'))}]"
        if kind == "fun":
            return f"dict[{self._type_expr(typ.get('arg'))}, {self._type_expr(typ.get('res'))}]"
        if kind == "const":
            name = str(typ.get("name"))
            if name in self._type_aliases:
                return self._type_expr(self._type_aliases[name])
            if name in self._typedef_class_names:
                return self._typedef_class_names[name]
            return "int"
        named = self._named_types.get(self._type_key(typ))
        if named is not None:
            return named
        if kind == "tup":
            fields = _row_fields(typ.get("fields", {}))
            if not fields:
                return "()"
            return "tuple[" + ", ".join(self._type_expr(t) for _, t in fields) + "]"
        if kind == "rec":
            field_key = tuple(
                sorted(
                    field_name for field_name, _ in _row_fields(typ.get("fields", {}))
                )
            )
            return self._record_type_names_by_fields.get(field_key, "int")
        if kind == "sum":
            return "int"
        if kind == "oper":
            return "int"
        return "int"

    def _is_map_type(self, typ: Any) -> bool:
        if not isinstance(typ, dict):
            return False
        if typ.get("kind") in {"map", "fun"}:
            return True
        if typ.get("kind") == "const":
            alias = self._type_aliases.get(str(typ.get("name")))
            return self._is_map_type(alias)
        return False

    def _map_update_type(
        self, result_type: Any, key_expr: dict[str, Any], value_expr: dict[str, Any]
    ) -> Any:
        key_type = self._expr_value_type(key_expr)
        value_type = self._expr_value_type(value_expr)
        if not (
            isinstance(key_type, dict)
            and isinstance(value_type, dict)
            and not self._type_contains_var(key_type)
            and not self._type_contains_var(value_type)
        ):
            return result_type

        if not isinstance(result_type, dict) or result_type.get("kind") not in {
            "map",
            "fun",
        }:
            return {"kind": "map", "from": key_type, "to": value_type}

        current_key_type = result_type.get("from") or result_type.get("arg")
        current_value_type = result_type.get("to") or result_type.get("res")
        if self._type_key(current_key_type) == self._type_key(
            key_type
        ) and self._type_key(current_value_type) == self._type_key(value_type):
            return result_type
        return {"kind": "map", "from": key_type, "to": value_type}

    def _emit_pending_comments(self, lines: list[str], indent: str) -> None:
        for comment in self._pending_comments:
            lines.append(f"{indent}# {comment}")
        self._pending_comments.clear()

    def _record_unsupported(
        self, expr: dict[str, Any], def_name: str, msg: str
    ) -> ConversionDiagnostic:
        kind = expr.get("kind")
        node_id = expr.get("id", "?")
        opcode = expr.get("opcode")
        diagnostic = ConversionDiagnostic(
            source=self.source,
            module=self.module_name,
            definition=def_name,
            node_id=node_id,
            kind=kind,
            opcode=opcode,
            message=msg,
            location=self.source_locations.get(str(node_id)),
        )
        self.diagnostics.append(diagnostic)
        self._pending_comments.append(diagnostic.comment())
        return diagnostic

    def _unsupported(self, expr: dict[str, Any], def_name: str, msg: str) -> str:
        self._record_unsupported(expr, def_name, msg)
        return self._placeholder_expr_for_node(expr)

    def _placeholder_expr_for_node(self, expr: dict[str, Any]) -> str:
        return self._dummy_expr_for_type(self._type_from_table(expr.get("id")))


def _hoist_nested_python_lambdas(source: str) -> str:
    try:
        module = ast.parse(source)
    except SyntaxError:
        return source

    taken_names = _python_names(module)
    hoister = _NestedLambdaHoister(taken_names)
    module = cast(ast.Module, hoister.visit(module))
    if not hoister.changed:
        return source

    ast.fix_missing_locations(module)
    lines = source.splitlines()
    replacements = _changed_function_replacements(module)
    for start, end, replacement in reversed(replacements):
        lines[start - 1 : end] = replacement.splitlines()
    return "\n".join(lines).rstrip() + "\n"


def _python_names(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            names.add(child.id)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(child.name)
        elif isinstance(child, ast.arg):
            names.add(child.arg)
    return names


def _changed_function_replacements(module: ast.Module) -> list[tuple[int, int, str]]:
    parent_by_node: dict[ast.AST, ast.AST] = {}
    for parent_node in ast.walk(module):
        for child in ast.iter_child_nodes(parent_node):
            parent_by_node[child] = parent_node

    replacements: list[tuple[int, int, str]] = []
    for node in ast.walk(module):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not getattr(node, "_quint_hoisted_lambdas", False):
            continue
        ancestor: ast.AST | None = parent_by_node.get(node)
        skip = False
        while ancestor is not None:
            if isinstance(
                ancestor, (ast.FunctionDef, ast.AsyncFunctionDef)
            ) and getattr(ancestor, "_quint_hoisted_lambdas", False):
                skip = True
                break
            ancestor = parent_by_node.get(ancestor)
        if skip or node.end_lineno is None:
            continue

        start = node.lineno
        if node.decorator_list:
            start = min(decorator.lineno for decorator in node.decorator_list)
        indent = " " * node.col_offset
        replacement = "\n".join(
            indent + line if line else line for line in ast.unparse(node).splitlines()
        )
        replacements.append((start, node.end_lineno, replacement))
    return replacements


class _NestedLambdaHoister(ast.NodeTransformer):
    def __init__(self, taken_names: set[str]) -> None:
        self.taken_names = set(taken_names)
        self.changed = False

    def visit_Module(self, node: ast.Module) -> ast.Module:
        node.body = self._visit_container_body(node.body)
        return node

    def visit_ClassDef(self, node: ast.ClassDef) -> ast.ClassDef:
        node.body = self._visit_container_body(node.body)
        return node

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        node.body, body_changed = self._process_body(node.body)
        if body_changed:
            setattr(node, "_quint_hoisted_lambdas", True)
            self.changed = True
        return node

    def visit_AsyncFunctionDef(
        self, node: ast.AsyncFunctionDef
    ) -> ast.AsyncFunctionDef:
        node.body, body_changed = self._process_body(node.body)
        if body_changed:
            setattr(node, "_quint_hoisted_lambdas", True)
            self.changed = True
        return node

    def _visit_container_body(self, body: list[ast.stmt]) -> list[ast.stmt]:
        visited: list[ast.stmt] = []
        for stmt in body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                visited.append(cast(ast.stmt, self.visit(stmt)))
            else:
                visited.append(stmt)
        return visited

    def _process_body(self, body: list[ast.stmt]) -> tuple[list[ast.stmt], bool]:
        changed = False
        processed: list[ast.stmt] = []
        for stmt in body:
            new_stmts, stmt_changed = self._process_stmt(stmt)
            processed.extend(new_stmts)
            changed = changed or stmt_changed
        return processed, changed

    def _process_stmt(self, stmt: ast.stmt) -> tuple[list[ast.stmt], bool]:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            return [cast(ast.stmt, self.visit(stmt))], False

        if isinstance(stmt, ast.Return):
            if stmt.value is None:
                return [stmt], False
            prelude, stmt.value = self._lower_expr(stmt.value)
            return [*prelude, stmt], bool(prelude)

        if isinstance(stmt, ast.Assign):
            prelude, stmt.value = self._lower_expr(stmt.value)
            return [*prelude, stmt], bool(prelude)

        if isinstance(stmt, ast.AnnAssign):
            if stmt.value is None:
                return [stmt], False
            prelude, stmt.value = self._lower_expr(stmt.value)
            return [*prelude, stmt], bool(prelude)

        if isinstance(stmt, ast.AugAssign):
            prelude, stmt.value = self._lower_expr(stmt.value)
            return [*prelude, stmt], bool(prelude)

        if isinstance(stmt, ast.Expr):
            prelude, stmt.value = self._lower_expr(stmt.value)
            return [*prelude, stmt], bool(prelude)

        if isinstance(stmt, ast.Assert):
            prelude, stmt.test = self._lower_expr(stmt.test)
            if stmt.msg is not None:
                msg_prelude, stmt.msg = self._lower_expr(stmt.msg)
                prelude.extend(msg_prelude)
            return [*prelude, stmt], bool(prelude)

        if isinstance(stmt, ast.If):
            prelude, stmt.test = self._lower_expr(stmt.test)
            stmt.body, body_changed = self._process_body(stmt.body)
            stmt.orelse, orelse_changed = self._process_body(stmt.orelse)
            changed = bool(prelude) or body_changed or orelse_changed
            return [*prelude, stmt], changed

        if isinstance(stmt, ast.For):
            prelude, stmt.iter = self._lower_expr(stmt.iter)
            stmt.body, body_changed = self._process_body(stmt.body)
            stmt.orelse, orelse_changed = self._process_body(stmt.orelse)
            changed = bool(prelude) or body_changed or orelse_changed
            return [*prelude, stmt], changed

        if isinstance(stmt, ast.While):
            prelude, stmt.test = self._lower_expr(stmt.test)
            stmt.body, body_changed = self._process_body(stmt.body)
            stmt.orelse, orelse_changed = self._process_body(stmt.orelse)
            changed = bool(prelude) or body_changed or orelse_changed
            return [*prelude, stmt], changed

        if isinstance(stmt, ast.With):
            with_prelude: list[ast.stmt] = []
            for item in stmt.items:
                item_prelude, item.context_expr = self._lower_expr(item.context_expr)
                with_prelude.extend(item_prelude)
            stmt.body, body_changed = self._process_body(stmt.body)
            return [*with_prelude, stmt], bool(with_prelude) or body_changed

        if isinstance(stmt, ast.Try):
            stmt.body, body_changed = self._process_body(stmt.body)
            stmt.orelse, orelse_changed = self._process_body(stmt.orelse)
            stmt.finalbody, final_changed = self._process_body(stmt.finalbody)
            handler_changed = False
            for handler in stmt.handlers:
                handler.body, changed = self._process_body(handler.body)
                handler_changed = handler_changed or changed
            return [
                stmt
            ], body_changed or orelse_changed or final_changed or handler_changed

        return [stmt], False

    def _lower_expr(
        self,
        expr: ast.expr,
        *,
        inside_hoisted_lambda: bool = False,
        allow_iife_inline: bool = False,
    ) -> tuple[list[ast.stmt], ast.expr]:
        if isinstance(expr, ast.Lambda):
            if not inside_hoisted_lambda and _lambda_nesting_depth(expr) >= 2:
                return self._helper_for_lambda(expr)
            return [], expr

        if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Lambda):
            if inside_hoisted_lambda and allow_iife_inline:
                inlined = self._inline_iife(expr)
                if inlined is not None:
                    return inlined
            if not inside_hoisted_lambda and _lambda_nesting_depth(expr) >= 2:
                helper_prelude, helper_name = self._helper_for_lambda(expr.func)
                arg_prelude, args = self._lower_exprs(expr.args)
                keyword_prelude, keywords = self._lower_keywords(expr.keywords)
                call = ast.Call(func=helper_name, args=args, keywords=keywords)
                ast.copy_location(call, expr)
                return [*helper_prelude, *arg_prelude, *keyword_prelude], call

        prelude: list[ast.stmt] = []
        for field_name, value in ast.iter_fields(expr):
            if isinstance(value, ast.expr):
                field_prelude, lowered = self._lower_expr(
                    value,
                    inside_hoisted_lambda=inside_hoisted_lambda,
                    allow_iife_inline=False,
                )
                prelude.extend(field_prelude)
                setattr(expr, field_name, lowered)
            elif isinstance(value, list):
                lowered_items: list[Any] = []
                for item in value:
                    if isinstance(item, ast.expr):
                        item_prelude, lowered = self._lower_expr(
                            item,
                            inside_hoisted_lambda=inside_hoisted_lambda,
                            allow_iife_inline=False,
                        )
                        prelude.extend(item_prelude)
                        lowered_items.append(lowered)
                    elif isinstance(item, ast.keyword):
                        item_prelude, lowered_keyword = self._lower_keyword(
                            item,
                            inside_hoisted_lambda=inside_hoisted_lambda,
                            allow_iife_inline=False,
                        )
                        prelude.extend(item_prelude)
                        lowered_items.append(lowered_keyword)
                    else:
                        lowered_items.append(item)
                setattr(expr, field_name, lowered_items)
        return prelude, expr

    def _lower_exprs(
        self, exprs: list[ast.expr]
    ) -> tuple[list[ast.stmt], list[ast.expr]]:
        prelude: list[ast.stmt] = []
        lowered: list[ast.expr] = []
        for expr in exprs:
            expr_prelude, lowered_expr = self._lower_expr(expr)
            prelude.extend(expr_prelude)
            lowered.append(lowered_expr)
        return prelude, lowered

    def _lower_keywords(
        self, keywords: list[ast.keyword]
    ) -> tuple[list[ast.stmt], list[ast.keyword]]:
        prelude: list[ast.stmt] = []
        lowered: list[ast.keyword] = []
        for keyword_node in keywords:
            keyword_prelude, lowered_keyword = self._lower_keyword(keyword_node)
            prelude.extend(keyword_prelude)
            lowered.append(lowered_keyword)
        return prelude, lowered

    def _lower_keyword(
        self,
        keyword: ast.keyword,
        *,
        inside_hoisted_lambda: bool = False,
        allow_iife_inline: bool = False,
    ) -> tuple[list[ast.stmt], ast.keyword]:
        prelude, keyword.value = self._lower_expr(
            keyword.value,
            inside_hoisted_lambda=inside_hoisted_lambda,
            allow_iife_inline=allow_iife_inline,
        )
        return prelude, keyword

    def _helper_for_lambda(self, node: ast.Lambda) -> tuple[list[ast.stmt], ast.Name]:
        helper_name = self._next_helper_name()
        body_prelude, body = self._lower_expr(
            node.body, inside_hoisted_lambda=True, allow_iife_inline=True
        )
        helper = ast.FunctionDef(
            name=helper_name,
            args=node.args,
            body=[*body_prelude, ast.Return(value=body)],
            decorator_list=[],
            returns=None,
            type_comment=None,
        )
        ast.copy_location(helper, node)
        for stmt in helper.body:
            ast.copy_location(stmt, node)
        name = ast.Name(id=helper_name, ctx=ast.Load())
        ast.copy_location(name, node)
        return [helper], name

    def _inline_iife(self, node: ast.Call) -> tuple[list[ast.stmt], ast.expr] | None:
        lambda_func = cast(ast.Lambda, node.func)
        params = _lambda_positional_params(lambda_func.args)
        if params is None or len(params) != len(node.args) or node.keywords:
            return None

        prelude: list[ast.stmt] = []
        for param, arg in zip(params, node.args):
            arg_prelude, value = self._lower_expr(
                arg, inside_hoisted_lambda=True, allow_iife_inline=False
            )
            prelude.extend(arg_prelude)
            target = ast.Name(id=param, ctx=ast.Store())
            ast.copy_location(target, node)
            assignment = ast.Assign(targets=[target], value=value)
            ast.copy_location(assignment, node)
            prelude.append(assignment)

        body_prelude, body = self._lower_expr(
            lambda_func.body, inside_hoisted_lambda=True, allow_iife_inline=True
        )
        prelude.extend(body_prelude)
        return prelude, body

    def _next_helper_name(self) -> str:
        index = 1
        while True:
            name = f"_quint_aux_{index}"
            if name not in self.taken_names:
                self.taken_names.add(name)
                return name
            index += 1


def _lambda_positional_params(args: ast.arguments) -> list[str] | None:
    if args.vararg is not None or args.kwonlyargs or args.kwarg is not None:
        return None
    if args.defaults:
        return None
    return [arg.arg for arg in [*args.posonlyargs, *args.args]]


def _lambda_nesting_depth(node: ast.AST, depth: int = 0) -> int:
    if isinstance(node, ast.Lambda):
        current = depth + 1
        return max(current, _lambda_nesting_depth(node.body, current))
    max_depth = depth
    for child in ast.iter_child_nodes(node):
        max_depth = max(max_depth, _lambda_nesting_depth(child, depth))
    return max_depth


def _format_python_source(source: str, *, text_width: int, text_indent: int) -> str:
    docs: list[AbstractDoc] = []
    paren_depth = 0
    for line in source.splitlines():
        if paren_depth > 0 or _paren_delta(line) != 0:
            docs.append(TextDoc(line))
            paren_depth = max(0, paren_depth + _paren_delta(line))
            continue
        docs.append(_format_python_line(line, text_indent))
    rendered = render_doc(_join_docs(docs, HardLine()), text_width)
    return rendered.rstrip() + "\n"


def _format_python_line(line: str, text_indent: int) -> AbstractDoc:
    if not line:
        return TextDoc("")

    stripped = line.lstrip(" ")
    leading = line[: len(line) - len(stripped)]
    if not stripped or stripped.startswith("#"):
        return TextDoc(line)
    if stripped.startswith(('"""', "'''")):
        return TextDoc(line)
    if stripped.startswith(("def ", "class ", "@", "import ", "from ")):
        return TextDoc(line)
    if stripped in {"try:", "else:", "finally:"} or stripped.startswith("except "):
        return TextDoc(line)

    stmt: ast.stmt | None = None
    try:
        if stripped.endswith(":"):
            parsed = ast.parse(stripped + "\n    pass")
        else:
            parsed = ast.parse(stripped)
        if len(parsed.body) == 1:
            stmt = parsed.body[0]
    except SyntaxError:
        return TextDoc(line)

    if stmt is None:
        return TextDoc(line)

    return TextDoc(leading) + NestDoc(
        _stmt_doc(stmt, stripped, text_indent), len(leading)
    )


def _paren_delta(line: str) -> int:
    depth = 0
    in_string: str | None = None
    escaped = False
    for char in line:
        if in_string is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == in_string:
                in_string = None
            continue
        if char in {"'", '"'}:
            in_string = char
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
    return depth


def _stmt_doc(stmt: ast.stmt, source: str, text_indent: int) -> AbstractDoc:
    if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
        return _assign_doc(
            ast.unparse(stmt.targets[0]),
            _expr_doc(stmt.value, text_indent),
            text_indent,
        )
    if isinstance(stmt, ast.AnnAssign):
        target = ast.unparse(stmt.target)
        annotation = ast.unparse(stmt.annotation)
        if stmt.value is None:
            return TextDoc(f"{target}: {annotation}")
        return _assign_doc(
            f"{target}: {annotation}",
            _expr_doc(stmt.value, text_indent),
            text_indent,
        )
    if isinstance(stmt, ast.Return):
        if stmt.value is None:
            return TextDoc("return")
        return _keyword_expr_doc(
            "return", _expr_doc(stmt.value, text_indent), text_indent
        )
    if isinstance(stmt, ast.Assert):
        return _keyword_expr_doc(
            "assert", _expr_doc(stmt.test, text_indent), text_indent
        )
    if isinstance(stmt, ast.Expr):
        return _expr_doc(stmt.value, text_indent)
    if isinstance(stmt, ast.With):
        items = []
        for item in stmt.items:
            item_doc = _expr_doc(item.context_expr, text_indent)
            if item.optional_vars is not None:
                item_doc += TextDoc(f" as {ast.unparse(item.optional_vars)}")
            items.append(item_doc)
        return GroupDoc(
            TextDoc("with ")
            + NestDoc(_join_docs(items, TextDoc(",") + BreakDoc(" ")), text_indent)
            + TextDoc(":")
        )
    if isinstance(stmt, ast.For):
        return GroupDoc(
            TextDoc(f"for {ast.unparse(stmt.target)} in ")
            + NestDoc(_expr_doc(stmt.iter, text_indent), text_indent)
            + TextDoc(":")
        )
    if isinstance(stmt, ast.If):
        return GroupDoc(
            TextDoc("if ")
            + NestDoc(_expr_doc(stmt.test, text_indent), text_indent)
            + TextDoc(":")
        )
    if isinstance(stmt, ast.While):
        return GroupDoc(
            TextDoc("while ")
            + NestDoc(_expr_doc(stmt.test, text_indent), text_indent)
            + TextDoc(":")
        )
    return TextDoc(source)


def _keyword_expr_doc(keyword: str, expr: AbstractDoc, text_indent: int) -> AbstractDoc:
    return GroupDoc(TextDoc(f"{keyword} ") + expr)


def _assign_doc(target: str, value: AbstractDoc, text_indent: int) -> AbstractDoc:
    return GroupDoc(TextDoc(f"{target} = ") + value)


def _expr_doc(node: ast.AST, text_indent: int) -> AbstractDoc:
    if isinstance(node, ast.Constant):
        if node.value is Ellipsis:
            return TextDoc("...")
        return TextDoc(repr(node.value))
    if isinstance(node, ast.Name):
        return TextDoc(node.id)
    if isinstance(node, ast.Attribute):
        return _expr_doc(node.value, text_indent) + TextDoc(f".{node.attr}")
    if isinstance(node, ast.Call):
        args = [_expr_doc(arg, text_indent) for arg in node.args]
        args.extend(_keyword_doc(keyword, text_indent) for keyword in node.keywords)
        return _call_doc(_expr_doc(node.func, text_indent), args, text_indent)
    if isinstance(node, ast.Subscript):
        return GroupDoc(
            _expr_doc(node.value, text_indent)
            + TextDoc("[")
            + _slice_doc(node.slice, text_indent)
            + TextDoc("]")
        )
    if isinstance(node, ast.Tuple):
        return _delimited_doc(
            TextDoc("("),
            _expr_list_docs(node.elts, text_indent),
            TextDoc(")"),
            text_indent,
            trailing_comma=len(node.elts) == 1,
        )
    if isinstance(node, ast.List):
        return _delimited_doc(
            TextDoc("["),
            _expr_list_docs(node.elts, text_indent),
            TextDoc("]"),
            text_indent,
        )
    if isinstance(node, ast.Set):
        return _delimited_doc(
            TextDoc("{"),
            _expr_list_docs(node.elts, text_indent),
            TextDoc("}"),
            text_indent,
        )
    if isinstance(node, ast.Dict):
        pairs = []
        for key, value in zip(node.keys, node.values):
            if key is None:
                pairs.append(TextDoc("**") + _expr_doc(value, text_indent))
            else:
                pairs.append(
                    _expr_doc(key, text_indent)
                    + TextDoc(": ")
                    + _expr_doc(value, text_indent)
                )
        return _delimited_doc(TextDoc("{"), pairs, TextDoc("}"), text_indent)
    if isinstance(node, ast.BinOp):
        return _binary_doc(
            _expr_doc(node.left, text_indent),
            _binop_text(node.op),
            _expr_doc(node.right, text_indent),
            text_indent,
        )
    if isinstance(node, ast.UnaryOp):
        op = _unaryop_text(node.op)
        operand = _expr_doc(node.operand, text_indent)
        if op == "not ":
            return GroupDoc(TextDoc(op) + operand)
        return GroupDoc(TextDoc(op) + operand)
    if isinstance(node, ast.Compare):
        parts = [_expr_doc(node.left, text_indent)]
        for cmp_op, comparator in zip(node.ops, node.comparators):
            parts.append(TextDoc(_cmpop_text(cmp_op)))
            parts.append(_expr_doc(comparator, text_indent))
        return GroupDoc(
            TextDoc("(")
            + NestDoc(_join_docs(parts, BreakDoc(" ")), text_indent)
            + TextDoc(")")
        )
    if isinstance(node, ast.BoolOp):
        op = " and " if isinstance(node.op, ast.And) else " or "
        return GroupDoc(
            TextDoc("(")
            + NestDoc(
                _join_docs(_expr_list_docs(node.values, text_indent), BreakDoc(op)),
                text_indent,
            )
            + TextDoc(")")
        )
    if isinstance(node, ast.IfExp):
        return GroupDoc(
            _expr_doc(node.body, text_indent)
            + NestDoc(
                BreakDoc(" ")
                + TextDoc("if ")
                + _expr_doc(node.test, text_indent)
                + TextDoc(" else ")
                + _expr_doc(node.orelse, text_indent),
                text_indent,
            )
        )
    if isinstance(node, ast.Lambda):
        return _lambda_doc(node, text_indent)
    if isinstance(node, ast.Starred):
        return TextDoc("*") + _expr_doc(node.value, text_indent)
    return TextDoc(ast.unparse(node))


def _slice_doc(node: ast.AST, text_indent: int) -> AbstractDoc:
    if isinstance(node, ast.Slice):
        lower = (
            _expr_doc(node.lower, text_indent)
            if node.lower is not None
            else TextDoc("")
        )
        upper = (
            _expr_doc(node.upper, text_indent)
            if node.upper is not None
            else TextDoc("")
        )
        step = _expr_doc(node.step, text_indent) if node.step is not None else None
        if step is None:
            return lower + TextDoc(":") + upper
        return lower + TextDoc(":") + upper + TextDoc(":") + step
    return _expr_doc(node, text_indent)


def _lambda_doc(node: ast.Lambda, text_indent: int) -> AbstractDoc:
    params = ast.unparse(node.args)
    return GroupDoc(
        TextDoc("(")
        + NestDoc(
            TextDoc(f"lambda {params}:")
            + NestDoc(
                BreakDoc(" ")
                + TextDoc("(")
                + NestDoc(_expr_doc(node.body, text_indent), text_indent)
                + TextDoc(")"),
                text_indent,
            ),
            text_indent,
        )
        + TextDoc(")")
    )


def _keyword_doc(keyword: ast.keyword, text_indent: int) -> AbstractDoc:
    value = _expr_doc(keyword.value, text_indent)
    if keyword.arg is None:
        return TextDoc("**") + value
    return TextDoc(f"{keyword.arg}=") + value


def _call_doc(
    func: AbstractDoc, args: list[AbstractDoc], text_indent: int
) -> AbstractDoc:
    return func + _delimited_doc(TextDoc("("), args, TextDoc(")"), text_indent)


def _delimited_doc(
    open_doc: AbstractDoc,
    items: list[AbstractDoc],
    close_doc: AbstractDoc,
    text_indent: int,
    *,
    trailing_comma: bool = False,
) -> AbstractDoc:
    if not items:
        return open_doc + close_doc
    body = _join_docs(items, TextDoc(",") + BreakDoc(" "))
    if trailing_comma:
        body += TextDoc(",")
    return GroupDoc(
        open_doc + NestDoc(BreakDoc("") + body, text_indent) + BreakDoc("") + close_doc
    )


def _binary_doc(
    left: AbstractDoc, op: str, right: AbstractDoc, text_indent: int
) -> AbstractDoc:
    return GroupDoc(
        TextDoc("(")
        + left
        + NestDoc(BreakDoc(" ") + TextDoc(op) + BreakDoc(" ") + right, text_indent)
        + TextDoc(")")
    )


def _expr_list_docs(nodes: list[ast.expr], text_indent: int) -> list[AbstractDoc]:
    return [_expr_doc(node, text_indent) for node in nodes]


def _join_docs(docs: list[AbstractDoc], sep: AbstractDoc) -> AbstractDoc:
    if not docs:
        return TextDoc("")
    result = docs[0]
    for doc in docs[1:]:
        result += sep + doc
    return result


def _binop_text(op: ast.operator) -> str:
    if isinstance(op, ast.Add):
        return "+"
    if isinstance(op, ast.Sub):
        return "-"
    if isinstance(op, ast.Mult):
        return "*"
    if isinstance(op, ast.Div):
        return "/"
    if isinstance(op, ast.FloorDiv):
        return "//"
    if isinstance(op, ast.Mod):
        return "%"
    if isinstance(op, ast.Pow):
        return "**"
    if isinstance(op, ast.BitOr):
        return "|"
    if isinstance(op, ast.BitAnd):
        return "&"
    if isinstance(op, ast.BitXor):
        return "^"
    if isinstance(op, ast.LShift):
        return "<<"
    if isinstance(op, ast.RShift):
        return ">>"
    return ast.unparse(ast.BinOp(ast.Constant(1), op, ast.Constant(1)))[1:-1]


def _unaryop_text(op: ast.unaryop) -> str:
    if isinstance(op, ast.Not):
        return "not "
    if isinstance(op, ast.Invert):
        return "~"
    if isinstance(op, ast.USub):
        return "-"
    if isinstance(op, ast.UAdd):
        return "+"
    return ""


def _cmpop_text(op: ast.cmpop) -> str:
    if isinstance(op, ast.Eq):
        return "=="
    if isinstance(op, ast.NotEq):
        return "!="
    if isinstance(op, ast.Lt):
        return "<"
    if isinstance(op, ast.LtE):
        return "<="
    if isinstance(op, ast.Gt):
        return ">"
    if isinstance(op, ast.GtE):
        return ">="
    if isinstance(op, ast.Is):
        return "is"
    if isinstance(op, ast.IsNot):
        return "is not"
    if isinstance(op, ast.In):
        return "in"
    if isinstance(op, ast.NotIn):
        return "not in"
    return ast.unparse(ast.Compare(ast.Constant(1), [op], [ast.Constant(2)]))[2:-2]


def _row_fields(row: Any) -> list[tuple[str, Any]]:
    if not isinstance(row, dict):
        return []
    return [
        (str(field.get("fieldName")), field.get("fieldType"))
        for field in row.get("fields", [])
        if isinstance(field, dict)
    ]


def _type_key(typ: Any) -> str:
    def strip_ids(value: Any) -> Any:
        if isinstance(value, dict):
            kind = value.get("kind")
            if kind == "empty":
                return {"kind": "empty"}
            if kind == "row":
                fields = [
                    strip_ids(field)
                    for field in sorted(
                        value.get("fields", []),
                        key=lambda item: (
                            str(item.get("fieldName")) if isinstance(item, dict) else ""
                        ),
                    )
                ]
                return {"kind": "row", "fields": fields}
            if kind == "tup" and isinstance(value.get("fields"), dict):
                fields = value["fields"]
                if fields.get("kind") == "empty":
                    value = {
                        **value,
                        "fields": {"kind": "row", "fields": [], "other": fields},
                    }
            return {k: strip_ids(v) for k, v in sorted(value.items()) if k != "id"}
        if isinstance(value, list):
            return [strip_ids(v) for v in value]
        return value

    return json.dumps(strip_ids(typ), sort_keys=True)
