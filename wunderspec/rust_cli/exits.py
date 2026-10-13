"""Exit statuses of `wunderspec rust` (spec 005 R9, FR-006).

Model outcomes keep the statuses of the generated model and `wunderspec-rust`; every other
failure uses a status from the `sysexits.h` range or just above it, which no model outcome uses.
"""

OK = 0
VIOLATION = 1
EXAMPLE = 2
MEMORY_LIMIT = 3
PANIC = 101
TIMEOUT = 124
INTERRUPTED = 130

USAGE = 64
DATA = 65
UNAVAILABLE = 69
CANT_CREATE = 73
IO = 74
TEMPFAIL = 75
CONFIG = 78
TOOLCHAIN = 79
BUILD = 80
MODEL_REJECTED_OPTION = 81

_TABLE = (
    (OK, "success; for run, check, and replay: no violation or example found"),
    (VIOLATION, "invariant violated"),
    (EXAMPLE, "example found"),
    (MEMORY_LIMIT, "model memory limit exceeded"),
    (USAGE, "usage error"),
    (DATA, "the specification could not be evaluated, exported, or translated"),
    (UNAVAILABLE, "the service refused: authentication, quota, busy, format version"),
    (CANT_CREATE, "output directory refused"),
    (IO, "network or TLS error, or an invalid archive"),
    (TEMPFAIL, "stopped waiting for the service; the job continues"),
    (CONFIG, "service configuration error"),
    (TOOLCHAIN, "Rust toolchain missing or older than 1.85"),
    (BUILD, "local build failed"),
    (MODEL_REJECTED_OPTION, "the model rejected an option; run again with --no-cache"),
    (PANIC, "the model panicked"),
    (TIMEOUT, "model time limit reached"),
    (INTERRUPTED, "interrupted while evaluating, building, or running the model"),
)


def exit_table_text() -> str:
    """The exit status table for `wunderspec rust --help-exit`."""
    lines = ["Exit statuses of wunderspec rust:"]
    lines += [f"  {status:>3}  {meaning}" for status, meaning in _TABLE]
    return "\n".join(lines) + "\n"
