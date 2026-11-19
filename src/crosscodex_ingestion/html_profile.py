"""HTML profiles: reviewed YAML files telling the HTML backend how one publisher marks up structure.

This module only loads and validates profiles. Selectors and regexes come from packaged or operator-supplied
files, never from requests.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

import soupsieve
import yaml
from jsonschema import Draft202012Validator

from crosscodex_ingestion.mapping import _StrictLoader

_SCHEMA: dict[str, Any] = json.loads(
    files("crosscodex_ingestion").joinpath("schemas/html-profile-v1.schema.json").read_text(encoding="utf-8")
)
Draft202012Validator.check_schema(_SCHEMA)
_VALIDATOR = Draft202012Validator(_SCHEMA)

_SELECTOR_KEYS = (
    ("detect",),
    ("title", "container"),
    ("title", "lines"),
    ("divisions", "title"),
    ("divisions", "exclude"),
    ("divisions", "subtitle"),
    ("numbered_tables", "exclude"),
)


class HtmlProfileError(Exception):
    """A profile could not be loaded; the message names the file."""


@dataclass(frozen=True)
class HtmlProfile:
    """A validated profile. *document* is the parsed YAML; *sha256* is the hash of the file's bytes."""

    id: str
    sha256: str
    document: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {"id": self.id, "sha256": self.sha256, "document": self.document}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "HtmlProfile":
        """Rebuild a profile the parent process already validated; nothing is re-checked."""
        return cls(id=data["id"], sha256=data["sha256"], document=data["document"])


def _check_selectors(document: dict[str, Any], source: Traversable | Path) -> None:
    """Compile every selector and the label regex so a bad profile fails at startup."""
    selectors: list[tuple[str, str]] = []
    for path in _SELECTOR_KEYS:
        value: Any = document
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None:
            selectors.append((".".join(path), value))
    for section in ("unwrap", "remove"):
        selectors.extend((f"{section}[{i}]", value) for i, value in enumerate(document.get(section, [])))
    for key, selector in selectors:
        try:
            soupsieve.compile(selector)
        except soupsieve.SelectorSyntaxError as exc:
            raise HtmlProfileError(f"HTML profile {source}: {key} is not a valid CSS selector: {exc}") from exc
    label = document.get("numbered_tables", {}).get("label")
    if label is not None:
        try:
            re.compile(label)
        except re.error as exc:
            raise HtmlProfileError(f"HTML profile {source}: numbered_tables.label is not a valid regex: {exc}") from exc


def _load(source: Traversable | Path) -> HtmlProfile:
    """Read, parse and validate one profile file."""
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise HtmlProfileError(f"HTML profile {source}: cannot read the file: {exc}") from exc
    try:
        document = yaml.load(raw, Loader=_StrictLoader)  # noqa: S506 (a SafeLoader subclass)
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark or exc.context_mark
        where = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise HtmlProfileError(f"HTML profile {source}: invalid YAML{where}: {exc.problem or exc.context}") from exc
    except yaml.YAMLError as exc:
        raise HtmlProfileError(f"HTML profile {source}: invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise HtmlProfileError(
            f"HTML profile {source}: the file must be a YAML mapping (key: value pairs) at the top level."
        )
    errors = sorted(_VALIDATOR.iter_errors(document), key=lambda e: list(e.absolute_path))
    if errors:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e.absolute_path) or '(top level)'}: {e.message}" for e in errors[:10]
        )
        raise HtmlProfileError(f"HTML profile {source} does not match the HTML profile schema: {problems}")
    if source.name != f"{document['id']}.yaml":
        raise HtmlProfileError(
            f"HTML profile {source}: a profile with id {document['id']!r} must be named {document['id']}.yaml."
        )
    _check_selectors(document, source)
    return HtmlProfile(id=document["id"], sha256=hashlib.sha256(raw).hexdigest(), document=document)


class HtmlProfileRegistry:
    """The HTML profiles a server accepts."""

    def __init__(self, profiles: dict[str, HtmlProfile]) -> None:
        self.profiles: tuple[HtmlProfile, ...] = tuple(profiles[key] for key in sorted(profiles))

    def ids(self) -> list[str]:
        return [profile.id for profile in self.profiles]


def load_html_profiles(html_profiles_path: str) -> HtmlProfileRegistry:
    """Load the packaged profiles plus every *.yaml in *html_profiles_path* (if set); fail closed on any problem."""
    sources: list[Traversable | Path] = [
        entry
        for entry in files("crosscodex_ingestion").joinpath("html_profiles").iterdir()
        if entry.name.endswith(".yaml")
    ]
    if html_profiles_path:
        directory = Path(html_profiles_path)
        if not directory.is_dir():
            raise HtmlProfileError(f"HTML_PROFILES_PATH {html_profiles_path!r} is not a directory.")
        sources.extend(directory.glob("*.yaml"))

    profiles: dict[str, HtmlProfile] = {}
    origins: dict[str, str] = {}
    for source in sorted(sources, key=str):
        profile = _load(source)
        if profile.id in profiles:
            raise HtmlProfileError(
                f"HTML profile id {profile.id!r} is registered twice: {origins[profile.id]} and {source}."
            )
        profiles[profile.id] = profile
        origins[profile.id] = str(source)
    return HtmlProfileRegistry(profiles)
