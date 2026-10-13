"""One table of command-line options for `wunderspec rust`, `wunderspec-rust`, and the
generated `wunderspec-model` (spec 005 R2, FR-001, FR-002, FR-021, FR-026).

Each row describes one option of one or more commands. An option whose default or description
differs between commands has one row per variant, so `(flag, command)` identifies a row for a
client. `wunderspec-rust` builds its parsers from the same table, and the translator generates
the model's help from the rows with `model=True`.
"""

import argparse
import sys
import textwrap
from dataclasses import dataclass
from typing import Any, Literal

WUNDERSPEC_RUST_CLI = "wunderspec rust"
WUNDERSPEC_RUST = "wunderspec-rust"
BOTH = (WUNDERSPEC_RUST_CLI, WUNDERSPEC_RUST)
CLI_ONLY = (WUNDERSPEC_RUST_CLI,)
INTERNAL_ONLY = (WUNDERSPEC_RUST,)

Kind = Literal["translation", "build", "execution", "service", "cache", "input"]
OptionType = Literal["int", "float", "str", "bool", "size", "duration"]

MODEL_COMMANDS = ("run", "check", "replay")
TRANSLATING = ("compile", "run", "check", "replay")
BUILD_PROFILES = ("fast", "release", "profiling")
DEFAULT_BUILD_PROFILE = "fast"
DEFAULT_MAX_RETRIES_PER_STEP = 30
DEFAULT_WAIT_SECONDS = 600.0

# Required mutually exclusive groups: name -> required.
GROUPS = {"schedule-or-seed": True}


@dataclass(frozen=True)
class OptionSpec:
    """One option (or positional argument) of the commands in `commands`."""

    flag: str
    dest: str
    type: OptionType
    help: str
    commands: tuple[str, ...]
    kind: Kind
    default: Any = None
    choices: tuple[str, ...] | None = None
    metavar: str | None = None
    required: bool = False
    group: str | None = None
    model: bool = False
    clients: tuple[str, ...] = BOTH
    hidden: bool = False
    default_text: str | None = None
    cli_help: str | None = None
    cli_default_text: str | None = None
    # The model's own default when it differs from the clients' (the clients always
    # pass a value), for the generated help (FR-022).
    model_default_text: str | None = None

    @property
    def positional(self) -> bool:
        return not self.flag.startswith("-")

    def help_for(self, client: str) -> str:
        """Help text with the default and, in `wunderspec rust`, the re-translation mark."""
        text = self.help
        if client == WUNDERSPEC_RUST_CLI and self.cli_help:
            text = self.cli_help
        shown = self.default_text
        if client == WUNDERSPEC_RUST_CLI and self.cli_default_text:
            shown = self.cli_default_text
        if shown is None and self.default is not None and self.type != "bool":
            shown = str(self.default)
        if shown is not None and not self.required:
            text += f" (default: {shown})"
        if client == WUNDERSPEC_RUST_CLI and self.kind == "translation":
            text += " [re-translates]"
        return text


def _spec(*commands: str) -> tuple[str, ...]:
    return commands


SOURCE_HELP = "Source Python file with @state/@action decorators"
_INIT_HELP = "Name of the init action function"
_STEP_HELP = "Name of the step action function"
_PROPERTY_HELP = "Property (@invariant or @example) to search for"

TRANSLATION_ROWS = (
    OptionSpec(
        "--init",
        "init",
        "str",
        _INIT_HELP,
        TRANSLATING,
        "translation",
        default="init",
        metavar="NAME",
    ),
    OptionSpec(
        "--init",
        "init",
        "str",
        _INIT_HELP,
        _spec("export", "submit"),
        "translation",
        default="init",
        metavar="NAME",
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "--step",
        "step",
        "str",
        _STEP_HELP,
        TRANSLATING,
        "translation",
        default="step",
        metavar="NAME",
    ),
    OptionSpec(
        "--step",
        "step",
        "str",
        _STEP_HELP,
        _spec("export", "submit"),
        "translation",
        default="step",
        metavar="NAME",
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "--property",
        "property",
        "str",
        _PROPERTY_HELP,
        _spec("compile", "run"),
        "translation",
        metavar="NAME",
        default_text="none",
    ),
    OptionSpec(
        "--property",
        "property",
        "str",
        _PROPERTY_HELP,
        _spec("export", "submit"),
        "translation",
        metavar="NAME",
        default_text="none",
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "--property",
        "property",
        "str",
        _PROPERTY_HELP,
        _spec("check", "replay"),
        "translation",
        metavar="NAME",
        required=True,
    ),
)

INPUT_ROWS = (
    OptionSpec(
        "source",
        "source",
        "str",
        SOURCE_HELP,
        MODEL_COMMANDS,
        "input",
        metavar="SPEC",
        required=True,
    ),
    OptionSpec(
        "source",
        "source",
        "str",
        SOURCE_HELP,
        _spec("export"),
        "input",
        metavar="SPEC",
        required=True,
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "document",
        "document",
        "str",
        "Transfer document written by 'wunderspec rust export'",
        _spec("submit"),
        "input",
        metavar="DOCUMENT",
        required=True,
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "job_id",
        "job_id",
        "str",
        "ID of a job submitted earlier",
        _spec("job"),
        "input",
        metavar="JOB_ID",
        required=True,
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "--from",
        "source",
        "str",
        SOURCE_HELP,
        _spec("compile"),
        "input",
        metavar="SPEC",
        required=True,
    ),
)

_OUT_DIR_HELP = "Output directory for the generated Cargo project"
CACHE_ROWS = (
    OptionSpec(
        "--to",
        "output_dir",
        "str",
        _OUT_DIR_HELP,
        _spec("compile"),
        "cache",
        metavar="DIR",
        required=True,
    ),
    OptionSpec(
        "--to",
        "output_dir",
        "str",
        _OUT_DIR_HELP,
        MODEL_COMMANDS,
        "cache",
        metavar="DIR",
        default_text="a temporary directory",
        cli_help="Directory for the generated project and its build",
        cli_default_text="the translation cache",
    ),
    OptionSpec(
        "--to",
        "output_dir",
        "str",
        _OUT_DIR_HELP,
        _spec("submit", "job"),
        "cache",
        metavar="DIR",
        required=True,
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "--to",
        "output_dir",
        "str",
        "File to write the transfer document to",
        _spec("export"),
        "cache",
        metavar="FILE",
        required=True,
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "--no-cache",
        "no_cache",
        "bool",
        "Ignore cached build and recompile from scratch",
        TRANSLATING,
        "cache",
        default=False,
        cli_help="Discard the cached translation and build, translate again on "
        "the service, and rebuild",
    ),
)

BUILD_ROWS = (
    OptionSpec(
        "--runtime-path",
        "runtime_path",
        "str",
        "Optional local path override for wunderspec-rs-runtime",
        TRANSLATING,
        "build",
        clients=INTERNAL_ONLY,
    ),
    OptionSpec(
        "--no-build",
        "no_build",
        "bool",
        "Skip cargo build after generating code",
        _spec("compile"),
        "build",
        default=False,
    ),
    OptionSpec(
        "--no-fmt",
        "no_fmt",
        "bool",
        "Skip cargo fmt; combine with --no-build for tool-free generation",
        _spec("compile"),
        "build",
        default=False,
        clients=INTERNAL_ONLY,
    ),
    OptionSpec(
        "--build-profile",
        "build_profile",
        "str",
        "Cargo build profile for the generated model",
        TRANSLATING,
        "build",
        default=DEFAULT_BUILD_PROFILE,
        choices=BUILD_PROFILES,
    ),
)

_WITH_SERVICE = _spec("compile", "run", "check", "replay", "submit", "job")
_WAIT_HELP = (
    "Seconds to wait for the translation service before stopping; the job continues"
)
SERVICE_ROWS = (
    OptionSpec(
        "--config",
        "config",
        "str",
        "Service configuration file",
        _WITH_SERVICE,
        "service",
        metavar="FILE",
        default_text="$WUNDERSPEC_CONFIG, else ~/.wunderspec/service.yaml",
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "--force",
        "force",
        "bool",
        "Replace the contents of an output directory that would otherwise be "
        "refused",
        _WITH_SERVICE,
        "service",
        default=False,
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "--wait-timeout",
        "wait_timeout",
        "float",
        _WAIT_HELP,
        TRANSLATING,
        "service",
        default=DEFAULT_WAIT_SECONDS,
        metavar="SECONDS",
        clients=CLI_ONLY,
    ),
    OptionSpec(
        "--timeout",
        "timeout",
        "float",
        _WAIT_HELP,
        _spec("submit", "job"),
        "service",
        default=DEFAULT_WAIT_SECONDS,
        metavar="SECONDS",
        clients=CLI_ONLY,
    ),
)

_ITF_STREAM_HELP = (
    "Stream found traces as ITF NDJSON to PATH (one trace per line); use '-' for "
    "stdout, in which case all other output goes to stderr"
)
_COMPAT_HELP = (
    "Emit the portable 'values' schedule representation that 'wunderspec replay' can "
    "consume (instead of the engine-native one)"
)
_REPLAY_COMPAT_HELP = (
    "Emit the re-emitted schedule in the portable 'values' representation that "
    "'wunderspec replay' can consume"
)
_RUN_WORKERS_HELP = (
    "Number of parallel simulator workers. With more than one worker, which traces are "
    "explored is nondeterministic even for a fixed --seed; reproduce a finding from its "
    "emitted schedule instead"
)
_DURATION_HELP = (
    "Wall-clock time limit for the {}, e.g. 30s, 5m, 1h (bare number = seconds)"
)


def _exec(
    flag: str,
    dest: str,
    type_: OptionType,
    help_: str,
    commands: tuple[str, ...],
    **kwargs: Any,
) -> OptionSpec:
    kwargs.setdefault("model", True)
    if type_ == "bool":
        kwargs.setdefault("default", False)
    return OptionSpec(flag, dest, type_, help_, commands, "execution", **kwargs)


EXECUTION_ROWS = (
    _exec(
        "--max-samples",
        "max_samples",
        "int",
        "Maximum number of traces to generate",
        _spec("run"),
        default=1000,
        metavar="N",
    ),
    _exec(
        "--max-steps",
        "max_steps",
        "int",
        "Maximum steps per trace",
        _spec("run"),
        default=10,
        metavar="N",
    ),
    _exec(
        "--max-steps",
        "max_steps",
        "int",
        "Maximum steps for seed replay",
        _spec("replay"),
        default=10,
        metavar="N",
    ),
    _exec(
        "--max-steps",
        "max_steps",
        "int",
        "Optional DFS cutoff in step transitions from init",
        _spec("check"),
        metavar="N",
        default_text="unlimited",
    ),
    _exec(
        "--max-retries-per-step",
        "max_retries_per_step",
        "int",
        "Per-step retry budget; total budget per trace is this value times "
        "--max-steps",
        _spec("run", "replay"),
        default=DEFAULT_MAX_RETRIES_PER_STEP,
        metavar="N",
    ),
    _exec(
        "--bound",
        "bound",
        "int",
        "Maximum branching bound",
        _spec("run", "replay"),
        default=sys.maxsize,
        metavar="N",
        default_text="unbounded",
    ),
    _exec(
        "--bound",
        "bound",
        "int",
        "Bound for integer sampling; also enables shuffle",
        _spec("check"),
        metavar="N",
        default_text="unbounded",
    ),
    _exec(
        "--seed",
        "seed",
        "int",
        "Random seed for scheduler",
        _spec("run"),
        default=0,
        metavar="N",
    ),
    _exec(
        "--from-schedule",
        "from_schedule",
        "str",
        "Replay schedule JSON to consume",
        _spec("replay"),
        metavar="FILE",
        group="schedule-or-seed",
    ),
    _exec(
        "--seed",
        "seed",
        "int",
        "Random trace seed to replay",
        _spec("replay"),
        metavar="N",
        group="schedule-or-seed",
    ),
    _exec(
        "--instance",
        "instance",
        "str",
        "Name of a compiled @instance factory to use as the initial state",
        MODEL_COMMANDS,
        metavar="NAME",
    ),
    _exec(
        "--out-schedule",
        "out_schedule",
        "str",
        "Write a replay schedule for the first matching random trace",
        _spec("run"),
        metavar="PATH",
    ),
    _exec(
        "--out-schedule",
        "out_schedule",
        "str",
        "Re-emit the replayed schedule to PATH (use with --compat to convert a native "
        "schedule into the portable values representation)",
        _spec("replay"),
        metavar="PATH",
    ),
    _exec(
        "--out-itf",
        "out_itf",
        "str",
        _ITF_STREAM_HELP,
        _spec("run", "check"),
        metavar="PATH",
    ),
    _exec(
        "--out-itf",
        "out_itf",
        "str",
        "Write the replayed trace to an ITF JSON file",
        _spec("replay"),
        metavar="PATH",
    ),
    _exec(
        "--no-progress",
        "no_progress",
        "bool",
        "Disable periodic progress output",
        _spec("run", "check"),
    ),
    _exec(
        "--no-action-profiling",
        "no_action_profiling",
        "bool",
        "Disable per-action tried/fired profiling (only @action(inline=False) actions "
        "are profiled)",
        _spec("run", "check"),
    ),
    _exec(
        "--max-memory",
        "max_memory",
        "size",
        "Execution memory limit, for example 512M or 4G",
        MODEL_COMMANDS,
        metavar="SIZE",
    ),
    _exec(
        "--timeout",
        "timeout",
        "duration",
        _DURATION_HELP.format("run"),
        _spec("run"),
        metavar="DURATION",
    ),
    _exec(
        "--timeout",
        "timeout",
        "duration",
        _DURATION_HELP.format("check"),
        _spec("check"),
        metavar="DURATION",
    ),
    _exec(
        "--stack-size",
        "stack_size",
        "size",
        "Stack size for the model-execution thread, e.g. 64M or 1G",
        _spec("run"),
        metavar="SIZE",
        default_text="64M",
    ),
    _exec(
        "--stack-size",
        "stack_size",
        "size",
        "Stack size for each worker thread, e.g. 64M or 1G; raise it if deep states "
        "overflow the stack under --workers",
        _spec("check"),
        metavar="SIZE",
        default_text="64M",
    ),
    _exec(
        "--max-findings",
        "max_findings",
        "int",
        "Stop after this many invariant violations or examples",
        _spec("run", "check"),
        default=1,
        metavar="N",
    ),
    _exec(
        "--coverage",
        "coverage",
        "str",
        "Name of an @coverage function to track and write to coverage.json",
        _spec("run"),
        metavar="NAME",
    ),
    _exec(
        "--invariant-cache",
        "invariant_cache",
        "str",
        "Invariant result cache strategy: 'lru' (100k entries) or 'no' (disabled)",
        _spec("run"),
        default="lru",
        choices=("lru", "no"),
    ),
    _exec(
        "--no-shuffle",
        "no_shuffle",
        "bool",
        "Disable pseudo-random shuffling of DFS exploration order",
        _spec("check"),
        model=False,
    ),
    _exec(
        "--shuffle-seed",
        "shuffle_seed",
        "int",
        "Seed for pseudo-random DFS exploration order",
        _spec("check"),
        metavar="N",
        default_text="random",
        model_default_text="no shuffling",
    ),
    _exec(
        "--visited",
        "visited",
        "str",
        "Visited-state tracking strategy",
        _spec("check"),
        choices=("exact", "fingerprint", "bitvec"),
        default_text="fingerprint",
    ),
    _exec(
        "--bitvec-size",
        "bitvec_size",
        "int",
        "Size in MB for bitvec visited-state tracking; implies --visited=bitvec",
        _spec("check"),
        metavar="MB",
        default_text="16",
    ),
    _exec(
        "--workers",
        "workers",
        "int",
        _RUN_WORKERS_HELP,
        _spec("run"),
        default=1,
        metavar="N",
    ),
    _exec(
        "--workers",
        "workers",
        "int",
        "Number of parallel fingerprint-checking workers",
        _spec("check"),
        default=1,
        metavar="N",
    ),
    _exec(
        "--sync-interval-secs",
        "sync_interval_secs",
        "int",
        "Seconds between parallel statistics/progress syncs",
        _spec("run"),
        default=60,
        metavar="N",
    ),
    _exec(
        "--sync-interval-secs",
        "sync_interval_secs",
        "int",
        "Seconds between parallel visited-table syncs",
        _spec("check"),
        default=60,
        metavar="N",
    ),
    _exec("--compat", "compat", "bool", _COMPAT_HELP, _spec("run", "check")),
    _exec("--compat", "compat", "bool", _REPLAY_COMPAT_HELP, _spec("replay")),
    _exec(
        "--check-property",
        "check_property",
        "bool",
        "Check the property while replaying (used by printed replay commands)",
        _spec("replay"),
        hidden=True,
    ),
)

OPTIONS: tuple[OptionSpec, ...] = (
    INPUT_ROWS
    + TRANSLATION_ROWS
    + CACHE_ROWS
    + BUILD_ROWS
    + SERVICE_ROWS
    + EXECUTION_ROWS
)


def command_entries(command: str, client: str) -> list[OptionSpec]:
    """The rows of `command` offered by `client`, in table order."""
    return [e for e in OPTIONS if command in e.commands and client in e.clients]


def entries(command: str, flag: str) -> list[OptionSpec]:
    """The rows for `flag` in `command`, for any client."""
    return [e for e in OPTIONS if command in e.commands and e.flag == flag]


def model_help_for(entry: OptionSpec) -> str:
    """Help text of a model option, with the model's own default (FR-022, FR-026)."""
    text = entry.help
    shown = entry.model_default_text or entry.default_text
    if shown is None and entry.default is not None and entry.type != "bool":
        shown = str(entry.default)
    if shown is not None and not entry.required:
        text += f" (default: {shown})"
    return text


def model_options(command: str) -> list[OptionSpec]:
    """The rows of `command` that are forwarded to the generated model."""
    return [e for e in command_entries(command, WUNDERSPEC_RUST) if e.model]


class HelpFormatter(argparse.HelpFormatter):
    """Wraps help without breaking words at hyphens, so `[re-translates]` stays whole."""

    def _split_lines(self, text: str, width: int) -> list[str]:
        return textwrap.wrap(" ".join(text.split()), width, break_on_hyphens=False)


_ARGPARSE_TYPES: dict[str, Any] = {
    "int": int,
    "float": float,
    "str": str,
    "size": str,
    "duration": str,
}


def add_options(parser: argparse.ArgumentParser, command: str, client: str) -> None:
    """Add the arguments of `command` for `client` to `parser`."""
    groups: dict[str, Any] = {}
    for entry in command_entries(command, client):
        help_text = argparse.SUPPRESS if entry.hidden else entry.help_for(client)
        target: Any = parser
        if entry.group is not None:
            if entry.group not in groups:
                groups[entry.group] = parser.add_mutually_exclusive_group(
                    required=GROUPS[entry.group]
                )
            target = groups[entry.group]
        if entry.positional:
            target.add_argument(entry.dest, metavar=entry.metavar, help=help_text)
            continue
        kwargs: dict[str, Any] = {"dest": entry.dest, "help": help_text}
        if entry.type == "bool":
            kwargs.update(action="store_true", default=False)
        else:
            kwargs.update(
                type=_ARGPARSE_TYPES[entry.type],
                default=entry.default,
                metavar=entry.metavar,
            )
            if entry.choices is not None:
                kwargs["choices"] = list(entry.choices)
            if entry.required:
                kwargs["required"] = True
        target.add_argument(entry.flag, **kwargs)
