"""Natural-language movement activity, separate from kernel-owned displacement."""

from collections.abc import Mapping


def movement_activity_from_predicate(predicate) -> dict | None:
    """Validate the public MOVE predicate without classifying activity types."""
    if predicate is None:
        return None
    if not isinstance(predicate, str):
        raise ValueError("MOVE predicate must be a natural-language activity string")
    text = predicate.strip()
    if not text:
        return None
    if len(text) > 240 or any(ord(character) < 32 for character in text):
        raise ValueError("MOVE predicate must be a single activity phrase of at most 240 characters")
    return {"text": text, "source": "actor_declared"}


def normalize_movement_activity(value) -> dict | None:
    """Check serialized activity metadata; never infer it from audit text."""
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"text", "source"}:
        raise ValueError("movement_activity requires only text and source")
    normalized = movement_activity_from_predicate(value["text"])
    if normalized is None or dict(value) != normalized:
        raise ValueError("movement_activity must contain normalized text and actor_declared source")
    return normalized
