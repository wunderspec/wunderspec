"""Job polling and diagnostics shared by the `wunderspec rust` commands (feature 003;
the commands themselves live in wunderspec.rust_cli since spec 005)."""

import sys
import time
from pathlib import Path
from typing import TextIO

from wunderspec.remote.client import Client, NetworkError, ServiceError
from wunderspec.transfer._parse import parse_strict
from wunderspec.transfer._validate import validate_document


class StoppedWaiting(Exception):
    """The wait limit passed; the job continues on the service."""

    def __init__(self, job_id: str) -> None:
        super().__init__(job_id)
        self.job_id = job_id


class TranslationFailed(Exception):
    """The job failed; `job` holds its curated diagnostics."""

    def __init__(self, job: dict) -> None:
        super().__init__("translation failed")
        self.job = job


def wait_for_job(
    client: Client, job_id: str, wait_timeout: float, *, out: TextIO | None = None
) -> bytes:
    """Poll the job until it finishes and return its archive (spec 005 T017).

    Prints status changes to `out`. Raises StoppedWaiting when `wait_timeout` passes,
    TranslationFailed for a failed job, and ServiceError("expired") for an expired one.
    """
    out = out if out is not None else sys.stderr
    deadline = time.monotonic() + wait_timeout
    interval = 1.0
    last_status = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise StoppedWaiting(job_id)
        job = client.job(job_id, timeout=min(30, remaining))
        status = job.get("status")
        if status not in ("queued", "running", "succeeded", "failed", "expired"):
            raise NetworkError("invalid job status in service response")
        if status != last_status:
            print(f"job {job_id} {status}", file=out, flush=True)
            last_status = status
        if status == "failed":
            raise TranslationFailed(job)
        if status == "expired":
            raise ServiceError("expired", 410)
        if status == "succeeded":
            return client.artifact(
                job_id, timeout=min(30, max(0.001, deadline - time.monotonic()))
            )
        time.sleep(min(interval, max(0, deadline - time.monotonic())))
        interval = min(5, interval * 1.5)


def print_diagnostics(job: dict, root: Path | None) -> None:
    """Print curated diagnostics as FILE:LINE:COL: CODE: MESSAGE (003 contract)."""
    for diagnostic in job.get("diagnostics", []):
        if not isinstance(diagnostic, dict):
            continue
        location = diagnostic.get("location") or {}
        filename = location.get("file", "<spec>")
        if root is not None and isinstance(filename, str):
            filename = str(root / filename)
        print(
            f"{filename}:{location.get('line', 1)}:{location.get('column', 0)}: "
            f"{diagnostic.get('code', 'translation_failed')}: {diagnostic.get('message', 'translation failed')}",
            file=sys.stderr,
        )
    if not job.get("diagnostics"):
        print("translation failed", file=sys.stderr)


def read_exported_document(path: Path) -> dict:
    """Read and validate an exported transfer document (pretty-printed exports allowed)."""
    # Offline exports can be much larger when indented. The submitted compact
    # document still obeys the shared 5 MiB limit enforced by validate_document.
    maximum = 256 * 1024 * 1024
    with path.open("rb") as stream:
        data = stream.read(maximum + 1)
    return validate_document(parse_strict(data, max_bytes=maximum))
