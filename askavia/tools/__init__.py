"""The ask-avia tools. Each is a plain function over a bound Store plus an AuditLog, so
it is testable without a running server; the MCP server (a later step) will register
these same functions. Every tool is read-only, audit-logged, and returns cited records:
no bare number ever leaves here without its unit, year, class, status and source.

Built so far: search_datapoints, get_source. Not yet: compare_evidence, find_precedents,
build_workbook, file_to_project, get_fact_of_day.
"""

from . import search_datapoints, get_source

__all__ = ["search_datapoints", "get_source"]
