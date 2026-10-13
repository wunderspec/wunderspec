"""The `wunderspec rust` commands (spec 005 R1; contracts/wunderspec-rust-cli.md).

`compile`, `run`, `check`, and `replay` mirror `wunderspec-rust`; `export`, `submit`, and `job`
are the service operations of feature 003 in command form.
"""

import argparse
import os
import shlex
import sys
import textwrap
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn
from uuid import UUID

from wunderspec.remote.client import Client, NetworkError, ServiceError
from wunderspec.remote.config import (
    ConfigError,
    ConfigNotFound,
    ServiceConfig,
    load_config,
)
from wunderspec.remote.rust import (
    StoppedWaiting,
    TranslationFailed,
    print_diagnostics,
    read_exported_document,
    wait_for_job,
)
from wunderspec.remote.unpack import ArchiveError, check_output, unpack
from wunderspec.rust_cli import cache, driver, exits
from wunderspec.rust_cli import options as opts
from wunderspec.transfer import TransferError, export_spec, write_document
from wunderspec.transfer._validate import validate_document

PROG = "wunderspec rust"

DESCRIPTIONS = {
    "compile": "Translate a specification on the service and build the model in a directory",
    "run": "Translate (or reuse a translation), build, and sample random traces",
    "check": "Translate (or reuse a translation), build, and check the model exhaustively",
    "replay": "Replay a schedule or a seed with the built model",
    "export": "Write the transfer document to a file without contacting the service",
    "submit": "Translate an exported transfer document into a directory",
    "job": "Wait for, or download, a job submitted earlier",
}


class UsageError(Exception):
    """A usage error found after parsing; exits with the usage status (FR-028)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(
            exits.USAGE,
            f"{self.prog}: error: {message}\nsee '{self.prog} --help'\n",
        )


class _HelpExit(argparse.Action):
    def __init__(
        self, option_strings: Sequence[str], dest: str, help: str | None = None
    ) -> None:
        super().__init__(option_strings, dest, nargs=0, help=help)

    def __call__(self, parser, namespace, values, option_string=None):  # type: ignore[no-untyped-def]
        sys.stdout.write(exits.exit_table_text())
        parser.exit(exits.OK)


def _epilog() -> str:
    root = cache.cache_root_path(os.environ, sys.platform, Path.home())
    paragraphs = (
        "Translation happens on the Wunderspec service and is reused while the "
        "specification and --init, --step, and --property stay the same; other options "
        "only change the local build or run.",
        f"Cache: {root} (WUNDERSPEC_CACHE_DIR). Service configuration: --config, "
        "WUNDERSPEC_CONFIG, or ~/.wunderspec/service.yaml.",
        f"Exit statuses: see '{PROG} --help-exit'. Run '{PROG} COMMAND --help' for a "
        "command's options.",
    )
    return "\n\n".join(textwrap.fill(p, 79) for p in paragraphs)


SUBPARSERS: dict[str, argparse.ArgumentParser] = {}


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog=PROG,
        description="Translate Wunderspec specifications to Rust on the Wunderspec "
        "service, then build and run the model locally.",
        epilog=_epilog(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--help-exit", action=_HelpExit, help="show the exit statuses and exit"
    )
    commands = parser.add_subparsers(
        dest="command", metavar="COMMAND", parser_class=_Parser
    )
    for name, description in DESCRIPTIONS.items():
        sub = commands.add_parser(
            name,
            help=description,
            description=description,
            formatter_class=opts.HelpFormatter,
        )
        opts.add_options(sub, name, opts.WUNDERSPEC_RUST_CLI)
        SUBPARSERS[name] = sub
    return parser


class CommandError(Exception):
    """A failure with a message for the engineer and an exit status of the contract."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


_ARGV: list[str] = []


def _same_command() -> str:
    return shlex.join(["wunderspec", "rust", *_ARGV])


def _say(message: str) -> None:
    """Messages of the command itself go to stderr, so stdout carries only results."""
    print(message, file=sys.stderr, flush=True)


# --- inputs -------------------------------------------------------------------------------


def _translation_options(args: argparse.Namespace) -> dict[str, str]:
    """The query options of a submission (003): init and step, and property if given."""
    options = {"init": args.init, "step": args.step}
    if args.property is not None:
        options["property"] = args.property
    return options


def _key_options(args: argparse.Namespace) -> dict[str, Any]:
    return {"init": args.init, "step": args.step, "property": args.property}


def _load_config(args: argparse.Namespace) -> tuple[ServiceConfig | None, str | None]:
    """The service configuration, or None and the reason when no file exists (R5).

    A file that exists but is invalid is always an error; a file named by --config must
    exist."""
    try:
        return load_config(args.config, os.environ), None
    except ConfigNotFound as missing:
        if args.config is not None:
            raise CommandError(str(missing), exits.CONFIG) from None
        return None, str(missing)
    except ConfigError as error:
        raise CommandError(str(error), exits.CONFIG) from None


def _export(args: argparse.Namespace) -> dict[str, Any]:
    try:
        document = export_spec(args.source, init=args.init, step=args.step)
        validate_document(document, _translation_options(args))
    except TransferError as error:
        message = str(error)
        if error.__cause__ is not None:
            message += f"\n{error.__cause__}"
        raise CommandError(message, exits.DATA) from None
    return document


def _check_instances(args: argparse.Namespace, document: dict[str, Any]) -> None:
    """A model command needs an instance the transfer document carries. The exporter skips,
    with a warning, an instance whose values the transfer format cannot carry (no value
    that the Python API builds is known to be skipped; wunderspec-saas spec 006), so say
    that here rather than let the model reject the option (FR-029)."""
    if not hasattr(args, "instance"):
        return  # compile builds without choosing an instance
    spec = document.get("spec", {})
    names = [i.get("name") for i in spec.get("instances", [])]
    limitation = "the export skipped it (see the warning above for the reason)"
    if args.instance is not None and args.instance not in names:
        exported = ", ".join(names) if names else "none"
        raise CommandError(
            f"instance '{args.instance}' is not in the transfer document: {limitation}; "
            f"exported instances: {exported}",
            exits.DATA,
        )
    if args.instance is None and spec.get("params") and not names:
        raise CommandError(
            "the specification has parameters, but none of its instances is in the "
            "transfer document: the export skipped them (see the warnings above for the "
            "reasons)",
            exits.DATA,
        )


def _refused(error: ServiceError, document: dict[str, Any]) -> CommandError:
    """The service refused a submission (exit 65 or 69). For a service that does not accept
    the format this wunderspec writes, name both versions (spec 006, FR-008)."""
    if error.code == "invalid_document":
        return CommandError(str(error), exits.DATA)
    if error.code == "unsupported_format_version":
        accepted = ", ".join(error.supported_versions or []) or "another version"
        return CommandError(
            f"the service accepts transfer format {accepted} only; this wunderspec writes "
            f"{document.get('version')}; use a wunderspec release that matches the service",
            exits.UNAVAILABLE,
        )
    return CommandError(str(error), exits.UNAVAILABLE)


def _property_is_example(document: dict[str, Any], name: str | None) -> bool:
    if name is None:
        return False
    defs = document.get("spec", {}).get("defs", [])
    return any(d.get("name") == name and d.get("kind") == "example" for d in defs)


def _check_directory(
    directory: Path, force: bool, spec_path: Path, service: str | None
) -> None:
    """Accept an absent or empty `--to` directory, or a project of `wunderspec rust` for the
    same specification file and service; refuse anything else unless --force, which
    replaces the contents (FR-034, research R11). Runs before the specification is
    evaluated."""
    if directory.is_symlink() or (directory.exists() and not directory.is_dir()):
        raise CommandError(f"{directory}: must be a directory", exits.CANT_CREATE)
    if not directory.exists():
        return
    contents = [c for c in directory.iterdir() if c.name != cache.PROJECT_META]
    if not contents:
        return
    recorded = cache.read_entry(cache.EntryPaths.in_directory(directory))
    reason = "is not a project of 'wunderspec rust'"
    if recorded is not None:
        same_spec = recorded.get("spec_path") == str(spec_path)
        same_service = service is None or recorded.get("service") == (
            cache.normalize_service(service)
        )
        if same_spec and same_service:
            return
        reason = (
            f"holds a project for {recorded.get('spec_path')}"
            if not same_spec
            else f"holds a project from {recorded.get('service')}"
        )
    if not force:
        raise CommandError(
            f"{directory}: {reason}; use --force to replace its contents",
            exits.CANT_CREATE,
        )
    for child in directory.iterdir():
        cache.remove_tree(child)


# --- translation (R4-R7) ------------------------------------------------------------------


class Translation:
    """Where one translation lives and its entry data."""

    def __init__(self, paths: cache.EntryPaths, key: str, in_directory: bool) -> None:
        self.paths = paths
        self.key = key
        self.in_directory = in_directory
        self.data: dict[str, Any] = {}
        self.changed = False  # translated or built by this command (eviction, FR-014)


def _locate(
    args: argparse.Namespace,
    config: ServiceConfig | None,
    missing: str | None,
    document: dict[str, Any],
) -> tuple[Translation, str]:
    """The entry for this translation and the service it belongs to."""
    options = _key_options(args)
    directory = Path(args.output_dir).absolute() if args.output_dir else None
    if config is not None:
        service = config.service
    elif directory is not None:
        recorded = cache.read_entry(cache.EntryPaths.in_directory(directory))
        if recorded is None or not recorded.get("service"):
            raise CommandError(missing or "no service configuration", exits.CONFIG)
        service = recorded["service"]
    else:
        root = cache.cache_root(os.environ)
        services = cache.find_any_service(root, document, options)
        if not services:
            raise CommandError(missing or "no service configuration", exits.CONFIG)
        if len(services) > 1:
            raise CommandError(
                "translations from several services match ("
                + ", ".join(services)
                + "); choose one with --config",
                exits.CONFIG,
            )
        service = services[0]
    key = cache.translation_key(document, options, service)
    if directory is not None:
        return Translation(cache.EntryPaths.in_directory(directory), key, True), service
    root = cache.cache_root(os.environ)
    return Translation(cache.EntryPaths.in_cache(root, key), key, False), service


def _reuse_notice(data: dict[str, Any]) -> str:
    return (
        f"using translation from {data.get('translated_at')} (job {data.get('job_id')}, "
        f"translator {data.get('translator_version')}, "
        f"runtime {data.get('runtime_version')})"
    )


def _obtain(
    args: argparse.Namespace,
    translation: Translation,
    service: str,
    config: ServiceConfig | None,
    missing: str | None,
    document: dict[str, Any],
) -> dict[str, Any]:
    """Reuse a complete entry, resume a pending job, or submit (R7; FR-011, FR-013,
    FR-017, FR-018). The caller holds the entry's exclusive lock."""
    paths, key = translation.paths, translation.key
    previous = cache.read_entry(paths)
    if (
        previous is not None
        and not args.no_cache
        and cache.is_valid_complete(paths, previous, key)
    ):
        _say(_reuse_notice(previous))
        cache.touch(previous)
        cache.write_entry(paths, previous)
        return previous
    job_id = None
    if (
        previous is not None
        and not args.no_cache
        and previous.get("key") == key
        and previous.get("state") == "pending"
        and previous.get("job_id")
    ):
        job_id = str(previous["job_id"])
    if config is None:
        raise CommandError(missing or "no service configuration", exits.CONFIG)
    client = Client(config.service, config.token, config.ca_file)
    spec_path = Path(args.source).resolve() if args.source else None
    data = cache.new_entry(key, service, _key_options(args), spec_path)
    for _ in range(2):
        if job_id is None:
            try:
                job_id = client.submit(
                    document,
                    _translation_options(args),
                    timeout=min(30, args.wait_timeout),
                )
            except ServiceError as error:
                raise _refused(error, document) from None
            _say(f"job {job_id} queued")
            resumed = False
        else:
            _say(f"resuming job {job_id}")
            resumed = True
        cache.mark_pending(data, job_id)
        cache.write_entry(paths, data)
        try:
            archive = wait_for_job(client, job_id, args.wait_timeout)
        except (StoppedWaiting, KeyboardInterrupt, NetworkError):
            raise CommandError(
                f"job {job_id} is still running; resume with: {_same_command()}",
                exits.TEMPFAIL,
            ) from None
        except TranslationFailed as failed:
            spec_root = spec_path.parent if spec_path is not None else None
            print_diagnostics(failed.job, spec_root)
            _forget(translation, previous)
            raise CommandError("translation failed", exits.DATA) from None
        except ServiceError as error:
            if resumed and error.code in ("expired", "not_found"):
                _say(f"job {job_id} is no longer available; translating again")
                job_id = None
                continue
            _forget(translation, previous)
            raise CommandError(str(error), exits.UNAVAILABLE) from None
        try:
            manifest, files = cache.install_archive(
                paths, archive, job_id, in_directory=translation.in_directory
            )
        except ArchiveError as error:
            _forget(translation, previous)
            raise CommandError(str(error), exits.IO) from None
        cache.mark_complete(data, manifest, files)
        cache.write_entry(paths, data)
        translation.changed = True
        return data
    raise CommandError("the service lost the job twice", exits.UNAVAILABLE)


def _forget(translation: Translation, previous: dict[str, Any] | None) -> None:
    """Failed translations are not cached (FR-018): drop the pending state."""
    paths = translation.paths
    if (
        translation.in_directory
        and previous is not None
        and previous.get("state") == "complete"
    ):
        cache.write_entry(paths, previous)
        return
    paths.entry_file.unlink(missing_ok=True)


def _ensure_built(translation: Translation, profile: str, cargo_version: str) -> Path:
    data = translation.data
    project = translation.paths.project
    if not cache.is_current(project, data, profile):
        translation.changed = True
        try:
            cache.build(project, profile, data, cargo_version)
        except cache.BuildError as error:
            cache.write_entry(translation.paths, data)  # the translation stays (FR-036)
            raise CommandError(str(error), exits.BUILD) from None
        cache.write_entry(translation.paths, data)
    return project / str(data["builds"][profile]["binary"])


def _prepare(
    args: argparse.Namespace, *, build: bool
) -> tuple[Translation, dict[str, Any], str | None]:
    """Contract steps 3-10: toolchain, configuration, `--to`, export, translation, build.

    Returns the translation (with its data), the document, and the cargo version."""
    cargo_version = None
    if build:
        try:
            cargo_version = cache.check_toolchain()
        except cache.ToolchainError as error:
            raise CommandError(str(error), exits.TOOLCHAIN) from None
    config, missing = _load_config(args)
    if args.output_dir:
        _check_directory(
            Path(args.output_dir).absolute(),
            args.force,
            Path(args.source).resolve(),
            config.service if config is not None else None,
        )
    document = _export(args)
    _check_instances(args, document)
    translation, service = _locate(args, config, missing, document)
    with cache.EntryLock(translation.paths.lock_file):
        translation.data = _obtain(
            args, translation, service, config, missing, document
        )
        if build:
            assert cargo_version is not None
            _ensure_built(translation, args.build_profile, cargo_version)
    if translation.changed and not translation.in_directory:
        limit = cache.parse_size(os.environ.get("WUNDERSPEC_CACHE_MAX_SIZE") or "2G")
        cache.evict(translation.paths.meta.parent.parent, limit, keep=translation.key)
    return translation, document, cargo_version


# --- model commands -----------------------------------------------------------------------


def _run_model(
    args: argparse.Namespace,
    command: str,
    model_argv: list[str],
    translation: Translation,
) -> driver.ModelProcess:
    project = translation.paths.project
    with cache.EntryLock(translation.paths.lock_file, shared=True):
        relative = translation.data["builds"][args.build_profile]["binary"]
        process = driver.run_model(
            project / relative, model_argv, project, command, args
        )
    rejected = driver.rejected_option(process)
    if rejected is not None:
        raise CommandError(
            f"the cached model (translator {translation.data.get('translator_version')}) "
            f"does not accept {rejected}; run again with --no-cache to translate with the "
            "service's current translator",
            exits.MODEL_REJECTED_OPTION,
        )
    return process


def _hint(args: argparse.Namespace) -> driver.Hint:
    output_dir = Path(args.output_dir).absolute() if args.output_dir else None
    return driver.Hint(["wunderspec", "rust"], output_dir)


def _finish(outcome: driver.Outcome) -> int:
    if outcome.error is not None:
        print(f"error: {outcome.error}", file=sys.stderr)
    return outcome.exit_code


def _validated(validate: Callable[[], Any]) -> Any:
    try:
        return validate()
    except driver.ValidationError as error:
        raise CommandError(error.message, exits.USAGE) from None


def run_command(args: argparse.Namespace) -> int:
    _validated(lambda: driver.validate_run(args))
    translation, _, _ = _prepare(args, build=True)
    out_schedule = _validated(lambda: driver.run_schedule_path(args))
    process = _run_model(args, "run", driver.run_argv(args, out_schedule), translation)
    return _finish(driver.report_run(process, args, out_schedule, _hint(args)))


def check_command(args: argparse.Namespace) -> int:
    plan = _validated(lambda: driver.plan_check(args))
    translation, document, _ = _prepare(args, build=True)
    process = _run_model(args, "check", driver.check_argv(args, plan), translation)
    result = driver.parse_check(process, translation.paths.project)
    example = _property_is_example(document, args.property)
    return _finish(driver.report_check(result, args, _hint(args), example=example))


def replay_command(args: argparse.Namespace) -> int:
    _validated(lambda: driver.validate_replay(args))
    translation, _, _ = _prepare(args, build=True)
    out_schedule = driver.replay_schedule_path(args)
    process = _run_model(
        args, "replay", driver.replay_argv(args, out_schedule), translation
    )
    return _finish(driver.report_replay(process, args, out_schedule, _hint(args)))


def compile_command(args: argparse.Namespace) -> int:
    """Translate (or reuse) into `--to DIR` and build it there (FR-032, FR-033)."""
    translation, _, _ = _prepare(args, build=not args.no_build)
    data = translation.data
    project = translation.paths.project
    if args.no_build:
        print(
            f"wrote {project}; job {data.get('job_id')}; "
            f"translator {data.get('translator_version')}; "
            f"runtime {data.get('runtime_version')}"
        )
        return exits.OK
    binary = project / str(data["builds"][args.build_profile]["binary"])
    if translation.changed:
        print(f"built {binary}")
    else:
        print(
            f"binary is up to date: {binary} "
            "(use --no-cache to translate and build again)"
        )
    return exits.OK


# --- service commands (feature 003 behavior, spec 005 FR-004) -----------------------------


def _wait_timeout(seconds: float, flag: str) -> float:
    if not 0 < seconds < float("inf"):
        raise CommandError(f"{flag} must be a positive, finite number", exits.USAGE)
    return seconds


def _install_plain(
    client: Client, job_id: str, out: Path, args: argparse.Namespace, root: Path | None
) -> int:
    """Wait for a job and install its archive into `out` as 003 did (no cache entry)."""
    try:
        archive = wait_for_job(client, job_id, args.timeout)
    except (StoppedWaiting, KeyboardInterrupt, NetworkError):
        hint = shlex.join(["wunderspec", "rust", "job", job_id, "--to", str(out)])
        raise CommandError(
            f"job {job_id} is still running; resume with: {hint}", exits.TEMPFAIL
        ) from None
    except TranslationFailed as failed:
        print_diagnostics(failed.job, root)
        raise CommandError("translation failed", exits.DATA) from None
    except ServiceError as error:
        raise CommandError(str(error), exits.UNAVAILABLE) from None
    manifest = unpack(archive, out, job_id, force=args.force)
    print(
        f"wrote {out}; job {job_id}; "
        f"translator {manifest.get('translator_version', 'unknown')}; "
        f"runtime {manifest.get('runtime_version', 'unknown')}"
    )
    return exits.OK


def _service_client(args: argparse.Namespace) -> Client:
    try:
        config = load_config(args.config, os.environ)
    except ConfigError as error:
        raise CommandError(str(error), exits.CONFIG) from None
    return Client(config.service, config.token, config.ca_file)


def _plain_output(args: argparse.Namespace) -> Path:
    out = Path(args.output_dir)
    try:
        check_output(out, args.force)
    except ValueError as error:
        raise CommandError(f"{out}: {error}", exits.CANT_CREATE) from None
    return out


def export_command(args: argparse.Namespace) -> int:
    """Write the transfer document without network access or configuration (003 US4)."""
    document = _export(args)
    write_document(document, args.output_dir)
    print(f"exported {args.output_dir}")
    return exits.OK


def submit_command(args: argparse.Namespace) -> int:
    _wait_timeout(args.timeout, "--timeout")
    client = _service_client(args)
    out = _plain_output(args)
    try:
        document = read_exported_document(Path(args.document))
        validate_document(document, _translation_options(args))
    except TransferError as error:
        raise CommandError(str(error), exits.DATA) from None
    try:
        job_id = client.submit(
            document, _translation_options(args), timeout=min(30, args.timeout)
        )
    except ServiceError as error:
        raise _refused(error, document) from None
    _say(f"job {job_id} queued")
    return _install_plain(client, job_id, out, args, None)


def job_command(args: argparse.Namespace) -> int:
    try:
        job_id = str(UUID(args.job_id))
    except ValueError:
        raise CommandError("JOB_ID must be a UUID", exits.USAGE) from None
    _wait_timeout(args.timeout, "--timeout")
    client = _service_client(args)
    out = _plain_output(args)
    return _install_plain(client, job_id, out, args, None)


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "compile": compile_command,
    "run": run_command,
    "check": check_command,
    "replay": replay_command,
    "export": export_command,
    "submit": submit_command,
    "job": job_command,
}


def main(argv: Sequence[str]) -> int:
    """Run `wunderspec rust` with the arguments after `rust`; return the exit status."""
    _ARGV[:] = list(argv)
    parser = build_parser()
    args, extra = parser.parse_known_args(list(argv))
    if extra:
        # Report unknown arguments with the command's own usage (FR-028).
        target = SUBPARSERS.get(args.command or "", parser)
        target.error(f"unrecognized arguments: {' '.join(extra)}")
    if args.command is None:
        parser.print_help(sys.stderr)
        return exits.USAGE
    try:
        return HANDLERS[args.command](args)
    except CommandError as error:
        print(f"error: {error.message}", file=sys.stderr)
        return error.status
    except cache.CacheError as error:
        print(f"error: {error}", file=sys.stderr)
        return exits.CANT_CREATE
    except ArchiveError as error:
        print(f"error: {error}", file=sys.stderr)
        return exits.IO
    except NetworkError as error:
        print(f"error: {error}", file=sys.stderr)
        return exits.IO
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return exits.INTERRUPTED
    except OSError as error:
        print(
            f"error: cannot read input or write output: {error.strerror}",
            file=sys.stderr,
        )
        return exits.CANT_CREATE
