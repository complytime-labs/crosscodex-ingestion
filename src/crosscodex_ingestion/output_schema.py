"""
Structured output contract — the versioned JSON Schema and the rules it can't express.

The schema ships as package data (schemas/structured-output-v1.json, one file per major version)
and is compiled once at import. validate_output() is the last check before an output is stored:
it applies the schema, then the rules JSON Schema has no keyword for (unique element_id, unique
non-null source id, parent_id naming an earlier element, element_count matching the elements).

Violation messages name where the output is wrong but never echo its values: element content
is tenant data, and these messages reach the client, the logs and the audit stream. The only
values quoted are element ids and property names, and only when they have the form the service
itself generates.
"""

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

# The version this service emits. The schema file lists every released 1.x it validates.
SCHEMA_VERSION = "1.3"

_SCHEMA: dict[str, Any] = json.loads(
    files("crosscodex_ingestion").joinpath("schemas/structured-output-v1.json").read_text(encoding="utf-8")
)
Draft202012Validator.check_schema(_SCHEMA)
if SCHEMA_VERSION not in _SCHEMA["properties"]["schema_version"]["enum"]:
    raise RuntimeError(f"schemas/structured-output-v1.json does not list schema_version {SCHEMA_VERSION}")
_VALIDATOR = Draft202012Validator(_SCHEMA)

# Same rule as the schema's elementId pattern; fullmatch, because Python's '$' accepts a trailing newline.
_ELEMENT_ID = re.compile(r"e-[0-9]{3,30}")
_PROPERTY_NAME = re.compile(r"[a-z_]{1,64}")


@dataclass(frozen=True)
class Violation:
    """One way an output breaks the contract.

    element_index and element_id identify the offending element, when the violation is inside one.
    element_id is set only when it has the generated form.
    """

    location: str
    reason: str
    element_index: int | None = None
    element_id: str | None = None

    def __str__(self) -> str:
        return f"{self.location}: {self.reason}"


class OutputSchemaError(Exception):
    """Raised when a structured output does not conform to the schema; violations are in document order."""

    def __init__(self, violations: list[Violation]) -> None:
        self.violations = violations
        more = f" (and {len(violations) - 1} more)" if len(violations) > 1 else ""
        super().__init__(f"Structured output does not conform to schema {SCHEMA_VERSION}: {violations[0]}{more}")


def _element_location(index: int, element_id: str | None) -> str:
    return f"elements[{index}]" + (f" (element_id {element_id!r})" if element_id is not None else "")


def _unexpected_properties(error: ValidationError) -> str:
    """Name the unexpected properties that look like field names; count the rest without quoting them."""
    allowed = error.schema.get("properties", {}) if isinstance(error.schema, dict) else {}
    extras = [key for key in error.instance if key not in allowed]
    names = sorted(key for key in extras if isinstance(key, str) and _PROPERTY_NAME.fullmatch(key))
    unnamed = len(extras) - len(names)
    parts = [f"unexpected property {name!r}" for name in names]
    if unnamed:
        parts.append(f"{unnamed} unexpected propert{'y' if unnamed == 1 else 'ies'}")
    return "; ".join(parts)


def _schema_violation(output: dict[str, Any], error: ValidationError) -> Violation:
    """Describe *error* by its path and the schema rule it broke, without quoting the instance."""
    path = list(error.absolute_path)
    element_index: int | None = None
    element_id: str | None = None
    location = ""
    for depth, part in enumerate(path):
        if depth == 1 and path[0] == "elements" and isinstance(part, int):
            element_index = part
            element = output["elements"][part]
            candidate = element.get("element_id") if isinstance(element, dict) else None
            if isinstance(candidate, str) and _ELEMENT_ID.fullmatch(candidate):
                element_id = candidate
            location = _element_location(element_index, element_id)
        elif isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}" if location else str(part)

    if error.validator == "required":
        # Names a property from the schema, never a value.
        reason = error.message
    elif error.validator == "additionalProperties":
        reason = _unexpected_properties(error)
    else:
        reason = f"fails {error.validator!r} {json.dumps(error.validator_value)}"
        description = error.schema.get("description") if isinstance(error.schema, dict) else None
        if description:
            reason += f" ({description})"
    return Violation(location or "output", reason, element_index, element_id)


def _reference_violations(output: dict[str, Any]) -> list[Violation]:
    """Check element_id and source id uniqueness, that each parent_id names an earlier element, and element_count.

    Runs only on schema-valid output, so every field is present, typed and every element_id has the
    generated form (safe to quote). Source ids are tenant data and are never quoted.
    """
    elements = output["elements"]
    violations = []
    seen: set[str] = set()
    source_ids: dict[str, int] = {}
    for index, element in enumerate(elements):
        source_id = element.get("id")
        if source_id is not None:
            if source_id in source_ids:
                violations.append(
                    Violation(
                        f"{_element_location(index, element['element_id'])}.id",
                        f"duplicates the id of elements[{source_ids[source_id]}]",
                        index,
                        element["element_id"],
                    )
                )
            else:
                source_ids[source_id] = index
        element_id = element["element_id"]
        parent_id = element["parent_id"]
        location = _element_location(index, element_id)
        if element_id in seen:
            violations.append(
                Violation(f"{location}.element_id", "duplicates an earlier element_id", index, element_id)
            )
        if parent_id is not None and parent_id not in seen:
            violations.append(
                Violation(
                    f"{location}.parent_id",
                    f"{parent_id!r} is not the element_id of an earlier element",
                    index,
                    element_id,
                )
            )
        seen.add(element_id)
    if output["metadata"]["element_count"] != len(elements):
        violations.append(
            Violation(
                "metadata.element_count",
                f"is {output['metadata']['element_count']}, but there are {len(elements)} elements",
            )
        )
    return violations


def validate_output(output: dict[str, Any]) -> None:
    """Raise OutputSchemaError unless *output* conforms to the schema and its cross-element rules."""
    errors = sorted(_VALIDATOR.iter_errors(output), key=lambda e: list(e.absolute_path))
    violations = [_schema_violation(output, e) for e in errors]
    if not violations:
        violations = _reference_violations(output)
    if violations:
        raise OutputSchemaError(violations)
