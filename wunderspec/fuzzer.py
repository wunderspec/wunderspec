"""Open-core build placeholder for a Wunderspec Premium module."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from wunderspec._edition import require_feature

if TYPE_CHECKING:

    class FuzzerCorpus:
        def __init__(self) -> None: ...
        def __len__(self) -> int: ...
        def load(self, directory: Path) -> None: ...
        def save(self, directory: Path) -> None: ...
        def save_args(self, directory: Path, args: dict[str, Any]) -> None: ...

    class FuzzStats:
        generations: int
        total_execs: int
        total_steps: int
        total_retries: int
        corpus_size: int
        violations: int
        examples_found: int
        timed_out: bool
        violation_schedules: list[tuple[int, ...]]
        example_schedules: list[tuple[int, ...]]

    def fuzz(*args: Any, **kwargs: Any) -> FuzzStats: ...


require_feature("fuzz")
