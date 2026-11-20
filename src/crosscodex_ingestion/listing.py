"""ListDocuments query semantics: filters, sort and keyset pagination.

Pagination is keyset-based: a page token carries the sort-key values of the last record served, not
an offset. Under the default sort (created_at, then document_id) both keys are immutable, so records
created or changed between pages never cause duplicates or skips. Sorting on updated_at or status is
only as stable as those fields: a record whose value changes between pages can repeat (ascending)
or be skipped (descending). The token is bound to a hash of the query's filters and sort, so reusing
it with a different query is rejected instead of returning wrong pages.
"""

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cmp_to_key
from typing import TYPE_CHECKING

from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2

if TYPE_CHECKING:
    from crosscodex_ingestion.state import DocumentRecord

DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 1000
TOKEN_VERSION = 1
BAD_CURSOR_MESSAGE = "page_token is invalid or belongs to a different query"

# Records with an empty timestamp sort before every real one and never match a time filter.
_EMPTY_TIME = -(2**63)

_TIME_FIELDS = ("created_at", "updated_at")
_FILTER_OPERATORS = {
    "status": ("eq", "in"),
    "created_at": ("eq", "gt", "lt"),
    "updated_at": ("eq", "gt", "lt"),
}
_SORT_FIELDS = ("created_at", "updated_at", "status")
_SUPPORTED = (
    "Supported filters: status (eq, in; value is a JobStatus name such as JOB_STATUS_FAILED), "
    "created_at and updated_at (eq, gt, lt; value is RFC 3339 with a timezone). "
    "Supported sort fields: created_at, updated_at, status."
)
_STATUS_NAMES = ", ".join(sorted(common_pb2.JobStatus.keys()))


class ListQueryError(ValueError):
    """The list options are unsupported or malformed. The message is safe to return to clients."""


@dataclass(frozen=True)
class Filter:
    """A parsed filter. value is a tuple of enum values for status, microseconds since epoch for times."""

    field: str
    operator: str
    value: int | tuple[int, ...]


@dataclass(frozen=True)
class SortKey:
    field: str
    descending: bool = False


@dataclass(frozen=True)
class ListQuery:
    """A validated query. after holds the last served record's sort values plus document_id, if paging."""

    page_size: int
    filters: tuple[Filter, ...]
    sort: tuple[SortKey, ...]
    after: tuple | None = None


def _micros(moment: datetime) -> int:
    delta = moment - datetime(1970, 1, 1, tzinfo=UTC)
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def _parse_time(value: str) -> int:
    """RFC 3339 timestamp with a timezone to microseconds since epoch."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ListQueryError(f"invalid timestamp {value!r}: expected RFC 3339 with a timezone") from None
    if parsed.tzinfo is None:
        raise ListQueryError(f"invalid timestamp {value!r}: expected RFC 3339 with a timezone")
    return _micros(parsed)


def _record_time(value: str) -> int:
    """Stored ISO string to microseconds since epoch; naive values are taken as UTC."""
    if not value:
        return _EMPTY_TIME
    parsed = datetime.fromisoformat(value)
    return _micros(parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))


def _parse_status(name: str) -> int:
    try:
        return common_pb2.JobStatus.Value(name)
    except ValueError:
        raise ListQueryError(f"unknown status {name!r}: expected one of {_STATUS_NAMES}") from None


def _parse_filter(raw: common_pb2.FieldFilter) -> Filter:
    operators = _FILTER_OPERATORS.get(raw.field)
    if operators is None:
        raise ListQueryError(f"unsupported filter field {raw.field!r}. {_SUPPORTED}")
    if raw.operator not in operators:
        raise ListQueryError(
            f"unsupported operator {raw.operator!r} for field {raw.field!r}: expected one of {', '.join(operators)}"
        )
    if raw.field in _TIME_FIELDS:
        return Filter(raw.field, raw.operator, _parse_time(raw.value))
    names = raw.value.split(",") if raw.operator == "in" else [raw.value]
    return Filter(raw.field, raw.operator, tuple(sorted({_parse_status(n.strip()) for n in names})))


def _parse_sort(raw: common_pb2.SortOrder) -> SortKey:
    if raw.field not in _SORT_FIELDS:
        raise ListQueryError(f"unsupported sort field {raw.field!r}. {_SUPPORTED}")
    return SortKey(raw.field, raw.descending)


def _query_hash(filters: tuple[Filter, ...], sort: tuple[SortKey, ...]) -> str:
    canonical = json.dumps(
        {
            "f": [[f.field, f.operator, list(f.value) if isinstance(f.value, tuple) else f.value] for f in filters],
            "s": [[k.field, k.descending] for k in sort],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _decode_token(token: str, filters: tuple[Filter, ...], sort: tuple[SortKey, ...]) -> tuple:
    """Return the token's key tuple, or raise ListQueryError if malformed or for another query."""
    invalid = ListQueryError(BAD_CURSOR_MESSAGE)
    try:
        payload = json.loads(base64.urlsafe_b64decode(token))
    except (binascii.Error, ValueError):
        raise invalid from None
    if not isinstance(payload, dict) or payload.get("v") != TOKEN_VERSION:
        raise invalid
    if payload.get("q") != _query_hash(filters, sort):
        raise invalid
    key = payload.get("k")
    if not isinstance(key, list) or len(key) != len(sort) + 1:
        raise invalid
    *values, document_id = key
    if not isinstance(document_id, str) or not all(type(v) is int for v in values):
        raise invalid
    return (*values, document_id)


def parse_list_options(options: common_pb2.ListOptions) -> ListQuery:
    """Validate options into a ListQuery. Raises ListQueryError on anything unsupported."""
    page_size = options.pagination.page_size
    if page_size < 0 or page_size > MAX_PAGE_SIZE:
        raise ListQueryError(f"page_size must be between 0 and {MAX_PAGE_SIZE} (0 means {DEFAULT_PAGE_SIZE})")
    filters = tuple(_parse_filter(f) for f in options.filters)
    sort = tuple(_parse_sort(s) for s in options.sort) or (SortKey("created_at"),)
    token = options.pagination.page_token
    after = _decode_token(token, filters, sort) if token else None
    return ListQuery(page_size or DEFAULT_PAGE_SIZE, filters, sort, after)


def _sort_value(record: "DocumentRecord", field: str) -> int:
    return record.status if field == "status" else _record_time(getattr(record, field))


def _key(record: "DocumentRecord", sort: tuple[SortKey, ...]) -> tuple:
    return (*(_sort_value(record, k.field) for k in sort), record.document_id)


def _compare(a: tuple, b: tuple, sort: tuple[SortKey, ...]) -> int:
    """Order two key tuples: sort keys in their direction, then document_id ascending."""
    for index, sort_key in enumerate(sort):
        if a[index] != b[index]:
            result = -1 if a[index] < b[index] else 1
            return -result if sort_key.descending else result
    if a[-1] == b[-1]:
        return 0
    return -1 if a[-1] < b[-1] else 1


def _matches(record: "DocumentRecord", query_filter: Filter) -> bool:
    target = query_filter.value
    if isinstance(target, tuple):
        return record.status in target
    value = _record_time(getattr(record, query_filter.field))
    if value == _EMPTY_TIME:
        return False
    if query_filter.operator == "eq":
        return value == target
    if query_filter.operator == "gt":
        return value > target
    return value < target


def apply_list_query(records: list["DocumentRecord"], query: ListQuery) -> tuple[list["DocumentRecord"], str]:
    """Filter, sort and cut one page. Returns (page, next_page_token); empty token means last page."""

    def by_key(a: tuple[tuple, "DocumentRecord"], b: tuple[tuple, "DocumentRecord"]) -> int:
        return _compare(a[0], b[0], query.sort)

    keyed: list[tuple[tuple, DocumentRecord]] = sorted(
        ((_key(r, query.sort), r) for r in records if all(_matches(r, f) for f in query.filters)),
        key=cmp_to_key(by_key),
    )
    if query.after is not None:
        keyed = [item for item in keyed if _compare(item[0], query.after, query.sort) > 0]

    page = keyed[: query.page_size]
    if len(keyed) <= query.page_size:
        return [r for _, r in page], ""

    payload = {"v": TOKEN_VERSION, "q": _query_hash(query.filters, query.sort), "k": list(page[-1][0])}
    token = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()
    return [r for _, r in page], token
