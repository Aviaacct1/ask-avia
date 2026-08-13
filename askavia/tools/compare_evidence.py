"""Tool 3: compare_evidence.

Are these figures actually comparable? The alignment is CODE, never model judgement. Two
numbers are comparable only when they share one definition: same metric, unit, currency,
scale and basis. Where they differ in any of those, the correct output is the refusal to
combine them, with the components shown and the exact axis of difference named. A mean of
a sterling figure and a euro figure, or of a per-passenger rate and a total, is worse than
no answer, because it looks like an answer.

This tool never returns a blended number across incomparable records. When the records do
share a basis it reports a descriptive spread (min, median, max), never a mean presented as
the truth. Read-only, audit-logged.
"""

from __future__ import annotations

from statistics import median
from typing import Any

from ..store import Store
from ..audit import AuditLog

# The axes that define "one definition". A difference on any of these blocks combination.
HARD_AXES = ("metric_code", "unit", "currency", "value_scale", "basis")
# A difference here does not block comparison but must be disclosed.
SOFT_AXES = ("temporality", "entity", "year")

_AXIS_MESSAGE = {
    "metric_code": "different metrics: these measure different things and cannot be compared",
    "unit": "different units",
    "currency": "different currencies: must not be pooled without a stated FX basis and date",
    "value_scale": "different scales (e.g. units vs thousands vs millions)",
    "basis": "different or unstated basis (e.g. real vs nominal): cannot confirm like-for-like",
}


def _distinct(records, axis):
    return sorted({(r.get(axis) or "") for r in records})


def run(store: Store, audit: AuditLog, *, user: str, record_ids: list[str]) -> dict[str, Any]:
    fetched = {rid: store.get_point(rid) for rid in record_ids}
    found = {rid: r for rid, r in fetched.items() if r is not None}
    missing = [rid for rid, r in fetched.items() if r is None]

    components = [r.as_dict() | {"citation": r.citation()} for r in found.values()]

    if len(found) < 2:
        audit.record(user=user, tool="compare_evidence",
                     filters={"record_ids": record_ids}, record_ids=list(found),
                     outcome="insufficient")
        return {
            "tool": "compare_evidence",
            "outcome": "insufficient",
            "note": "need at least two held records to compare",
            "missing": missing,
            "components": components,
        }

    recs = list(found.values())
    differs_hard = {ax: _distinct(recs, ax) for ax in HARD_AXES if len(_distinct(recs, ax)) > 1}
    differs_soft = {ax: _distinct(recs, ax) for ax in SOFT_AXES if len(_distinct(recs, ax)) > 1}

    flags = [f"{ax}: {_AXIS_MESSAGE[ax]} ({', '.join(v or '(blank)' for v in vals)})"
             for ax, vals in differs_hard.items()]
    if "temporality" in differs_soft:
        flags.append("temporality: mixes actual and forecast; comparable only as a like-for-like "
                     "if that mix is intended")

    comparable = not differs_hard
    result: dict[str, Any] = {
        "tool": "compare_evidence",
        "verdict": "comparable" if comparable else "not_comparable",
        "aligned_on": {ax: _distinct(recs, ax)[0] for ax in HARD_AXES
                       if len(_distinct(recs, ax)) == 1},
        "differs_on": differs_hard,
        "flags": flags,
        "components": components,
        "missing": missing,
        "provenance": store.bound.provenance_note(),
    }

    if comparable:
        vals = []
        for r in recs:
            try:
                vals.append(float(r.get("value")))
            except (TypeError, ValueError):
                vals = []
                break
        if vals:
            # a spread, deliberately NOT a mean: the median is a robust midpoint and the
            # min/max show the range, so the reader sees dispersion rather than one number.
            result["spread"] = {"n": len(vals), "min": min(vals),
                                "median": round(median(vals), 4), "max": max(vals)}
    else:
        result["note"] = ("these records are not on one basis; the components are shown "
                          "and must not be averaged into a single figure")

    audit.record(user=user, tool="compare_evidence",
                 filters={"record_ids": record_ids},
                 record_ids=list(found),
                 outcome="ok" if comparable else "not_comparable")
    return result
