"""Stable transfer errors with JSON pointers and optional Python locations."""

from wunderspec.ast.ast import SourceSpan


class TransferError(ValueError):
    def __init__(
        self, problem: str, path: str = "", *, location: SourceSpan | None = None
    ):
        self.problem = problem
        self.path = path
        self.location = location
        super().__init__(problem, path)

    def __str__(self) -> str:
        prefix = (
            f"{self.location.filename}:{self.location.lineno}:{self.location.col_offset}: "
            if self.location
            else ""
        )
        return f"{prefix}{self.problem} at {self.path or '/'}"
