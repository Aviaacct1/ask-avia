"""Tool 1: search_datapoints.

A structured query over the store. The one behaviour that makes it trustworthy rather
than merely convenient: it ECHOES the filters it actually applied, and names any filter
it could not apply because the store does not carry that concept. So the model can say
"Understood as ..." and the user can correct it in plain language, and a filter that
silently did nothing can never masquerade as a filter that found nothing.

Read-only. Audit-logged. Every returned record carries value, unit, year, class,
verification status and source; nothing is returned as a bare number.
"""

from __future__ import annotations

from typing import Any

from ..store import Store
from ..audit import AuditLog


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
    limit: int = 200,
) -> dict[str, Any]:
    records, echoed = store.search(
        metric=metric, entity=entity, geography=geography,
        year_from=year_from, year_to=year_to,
        data_class=data_class, status=status, limit=limit,
    )
    ids = [r.get("record_id") for r in records if r.get("record_id")]

    filters = {
        "metric": metric, "entity": entity, "geography": geography,
        "year_from": year_from, "year_to": year_to,
        "data_class": data_class, "status": status, "limit": limit,
    }
    audit.record(user=user, tool="search_datapoints",
                 filters=filters, record_ids=ids,
                 outcome="ok" if records else "no_evidence")

    result: dict[str, Any] = {
        "tool": "search_datapoints",
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
    return result
