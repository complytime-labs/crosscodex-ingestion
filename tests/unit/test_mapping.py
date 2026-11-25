"""Tests for mapping.py — loading, validating and registering structured-source mappings."""

import hashlib
import shutil
from importlib.resources import files
from pathlib import Path

import pytest

from crosscodex_ingestion.mapping import Mapping, MappingError, MappingRegistry, load_mapping_file, load_registry

PACKAGED = Path(str(files("crosscodex_ingestion").joinpath("mappings")))
PACKAGED_IDS = [
    "mitre-attack-enterprise-17.1-techniques-xlsx",
    "nist-csf-2.0-core-xlsx",
    "nist-sp800-53r5-catalog-xlsx",
    "owasp-asvs-5.0.0-csv",
    "owasp-asvs-5.0.0-json",
]

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

    def test_group_levels_and_version_load(self, tmp_path):
        text = TREE + 'group_levels: 2\nversion: "5.0.0"\n'

        mapping = load_mapping_file(_write(tmp_path, "example-1.0-json.yaml", text))

        assert mapping.document["group_levels"] == 2
        assert mapping.document["version"] == "5.0.0"

    @pytest.mark.parametrize("value", ["0", "-1", '"2"', "1.5", "101"])
    def test_group_levels_outside_one_to_one_hundred_or_not_an_integer_is_rejected(self, tmp_path, value):
        path = _write(tmp_path, "example-1.0-json.yaml", TREE + f"group_levels: {value}\n")

        with pytest.raises(MappingError, match="group_levels"):
            load_mapping_file(path)

    @pytest.mark.parametrize("value", ['""', '"  "'])
    def test_blank_version_is_rejected(self, tmp_path, value):
        path = _write(tmp_path, "example-1.0-json.yaml", TREE + f"version: {value}\n")

        with pytest.raises(MappingError, match="version"):
            load_mapping_file(path)

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

    def test_packaged_mappings_declare_versions(self):
        registry = load_registry("")
        documents = {i: registry.get(i).document for i in registry.ids()}

        assert {i: d.get("version") for i, d in documents.items()} == {
            "owasp-asvs-5.0.0-json": "5.0.0",
            "owasp-asvs-5.0.0-csv": "5.0.0",
            "nist-csf-2.0-core-xlsx": "2.0",
            "nist-sp800-53r5-catalog-xlsx": "Revision 5",
            "mitre-attack-enterprise-17.1-techniques-xlsx": "17.1",
        }
        assert {i: d.get("group_levels") for i, d in documents.items()} == {
            "owasp-asvs-5.0.0-json": 2,
            "owasp-asvs-5.0.0-csv": 2,
            "nist-csf-2.0-core-xlsx": 2,
            "nist-sp800-53r5-catalog-xlsx": None,
            "mitre-attack-enterprise-17.1-techniques-xlsx": None,
        }

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


LEVELS = TABLE.replace(
    'rows: {id: ID, text: Text, parent: {id_prefix: {separator: "("}}}',
    "rows: {levels: [{id: Chap, title: ChapName}], id: ID, text: Text}",
)


class TestLevels:
    def test_levels_are_accepted_and_kept(self, tmp_path):
        mapping = load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", LEVELS))

        assert mapping.document["rows"]["levels"] == [{"id": "Chap", "title": "ChapName"}]

    @pytest.mark.parametrize(
        "levels",
        ["[]", "[{id: Chap}]", "[{title: ChapName}]", "[{id: Chap, title: N, text: X}]", "[Chap]"],
    )
    def test_malformed_levels_are_rejected(self, tmp_path, levels):
        text = LEVELS.replace("[{id: Chap, title: ChapName}]", levels)

        with pytest.raises(MappingError, match="levels"):
            load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", text))

    def test_levels_with_a_parent_rule_are_rejected(self, tmp_path):
        text = LEVELS.replace("text: Text}", "text: Text, parent: {column: P}}")

        with pytest.raises(MappingError):
            load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", text))


CELLS = TABLE.replace(
    'rows: {id: ID, text: Text, parent: {id_prefix: {separator: "("}}}',
    'rows: {level_cells: {columns: [F, C, S], separator: ":"}}',
)


class TestLevelCells:
    def test_level_cells_are_accepted(self, tmp_path):
        mapping = load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", CELLS))

        assert mapping.document["rows"]["level_cells"]["columns"] == ["F", "C", "S"]

    def test_brackets_are_accepted(self, tmp_path):
        text = CELLS.replace('separator: ":"}', 'separator: ":", id_brackets: {open: "(", close: ")"}}')

        load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", text))

    @pytest.mark.parametrize(
        "change",
        [
            ("columns: [F, C, S]", "columns: []"),
            ("columns: [F, C, S]", "columns: [F, F]"),
            ('separator: ":"', 'separator: ""'),
            ('separator: ":"}', 'separator: ":", id_brackets: {open: "("}}'),
            ('separator: ":"}', 'separator: ":", pattern: x}'),
            ("level_cells: {", "id: ID, level_cells: {"),
            ("level_cells: {", "levels: [{id: A, title: B}], level_cells: {"),
            ("level_cells: {", "parent: {column: P}, level_cells: {"),
        ],
    )
    def test_malformed_level_cells_are_rejected(self, tmp_path, change):
        with pytest.raises(MappingError):
            load_mapping_file(_write(tmp_path, "example-1.0-csv.yaml", CELLS.replace(*change)))
