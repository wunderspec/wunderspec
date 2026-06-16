"""Command-line interface for Wunderspec."""

from __future__ import annotations

import argparse
import importlib.metadata
import os
import platform
import shutil
import signal
import sys
from typing import TextIO

from wunderspec import api
from wunderspec._edition import feature_message
from wunderspec.trace_output import DEFAULT_TRACE_WIDTH, TraceStyle, print_trace


class Colors:
    """ANSI color codes for terminal output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"

    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    BLUE = "\033[34m"


class ConsoleReporter:
    """Reporter implementation for terminal output."""

    def __init__(self) -> None:
        self.use_color = True
        self.trace_width = DEFAULT_TRACE_WIDTH
        # Where human-readable output goes. Defaults to the current stdout and is
        # redirected to stderr when NDJSON is streamed to stdout (--out-itf -) so
        # stdout carries only NDJSON.
        self._out_file: TextIO | None = None

    @property
    def out_file(self) -> TextIO:
        return self._out_file if self._out_file is not None else sys.stdout

    @out_file.setter
    def out_file(self, stream: TextIO) -> None:
        self._out_file = stream

    def _color(self, code: str) -> str:
        return code if self.use_color else ""

    def info(self, msg: str) -> None:
        print(
            f"{self._color(Colors.CYAN)}info:{self._color(Colors.RESET)} {msg}",
            file=self.out_file,
        )

    def success(self, msg: str) -> None:
        print(
            f"{self._color(Colors.GREEN)}success:{self._color(Colors.RESET)} {msg}",
            file=self.out_file,
        )

    def warn(self, msg: str) -> None:
        print(
            f"{self._color(Colors.YELLOW)}warning:{self._color(Colors.RESET)} {msg}",
            file=self.out_file,
        )

    def error(self, msg: str) -> None:
        print(
            f"{self._color(Colors.RED)}error:{self._color(Colors.RESET)} {msg}",
            file=sys.stderr,
        )

    def hint(self, msg: str) -> None:
        print(msg, file=sys.stderr)

    def out(self, msg: str = "") -> None:
        print(msg, file=self.out_file)


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("spec", help="Spec file (e.g., examples/readers_writers.py)")
    parser.add_argument(
        "--instance",
        default=None,
        metavar="NAME",
        help="Name of an @instance factory to use as the state prototype.",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Use debug mode (slower but better errors)"
    )
    parser.add_argument(
        "--init", default="init", help="Init action name (default: init)"
    )
    parser.add_argument(
        "--step", default="step", help="Step action name (default: step)"
    )
    parser.add_argument(
        "--property",
        help="Property (@invariant or @example) to search for",
    )
    parser.add_argument(
        "--max-samples", type=int, default=1000, help="Max samples (default: 1000)"
    )
    parser.add_argument(
        "--max-steps", type=int, default=20, help="Max steps per trace (default: 20)"
    )
    parser.add_argument(
        "--bound",
        type=int,
        default=2**31 - 1,
        help="Bound for integer sampling (default: 2^31-1)",
    )
    parser.add_argument("--seed", type=int, default=None, help="Random seed")
    parser.add_argument(
        "--max-findings",
        type=int,
        default=1,
        help="Stop after this many violations or examples (default: 1)",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )


def _add_no_print_trace_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--no-print-trace",
        action="store_true",
        help="Suppress human-readable trace printing for findings",
    )


def _configure_colors(no_color: bool, reporter: ConsoleReporter) -> None:
    is_tty = reporter.out_file.isatty()
    reporter.use_color = not no_color and is_tty
    reporter.trace_width = (
        shutil.get_terminal_size(fallback=(DEFAULT_TRACE_WIDTH, 24)).columns
        if is_tty
        else DEFAULT_TRACE_WIDTH
    )


def _run_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    if args.out_itf == "-":
        reporter.out_file = sys.stderr
    _configure_colors(bool(args.no_color), reporter)
    result = api.run(
        api.RunRequest(
            spec=args.spec,
            instance=args.instance,
            debug=args.debug,
            init=args.init,
            step=args.step,
            property=args.property,
            max_samples=args.max_samples,
            max_steps=args.max_steps,
            bound=args.bound,
            seed=args.seed,
            max_findings=args.max_findings,
            out_itf=args.out_itf,
            no_progress=args.no_progress,
            coverage=args.coverage,
            timeout=args.timeout,
            best_trace=(None if args.best_trace is None else bool(args.best_trace)),
            no_print_trace=args.no_print_trace,
        ),
        reporter=reporter,
    )
    if result.outcome_kind == "violation":
        sys.exit(1)
    if result.outcome_kind == "example_found":
        sys.exit(2)


def _replay_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    _configure_colors(bool(args.no_color), reporter)
    result = api.replay(
        api.ReplayRequest(
            spec=args.spec,
            instance=args.instance,
            debug=args.debug,
            init=args.init,
            step=args.step,
            property=args.property,
            max_steps=args.max_steps,
            bound=args.bound,
            seed=args.seed,
            from_schedule=args.from_schedule,
            out_itf=args.out_itf,
            out_schedule=args.out_schedule,
        ),
        reporter=reporter,
    )
    if result.outcome_kind == "violation":
        sys.exit(1)
    if result.outcome_kind == "example_found":
        sys.exit(2)


def _explain_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    _configure_colors(bool(args.no_color), reporter)
    api.explain(api.ExplainRequest(trace=args.trace, bdd=args.bdd), reporter=reporter)


def _convert_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    _configure_colors(False, reporter)
    result = api.convert(
        api.ConvertRequest(
            source=args.source,
            output=args.output,
            defs=args.defs,
            instance=args.instance,
            text_width=args.text_width,
            text_indent=args.text_indent,
            main=args.main,
            quint=args.quint,
            run_seed=args.run_seed,
            run_samples=args.run_samples,
        ),
        reporter=reporter,
    )
    if result.error_count:
        sys.exit(1)


def _with_tlc_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    if args.out_itf == "-":
        reporter.out_file = sys.stderr
    _configure_colors(bool(args.no_color), reporter)
    result = api.tlc(
        api.TlcRequest(
            spec=args.spec,
            instance=args.instance,
            property=args.property,
            init=args.init,
            step=args.step,
            out_dir=args.out_dir,
            keep_files=args.keep_files,
            jar=args.jar,
            java=args.java,
            workers=args.workers,
            simulate=args.simulate,
            max_findings=args.max_findings,
            out_itf=args.out_itf,
            max_memory=args.max_memory,
            no_print_trace=args.no_print_trace,
        ),
        reporter=reporter,
    )
    if result.outcome_kind == "example_found":
        sys.exit(2)
    if result.outcome_kind == "example_not_found":
        sys.exit(0)
    if result.outcome_kind == "violation":
        # -continue makes TLC exit 0 even after reporting violations.
        sys.exit(result.returncode or 1)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _with_apalache_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    if args.out_itf == "-":
        reporter.out_file = sys.stderr
    _configure_colors(bool(args.no_color), reporter)
    result = api.apalache(
        api.ApalacheRequest(
            spec=args.spec,
            instance=args.instance,
            property=args.property,
            init=args.init,
            step=args.step,
            out_dir=args.out_dir,
            keep_files=args.keep_files,
            jar=args.jar,
            java=args.java,
            max_steps=args.max_steps,
            max_samples=args.max_samples,
            simulate=args.simulate,
            max_findings=args.max_findings,
            out_itf=args.out_itf,
            max_memory=args.max_memory,
            no_print_trace=args.no_print_trace,
        ),
        reporter=reporter,
    )
    if result.outcome_kind == "example_found":
        sys.exit(2)
    if result.outcome_kind == "example_not_found":
        sys.exit(0)
    if result.outcome_kind == "violation":
        sys.exit(result.returncode or 1)
    if result.returncode != 0:
        sys.exit(result.returncode)


def _check_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    if args.out_itf == "-":
        reporter.out_file = sys.stderr
    _configure_colors(bool(args.no_color), reporter)
    shuffle_enabled = not args.no_shuffle
    bound = args.bound if args.bound is not None else 2**31 - 1
    request = api.CheckRequest(
        spec=args.spec,
        instance=args.instance,
        init=args.init,
        step=args.step,
        property=args.property,
        max_steps=args.max_steps,
        bound=bound,
        no_progress=args.no_progress,
        timeout=args.timeout,
        no_shuffle=not shuffle_enabled,
        seed=args.seed,
        out_schedule=args.out_schedule,
        max_findings=args.max_findings,
        out_itf=args.out_itf,
    )
    result = api.check(request, reporter=reporter)
    if result.outcome_kind in ("violation", "example_found"):
        count = len(result.traces)
        noun = (
            "example"
            if result.outcome_kind == "example_found"
            else ("invariant violation")
        )
        if count == 1:
            header = f"{noun.capitalize()} found"
        else:
            header = f"Found {count} {noun}s"
        header += (
            f" ({result.produced_states} states produced, "
            f"{result.distinct_states} distinct)"
        )
        emit = reporter.info if result.outcome_kind == "example_found" else reporter.out
        emit(header)
        style = TraceStyle(color=reporter.use_color, width=reporter.trace_width)
        for i, trace in enumerate(result.traces):
            if count > 1:
                reporter.out(f"--- {noun.capitalize()} {i + 1} of {count} ---")
            if not args.no_print_trace:
                print_trace(trace, reporter, style=style)
            if i < len(result.schedule_paths):
                reporter.out(
                    f"Replay with: "
                    f"{api._replay_command_for_check(request, result.schedule_paths[i])}"
                )
        sys.exit(1 if result.outcome_kind == "violation" else 2)
    if result.predicate_kind == "example":
        reporter.success(
            f"No examples found "
            f"({result.produced_states} states produced, "
            f"{result.distinct_states} distinct)"
        )
    else:
        reporter.success(
            f"No invariant violations found "
            f"({result.produced_states} states produced, "
            f"{result.distinct_states} distinct)"
        )


def _fuzz_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    _configure_colors(bool(args.no_color), reporter)
    result = api.fuzz(
        api.FuzzRequest(
            spec=args.spec,
            instance=args.instance,
            init=args.init,
            step=args.step,
            property=args.property,
            coverage=args.coverage,
            max_generations=args.max_generations,
            max_steps=args.max_steps,
            bound=args.bound,
            seed=args.seed,
            no_progress=args.no_progress,
            no_energy=args.no_energy,
            corpus_dir=args.corpus_dir,
            timeout=args.timeout,
            no_print_trace=args.no_print_trace,
        ),
        reporter=reporter,
    )
    if result.outcome_kind == "violation":
        sys.exit(1)
    if result.outcome_kind == "example_found":
        sys.exit(2)


def _lint_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    _configure_colors(False, reporter)
    result = api.lint(
        api.LintRequest(spec=args.spec, effects_out=args.effects_out),
        reporter=reporter,
    )
    for warning in result.warnings:
        reporter.warn(f"{warning.func_name}: {warning.message}")
        for src in warning.sources:
            reporter.hint(
                f"  {reporter._color(Colors.DIM)}"
                f"at {src.filename}:{src.lineno} in {src.func}()"
                f"{reporter._color(Colors.RESET)}"
            )
            if src.line:
                reporter.hint(
                    f"    {reporter._color(Colors.DIM)}{src.line}"
                    f"{reporter._color(Colors.RESET)}"
                )
    if result.errors:
        for e in result.errors:
            reporter.error(f"{e.func_name}: {e.message}")
            for src in e.sources:
                reporter.hint(
                    f"  {reporter._color(Colors.DIM)}"
                    f"at {src.filename}:{src.lineno} in {src.func}()"
                    f"{reporter._color(Colors.RESET)}"
                )
                if src.line:
                    reporter.hint(
                        f"    {reporter._color(Colors.DIM)}{src.line}"
                        f"{reporter._color(Colors.RESET)}"
                    )
        sys.exit(1)
    reporter.success("No lint errors found")


def _stub_command(args: argparse.Namespace, reporter: ConsoleReporter) -> None:
    reporter.error(feature_message(args.command))
    sys.exit(1)


def _maybe_reexec_runtime() -> None:
    """If ``--runtime graalpy`` is present, re-exec under GraalPy."""
    try:
        idx = sys.argv.index("--runtime")
    except ValueError:
        return
    if idx + 1 >= len(sys.argv):
        return
    runtime = sys.argv[idx + 1]
    if runtime == "cpython":
        # Already the default — just strip the flag so argparse doesn't choke.
        del sys.argv[idx : idx + 2]
        return
    if runtime != "graalpy":
        return
    if platform.python_implementation() != "CPython":
        # Already running under an alternative runtime — strip and continue.
        del sys.argv[idx : idx + 2]
        return
    graalpy = shutil.which("graalpy")
    if graalpy is None:
        print("error: graalpy not found on PATH", file=sys.stderr)
        sys.exit(1)
    remaining = sys.argv[1:]
    del remaining[remaining.index("--runtime") : remaining.index("--runtime") + 2]
    os.execvp(graalpy, [graalpy, "-m", "wunderspec"] + remaining)


def main() -> None:
    # Restore the default SIGPIPE handling so that a closed downstream pipe
    # (e.g. `wunderspec check ... | head`, or piping into a command that does
    # not read stdin) terminates us quietly like standard Unix tools, instead
    # of surfacing a BrokenPipeError traceback. Not available on every platform
    # (e.g. Windows) or outside the main thread; the BrokenPipeError handler
    # below covers those cases.
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError):
        pass

    _maybe_reexec_runtime()

    parser = argparse.ArgumentParser(
        prog="wunderspec",
        description="Wunderspec - Temporal specification as code",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {importlib.metadata.version('wunderspec')}",
    )
    parser.add_argument(
        "--runtime",
        choices=["cpython", "graalpy"],
        default="cpython",
        help="Python runtime to use (default: cpython). "
        "With 'graalpy', re-execs under GraalPy for JIT acceleration.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    convert_parser = subparsers.add_parser(
        "convert",
        help="Convert Wunderspec to TLA+ or Quint to Wunderspec",
        description=(
            "Convert a Wunderspec .py module to TLA+ .tla, "
            "or a Quint .qnt module to Wunderspec .py."
        ),
    )
    convert_parser.add_argument(
        "--from",
        dest="source",
        required=True,
        help="Source specification file: Wunderspec .py or Quint .qnt",
    )
    convert_parser.add_argument(
        "--to",
        dest="output",
        required=True,
        help="Output file path: .tla for Wunderspec input, .py for Quint input",
    )
    convert_parser.add_argument(
        "--defs",
        default=None,
        help="Comma-separated definitions to convert (e.g., init,step,invariant). "
        "If omitted, all @action, @invariant, @temporal, and @expr definitions are converted.",
    )
    convert_parser.add_argument(
        "--instance",
        default=None,
        help="Name of a partially specialized state object in the source module "
        "(e.g., proto5). Applies only to Wunderspec-to-TLA+ conversion; "
        "ignored for Quint input. Generates an MC*_<Base> wrapper with fixed "
        "constants and INSTANCE.",
    )
    convert_parser.add_argument(
        "--text-width",
        type=int,
        default=api.ConvertRequest.DEFAULT_TEXT_WIDTH,
        help=(
            "Preferred generated output width in columns "
            f"(default: {api.ConvertRequest.DEFAULT_TEXT_WIDTH})."
        ),
    )
    convert_parser.add_argument(
        "--text-indent",
        type=int,
        default=api.ConvertRequest.DEFAULT_TEXT_INDENT,
        help=(
            "Preferred generated continuation indentation in columns "
            f"(default: {api.ConvertRequest.DEFAULT_TEXT_INDENT})."
        ),
    )
    convert_parser.add_argument(
        "--main",
        default=None,
        help="Quint main module when converting from .qnt to .py.",
    )
    convert_parser.add_argument(
        "--quint",
        default="quint",
        help="Quint executable to use when converting from .qnt (default: quint).",
    )
    convert_parser.add_argument(
        "--run-seed",
        type=int,
        default=0,
        help="Default seed embedded in generated Quint run pyunit tests.",
    )
    convert_parser.add_argument(
        "--run-samples",
        type=int,
        default=1,
        help="Default sample count embedded in generated Quint run pyunit tests.",
    )

    tlc_parser = subparsers.add_parser(
        "with-tlc",
        help="Convert a Wunderspec instance to TLA+ and check it with TLC",
        description="Convert a Wunderspec instance to TLA+ and check it with TLC",
    )
    tlc_parser.add_argument(
        "spec", help="Spec file (e.g., examples/readers_writers.py)"
    )
    tlc_parser.add_argument(
        "--instance",
        required=True,
        metavar="NAME",
        help="Name of an @instance factory or fixed state prototype",
    )
    tlc_parser.add_argument(
        "--init", default="init", help="Init action name (default: init)"
    )
    tlc_parser.add_argument(
        "--step", default="step", help="Step action name (default: step)"
    )
    tlc_parser.add_argument(
        "--property",
        required=True,
        help="Property (@invariant, @example, or @temporal) to check with TLC",
    )
    tlc_parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for generated TLA+/CFG files (default: temporary directory)",
    )
    tlc_parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Keep generated files when using the default temporary directory",
    )
    tlc_parser.add_argument(
        "--jar",
        default=None,
        help="Path to tla2tools.jar (otherwise resolved or downloaded automatically)",
    )
    tlc_parser.add_argument(
        "--java",
        default="java",
        help="Java executable to run (default: java)",
    )
    tlc_parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of TLC workers (default: 1)",
    )
    tlc_parser.add_argument(
        "--max-memory",
        default=None,
        metavar="N",
        help=(
            "Max JVM heap for TLC, e.g. 4G (gigabytes) or 2048M "
            "(megabytes); passed as -Xmx (default: JVM default)"
        ),
    )
    tlc_parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run TLC in simulation mode",
    )
    tlc_parser.add_argument(
        "--max-findings",
        type=int,
        default=1,
        help="Stop after this many violations or examples (default: 1)",
    )
    tlc_parser.add_argument(
        "--out-itf",
        default=None,
        metavar="PATH",
        help=(
            "Stream found traces as ITF NDJSON to PATH (one trace per line); "
            "use '-' for stdout, in which case all other output goes to stderr"
        ),
    )
    tlc_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    _add_no_print_trace_arg(tlc_parser)

    apalache_parser = subparsers.add_parser(
        "with-apalache",
        help="Convert a Wunderspec instance to TLA+ and check it with Apalache",
        description="Convert a Wunderspec instance to TLA+ and check it with Apalache",
    )
    apalache_parser.add_argument(
        "spec", help="Spec file (e.g., examples/readers_writers.py)"
    )
    apalache_parser.add_argument(
        "--instance",
        required=True,
        metavar="NAME",
        help="Name of an @instance factory or fixed state prototype",
    )
    apalache_parser.add_argument(
        "--init", default="init", help="Init action name (default: init)"
    )
    apalache_parser.add_argument(
        "--step", default="step", help="Step action name (default: step)"
    )
    apalache_parser.add_argument(
        "--property",
        required=True,
        help="Property (@invariant, @example, or @temporal) to check with Apalache",
    )
    apalache_parser.add_argument(
        "--out-dir",
        default=None,
        help="Directory for generated TLA+ files (default: temporary directory)",
    )
    apalache_parser.add_argument(
        "--keep-files",
        action="store_true",
        help="Keep generated files when using the default temporary directory",
    )
    apalache_parser.add_argument(
        "--jar",
        default=None,
        help="Path to apalache.jar (otherwise resolved or downloaded automatically)",
    )
    apalache_parser.add_argument(
        "--java",
        default="java",
        help="Java executable to run (default: java)",
    )
    apalache_parser.add_argument(
        "--max-steps",
        type=int,
        default=10,
        help="Maximum number of steps to explore (default: 10)",
    )
    apalache_parser.add_argument(
        "--max-memory",
        default=None,
        metavar="N",
        help=(
            "Max JVM heap for Apalache, e.g. 4G (gigabytes) or 2048M "
            "(megabytes); passed as -Xmx (default: JVM default)"
        ),
    )
    apalache_parser.add_argument(
        "--max-samples",
        type=int,
        default=100,
        help="Maximum number of samples for --simulate (default: 100)",
    )
    apalache_parser.add_argument(
        "--simulate",
        action="store_true",
        help="Run Apalache in symbolic simulation mode",
    )
    apalache_parser.add_argument(
        "--max-findings",
        type=int,
        default=1,
        help="Stop after this many violations or examples (default: 1)",
    )
    apalache_parser.add_argument(
        "--out-itf",
        default=None,
        metavar="PATH",
        help=(
            "Stream found traces as ITF NDJSON to PATH (one trace per line); "
            "use '-' for stdout, in which case all other output goes to stderr"
        ),
    )
    apalache_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    _add_no_print_trace_arg(apalache_parser)

    run_parser = subparsers.add_parser(
        "run",
        help="Run random walks on a specification",
    )
    _add_run_args(run_parser)
    run_parser.add_argument(
        "--out-itf",
        default=None,
        metavar="PATH",
        help=(
            "Stream found traces as ITF NDJSON to PATH (one trace per line); "
            "use '-' for stdout, in which case all other output goes to stderr"
        ),
    )
    run_parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bar output",
    )
    run_parser.add_argument(
        "--coverage",
        default=None,
        metavar="NAME",
        help="Name of a @coverage function to track state coverage",
    )
    run_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Stop run after this many seconds",
    )
    run_parser.add_argument(
        "--best-trace",
        type=int,
        choices=[0, 1],
        default=None,
        metavar="{0,1}",
        help=(
            "Print the longest sampled trace (default: 1 without --property, "
            "0 otherwise)"
        ),
    )
    _add_no_print_trace_arg(run_parser)

    replay_parser = subparsers.add_parser(
        "replay",
        help="Replay a single trace with action tracing",
    )
    _add_run_args(replay_parser)
    replay_parser.add_argument(
        "--from-schedule",
        default=None,
        help="Replay using a schedule JSON file emitted by wunderspec check",
    )
    replay_parser.add_argument(
        "--out-itf",
        default=None,
        help="Write replay trace to ITF JSON at the given path",
    )
    replay_parser.add_argument(
        "--out-schedule",
        default=None,
        help="Re-emit the replayed trace as a portable 'values' schedule JSON "
        "at the given path (requires --from-schedule)",
    )

    explain_parser = subparsers.add_parser(
        "explain",
        help="Explain an ITF trace",
        description="Explain an ITF trace produced by Wunderspec",
    )
    explain_parser.add_argument(
        "trace",
        help="ITF trace JSON file to explain",
    )
    explain_parser.add_argument(
        "--bdd",
        action="store_true",
        default=False,
        help="Render the explanation as a Gherkin-like BDD scenario",
    )
    explain_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )

    check_parser = subparsers.add_parser(
        "check",
        help="Model-check a specification by exhaustive DFS",
    )
    check_parser.add_argument(
        "spec", help="Spec file (e.g., examples/readers_writers.py)"
    )
    check_parser.add_argument(
        "--instance",
        default=None,
        metavar="NAME",
        help="Name of an @instance factory to use as the state prototype.",
    )
    check_parser.add_argument(
        "--init", default="init", help="Init action name (default: init)"
    )
    check_parser.add_argument(
        "--step", default="step", help="Step action name (default: step)"
    )
    check_parser.add_argument(
        "--property",
        default=None,
        help="Property (@invariant or @example) to search for",
    )
    check_parser.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Optional DFS cutoff in step transitions from init (default: unlimited)",
    )
    check_parser.add_argument(
        "--bound",
        type=int,
        default=None,
        help="Bound for integer sampling (default: 2^31-1)",
    )
    check_parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress bar output",
    )
    check_parser.add_argument(
        "--no-shuffle",
        action="store_true",
        help="Disable pseudo-random shuffling of DFS exploration order",
    )
    check_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Seed for pseudo-random DFS shuffling (implies shuffling enabled)",
    )
    check_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Stop check after this many seconds",
    )
    check_parser.add_argument(
        "--out-schedule",
        default=None,
        help="Write replay schedule for a found trace (default: temporary file)",
    )
    check_parser.add_argument(
        "--max-findings",
        type=int,
        default=1,
        help="Stop after this many violations or examples (default: 1)",
    )
    check_parser.add_argument(
        "--out-itf",
        default=None,
        metavar="PATH",
        help=(
            "Stream found traces as ITF NDJSON to PATH (one trace per line); "
            "use '-' for stdout, in which case all other output goes to stderr"
        ),
    )
    check_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    _add_no_print_trace_arg(check_parser)

    fuzz_parser = subparsers.add_parser(
        "fuzz",
        help="Coverage-guided fuzzing of a specification",
    )
    fuzz_parser.add_argument(
        "spec", help="Spec file (e.g., examples/readers_writers.py)"
    )
    fuzz_parser.add_argument(
        "--instance",
        default=None,
        metavar="NAME",
        help="Name of an @instance factory to use as the state prototype.",
    )
    fuzz_parser.add_argument(
        "--init", default="init", help="Init action name (default: init)"
    )
    fuzz_parser.add_argument(
        "--step", default="step", help="Step action name (default: step)"
    )
    fuzz_parser.add_argument(
        "--property",
        help="Property (@invariant or @example) to search for",
    )
    fuzz_parser.add_argument(
        "--coverage",
        required=True,
        metavar="NAME",
        help="Name of a @coverage function to guide fuzzing",
    )
    fuzz_parser.add_argument(
        "--max-generations",
        type=int,
        default=100,
        help="Max fuzzer generations (default: 100)",
    )
    fuzz_parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Max steps per trace (default: 20)",
    )
    fuzz_parser.add_argument(
        "--bound",
        type=int,
        default=2**31 - 1,
        help="Bound for integer sampling (default: 2^31-1)",
    )
    fuzz_parser.add_argument("--seed", type=int, default=None, help="Random seed")
    fuzz_parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable progress output",
    )
    fuzz_parser.add_argument(
        "--no-color",
        action="store_true",
        help="Disable colored output",
    )
    fuzz_parser.add_argument(
        "--no-energy",
        action="store_true",
        help="Disable energy-based mutation scheduling (use uniform random instead)",
    )
    fuzz_parser.add_argument(
        "--corpus-dir",
        default=None,
        help="Base directory for corpus storage (default: corpus/)",
    )
    fuzz_parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        metavar="SECONDS",
        help="Stop fuzzing after this many seconds",
    )
    _add_no_print_trace_arg(fuzz_parser)

    lint_parser = subparsers.add_parser(
        "lint",
        help="Lint a Wunderspec module",
    )
    lint_parser.add_argument(
        "spec",
        help="Spec file to lint (e.g., examples/spec.py)",
    )
    lint_parser.add_argument(
        "--effects-out",
        default=None,
        help="Write action read/write effects to the given text file",
    )

    rust_parser = subparsers.add_parser(
        "rust",
        help="Translate a Wunderspec module to Rust (Premium)",
    )
    rust_parser.set_defaults(_stub=True)

    lean_parser = subparsers.add_parser(
        "lean",
        help="Translate a Wunderspec module to Lean (Premium)",
    )
    lean_parser.set_defaults(_stub=True)

    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    reporter = ConsoleReporter()

    try:
        if getattr(args, "_stub", False):
            _stub_command(args, reporter)
        if args.command == "convert":
            _convert_command(args, reporter)
        if args.command == "with-tlc":
            _with_tlc_command(args, reporter)
        if args.command == "with-apalache":
            _with_apalache_command(args, reporter)
        if args.command == "run":
            _run_command(args, reporter)
        if args.command == "replay":
            _replay_command(args, reporter)
        if args.command == "explain":
            _explain_command(args, reporter)
        if args.command == "check":
            _check_command(args, reporter)
        if args.command == "fuzz":
            _fuzz_command(args, reporter)
        if args.command == "lint":
            _lint_command(args, reporter)
    except api.ApiError as e:
        reporter.error(e.message)
        sys.exit(e.exit_code)
    except BrokenPipeError:
        # The downstream consumer of our stdout (e.g. `head`, a pager, or a
        # pipe into a command that does not read stdin) closed the pipe early.
        # Redirect the stdout fd to /dev/null so the interpreter's final flush
        # on exit does not raise a second BrokenPipeError, then exit quietly.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        sys.exit(141)


if __name__ == "__main__":
    main()
