"""Edition feature gates for the Wunderspec open-core distribution."""

from __future__ import annotations

EDITION = "open-core"

PREMIUM_FEATURES = {
    "fuzz": "Wunderspec fuzzing is available in Wunderspec Premium.",
    "lean": "Lean translation is available in Wunderspec Premium.",
    "rust": "Rust translation is available in Wunderspec Premium.",
}

DISABLED_FEATURES: frozenset[str] = frozenset({"fuzz"})


class PreviewFeatureUnavailable(ImportError):
    """Raised when open-core mock modules are imported directly."""


def feature_message(name: str) -> str:
    if name in PREMIUM_FEATURES:
        return PREMIUM_FEATURES[name]
    return f"Wunderspec feature '{name}' is not available in this distribution."


def is_feature_enabled(name: str) -> bool:
    return name not in DISABLED_FEATURES


def require_feature(name: str) -> None:
    if not is_feature_enabled(name):
        raise PreviewFeatureUnavailable(feature_message(name))
