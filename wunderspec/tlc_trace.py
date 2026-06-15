"""Parse TLC counterexample traces into Wunderspec StateView objects."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from typing import Mapping, cast

from wunderspec.ast.sorts import (
    BoolSort,
    EnumSort,
    IntSort,
    ListSort,
    MapSort,
    RecordSort,
    SetSort,
    Sort,
    StrSort,
    TupleSort,
    UnionSort,
)
from wunderspec.interpreter_value import (
    BoolValue,
    EnumeratedSetValue,
    EnumValue,
    IntValue,
    IValue,
    ListValue,
    MapValue,
    RecordValue,
    StateView,
    StrValue,
    TupleValue,
    UnionValue,
)
from wunderspec.tla import _union_tag_names


@dataclass(frozen=True)
class TlcTrace:
    raw_trace: str
    trace: tuple[StateView, ...]
    stdout_without_trace: str


@dataclass(frozen=True)
class _RawSeq:
    values: tuple["_RawValue", ...]


@dataclass(frozen=True)
class _RawSet:
    values: tuple["_RawValue", ...]


@dataclass(frozen=True)
class _RawPairs:
    pairs: tuple[tuple["_RawValue", "_RawValue"], ...]


_RawValue = int | bool | str | _RawSeq | _RawSet | _RawPairs


class _TokenStream:
    def __init__(self, text: str) -> None:
        self.tokens = _tokenize(text)
        self.pos = 0

    def peek(self) -> str | None:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def pop(self) -> str:
        token = self.peek()
        if token is None:
            raise ValueError("Unexpected end of TLC value")
        self.pos += 1
        return token

    def expect(self, expected: str) -> None:
        actual = self.pop()
        if actual != expected:
            raise ValueError(f"Expected {expected!r}, got {actual!r}")


def _parse_trace_block(
    raw_trace: str,
    stdout_without_trace: str,
    *,
    state_sorts: Mapping[str, Sort],
    params: Mapping[str, IValue],
) -> TlcTrace:
    """Parse one already-extracted raw TLC trace block into a ``TlcTrace``."""
    raw_states = _parse_state_assignments(raw_trace)
    state_views = []
    for raw_state in raw_states:
        values = {
            name: _raw_to_ivalue(raw, state_sorts[name])
            for name, raw in raw_state.items()
            if name in state_sorts
        }
        state_views.append(StateView(values, params))

    return TlcTrace(
        raw_trace=raw_trace,
        trace=tuple(state_views),
        stdout_without_trace=stdout_without_trace,
    )


def parse_tlc_trace(
    stdout: str,
    *,
    state_sorts: Mapping[str, Sort],
    params: Mapping[str, IValue] | None = None,
) -> TlcTrace | None:
    """Extract and parse the first TLC counterexample trace from stdout."""
    raw_trace, stdout_without_trace = extract_tlc_trace_block(stdout)
    if raw_trace is None:
        return None
    return _parse_trace_block(
        raw_trace,
        stdout_without_trace,
        state_sorts=state_sorts,
        params=params if params is not None else {},
    )


def parse_tlc_traces(
    stdout: str,
    *,
    state_sorts: Mapping[str, Sort],
    params: Mapping[str, IValue] | None = None,
) -> list[TlcTrace]:
    """Extract and parse every TLC counterexample trace from stdout.

    With ``-continue`` TLC reports several error-trace blocks; each becomes its
    own ``TlcTrace``. The returned traces share ``stdout_without_trace`` (the
    stdout with *all* trace blocks removed).
    """
    raw_blocks, stdout_without_traces = extract_tlc_trace_blocks(stdout)
    param_values = params if params is not None else {}
    return [
        _parse_trace_block(
            raw_block,
            stdout_without_traces,
            state_sorts=state_sorts,
            params=param_values,
        )
        for raw_block in raw_blocks
    ]


def extract_tlc_trace_block(stdout: str) -> tuple[str | None, str]:
    """Return the raw TLC trace block and stdout with that block removed."""
    lines = stdout.splitlines()
    marker_idx: int | None = None
    for idx, line in enumerate(lines):
        if "Error: The behavior up to this point is:" in line:
            marker_idx = idx
            break
        if re.search(r"Error: Invariant .* is violated by the initial state:", line):
            marker_idx = idx
            break
    if marker_idx is None:
        return None, stdout

    end_idx = marker_idx + 1
    saw_assignment = False
    while end_idx < len(lines):
        line = lines[end_idx]
        stripped = line.strip()
        if stripped.startswith("/\\ "):
            saw_assignment = True
        elif re.match(r"State \d+:", stripped):
            pass
        elif stripped == "":
            pass
        elif stripped.startswith("Error:") and saw_assignment:
            break
        elif (
            saw_assignment
            and not line.startswith((" ", "\t"))
            and not stripped.startswith("/\\ ")
        ):
            break
        end_idx += 1

    raw_lines = lines[marker_idx + 1 : end_idx]
    raw_trace = "\n".join(raw_lines).strip()
    filtered_lines = lines[:marker_idx] + lines[end_idx:]
    return raw_trace, "\n".join(filtered_lines).strip()


def extract_tlc_trace_blocks(stdout: str) -> tuple[list[str], str]:
    """Extract every TLC error-trace block in document order.

    TLC run with ``-continue`` reports multiple counterexamples. Returns the
    list of raw trace blocks and the stdout with all of them removed.
    """
    blocks: list[str] = []
    remaining = stdout
    while True:
        block, remaining = extract_tlc_trace_block(remaining)
        if block is None:
            break
        blocks.append(block)
    return blocks, remaining


def _parse_state_assignments(raw_trace: str) -> list[dict[str, _RawValue]]:
    states: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    current_name: str | None = None

    def finish_assignment() -> None:
        nonlocal current_name
        if current is None or current_name is None:
            return
        current[current_name] = current[current_name].strip()
        current_name = None

    def ensure_state() -> dict[str, str]:
        nonlocal current
        if current is None:
            current = {}
            states.append(current)
        return current

    for line in raw_trace.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"State \d+:", stripped):
            finish_assignment()
            current = {}
            states.append(current)
            continue
        if stripped.startswith("/\\ "):
            finish_assignment()
            state = ensure_state()
            assignment = stripped.removeprefix("/\\ ").strip()
            name, value = assignment.split("=", 1)
            current_name = name.strip()
            state[current_name] = value.strip()
            continue
        if current is not None and current_name is not None:
            current[current_name] += " " + stripped

    finish_assignment()
    parsed_states: list[dict[str, _RawValue]] = []
    for state in states:
        parsed_states.append(
            {name: _parse_value_text(value) for name, value in state.items()}
        )
    return parsed_states


def _parse_value_text(text: str) -> _RawValue:
    stream = _TokenStream(text)
    value = _parse_value(stream)
    if stream.peek() is not None:
        raise ValueError(f"Unexpected token {stream.peek()!r} in TLC value {text!r}")
    return value


def _parse_value(stream: _TokenStream) -> _RawValue:
    token = stream.pop()
    if token == "TRUE":
        return True
    if token == "FALSE":
        return False
    if re.fullmatch(r"-?\d+", token):
        return int(token)
    if token.startswith('"'):
        return cast(str, ast.literal_eval(token))
    if token == "{":
        return _RawSet(_parse_comma_values(stream, "}"))
    if token == "<<":
        return _RawSeq(_parse_comma_values(stream, ">>"))
    if token == "[":
        return _RawPairs(_parse_pairs(stream, "]", "|->", ","))
    if token == "(":
        first = _parse_value(stream)
        if stream.peek() == ":>":
            stream.pop()
            value = _parse_value(stream)
            pairs = [(first, value)]
            while stream.peek() == "@@":
                stream.pop()
                key = _parse_value(stream)
                stream.expect(":>")
                pairs.append((key, _parse_value(stream)))
            stream.expect(")")
            return _RawPairs(tuple(pairs))
        stream.expect(")")
        return first
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", token):
        return token
    raise ValueError(f"Unexpected TLC token {token!r}")


def _parse_comma_values(stream: _TokenStream, terminator: str) -> tuple[_RawValue, ...]:
    if stream.peek() == terminator:
        stream.pop()
        return ()
    values = [_parse_value(stream)]
    while stream.peek() == ",":
        stream.pop()
        values.append(_parse_value(stream))
    stream.expect(terminator)
    return tuple(values)


def _parse_pairs(
    stream: _TokenStream,
    terminator: str,
    arrow: str,
    separator: str,
) -> tuple[tuple[_RawValue, _RawValue], ...]:
    if stream.peek() == terminator:
        stream.pop()
        return ()
    pairs = []
    while True:
        key = _parse_value(stream)
        stream.expect(arrow)
        pairs.append((key, _parse_value(stream)))
        if stream.peek() != separator:
            break
        stream.pop()
    stream.expect(terminator)
    return tuple(pairs)


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
            continue
        for symbol in ("<<", ">>", "|->", "@@", ":>"):
            if text.startswith(symbol, pos):
                tokens.append(symbol)
                pos += len(symbol)
                break
        else:
            ch = text[pos]
            if ch in "{}[](),":
                tokens.append(ch)
                pos += 1
            elif ch == '"':
                end = pos + 1
                escaped = False
                while end < len(text):
                    if text[end] == '"' and not escaped:
                        end += 1
                        break
                    escaped = text[end] == "\\" and not escaped
                    if text[end] != "\\":
                        escaped = False
                    end += 1
                tokens.append(text[pos:end])
                pos = end
            else:
                match = re.match(r"-?\d+|[A-Za-z_][A-Za-z0-9_]*", text[pos:])
                if match is None:
                    raise ValueError(f"Unexpected character {text[pos]!r}")
                tokens.append(match.group(0))
                pos += len(match.group(0))
    return tokens


def _raw_to_ivalue(raw: _RawValue, sort: Sort) -> IValue:
    if isinstance(sort, BoolSort):
        if not isinstance(raw, bool):
            raise ValueError(f"Expected Bool, got {raw!r}")
        return BoolValue(raw)
    if isinstance(sort, IntSort):
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise ValueError(f"Expected Int, got {raw!r}")
        return IntValue(raw)
    if isinstance(sort, StrSort):
        if not isinstance(raw, str):
            raise ValueError(f"Expected Str, got {raw!r}")
        return StrValue(raw)
    if isinstance(sort, EnumSort):
        if not isinstance(raw, str):
            raise ValueError(f"Expected enum string, got {raw!r}")
        enum_name = sort.enum_type.__name__.upper()
        suffix = f"_OF_{enum_name}"
        member_name = raw.removesuffix(suffix)
        return EnumValue(sort.enum_type[member_name])
    if isinstance(sort, SetSort):
        if not isinstance(raw, _RawSet):
            raise ValueError(f"Expected set, got {raw!r}")
        return EnumeratedSetValue(
            *(_raw_to_ivalue(elem, sort.elem_sort) for elem in raw.values),
            elem_sort=sort.elem_sort,
        )
    if isinstance(sort, ListSort):
        if not isinstance(raw, _RawSeq):
            raise ValueError(f"Expected sequence, got {raw!r}")
        return ListValue(
            [_raw_to_ivalue(elem, sort.elem_sort) for elem in raw.values],
            elem_sort=sort.elem_sort,
        )
    if isinstance(sort, TupleSort):
        if not isinstance(raw, _RawSeq):
            raise ValueError(f"Expected tuple, got {raw!r}")
        if len(raw.values) != len(sort.elem_sorts):
            raise ValueError(f"Expected {len(sort.elem_sorts)} tuple elements")
        return TupleValue(
            *(
                _raw_to_ivalue(elem, elem_sort)
                for elem, elem_sort in zip(raw.values, sort.elem_sorts)
            )
        )
    if isinstance(sort, MapSort):
        pairs = _raw_map_pairs(raw)
        return MapValue(
            {
                _raw_to_ivalue(key, sort.key_sort): _raw_to_ivalue(
                    value, sort.value_sort
                )
                for key, value in pairs
            },
            key_sort=sort.key_sort,
            value_sort=sort.value_sort,
        )
    if isinstance(sort, RecordSort):
        if not isinstance(raw, _RawPairs):
            raise ValueError(f"Expected record, got {raw!r}")
        fields = {}
        for key, value in raw.pairs:
            if not isinstance(key, str):
                raise ValueError(f"Expected record field name, got {key!r}")
            fields[key] = _raw_to_ivalue(value, sort[key])
        return RecordValue(**fields)
    if isinstance(sort, UnionSort):
        if not isinstance(raw, _RawPairs) or len(raw.pairs) != 1:
            raise ValueError(f"Expected variant record, got {raw!r}")
        raw_tag, raw_payload = raw.pairs[0]
        if not isinstance(raw_tag, str):
            raise ValueError(f"Expected variant tag, got {raw_tag!r}")
        reverse_tags = {safe: tag for tag, safe in _union_tag_names(sort).items()}
        tag = reverse_tags.get(raw_tag, raw_tag)
        payload_sort = sort[tag]
        if payload_sort is None:
            return UnionValue(tag)
        return UnionValue(tag, _raw_to_ivalue(raw_payload, payload_sort))
    raise TypeError(f"Unsupported sort {sort!r}")


def _raw_map_pairs(raw: _RawValue) -> tuple[tuple[_RawValue, _RawValue], ...]:
    if isinstance(raw, _RawPairs):
        return raw.pairs
    if isinstance(raw, _RawSeq):
        return tuple((idx, value) for idx, value in enumerate(raw.values, start=1))
    raise ValueError(f"Expected function, got {raw!r}")
