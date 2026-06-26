"""
Per-action profiling for ``wunderspec run`` and ``wunderspec check``.

For every named (non-inline) action that survives in the executed AST as an
``ActionCallNode``, we count how many times it was *tried* (entered) and how
many times it *fired* (its body evaluated without violating an assumption).

Only ``@action(inline=False)`` actions keep a distinct identity in the executed
AST; inlined actions are flattened into their parent and therefore are not
profiled individually.

Igor Konnov, 2026
"""

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field

from wunderspec.ast.action_ast import (
    ActionAndNode,
    ActionCallNode,
    ActionChoiceNode,
    ActionLetNode,
    ActionNode,
    NondetChoiceNode,
)
from wunderspec.ast.ast import Node

# The title printed above the action-profile table.
_TITLE = "Action profile (fired/tried):"

# Fire rate (fired/tried) at or below this percentage is highlighted in red:
# such actions almost never fire and are likely dead or rarely enabled.
_LOW_FIRE_PCT = 1.0

# ANSI color codes used when coloring is enabled.
_RED = "\033[31m"
_RESET = "\033[0m"


@dataclass
class ActionProfiler:
    """Mutable accumulator of per-action tried/fired counts during execution."""

    tried: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))
    fired: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))

    def register(self, name: str) -> None:
        """Ensure ``name`` appears in the profile, even if never tried.

        Used to seed the profiler with every known action up front, so actions
        that are never reached still show up in the table as ``0/0``.
        """
        self.tried.setdefault(name, 0)
        self.fired.setdefault(name, 0)

    def enter(self, name: str) -> None:
        """Record that the action ``name`` was entered (tried)."""
        self.tried[name] += 1

    def succeeded(self, name: str) -> None:
        """Record that the action ``name`` fired (its body succeeded)."""
        self.fired[name] += 1


def collect_action_names(node: ActionNode) -> set[str]:
    """Collect the names of all named (non-inline) actions reachable in *node*.

    Walks the action AST and gathers every :class:`ActionCallNode` name,
    descending into call bodies so nested non-inline actions are included too.
    """
    names: set[str] = set()

    def walk(n: Node) -> None:
        if isinstance(n, ActionCallNode):
            names.add(n.action_name)
            walk(n.body)
        elif isinstance(n, (ActionChoiceNode, ActionAndNode)):
            for action in n.actions:
                walk(action)
        elif isinstance(n, (NondetChoiceNode, ActionLetNode)):
            walk(n.body)
        # AssumeNode / AssignNode are leaves with no nested actions.

    walk(node)
    return names


@dataclass(frozen=True)
class ActionProfile:
    """Immutable snapshot of accumulated per-action profiling counts."""

    tried: Mapping[str, int]
    fired: Mapping[str, int]

    @classmethod
    def from_profiler(cls, profiler: ActionProfiler) -> "ActionProfile":
        return cls(tried=dict(profiler.tried), fired=dict(profiler.fired))


def render_action_profile(
    profile: ActionProfile, width: int, use_color: bool = False
) -> list[str]:
    """Render the profile as a compact ``fired/tried (pct%)`` table.

    Each cell shows ``name fired/tried (pct%)`` where ``pct`` is the fire rate
    ``fired/tried``. Actions are sorted by name and laid out into a grid that is
    filled horizontally first, then wrapped vertically, packing as many columns
    as fit into ``width``. When ``use_color`` is set, cells whose fire rate is at
    or below ``_LOW_FIRE_PCT`` are highlighted in red. Returns the lines to print,
    or an empty list when there is nothing to profile (e.g. a fully inlined spec),
    so callers print nothing in that case.
    """
    names = sorted(profile.tried)
    if not names:
        return []

    # Each cell carries its plain text and whether its fire rate is near zero.
    cells: list[tuple[str, bool]] = []
    for name in names:
        tried = profile.tried[name]
        fired = profile.fired.get(name, 0)
        pct = (fired / tried * 100.0) if tried else 0.0
        cells.append((f"{name} {fired}/{tried} ({pct:.0f}%)", pct <= _LOW_FIRE_PCT))

    cell_width = max(len(text) for text, _ in cells)
    gap = 2
    # Maximum number of columns whose padded cells fit into the given width.
    # A row of n columns occupies n*cell_width + (n-1)*gap characters.
    columns = max(1, (max(width, cell_width) + gap) // (cell_width + gap))

    lines = [_TITLE]
    for start in range(0, len(cells), columns):
        row = cells[start : start + columns]
        parts: list[str] = []
        for i, (text, is_low) in enumerate(row):
            # Pad every cell but the last in the row, to avoid trailing spaces
            # (which also keeps color codes from wrapping invisible padding).
            padded = text if i == len(row) - 1 else text.ljust(cell_width)
            if use_color and is_low:
                padded = f"{_RED}{padded}{_RESET}"
            parts.append(padded)
        lines.append((" " * gap).join(parts))
    return lines
