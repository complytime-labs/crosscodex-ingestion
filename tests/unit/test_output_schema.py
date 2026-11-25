"""Unit tests for the structured output schema and its validation."""

import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator

from crosscodex_ingestion.output_schema import SCHEMA_VERSION, OutputSchemaError, validate_output
from crosscodex_ingestion.quality import measure_structure
from tests.unit.output_samples import conforming_output, structured_output


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


class TestStructuredSourceFields:
    """Schema 1.1 adds source ids, titles, properties and metadata.source, all optional."""

    def test_this_service_emits_1_3(self):
        assert SCHEMA_VERSION == "1.3"

    def test_structured_output_passes(self):
        validate_output(structured_output())

    def test_an_output_without_the_new_fields_still_passes(self):
        output = conforming_output()
        assert "id" not in output["elements"][0]

        validate_output(output)

    def test_heading_without_an_id_cannot_carry_a_title(self):
        output = structured_output()
        output["elements"][0]["id"] = None
        output["elements"][0]["title"] = "Password Security"

        assert _violation(output).violations[0].element_index == 0

    @pytest.mark.parametrize("blank", ["", "   "])
    def test_blank_source_id_is_rejected(self, blank):
        output = structured_output()
        output["elements"][2]["id"] = blank

        assert _violation(output).violations[0].location.startswith("elements[2]")

    def test_property_values_must_be_strings(self):
        output = structured_output()
        output["elements"][2]["properties"]["level"] = 1

        assert _violation(output).violations[0].element_index == 2

    def test_property_names_must_be_identifiers(self):
        output = structured_output()
        output["elements"][2]["properties"] = {"Level 1": "x"}

        assert _violation(output).violations[0].element_index == 2

    def test_metadata_source_kind_must_be_structured(self):
        output = structured_output()
        output["metadata"]["source"]["kind"] = "document"

        assert _violation(output).violations[0].location.startswith("metadata.source")

    def test_mapping_sha256_must_be_a_hex_digest(self):
        output = structured_output()
        output["metadata"]["source"]["mapping"]["sha256"] = "not-a-digest"

        assert _violation(output).violations[0].location == "metadata.source.mapping.sha256"

    def test_duplicate_source_id_is_rejected_without_quoting_it(self):
        output = structured_output()
        output["elements"][2]["id"] = "V6.2"

        error = _violation(output)

        assert error.violations[0].element_index == 2
        assert error.violations[0].location == "elements[2] (element_id 'e-003').id"
        assert "elements[0]" in error.violations[0].reason
        assert "V6.2" not in str(error)

    def test_null_source_ids_may_repeat(self):
        output = structured_output()
        output["elements"][2]["id"] = None
        for field in ("role", "text"):
            del output["elements"][2][field]

        validate_output(output)


class TestElementRoles:
    """Schema 1.3 adds role, text and a nullable title on elements that have a source id."""

    def test_1_3_structured_output_validates(self):
        output = structured_output()
        assert output["schema_version"] == "1.3"
        assert {e.get("role") for e in output["elements"] if e.get("id")} == {"control"}

        validate_output(output)

    def test_role_outside_enum_is_rejected(self):
        output = structured_output()
        output["elements"][2]["role"] = "part"

        assert _violation(output).violations[0].element_index == 2

    @pytest.mark.parametrize(("field", "value"), [("role", "group"), ("text", "Some text.")])
    @pytest.mark.parametrize("id_state", ["absent", "null"])
    def test_role_and_text_need_a_string_id(self, field, value, id_state):
        output = structured_output()
        leaf = output["elements"][1]  # the sample's paragraph with id None
        if id_state == "absent":
            del leaf["id"]
        leaf[field] = value

        assert _violation(output).violations[0].element_index == 1

    def test_heading_with_id_may_have_a_title(self):
        output = structured_output()
        output["elements"][0]["title"] = "Password Security"

        validate_output(output)

    def test_heading_without_id_and_with_title_is_rejected(self):
        output = structured_output()
        del output["elements"][0]["id"]
        output["elements"][0]["title"] = "Password Security"

        assert _violation(output).violations[0].element_index == 0

    @pytest.mark.parametrize("field", ["title", "text"])
    def test_title_and_text_may_be_null(self, field):
        output = structured_output()
        output["elements"][2][field] = None

        validate_output(output)

    @pytest.mark.parametrize("field", ["title", "text"])
    @pytest.mark.parametrize("blank", ["", "  "])
    def test_title_and_text_may_not_be_blank(self, field, blank):
        output = structured_output()
        output["elements"][2][field] = blank

        assert _violation(output).violations[0].element_index == 2

    def test_1_2_output_with_ids_and_no_role_validates(self):
        output = structured_output()
        output["schema_version"] = "1.2"
        for element in output["elements"]:
            for field in ("role", "text", "title"):
                element.pop(field, None)

        validate_output(output)

    @pytest.mark.parametrize("version", ["5.0.0", None])
    def test_catalog_version_is_nullable(self, version):
        output = structured_output()
        output["metadata"]["source"]["catalog_version"] = version

        validate_output(output)

    @pytest.mark.parametrize("blank", ["", "  "])
    def test_blank_catalog_version_is_rejected(self, blank):
        output = structured_output()
        output["metadata"]["source"]["catalog_version"] = blank

        assert _violation(output).violations[0].location.startswith("metadata.source")


class TestStructureFields:
    """Schema 1.2 adds optional metadata.structure and metadata.html_profile."""

    def test_structure_and_html_profile_validate(self):
        output = conforming_output()
        output["metadata"]["structure"] = measure_structure(output["elements"])
        output["metadata"]["html_profile"] = {"id": "eur-lex-oj", "sha256": "0" * 64}

        validate_output(output)

        assert output["schema_version"] == "1.3"

    @pytest.mark.parametrize("version", ["1.0", "1.1", "1.2"])
    def test_older_versions_still_validate(self, version):
        output = conforming_output()
        output["schema_version"] = version

        validate_output(output)

    @pytest.mark.parametrize(
        "mutate",
        [
            lambda m: m["structure"].update(heading_count=-1),
            lambda m: m["structure"].update(warnings=[3]),
            lambda m: m["structure"].update(warnings=["No-Headings"]),
            lambda m: m["structure"].update(ratio=0.5),
            lambda m: m["structure"].pop("warnings"),
            lambda m: m["html_profile"].pop("sha256"),
        ],
        ids=["negative_count", "non_string_warning", "bad_warning_code", "extra_key", "missing_warnings", "no_sha256"],
    )
    def test_invalid_shapes_are_rejected(self, mutate):
        output = conforming_output()
        output["metadata"]["structure"] = measure_structure(output["elements"])
        output["metadata"]["html_profile"] = {"id": "eur-lex-oj", "sha256": "0" * 64}
        mutate(output["metadata"])

        _violation(output)
