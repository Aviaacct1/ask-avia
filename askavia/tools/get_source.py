"""Tool 4: get_source.

What does the document actually say, and where. Returns the verbatim extract the store
retained, the Egnyte path plus the in-file cell locator, and, crucially, the skip
disclosure for the scope: "N of M in-scope documents skipped". That disclosure is a
design requirement, not a nicety, because a confident answer drawn from the 74% that
parsed, with no word about the 26% that did not, is the failure mode this whole service
exists to prevent.

Read-only. Audit-logged. Refuses (never silently filters) any record whose source sits
in the quarantined Benchmark folder.
"""

from __future__ import annotations

from typing import Any

from .. import config as cfg
from ..store import Store
from ..audit import AuditLog
from ..errors import QuarantineError


def _folder_of(path: str) -> str:
    """The containing folder of a source path, used as the default skip-disclosure scope."""
    p = (path or "").replace("\\", "/")
    return p.rsplit("/", 1)[0] if "/" in p else ""


def run(
    store: Store,
    audit: AuditLog,
    *,
    user: str,
    record_id: str,
    scope: str | None = None,
) -> dict[str, Any]:
    rec = store.get_point(record_id)

    if rec is None:
        audit.record(user=user, tool="get_source",
                     filters={"record_id": record_id, "scope": scope},
                     record_ids=[], outcome="no_evidence")
        return {
            "tool": "get_source",
            "outcome": "no_evidence",
            "note": f"no evidence held for record_id {record_id!r}",
            "provenance": store.bound.provenance_note(),
        }

    source_path = rec.get("source", "") or ""
    # Defence in depth: the pipeline should never load the quarantined Benchmark folder,
    # but if a record's source is inside it, refuse loudly rather than return its content.
    if cfg.is_excluded(source_path):
        audit.record(user=user, tool="get_source",
                     filters={"record_id": record_id, "scope": scope},
                     record_ids=[record_id], outcome="refused_quarantine")
        raise QuarantineError(source_path)

    disclosure_scope = scope if scope is not None else _folder_of(source_path)
    skip = store.skip_disclosure(disclosure_scope)

    audit.record(user=user, tool="get_source",
                 filters={"record_id": record_id, "scope": disclosure_scope},
                 record_ids=[record_id], outcome="ok", skipped=skip)

    return {
        "tool": "get_source",
        "outcome": "ok",
        "record": rec.as_dict(),
        "verbatim": rec.get("extract_text"),
        "locator": {
            "egnyte_path": source_path or None,
            "cell": rec.get("cell"),
        },
        "skip_disclosure": skip,
        "citation": rec.citation(),
        "provenance": store.bound.provenance_note(),
    }
