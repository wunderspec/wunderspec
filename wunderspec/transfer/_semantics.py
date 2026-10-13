"""Names, references, and the selected definitions (research R4; contracts/transfer-format.md,
"Semantic rules" 1, 3, and 5; FR-010). AST context checks are in ``_checks``."""

from collections.abc import Mapping
from typing import Any

from wunderspec.transfer._parse import json_pointer
from wunderspec.transfer.errors import TransferError as TransferInvalid

_SELECTION_KINDS = {
    "init": ("init",),
    "step": ("action",),
    "property": ("invariant", "example"),
}


def check_names_and_references(spec: dict[str, Any]) -> list[TransferInvalid]:
    problems: list[TransferInvalid] = []
    for field in ("defs", "extractedActions", "instances"):
        seen: set[str] = set()
        for i, item in enumerate(spec[field]):
            if item["name"] in seen:
                problems.append(
                    TransferInvalid("duplicate_name", f"/spec/{field}/{i}/name")
                )
            seen.add(item["name"])
            if field != "instances":
                continue
            parameter_names: set[str] = set()
            for j, (name, _) in enumerate(item["params"]):
                if name in parameter_names:
                    problems.append(
                        TransferInvalid(
                            "duplicate_name", f"/spec/instances/{i}/params/{j}/0"
                        )
                    )
                parameter_names.add(name)
    # ``pureExprs`` names non-inlined helpers called through ``exprCall``; they are not
    # necessarily definitions (readers_writers' ``list_to_set``), so only their form is checked.
    parameters = sorted(name for name, _ in spec["params"])
    for i, instance in enumerate(spec["instances"]):
        if sorted(name for name, _ in instance["params"]) != parameters:
            problems.append(
                TransferInvalid("unknown_reference", f"/spec/instances/{i}/params")
            )
    return problems


def check_selection(
    spec: dict[str, Any], options: Mapping[str, str]
) -> list[TransferInvalid]:
    """The definitions a job selects exist and have the right kinds (rule 5)."""
    problems: list[TransferInvalid] = []
    by_name = {d["name"]: (i, d["kind"]) for i, d in enumerate(spec["defs"])}
    for option, kinds in _SELECTION_KINDS.items():
        name = options.get(option)
        if name is None:
            continue
        if name not in by_name:
            problems.append(TransferInvalid("missing_definition", "/spec/defs"))
            continue
        index, kind = by_name[name]
        if kind not in kinds:
            problems.append(
                TransferInvalid(
                    "wrong_definition_kind",
                    json_pointer(("spec", "defs", index, "kind")),
                )
            )
    return problems
