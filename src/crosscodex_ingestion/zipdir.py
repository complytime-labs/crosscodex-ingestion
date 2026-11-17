"""
Read a ZIP archive's central directory without zipfile, with a hard cap on the records read.

zipfile.ZipFile walks every record within the directory size its end record declares, whatever
entry count that record gives, so a 50 MB upload can make it build a million ZipInfo objects.
This reader stops after *max_entries* records. It runs in the service process (format sniffing)
and, before zipfile, in the structured worker. It only lists names; nothing is decompressed.
"""

import struct
from dataclasses import dataclass

_END_RECORD = b"PK\x05\x06"
_END_RECORD_SIZE = 22
_END_RECORD_MAX_OFFSET = _END_RECORD_SIZE + 65_535  # fixed record plus the longest comment
_ZIP64_LOCATOR = b"PK\x06\x07"
_RECORD = b"PK\x01\x02"
_RECORD_SIZE = 46
_UTF8_NAMES = 0x800  # general purpose flag bit 11
_UNICODE_PATH_EXTRA = 0x7075  # Info-ZIP Unicode Path extra field


def _has_extra(extra: bytes, tag: int) -> bool:
    """True if the extra-field block *extra* contains a field with *tag*."""
    cursor = 0
    while cursor + 4 <= len(extra):
        field_tag, length = struct.unpack_from("<HH", extra, cursor)
        if field_tag == tag:
            return True
        cursor += 4 + length
    return False


class ZipDirectoryError(ValueError):
    """The bytes are not a complete, readable ZIP archive; the message says what is missing."""


class ZipDirectoryTooLarge(ZipDirectoryError):
    """The directory has more than max_entries records."""

    def __init__(self, max_entries: int) -> None:
        self.max_entries = max_entries
        super().__init__(f"the archive has more than {max_entries} entries")


@dataclass(frozen=True)
class ZipDirectory:
    names: frozenset[str]
    entries: int


def read_zip_directory(content: bytes, max_entries: int) -> ZipDirectory:
    """List the entry names of the ZIP archive *content*, reading at most *max_entries* records."""
    end = content.rfind(_END_RECORD, max(0, len(content) - _END_RECORD_MAX_OFFSET))
    if end < 0 or len(content) < end + _END_RECORD_SIZE:
        raise ZipDirectoryError("no end of central directory record (truncated, or not a ZIP archive)")
    if content[max(0, end - 20) : end - 16] == _ZIP64_LOCATOR:
        raise ZipDirectoryError("ZIP64 archives are not supported")
    size, offset = struct.unpack_from("<II", content, end + 12)
    if offset + size > end:
        raise ZipDirectoryError("the central directory lies outside the file (truncated or corrupt)")
    # zipfile ignores the offset and reads the *size* bytes before the end record; reading anything
    # else would list different entries than zipfile (and openpyxl) see. Real workbooks and documents
    # have no data before the first local header, so the two always agree for them.
    if offset + size != end:
        raise ZipDirectoryError(
            "the central directory does not end where the end record starts (prepended or appended data, or corrupt)"
        )

    names: set[str] = set()
    entries = 0
    position, stop = offset, offset + size
    while position < stop:
        if entries == max_entries:
            raise ZipDirectoryTooLarge(max_entries)
        if position + _RECORD_SIZE > stop or content[position : position + 4] != _RECORD:
            raise ZipDirectoryError(f"malformed central directory record at byte {position}")
        flags = struct.unpack_from("<H", content, position + 8)[0]
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", content, position + 28)
        if position + _RECORD_SIZE + name_length > stop:
            raise ZipDirectoryError(f"malformed central directory record at byte {position}")
        raw_name = content[position + _RECORD_SIZE : position + _RECORD_SIZE + name_length]
        if b"\0" in raw_name:  # zipfile truncates the name there
            raise ZipDirectoryError(f"an entry name at byte {position} contains NUL")
        extra = content[position + _RECORD_SIZE + name_length : position + _RECORD_SIZE + name_length + extra_length]
        if _has_extra(extra, _UNICODE_PATH_EXTRA):  # zipfile renames the entry to the name inside
            raise ZipDirectoryError(f"the entry at byte {position} has an Info-ZIP Unicode Path field")
        try:
            names.add(raw_name.decode("utf-8" if flags & _UTF8_NAMES else "cp437"))
        except UnicodeDecodeError as exc:
            raise ZipDirectoryError(f"malformed central directory record at byte {position}") from exc
        position += _RECORD_SIZE + name_length + extra_length + comment_length
        entries += 1
    return ZipDirectory(frozenset(names), entries)
