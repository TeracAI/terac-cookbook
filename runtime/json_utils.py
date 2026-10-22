"""Strict JSON decoding helpers shared by persisted runtime state."""


def unique_json_object(pairs):
    """Build a JSON object and reject duplicate property names."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result
