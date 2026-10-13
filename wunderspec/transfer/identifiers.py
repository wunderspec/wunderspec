"""Identifiers in transfer documents (contracts/transfer-format.md "Identifiers"; FR-006).

ASCII only: rules out Unicode confusables and bidirectional controls in generated Rust code, and
anything that could break out of an identifier position (quotes, punctuation, whitespace).
"""

import re

IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_]{0,127}$"
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")


def is_identifier(value: object) -> bool:
    return isinstance(value, str) and IDENTIFIER.fullmatch(value) is not None
