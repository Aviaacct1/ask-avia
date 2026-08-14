"""Tool 1: search_datapoints.

A structured query over the store. The one behaviour that makes it trustworthy rather
than merely convenient: it ECHOES the filters it actually applied, and names any filter
it could not apply because the store does not carry that concept. So the model can say
"Understood as ..." and the user can correct it in plain language, and a filter that
silently did nothing can never masquerade as a filter that found nothing.

It has two modes. `summarise=True` returns grouped counts of what the store HOLDS for a
filter and no rows at all; the default returns cited records. The summary mode exists
because a caller that cannot answer from the records it got will otherwise re-query with
filter after filter, and every one of those is a full scan of 722m rows. Ask what is held
first, then aim one query at it.

Read-only. Audit-logged. Every returned record carries value, unit, year, class,
verification status and source; nothing is returned as a bare number.
"""

from __future__ import annotations

from typing import Any

from ..store import Store
from ..audit import AuditLog

# Deliberately small. The previous default of 200 filled the caller's context with
# near-identical rows and made every miss expensive. A caller that wants more can say so.
DEFAULT_LIMIT = 25


def run(
    store: Store,
    audit: AuditLog,
    *,
    user: str,
    metric: str | None = None,
    entity: str | None = None,
    geography: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    data_class: str | None = None,
    status: str | None = None,
    limit: int = DEFAULT_LIMIT,
    require_metric_code: bool = False,
    summarise: bool = False,
) -> dict[str, Any]:
    filters = {
        "metric": metric, "entity": entity, "geography": geography,
        "year_from": year_from, "year_to": year_to,
        "data_class": data_class, "status": status,
        "require_metric_code": require_metric_code, "summarise": summarise,
    }

    if summarise:
        summary, echoed = store.summarise(
            metric=metric, entity=entity, geography=geography,
            year_from=year_from, year_to=year_to,
            data_class=data_class, status=status,
            require_metric_code=require_metric_code,
        )
        audit.record(user=user, tool="search_datapoints", filters=filters,
                     record_ids=[],
                     outcome="ok" if summary.get("matching_points") else "no_evidence")
        result: dict[str, Any] = {
            "tool": "search_datapoints",
            "mode": "summary",
            "understood_as": echoed["understood_as"],
            "not_applicable": echoed["not_applicable"],
            "provenance": store.bound.provenance_note(),
        }
        result.update(summary)
        if not summary.get("matching_points"):
            result.setdefault("note", "no evidence held for these filters")
        if echoed["not_applicable"]:
            result["warning"] = (
                "these filters were NOT applied because the bound store does not carry "
                f"the concept: {echoed['not_applicable']}. The counts are unfiltered on "
                "them."
            )
        return result

    filters["limit"] = limit
    records, echoed = store.search(
        metric=metric, entity=entity, geography=geography,
        year_from=year_from, year_to=year_to,
        data_class=data_class, status=status, limit=limit,
        require_metric_code=require_metric_code,
    )
    ids = [r.get("record_id") for r in records if r.get("record_id")]

    audit.record(user=user, tool="search_datapoints",
                 filters=filters, record_ids=ids,
                 outcome="ok" if records else "no_evidence")

    result = {
        "tool": "search_datapoints",
        "mode": "records",
        "understood_as": echoed["understood_as"],
        "not_applicable": echoed["not_applicable"],
        "count": len(records),
        "records": [r.as_dict() for r in records],
        "provenance": store.bound.provenance_note(),
    }
    if not records:
        result["note"] = "no evidence held for these filters"
    if echoed["not_applicable"]:
        result["warning"] = (
            "these filters were NOT applied because the bound store does not carry the "
            f"concept: {echoed['not_applicable']}. The result is unfiltered on them."
        )
    # If the caller took the default and got a full page, say so, rather than letting a
    # truncated page read as the whole of what is held.
    if len(records) >= limit:
        result["truncated"] = (
            f"exactly {limit} records returned, which is the limit; more are held. "
            f"Re-run with summarise=true to see the shape of the scope before widening."
        )
    return result
