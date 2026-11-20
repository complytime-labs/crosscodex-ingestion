import base64
import json
from datetime import UTC, datetime, timedelta

import pytest

from crosscodex_ingestion.listing import ListQueryError, apply_list_query, parse_list_options
from crosscodex_ingestion.proto.crosscodex.v1 import common_pb2
from crosscodex_ingestion.state import DocumentRecord

BASE = datetime(2026, 1, 1, tzinfo=UTC)
BAD_CURSOR_MESSAGE = "page_token is invalid or belongs to a different query"


def _rec(doc_id: str, minutes: int, status: int = common_pb2.JOB_STATUS_PENDING) -> DocumentRecord:
    ts = (BASE + timedelta(minutes=minutes)).isoformat()
    return DocumentRecord(document_id=doc_id, tenant_id="t", status=status, created_at=ts, updated_at=ts)


def _options(
    filters: list[tuple[str, str, str]] = (),
    sort: list[tuple[str, bool]] = (),
    page_size: int = 0,
    page_token: str = "",
) -> common_pb2.ListOptions:
    return common_pb2.ListOptions(
        pagination=common_pb2.Pagination(page_size=page_size, page_token=page_token),
        filters=[common_pb2.FieldFilter(field=f, operator=o, value=v) for f, o, v in filters],
        sort=[common_pb2.SortOrder(field=f, descending=d) for f, d in sort],
    )


def _ids(records: list[DocumentRecord]) -> list[str]:
    return [r.document_id for r in records]


def _run(records: list[DocumentRecord], **kwargs) -> tuple[list[DocumentRecord], str]:
    return apply_list_query(records, parse_list_options(_options(**kwargs)))


def _walk(records: list[DocumentRecord], **kwargs) -> list[str]:
    """Follow next_page_token until exhausted; return ids in the order served."""
    seen: list[str] = []
    token = ""
    while True:
        page, token = _run(records, page_token=token, **kwargs)
        seen.extend(_ids(page))
        if not token:
            return seen


class TestFilters:
    def test_status_eq_keeps_only_matching(self):
        records = [_rec("a", 0), _rec("b", 1, common_pb2.JOB_STATUS_FAILED), _rec("c", 2)]

        page, _ = _run(records, filters=[("status", "eq", "JOB_STATUS_FAILED")])

        assert _ids(page) == ["b"]

    def test_status_in_accepts_comma_separated_names(self):
        records = [
            _rec("a", 0),
            _rec("b", 1, common_pb2.JOB_STATUS_FAILED),
            _rec("c", 2, common_pb2.JOB_STATUS_COMPLETED),
        ]

        page, _ = _run(records, filters=[("status", "in", "JOB_STATUS_FAILED, JOB_STATUS_COMPLETED")])

        assert _ids(page) == ["b", "c"]

    def test_created_at_gt_and_lt_are_exclusive(self):
        records = [_rec("a", 0), _rec("b", 1), _rec("c", 2)]
        after = (BASE + timedelta(minutes=0)).isoformat()
        before = (BASE + timedelta(minutes=2)).isoformat()

        page, _ = _run(records, filters=[("created_at", "gt", after), ("created_at", "lt", before)])

        assert _ids(page) == ["b"]

    def test_created_at_eq_accepts_z_suffix(self):
        records = [_rec("a", 0), _rec("b", 1)]

        page, _ = _run(records, filters=[("created_at", "eq", "2026-01-01T00:01:00Z")])

        assert _ids(page) == ["b"]

    def test_filters_are_anded(self):
        records = [_rec("a", 0, common_pb2.JOB_STATUS_FAILED), _rec("b", 5, common_pb2.JOB_STATUS_FAILED), _rec("c", 5)]

        page, _ = _run(
            records,
            filters=[("status", "eq", "JOB_STATUS_FAILED"), ("updated_at", "gt", "2026-01-01T00:01:00+00:00")],
        )

        assert _ids(page) == ["b"]

    def test_empty_timestamp_never_matches_time_filters(self):
        records = [DocumentRecord(document_id="a", tenant_id="t", status=1), _rec("b", 1)]

        for op, value in (("gt", "2025-01-01T00:00:00Z"), ("lt", "2027-01-01T00:00:00Z")):
            page, _ = _run(records, filters=[("created_at", op, value)])
            assert _ids(page) == ["b"]


class TestSortAndPaging:
    def test_default_order_is_created_at_ascending(self):
        records = [_rec("c", 2), _rec("a", 0), _rec("b", 1)]

        page, _ = _run(records)

        assert _ids(page) == ["a", "b", "c"]

    def test_gt_filter_with_created_at_descending_returns_newest_first(self):
        records = [_rec("a", 0), _rec("b", 1), _rec("c", 2), _rec("d", 3)]

        page, _ = _run(
            records,
            filters=[("created_at", "gt", "2026-01-01T00:00:00Z")],
            sort=[("created_at", True)],
        )

        assert _ids(page) == ["d", "c", "b"]

    def test_empty_timestamp_sorts_first_ascending(self):
        records = [_rec("b", 1), DocumentRecord(document_id="z", tenant_id="t", status=1)]

        page, _ = _run(records)

        assert _ids(page) == ["z", "b"]

    def test_status_sorts_by_enum_value_then_document_id(self):
        records = [
            _rec("a", 0, common_pb2.JOB_STATUS_FAILED),
            _rec("b", 1, common_pb2.JOB_STATUS_PENDING),
            _rec("c", 2, common_pb2.JOB_STATUS_PENDING),
        ]

        page, _ = _run(records, sort=[("status", False)])

        assert _ids(page) == ["b", "c", "a"]

    def test_document_id_tiebreak_stays_ascending_when_sort_is_descending(self):
        records = [_rec("b", 0), _rec("a", 0), _rec("c", 0)]

        page, _ = _run(records, sort=[("created_at", True)])

        assert _ids(page) == ["a", "b", "c"]

    def test_paging_returns_each_record_once(self):
        records = [_rec(f"d{i}", i // 2) for i in range(7)]

        assert _walk(records, page_size=2) == [f"d{i}" for i in range(7)]

    def test_paging_descending_returns_each_record_once(self):
        records = [_rec(f"d{i}", i // 2) for i in range(7)]

        seen = _walk(records, page_size=3, sort=[("status", True), ("created_at", True)])

        assert seen == ["d6", "d4", "d5", "d2", "d3", "d0", "d1"]

    def test_last_page_has_empty_token(self):
        page, token = _run([_rec("a", 0), _rec("b", 1)], page_size=2)

        assert _ids(page) == ["a", "b"]
        assert token == ""

    def test_newer_record_created_between_pages_does_not_duplicate_or_skip(self):
        records = [_rec(f"d{i}", i) for i in range(5)]
        query = parse_list_options(_options(page_size=2))
        page1, token = apply_list_query(records, query)

        records.append(_rec("newest", 100))
        records.append(_rec("older", -1))
        seen = _ids(page1)
        while token:
            page, token = apply_list_query(records, parse_list_options(_options(page_size=2, page_token=token)))
            seen.extend(_ids(page))

        assert len(seen) == len(set(seen))
        assert seen == ["d0", "d1", "d2", "d3", "d4", "newest"]

    def test_created_at_paging_ignores_status_and_updated_at_changes_between_pages(self):
        records = [_rec(f"d{i}", i) for i in range(5)]
        page1, token = _run(records, page_size=2)

        for record in records:
            record.status = common_pb2.JOB_STATUS_FAILED
            record.updated_at = (BASE + timedelta(days=1)).isoformat()
        seen = _ids(page1)
        while token:
            page, token = _run(records, page_size=2, page_token=token)
            seen.extend(_ids(page))

        assert seen == [f"d{i}" for i in range(5)]

    def test_page_size_may_change_between_pages(self):
        records = [_rec(f"d{i}", i) for i in range(5)]
        page1, token = _run(records, page_size=2)

        page2, _ = _run(records, page_size=10, page_token=token)

        assert _ids(page1) + _ids(page2) == [f"d{i}" for i in range(5)]

    def test_default_page_size_is_50(self):
        records = [_rec(f"d{i:03d}", i) for i in range(60)]

        page, token = _run(records)

        assert len(page) == 50
        assert token != ""

    def test_page_token_is_urlsafe_base64_json_with_version(self):
        _, token = _run([_rec("a", 0), _rec("b", 1)], page_size=1)

        payload = json.loads(base64.urlsafe_b64decode(token))

        assert payload["v"] == 1
        assert len(payload["q"]) == 64
        assert payload["k"][-1] == "a"


class TestErrors:
    @pytest.mark.parametrize(
        ("kwargs", "fragment"),
        [
            ({"filters": [("framework", "eq", "x")]}, "created_at"),
            ({"filters": [("status", "contains", "FAILED")]}, "eq"),
            ({"filters": [("status", "gt", "JOB_STATUS_FAILED")]}, "eq"),
            ({"filters": [("created_at", "in", "2026-01-01T00:00:00Z")]}, "gt"),
            ({"filters": [("status", "eq", "NOT_A_STATUS")]}, "JOB_STATUS_FAILED"),
            ({"filters": [("status", "in", "")]}, "JOB_STATUS_FAILED"),
            ({"filters": [("created_at", "gt", "yesterday")]}, "RFC 3339"),
            ({"filters": [("created_at", "gt", "2026-01-01T00:00:00")]}, "RFC 3339"),
            ({"sort": [("document_id", False)]}, "created_at"),
            ({"page_size": -2}, "page_size"),
            ({"page_size": 1001}, "page_size"),
        ],
    )
    def test_bad_options_raise_with_supported_list(self, kwargs, fragment):
        with pytest.raises(ListQueryError, match=fragment):
            parse_list_options(_options(**kwargs))

    def test_page_size_bounds_are_accepted(self):
        assert parse_list_options(_options(page_size=1000)).page_size == 1000
        assert parse_list_options(_options(page_size=0)).page_size == 50

    def test_garbage_token_is_rejected(self):
        with pytest.raises(ListQueryError, match=BAD_CURSOR_MESSAGE):
            parse_list_options(_options(page_token="garbage"))

    @pytest.mark.parametrize(
        "payload",
        [
            "[]",
            '{"v":2,"q":"x","k":[]}',
            '{"v":1,"q":"x"}',
            '{"v":1,"q":"x","k":"not-a-list"}',
        ],
    )
    def test_structurally_bad_token_is_rejected(self, payload):
        token = base64.urlsafe_b64encode(payload.encode()).decode()

        with pytest.raises(ListQueryError, match=BAD_CURSOR_MESSAGE):
            parse_list_options(_options(page_token=token))

    def test_token_with_wrong_key_shape_is_rejected(self):
        records = [_rec("a", 0), _rec("b", 1)]
        _, token = _run(records, page_size=1)
        payload = json.loads(base64.urlsafe_b64decode(token))
        payload["k"] = ["not-an-int", "a"]
        forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()

        with pytest.raises(ListQueryError, match=BAD_CURSOR_MESSAGE):
            parse_list_options(_options(page_token=forged))

    def test_token_reused_with_different_filters_is_rejected(self):
        records = [_rec("a", 0), _rec("b", 1), _rec("c", 2)]
        _, token = _run(records, page_size=1)

        with pytest.raises(ListQueryError, match=BAD_CURSOR_MESSAGE):
            parse_list_options(_options(page_token=token, filters=[("status", "eq", "JOB_STATUS_FAILED")]))

    def test_token_reused_with_different_sort_is_rejected(self):
        _, token = _run([_rec("a", 0), _rec("b", 1)], page_size=1)

        with pytest.raises(ListQueryError, match=BAD_CURSOR_MESSAGE):
            parse_list_options(_options(page_token=token, sort=[("status", False)]))
