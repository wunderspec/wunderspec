"""Apalache integration implementation for Wunderspec."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Literal

from wunderspec.api import (
    APALACHE_URL,
    CACHE_APALACHE_JAR,
    REPO_APALACHE_JAR,
    ActionDefs,
    ApalacheRequest,
    ApalacheResult,
    Node,
    Reporter,
    _fatal,
    _indexed_path,
    _ItfNdjsonSink,
    _print_trace,
    _resolve_instance_params,
    build_action_ast,
    build_expr_ast,
    find_state_classes,
    get_definition,
    is_action_function,
    load_module,
    to_camel_case,
)
from wunderspec.ast.sorts import BoolSort, TemporalSort
from wunderspec.interpreter import value
from wunderspec.itf_trace import itf_trace_json_line, read_itf_trace, write_itf_trace
from wunderspec.lang import Not
from wunderspec.tla import to_tla, to_tla_instance


def _resolve_apalache_jar(request: ApalacheRequest, reporter: Reporter) -> Path:
    if request.jar is not None:
        jar = Path(request.jar)
        if not jar.exists():
            _fatal(f"apalache.jar not found: {jar}")
        return jar

    env_jar = os.environ.get("WUNDERSPEC_APALACHE_JAR")
    if env_jar:
        jar = Path(env_jar)
        if not jar.exists():
            _fatal(f"WUNDERSPEC_APALACHE_JAR does not exist: {jar}")
        return jar

    if REPO_APALACHE_JAR.exists():
        return REPO_APALACHE_JAR

    if CACHE_APALACHE_JAR.exists():
        return CACHE_APALACHE_JAR

    return _download_apalache_jar(reporter)


def _download_apalache_jar(reporter: Reporter) -> Path:
    """Download the Apalache release tarball and cache the bundled jar."""
    reporter.info(f"Downloading Apalache from {APALACHE_URL}")
    CACHE_APALACHE_JAR.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wunderspec-apalache-") as tmp:
        archive = Path(tmp) / "apalache.tgz"
        try:
            urllib.request.urlretrieve(APALACHE_URL, archive)
        except Exception as e:
            _fatal(f"Failed to download Apalache: {e}")
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(tmp)
        except Exception as e:
            _fatal(f"Failed to extract Apalache archive: {e}")
        jars = sorted(Path(tmp).glob("apalache*/lib/apalache.jar"))
        if not jars:
            _fatal("apalache.jar not found inside the downloaded Apalache archive")
        shutil.copy(jars[0], CACHE_APALACHE_JAR)
    return CACHE_APALACHE_JAR


def _apalache_output_dir(request: ApalacheRequest) -> tuple[Path, bool]:
    if request.out_dir is not None:
        out_dir = Path(request.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir, False
    return Path(tempfile.mkdtemp(prefix="wunderspec-apalache-")), True


def _find_counterexample_itfs(run_dir: Path) -> list[Path]:
    """Return Apalache's counterexample ITF files in ``violationN`` order."""

    def _violation_index(path: Path) -> int:
        match = re.search(r"violation(\d+)", path.name)
        return int(match.group(1)) if match else 0

    return sorted(run_dir.glob("violation*.itf.json"), key=_violation_index)


def run_apalache(request: ApalacheRequest, reporter: Reporter) -> ApalacheResult:
    """Generate TLA+ for a Wunderspec instance and run Apalache."""
    if request.property is None:
        _fatal("--property is required")
    if request.max_steps < 1:
        _fatal("--max-steps must be at least 1")
    if request.simulate and request.max_samples < 1:
        _fatal("--max-samples must be at least 1")
    if request.max_findings < 1:
        _fatal("--max-findings must be >= 1")
    if request.max_memory is not None and not re.fullmatch(
        r"\d+[gGmM]", request.max_memory
    ):
        _fatal(
            "--max-memory must be <int>G or <int>M, e.g. 4G or 2048M, "
            f"got: {request.max_memory}"
        )

    source_path = Path(request.spec)
    if not source_path.exists():
        _fatal(f"File not found: {source_path}")
    if not source_path.suffix == ".py":
        _fatal(f"Expected a .py file, got: {source_path}")

    reporter.info(f"Loading module: {source_path}")
    module = load_module(source_path)
    state_classes = find_state_classes(module)
    if len(state_classes) == 0:
        _fatal("No @state-decorated class found in the module")
    if len(state_classes) > 1:
        class_names = ", ".join(c.__name__ for c in state_classes)
        _fatal(
            f"Multiple @state-decorated classes found: {class_names}\n"
            "       Only one state class per module is supported"
        )
    state_cls = state_classes[0]
    reporter.info(f"Found state class: {state_cls.__name__}")

    init_func = get_definition(module, request.init)
    step_func = get_definition(module, request.step)
    if not is_action_function(init_func):
        _fatal(f"--init '{request.init}' is not an action")
    if not getattr(init_func, "_is_init", False):
        _fatal(f"--init '{request.init}' must be decorated with @action(init=True)")
    if not is_action_function(step_func):
        _fatal(f"--step '{request.step}' is not an action")

    property_kind: Literal["invariant", "temporal", "example"]
    property_func_name = request.property
    property_func = get_definition(module, property_func_name)
    if getattr(property_func, "_is_invariant", False):
        property_kind = "invariant"
    elif getattr(property_func, "_is_temporal", False):
        property_kind = "temporal"
    elif getattr(property_func, "_is_example", False):
        property_kind = "example"
    else:
        _fatal(
            f"--property '{property_func_name}' is not annotated; "
            f"mark it with @invariant, @example, or @temporal"
        )

    if property_kind == "temporal" and request.simulate:
        _fatal("--simulate does not support temporal properties")

    nodes: dict[str, Node] = {}
    all_extracted_actions: ActionDefs = {}
    init_op = to_camel_case(request.init)
    step_op = to_camel_case(request.step)
    property_op = to_camel_case(property_func_name)

    for def_name, func, is_init in (
        (request.init, init_func, True),
        (request.step, step_func, False),
    ):
        reporter.info(f"Building AST for action: {def_name}")
        try:
            node, extracted = build_action_ast(state_cls, func)
        except Exception as e:
            _fatal(f"Error building AST for action '{def_name}': {e}")
        all_extracted_actions.update(extracted)
        nodes[to_camel_case(def_name)] = node
        if is_init:
            init_op = to_camel_case(def_name)

    reporter.info(f"Building AST for {property_kind}: {property_func_name}")
    # An @example asserts reachability; Apalache reports a counterexample to the
    # negated predicate as the witnessing trace (same trick as `wunderspec tlc`).
    expr_func = (
        (lambda s, _f=property_func: Not(_f(s)))
        if property_kind == "example"
        else property_func
    )
    try:
        property_node = build_expr_ast(state_cls, expr_func)
    except Exception as e:
        _fatal(f"Error building AST for expression '{property_func_name}': {e}")

    if property_kind in ("invariant", "example") and not isinstance(
        property_node.sort, BoolSort
    ):
        _fatal(
            f"--property '{property_func_name}' must return a Boolean expression, "
            f"got {property_node.sort}"
        )
    if property_kind == "temporal" and not isinstance(property_node.sort, TemporalSort):
        _fatal(
            f"--property '{property_func_name}' must return a temporal expression, "
            f"got {property_node.sort}"
        )
    nodes[property_op] = property_node

    fixed_params = _resolve_instance_params(module, state_cls, request.instance)
    out_dir, is_temp_dir = _apalache_output_dir(request)
    base_module = source_path.stem
    wrapper_module = f"MC_{base_module}"
    base_path = out_dir / f"{base_module}.tla"
    wrapper_path = out_dir / f"{wrapper_module}.tla"
    # Resolved to an absolute path: Apalache runs with cwd=out_dir, so a relative
    # --run-dir would otherwise be nested under out_dir a second time.
    run_dir = (out_dir / "apalache-out").resolve()

    sink = _ItfNdjsonSink(request.out_itf)
    try:
        reporter.info(f"Generating TLA+ module: {base_module}")
        base_tla = to_tla(
            state_cls,
            base_module,
            extracted_actions=all_extracted_actions,
            init_ops={init_op},
            text_width=79,
            text_indent=4,
            **nodes,
        )
        base_path.write_text(base_tla)

        reporter.info(f"Generating Apalache wrapper module: {wrapper_module}")
        wrapper_tla = to_tla_instance(
            state_cls,
            wrapper_module,
            base_module,
            fixed_params,
            include_behavior_spec=True,
            init_op=init_op,
            step_op=step_op,
        )
        wrapper_path.write_text(wrapper_tla)

        jar = _resolve_apalache_jar(request, reporter)
        subcommand = "simulate" if request.simulate else "check"
        property_flag = "--temporal" if property_kind == "temporal" else "--inv"
        command = [request.java]
        if request.max_memory:
            # -Xmx must precede -jar: JVM options after -jar are passed to the
            # application, not the JVM.
            command.append(f"-Xmx{request.max_memory}")
        command += [
            "-jar",
            str(jar),
            subcommand,
            f"--out-dir={run_dir}",
            f"--run-dir={run_dir}",
            f"--init={init_op}",
            f"--next={step_op}",
            f"{property_flag}={property_op}",
            f"--length={request.max_steps}",
        ]
        if request.simulate:
            command.append(f"--max-run={request.max_samples}")
        else:
            command.append(f"--max-error={request.max_findings}")
            if request.max_findings > 1:
                # Apalache needs a state view to enumerate several distinct
                # counterexamples; the wrapper's Vars tuple is the full state.
                command.append("--view=Vars")
        command.append(wrapper_path.name)
        reporter.info("Running Apalache")
        try:
            completed = subprocess.run(
                command,
                cwd=out_dir,
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            _fatal(f"Java executable not found: {request.java}")

        if completed.stdout:
            reporter.out(completed.stdout.rstrip())
        if completed.stderr:
            reporter.hint(completed.stderr.rstrip())

        artifacts = [base_path.name, wrapper_path.name]
        counterexamples = _find_counterexample_itfs(run_dir)[: request.max_findings]
        found_any = bool(counterexamples)

        if counterexamples:
            state_sorts = {
                name: getattr(state_cls, name).sort for name in state_cls._vars
            }
            param_values = {name: value(node) for name, node in fixed_params.items()}
            itf_base = out_dir / f"{wrapper_module}.itf.json"
            written = 0
            for source_itf in counterexamples:
                try:
                    document = json.loads(source_itf.read_text())
                    trace = read_itf_trace(
                        document, state_sorts=state_sorts, params=param_values
                    )
                except Exception as e:
                    reporter.warn(
                        f"Could not parse Apalache counterexample trace "
                        f"{source_itf.name}: {e}"
                    )
                    continue
                if not trace:
                    continue
                meta: dict[str, Any] = {
                    "source": "wunderspec with-apalache",
                    "spec": str(source_path),
                    "instance": request.instance,
                    "property": property_func_name,
                    "property_kind": property_kind,
                    **(
                        {"violation_step": len(trace) - 1}
                        if property_kind == "invariant"
                        else (
                            {"example_step": len(trace) - 1}
                            if property_kind == "example"
                            else {}
                        )
                    ),
                }
                itf_path = _indexed_path(itf_base, written)
                write_itf_trace(
                    itf_path,
                    trace,
                    meta=meta,
                    params=list(state_cls._params),
                    vars=list(state_cls._vars),
                )
                artifacts.append(itf_path.name)
                reporter.info(f"Wrote ITF trace to {itf_path}")
                if sink.enabled:
                    sink.emit(
                        itf_trace_json_line(
                            trace,
                            meta=meta,
                            params=list(state_cls._params),
                            vars=list(state_cls._vars),
                        )
                    )
                if not request.no_print_trace:
                    _print_trace(trace, reporter)
                written += 1

        outcome_kind: Literal[
            "checked", "violation", "example_found", "example_not_found"
        ]
        if property_kind == "example":
            if found_any:
                outcome_kind = "example_found"
                reporter.success("Example found")
                reporter.info(f"Generated files kept in: {out_dir}")
            elif completed.returncode == 0:
                outcome_kind = "example_not_found"
                reporter.info("Example not reachable within --max-steps")
                if is_temp_dir and not request.keep_files:
                    shutil.rmtree(out_dir, ignore_errors=True)
            else:
                outcome_kind = "violation"
                reporter.error(f"Apalache failed with exit code {completed.returncode}")
                reporter.info(f"Generated files kept in: {out_dir}")
        elif found_any:
            outcome_kind = "violation"
            reporter.error("Apalache found a counterexample")
            reporter.info(f"Generated files kept in: {out_dir}")
        elif completed.returncode == 0:
            outcome_kind = "checked"
            reporter.success("Apalache completed successfully")
            if is_temp_dir and not request.keep_files:
                shutil.rmtree(out_dir, ignore_errors=True)
        else:
            outcome_kind = "violation"
            reporter.error(f"Apalache failed with exit code {completed.returncode}")
            reporter.info(f"Generated files kept in: {out_dir}")

        return ApalacheResult(
            returncode=completed.returncode,
            out_dir=str(out_dir),
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
            artifacts=artifacts,
            outcome_kind=outcome_kind,
        )
    except Exception:
        if is_temp_dir and not request.keep_files:
            shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        sink.close()
