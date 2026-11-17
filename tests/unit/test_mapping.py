"""Tests for mapping.py — loading, validating and registering structured-source mappings."""

import hashlib
import shutil
from importlib.resources import files
from pathlib import Path

import pytest

from crosscodex_ingestion.mapping import Mapping, MappingError, MappingRegistry, load_mapping_file, load_registry

PACKAGED = Path(str(files("crosscodex_ingestion").joinpath("mappings")))
PACKAGED_IDS = ["mitre-attack-enterprise-17.1-techniques-xlsx", "nist-sp800-53r5-catalog-xlsx", "owasp-asvs-5.0.0-json"]

TREE = """\
mapping_version: 1
id: example-1.0-json
title: Example
source: {format: json, release: "https://example.org/example.json"}
nodes: {select: Items, children: Items, id: Id, title: Name}
"""

TABLE = """\
mapping_version: 1
id: example-1.0-csv
title: Example
source: {format: csv, release: "https://example.org/example.csv", delimiter: ";"}
rows: {id: ID, text: Text, parent: {id_prefix: {separator: "("}}}
"""


def _write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


class TestLoadMappingFile:
    def test_tree_mapping_loads_with_its_file_hash(self, tmp_path):
        path = _write(tmp_path, "example-1.0-json.yaml", TREE)

        mapping = load_mapping_file(path)

        assert mapping.id == "example-1.0-json"
        assert mapping.format == "json"
        assert mapping.mapping_version == 1
        assert mapping.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
        assert mapping.document["nodes"]["children"] == "Items"

    def test_table_mapping_loads(self, tmp_path):
        mapping = load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", TABLE))

        assert mapping.format == "csv"
        assert mapping.document["rows"]["parent"] == {"id_prefix": {"separator": "("}}

    def test_malformed_yaml_names_the_file_line_and_column(self, tmp_path):
        path = _write(tmp_path, "example-1.0-json.yaml", "mapping_version: 1\nid: [unclosed\n")

        with pytest.raises(MappingError) as exc_info:
            load_mapping_file(path)

        message = str(exc_info.value)
        assert str(path) in message
        assert "line 3" in message or "line 2" in message
        assert "column" in message

    def test_duplicate_yaml_key_is_rejected_with_its_line(self, tmp_path):
        path = _write(tmp_path, "example-1.0-json.yaml", TREE + "title: Again\n")

        with pytest.raises(MappingError, match=r"line 6, column 1: duplicate key 'title'"):
            load_mapping_file(path)

    def test_schema_violation_names_the_file_and_the_location(self, tmp_path):
        path = _write(
            tmp_path, "example-1.0-json.yaml", TREE.replace("children: Items, ", "children: Items, kids: X, ")
        )

        with pytest.raises(MappingError) as exc_info:
            load_mapping_file(path)

        assert str(path) in str(exc_info.value)
        assert "nodes" in str(exc_info.value)
        assert "kids" in str(exc_info.value)

    def test_unknown_mapping_version_is_rejected(self, tmp_path):
        path = _write(tmp_path, "example-1.0-json.yaml", TREE.replace("mapping_version: 1", "mapping_version: 2"))

        with pytest.raises(MappingError, match="mapping_version"):
            load_mapping_file(path)

    def test_json_source_with_rows_is_rejected(self, tmp_path):
        text = TREE.replace("nodes: {select: Items, children: Items, id: Id, title: Name}", "rows: {id: ID, title: T}")

        with pytest.raises(MappingError):
            load_mapping_file(_write(tmp_path, "example-1.0-json.yaml", text))

    def test_xlsx_source_requires_a_sheet(self, tmp_path):
        text = TABLE.replace(
            'format: csv, release: "https://example.org/example.csv", delimiter: ";"',
            'format: xlsx, release: "https://example.org/x.xlsx"',
        )

        with pytest.raises(MappingError, match="sheet"):
            load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", text))

    def test_csv_option_on_an_xlsx_source_is_rejected(self, tmp_path):
        text = TABLE.replace("format: csv", "format: xlsx, sheet: S")

        with pytest.raises(MappingError, match="delimiter"):
            load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", text))

    @pytest.mark.parametrize("delimiter", ['"\\""', '"\\n"', '"\\r"'])
    def test_csv_delimiter_cannot_be_a_quote_or_line_break(self, tmp_path, delimiter):
        text = TABLE.replace('delimiter: ";"', f"delimiter: {delimiter}")

        with pytest.raises(MappingError, match="delimiter"):
            load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", text))

    def test_mapping_without_title_or_text_is_rejected(self, tmp_path):
        text = TREE.replace(", title: Name", "")

        with pytest.raises(MappingError):
            load_mapping_file(_write(tmp_path, "example-1.0-json.yaml", text))

    def test_select_with_a_wildcard_segment_is_rejected(self, tmp_path):
        text = TREE.replace("select: Items", "select: Items..Sub")

        with pytest.raises(MappingError, match="select"):
            load_mapping_file(_write(tmp_path, "example-1.0-json.yaml", text))

    def test_plain_http_release_is_rejected(self, tmp_path):
        text = TREE.replace("https://example.org", "http://example.org")

        with pytest.raises(MappingError, match="release"):
            load_mapping_file(_write(tmp_path, "example-1.0-json.yaml", text))

    def test_file_name_must_be_the_mapping_id(self, tmp_path):
        path = _write(tmp_path, "other.yaml", TREE)

        with pytest.raises(MappingError, match=r"example-1\.0-json\.yaml"):
            load_mapping_file(path)

    def test_top_level_that_is_not_a_mapping_is_rejected(self, tmp_path):
        with pytest.raises(MappingError, match="must be a YAML mapping"):
            load_mapping_file(_write(tmp_path, "example-1.0-json.yaml", "- a\n- b\n"))


class TestFromDocument:
    def test_round_trips_a_validated_document(self, tmp_path):
        original = load_mapping_file(_write(tmp_path, "example-1.0-json.yaml", TREE))

        rebuilt = Mapping.from_document(original.document, original.sha256, "job")

        assert rebuilt == original

    def test_revalidates_the_document(self):
        with pytest.raises(MappingError, match="job"):
            Mapping.from_document({"mapping_version": 1}, "0" * 64, "job")


class TestRegistry:
    def test_packaged_mappings_are_registered(self):
        registry = load_registry("")

        assert registry.ids() == PACKAGED_IDS
        assert registry.get("owasp-asvs-5.0.0-json").format == "json"

    def test_unknown_id_is_none(self):
        assert load_registry("").get("nope") is None

    def test_operator_directory_adds_mappings(self, tmp_path):
        _write(tmp_path, "example-1.0-json.yaml", TREE)

        registry = load_registry(str(tmp_path))

        assert registry.ids() == sorted([*PACKAGED_IDS, "example-1.0-json"])

    def test_operator_directory_ignores_other_files(self, tmp_path):
        _write(tmp_path, "README.md", "not a mapping")

        assert load_registry(str(tmp_path)).ids() == PACKAGED_IDS

    def test_missing_operator_directory_fails(self, tmp_path):
        with pytest.raises(MappingError, match="MAPPINGS_PATH"):
            load_registry(str(tmp_path / "absent"))

    def test_invalid_operator_mapping_fails_the_whole_registry(self, tmp_path):
        _write(tmp_path, "example-1.0-json.yaml", "id: [\n")

        with pytest.raises(MappingError, match=r"example-1\.0-json\.yaml"):
            load_registry(str(tmp_path))

    def test_an_id_registered_twice_fails(self, tmp_path):
        shutil.copy(PACKAGED / "owasp-asvs-5.0.0-json.yaml", tmp_path / "owasp-asvs-5.0.0-json.yaml")

        with pytest.raises(MappingError, match=r"owasp-asvs-5\.0\.0-json.*registered twice"):
            load_registry(str(tmp_path))

    def test_empty_registry(self):
        registry = MappingRegistry({})

        assert registry.ids() == []
        assert registry.get("owasp-asvs-5.0.0-json") is None
