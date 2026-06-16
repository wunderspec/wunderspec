"""Shared AST helpers for wunderspec example scripts."""

import ast
import re
from pathlib import Path
from typing import Any, TypedDict

import yaml  # type: ignore[import-untyped]


class ExampleConfigRow(TypedDict):
    file: str
    instances: list[str]
    invariants_auto: bool
    invariants: list[str]
    examples_auto: bool
    examples: list[str]
    example_run_seeds: dict[str, int]
    example_run_max_samples: dict[str, int]
    timeout: int | None


def _find_decorated_names(spec_path: Path, decorator_id: str) -> list[str]:
    """Return names of functions decorated with ``@decorator_id`` in *spec_path*.

    Uses AST inspection so the module is never imported (avoids dependency
    issues with spec-local imports such as ``from simple_ponzi import *``).
    """
    tree = ast.parse(spec_path.read_text())
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id == decorator_id:
                    names.append(node.name)
    return names


def find_invariant_names(spec_path: Path) -> set[str]:
    """Return the names of functions decorated with ``@invariant`` in *spec_path*."""
    return set(_find_decorated_names(spec_path, "invariant"))


def find_example_names(spec_path: Path) -> set[str]:
    """Return the names of functions decorated with ``@example`` in *spec_path*."""
    return set(_find_decorated_names(spec_path, "example"))


def find_coverage_names(spec_path: Path) -> set[str]:
    """Return the names of functions decorated with ``@coverage`` in *spec_path*."""
    return set(_find_decorated_names(spec_path, "coverage"))


def find_init_action_name(spec_path: Path) -> str:
    """Return the name of the ``@action(init=True)`` function, or ``"init"``."""
    tree = ast.parse(spec_path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Call):
                    func = decorator.func
                    if isinstance(func, ast.Name) and func.id == "action":
                        for kw in decorator.keywords:
                            if (
                                kw.arg == "init"
                                and isinstance(kw.value, ast.Constant)
                                and kw.value.value is True
                            ):
                                return node.name
    return "init"


_STEP_NAME_RE = re.compile(r"^(Next|step|Step|.*_next|.*Next)$")


def find_step_action_name(spec_path: Path, init_name: str) -> str:
    """Return the name of the bare ``@action`` (non-init, single-param) function.

    Only considers names matching: Next, step, Step, *_next, *Next.
    Falls back to ``"step"`` if none is found.
    """
    tree = ast.parse(spec_path.read_text())
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name == init_name:
            continue
        if not _STEP_NAME_RE.match(node.name):
            continue
        if len(node.args.args) != 1:
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == "action":
                return node.name
    return "step"


def load_examples_config(config_path: Path) -> list[ExampleConfigRow]:
    """Load examples YAML config.

    Expected shape:

    examples:
      - file: spec.py
        instances: inst_a inst_b   # or a YAML list
        invariants: inv_x inv_y    # or a YAML list; omit for auto-discovery
        examples: ex_a ex_b        # or a YAML list; omit for auto-discovery
        example_run_seeds:         # optional mapping example -> integer seed
          ex_b: 123
        example_run_max_samples:   # optional mapping example -> integer
          ex_b: 300
        timeout: 30                # optional wall-clock cap (seconds) per run
    """

    def _to_tokens(raw: Any, key: str, file_label: str) -> list[str]:
        if raw is None:
            return []
        if isinstance(raw, str):
            return raw.split()
        if isinstance(raw, list) and all(isinstance(x, str) for x in raw):
            return raw
        raise ValueError(
            f"{config_path}: '{key}' in '{file_label}' must be a string or list[str]"
        )

    def _to_optional_int(raw: Any, key: str, file_label: str) -> int | None:
        if raw is None:
            return None
        if isinstance(raw, int) and not isinstance(raw, bool):
            return raw
        raise ValueError(f"{config_path}: '{key}' in '{file_label}' must be an integer")

    def _to_str_int_map(raw: Any, key: str, file_label: str) -> dict[str, int]:
        if raw is None:
            return {}
        if not isinstance(raw, dict):
            raise ValueError(
                f"{config_path}: '{key}' in '{file_label}' must be a mapping[str, int]"
            )
        out: dict[str, int] = {}
        for k, v in raw.items():
            if not isinstance(k, str) or not isinstance(v, int):
                raise ValueError(
                    f"{config_path}: '{key}' in '{file_label}' must be a mapping[str, int]"
                )
            out[k] = v
        return out

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{config_path}: root must be a mapping")
    examples = data.get("examples")
    if not isinstance(examples, list) or not examples:
        raise ValueError(f"{config_path}: 'examples' must be a non-empty list")

    rows: list[ExampleConfigRow] = []
    for entry in examples:
        if not isinstance(entry, dict):
            raise ValueError(f"{config_path}: each example entry must be a mapping")
        spec_file = entry.get("file")
        if not isinstance(spec_file, str) or not spec_file.strip():
            raise ValueError(
                f"{config_path}: each example must define non-empty 'file'"
            )
        spec_file = spec_file.strip()
        rows.append(
            {
                "file": spec_file,
                "instances": _to_tokens(entry.get("instances"), "instances", spec_file),
                "invariants_auto": "invariants" not in entry,
                "invariants": _to_tokens(
                    entry.get("invariants"), "invariants", spec_file
                ),
                "examples_auto": "examples" not in entry,
                "examples": _to_tokens(entry.get("examples"), "examples", spec_file),
                "example_run_seeds": _to_str_int_map(
                    entry.get("example_run_seeds"),
                    "example_run_seeds",
                    spec_file,
                ),
                "example_run_max_samples": _to_str_int_map(
                    entry.get("example_run_max_samples"),
                    "example_run_max_samples",
                    spec_file,
                ),
                "timeout": _to_optional_int(entry.get("timeout"), "timeout", spec_file),
            }
        )

    return rows
