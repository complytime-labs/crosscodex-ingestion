"""Tests for zipdir.py — reading a ZIP's central directory without zipfile, with a hard record cap."""

import io
import struct
import time
import zipfile

import pytest

from crosscodex_ingestion.zipdir import ZipDirectoryError, ZipDirectoryTooLarge, read_zip_directory


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _record(name: bytes, extra: bytes = b"", comment_length: int = 0) -> bytes:
    header = bytearray(b"PK\x01\x02" + b"\0" * 42)
    struct.pack_into("<HHH", header, 28, len(name), len(extra), comment_length)
    return bytes(header) + name + extra


def _archive(directory: bytes) -> bytes:
    """A local-header signature, *directory*, and an end record pointing at it."""
    return b"PK\x03\x04" + directory + b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 1, 1, len(directory), 4, 0)


def forged_directory(records: int) -> bytes:
    """*records* minimal central-directory records behind an end record that declares one entry."""
    directory = (b"PK\x01\x02" + b"\0" * 42) * records
    end = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 1, 1, len(directory), 4, 0)
    return b"PK\x03\x04" + directory + end


class TestReadZipDirectory:
    def test_lists_the_entry_names(self):
        directory = read_zip_directory(_zip({"xl/workbook.xml": b"<w/>", "a/b.txt": b"x"}), max_entries=10)

        assert directory.names == frozenset({"xl/workbook.xml", "a/b.txt"})
        assert directory.entries == 2

    def test_forged_directory_size_stops_at_the_record_cap_quickly(self):
        """zipfile walks every record in size_cd whatever the end record's count says; this must not."""
        data = forged_directory(500_000)

        started = time.monotonic()
        with pytest.raises(ZipDirectoryTooLarge) as exc_info:
            read_zip_directory(data, max_entries=10_000)

        assert time.monotonic() - started < 0.5
        assert exc_info.value.max_entries == 10_000

    def test_exactly_the_cap_is_accepted(self):
        data = _zip({f"f{n}": b"" for n in range(5)})

        assert read_zip_directory(data, max_entries=5).entries == 5
        with pytest.raises(ZipDirectoryTooLarge):
            read_zip_directory(data, max_entries=4)

    def test_truncated_archive_has_no_end_record(self):
        data = _zip({"xl/workbook.xml": b"<w/>" * 1000})

        with pytest.raises(ZipDirectoryError, match="no end of central directory record"):
            read_zip_directory(data[: len(data) // 2], max_entries=10)

    def test_directory_outside_the_file(self):
        data = bytearray(_zip({"xl/workbook.xml": b"<w/>"}))
        end = data.rfind(b"PK\x05\x06")
        data[end + 16 : end + 20] = struct.pack("<I", 10_000_000)

        with pytest.raises(ZipDirectoryError, match="central directory lies outside the file"):
            read_zip_directory(bytes(data), max_entries=10)

    def test_directory_must_end_at_the_end_record(self):
        """zipfile reads the size_cd bytes before the end record and ignores the offset; anything else differs."""
        real = _record(b"a-real-entry-with-a-longer-name.txt")
        padding = len(real) - len(_record(b"xl/workbook.xml"))
        decoy = _record(b"xl/workbook.xml", comment_length=padding) + b"\0" * padding
        assert len(decoy) == len(real)
        end = b"PK\x05\x06" + struct.pack("<HHHHIIH", 0, 0, 1, 1, len(real), 4, 0)

        with pytest.raises(ZipDirectoryError, match="does not end where the end record starts"):
            read_zip_directory(b"PK\x03\x04" + decoy + real + end, max_entries=10)

    def test_info_zip_unicode_path_extra_is_refused(self):
        """zipfile renames an entry to the name in a 0x7075 extra field; refuse rather than emulate."""
        unicode_path = b"\x01" + struct.pack("<I", 0) + b"xl/workbook.xml"
        extra = struct.pack("<HH", 0x7075, len(unicode_path)) + unicode_path

        with pytest.raises(ZipDirectoryError, match="Unicode Path"):
            read_zip_directory(_archive(_record(b"decoy.txt", extra=extra)), max_entries=10)

    def test_nul_in_a_name_is_refused(self):
        """zipfile truncates a name at NUL."""
        with pytest.raises(ZipDirectoryError, match="NUL"):
            read_zip_directory(_archive(_record(b"xl/workbook.xml\0junk")), max_entries=10)

    def test_other_extra_fields_are_fine(self):
        extra = struct.pack("<HH", 0x5455, 5) + b"\x01\0\0\0\0"  # extended timestamp

        assert read_zip_directory(_archive(_record(b"a.txt", extra=extra)), max_entries=10).names == {"a.txt"}

    def test_malformed_record(self):
        data = bytearray(_zip({"xl/workbook.xml": b"<w/>"}))
        data[data.find(b"PK\x01\x02") + 2] = 0x09

        with pytest.raises(ZipDirectoryError, match="malformed central directory record"):
            read_zip_directory(bytes(data), max_entries=10)

    def test_zip64_is_refused(self):
        data = _zip({"xl/workbook.xml": b"<w/>"})
        end = data.rfind(b"PK\x05\x06")

        with pytest.raises(ZipDirectoryError, match="ZIP64"):
            read_zip_directory(data[:end] + b"PK\x06\x07" + b"\0" * 16 + data[end:], max_entries=10)

    def test_unsupported_extract_version_does_not_matter_for_listing(self):
        data = bytearray(_zip({"xl/workbook.xml": b"<w/>"}))
        data[data.find(b"PK\x01\x02") + 6] = 0xFF

        assert "xl/workbook.xml" in read_zip_directory(bytes(data), max_entries=10).names
