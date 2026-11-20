# Listing documents

`ListDocuments` returns a tenant's documents. Use `options.filters`, `options.sort` and
`options.pagination` to narrow, order and page the result.

## Filters

Filters are ANDed. Any other field or operator is rejected with `INVALID_ARGUMENT`.

| Field                    | Operators        | Value                                                                  |
| ------------------------ | ---------------- | ---------------------------------------------------------------------- |
| `status`                 | `eq`, `in`       | A `JobStatus` name, such as `JOB_STATUS_FAILED`. `in` takes a comma-separated list. |
| `created_at`, `updated_at` | `eq`, `gt`, `lt` | RFC 3339 with a timezone, such as `2026-01-01T00:00:00Z`.              |

A document with no recorded timestamp never matches a `created_at` or `updated_at` filter.

## Sort

Supported fields: `created_at`, `updated_at` and `status` (ordered by the `JobStatus` number). Set
`descending` for newest-first. Keys apply in order. `document_id` ascending is always the final
tiebreak. With no sort, results are ordered by `created_at` ascending. A document with no recorded
timestamp sorts first in ascending order.

## Pagination

`page_size` defaults to 50 (send 0 or omit it) and may be at most 1000. Pass the response's
`page_info.next_page_token` as `page_token` to get the next page. An empty `next_page_token` means
the last page.

- Paging by `created_at` (the default) is stable: documents created or changed while you page do not
  cause duplicates or skips. Sorting by `updated_at` or `status` is only as stable as those fields:
  a document whose value changes between pages can appear twice (ascending) or be missed (descending).
- The token is opaque. It is bound to the filters and sort of the query that produced it. Reusing it
  with different filters or sort fails with `INVALID_ARGUMENT`
  (`page_token is invalid or belongs to a different query`). You may change `page_size` between pages.
- `page_info.total_count` is not set.

## Example

Failed documents, newest first, two per page. `$G` is defined in the [README](../../README.md).

```bash
$G -d '{"tenant_context": {"tenant_id": "default"},
        "options": {
          "pagination": {"page_size": 2},
          "filters": [{"field": "status", "operator": "eq", "value": "JOB_STATUS_FAILED"},
                      {"field": "created_at", "operator": "gt", "value": "2026-01-01T00:00:00Z"}],
          "sort": [{"field": "created_at", "descending": true}]}}' \
  localhost:8080 crosscodex.v1.IngestionService/ListDocuments
```

For the next page, send the same `filters` and `sort` with
`"pagination": {"page_size": 2, "page_token": "<nextPageToken>"}`.
