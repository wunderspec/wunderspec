"""
Pretty printing utilities for AST nodes.

Supports both IPython's _repr_pretty_ protocol and fallback for vanilla Python.
"""

from __future__ import annotations

import os
import re
import sys
from io import StringIO
from typing import TYPE_CHECKING, Any

from wunderspec.ast.ast import (
    AlgebraNode,
    InNode,
    IteNode,
    LetNode,
    LitNode,
    Node,
    VarNode,
)
from wunderspec.ast.list_ast import (
    ListEnumNode,
    ListFilterNode,
    ListGetNode,
    ListKeysNode,
    ListRangeNode,
    ListReduceNode,
    ListSliceNode,
    ListUpdateNode,
)
from wunderspec.ast.map_ast import (
    MapEnumNode,
    MapGetNode,
    MapKeysNode,
    MapLambdaNode,
    MapSetNode,
)
from wunderspec.ast.record_ast import RecordCtorNode, RecordGetNode, RecordUpdateNode
from wunderspec.ast.set_ast import (
    AllMapsNode,
    AllRecordsNode,
    AllSubsetsNode,
    AllTuplesNode,
    ChooseNode,
    IntervalNode,
    SetEnumNode,
    SetFilterNode,
    SetMapNode,
    SetQuantNode,
    SetReduceNode,
)
from wunderspec.ast.temporal_ast import (
    AlwaysNode,
    EnabledNode,
    EventuallyNode,
    FairnessNode,
    ToTemporalNode,
)
from wunderspec.ast.tuple_ast import TupleCtorNode, TupleGetNode, TupleUpdateNode

if TYPE_CHECKING:
    from IPython.lib.pretty import RepresentationPrinter
    from rich.console import RenderableType

    HAS_IPYTHON = False
else:
    try:
        from IPython.lib.pretty import RepresentationPrinter

        HAS_IPYTHON = True
    except ImportError:
        HAS_IPYTHON = False


# Pygments style used when rendering through rich's __rich__ protocol.
# Set to a concrete style name (e.g. "monokai") to force it; "auto" detects the
# terminal background once and picks "ansi_dark"/"ansi_light" accordingly.
RICH_THEME = "auto"

# Cache for the detected theme (only used when RICH_THEME == "auto").
_DETECTED_THEME: str | None = None


def _query_terminal_bg_luminance() -> float | None:
    """Query the terminal background color via the OSC 11 escape sequence.

    Returns the background's relative luminance in [0, 1], or None when this is
    not an interactive POSIX terminal or the terminal does not answer.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    if "JPY_PARENT_PID" in os.environ:  # running under Jupyter
        return None
    try:
        import select
        import termios
        import tty
    except ImportError:
        return None  # non-POSIX (e.g. Windows)

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        sys.stdout.write("\033]11;?\033\\")  # query background color
        sys.stdout.flush()
        if not select.select([fd], [], [], 0.1)[0]:  # ~100ms timeout
            return None
        resp = os.read(fd, 64).decode("latin-1", "replace")
    except Exception:
        return None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

    m = re.search(r"rgb:([0-9a-f]+)/([0-9a-f]+)/([0-9a-f]+)", resp, re.IGNORECASE)
    if not m:
        return None
    r, g, b = (int(h, 16) / ((1 << (4 * len(h))) - 1) for h in m.groups())
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _detect_background_theme() -> str:
    """Best-effort dark/light detection, returning an ansi pygments style name."""
    # 1. COLORFGBG="fg;bg" (or "fg;default;bg"); a bg of 7/15 means light.
    fgbg = os.environ.get("COLORFGBG")
    if fgbg:
        return "ansi_light" if fgbg.split(";")[-1] in {"7", "15"} else "ansi_dark"
    # 2. Ask the terminal directly (interactive ttys only).
    lum = _query_terminal_bg_luminance()
    if lum is not None:
        return "ansi_light" if lum > 0.5 else "ansi_dark"
    # 3. Most terminals are dark.
    return "ansi_dark"


def _resolve_theme() -> str:
    """Resolve the pygments style for rich rendering (override > detect > default)."""
    global _DETECTED_THEME
    if RICH_THEME != "auto":
        return RICH_THEME
    if _DETECTED_THEME is None:
        _DETECTED_THEME = _detect_background_theme()
    return _DETECTED_THEME


def to_rich(text: str) -> "RenderableType":
    """Wrap pre-rendered pretty text as a rich renderable.

    Imported lazily: this is only reached through the ``__rich__`` protocol, i.e.
    when rich is installed and doing the rendering, so wunderspec never depends on
    rich at import time.
    """
    from rich.syntax import Syntax

    return Syntax(
        text,
        "python",
        theme=_resolve_theme(),
        background_color="default",
        word_wrap=False,
    )


def pretty(node: Node, max_width: int = 80) -> str:
    """Pretty print an AST node.

    Args:
        node: The AST node to pretty print.
        max_width: Maximum line width for formatting (default: 80).

    Returns:
        A nicely formatted string representation of the node.
    """
    if HAS_IPYTHON:
        stream = StringIO()
        printer = RepresentationPrinter(stream, max_width=max_width)
        node._repr_pretty_(printer, cycle=False)
        printer.flush()
        return stream.getvalue()
    else:
        # Fallback to custom implementation
        return _simple_pretty(node, indent=0, max_width=max_width)


def pretty_value(value: Any, max_width: int = 80) -> str:
    """Pretty print an interpreter value (``IValue`` or ``StateView``).

    Compact values stay on one line; nested records/maps/lists/tuples/sets that
    exceed ``max_width`` are broken across indented lines.
    """
    return _pretty_value(value, indent=0, max_width=max_width)


def _pretty_value(value: Any, indent: int = 0, max_width: int = 80) -> str:
    # Imported lazily to avoid a circular import (interpreter_value -> expr -> ast,
    # while ast/expr import this module only inside methods).
    from wunderspec.interpreter_value import (
        EnumeratedSetValue,
        ListValue,
        MapValue,
        RecordValue,
        StateView,
        TupleValue,
    )

    indent_str = "  " * indent
    next_indent = "  " * (indent + 1)

    # Anything that already fits on one line is left as-is.
    flat = str(value)
    if len(indent_str) + len(flat) <= max_width:
        return flat

    def _block(head: str, items: list[str], close: str) -> str:
        body = (",\n" + next_indent).join(items)
        return f"{head}\n{next_indent}{body}\n{indent_str}{close}"

    if isinstance(value, StateView):
        items = [
            f"{k}={_pretty_value(v, indent + 1, max_width)}"
            for k, v in (*value._mapping.items(), *value._params.items())
        ]
        return _block("StateView(", items, ")")

    if isinstance(value, RecordValue):
        items = [
            f"{name}={_pretty_value(v, indent + 1, max_width)}"
            for name, v in value.fields
        ]
        return _block("Record(", items, ")")

    if isinstance(value, MapValue):
        items = [
            f"{k} -> {_pretty_value(v, indent + 1, max_width)}"
            for k, v in sorted(value.mappings.items(), key=lambda kv: repr(kv[0]))
        ]
        return _block("Map(", items, ")")

    if isinstance(value, ListValue):
        items = [_pretty_value(e, indent + 1, max_width) for e in value.elements]
        return _block("[", items, "]")

    if isinstance(value, TupleValue):
        items = [_pretty_value(e, indent + 1, max_width) for e in value.elements]
        return _block("(", items, ")")

    if isinstance(value, EnumeratedSetValue):
        items = [_pretty_value(e, indent + 1, max_width) for e in value]
        return _block("Set({", items, "})")

    # Scalars and lazy/infinite sets fall back to their compact string form.
    return flat


def _simple_pretty(node: Node, indent: int = 0, max_width: int = 80) -> str:
    """Simple fallback pretty printer without IPython.

    Args:
        node: The AST node to format.
        indent: Current indentation level.
        max_width: Maximum line width.

    Returns:
        Formatted string representation.
    """
    indent_str = "  " * indent
    next_indent_str = "  " * (indent + 1)

    # Simple nodes that fit on one line
    if isinstance(node, (VarNode, LitNode)):
        return str(node)

    # Check if node should be compact (fits on one line)
    str_repr = str(node)
    if len(indent_str) + len(str_repr) <= max_width:
        return str_repr

    # Complex nodes that need multi-line formatting
    match node:
        case AlgebraNode():
            if len(node.args) == 1:
                # Unary operator
                arg_str = _simple_pretty(node.args[0], indent + 1, max_width)
                return f"{node.op.name}(\n{next_indent_str}{arg_str}\n{indent_str})"
            else:
                # Binary or n-ary operator
                args_strs = [
                    _simple_pretty(arg, indent + 1, max_width) for arg in node.args
                ]
                args_formatted = (",\n" + next_indent_str).join(args_strs)
                return (
                    f"{node.op.name}(\n{next_indent_str}{args_formatted}\n{indent_str})"
                )

        case IteNode():
            cond_str = _simple_pretty(node.condition, indent + 1, max_width)
            then_str = _simple_pretty(node.then_node, indent + 1, max_width)
            else_str = _simple_pretty(node.else_node, indent + 1, max_width)
            return (
                f"Ite(\n{next_indent_str}{cond_str},\n"
                f"{next_indent_str}{then_str},\n"
                f"{next_indent_str}{else_str}\n{indent_str})"
            )

        case InNode():
            elem_str = _simple_pretty(node.elem, indent + 1, max_width)
            set_str = _simple_pretty(node.set_node, indent + 1, max_width)
            return f"In(\n{next_indent_str}{elem_str},\n{next_indent_str}{set_str}\n{indent_str})"

        case LetNode():
            value_str = _simple_pretty(node.value, indent + 1, max_width)
            body_str = _simple_pretty(node.body, indent + 1, max_width)
            return (
                f"Let({repr(node.name)},\n{next_indent_str}{value_str},\n"
                f"{next_indent_str}{body_str}\n{indent_str})"
            )

        case TupleCtorNode():
            if len(node.elements) <= 2:
                return repr(node)
            elem_strs = [
                _simple_pretty(elem, indent + 1, max_width) for elem in node.elements
            ]
            elems_formatted = (",\n" + next_indent_str).join(elem_strs)
            return f"Tuple(\n{next_indent_str}{elems_formatted}\n{indent_str})"

        case TupleGetNode():
            tuple_str = _simple_pretty(node.tuple_node, indent + 1, max_width)
            return f"TupleGet(\n{next_indent_str}{tuple_str},\n{next_indent_str}{node.index}\n{indent_str})"

        case TupleUpdateNode():
            base_str = _simple_pretty(node.base_tuple, indent + 1, max_width)
            val_str = _simple_pretty(node.new_value, indent + 1, max_width)
            return (
                f"TupleUpdate(\n{next_indent_str}{base_str},\n"
                f"{next_indent_str}{node.index},\n"
                f"{next_indent_str}{val_str}\n{indent_str})"
            )

        case RecordCtorNode():
            if len(node.fields) <= 2:
                return repr(node)
            field_strs = [
                f"{name}={_simple_pretty(val, indent + 1, max_width)}"
                for name, val in node.fields
            ]
            fields_formatted = (",\n" + next_indent_str).join(field_strs)
            return f"Record(\n{next_indent_str}{fields_formatted}\n{indent_str})"

        case RecordGetNode():
            rec_str = _simple_pretty(node.record_node, indent + 1, max_width)
            return f"RecordGet(\n{next_indent_str}{rec_str},\n{next_indent_str}{repr(node.field_name)}\n{indent_str})"

        case RecordUpdateNode():
            base_str = _simple_pretty(node.base_record, indent + 1, max_width)
            update_strs = [
                f"{name}={_simple_pretty(val, indent + 1, max_width)}"
                for name, val in node.updates
            ]
            updates_formatted = (", ").join(update_strs)
            return f"RecordUpdate(\n{next_indent_str}{base_str},\n{next_indent_str}{updates_formatted}\n{indent_str})"

        case SetEnumNode():
            if len(node.elements) <= 3:
                return repr(node)
            elem_strs = [
                _simple_pretty(elem, indent + 1, max_width) for elem in node.elements
            ]
            elems_formatted = (",\n" + next_indent_str).join(elem_strs)
            return f"Set(\n{next_indent_str}{elems_formatted}\n{indent_str})"

        case IntervalNode():
            return repr(node)

        case SetFilterNode():
            if len(node.bindings) == 1:
                base_str = _simple_pretty(node.base_set, indent + 1, max_width)
                pred_str = _simple_pretty(node.body, indent + 1, max_width)
                return (
                    f"SetFilter(\n{next_indent_str}{repr(node.var)},\n"
                    f"{next_indent_str}{base_str},\n"
                    f"{next_indent_str}{pred_str}\n{indent_str})"
                )
            binding_strs = []
            for v, d in node.bindings:
                d_str = _simple_pretty(d, indent + 1, max_width)
                binding_strs.append(f"({repr(v)}, {d_str})")
            bindings_formatted = (",\n" + next_indent_str).join(binding_strs)
            body_str = _simple_pretty(node.body, indent + 1, max_width)
            return (
                f"SetFilter(\n{next_indent_str}[{bindings_formatted}],\n"
                f"{next_indent_str}{body_str}\n{indent_str})"
            )

        case SetMapNode():
            if len(node.bindings) == 1:
                base_str = _simple_pretty(node.base_set, indent + 1, max_width)
                mapper_str = _simple_pretty(node.body, indent + 1, max_width)
                return (
                    f"SetMap(\n{next_indent_str}{repr(node.var)},\n"
                    f"{next_indent_str}{base_str},\n"
                    f"{next_indent_str}{mapper_str}\n{indent_str})"
                )
            binding_strs = []
            for v, d in node.bindings:
                d_str = _simple_pretty(d, indent + 1, max_width)
                binding_strs.append(f"({repr(v)}, {d_str})")
            bindings_formatted = (",\n" + next_indent_str).join(binding_strs)
            body_str = _simple_pretty(node.body, indent + 1, max_width)
            return (
                f"SetMap(\n{next_indent_str}[{bindings_formatted}],\n"
                f"{next_indent_str}{body_str}\n{indent_str})"
            )

        case SetQuantNode():
            if len(node.bindings) == 1:
                base_str = _simple_pretty(node.base_set, indent + 1, max_width)
                pred_str = _simple_pretty(node.body, indent + 1, max_width)
                return (
                    f"SetQuant(\n{next_indent_str}{repr(node.quant.value)},\n"
                    f"{next_indent_str}{repr(node.var)},\n"
                    f"{next_indent_str}{base_str},\n"
                    f"{next_indent_str}{pred_str}\n{indent_str})"
                )
            binding_strs = []
            for v, d in node.bindings:
                d_str = _simple_pretty(d, indent + 1, max_width)
                binding_strs.append(f"({repr(v)}, {d_str})")
            bindings_formatted = (",\n" + next_indent_str).join(binding_strs)
            body_str = _simple_pretty(node.body, indent + 1, max_width)
            return (
                f"SetQuant(\n{next_indent_str}{repr(node.quant.value)},\n"
                f"{next_indent_str}[{bindings_formatted}],\n"
                f"{next_indent_str}{body_str}\n{indent_str})"
            )

        case SetReduceNode():
            base_str = _simple_pretty(node.base_set, indent + 1, max_width)
            fun_str = _simple_pretty(node.fun, indent + 1, max_width)
            init_str = _simple_pretty(node.initial, indent + 1, max_width)
            return (
                f"SetReduce(\n{next_indent_str}({repr(node.acc_var)}, {repr(node.elem_var)}),\n"
                f"{next_indent_str}{base_str},\n"
                f"{next_indent_str}{fun_str},\n"
                f"{next_indent_str}{init_str}\n{indent_str})"
            )

        case ListEnumNode():
            if len(node.elements) <= 3:
                return repr(node)
            elem_strs = [
                _simple_pretty(elem, indent + 1, max_width) for elem in node.elements
            ]
            elems_formatted = (",\n" + next_indent_str).join(elem_strs)
            return f"List(\n{next_indent_str}{elems_formatted}\n{indent_str})"

        case ListRangeNode():
            return repr(node)

        case ListGetNode():
            list_str = _simple_pretty(node.list_node, indent + 1, max_width)
            idx_str = _simple_pretty(node.index, indent + 1, max_width)
            return f"ListGet(\n{next_indent_str}{list_str},\n{next_indent_str}{idx_str}\n{indent_str})"

        case ListUpdateNode():
            base_str = _simple_pretty(node.base_list, indent + 1, max_width)
            idx_str = _simple_pretty(node.index, indent + 1, max_width)
            val_str = _simple_pretty(node.new_value, indent + 1, max_width)
            return (
                f"ListUpdate(\n{next_indent_str}{base_str},\n"
                f"{next_indent_str}{idx_str},\n"
                f"{next_indent_str}{val_str}\n{indent_str})"
            )

        case ListSliceNode():
            base_str = _simple_pretty(node.base_list, indent + 1, max_width)
            start_str = _simple_pretty(node.start, indent + 1, max_width)
            end_str = _simple_pretty(node.end, indent + 1, max_width)
            return (
                f"ListSlice(\n{next_indent_str}{base_str},\n"
                f"{next_indent_str}{start_str},\n"
                f"{next_indent_str}{end_str}\n{indent_str})"
            )

        case ListFilterNode():
            base_str = _simple_pretty(node.base_list, indent + 1, max_width)
            pred_str = _simple_pretty(node.predicate, indent + 1, max_width)
            return (
                f"ListFilter(\n{next_indent_str}{repr(node.var)},\n"
                f"{next_indent_str}{base_str},\n"
                f"{next_indent_str}{pred_str}\n{indent_str})"
            )

        case ListReduceNode():
            base_str = _simple_pretty(node.base_list, indent + 1, max_width)
            fun_str = _simple_pretty(node.fun, indent + 1, max_width)
            init_str = _simple_pretty(node.initial, indent + 1, max_width)
            return (
                f"ListReduce(\n{next_indent_str}({repr(node.acc_var)}, {repr(node.elem_var)}),\n"
                f"{next_indent_str}{base_str},\n"
                f"{next_indent_str}{fun_str},\n"
                f"{next_indent_str}{init_str}\n{indent_str})"
            )

        case ListKeysNode():
            list_str = _simple_pretty(node.list_node, indent + 1, max_width)
            return f"ListKeys(\n{next_indent_str}{list_str}\n{indent_str})"

        case MapEnumNode():
            if len(node.mappings) <= 2:
                return repr(node)
            item_strs = [
                f"Tuple({_simple_pretty(k, indent + 1, max_width)}, {_simple_pretty(v, indent + 1, max_width)})"
                for k, v in node.mappings.items()
            ]
            items_formatted = (",\n" + next_indent_str).join(item_strs)
            return f"Map(\n{next_indent_str}{items_formatted}\n{indent_str})"

        case MapLambdaNode():
            base_str = _simple_pretty(node.base_set, indent + 1, max_width)
            mapper_str = _simple_pretty(node.mapper, indent + 1, max_width)
            return (
                f"MapLambda(\n{next_indent_str}{repr(node.var)},\n"
                f"{next_indent_str}{base_str},\n"
                f"{next_indent_str}{mapper_str}\n{indent_str})"
            )

        case MapGetNode():
            map_str = _simple_pretty(node.map_node, indent + 1, max_width)
            key_str = _simple_pretty(node.key, indent + 1, max_width)
            return f"MapGet(\n{next_indent_str}{map_str},\n{next_indent_str}{key_str}\n{indent_str})"

        case MapSetNode():
            name = "MapReplace" if node.replace_only else "MapSet"
            map_str = _simple_pretty(node.base_map, indent + 1, max_width)
            key_str = _simple_pretty(node.update_key, indent + 1, max_width)
            val_str = _simple_pretty(node.update_value, indent + 1, max_width)
            return (
                f"{name}(\n{next_indent_str}{map_str},\n"
                f"{next_indent_str}{key_str},\n"
                f"{next_indent_str}{val_str}\n{indent_str})"
            )

        case MapKeysNode():
            map_str = _simple_pretty(node.map_node, indent + 1, max_width)
            return f"MapKeys(\n{next_indent_str}{map_str}\n{indent_str})"

        case ChooseNode():
            base_str = _simple_pretty(node.base_set, indent + 1, max_width)
            pred_str = _simple_pretty(node.predicate, indent + 1, max_width)
            return (
                f"Choose(\n{next_indent_str}{repr(node.var)},\n"
                f"{next_indent_str}{base_str},\n"
                f"{next_indent_str}{pred_str}\n{indent_str})"
            )

        case AllSubsetsNode():
            base_str = _simple_pretty(node.base_set, indent + 1, max_width)
            return f"AllSubsets(\n{next_indent_str}{base_str}\n{indent_str})"

        case AllMapsNode():
            key_str = _simple_pretty(node.key_set, indent + 1, max_width)
            val_str = _simple_pretty(node.value_set, indent + 1, max_width)
            return (
                f"AllMaps(\n{next_indent_str}{key_str},\n"
                f"{next_indent_str}{val_str}\n{indent_str})"
            )

        case AllTuplesNode():
            set_strs = [_simple_pretty(s, indent + 1, max_width) for s in node.sets]
            sets_formatted = (",\n" + next_indent_str).join(set_strs)
            return f"AllTuples(\n{next_indent_str}{sets_formatted}\n{indent_str})"

        case AllRecordsNode():
            field_strs = [
                f"{name}={_simple_pretty(s, indent + 1, max_width)}"
                for name, s in sorted(node.field_sets.items())
            ]
            fields_formatted = (",\n" + next_indent_str).join(field_strs)
            return f"AllRecords(\n{next_indent_str}{fields_formatted}\n{indent_str})"

        case AlwaysNode():
            sub_str = _simple_pretty(node.subformula, indent + 1, max_width)
            return f"Always(\n{next_indent_str}{sub_str}\n{indent_str})"

        case EventuallyNode():
            sub_str = _simple_pretty(node.subformula, indent + 1, max_width)
            return f"Eventually(\n{next_indent_str}{sub_str}\n{indent_str})"

        case EnabledNode():
            action_str = _simple_pretty(node.action, indent + 1, max_width)
            return f"Enabled(\n{next_indent_str}{action_str}\n{indent_str})"

        case FairnessNode():
            kind_str = repr(node.kind)
            action_str = _simple_pretty(node.action, indent + 1, max_width)
            vars_str = repr(node.stuttering_vars)
            return (
                f"Fairness(\n{next_indent_str}{kind_str},\n"
                f"{next_indent_str}{action_str},\n"
                f"{next_indent_str}{vars_str}\n{indent_str})"
            )

        case ToTemporalNode():
            sub_str = _simple_pretty(node.bool_formula, indent + 1, max_width)
            return f"ToTemporal(\n{next_indent_str}{sub_str}\n{indent_str})"

        case _:
            # Fallback for any other node type
            return repr(node)
