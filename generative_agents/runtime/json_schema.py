"""Small dependency-free JSON Schema validator for workflow LLM outputs.

It intentionally supports the subset emitted by the Prompt workbench.  The
validation runs inside the model retry loop, so a malformed structured result
is retried instead of leaking into Agent code.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "object": isinstance(value, Mapping),
        "array": isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
    }.get(expected, True)


def validate_json_schema(value: Any, schema: Mapping[str, Any], path: str = "$") -> None:
    """Raise ``ValueError`` when ``value`` violates the supported schema subset."""

    if not isinstance(schema, Mapping):
        raise ValueError(f"{path}: schema must be an object")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path}: value is not one of the allowed enum values")

    expected = schema.get("type")
    expected_types = expected if isinstance(expected, list) else [expected] if expected else []
    if expected_types and not any(_matches_type(value, item) for item in expected_types):
        raise ValueError(f"{path}: expected {' or '.join(expected_types)}")

    if isinstance(value, Mapping):
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"{path}: missing required fields {', '.join(missing)}")
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for key, child_schema in properties.items():
                if key in value:
                    validate_json_schema(value[key], child_schema, f"{path}.{key}")
            if schema.get("additionalProperties") is False:
                extras = set(value) - set(properties)
                if extras:
                    raise ValueError(f"{path}: unknown fields {', '.join(sorted(extras))}")
            elif isinstance(schema.get("additionalProperties"), Mapping):
                child_schema = schema["additionalProperties"]
                for key in set(value) - set(properties):
                    validate_json_schema(value[key], child_schema, f"{path}.{key}")

    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"{path}: requires at least {schema['minItems']} items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{path}: allows at most {schema['maxItems']} items")
        prefix_items = schema.get("prefixItems")
        if isinstance(prefix_items, list):
            for index, child_schema in enumerate(prefix_items[: len(value)]):
                validate_json_schema(value[index], child_schema, f"{path}[{index}]")
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                validate_json_schema(item, items, f"{path}[{index}]")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{path}: must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{path}: must be at most {schema['maximum']}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"{path}: is shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"{path}: is longer than {schema['maxLength']} characters")
