"""Render ITF traces as compact, human-readable explanations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from wadler_lindig import AbstractDoc, TextDoc

from wunderspec.doc_format import StyledDoc, render_doc
from wunderspec.trace_output import (
    BLUE,
    BOLD,
    CYAN,
    DIM,
    GREEN,
    MAGENTA,
    YELLOW,
    TraceStyle,
)

_RAINBOW_DELIMITERS = (CYAN, YELLOW, MAGENTA, GREEN, BLUE)
_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


@dataclass(frozen=True)
class DisplayValue:
    value: Any
    text: str


def render_itf_explanation(
    data: dict[str, Any], *, style: TraceStyle | None = None
) -> list[str]:
    """Render an ITF trace JSON object into explanation lines."""
    actual_style = style if style is not None else TraceStyle()
    meta, params, vars_, states = _trace_parts(data)
    lines: list[str] = []
    prev_values: dict[str, DisplayValue] | None = None
    for fallback_step, raw_state in enumerate(states):
        assert isinstance(raw_state, dict)
        state_meta = raw_state.get("#meta", {})
        assert isinstance(state_meta, dict)

        step_idx = _int_meta(state_meta, "step", fallback_step)
        values = _state_display_values(
            raw_state, params=params, vars_=vars_, style=actual_style
        )
        action_trace = _action_trace(state_meta)
        action_desc = _action_desc(action_trace, step_idx)

        if fallback_step == 0:
            lines.append(f"State {step_idx} ({action_desc}):")
        else:
            lines.append("")
            lines.append(f"State {step_idx} via {action_desc}:")

        for action in action_trace:
            lines.append(f"  > {action}")

        if prev_values is None:
            for name, display in values.items():
                lines.append(f"  {name} = {display.text}")
        else:
            changed: list[str] = []
            unchanged: list[str] = []
            for name, display in values.items():
                previous = prev_values.get(name)
                if previous is None or display.value != previous.value:
                    changed.append(name)
                else:
                    unchanged.append(name)
            for name in changed:
                previous = prev_values.get(name)
                before = "<unset>" if previous is None else previous.text
                lines.append(f"  {name}: {before} -> {values[name].text}")
            if unchanged:
                lines.append(f"  ({', '.join(unchanged)} unchanged)")

        prev_values = values

    _append_terminal_summary(lines, meta, states, params, vars_, actual_style)
    return lines


def render_itf_bdd_explanation(
    data: dict[str, Any], *, style: TraceStyle | None = None
) -> list[str]:
    """Render an ITF trace JSON object as a Gherkin-like scenario."""
    actual_style = style if style is not None else TraceStyle()
    meta, params, vars_, states = _trace_parts(data)
    lines = ["Scenario: Explain ITF trace"]
    prev_values: dict[str, DisplayValue] | None = None
    for fallback_step, raw_state in enumerate(states):
        assert isinstance(raw_state, dict)
        state_meta = raw_state.get("#meta", {})
        assert isinstance(state_meta, dict)

        step_idx = _int_meta(state_meta, "step", fallback_step)
        values = _state_display_values(
            raw_state, params=params, vars_=vars_, style=actual_style
        )
        if prev_values is None:
            lines.append(f"  Given state {step_idx}")
            for name, display in values.items():
                lines.append(f"  And {name} = {display.text}")
        else:
            action_desc = _action_desc(_action_trace(state_meta), step_idx)
            lines.append(f"  When state {step_idx} is reached via {action_desc}")
            changed = _changed_fields(prev_values, values)
            if changed:
                first = True
                for name in changed:
                    previous = prev_values.get(name)
                    before = "<unset>" if previous is None else previous.text
                    prefix = "Then" if first else "And"
                    lines.append(
                        f"  {prefix} {name} changes from {before} to {values[name].text}"
                    )
                    first = False
            else:
                lines.append("  Then no state variables change")
        prev_values = values

    _append_bdd_terminal_summary(lines, meta, states)
    return lines


def _trace_parts(
    data: dict[str, Any],
) -> tuple[dict[str, Any], list[str], list[str], list[Any]]:
    meta = data.get("#meta", {})
    params = list(data.get("params", []))
    vars_ = list(data.get("vars", []))
    states = data.get("states", [])
    if not isinstance(meta, dict):
        raise ValueError("ITF trace '#meta' must be an object")
    if not isinstance(states, list):
        raise ValueError("ITF trace 'states' must be an array")
    for raw_state in states:
        if not isinstance(raw_state, dict):
            raise ValueError("ITF state must be an object")
        state_meta = raw_state.get("#meta", {})
        if not isinstance(state_meta, dict):
            raise ValueError("ITF state '#meta' must be an object")
    return meta, params, vars_, states


def _action_desc(action_trace: list[str], step_idx: int) -> str:
    return action_trace[0] if action_trace else ("init" if step_idx == 0 else "step")


def _changed_fields(
    previous: dict[str, DisplayValue], current: dict[str, DisplayValue]
) -> list[str]:
    changed = []
    for name, display in current.items():
        old = previous.get(name)
        if old is None or display.value != old.value:
            changed.append(name)
    return changed


def _state_display_values(
    raw_state: dict[str, Any],
    *,
    params: list[str],
    vars_: list[str],
    style: TraceStyle,
) -> dict[str, DisplayValue]:
    names = [name for name in params + vars_ if name in raw_state]
    extras = sorted(
        name for name in raw_state if name != "#meta" and name not in set(names)
    )
    names.extend(extras)
    return {name: _decode_value(raw_state[name], style, depth=0) for name in names}


def _syntax_enabled(style: TraceStyle) -> bool:
    return style.color and style.syntax


def _render(doc: AbstractDoc) -> str:
    return render_doc(doc, 10_000)


def _styled_text(text: str, style: TraceStyle, *codes: str) -> AbstractDoc:
    doc: AbstractDoc = TextDoc(text)
    if not _syntax_enabled(style):
        return doc
    return StyledDoc(doc, codes)


def _constructor_doc(text: str, style: TraceStyle) -> AbstractDoc:
    return _styled_text(text, style, BOLD, MAGENTA)


def _variant_doc(text: str, style: TraceStyle) -> AbstractDoc:
    return _styled_text(text, style, BOLD, BLUE)


def _field_name_doc(text: str, style: TraceStyle) -> AbstractDoc:
    return _styled_text(text, style, CYAN)


def _operator_doc(text: str, style: TraceStyle) -> AbstractDoc:
    return _styled_text(text, style, DIM)


def _literal_doc(text: str, style: TraceStyle, *codes: str) -> AbstractDoc:
    return _styled_text(text, style, *codes)


def _delimiter_doc(text: str, style: TraceStyle, depth: int) -> AbstractDoc:
    if not _syntax_enabled(style):
        return TextDoc(text)
    color = _RAINBOW_DELIMITERS[depth % len(_RAINBOW_DELIMITERS)]
    return StyledDoc(TextDoc(text), (color,))


def _join_docs(docs: list[AbstractDoc], separator: AbstractDoc) -> AbstractDoc:
    result: AbstractDoc = TextDoc("")
    for idx, doc in enumerate(docs):
        if idx > 0:
            result += separator
        result += doc
    return result


def _plain_text(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _string_doc(raw: str, style: TraceStyle) -> AbstractDoc:
    return _literal_doc(repr(raw), style, GREEN)


def _delimited_text(
    open_text: str,
    item_docs: list[AbstractDoc],
    close_text: str,
    style: TraceStyle,
    depth: int,
) -> str:
    return _render(
        _delimiter_doc(open_text, style, depth)
        + _join_docs(item_docs, _operator_doc(", ", style))
        + _delimiter_doc(close_text, style, depth)
    )


def _tuple_text(items: list[DisplayValue], style: TraceStyle, depth: int) -> str:
    item_docs = [TextDoc(item.text) for item in items]
    if len(item_docs) == 1:
        return _render(
            _delimiter_doc("(", style, depth)
            + item_docs[0]
            + _operator_doc(",", style)
            + _delimiter_doc(")", style, depth)
        )
    return _delimited_text("(", item_docs, ")", style, depth)


def _decode_value(raw: Any, style: TraceStyle, depth: int) -> DisplayValue:
    if isinstance(raw, bool):
        return DisplayValue(raw, _render(_literal_doc(str(raw), style, BOLD, YELLOW)))
    if isinstance(raw, int):
        return DisplayValue(raw, _render(_literal_doc(str(raw), style, YELLOW)))
    if isinstance(raw, str):
        return DisplayValue(raw, _render(_string_doc(raw, style)))
    if isinstance(raw, list):
        items = [_decode_value(item, style, depth + 1) for item in raw]
        return DisplayValue(
            ("list", tuple(item.value for item in items)),
            _delimited_text(
                "[", [TextDoc(item.text) for item in items], "]", style, depth
            ),
        )
    if not isinstance(raw, dict):
        return DisplayValue(raw, str(raw))

    if "#bigint" in raw:
        value = int(raw["#bigint"])
        return DisplayValue(value, _render(_literal_doc(str(value), style, YELLOW)))
    if "#tup" in raw:
        items = [_decode_value(item, style, depth + 1) for item in raw["#tup"]]
        text = _tuple_text(items, style, depth)
        return DisplayValue(("tuple", tuple(item.value for item in items)), text)
    if "#set" in raw:
        items = sorted(
            (_decode_value(item, style, depth + 2) for item in raw["#set"]),
            key=lambda v: _plain_text(v.text),
        )
        text = _render(
            _constructor_doc("frozenset", style)
            + _delimiter_doc("(", style, depth)
            + TextDoc(
                _delimited_text(
                    "{", [TextDoc(item.text) for item in items], "}", style, depth + 1
                )
            )
            + _delimiter_doc(")", style, depth)
        )
        return DisplayValue(("set", tuple(item.value for item in items)), text)
    if "#map" in raw:
        pairs = [
            (_decode_value(k, style, depth + 1), _decode_value(v, style, depth + 1))
            for k, v in raw["#map"]
        ]
        pairs.sort(key=lambda pair: _plain_text(pair[0].text))
        text = _delimited_text(
            "{",
            [
                TextDoc(k.text) + _operator_doc(": ", style) + TextDoc(v.text)
                for k, v in pairs
            ],
            "}",
            style,
            depth,
        )
        map_value = ("map", tuple((k.value, v.value) for k, v in pairs))
        return DisplayValue(map_value, text)
    if "#unserializable" in raw:
        text = str(raw["#unserializable"])
        return DisplayValue(("unserializable", text), text)

    keys = set(raw)
    if keys == {"tag", "value"}:
        tag = str(raw["tag"])
        payload = raw["value"]
        if payload is None:
            return DisplayValue(
                ("variant", tag, None), _render(_variant_doc(tag, style))
            )
        payload_value = _decode_value(payload, style, depth + 1)
        return DisplayValue(
            ("variant", tag, payload_value.value),
            _render(
                _variant_doc(tag, style)
                + _delimiter_doc("(", style, depth)
                + TextDoc(payload_value.text)
                + _delimiter_doc(")", style, depth)
            ),
        )

    fields = [
        (name, _decode_value(raw[name], style, depth + 1)) for name in sorted(raw)
    ]
    text = _render(
        _constructor_doc("Record", style)
        + _delimiter_doc("(", style, depth)
        + _join_docs(
            [
                _field_name_doc(name, style)
                + _operator_doc("=", style)
                + TextDoc(value.text)
                for name, value in fields
            ],
            _operator_doc(", ", style),
        )
        + _delimiter_doc(")", style, depth)
    )
    return DisplayValue(
        ("record", tuple((name, value.value) for name, value in fields)),
        text,
    )


def _action_trace(meta: dict[str, Any]) -> list[str]:
    raw = meta.get("action_trace", [])
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw]


def _int_meta(meta: dict[str, Any], name: str, default: int) -> int:
    value = meta.get(name, default)
    return value if isinstance(value, int) else default


def _append_terminal_summary(
    lines: list[str],
    meta: dict[str, Any],
    states: list[Any],
    params: list[str],
    vars_: list[str],
    style: TraceStyle,
) -> None:
    if "violation_step" in meta:
        step = _int_meta(meta, "violation_step", len(states) - 1)
        name = meta.get("predicate_name") or meta.get("property")
        if name:
            lines.append(f'\nInvariant "{name}" violated in state {step}.')
        else:
            lines.append(f"\nInvariant violated in state {step}.")
        lines.append("  State at violation:")
        _append_state_values(lines, states, step, params, vars_, style)
    elif "example_step" in meta:
        step = _int_meta(meta, "example_step", len(states) - 1)
        name = meta.get("predicate_name") or meta.get("property")
        if name:
            lines.append(f'\nExample "{name}" found in state {step}.')
        else:
            lines.append(f"\nExample found in state {step}.")
        lines.append("  State at match:")
        _append_state_values(lines, states, step, params, vars_, style)


def _append_bdd_terminal_summary(
    lines: list[str],
    meta: dict[str, Any],
    states: list[Any],
) -> None:
    if "violation_step" in meta:
        step = _int_meta(meta, "violation_step", len(states) - 1)
        name = meta.get("predicate_name") or meta.get("property")
        if name:
            lines.append(f'  Then invariant "{name}" is violated in state {step}')
        else:
            lines.append(f"  Then invariant is violated in state {step}")
    elif "example_step" in meta:
        step = _int_meta(meta, "example_step", len(states) - 1)
        name = meta.get("predicate_name") or meta.get("property")
        if name:
            lines.append(f'  Then example "{name}" is found in state {step}')
        else:
            lines.append(f"  Then example is found in state {step}")
    else:
        lines.append(f"  Then the trace completes after {len(states)} states")


def _append_state_values(
    lines: list[str],
    states: list[Any],
    step: int,
    params: list[str],
    vars_: list[str],
    style: TraceStyle,
) -> None:
    if step < 0 or step >= len(states) or not isinstance(states[step], dict):
        return
    values = _state_display_values(
        states[step], params=params, vars_=vars_, style=style
    )
    for name, display in values.items():
        lines.append(f"    {name} = {display.text}")
