"""Audit log. Every call, every time.

AIP Note 3: "Every call is audit-logged (user, tools, records returned)."
Note 2 assumption A8: the team tests unsupervised from 27 August to 16 September, and
the corrections queue plus this log capture what John would otherwise observe. That
makes the log an adoption instrument as much as a security one, which is why it records
the filters as the caller gave them and the record IDs actually returned, not a summary.

Two properties matter more than completeness:

  1. It records WHICH STORE answered. A two-week schedule snapshot annualised as though
     it were a full year, and a store double-loaded so it read twice the real figure,
     are the same fault: something standing in for something else without saying so.
     Stamp scope on the run.
  2. A failure to write the log never fails the call, but it is reported to stderr
     rather than swallowed. Losing the audit trail silently is worse than a noisy run.

Append-only JSON Lines. One file per UTC day. No message bodies, no document text: the
log records that a record was returned, never what it said.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()

# Argument names whose values must never reach the log, whatever a caller passes.
_REDACT = ("token", "auth", "secret", "password", "key", "credential")


def _scrub(value: Any) -> Any:
    """Redact by key name at any depth. Values are never guessed at, only replaced."""
    if isinstance(value, dict):
        return {
            k: ("[redacted]" if any(m in str(k).casefold() for m in _REDACT) else _scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(v) for v in value]
    return value


@dataclass(frozen=True)
class AuditContext:
    """Set once at startup, stamped on every entry."""

    store_kind: str
    store_path: str
    service_version: str
    hostname: str


class AuditLog:
    def __init__(self, directory: Path, context: AuditContext):
        self.directory = Path(directory)
        self.context = context
        self._degraded = False
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            probe = self.directory / ".write_probe"
            probe.write_text("", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            # Report, do not swallow, and do not pretend the log is working.
            self._degraded = True
            print(
                f"[audit] DEGRADED: cannot write to {self.directory}: {exc}. "
                f"Calls will still be served and every entry will be echoed to stderr. "
                f"Fix before the team uses this service, because the log is how "
                f"unsupervised use is reviewed.",
                file=sys.stderr,
            )

    def _path_for(self, when: datetime) -> Path:
        return self.directory / f"ask-avia-{when:%Y-%m-%d}.jsonl"

    def record(
        self,
        *,
        user: str,
        tool: str,
        filters: dict[str, Any] | None = None,
        record_ids: list[str] | None = None,
        outcome: str = "ok",
        detail: str = "",
        skipped: dict[str, Any] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        entry = {
            "ts": now.isoformat(timespec="milliseconds"),
            "user": user or "unknown",
            "tool": tool,
            "filters": _scrub(filters or {}),
            "record_ids": record_ids or [],
            "record_count": len(record_ids or []),
            "outcome": outcome,
            "detail": detail,
            "skipped": _scrub(skipped or {}),
            "store_kind": self.context.store_kind,
            "store_path": self.context.store_path,
            "service_version": self.context.service_version,
            "hostname": self.context.hostname,
            "pid": os.getpid(),
        }
        line = json.dumps(entry, ensure_ascii=False, default=str)

        if self._degraded:
            print(f"[audit] {line}", file=sys.stderr)
            return

        try:
            with _LOCK, self._path_for(now).open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError as exc:
            # A logging failure must not fail the caller's query, and must not be quiet.
            print(f"[audit] WRITE FAILED ({exc}). Entry follows: {line}", file=sys.stderr)
