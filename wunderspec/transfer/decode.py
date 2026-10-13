"""Validate and reconstruct declarative AST documents without loading user modules."""

from wunderspec.transfer._construct import Decoder as Decoder
from wunderspec.transfer._validate import decode_document
from wunderspec.transfer.gather import GatheredSpec

__all__ = ["Decoder", "import_spec"]


def import_spec(document: dict) -> GatheredSpec:
    """Validate and reconstruct ASTs using only documented constructors."""
    return decode_document(document)
