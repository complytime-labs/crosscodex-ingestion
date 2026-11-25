"""Tests for structured.py — reading JSON, CSV and XLSX sources through a mapping into elements."""

import copy
import datetime as dt
import io
import json
import re
import struct
import time
import warnings
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from openpyxl.chart import BarChart

from crosscodex_ingestion import structured
from crosscodex_ingestion.config import IngestionSettings
from crosscodex_ingestion.mapping import Mapping, load_registry
from crosscodex_ingestion.output_schema import SCHEMA_VERSION, validate_output
from crosscodex_ingestion.structured import (
    INPUT_ERROR_EXIT_CODE,
    StructuredLimitError,
    StructuredLimits,
    StructuredSourceError,
    convert_source,
    run_job,
)
from crosscodex_ingestion.zipdir import ZipDirectory

FIXTURES = Path(__file__).parent.parent / "fixtures" / "structured"
REGISTRY = load_registry("")
ASVS = REGISTRY.get("owasp-asvs-5.0.0-json")
ASVS_CSV = REGISTRY.get("owasp-asvs-5.0.0-csv")
ATTACK = REGISTRY.get("mitre-attack-enterprise-17.1-techniques-xlsx")
NIST = REGISTRY.get("nist-sp800-53r5-catalog-xlsx")
NIST_CSF = REGISTRY.get("nist-csf-2.0-core-xlsx")
LIMITS = StructuredLimits.from_settings(IngestionSettings())


def _with_document(mapping: Mapping, **overrides: Any) -> Mapping:
    """*mapping* with top-level keys replaced (a None value removes the key)."""
    document = {**copy.deepcopy(mapping.document), **overrides}
    document = {key: value for key, value in document.items() if value is not None}
    return Mapping.from_document(document, "e" * 64, "test")


# The ASVS mapping without group_levels: tree and error tests use shallow trees where no node is a group.
ASVS_FLAT = _with_document(ASVS, group_levels=None)

GOLDEN = [
    ("owasp-asvs-5.0.0", "input.json", ASVS),
    ("owasp-asvs-5.0.0-csv", "input.csv", ASVS_CSV),
    ("mitre-attack-enterprise-17.1", "input.xlsx", ATTACK),
    ("nist-sp800-53r5", "input.xlsx", NIST),
    ("nist-csf-2.0", "input.xlsx", NIST_CSF),
]

CSV_MAPPING = Mapping.from_document(
    {
        "mapping_version": 1,
        "id": "example-1.0-csv",
        "title": "Example CSV",
        "source": {"format": "csv", "release": "https://example.org/example.csv", "delimiter": ";"},
        "rows": {"id": "ID", "title": "Name", "text": "Text", "parent": {"column": "Parent"}},
    },
    "c" * 64,
    "test",
)

LEVELS_MAPPING = Mapping.from_document(
    {
        "mapping_version": 1,
        "id": "levels-1.0-csv",
        "title": "Levels",
        "source": {"format": "csv", "release": "https://example.org/levels.csv"},
        "rows": {
            "levels": [{"id": "ch_id", "title": "ch_name"}, {"id": "sec_id", "title": "sec_name"}],
            "id": "req_id",
            "text": "req_text",
            "properties": {"level": "L"},
        },
    },
    "d" * 64,
    "test",
)
CELLS_MAPPING = Mapping.from_document(
    {
        "mapping_version": 1,
        "id": "cells-1.0-csv",
        "title": "Cells",
        "source": {"format": "csv", "release": "https://example.org/cells.csv"},
        "rows": {
            "level_cells": {
                "columns": ["Function", "Category", "Sub"],
                "separator": ":",
                "id_brackets": {"open": "(", "close": ")"},
            },
            "properties": {"examples": "Examples"},
        },
    },
    "f" * 64,
    "test",
)
CELLS_HEADER = "Function,Category,Sub,Examples\n"
LEVELS_HEADER = "ch_id,ch_name,sec_id,sec_name,req_id,req_text,L\n"


def _limits(**overrides: int) -> StructuredLimits:
    return StructuredLimits(**{**LIMITS.__dict__, **overrides})


def _json(document: Any) -> bytes:
    return json.dumps(document).encode()


def _asvs(*chapters: dict) -> bytes:
    return _json({"Name": "ASVS", "Requirements": list(chapters)})


def _requirement(shortcode: str, description: str = "Verify something.", **extra: Any) -> dict:
    return {"Shortcode": shortcode, "Ordinal": 1, "Description": description, "L": "1", **extra}


def _xlsx(rows: list[list[Any]], sheet: str = "techniques") -> bytes:
    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = sheet
    for row in rows:
        worksheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


ATTACK_HEADER = ["ID", "name", "description", "url", "version", "tactics", "platforms", "sub-technique of"]


def _technique(technique_id: str, parent: str | None = None, name: str = "Name") -> list[Any]:
    return [technique_id, name, "Adversaries may.", "https://attack.mitre.org", "1.0", "Execution", "Linux", parent]


def _source_error(data: bytes, mapping: Mapping, limits: StructuredLimits = LIMITS) -> StructuredSourceError:
    with pytest.raises(StructuredSourceError) as exc_info:
        convert_source(data, mapping, limits)
    return exc_info.value


def _problems(data: bytes, mapping: Mapping, limits: StructuredLimits = LIMITS) -> list[str]:
    error = _source_error(data, mapping, limits)
    return [error.details[f"problem.{n}"] for n in range(1, min(error.count, 20) + 1)]


def _by_id(output: dict) -> dict[str, dict]:
    return {e["id"]: e for e in output["elements"] if e.get("id") is not None}


class TestGoldenOutputs:
    """The publishers' real releases (trimmed), read through the registered mappings."""

    @pytest.mark.parametrize(("directory", "name", "mapping"), GOLDEN)
    def test_output_matches_the_golden_file(self, directory, name, mapping):
        data = (FIXTURES / directory / name).read_bytes()

        output = convert_source(data, mapping, LIMITS)

        expected = json.loads((FIXTURES / directory / "expected.json").read_text(encoding="utf-8"))
        assert output == expected

    @pytest.mark.parametrize(("directory", "name", "mapping"), GOLDEN)
    def test_role_title_and_text_are_on_exactly_the_elements_with_an_id(self, directory, name, mapping):
        output = convert_source((FIXTURES / directory / name).read_bytes(), mapping, LIMITS)

        for element in output["elements"]:
            present = {"role", "title", "text"} & element.keys()
            expected = {"role", "title", "text"} if isinstance(element.get("id"), str) else set()
            assert present == expected, element["element_id"]

    @pytest.mark.parametrize(("directory", "name", "mapping"), GOLDEN)
    def test_output_conforms_to_the_schema(self, directory, name, mapping):
        output = convert_source((FIXTURES / directory / name).read_bytes(), mapping, LIMITS)

        validate_output(
            {"schema_version": SCHEMA_VERSION, **output, "metadata": {**output["metadata"], "processing_time_ms": 1}}
        )


class TestAsvs:
    @pytest.fixture(scope="class")
    @classmethod
    def output(cls) -> dict:
        return convert_source((FIXTURES / "owasp-asvs-5.0.0" / "input.json").read_bytes(), ASVS, LIMITS)

    def test_chapter_v6_keeps_its_8_sections_and_47_requirements(self, output):
        elements = _by_id(output)
        sections = [e for e in output["elements"] if e["parent_id"] == elements["V6"]["element_id"]]
        requirements = [i for i in elements if i.startswith("V6.") and i.count(".") == 2]

        assert [s["id"] for s in sections] == [f"V6.{n}" for n in range(1, 9)]
        assert len(requirements) == 47

    def test_hierarchy_becomes_heading_levels(self, output):
        elements = _by_id(output)

        assert (elements["V6"]["type"], elements["V6"]["level"], elements["V6"]["content"]) == (
            "heading",
            1,
            "Authentication",
        )
        assert (elements["V6.2"]["type"], elements["V6.2"]["level"]) == ("heading", 2)
        assert elements["V6.2.5"]["parent_id"] == elements["V6.2"]["element_id"]

    def test_requirement_keeps_its_whole_text(self, output):
        requirement = _by_id(output)["V6.2.5"]

        assert requirement["type"] == "paragraph"
        assert requirement["content"].endswith("numbers, or special characters.")
        assert (requirement["title"], requirement["role"]) == (None, "control")

    def test_properties_come_from_the_mapping(self, output):
        elements = _by_id(output)

        assert elements["V6.2.5"]["properties"] == {"level": "1"}
        assert elements["V6"]["properties"] == {"short_name": "Authentication"}
        assert elements["V6.2"]["properties"] == {}

    def test_metadata_names_the_mapping_and_the_unmapped_fields(self, output):
        source = output["metadata"]["source"]

        assert source["mapping"] == {"id": ASVS.id, "sha256": ASVS.sha256, "mapping_version": 1}
        assert source["unmapped_fields"] == ["Ordinal"]
        assert source["node_count"] == 2 + 1 + 8 + 47 + len(
            json.loads((FIXTURES / "owasp-asvs-5.0.0" / "input.json").read_text())["Requirements"][0]["Items"][0][
                "Items"
            ]
        )
        assert output["metadata"]["title"] == ASVS.title
        assert output["metadata"]["detected_format"] == "json"

    def test_every_parent_precedes_its_children(self, output):
        seen = set()
        for element in output["elements"]:
            assert element["parent_id"] is None or element["parent_id"] in seen
            seen.add(element["element_id"])


class TestTreeSemantics:
    def test_node_with_children_and_text_gets_a_text_paragraph_without_id(self):
        chapter = {"Shortcode": "V1", "Name": "Chapter", "Description": "Intro.", "Items": [_requirement("V1.1")]}

        elements = convert_source(_asvs(chapter), ASVS_FLAT, LIMITS)["elements"]

        assert [(e["type"], e["id"], e["parent_id"]) for e in elements] == [
            ("heading", "V1", None),
            ("paragraph", None, "e-001"),
            ("paragraph", "V1.1", "e-001"),
        ]
        assert elements[1]["content"] == "Intro."
        assert "properties" not in elements[1]

    def test_leaf_without_text_uses_its_title_as_content(self):
        elements = convert_source(_asvs({"Shortcode": "V1", "Name": "Only a title"}), ASVS_FLAT, LIMITS)["elements"]

        assert elements[0]["content"] == "Only a title"
        assert (elements[0]["title"], elements[0]["text"]) == ("Only a title", None)

    def test_leaf_with_title_and_text_keeps_both(self):
        leaf = {"Shortcode": "V1", "Name": "Title", "Description": "Text"}

        element = convert_source(_asvs(leaf), ASVS_FLAT, LIMITS)["elements"][0]

        assert (element["content"], element["title"]) == ("Text", "Title")

    def test_values_are_trimmed_and_integral_numbers_lose_the_fraction(self):
        leaf = {"Shortcode": 5.0, "Description": "  Text \n", "L": 2}

        element = convert_source(_asvs(leaf), ASVS_FLAT, LIMITS)["elements"][0]

        assert (element["id"], element["content"], element["properties"]) == ("5", "Text", {"level": "2"})

    def test_non_integral_number_is_kept(self):
        element = convert_source(_asvs(_requirement("V1", L=1.5)), ASVS_FLAT, LIMITS)["elements"][0]

        assert element["properties"] == {"level": "1.5"}

    def test_missing_or_null_property_is_omitted(self):
        leaf = {"Shortcode": "V1", "Description": "Text", "L": None}

        assert convert_source(_asvs(leaf), ASVS_FLAT, LIMITS)["elements"][0]["properties"] == {}

    def test_select_empty_string_reads_a_root_array(self):
        document = dict(ASVS_FLAT.document, nodes={**ASVS_FLAT.document["nodes"], "select": ""})
        mapping = Mapping.from_document(document, ASVS_FLAT.sha256, "test")

        elements = convert_source(_json([_requirement("V1")]), mapping, LIMITS)["elements"]

        assert elements[0]["id"] == "V1"

    def test_nested_select_path(self):
        document = dict(ASVS_FLAT.document, nodes={**ASVS_FLAT.document["nodes"], "select": "a.b"})
        mapping = Mapping.from_document(document, ASVS_FLAT.sha256, "test")

        elements = convert_source(_json({"a": {"b": [_requirement("V1")]}}), mapping, LIMITS)["elements"]

        assert elements[0]["id"] == "V1"

    def test_byte_order_mark_is_accepted(self):
        elements = convert_source(b"\xef\xbb\xbf" + _asvs(_requirement("V1")), ASVS_FLAT, LIMITS)["elements"]

        assert elements[0]["id"] == "V1"


class TestTreeErrors:
    def test_node_without_id(self):
        chapter = {"Shortcode": "V1", "Name": "C", "Items": [{"Shortcode": "V1.1", "Name": "S", "Items": [{}]}]}

        assert _problems(_asvs(chapter), ASVS_FLAT) == [
            "Node at Requirements[0].Items[0].Items[0] has no ID: field 'Shortcode' is missing or blank."
        ]

    def test_node_with_children_but_no_title(self):
        chapter = {"Shortcode": "V6.2", "Name": " ", "Items": [_requirement("V6.2.1"), _requirement("V6.2.2")]}

        assert _problems(_asvs(chapter), ASVS_FLAT) == [
            "Node 'V6.2' (at Requirements[0]) has 2 children but no title: field 'Name' is missing or blank. "
            "A node with children becomes a heading, and headings need a title."
        ]

    def test_empty_leaf(self):
        assert _problems(_asvs({"Shortcode": "V1.1.1", "Description": ""}), ASVS_FLAT) == [
            "Node 'V1.1.1' (at Requirements[0]) has neither a title ('Name') nor text ('Description')."
        ]

    def test_duplicate_id(self):
        chapter = {"Shortcode": "V6", "Name": "C", "Items": [_requirement("V6.2.5"), _requirement("V6.2.5")]}

        assert _problems(_asvs(chapter), ASVS_FLAT) == [
            "Duplicate ID 'V6.2.5' at Requirements[0].Items[0] and Requirements[0].Items[1]. "
            "IDs must be unique across the source."
        ]

    def test_problems_are_reported_together(self):
        error = _source_error(
            _asvs({}, {"Shortcode": "V2"}, {"Shortcode": "V3", "Description": "ok", "L": True}), ASVS_FLAT
        )

        assert error.count == 3
        assert str(error).startswith(
            f"3 problems in source (mapping {ASVS_FLAT.id}): 1) Node at Requirements[0] has no ID"
        )
        assert " 2) Node 'V2' (at Requirements[1]) has neither" in str(error)
        assert " 3) Field 'L' at Requirements[2] is a boolean" in str(error)
        assert error.details["mapping"] == ASVS_FLAT.id
        assert error.details["count"] == "3"
        assert set(error.details) == {
            "mapping",
            "count",
            *(f"{kind}.{n}" for kind in ("problem", "location") for n in (1, 2, 3)),
        }
        assert [error.details[f"location.{n}"] for n in (1, 2, 3)] == [
            "Requirements[0]",
            "Requirements[1]",
            "Requirements[2]",
        ]

    def test_redacted_form_names_locations_but_quotes_no_source_values(self):
        chapter = {"Shortcode": "C", "Name": "C", "Items": [_requirement("secret-id"), _requirement("secret-id")]}
        error = _source_error(_asvs(chapter), ASVS_FLAT)

        message, details = error.redacted()

        assert "secret-id" in str(error)
        assert "secret-id" not in message
        assert "secret-id" not in str(details)
        assert message == (
            f"Problem in source (mapping {ASVS_FLAT.id}) at Requirements[0].Items[1]; source values are withheld here, "
            "the document's error has the full message."
        )
        assert details == {"mapping": ASVS_FLAT.id, "count": "1", "location.1": "Requirements[0].Items[1]"}

    def test_single_problem_message(self):
        error = _source_error(_asvs({}), ASVS_FLAT)

        assert str(error) == f"Problem in source (mapping {ASVS_FLAT.id}): Node at Requirements[0] has no ID: " + (
            "field 'Shortcode' is missing or blank."
        )

    def test_listed_problems_are_the_first_in_source_order_whatever_phase_found_them(self):
        # 25 nodes without ID (found while reading fields), then one with a wrong-typed field at index 0.
        error = _source_error(_asvs({"Shortcode": "V0", "Description": True}, *([{}] * 25)), ASVS_FLAT)

        assert error.count == 26
        assert error.problems[0].startswith("Field 'Description' at Requirements[0]")
        assert error.problems[19].startswith("Node at Requirements[19] has no ID")

    def test_at_most_20_problems_are_listed_but_all_are_counted(self):
        error = _source_error(_asvs(*([{}] * 25)), ASVS_FLAT)

        assert error.count == 25
        assert str(error).startswith(f"25 problems in source (mapping {ASVS_FLAT.id}); the first 20: 1) ")
        assert "problem.20" in error.details
        assert "problem.21" not in error.details

    def test_select_path_not_found_lists_the_available_keys(self):
        assert _problems(_json({"Name": "x", "Chapters": []}), ASVS_FLAT) == [
            "Path 'Requirements' not found: the root object has no key 'Requirements'. Its keys: 'Name', 'Chapters'."
        ]

    def test_select_path_through_a_non_object(self):
        document = dict(ASVS_FLAT.document, nodes={**ASVS_FLAT.document["nodes"], "select": "a.b"})
        mapping = Mapping.from_document(document, ASVS_FLAT.sha256, "test")

        assert _problems(_json({"a": [1]}), mapping) == ["Path 'a.b' not found: 'a' is an array, not an object."]

    def test_select_path_must_end_at_an_array(self):
        assert _problems(_json({"Requirements": {"a": 1}}), ASVS_FLAT) == [
            "Path 'Requirements' selects an object, not an array of nodes."
        ]

    def test_node_that_is_not_an_object(self):
        assert _problems(_asvs("V1"), ASVS_FLAT) == ["Node at Requirements[0] is a string, not an object."]

    def test_children_that_are_not_an_array(self):
        assert _problems(_asvs({"Shortcode": "V1", "Name": "C", "Items": "none"}), ASVS_FLAT) == [
            "Field 'Items' at Requirements[0] is a string; a node's children must be an array."
        ]

    @pytest.mark.parametrize(("value", "kind"), [(True, "a boolean"), ({"a": 1}, "an object"), ([1], "an array")])
    def test_value_of_the_wrong_type(self, value, kind):
        assert _problems(_asvs(_requirement("V1", description=value)), ASVS_FLAT) == [
            f"Field 'Description' at Requirements[0] is {kind}; IDs, titles, text and properties must be "
            "strings or numbers."
        ]

    def test_echoed_values_are_truncated_and_escaped(self):
        long_id = "V" + "1" * 100
        chapter = {"Shortcode": "C", "Name": "C", "Items": [_requirement(long_id), _requirement(long_id)]}

        problem = _problems(_asvs(chapter), ASVS_FLAT)[0]

        assert f"'V{'1' * 63}…'" in problem
        assert long_id not in problem

    def test_control_characters_are_escaped_in_messages(self):
        chapter = {"Shortcode": "C", "Name": "C", "Items": [_requirement("a\nb"), _requirement("a\nb")]}

        assert _problems(_asvs(chapter), ASVS_FLAT)[0].startswith("Duplicate ID 'a\\nb' at")


class TestJsonHardening:
    def test_too_deep_input_is_refused_before_parsing(self):
        with pytest.raises(StructuredLimitError, match=r"MAX_JSON_DEPTH \(64\)"):
            convert_source(b"[" * 65 + b"]" * 65, ASVS_FLAT, LIMITS)

    def test_recursion_in_the_parser_is_a_depth_limit_error(self, monkeypatch):
        """Backstop if the pre-parse scan ever misses nesting: json's RecursionError is not a crash."""
        monkeypatch.setattr(structured, "_check_json_depth", lambda *_: None)

        with pytest.raises(StructuredLimitError, match="MAX_JSON_DEPTH"):
            convert_source(b"[" * 200_000 + b"]" * 200_000, ASVS_FLAT, LIMITS)

    def test_input_at_the_depth_limit_is_parsed(self):
        nested: Any = [_requirement("V1")]
        for _ in range(3):
            nested = {"Requirements": nested}
        document = dict(
            ASVS_FLAT.document, nodes={**ASVS_FLAT.document["nodes"], "select": "Requirements.Requirements"}
        )
        mapping = Mapping.from_document(document, ASVS_FLAT.sha256, "test")

        convert_source(_json(nested["Requirements"]), mapping, _limits(max_json_depth=5))

    def test_unterminated_string_full_of_escaped_quotes_is_scanned_in_linear_time(self):
        data = b'{"Requirements": "' + b'\\"' * 200_000

        started = time.monotonic()
        problems = _problems(data, ASVS_FLAT)

        assert time.monotonic() - started < 5
        assert problems[0].startswith("The input is not valid JSON: Unterminated string")

    def test_brackets_inside_strings_do_not_count_toward_depth(self):
        output = convert_source(
            _asvs(_requirement("V1", description='[[[[{{{{ "\\" [[[[')), ASVS_FLAT, _limits(max_json_depth=3)
        )

        assert output["elements"][0]["content"] == '[[[[{{{{ "\\" [[[['

    def test_duplicate_keys_are_rejected(self):
        data = b'{"Requirements": [{"Shortcode": "V1", "Shortcode": "V2", "Description": "x"}]}'

        assert _problems(data, ASVS_FLAT) == [
            "The input has a JSON object with the key 'Shortcode' twice; duplicate keys are ambiguous, so they are "
            "rejected."
        ]

    @pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_numbers_are_rejected(self, constant):
        data = b'{"Requirements": [{"Shortcode": ' + constant.encode() + b', "Description": "x"}]}'

        assert _problems(data, ASVS_FLAT) == [f"The input contains {constant}, which is not valid JSON."]

    def test_invalid_json_names_line_and_column(self):
        assert _problems(b'{"Requirements": [\n  {"Shortcode": }]}', ASVS_FLAT) == [
            "The input is not valid JSON: Expecting value at line 2, column 17."
        ]

    def test_invalid_utf8_names_the_byte(self):
        assert _problems(b'{"Requirements": "\xff"}', ASVS_FLAT) == ["The input is not valid UTF-8 (at byte 18)."]

    def test_huge_integer_is_rejected(self):
        problems = _problems(b'{"Requirements": [{"Shortcode": ' + b"9" * 5000 + b"}]}", ASVS_FLAT)

        assert problems[0].startswith("The input is not valid JSON:")

    def test_non_objects_count_toward_the_node_limit(self):
        with pytest.raises(StructuredLimitError, match=r"MAX_STRUCTURED_NODES \(5\)"):
            convert_source(_json({"Requirements": [1] * 100}), ASVS_FLAT, _limits(max_structured_nodes=5))

    def test_non_finite_number_is_named_as_such(self):
        data = b'{"Requirements": [{"Shortcode": 1e999, "Description": "x"}]}'

        assert _problems(data, ASVS_FLAT) == [
            "Field 'Shortcode' at Requirements[0] is a non-finite number; IDs, titles, text and properties must be "
            "strings or numbers."
        ]

    def test_node_count_limit(self):
        with pytest.raises(StructuredLimitError, match=r"MAX_STRUCTURED_NODES \(2\)"):
            convert_source(
                _asvs(_requirement("V1"), _requirement("V2"), _requirement("V3")),
                ASVS_FLAT,
                _limits(max_structured_nodes=2),
            )

    def test_value_length_limit(self):
        with pytest.raises(
            StructuredLimitError,
            match=r"Field 'Description' at Requirements\[0\] has 11 characters.*MAX_CELL_LENGTH \(10\)",
        ):
            convert_source(_asvs(_requirement("V1", description="x" * 11)), ASVS_FLAT, _limits(max_cell_length=10))

    def test_id_length_limit(self):
        with pytest.raises(StructuredLimitError, match=r"MAX_ID_LENGTH \(4\)"):
            convert_source(_asvs(_requirement("V1.2.3")), ASVS_FLAT, _limits(max_id_length=4))


class TestAttackParentColumn:
    @pytest.fixture(scope="class")
    @classmethod
    def output(cls) -> dict:
        return convert_source((FIXTURES / "mitre-attack-enterprise-17.1" / "input.xlsx").read_bytes(), ATTACK, LIMITS)

    def test_sub_technique_listed_above_its_parent_is_attached_and_emitted_after_it(self, output):
        ids = [e["id"] for e in output["elements"]]
        elements = _by_id(output)

        assert elements["T1087.001"]["parent_id"] == elements["T1087"]["element_id"]
        assert ids.index("T1087") < ids.index("T1087.001") < ids.index("T1087.004")

    def test_technique_with_sub_techniques_is_a_heading_followed_by_its_description(self, output):
        heading = _by_id(output)["T1548"]
        following = output["elements"][output["elements"].index(heading) + 1]

        assert (heading["type"], heading["level"], heading["content"]) == (
            "heading",
            1,
            "Abuse Elevation Control Mechanism",
        )
        assert following["id"] is None
        assert following["content"].startswith("Adversaries may circumvent")

    def test_sub_technique_keeps_name_as_title_and_mapped_properties(self, output):
        sub = _by_id(output)["T1548.002"]

        assert sub["title"].startswith("Abuse Elevation Control Mechanism: Bypass")
        assert set(sub["properties"]) == {"url", "version", "tactics", "platforms"}

    def test_unmapped_columns_are_listed_in_header_order(self, output):
        assert output["metadata"]["source"]["unmapped_fields"][:3] == ["STIX ID", "created", "last modified"]
        assert "sub-technique of" not in output["metadata"]["source"]["unmapped_fields"]


class TestTableErrors:
    def test_dangling_parent_column(self):
        data = _xlsx([ATTACK_HEADER, _technique("T1548.002", "T1548")])

        assert _problems(data, ATTACK) == [
            "Sheet \"techniques\" row 2 (ID 'T1548.002') names parent 'T1548' in column 'sub-technique of', "
            "but no row has that ID."
        ]

    def test_parent_cycle(self):
        data = _xlsx([ATTACK_HEADER, _technique("A", "B"), _technique("B", "A"), _technique("C")])

        assert _problems(data, ATTACK) == [
            "Parent cycle: 'A' (sheet \"techniques\" row 2) > 'B' (sheet \"techniques\" row 3) > 'A'."
        ]

    def test_row_that_is_its_own_parent(self):
        assert _problems(_xlsx([ATTACK_HEADER, _technique("A", "A")]), ATTACK) == [
            "Parent cycle: 'A' (sheet \"techniques\" row 2) > 'A'."
        ]

    def test_chart_sheet_is_not_a_table(self):
        workbook = openpyxl.Workbook()
        workbook.active.title = "data"
        workbook.create_chartsheet("techniques").add_chart(BarChart())
        buffer = io.BytesIO()
        workbook.save(buffer)

        assert _problems(buffer.getvalue(), ATTACK) == [
            "Sheet 'techniques' is a chart sheet, not a worksheet. Worksheets: 'data'."
        ]

    def test_wrong_declared_dimension_does_not_truncate_the_table(self):
        """openpyxl read-only trusts <dimension>; a stale one would silently drop rows and columns."""
        data = _rewrite_sheet(
            _xlsx([ATTACK_HEADER, _technique("T1"), _technique("T2")]),
            lambda xml: re.sub(rb'<dimension ref="[^"]*"/>', b'<dimension ref="A1:A1"/>', xml),
        )

        assert [e["id"] for e in convert_source(data, ATTACK, LIMITS)["elements"]] == ["T1", "T2"]

    def test_huge_declared_dimension_is_not_padded(self):
        data = _rewrite_sheet(
            _xlsx([ATTACK_HEADER, _technique("T1")]),
            lambda xml: re.sub(rb'<dimension ref="[^"]*"/>', b'<dimension ref="A1:XFD100000"/>', xml),
        )

        started = time.monotonic()
        elements = convert_source(data, ATTACK, LIMITS)["elements"]

        assert time.monotonic() - started < 5
        assert [e["id"] for e in elements] == ["T1"]

    def test_sheet_not_found_lists_the_sheets(self):
        assert _problems(_xlsx([ATTACK_HEADER], sheet="other"), ATTACK) == [
            "Sheet 'techniques' not found. Worksheets: 'other'."
        ]

    def test_mapped_column_not_found_lists_the_columns(self):
        header = [h for h in ATTACK_HEADER if h != "tactics"]

        assert _problems(_xlsx([header]), ATTACK) == [
            "Column 'tactics' not found in the header row. Columns: 'ID', 'name', 'description', 'url', 'version', "
            "'platforms', 'sub-technique of'."
        ]

    def test_blank_and_duplicate_headers(self):
        assert _problems(_xlsx([[*ATTACK_HEADER[:2], None, *ATTACK_HEADER[2:], "name"]]), ATTACK) == [
            "Column 3 of the header row is blank; every column needs a name.",
            "Column 10 of the header row repeats the name 'name'.",
        ]

    def test_value_in_a_column_without_header(self):
        data = _xlsx([ATTACK_HEADER, [*_technique("T1"), None, "stray"]])

        assert _problems(data, ATTACK) == ['Sheet "techniques" row 2 has a value in column 10, which has no header.']

    def test_blank_rows_are_skipped(self):
        data = _xlsx([ATTACK_HEADER, _technique("T1"), [None] * 8, _technique("T2")])

        assert [e["id"] for e in convert_source(data, ATTACK, LIMITS)["elements"]] == ["T1", "T2"]

    def test_row_limit(self):
        data = _xlsx([ATTACK_HEADER, _technique("T1"), _technique("T2"), _technique("T3")])

        with pytest.raises(StructuredLimitError, match=r"MAX_TABLE_ROWS \(2\)"):
            convert_source(data, ATTACK, _limits(max_table_rows=2))

    def test_column_limit(self):
        with pytest.raises(StructuredLimitError, match=r"MAX_TABLE_COLUMNS \(4\)"):
            convert_source(_xlsx([ATTACK_HEADER]), ATTACK, _limits(max_table_columns=4))

    def test_boolean_cell_is_rejected(self):
        row = _technique("T1")
        row[4] = True

        assert _problems(_xlsx([ATTACK_HEADER, row]), ATTACK) == [
            "Field 'version' at sheet \"techniques\" row 2 is a boolean; IDs, titles, text and properties must be "
            "strings or numbers."
        ]

    def test_date_cell_is_rejected(self):
        row = _technique("T1")
        row[4] = dt.date(2025, 4, 15)

        assert "is a date/time" in _problems(_xlsx([ATTACK_HEADER, row]), ATTACK)[0]

    def test_nesting_deeper_than_100_levels(self):
        rows = [ATTACK_HEADER, _technique("N0")] + [_technique(f"N{n}", f"N{n - 1}") for n in range(1, 101)]

        assert _problems(_xlsx(rows), ATTACK) == [
            "Node 'N100' (at sheet \"techniques\" row 102) is nested 101 levels deep; at most 100 levels are "
            "supported (heading levels 1-100)."
        ]


class TestNistIdPrefix:
    @pytest.fixture(scope="class")
    @classmethod
    def output(cls) -> dict:
        return convert_source((FIXTURES / "nist-sp800-53r5" / "input.xlsx").read_bytes(), NIST, LIMITS)

    def test_enhancements_sit_under_their_control(self, output):
        elements = _by_id(output)

        assert elements["AC-2"]["type"] == "heading"
        for enhancement in ("AC-2(1)", "AC-2(2)", "AC-2(3)"):
            assert elements[enhancement]["parent_id"] == elements["AC-2"]["element_id"]

    def test_ids_without_the_separator_are_top_level(self, output):
        elements = _by_id(output)

        assert elements["AC-1"]["parent_id"] is None
        assert elements["AC-3"]["parent_id"] is None

    def test_separator_that_occurs_in_no_id_fails_instead_of_flattening(self):
        document = dict(ATTACK.document, rows={**ATTACK.document["rows"], "parent": {"id_prefix": {"separator": "("}}})
        mapping = Mapping.from_document(document, ATTACK.sha256, "test")
        data = (FIXTURES / "mitre-attack-enterprise-17.1" / "input.xlsx").read_bytes()

        assert _problems(data, mapping) == [
            "The ID-prefix rule (separator '(') matches none of the 9 IDs, so nothing would nest. Check the "
            "separator; sample IDs: 'T1548', 'T1548.002', 'T1548.004', 'T1134', 'T1134.002'."
        ]

    def test_separator_in_some_ids_is_fine(self, output):
        assert _by_id(output)["AC-1"]["parent_id"] is None

    def test_dangling_prefix_parent(self):
        header = ["Control Identifier", "Control (or Control Enhancement) Name", "Control Text", "Discussion"]
        data = _xlsx([[*header, "Related Controls"], ["AC-2(1)", "Name", "Text", None, None]], "SP 800-53 Revision 5")

        assert _problems(data, NIST) == [
            "Sheet \"SP 800-53 Revision 5\" row 2 (ID 'AC-2(1)') has parent 'AC-2' by the ID-prefix rule "
            "(separator '('), but no row has that ID."
        ]


def _rewrite_sheet(data: bytes, rewrite: Callable[[bytes], bytes]) -> bytes:
    """Return the workbook *data* with xl/worksheets/sheet1.xml passed through *rewrite*."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    entries = {info.filename: archive.read(info) for info in archive.infolist()}
    entries["xl/worksheets/sheet1.xml"] = rewrite(entries["xl/worksheets/sheet1.xml"])
    return _zip(entries)


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


class TestXlsxHardening:
    def test_zip_bomb_is_refused_before_openpyxl_reads_it(self):
        bomb = _zip({"xl/workbook.xml": b"<workbook/>", "xl/worksheets/sheet1.xml": b"\0" * 50_000_000})

        with pytest.raises(StructuredLimitError, match="MAX_XLSX_COMPRESSION_RATIO"):
            convert_source(bomb, ATTACK, LIMITS)

    def test_ratio_limit_names_the_entry_for_the_caller_only(self):
        bomb = _zip({"xl/workbook.xml": b"<workbook/>", "xl/tenant-secret.xml": b"\0" * 5_000_000})

        with pytest.raises(StructuredLimitError) as exc_info:
            convert_source(bomb, ATTACK, LIMITS)

        assert "tenant-secret" in str(exc_info.value)
        assert "tenant-secret" not in exc_info.value.redacted_message
        assert exc_info.value.redacted_message.startswith("A ZIP entry expands 5000000 bytes from ")
        assert "MAX_XLSX_COMPRESSION_RATIO (100)" in exc_info.value.redacted_message

    def test_limit_messages_without_source_values_are_their_own_redacted_form(self):
        with pytest.raises(StructuredLimitError) as exc_info:
            convert_source(b"[" * 100, ASVS, LIMITS)

        assert exc_info.value.redacted_message == str(exc_info.value)

    def test_total_uncompressed_size_limit(self):
        data = _xlsx([ATTACK_HEADER, _technique("T1")])

        with pytest.raises(StructuredLimitError, match=r"MAX_XLSX_UNCOMPRESSED_BYTES \(1000\)"):
            convert_source(data, ATTACK, _limits(max_xlsx_uncompressed_bytes=1000))

    def test_entry_count_limit(self):
        data = _xlsx([ATTACK_HEADER, _technique("T1")])

        with pytest.raises(StructuredLimitError, match=r"MAX_XLSX_ENTRIES \(3\)"):
            convert_source(data, ATTACK, _limits(max_xlsx_entries=3))

    def test_encrypted_entry_is_refused(self):
        data = bytearray(_xlsx([ATTACK_HEADER, _technique("T1")]))
        central = data.find(b"PK\x01\x02")
        data[central + 8] |= 0x1  # general purpose flag bit 0: encrypted

        assert _problems(bytes(data), ATTACK) == [
            "The workbook has encrypted entries; encrypted workbooks are not supported."
        ]

    def test_input_that_is_not_a_zip(self):
        assert _problems(b"ID,name\n", ATTACK) == [
            "The input is not a valid XLSX file: no end of central directory record (truncated, or not a ZIP archive)."
        ]

    def test_forged_directory_size_is_a_limit_before_zipfile_reads_it(self):
        directory = (b"PK\x01\x02" + b"\0" * 42) * 500_000
        end = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 1, 1, len(directory), 4, 0)

        started = time.monotonic()
        with pytest.raises(StructuredLimitError, match=r"MAX_XLSX_ENTRIES \(10000\)"):
            convert_source(b"PK\x03\x04" + directory + end, ATTACK, LIMITS)

        assert time.monotonic() - started < 0.5

    def test_zipfile_and_zipdir_must_count_the_same_entries(self, monkeypatch):
        """Belt and braces: the entry cap is only sound if zipfile reads the directory zipdir counted."""
        real = structured.read_zip_directory

        def undercount(data, max_entries):
            directory = real(data, max_entries)
            return ZipDirectory(directory.names, directory.entries - 1)

        monkeypatch.setattr(structured, "read_zip_directory", undercount)

        assert _problems(_xlsx([ATTACK_HEADER, _technique("T1")]), ATTACK) == [
            "The input is not a valid XLSX file: its ZIP directory reads differently to different readers."
        ]

    def test_unsupported_zip_version_is_an_input_error(self):
        data = bytearray(_xlsx([ATTACK_HEADER, _technique("T1")]))
        data[data.find(b"PK\x01\x02") + 6] = 0xFF

        problem = _problems(bytes(data), ATTACK)[0]

        assert problem.startswith("The input is not a valid XLSX file: it uses an unsupported ZIP feature (")

    def test_workbook_without_styles_converts_without_a_warning(self):
        """openpyxl warns "Workbook contains no default style" when a stylesheet has no named styles (NIST CSF 2.0)."""
        archive = zipfile.ZipFile(io.BytesIO(_xlsx([ATTACK_HEADER, _technique("T1")])))
        entries = {i.filename: archive.read(i) for i in archive.infolist()}
        entries["xl/styles.xml"] = re.sub(
            rb"<cellStyles.*?</cellStyles>", b"", entries["xl/styles.xml"], flags=re.DOTALL
        )

        with warnings.catch_warnings():
            warnings.simplefilter("error")
            elements = convert_source(_zip(entries), ATTACK, LIMITS)["elements"]

        assert [e["id"] for e in elements] == ["T1"]

    def test_xml_entities_in_the_workbook_are_refused(self):
        archive = zipfile.ZipFile(io.BytesIO(_xlsx([ATTACK_HEADER, _technique("T1")])))
        entries = {info.filename: archive.read(info) for info in archive.infolist()}
        entries["xl/worksheets/sheet1.xml"] = (
            b'<?xml version="1.0"?><!DOCTYPE lol [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
            b'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            b'<row r="1"><c r="A1" t="inlineStr"><is><t>&lol2;</t></is></c></row></sheetData></worksheet>'
        )

        assert _problems(_zip(entries), ATTACK) == [
            "The workbook contains XML entity declarations, which are not allowed."
        ]

    def test_openpyxl_parses_through_defusedxml_here(self):
        structured._require_defused_openpyxl()

    def test_openpyxl_without_defusedxml_fails_closed(self, monkeypatch):
        from xml.etree import ElementTree as ET

        import openpyxl.xml.functions

        monkeypatch.setattr(openpyxl.xml.functions, "iterparse", ET.iterparse)

        with pytest.raises(RuntimeError, match="defusedxml"):
            structured._require_defused_openpyxl()


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


class TestCsv:
    def test_rows_with_a_parent_column(self):
        data = _csv("ID;Name;Text;Parent\nA;Alpha;;\nA.1;;First;A\n")

        elements = convert_source(data, CSV_MAPPING, LIMITS)["elements"]

        assert [(e["type"], e["id"], e["content"], e["parent_id"]) for e in elements] == [
            ("heading", "A", "Alpha", None),
            ("paragraph", "A.1", "First", "e-001"),
        ]

    def test_metadata_for_a_csv_source(self):
        metadata = convert_source(_csv("ID;Name;Text;Parent;Extra\nA;Alpha;;;x\n"), CSV_MAPPING, LIMITS)["metadata"]

        assert metadata["detected_format"] == "csv"
        assert metadata["source"]["format"] == "csv"
        assert metadata["source"]["unmapped_fields"] == ["Extra"]
        assert metadata["source"]["node_count"] == 1

    def test_quoted_fields_with_delimiters_and_newlines(self):
        data = _csv('ID;Name;Text;Parent\nA;"x; y";"line 1\nline 2";\n')

        element = convert_source(data, CSV_MAPPING, LIMITS)["elements"][0]

        assert (element["title"], element["content"]) == ("x; y", "line 1\nline 2")

    def test_byte_order_mark_is_stripped(self):
        elements = convert_source(b"\xef\xbb\xbf" + _csv("ID;Name;Text;Parent\nA;Alpha;;\n"), CSV_MAPPING, LIMITS)[
            "elements"
        ]

        assert elements[0]["id"] == "A"

    def test_rows_are_numbered_with_the_header_as_row_1(self):
        assert _problems(_csv("ID;Name;Text;Parent\nA;Alpha;;\n;Beta;;\n"), CSV_MAPPING) == [
            "Node at row 3 has no ID: field 'ID' is missing or blank."
        ]

    def test_nul_is_rejected(self):
        assert _problems(_csv("ID;Name;Text;Parent\nA\0;Alpha;;\n"), CSV_MAPPING) == [
            "The input contains a NUL character, so it is not CSV text."
        ]

    def test_invalid_utf8_is_rejected(self):
        assert _problems(b"ID;Name;Text;Parent\nA;\xe9;;\n", CSV_MAPPING) == [
            "The input is not valid UTF-8 (at byte 22)."
        ]

    def test_malformed_quoting_is_rejected(self):
        problems = _problems(_csv('ID;Name;Text;Parent\nA;"x"y;;\n'), CSV_MAPPING)

        assert problems[0].startswith("The input is not valid CSV near line 2:")

    def test_field_longer_than_the_limit(self):
        with pytest.raises(StructuredLimitError, match=r"MAX_CELL_LENGTH \(10\)"):
            convert_source(
                _csv("ID;Name;Text;Parent\nA;" + "x" * 20 + ";;\n"), CSV_MAPPING, _limits(max_cell_length=10)
            )

    def test_duplicate_header(self):
        assert _problems(_csv("ID;Name;Text;Parent;ID\n"), CSV_MAPPING) == [
            "Column 5 of the header row repeats the name 'ID'."
        ]

    def test_empty_input_has_no_header_row(self):
        assert _problems(b"", CSV_MAPPING) == ["The table has no header row."]


class TestRunJob:
    def _job(self, tmp_path: Path, mapping: Mapping, data: bytes) -> tuple[str, str, dict]:
        input_path = tmp_path / "input"
        input_path.write_bytes(data)
        job = {"mapping": mapping.document, "mapping_sha256": mapping.sha256, "limits": LIMITS.__dict__}
        return str(input_path), str(tmp_path / "output.json"), job

    def test_writes_the_output(self, tmp_path):
        data = _asvs(_requirement("V1"))
        input_path, output_path, job = self._job(tmp_path, ASVS_FLAT, data)

        assert run_job(input_path, output_path, job) == 0

        assert json.loads(Path(output_path).read_text()) == convert_source(data, ASVS_FLAT, LIMITS)

    def test_source_problems_go_to_the_error_file(self, tmp_path):
        input_path, output_path, job = self._job(tmp_path, ASVS_FLAT, _asvs({}))

        assert run_job(input_path, output_path, job) == INPUT_ERROR_EXIT_CODE

        error = json.loads((tmp_path / "error.json").read_text())
        assert error["kind"] == "source"
        assert error["message"].startswith("Problem in source")
        assert error["details"]["count"] == "1"
        assert not Path(output_path).exists()

    def test_limit_errors_go_to_the_error_file(self, tmp_path):
        input_path, output_path, job = self._job(tmp_path, ASVS_FLAT, b"[" * 100)

        assert run_job(input_path, output_path, job) == INPUT_ERROR_EXIT_CODE

        error = json.loads((tmp_path / "error.json").read_text())
        assert (error["kind"], error["details"]) == ("limit", {})
        assert "MAX_JSON_DEPTH" in error["message"]
        assert error["redacted_message"] == error["message"]


class TestCli:
    def test_prints_the_validated_output(self, capsys):
        mapping_file = Path(structured.__file__).parent / "mappings" / f"{ASVS.id}.yaml"

        status = structured.main(["--mapping", str(mapping_file), str(FIXTURES / "owasp-asvs-5.0.0" / "input.json")])

        assert status == 0
        output = json.loads(capsys.readouterr().out)
        assert output["schema_version"] == SCHEMA_VERSION
        assert (
            output["elements"] == json.loads((FIXTURES / "owasp-asvs-5.0.0" / "expected.json").read_text())["elements"]
        )

    def test_prints_problems_and_fails(self, tmp_path, capsys):
        mapping_file = Path(structured.__file__).parent / "mappings" / f"{ASVS.id}.yaml"
        source = tmp_path / "bad.json"
        source.write_bytes(_asvs({}))

        assert structured.main(["--mapping", str(mapping_file), str(source)]) == 1

        assert "has no ID" in capsys.readouterr().err

    def test_input_of_another_format_fails(self, tmp_path, capsys):
        mapping_file = Path(structured.__file__).parent / "mappings" / f"{ATTACK.id}.yaml"

        assert structured.main(["--mapping", str(mapping_file), str(FIXTURES / "owasp-asvs-5.0.0" / "input.json")]) == 1

        assert f"Mapping '{ATTACK.id}' reads xlsx, but the input is json." in capsys.readouterr().err

    def test_invalid_mapping_fails(self, tmp_path, capsys):
        mapping_file = tmp_path / "x.yaml"
        mapping_file.write_text("id: [\n")

        assert structured.main(["--mapping", str(mapping_file), str(mapping_file)]) == 2

        assert "invalid YAML" in capsys.readouterr().err


def test_structured_module_does_not_import_docling():
    import subprocess
    import sys

    code = "import sys, crosscodex_ingestion.structured\n" + (
        "print(any(m.split('.')[0] in ('docling', 'torch') for m in sys.modules))"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)

    assert result.stdout.strip() == "False"


def test_limits_are_read_from_settings():
    limits = StructuredLimits.from_settings(IngestionSettings(max_json_depth=7, max_table_rows=9))

    assert (limits.max_json_depth, limits.max_table_rows) == (7, 9)
    assert copy.copy(limits) == limits


class TestLevelColumns:
    """rows.levels: a table with one column pair per hierarchy level, every row complete."""

    def _tree(self, output: dict) -> list[tuple]:
        return [(e["type"], e["id"], e["content"], e["level"], e["parent_id"]) for e in output["elements"]]

    def test_levels_become_nested_headings_above_the_requirement(self):
        data = _csv(LEVELS_HEADER + "C1,Chap,C1.1,Sect,R1,Text one,1\n")

        output = convert_source(data, LEVELS_MAPPING, LIMITS)

        assert self._tree(output) == [
            ("heading", "C1", "Chap", 1, None),
            ("heading", "C1.1", "Sect", 2, "e-001"),
            ("paragraph", "R1", "Text one", None, "e-002"),
        ]

    def test_repeated_level_values_are_one_node(self):
        data = _csv(
            LEVELS_HEADER + "C1,Chap,C1.1,Sect,R1,One,1\nC1,Chap,C1.1,Sect,R2,Two,1\nC1,Chap,C1.2,Other,R3,Three,2\n"
        )

        output = convert_source(data, LEVELS_MAPPING, LIMITS)

        assert [e["id"] for e in output["elements"] if e["type"] == "heading"] == ["C1", "C1.1", "C1.2"]
        assert [e["parent_id"] for e in output["elements"] if e["id"] in {"R1", "R2", "R3"}] == [
            "e-002",
            "e-002",
            "e-005",
        ]

    def test_node_order_is_first_appearance(self):
        data = _csv(LEVELS_HEADER + "C2,B,C2.1,S,R1,t,1\nC1,A,C1.1,S,R2,t,1\nC2,B,C2.1,S,R3,t,1\n")

        output = convert_source(data, LEVELS_MAPPING, LIMITS)

        assert [e["id"] for e in output["elements"] if e["id"]] == ["C2", "C2.1", "R1", "R3", "C1", "C1.1", "R2"]

    def test_level_columns_are_mapped_fields_not_unmapped(self):
        data = _csv(LEVELS_HEADER.rstrip() + ",extra\n" + "C1,Chap,C1.1,Sect,R1,Text,1,x\n")

        source = convert_source(data, LEVELS_MAPPING, LIMITS)["metadata"]["source"]

        assert source["unmapped_fields"] == ["extra"]

    def test_same_id_with_two_titles_names_both_rows(self):
        data = _csv(LEVELS_HEADER + "C1,Chap,C1.1,S,R1,t,1\nC1,Other,C1.1,S,R2,t,1\n")

        problems = _problems(data, LEVELS_MAPPING)

        assert problems == [
            "Conflicting level nodes: ID 'C1' is titled 'Chap' at row 2 and 'Other' at row 3. "
            "A level ID must have one title."
        ]

    def test_same_id_under_two_parents_names_both_rows(self):
        data = _csv(LEVELS_HEADER + "C1,A,S1,S,R1,t,1\nC2,B,S1,S,R2,t,1\n")

        problems = _problems(data, LEVELS_MAPPING)

        assert problems == [
            "Conflicting level nodes: ID 'S1' is under 'C1' at row 2 and under 'C2' at row 3. "
            "A level ID must have one parent."
        ]

    def test_same_id_at_two_levels_names_both_rows(self):
        data = _csv(LEVELS_HEADER + "C1,A,S1,S,R1,t,1\nX,B,C1,S,R2,t,1\n")

        problems = _problems(data, LEVELS_MAPPING)

        assert problems == [
            "Conflicting level nodes: ID 'C1' is at level 1 at row 2 and at level 2 at row 3. "
            "A level ID must have one level."
        ]

    def test_requirement_id_equal_to_a_level_id_is_a_duplicate(self):
        data = _csv(LEVELS_HEADER + "C1,A,S1,S,C1,t,1\n")

        problems = _problems(data, LEVELS_MAPPING)

        assert problems == ["Duplicate ID 'C1' at row 2 and row 2. IDs must be unique across the source."]

    @pytest.mark.parametrize(
        ("row", "column"),
        [(",Chap,S1,S,R1,t,1", "ch_id"), ("C1,,S1,S,R1,t,1", "ch_name"), ("C1,A,,S,R1,t,1", "sec_id")],
    )
    def test_blank_level_cell_names_the_row_and_column(self, row, column):
        problems = _problems(_csv(LEVELS_HEADER + row + "\n"), LEVELS_MAPPING)

        assert problems == [f"Row 2 has no value in level column {column!r}; every row must fill every level."]

    def test_missing_level_column_is_reported_with_the_header_check(self):
        data = _csv("ch_id,ch_name,req_id,req_text,L\nC1,A,R1,t,1\n")

        problems = _problems(data, LEVELS_MAPPING)

        assert problems[0].startswith("Column 'sec_id' not found in the header row.")

    def test_synthesised_nodes_count_against_the_node_limit(self):
        data = _csv(LEVELS_HEADER + "C1,A,S1,S,R1,t,1\n")  # 1 row -> 3 nodes

        with pytest.raises(StructuredLimitError, match="MAX_STRUCTURED_NODES"):
            convert_source(data, LEVELS_MAPPING, _limits(max_structured_nodes=2))

    def test_level_cell_of_the_wrong_type_is_reported(self):
        data = _xlsx(
            [["ch_id", "ch_name", "sec_id", "sec_name", "req_id", "req_text", "L"], [True, "A", "S", "S", "R", "t", 1]]
        )
        mapping = Mapping.from_document(
            {
                **LEVELS_MAPPING.document,
                "source": {"format": "xlsx", "release": "https://example.org/l.xlsx", "sheet": "techniques"},
            },
            "e" * 64,
            "test",
        )

        problems = _problems(data, mapping)

        assert problems == [
            "Field 'ch_id' at sheet \"techniques\" row 2 is a boolean; "
            "IDs, titles, text and properties must be strings or numbers."
        ]


class TestAsvsCsv:
    """The ASVS CSV release must give the same tree as the ASVS JSON release."""

    def _tree(self, mapping: Mapping, name: str) -> list[dict]:
        directory = FIXTURES / ("owasp-asvs-5.0.0" if mapping is ASVS else "owasp-asvs-5.0.0-csv")
        elements = convert_source((directory / name).read_bytes(), mapping, LIMITS)["elements"]
        # Chapter short names exist only in the JSON release.
        return [{k: v for k, v in e.items() if not (e["type"] == "heading" and k == "properties")} for e in elements]

    def test_csv_tree_matches_the_json_tree(self):
        assert self._tree(ASVS_CSV, "input.csv") == self._tree(ASVS, "input.json")


class TestLevelCells:
    """rows.level_cells: one column per level, exactly one filled per row, the ID inside the cell."""

    def _tree(self, text: str) -> list[tuple]:
        elements = convert_source(_csv(CELLS_HEADER + text), CELLS_MAPPING, LIMITS)["elements"]
        return [(e["type"], e["id"], e["content"], e["level"], e["parent_id"]) for e in elements]

    def test_rows_nest_by_their_level_column(self):
        tree = self._tree(
            "GOVERN (GV): Strategy.,,,\n"
            ",Context (GV.OC): Circumstances.,,\n"
            ",,GV.OC-01: Mission is understood.,Ex1\n"
            ",,GV.OC-02: Stakeholders are understood.,Ex2\n"
            ",Risk (GV.RM): Priorities.,,\n"
            ",,GV.RM-01: Objectives.,\n"
        )

        assert tree == [
            ("heading", "GV", "GOVERN", 1, None),
            ("paragraph", None, "Strategy.", None, "e-001"),
            ("heading", "GV.OC", "Context", 2, "e-001"),
            ("paragraph", None, "Circumstances.", None, "e-003"),
            ("paragraph", "GV.OC-01", "Mission is understood.", None, "e-003"),
            ("paragraph", "GV.OC-02", "Stakeholders are understood.", None, "e-003"),
            ("heading", "GV.RM", "Risk", 2, "e-001"),
            ("paragraph", None, "Priorities.", None, "e-007"),
            ("paragraph", "GV.RM-01", "Objectives.", None, "e-007"),
        ]

    def test_other_columns_become_properties(self):
        elements = convert_source(
            _csv(CELLS_HEADER + "A (A): t,,,\n,C (A.C): c,,\n,,A-1: x,Ex1\n"), CELLS_MAPPING, LIMITS
        )["elements"]

        assert elements[-1]["properties"] == {"examples": "Ex1"}

    def test_level_columns_are_mapped_fields_not_unmapped(self):
        data = _csv("Function,Category,Sub,Examples,Other\nA (A): t,,,,o\n")

        source = convert_source(data, CELLS_MAPPING, LIMITS)["metadata"]["source"]

        assert source["unmapped_fields"] == ["Other"]

    def test_cell_without_brackets_is_the_id_before_the_separator(self):
        tree = self._tree("A (A): t,,,\n,C (A.C): c,,\n,,A-1: x,\n")

        assert tree[-1] == ("paragraph", "A-1", "x", None, "e-003")

    def test_bare_leaf_has_neither_title_nor_text(self):
        problems = _problems(_csv(CELLS_HEADER + "A (A): t,,,\n,C (A.C): c,,\n,,A-1,\n"), CELLS_MAPPING)

        assert problems == ["Node 'A-1' (at row 4) has neither a title ('Sub (title)') nor text ('Sub (text)')."]

    def test_only_the_first_separator_splits(self):
        tree = self._tree("A (A): one: two,,,\n")

        assert tree == [("paragraph", "A", "one: two", None, None)]

    def test_bare_repeat_of_a_level_row_re_enters_it(self):
        tree = self._tree("A (A): t,,,\n,B (A.B): u,,\n,,A.B-1: x,\nA (A),,,\n,C (A.C): v,,\n,,A.C-1: y,\n")

        assert [(t[1], t[4]) for t in tree if t[0] == "heading"] == [("A", None), ("A.B", "e-001"), ("A.C", "e-001")]

    def test_repeat_that_carries_text_is_a_duplicate_id(self):
        problems = _problems(_csv(CELLS_HEADER + "A (A): t,,,\nA (A): again,,,\n"), CELLS_MAPPING)

        assert problems == ["Duplicate ID 'A' at row 2 and row 3. IDs must be unique across the source."]

    def test_repeat_with_another_title_is_a_conflict_naming_both_rows(self):
        problems = _problems(_csv(CELLS_HEADER + "A (A): t,,,\nOther (A),,,\n"), CELLS_MAPPING)

        assert problems == [
            "Conflicting level nodes: ID 'A' is titled 'A' at row 2 and 'Other' at row 3. "
            "A level ID must have one title."
        ]

    @pytest.mark.parametrize(
        ("row", "problem"),
        [
            ("A (A): t,B (B): u,,", "Row 2 fills 2 level columns ('Function', 'Category'); exactly one is allowed."),
            (",,,Ex1", "Row 2 fills none of the level columns ('Function', 'Category', 'Sub')."),
            (",,A-1: x,", "Row 2 fills level column 'Sub' (level 3), but no row at level 2 precedes it."),
            (",B (B): u,,", "Row 2 fills level column 'Category' (level 2), but no row at level 1 precedes it."),
        ],
    )
    def test_misplaced_rows_are_reported(self, row, problem):
        assert _problems(_csv(CELLS_HEADER + row + "\n"), CELLS_MAPPING) == [problem]

    def test_a_row_cannot_skip_the_level_above_it_after_a_new_parent(self):
        data = _csv(CELLS_HEADER + "A (A): t,,,\n,B (A.B): u,,\nC (C): v,,,\n,,C-1: x,\n")

        problems = _problems(data, CELLS_MAPPING)

        assert problems == ["Row 5 fills level column 'Sub' (level 3), but no row at level 2 precedes it."]

    def test_missing_level_column_is_reported_with_the_header_check(self):
        problems = _problems(_csv("Function,Sub,Examples\nA (A): t,,\n"), CELLS_MAPPING)

        assert problems[0].startswith("Column 'Category' not found in the header row.")

    def test_empty_id_between_brackets_is_reported(self):
        assert _problems(_csv(CELLS_HEADER + "Name (): t,,,\n"), CELLS_MAPPING) == [
            "Row 2: the ID between '(' and ')' is empty."
        ]

    def test_empty_id_before_the_separator_is_reported(self):
        assert _problems(_csv(CELLS_HEADER + ": t,,,\n"), CELLS_MAPPING) == ["Row 2: the cell has no ID before ':'."]


class TestNistCsf:
    def test_function_category_and_subcategory_nest(self):
        data = (FIXTURES / "nist-csf-2.0" / "input.xlsx").read_bytes()

        elements = convert_source(data, NIST_CSF, LIMITS)["elements"]

        headings = [(e["id"], e["content"], e["level"]) for e in elements if e["type"] == "heading"]
        assert headings[:3] == [
            ("GV", "GOVERN", 1),
            ("GV.OC", "Organizational Context", 2),
            ("GV.RM", "Risk Management Strategy", 2),
        ]
        by_id = _by_id({"elements": elements})
        assert by_id["GV.OC-01"]["parent_id"] == by_id["GV.OC"]["element_id"]
        assert by_id["GV.OC-01"]["content"].startswith("The organizational mission is understood")


def _roles(output: dict) -> dict[str, str]:
    return {e["id"]: e["role"] for e in output["elements"] if e.get("id") is not None}


def _validate(output: dict) -> None:
    validate_output(
        {"schema_version": SCHEMA_VERSION, **output, "metadata": {**output["metadata"], "processing_time_ms": 1}}
    )


class TestRoles:
    """Every element with an ID carries its role, title and text; group_levels says which levels are groups."""

    def _asvs_data(self) -> bytes:
        section = {
            "Shortcode": "V1.1",
            "Name": "Section",
            "Items": [_requirement("V1.1.1", "First."), _requirement("V1.1.2", "Second.")],
        }
        return _asvs({"Shortcode": "V1", "Name": "Chapter", "Items": [section]})

    def test_without_group_levels_every_id_is_a_control(self):
        mapping = _with_document(ATTACK, group_levels=None)
        data = _xlsx([ATTACK_HEADER, _technique("T1055", name="Process Injection"), _technique("T1055.001", "T1055")])

        output = convert_source(data, mapping, LIMITS)

        _validate(output)
        assert set(_roles(output).values()) == {"control"}
        heading = _by_id(output)["T1055"]
        assert (heading["title"], heading["text"]) == ("Process Injection", "Adversaries may.")

    def test_asvs_group_levels_two(self):
        output = convert_source(self._asvs_data(), _with_document(ASVS, group_levels=2), LIMITS)

        _validate(output)
        assert _roles(output) == {"V1": "group", "V1.1": "group", "V1.1.1": "control", "V1.1.2": "control"}
        requirement = _by_id(output)["V1.1.1"]
        assert (requirement["title"], requirement["text"]) == (None, "First.")

    def test_csv_levels_with_group_levels_two(self):
        data = _csv(
            "chapter_id,chapter_name,section_id,section_name,req_id,req_description,L\n"
            "V1,Chapter,V1.1,Section,V1.1.1,First.,1\n"
            "V1,Chapter,V1.1,Section,V1.1.2,Second.,2\n"
        )

        output = convert_source(data, _with_document(ASVS_CSV, group_levels=2), LIMITS)

        _validate(output)
        assert _roles(output) == {"V1": "group", "V1.1": "group", "V1.1.1": "control", "V1.1.2": "control"}
        assert _by_id(output)["V1.1.2"]["text"] == "Second."

    def test_csf_level_cells_with_group_levels_two(self):
        mapping = _with_document(CELLS_MAPPING, group_levels=2)

        output = convert_source(
            _csv(
                CELLS_HEADER
                + "GOVERN (GV): Strategy.,,,\n,Context (GV.OC): Circumstances.,,\n,,GV.OC-01: Mission.,Ex1\n"
            ),
            mapping,
            LIMITS,
        )

        _validate(output)
        assert _roles(output) == {"GV": "group", "GV.OC": "group", "GV.OC-01": "control"}
        assert _by_id(output)["GV"]["text"] == "Strategy."

    def test_rows_parent_shape_with_group_levels_one(self):
        mapping = _with_document(ATTACK, group_levels=1)
        data = _xlsx([ATTACK_HEADER, _technique("T1055"), _technique("T1055.001", "T1055")])

        output = convert_source(data, mapping, LIMITS)

        _validate(output)
        assert _roles(output) == {"T1055": "group", "T1055.001": "control"}

    def test_title_and_text_are_null_not_blank(self):
        mapping = _with_document(ASVS, group_levels=1)
        chapter = {"Shortcode": "V1", "Name": "Chapter", "Items": [{"Shortcode": "V1.1", "Name": "Title only"}]}

        output = convert_source(_asvs(chapter), mapping, LIMITS)

        _validate(output)
        by_id = _by_id(output)
        assert (by_id["V1"]["title"], by_id["V1"]["text"]) == ("Chapter", None)
        assert (by_id["V1.1"]["title"], by_id["V1.1"]["text"]) == ("Title only", None)

    def test_group_levels_deeper_than_a_branch_fails_naming_the_leaf(self):
        problems = _problems(self._asvs_data(), _with_document(ASVS, group_levels=3))

        assert len(problems) == 2
        assert all("'V1.1.1'" in p or "'V1.1.2'" in p for p in problems)
        assert "group_levels (3)" in problems[0]
        assert "level 3" in problems[0]
        assert "owasp-asvs-5.0.0-json" in str(_source_error(self._asvs_data(), _with_document(ASVS, group_levels=3)))

    def test_float_group_levels_reads_as_an_integer(self):
        output = convert_source(self._asvs_data(), _with_document(ASVS, group_levels=2.0), LIMITS)

        assert _roles(output)["V1.1"] == "group"
        assert "group_levels (3)" in _problems(self._asvs_data(), _with_document(ASVS, group_levels=3.0))[0]

    def test_catalog_version_from_mapping(self):
        with_version = convert_source(self._asvs_data(), _with_document(ASVS, version="5.0.0"), LIMITS)
        without = convert_source(self._asvs_data(), _with_document(ASVS, version=None), LIMITS)

        _validate(with_version)
        _validate(without)
        assert with_version["metadata"]["source"]["catalog_version"] == "5.0.0"
        assert without["metadata"]["source"]["catalog_version"] is None

    def test_elements_without_id_have_no_role(self):
        chapter = {"Shortcode": "V1", "Name": "Chapter", "Description": "Intro.", "Items": [_requirement("V1.1")]}

        output = convert_source(_asvs(chapter), _with_document(ASVS, group_levels=None), LIMITS)

        paragraph = output["elements"][1]
        assert paragraph["id"] is None
        assert not {"role", "title", "text"} & paragraph.keys()
