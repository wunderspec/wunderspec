"""TLC integration implementation for Wunderspec."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path
from typing import Any, Literal

from wunderspec.api import (
    CACHE_TLA2TOOLS_JAR,
    REPO_TLA2TOOLS_JAR,
    TLA2TOOLS_URL,
    ActionDefs,
    Node,
    Reporter,
    TlcRequest,
    TlcResult,
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
from wunderspec.itf_trace import itf_trace_json_line, write_itf_trace
from wunderspec.lang import Not
from wunderspec.tla import to_tla, to_tla_instance
from wunderspec.tlc_trace import TlcTrace, extract_tlc_trace_blocks, parse_tlc_traces

SUPPORT_MODULE_URLS = {
    "Variants.tla": "https://raw.githubusercontent.com/apalache-mc/apalache/main/src/tla/Variants.tla",
    "Apalache.tla": "https://raw.githubusercontent.com/apalache-mc/apalache/main/src/tla/Apalache.tla",
}


def _resolve_tla2tools_jar(request: TlcRequest, reporter: Reporter) -> Path:
    if request.jar is not None:
        jar = Path(request.jar)
        if not jar.exists():
            _fatal(f"tla2tools.jar not found: {jar}")
        return jar

    env_jar = os.environ.get("WUNDERSPEC_TLA2TOOLS_JAR")
    if env_jar:
        jar = Path(env_jar)
        if not jar.exists():
            _fatal(f"WUNDERSPEC_TLA2TOOLS_JAR does not exist: {jar}")
        return jar

    if REPO_TLA2TOOLS_JAR.exists():
        return REPO_TLA2TOOLS_JAR

    if CACHE_TLA2TOOLS_JAR.exists():
        return CACHE_TLA2TOOLS_JAR

    reporter.info(f"Downloading tla2tools.jar from {TLA2TOOLS_URL}")
    CACHE_TLA2TOOLS_JAR.parent.mkdir(parents=True, exist_ok=True)
    try:
        urllib.request.urlretrieve(TLA2TOOLS_URL, CACHE_TLA2TOOLS_JAR)
    except Exception as e:
        _fatal(f"Failed to download tla2tools.jar: {e}")
    return CACHE_TLA2TOOLS_JAR


def _extends_module(tla_text: str, module: str) -> bool:
    """Return whether a TLA+ module text directly extends ``module``."""
    for line in tla_text.splitlines():
        if not line.startswith("EXTENDS "):
            continue
        modules = [item.strip() for item in line.removeprefix("EXTENDS ").split(",")]
        if module in modules:
            return True
    return False


def _ensure_support_modules(
    out_dir: Path, reporter: Reporter, *tla_texts: str
) -> list[str]:
    """Ensure generated specs can resolve non-standard helper modules."""
    artifacts: list[str] = []
    repo_root = Path(__file__).resolve().parent.parent
    for filename, url in SUPPORT_MODULE_URLS.items():
        module = filename.removesuffix(".tla")
        if not any(_extends_module(tla_text, module) for tla_text in tla_texts):
            continue
        dst = out_dir / filename
        artifacts.append(filename)
        if dst.exists():
            continue
        src = repo_root / filename
        if src.exists():
            shutil.copy(src, dst)
            continue
        reporter.info(f"Downloading {filename} from {url}")
        try:
            urllib.request.urlretrieve(url, dst)
        except Exception as e:
            _fatal(f"Failed to download {filename}: {e}")
    return artifacts


def _tlc_output_dir(request: TlcRequest) -> tuple[Path, bool]:
    if request.out_dir is not None:
        out_dir = Path(request.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        return out_dir, False
    return Path(tempfile.mkdtemp(prefix="wunderspec-tlc-")), True


def _write_tlc_cfg(
    path: Path,
    *,
    property_kind: Literal["invariant", "temporal", "example"],
    property_name: str,
) -> None:
    # An @example is emitted as its negation, so it is checked as an INVARIANT.
    directive = "PROPERTY" if property_kind == "temporal" else "INVARIANT"
    path.write_text(f"SPECIFICATION Spec\n{directive} {property_name}\n")


def _is_tlc_progress_line(line: str) -> bool:
    return line.lstrip().startswith("Progress:")


def _without_streamed_tlc_progress(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not _is_tlc_progress_line(line)
    ).strip()


def _run_tlc_process(
    command: list[str],
    *,
    cwd: Path,
    reporter: Reporter,
) -> subprocess.CompletedProcess[str]:
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []

    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    def read_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            stdout_parts.append(line)
            if _is_tlc_progress_line(line):
                reporter.out(line.rstrip("\n"))

    def read_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_parts.append(line)

    stdout_thread = threading.Thread(target=read_stdout)
    stderr_thread = threading.Thread(target=read_stderr)
    stdout_thread.start()
    stderr_thread.start()
    returncode = proc.wait()
    stdout_thread.join()
    stderr_thread.join()

    return subprocess.CompletedProcess(
        args=command,
        returncode=returncode,
        stdout="".join(stdout_parts),
        stderr="".join(stderr_parts),
    )


def run_tlc(request: TlcRequest, reporter: Reporter) -> TlcResult:
    """Generate TLA+ for a Wunderspec instance and run TLC."""
    if request.property is None:
        _fatal("--property is required")
    if request.workers < 1:
        _fatal("--workers must be at least 1")
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
    # An @example asserts reachability; TLC has no native reachability check, so we
    # emit the negated predicate as an INVARIANT and treat TLC's counterexample as
    # the witnessing trace (same trick as `wunderspec convert`).
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
    out_dir, is_temp_dir = _tlc_output_dir(request)
    base_module = source_path.stem
    wrapper_module = f"MC_{base_module}"
    base_path = out_dir / f"{base_module}.tla"
    wrapper_path = out_dir / f"{wrapper_module}.tla"
    cfg_path = out_dir / f"{wrapper_module}.cfg"

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

        reporter.info(f"Generating TLC wrapper module: {wrapper_module}")
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
        _write_tlc_cfg(cfg_path, property_kind=property_kind, property_name=property_op)
        support_artifacts = _ensure_support_modules(
            out_dir, reporter, base_tla, wrapper_tla
        )

        jar = _resolve_tla2tools_jar(request, reporter)
        command = [request.java, "-XX:+UseParallelGC"]
        if request.max_memory:
            # JVM heap option must precede the main class arguments.
            command.append(f"-Xmx{request.max_memory}")
        command += [
            "-cp",
            str(jar),
            "tlc2.TLC",
            "-cleanup",
            "-workers",
            str(request.workers),
            "-config",
            cfg_path.name,
        ]
        if request.simulate:
            command.append("-simulate")
        if request.max_findings > 1:
            # Keep checking past the first violation so TLC reports several
            # counterexamples (one error-trace block per finding).
            command.append("-continue")
        command.append(wrapper_path.name)
        reporter.info("Running TLC")
        try:
            completed = _run_tlc_process(
                command,
                cwd=out_dir,
                reporter=reporter,
            )
        except FileNotFoundError:
            _fatal(f"Java executable not found: {request.java}")

        traces: list[TlcTrace] = []
        raw_trace_texts: list[str] = []
        if completed.stdout:
            filtered_stdout = completed.stdout.rstrip()
            state_sorts = {
                name: getattr(state_cls, name).sort for name in state_cls._vars
            }
            param_values = {name: value(node) for name, node in fixed_params.items()}
            try:
                traces = parse_tlc_traces(
                    completed.stdout,
                    state_sorts=state_sorts,
                    params=param_values,
                )[: request.max_findings]
                raw_trace_texts = [t.raw_trace for t in traces]
                if traces:
                    filtered_stdout = traces[0].stdout_without_trace
            except Exception as e:
                raw_blocks, filtered_stdout = extract_tlc_trace_blocks(completed.stdout)
                raw_trace_texts = raw_blocks[: request.max_findings]
                reporter.warn(f"Could not parse TLC counterexample trace: {e}")
            output = _without_streamed_tlc_progress(filtered_stdout)
            if output:
                reporter.out(output)
        if completed.stderr:
            reporter.hint(completed.stderr.rstrip())

        artifacts = [base_path.name, wrapper_path.name, cfg_path.name]
        artifacts.extend(support_artifacts)

        raw_trace_base = out_dir / f"{wrapper_module}.tlc.trace"
        for i, raw_text in enumerate(raw_trace_texts):
            raw_trace_path = _indexed_path(raw_trace_base, i)
            raw_trace_path.write_text(raw_text + "\n")
            artifacts.append(raw_trace_path.name)
            reporter.info(f"Wrote raw TLC trace to {raw_trace_path}")

        itf_base = out_dir / f"{wrapper_module}.itf.json"
        for i, trace_result in enumerate(traces):
            meta: dict[str, Any] = {
                "source": "wunderspec with-tlc",
                "spec": str(source_path),
                "instance": request.instance,
                "property": property_func_name,
                "property_kind": property_kind,
                **(
                    {"violation_step": len(trace_result.trace) - 1}
                    if property_kind == "invariant"
                    else (
                        {"example_step": len(trace_result.trace) - 1}
                        if property_kind == "example"
                        else {}
                    )
                ),
            }
            itf_path = _indexed_path(itf_base, i)
            write_itf_trace(
                itf_path,
                trace_result.trace,
                meta=meta,
                params=list(state_cls._params),
                vars=list(state_cls._vars),
            )
            artifacts.append(itf_path.name)
            reporter.info(f"Wrote ITF trace to {itf_path}")
            if sink.enabled:
                sink.emit(
                    itf_trace_json_line(
                        trace_result.trace,
                        meta=meta,
                        params=list(state_cls._params),
                        vars=list(state_cls._vars),
                    )
                )
            _print_trace(trace_result.trace, reporter)
        # Key the outcome off whether a counterexample was found rather than the
        # exit code: with ``-continue`` TLC explores the whole state space and
        # exits 0 even when it reported violations.
        found = bool(traces) or bool(raw_trace_texts)
        outcome_kind: Literal[
            "checked", "violation", "example_found", "example_not_found"
        ]
        if property_kind == "example":
            # TLC violating the negated predicate means the example was reached.
            if found:
                outcome_kind = "example_found"
                reporter.success("Example found")
                reporter.info(f"Generated files kept in: {out_dir}")
            elif completed.returncode == 0:
                outcome_kind = "example_not_found"
                reporter.info("Example not reachable in the explored state space")
                if is_temp_dir and not request.keep_files:
                    shutil.rmtree(out_dir, ignore_errors=True)
            else:
                outcome_kind = "violation"
                reporter.error(f"TLC failed with exit code {completed.returncode}")
                reporter.info(f"Generated files kept in: {out_dir}")
        elif found:
            outcome_kind = "violation"
            reporter.error("TLC found a counterexample")
            reporter.info(f"Generated files kept in: {out_dir}")
        elif completed.returncode == 0:
            outcome_kind = "checked"
            reporter.success("TLC completed successfully")
            if is_temp_dir and not request.keep_files:
                shutil.rmtree(out_dir, ignore_errors=True)
        else:
            outcome_kind = "violation"
            reporter.error(f"TLC failed with exit code {completed.returncode}")
            reporter.info(f"Generated files kept in: {out_dir}")

        return TlcResult(
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
            # Keep files on TLC failures, but clean up unexpected generation errors.
            shutil.rmtree(out_dir, ignore_errors=True)
        raise
    finally:
        sink.close()
