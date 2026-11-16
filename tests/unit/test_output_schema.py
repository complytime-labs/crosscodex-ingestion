"""Unit tests for the structured output schema and its validation."""

import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator

from crosscodex_ingestion.output_schema import SCHEMA_VERSION, OutputSchemaError, validate_output
from tests.unit.output_samples import conforming_output


def _violation(output: dict) -> OutputSchemaError:
    with pytest.raises(OutputSchemaError) as excinfo:
        validate_output(output)
    return excinfo.value


class TestShippedSchema:
    def test_schema_is_package_data_and_a_valid_draft_2020_12_schema(self):
        schema = json.loads(files("crosscodex_ingestion").joinpath("schemas/structured-output-v1.json").read_text())

        Draft202012Validator.check_schema(schema)
        assert SCHEMA_VERSION in schema["properties"]["schema_version"]["enum"]

    def test_schema_version_is_major_dot_minor(self):
        major, minor = SCHEMA_VERSION.split(".")
        assert major == "1"
        assert minor.isdigit()

    def test_the_v1_file_accepts_every_released_1x_version(self):
        """One file per major: outputs stored under an earlier 1.x keep validating after a minor release."""
        schema = json.loads(files("crosscodex_ingestion").joinpath("schemas/structured-output-v1.json").read_text())
        released = schema["properties"]["schema_version"]["enum"]

        assert all(v.split(".")[0] == "1" and v.split(".")[1].isdigit() for v in released)
        for version in released:
            output = conforming_output()
            output["schema_version"] = version
            validate_output(output)


class TestConformingOutput:
    def test_conforming_output_passes(self):
        validate_output(conforming_output())

    def test_every_element_type_and_dropped_entry_passes(self):
        output = conforming_output()
        output["elements"] += [
            {
                "element_id": "e-003",
                "type": "list_item",
                "content": "Item",
                "page": None,
                "level": None,
                "parent_id": "e-001",
                "style": {},
            },
            {
                "element_id": "e-004",
                "type": "table",
                "content": "| a |\n|---|\n| 1 |",
                "page": 2,
                "level": None,
                "parent_id": None,
                "style": {},
            },
            {
                "element_id": "e-005",
                "type": "heading",
                "content": "Deep",
                "page": 2,
                "level": 100,
                "parent_id": "e-001",
                "style": {},
            },
        ]
        output["metadata"]["element_count"] = 5
        output["metadata"]["dropped"] = [{"type": "table", "page": 3, "reason": "table export failed (ValueError)"}]

        validate_output(output)

    def test_empty_document_passes(self):
        output = conforming_output()
        output["elements"] = []
        output["metadata"].update(element_count=0, page_count=None)

        validate_output(output)


class TestSchemaViolations:
    @pytest.mark.parametrize("blank", ["", "   ", "\n\t"])
    def test_blank_heading_is_rejected_and_named(self, blank):
        output = conforming_output()
        output["elements"][0]["content"] = blank

        error = _violation(output)

        assert error.violations[0].element_index == 0
        assert error.violations[0].element_id == "e-001"
        assert "elements[0] (element_id 'e-001').content" in str(error)

    @pytest.mark.parametrize("level", [0, 101, None, "1"])
    def test_heading_level_outside_1_to_100_is_rejected(self, level):
        output = conforming_output()
        output["elements"][0]["level"] = level

        error = _violation(output)

        assert error.violations[0].element_id == "e-001"
        assert ".level" in str(error)

    def test_level_on_a_non_heading_is_rejected(self):
        output = conforming_output()
        output["elements"][1]["level"] = 2

        error = _violation(output)

        assert error.violations[0].element_id == "e-002"
        assert ".level" in str(error)

    def test_unknown_element_type_is_rejected(self):
        output = conforming_output()
        output["elements"][1]["type"] = "text"

        assert _violation(output).violations[0].element_index == 1

    def test_missing_field_is_rejected(self):
        output = conforming_output()
        del output["elements"][1]["parent_id"]

        assert "'parent_id' is a required property" in str(_violation(output))

    def test_unknown_field_is_rejected_by_the_producer_schema(self):
        """The service's own output must match the schema exactly; consumers are the ones who ignore extras."""
        output = conforming_output()
        output["elements"][0]["colour"] = "red"

        assert "unexpected property 'colour'" in str(_violation(output))

    def test_wrong_schema_version_is_rejected(self):
        output = conforming_output()
        output["schema_version"] = "0.9"

        error = _violation(output)

        assert error.violations[0].element_index is None
        assert "schema_version" in str(error)

    def test_dropped_entry_needs_a_reason(self):
        output = conforming_output()
        output["metadata"]["dropped"] = [{"type": "table", "page": 3, "reason": " "}]

        assert "metadata.dropped[0].reason" in str(_violation(output))

    def test_message_does_not_echo_document_text(self):
        """Element content is tenant data; it must not end up in errors, logs or audit events."""
        output = conforming_output()
        output["elements"][1]["content"] = {"secret": "Confidential paragraph"}

        assert "Confidential paragraph" not in str(_violation(output))

    @pytest.mark.parametrize("hostile", ["Confidential paragraph", "e-001\n", "e-" + "1" * 40, ""])
    def test_element_id_outside_the_generated_form_is_rejected_and_not_quoted(self, hostile):
        output = conforming_output()
        output["elements"][1]["element_id"] = hostile

        error = _violation(output)

        assert error.violations[0].element_index == 1
        assert error.violations[0].element_id is None
        assert "elements[1].element_id" in str(error)
        if hostile:
            assert hostile not in str(error)

    def test_parent_id_outside_the_generated_form_is_rejected_and_not_quoted(self):
        output = conforming_output()
        output["elements"][1]["parent_id"] = "Confidential parent"

        error = _violation(output)

        assert "elements[1] (element_id 'e-002').parent_id" in str(error)
        assert "Confidential" not in str(error)

    def test_unexpected_property_with_a_non_identifier_name_is_not_quoted(self):
        output = conforming_output()
        output["elements"][0]["Confidential Key!"] = 1

        error = _violation(output)

        assert "unexpected property" in str(error)
        assert "Confidential" not in str(error)

    def test_every_violation_is_collected_in_document_order(self):
        output = conforming_output()
        output["elements"][1]["content"] = ""
        output["elements"][0]["content"] = ""

        error = _violation(output)

        assert [v.element_index for v in error.violations] == [0, 1]
        assert "(and 1 more)" in str(error)


class TestReferenceViolations:
    """JSON Schema can't relate one element to another, so validate_output checks references itself."""

    def test_dangling_parent_id_is_rejected(self):
        output = conforming_output()
        output["elements"][1]["parent_id"] = "e-999"

        error = _violation(output)

        assert error.violations[0].element_index == 1
        assert error.violations[0].element_id == "e-002"
        assert "'e-999' is not the element_id of an earlier element" in str(error)

    def test_parent_id_naming_a_later_element_is_rejected(self):
        output = conforming_output()
        output["elements"][0]["parent_id"] = "e-002"

        assert _violation(output).violations[0].element_index == 0

    def test_element_cannot_be_its_own_parent(self):
        output = conforming_output()
        output["elements"][1]["parent_id"] = "e-002"

        assert _violation(output).violations[0].element_index == 1

    def test_duplicate_element_id_is_rejected(self):
        output = conforming_output()
        output["elements"][1]["element_id"] = "e-001"
        output["elements"][1]["parent_id"] = None

        error = _violation(output)

        assert error.violations[0].element_index == 1
        assert "duplicates" in str(error)

    @pytest.mark.parametrize("count", [0, 3])
    def test_element_count_must_equal_the_number_of_elements(self, count):
        output = conforming_output()
        output["metadata"]["element_count"] = count

        error = _violation(output)

        assert error.violations[0].location == "metadata.element_count"
        assert error.violations[0].element_index is None
        assert "2" in error.violations[0].reason
