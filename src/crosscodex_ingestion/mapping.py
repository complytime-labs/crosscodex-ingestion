"""
Structured-source mappings — loading, validation and the registry.

A mapping is a reviewed YAML file that tells the structured converter (structured.py) where the
nodes of one publisher release are and which fields carry an element's ID, title, text and
properties. Mappings ship as package data (mappings/*.yaml) and, optionally, in an operator
directory (MAPPINGS_PATH). All of them are loaded and validated against
schemas/mapping-v1.schema.json at server startup; one invalid file stops the server, so a request
can only ever name a mapping that was valid when the server started.

A request names a mapping by ID; there are no inline mappings and no mapping URLs.
"""

import hashlib
import json
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

MAPPING_VERSION = 1

_SCHEMA: dict[str, Any] = json.loads(
    files("crosscodex_ingestion").joinpath("schemas/mapping-v1.schema.json").read_text(encoding="utf-8")
)
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA)


class MappingError(Exception):
    """A mapping file is unreadable, malformed or invalid. Raised at startup; the server refuses to start."""


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that rejects duplicate keys, which PyYAML otherwise resolves silently to the last one."""


def _construct_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    seen: set[Any] = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
        try:
            duplicate = key in seen
        except TypeError:
            continue  # unhashable key: construct_mapping below reports it
        if duplicate:
            raise yaml.constructor.ConstructorError(None, None, f"duplicate key {key!r}", key_node.start_mark)
        seen.add(key)
    mapping: dict[Any, Any] = loader.construct_mapping(node, deep)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


@dataclass(frozen=True)
class Mapping:
    """A validated mapping. *document* is the parsed YAML; *sha256* is the hash of the file's bytes."""

    id: str
    title: str
    format: str
    mapping_version: int
    sha256: str
    document: dict[str, Any]

    @classmethod
    def from_document(cls, document: Any, sha256: str, origin: str) -> "Mapping":
        """Validate *document* against the mapping schema; *origin* names it in errors (a path, or "job")."""
        errors = sorted(_VALIDATOR.iter_errors(document), key=lambda e: list(e.absolute_path))
        if errors:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e.absolute_path) or '(top level)'}: {e.message}" for e in errors[:10]
            )
            raise MappingError(f"Mapping {origin} does not match the mapping schema: {problems}")
        return cls(
            id=document["id"],
            title=document["title"],
            format=document["source"]["format"],
            mapping_version=document["mapping_version"],
            sha256=sha256,
            document=document,
        )


def _load(source: Traversable | Path) -> Mapping:
    """Read, parse and validate one mapping file."""
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise MappingError(f"Mapping {source}: cannot read the file: {exc}") from exc
    try:
        document = yaml.load(raw, Loader=_StrictLoader)  # noqa: S506 (a SafeLoader subclass)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise MappingError(f"Mapping {source}: invalid YAML{where}: {exc.problem or exc.context}") from exc
    except yaml.YAMLError as exc:
        raise MappingError(f"Mapping {source}: invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise MappingError(f"Mapping {source}: the file must be a YAML mapping (key: value pairs) at the top level.")
    mapping = Mapping.from_document(document, hashlib.sha256(raw).hexdigest(), str(source))
    if source.name != f"{mapping.id}.yaml":
        raise MappingError(f"Mapping {source}: a mapping with id {mapping.id!r} must be named {mapping.id}.yaml.")
    return mapping


def load_mapping_file(path: Path) -> Mapping:
    """Load and validate the mapping file at *path*; raise MappingError naming the file on any problem."""
    return _load(path)


class MappingRegistry:
    """The mappings a server accepts, by ID."""

    def __init__(self, mappings: dict[str, Mapping]) -> None:
        self._mappings = dict(mappings)

    def get(self, mapping_id: str) -> Mapping | None:
        return self._mappings.get(mapping_id)

    def ids(self, fmt: str | None = None) -> list[str]:
        """The registered IDs, sorted; only those of mappings for *fmt* when it is given."""
        return sorted(i for i, m in self._mappings.items() if fmt is None or m.format == fmt)


def load_registry(mappings_path: str) -> MappingRegistry:
    """Load the packaged mappings plus every *.yaml in *mappings_path* (if set); fail closed on any problem."""
    sources: list[Traversable | Path] = [
        entry for entry in files("crosscodex_ingestion").joinpath("mappings").iterdir() if entry.name.endswith(".yaml")
    ]
    if mappings_path:
        directory = Path(mappings_path)
        if not directory.is_dir():
            raise MappingError(f"MAPPINGS_PATH {mappings_path!r} is not a directory.")
        sources.extend(directory.glob("*.yaml"))

    mappings: dict[str, Mapping] = {}
    origins: dict[str, str] = {}
    for source in sorted(sources, key=str):
        mapping = _load(source)
        if mapping.id in mappings:
            raise MappingError(f"Mapping id {mapping.id!r} is registered twice: {origins[mapping.id]} and {source}.")
        mappings[mapping.id] = mapping
        origins[mapping.id] = str(source)
    return MappingRegistry(mappings)
