"""Small Wadler-Lindig rendering helpers shared by code generators."""

from __future__ import annotations

from dataclasses import dataclass

from wadler_lindig import AbstractDoc, BreakDoc, ConcatDoc, GroupDoc, NestDoc, TextDoc


class HardLine(AbstractDoc):
    """Always inserts a newline + indent, regardless of horizontal mode."""

    pass


@dataclass(frozen=True)
class AlignDoc(AbstractDoc):
    """Set the indent to the current output column for all hard lines."""

    child: AbstractDoc


@dataclass(frozen=True)
class StyledDoc(AbstractDoc):
    """Render *child* wrapped in ANSI codes without affecting visible width."""

    child: AbstractDoc
    codes: tuple[str, ...]


@dataclass(frozen=True)
class _StyleEnd:
    pass


def fits(doc: AbstractDoc, width: int) -> bool:
    """Check if *doc* fits horizontally in *width* columns."""
    todo: list[AbstractDoc] = [doc]
    while todo and width >= 0:
        match todo.pop():
            case TextDoc(text):
                width -= len(text)
            case BreakDoc(text):
                width -= len(text)
            case ConcatDoc(children):
                todo.extend(reversed(children))
            case NestDoc(child, _):
                todo.append(child)
            case GroupDoc(child):
                todo.append(child)
            case AlignDoc(child):
                todo.append(child)
            case StyledDoc(child, _):
                todo.append(child)
            case HardLine():
                return False
    return width >= 0


def render_doc(doc: AbstractDoc, width: int) -> str:
    """Render a Doc tree to a string."""
    outs: list[str] = []
    width_so_far = 0
    vertical = True
    indent = 0
    todo: list[bool | int | AbstractDoc | _StyleEnd] = [doc]

    while todo:
        item = todo.pop()
        match item:
            case _StyleEnd():
                outs.append("\033[0m")
            case bool(vertical2):
                vertical = vertical2
            case int(indent2):
                indent = indent2
            case TextDoc(text):
                outs.append(text)
                width_so_far += len(text)
            case HardLine():
                outs.append("\n" + " " * indent)
                width_so_far = indent
            case BreakDoc(text):
                if vertical:
                    outs.append("\n" + " " * indent)
                    width_so_far = indent
                else:
                    outs.append(text)
                    width_so_far += len(text)
            case ConcatDoc(children):
                todo.extend(reversed(children))
            case NestDoc(child, extra_indent):
                todo.append(indent)
                todo.append(child)
                indent += extra_indent
            case AlignDoc(child):
                todo.append(indent)
                todo.append(child)
                indent = width_so_far
            case StyledDoc(child, codes):
                outs.append("".join(codes))
                todo.append(_StyleEnd())
                todo.append(child)
            case GroupDoc(child):
                if vertical and fits(child, width - width_so_far):
                    todo.append(True)
                    todo.append(child)
                    vertical = False
                else:
                    todo.append(child)

    return "".join(outs)


def with_text_indent(doc: AbstractDoc, text_indent: int) -> AbstractDoc:
    """Return *doc* with block continuation indentation set to *text_indent*."""
    match doc:
        case ConcatDoc(children):
            return ConcatDoc(
                *(with_text_indent(child, text_indent) for child in children)
            )
        case NestDoc(child, extra_indent):
            next_indent = text_indent if extra_indent == 4 else extra_indent
            return NestDoc(with_text_indent(child, text_indent), next_indent)
        case GroupDoc(child):
            return GroupDoc(with_text_indent(child, text_indent))
        case AlignDoc(child):
            return AlignDoc(with_text_indent(child, text_indent))
        case StyledDoc(child, codes):
            return StyledDoc(with_text_indent(child, text_indent), codes)
        case _:
            return doc
