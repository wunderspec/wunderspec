"""Validation, command lines, result reporting, and replay hints for generated models
(spec 005 R3, R10; FR-005 to FR-008, FR-029, FR-037).

Moved from `wunderspec-rust` v0.67.1 (`cli.py` and `api.py`) so that `wunderspec rust` and
`wunderspec-rust` drive the model identically. The model command lines are exactly those of
v0.67.1: some options are always passed with their values, the others only when given.
"""

import argparse
import codecs
import contextlib
import json
import random
import re
import shlex
import signal
import subprocess
import sys
import threading
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from wunderspec.rust_cli.options import DEFAULT_BUILD_PROFILE

TIMEOUT_EXIT_CODE = 124
INTERRUPT_EXIT_CODE = 130
_UNLIMITED_STEPS = 2**63 - 1
_STDERR_HEAD = 64 * 1024


class ValidationError(Exception):
    """An invalid option value or combination, found before any work starts."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class Outcome:
    """How a command ends: the exit status and, if it failed, the error line to print."""

    exit_code: int
    error: str | None = None


@dataclass(frozen=True)
class Hint:
    """How replay commands are printed: the command prefix and the user's `--to`, if any."""

    prefix: Sequence[str]
    output_dir: Path | None


@dataclass(frozen=True)
class CheckPlan:
    visited: str
    bitvec_size: int
    shuffle_seed: int | None
    bound: int


@dataclass(frozen=True)
class ModelProcess:
    """A finished model run: status, captured stdout (if captured), and stderr text."""

    returncode: int
    output_dir: Path
    stdout: str | None
    stderr: str
    summary_text: str


@dataclass(frozen=True)
class CheckResult:
    output_dir: Path
    exit_code: int
    produced_states: int | None = None
    distinct_states: int | None = None
    violation_found: bool | None = None
    example_found: bool | None = None
    replay_shuffle_seed: int | None = None
    memory_limit_exceeded: bool | None = None
    timed_out: bool | None = None
    interrupted: bool | None = None
    process_failure: str | None = None
    schedule_paths: tuple[Path, ...] = field(default_factory=tuple)
    finding_kinds: tuple[str, ...] = field(default_factory=tuple)


# --- validation ---------------------------------------------------------------------------


def validate_max_memory(value: str | None) -> None:
    if value is None:
        return
    text = value.strip()
    if len(text) < 2:
        raise ValidationError("--max-memory must be an integer followed by M or G")
    digits, suffix = text[:-1], text[-1]
    if not digits.isdigit():
        raise ValidationError("--max-memory must be an integer followed by M or G")
    if suffix not in {"M", "m", "G", "g"}:
        raise ValidationError("--max-memory suffix must be M or G")
    if int(digits) <= 0:
        raise ValidationError("--max-memory must be greater than zero")


def validate_timeout(value: str | None) -> None:
    if value is None:
        return
    text = value.strip()
    digits = text[:-1] if text[-1:] in {"s", "m", "h"} else text
    if not digits.isdigit():
        raise ValidationError(
            "--timeout must be an integer number of seconds, "
            "or a value like 30s, 5m, or 1h"
        )
    if int(digits) <= 0:
        raise ValidationError("--timeout must be greater than zero")


def validate_run(args: argparse.Namespace) -> None:
    if args.workers < 1:
        raise ValidationError("--workers must be at least 1")
    if args.sync_interval_secs < 1:
        raise ValidationError("--sync-interval-secs must be at least 1")
    validate_max_memory(args.max_memory)
    validate_timeout(args.timeout)


def validate_replay(args: argparse.Namespace) -> None:
    validate_max_memory(args.max_memory)


def plan_check(args: argparse.Namespace) -> CheckPlan:
    """Validate `check` options, resolve visited-set defaults, and choose the shuffle seed."""
    shuffle_enabled = not args.no_shuffle
    if args.shuffle_seed is not None and not shuffle_enabled:
        raise ValidationError("--shuffle-seed cannot be used with --no-shuffle")
    visited = args.visited
    bitvec_size = args.bitvec_size
    # --bitvec-size implies --visited=bitvec when --visited isn't given.
    if bitvec_size is not None and visited is None:
        visited = "bitvec"
    # An explicit, contradictory --visited is an error, not a silent no-op.
    if bitvec_size is not None and visited != "bitvec":
        raise ValidationError("--bitvec-size requires --visited=bitvec")
    if visited is None:
        visited = "fingerprint"
    if bitvec_size is None:
        bitvec_size = 16
    if bitvec_size < 1:
        raise ValidationError("--bitvec-size must be at least 1 MB")
    if args.workers < 1:
        raise ValidationError("--workers must be at least 1")
    if args.sync_interval_secs < 1:
        raise ValidationError("--sync-interval-secs must be at least 1")
    if args.workers > 1 and visited == "exact":
        raise ValidationError("--workers > 1 requires --visited fingerprint or bitvec")
    if args.workers > 1 and not shuffle_enabled:
        raise ValidationError("--workers > 1 requires shuffling; remove --no-shuffle")
    validate_max_memory(args.max_memory)
    validate_timeout(args.timeout)
    shuffle_seed = args.shuffle_seed
    if shuffle_seed is None and shuffle_enabled:
        shuffle_seed = random.randrange(2**63)
    if shuffle_seed is not None:
        print(f"Shuffle seed: {shuffle_seed}", file=sys.stderr, flush=True)
    bound = args.bound if args.bound is not None else sys.maxsize
    return CheckPlan(visited, bitvec_size, shuffle_seed, bound)


# --- command lines ------------------------------------------------------------------------


def _available_schedule_path(base_dir: Path) -> Path:
    for i in range(sys.maxsize):
        schedule = base_dir / f"schedule_{i}.json"
        if not schedule.exists():
            return schedule
    raise ValidationError("could not choose an unused replay schedule path")


def run_schedule_path(args: argparse.Namespace) -> str | None:
    """Where `run` asks the model to write a replay schedule (absolute), if anywhere."""
    if args.out_schedule is not None:
        # Absolute, so the schedule survives a temporary build directory.
        return str(Path(args.out_schedule).expanduser().resolve())
    if args.property is not None:
        base_dir = (
            Path(args.output_dir).expanduser().resolve()
            if args.output_dir is not None
            else Path.cwd()
        )
        return str(_available_schedule_path(base_dir))
    return None


def replay_schedule_path(args: argparse.Namespace) -> str | None:
    if args.out_schedule is None:
        return None
    return str(Path(args.out_schedule).expanduser().resolve())


def run_argv(args: argparse.Namespace, out_schedule: str | None) -> list[str]:
    argv = [
        "run",
        "--max-samples",
        str(args.max_samples),
        "--max-steps",
        str(args.max_steps),
        "--bound",
        str(args.bound),
        "--seed",
        str(args.seed),
        "--max-findings",
        str(args.max_findings),
        "--max-retries-per-step",
        str(args.max_retries_per_step),
        "--workers",
        str(args.workers),
        "--sync-interval-secs",
        str(args.sync_interval_secs),
    ]
    if args.instance is not None:
        argv += ["--instance", args.instance]
    if args.out_itf is not None:
        argv += ["--out-itf", str(args.out_itf)]
    if out_schedule is not None:
        argv += ["--out-schedule", out_schedule]
    if args.compat:
        argv.append("--compat")
    if args.coverage:
        argv += ["--coverage", args.coverage]
    argv += ["--invariant-cache", args.invariant_cache]
    if args.no_progress:
        argv.append("--no-progress")
    if args.no_action_profiling:
        argv.append("--no-action-profiling")
    if args.max_memory is not None:
        argv += ["--max-memory", args.max_memory]
    if args.timeout is not None:
        argv += ["--timeout", args.timeout]
    if args.stack_size is not None:
        argv += ["--stack-size", args.stack_size]
    return argv


def check_argv(args: argparse.Namespace, plan: CheckPlan) -> list[str]:
    argv = [
        "check",
        "--bound",
        str(plan.bound),
        "--visited",
        plan.visited,
        "--bitvec-size",
        str(plan.bitvec_size),
        "--workers",
        str(args.workers),
        "--sync-interval-secs",
        str(args.sync_interval_secs),
        "--max-findings",
        str(args.max_findings),
    ]
    max_steps = args.max_steps if args.max_steps is not None else _UNLIMITED_STEPS
    argv += ["--max-steps", str(max_steps)]
    if args.instance is not None:
        argv += ["--instance", args.instance]
    if args.out_itf is not None:
        argv += ["--out-itf", str(args.out_itf)]
    if args.compat:
        argv.append("--compat")
    if args.no_progress:
        argv.append("--no-progress")
    if args.no_action_profiling:
        argv.append("--no-action-profiling")
    if plan.shuffle_seed is not None:
        argv += ["--shuffle-seed", str(plan.shuffle_seed)]
    if args.max_memory is not None:
        argv += ["--max-memory", args.max_memory]
    if args.timeout is not None:
        argv += ["--timeout", args.timeout]
    if args.stack_size is not None:
        argv += ["--stack-size", args.stack_size]
    return argv


def replay_argv(args: argparse.Namespace, out_schedule: str | None) -> list[str]:
    argv = ["replay", "--bound", str(args.bound)]
    if args.from_schedule is not None:
        argv += ["--from-schedule", str(args.from_schedule)]
    if args.seed is not None:
        argv += ["--seed", str(args.seed)]
        argv += ["--max-steps", str(args.max_steps)]
        argv += ["--max-retries-per-step", str(args.max_retries_per_step)]
    if args.instance is not None:
        argv += ["--instance", args.instance]
    if args.out_itf is not None:
        argv += ["--out-itf", str(args.out_itf)]
    if out_schedule is not None:
        argv += ["--out-schedule", out_schedule]
    if args.compat:
        argv.append("--compat")
    if args.check_property:
        argv.append("--check-property")
    if args.max_memory is not None:
        argv += ["--max-memory", args.max_memory]
    return argv


# --- running ------------------------------------------------------------------------------


@contextlib.contextmanager
def _child_owns_sigint() -> Iterator[None]:
    """Ignore SIGINT here while the model runs, so it can flush its partial results.

    A Ctrl-C reaches the whole foreground process group; if this process acted on it,
    `subprocess` would kill the model before it saved its coverage and action profile.
    """
    try:
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    except ValueError:  # not on the main thread
        previous = None
    try:
        yield
    finally:
        if previous is not None:
            signal.signal(signal.SIGINT, previous)


def _tee(source: IO[bytes], sink: Any, keep_all: bool, kept: list[str]) -> None:
    """Copy the model's stderr to `sink` as it arrives and keep its text (or its head)."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    size = 0
    while True:
        chunk = source.read1(65536) if hasattr(source, "read1") else source.read(65536)
        if not chunk:
            break
        text = decoder.decode(chunk)
        sink.write(text)
        sink.flush()
        if keep_all or size < _STDERR_HEAD:
            kept.append(text)
            size += len(text)
    tail = decoder.decode(b"", final=True)
    if tail:
        sink.write(tail)
        kept.append(tail)


def run_model(
    binary: Path,
    argv: list[str],
    cwd: Path,
    command: str,
    args: argparse.Namespace,
) -> ModelProcess:
    """Run the model in its project directory.

    For `check`, the machine-readable summary is read from stdout, or from stderr when ITF
    traces stream to stdout (`--out-itf -`); stdout then passes through untouched. The
    model's stderr is always copied to ours as it arrives.
    """
    itf_stdout = getattr(args, "out_itf", None) == "-"
    capture_stdout = command == "check" and not itf_stdout
    kept: list[str] = []
    with _child_owns_sigint():
        process = subprocess.Popen(
            [str(binary), *argv],
            cwd=str(cwd),
            stdout=subprocess.PIPE if capture_stdout else None,
            stderr=subprocess.PIPE,
        )
        assert process.stderr is not None
        reader = threading.Thread(
            target=_tee,
            args=(process.stderr, sys.stderr, command == "check" and itf_stdout, kept),
            daemon=True,
        )
        reader.start()
        stdout_bytes = process.stdout.read() if process.stdout is not None else None
        returncode = process.wait()
        reader.join()
    stdout = (
        stdout_bytes.decode("utf-8", errors="replace")
        if stdout_bytes is not None
        else None
    )
    stderr = "".join(kept)
    summary = stderr if itf_stdout else (stdout or "")
    return ModelProcess(returncode, cwd, stdout, stderr, summary)


_REJECTED = re.compile(
    r"""^error: (?:invalid option|unexpected argument|unknown option|unexpected option)"""
    r"""[^'"]*['"](--[A-Za-z0-9][A-Za-z0-9-]*)"""
)


def rejected_option(process: ModelProcess) -> str | None:
    """The option a model refused (version skew, R10), if this run ended that way.

    Models from translator 0.68 exit 64 on usage errors; older ones exit 1 with a lexopt
    message as the first line of stderr, before any other output.
    """
    first = process.stderr.lstrip("\n").split("\n", 1)[0]
    if process.returncode == 64:
        found = re.search(r"--[A-Za-z0-9][A-Za-z0-9-]*", first)
        return found.group(0) if found else None
    if process.returncode == 1:
        match = _REJECTED.match(first)
        return match.group(1) if match else None
    return None


# --- results ------------------------------------------------------------------------------


def describe_process_failure(returncode: int) -> str:
    if returncode < 0:
        sig_num = -returncode
        try:
            sig_name = signal.Signals(sig_num).name
        except ValueError:
            sig_name = f"signal {sig_num}"
        detail = f"checker process was terminated by {sig_name}"
        if sig_num == getattr(signal, "SIGKILL", 9):
            detail += " (likely killed by the OS, commonly due to OOM)"
        elif sig_num == signal.SIGABRT:
            detail += " (process aborted)"
        return f"{detail}; no model-check summary was produced"
    if returncode == 137:
        return (
            "checker process exited with code 137 (SIGKILL; likely killed by the "
            "OS, commonly due to OOM); no model-check summary was produced"
        )
    if returncode == 134:
        return (
            "checker process exited with code 134 (SIGABRT/process abort); "
            "no model-check summary was produced"
        )
    if returncode == 101:
        return (
            "checker process exited with code 101 (Rust panic); "
            "no model-check summary was produced"
        )
    return (
        f"checker process exited with code {returncode}; "
        "no model-check summary was produced"
    )


def parse_check(process: ModelProcess, output_dir: Path) -> CheckResult:
    """Read the model-check JSON summary, as `wunderspec-rust` v0.67.1 `api.check` does."""
    values: dict[str, Any] = {}
    finding_kinds: tuple[str, ...] = ()
    for line in process.summary_text.strip().splitlines():
        try:
            data = json.loads(line)
            values = {
                key: data.get(key)
                for key in (
                    "produced_states",
                    "distinct_states",
                    "violation_found",
                    "example_found",
                    "replay_shuffle_seed",
                    "memory_limit_exceeded",
                    "timed_out",
                    "interrupted",
                )
            }
            raw = data.get("finding_kinds")
            if isinstance(raw, list):
                finding_kinds = tuple(str(kind) for kind in raw)
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass
    schedule_paths = tuple(sorted(output_dir.glob("schedule_*.json")))
    stopped = any(
        values.get(key) is True
        for key in ("violation_found", "example_found")
        + ("memory_limit_exceeded", "timed_out", "interrupted")
    )
    process_failure = None
    if process.returncode != 0 and not stopped:
        process_failure = describe_process_failure(process.returncode)
    elif (
        values.get("produced_states") is None
        or values.get("distinct_states") is None
        or (
            values.get("violation_found") is None
            and values.get("example_found") is None
            and not any(
                values.get(k) is True
                for k in ("memory_limit_exceeded", "timed_out", "interrupted")
            )
        )
    ):
        process_failure = (
            "checker process did not emit a model-check JSON summary; "
            "no checking result was produced"
        )
    return CheckResult(
        output_dir=output_dir,
        exit_code=process.returncode,
        process_failure=process_failure,
        schedule_paths=schedule_paths,
        finding_kinds=finding_kinds,
        **values,
    )


def _counts(result: CheckResult) -> str:
    return (
        f"{result.produced_states} states produced, {result.distinct_states} distinct"
    )


def report_check(
    result: CheckResult,
    args: argparse.Namespace,
    hint: Hint,
    *,
    example: bool = False,
) -> Outcome:
    """Print the verdict of `check` and return how the command ends.

    `example` tells whether the checked property is an `@example` (the clients know it
    from the specification), which only changes the wording when nothing was found.
    """
    # With ITF on stdout, the human summary goes to stderr (FR-007).
    out = sys.stderr if args.out_itf == "-" else sys.stdout
    if result.memory_limit_exceeded is True:
        print(f"Memory limit exceeded ({_counts(result)})", file=out)
        return _stopped(result, "memory limit exceeded")
    if result.timed_out is True:
        print(
            f"Timed out before exploring the full state space ({_counts(result)}); "
            "the check is incomplete",
            file=out,
        )
        return _stopped(result, "check timed out")
    if result.interrupted is True:
        print(
            f"Interrupted by Ctrl-C ({_counts(result)}); the check is incomplete "
            "(partial action profile saved)",
            file=out,
        )
        return _stopped(result, "check interrupted")
    if result.violation_found is False and result.example_found is False:
        if example:
            print(f"No examples found ({_counts(result)})", file=out)
        else:
            print(f"No invariant violations found ({_counts(result)})", file=out)
    elif result.violation_found is True:
        print(f"Invariant violated ({_counts(result)})", file=out)
    elif result.example_found is True:
        print(f"Example found ({_counts(result)})", file=out)
    if result.process_failure is not None:
        if result.exit_code != 0:
            print_check_replay_hint(args, result, hint)
        return Outcome(
            result.exit_code if result.exit_code != 0 else 1,
            f"model check process failed: {result.process_failure}",
        )
    if result.exit_code != 0:
        print_check_replay_hint(args, result, hint)
        return Outcome(result.exit_code, "model check failed")
    return Outcome(0)


def _stopped(result: CheckResult, message: str) -> Outcome:
    if result.exit_code != 0:
        return Outcome(result.exit_code, message)
    return Outcome(0)


def report_run(
    process: ModelProcess,
    args: argparse.Namespace,
    out_schedule: str | None,
    hint: Hint,
) -> Outcome:
    if process.returncode == TIMEOUT_EXIT_CODE:
        print(
            "Timed out before sampling the requested number of traces.", file=sys.stderr
        )
        return Outcome(process.returncode, "run timed out")
    if process.returncode == INTERRUPT_EXIT_CODE:
        print(
            "Interrupted by Ctrl-C; partial coverage and action profile saved.",
            file=sys.stderr,
        )
        return Outcome(process.returncode, "run interrupted")
    if (
        process.returncode != 0
        and out_schedule is not None
        and Path(out_schedule).exists()
    ):
        print_run_replay_hint(
            args, Path(out_schedule), hint, check_property=process.returncode == 101
        )
    if process.returncode != 0:
        return Outcome(process.returncode, "model run failed")
    return Outcome(0)


def report_replay(
    process: ModelProcess,
    args: argparse.Namespace,
    out_schedule: str | None,
    hint: Hint,
) -> Outcome:
    if (
        process.returncode == 0
        and args.compat
        and out_schedule is not None
        and Path(out_schedule).exists()
    ):
        print_run_replay_hint(args, Path(out_schedule), hint)
    if process.returncode != 0:
        return Outcome(process.returncode, "model replay failed")
    return Outcome(0)


# --- replay hints -------------------------------------------------------------------------


def wunderspec_replay_command(
    source: str, schedule: Path, args: argparse.Namespace
) -> list[str]:
    """A `wunderspec replay` command (the Python CLI, with a richer trace UI) for a
    portable schedule; only flags that `wunderspec replay` accepts are emitted."""
    cmd = ["wunderspec", "replay", source, "--from-schedule", str(schedule)]
    if args.init != "init":
        cmd += ["--init", args.init]
    if args.step != "step":
        cmd += ["--step", args.step]
    if args.property is not None:
        cmd += ["--property", args.property]
    if args.instance is not None:
        cmd += ["--instance", args.instance]
    return cmd


def model_replay_command(
    hint: Hint,
    args: argparse.Namespace,
    schedule: Path,
    *,
    out_itf: Path | None = None,
    check_property: bool = False,
) -> list[str]:
    """A replay command for the client named by `hint.prefix`.

    Carries what is needed to reach the same translation and build: `--to` only when the
    user gave it (a cache entry is found again by its key), the translation options, and
    `--build-profile` and `--config` when not the defaults. Never the token (FR-008).
    """
    bound = args.bound if args.bound is not None else sys.maxsize
    cmd = [*hint.prefix, "replay", args.source, "--from-schedule", str(schedule)]
    cmd += ["--bound", str(bound)]
    if hint.output_dir is not None:
        cmd += ["--to", str(hint.output_dir)]
    cmd += ["--init", args.init, "--step", args.step]
    if args.property is not None:
        cmd += ["--property", args.property]
    if args.instance is not None:
        cmd += ["--instance", args.instance]
    if args.max_memory is not None:
        cmd += ["--max-memory", args.max_memory]
    if getattr(args, "runtime_path", None) is not None:
        cmd += ["--runtime-path", args.runtime_path]
    if args.build_profile != DEFAULT_BUILD_PROFILE:
        cmd += ["--build-profile", args.build_profile]
    if getattr(args, "config", None) is not None:
        cmd += ["--config", args.config]
    if out_itf is not None:
        cmd += ["--out-itf", str(out_itf)]
    if getattr(args, "compat", False):
        cmd.append("--compat")
    if check_property:
        cmd.append("--check-property")
    return cmd


def print_check_replay_hint(
    args: argparse.Namespace, result: CheckResult, hint: Hint
) -> None:
    schedules = result.schedule_paths or (result.output_dir / "schedule_0.json",)
    schedules = tuple(schedule for schedule in schedules if schedule.exists())
    if not schedules:
        return
    print(f"Generated checker preserved in: {result.output_dir}", file=sys.stderr)
    for index, schedule in enumerate(schedules):
        check_property = (
            index < len(result.finding_kinds) and result.finding_kinds[index] == "panic"
        )
        cmd = model_replay_command(
            hint,
            args,
            schedule,
            out_itf=result.output_dir / f"out_{index}.itf.json",
            check_property=check_property,
        )
        print(f"Replay schedule: {schedule}", file=sys.stderr)
        print("Fast replay command: " + shlex.join(cmd), file=sys.stderr)
        # A native schedule is engine-specific; only a portable values schedule
        # (--compat) can be replayed by `wunderspec replay`.
        if getattr(args, "compat", False):
            portable = wunderspec_replay_command(args.source, schedule, args)
            print("Replay command: " + shlex.join(portable), file=sys.stderr)


def print_run_replay_hint(
    args: argparse.Namespace,
    schedule: Path,
    hint: Hint,
    *,
    check_property: bool = False,
) -> None:
    print(f"Replay schedule: {schedule}", file=sys.stderr)
    cmd = model_replay_command(hint, args, schedule, check_property=check_property)
    print("Fast replay command: " + shlex.join(cmd), file=sys.stderr)
    if getattr(args, "compat", False):
        portable = wunderspec_replay_command(args.source, schedule, args)
        print("Replay command: " + shlex.join(portable), file=sys.stderr)
