"""User-facing formatting helpers for concrete traces."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol

from wadler_lindig import AbstractDoc, BreakDoc, ConcatDoc, GroupDoc, NestDoc, TextDoc

from wunderspec.doc_format import StyledDoc, render_doc
from wunderspec.interpreter_value import (
    AllMapsValue,
    AllRecordsValue,
    AllSubsetsValue,
    AllTuplesValue,
    BoolValue,
    EnumeratedSetValue,
    EnumValue,
    IntervalSetValue,
    IntValue,
    IValue,
    ListValue,
    MapValue,
    RecordValue,
    SetMapValue,
    StateView,
    StrValue,
    TupleValue,
    UnionValue,
)

RESET = "\033[0m"
BOLD = "\033[1m"
CYAN = "\033[36m"
BLUE = "\033[34m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
MAGENTA = "\033[35m"
DIM = "\033[2m"
DEFAULT_TRACE_WIDTH = 80
MIN_TRACE_VALUE_WIDTH = 20
TRACE_VALUE_INDENT = 2
_RAINBOW_DELIMITERS = (CYAN, YELLOW, MAGENTA, GREEN, BLUE)


@dataclass(frozen=True)
class TraceStyle:
    """Presentation options for user-facing trace output."""

    color: bool = False
    width: int = DEFAULT_TRACE_WIDTH
    syntax: bool = True


class TraceReporter(Protocol):
    def out(self, msg: str = "") -> None: ...


def format_trace_value(
    value: IValue,
    *,
    width: int | None = None,
    style: TraceStyle | None = None,
) -> str:
    """Format an interpreted value for human-readable trace output."""
    actual_style = style if style is not None else TraceStyle()
    render_width = max(1, width if width is not None else 10_000)
    return render_doc(_value_to_doc(value, actual_style, depth=0), render_width)


def iter_trace_fields(state: StateView) -> Iterator[tuple[str, str]]:
    """Yield state fields in the same stable order as StateView.to_python()."""
    for name, value in _iter_trace_field_values(state):
        yield name, format_trace_value(value)


def _iter_trace_field_values(
    state: StateView, *, include_params: bool = True
) -> Iterator[tuple[str, IValue]]:
    all_fields: dict[str, IValue] = {}
    if include_params:
        all_fields.update(state._params)  # type: ignore[attr-defined]
    all_fields.update(state._mapping)  # type: ignore[attr-defined]
    for name in sorted(all_fields):
        yield name, all_fields[name]


def _join_docs(docs: list[AbstractDoc], separator: AbstractDoc) -> AbstractDoc:
    parts: list[AbstractDoc] = []
    for i, doc in enumerate(docs):
        if i > 0:
            parts.append(separator)
        parts.append(doc)
    return ConcatDoc(*parts) if parts else TextDoc("")


def _breakable_comma_docs(docs: list[AbstractDoc]) -> AbstractDoc:
    return _join_docs(docs, TextDoc(",") + BreakDoc(" "))


def _syntax_enabled(style: TraceStyle) -> bool:
    return style.color and style.syntax


def _styled_doc(text: str, style: TraceStyle, *codes: str) -> AbstractDoc:
    doc: AbstractDoc = TextDoc(text)
    if not _syntax_enabled(style):
        return doc
    return StyledDoc(doc, codes)


def _constructor_doc(name: str, style: TraceStyle) -> AbstractDoc:
    return _styled_doc(name, style, BOLD, MAGENTA)


def _variant_doc(name: str, style: TraceStyle) -> AbstractDoc:
    return _styled_doc(name, style, BOLD, BLUE)


def _field_name_doc(name: str, style: TraceStyle) -> AbstractDoc:
    return _styled_doc(name, style, CYAN)


def _operator_doc(text: str, style: TraceStyle) -> AbstractDoc:
    return _styled_doc(text, style, DIM)


def _literal_doc(text: str, style: TraceStyle, *codes: str) -> AbstractDoc:
    return _styled_doc(text, style, *codes)


def _delimiter_doc(text: str, style: TraceStyle, depth: int) -> AbstractDoc:
    if not _syntax_enabled(style):
        return TextDoc(text)
    color = _RAINBOW_DELIMITERS[depth % len(_RAINBOW_DELIMITERS)]
    return StyledDoc(TextDoc(text), (color,))


def _delimited_doc(
    open_text: str,
    items: list[AbstractDoc],
    close_text: str,
    style: TraceStyle,
    depth: int,
) -> AbstractDoc:
    open_doc = _delimiter_doc(open_text, style, depth)
    close_doc = _delimiter_doc(close_text, style, depth)
    if not items:
        return open_doc + close_doc
    return GroupDoc(
        open_doc
        + NestDoc(
            BreakDoc("") + _breakable_comma_docs(items), indent=TRACE_VALUE_INDENT
        )
        + BreakDoc("")
        + close_doc
    )


def _call_doc(
    name: str,
    items: list[AbstractDoc],
    style: TraceStyle,
    depth: int,
    *,
    variant: bool = False,
) -> AbstractDoc:
    name_doc = _variant_doc(name, style) if variant else _constructor_doc(name, style)
    return name_doc + _delimited_doc("(", items, ")", style, depth)


def _field_doc(name: str, value: IValue, style: TraceStyle, depth: int) -> AbstractDoc:
    return GroupDoc(
        _field_name_doc(name, style)
        + _operator_doc("=", style)
        + NestDoc(_value_to_doc(value, style, depth), indent=TRACE_VALUE_INDENT)
    )


def _map_item_doc(
    key: IValue, value: IValue, style: TraceStyle, depth: int
) -> AbstractDoc:
    return GroupDoc(
        _value_to_doc(key, style, depth)
        + _operator_doc(" ->", style)
        + NestDoc(
            BreakDoc(" ") + _value_to_doc(value, style, depth),
            indent=TRACE_VALUE_INDENT,
        )
    )


def _braced_elements_doc(
    values: list[IValue], style: TraceStyle, depth: int
) -> AbstractDoc:
    return _delimited_doc(
        "{", [_value_to_doc(v, style, depth + 1) for v in values], "}", style, depth
    )


def _value_to_doc(value: IValue, style: TraceStyle, depth: int) -> AbstractDoc:
    if isinstance(value, BoolValue):
        return _literal_doc(str(value), style, BOLD, YELLOW)
    if isinstance(value, IntValue):
        return _literal_doc(str(value), style, YELLOW)
    if isinstance(value, StrValue):
        return _literal_doc(repr(value.value), style, GREEN)
    if isinstance(value, EnumValue):
        return _literal_doc(str(value), style, BOLD, GREEN)
    if isinstance(value, RecordValue):
        return _call_doc(
            "Record",
            [
                _field_doc(name, field_value, style, depth + 1)
                for name, field_value in value.fields
            ],
            style,
            depth,
        )
    if isinstance(value, TupleValue):
        return _delimited_doc(
            "(",
            [_value_to_doc(element, style, depth + 1) for element in value.elements],
            ")",
            style,
            depth,
        )
    if isinstance(value, UnionValue):
        if value.payload is None:
            return _variant_doc(value.tag, style)
        return _call_doc(
            value.tag,
            [_value_to_doc(value.payload, style, depth + 1)],
            style,
            depth,
            variant=True,
        )
    if isinstance(value, ListValue):
        return _delimited_doc(
            "[",
            [_value_to_doc(element, style, depth + 1) for element in value.elements],
            "]",
            style,
            depth,
        )
    if isinstance(value, MapValue):
        return _call_doc(
            "Map",
            [
                _map_item_doc(key, item_value, style, depth + 1)
                for key, item_value in sorted(
                    value.mappings.items(), key=lambda kv: repr(kv[0])
                )
            ],
            style,
            depth,
        )
    if isinstance(value, EnumeratedSetValue):
        if not value.material_set:
            return _call_doc("Set", [], style, depth)
        elements = sorted(value.material_set, key=str)
        return (
            _constructor_doc("Set", style)
            + _delimiter_doc("(", style, depth)
            + _braced_elements_doc(elements, style, depth + 1)
            + _delimiter_doc(")", style, depth)
        )
    if isinstance(value, SetMapValue):
        return _value_to_doc(value.materialize(), style, depth)
    if isinstance(value, IntervalSetValue):
        return _call_doc(
            "Set",
            [
                _literal_doc(str(value.start), style, YELLOW),
                _operator_doc("...", style),
                _literal_doc(str(value.end), style, YELLOW),
            ],
            style,
            depth,
        )
    if isinstance(value, AllSubsetsValue):
        elements = list(value._base_elements)  # type: ignore[attr-defined]
        return (
            _constructor_doc("AllSubsets", style)
            + _delimiter_doc("(", style, depth)
            + _braced_elements_doc(elements, style, depth + 1)
            + _delimiter_doc(")", style, depth)
        )
    if isinstance(value, AllMapsValue):
        keys = list(value._keys)  # type: ignore[attr-defined]
        values = list(value._values)  # type: ignore[attr-defined]
        return _call_doc(
            "AllMaps",
            [
                _braced_elements_doc(keys, style, depth + 1),
                _braced_elements_doc(values, style, depth + 1),
            ],
            style,
            depth,
        )
    if isinstance(value, AllTuplesValue):
        dimensions = [
            _braced_elements_doc(list(dimension), style, depth + 1)
            for dimension in value._dimension_elements  # type: ignore[attr-defined]
        ]
        return _call_doc("AllTuples", dimensions, style, depth)
    if isinstance(value, AllRecordsValue):
        field_names = value._field_names  # type: ignore[attr-defined]
        field_elements = value._field_elements  # type: ignore[attr-defined]
        return _call_doc(
            "AllRecords",
            [
                GroupDoc(
                    _field_name_doc(name, style)
                    + _operator_doc("=", style)
                    + NestDoc(
                        _braced_elements_doc(list(field_elements[i]), style, depth + 1),
                        indent=TRACE_VALUE_INDENT,
                    )
                )
                for i, name in enumerate(field_names)
            ],
            style,
            depth,
        )
    return TextDoc(str(value))


def _color(text: str, style: TraceStyle, *codes: str) -> str:
    if not style.color:
        return text
    return "".join(codes) + text + RESET


def iter_state_lines(
    step_idx: int,
    state: StateView,
    *,
    indent: str = "",
    style: TraceStyle | None = None,
) -> Iterator[str]:
    """Yield formatted lines for a single trace state."""
    actual_style = style if style is not None else TraceStyle()
    yield f"{indent}{_color(f'[State {step_idx}]', actual_style, BOLD, BLUE)}"
    for name, value in _iter_trace_field_values(state, include_params=step_idx == 0):
        rendered_name = _color(name, actual_style, CYAN)
        prefix = f"{indent}  {rendered_name}: "
        visible_prefix_width = len(indent) + 2 + len(name) + 2
        available_width = actual_style.width - visible_prefix_width
        if available_width < MIN_TRACE_VALUE_WIDTH:
            continuation_prefix = f"{indent}    "
            value_width = max(
                MIN_TRACE_VALUE_WIDTH,
                actual_style.width - len(continuation_prefix),
            )
            yield f"{indent}  {rendered_name}:"
        else:
            continuation_prefix = " " * visible_prefix_width
            value_width = available_width

        value_lines = format_trace_value(
            value, width=value_width, style=actual_style
        ).splitlines()
        if not value_lines:
            value_lines = [""]
        if available_width < MIN_TRACE_VALUE_WIDTH:
            for line in value_lines:
                yield f"{continuation_prefix}{line}"
        else:
            yield f"{prefix}{value_lines[0]}"
            for line in value_lines[1:]:
                yield f"{continuation_prefix}{line}"


def iter_trace_lines(
    trace: tuple[StateView, ...],
    *,
    indent: str = "",
    style: TraceStyle | None = None,
) -> Iterator[str]:
    """Yield formatted lines for every state in a trace."""
    actual_style = style if style is not None else TraceStyle()
    for step_idx, state in enumerate(trace):
        yield from iter_state_lines(
            step_idx,
            state,
            indent=indent,
            style=actual_style,
        )


def print_state(
    step_idx: int,
    state: StateView,
    reporter: TraceReporter,
    *,
    indent: str = "",
    style: TraceStyle | None = None,
) -> None:
    """Print a formatted state through a reporter with an ``out`` method."""
    for line in iter_state_lines(step_idx, state, indent=indent, style=style):
        reporter.out(line)


def print_trace(
    trace: tuple[StateView, ...],
    reporter: TraceReporter,
    *,
    indent: str = "",
    style: TraceStyle | None = None,
) -> None:
    """Print a formatted trace through a reporter with an ``out`` method."""
    for line in iter_trace_lines(trace, indent=indent, style=style):
        reporter.out(line)
