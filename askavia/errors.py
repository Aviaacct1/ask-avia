"""Errors that report.

234 swallowed exception handlers across 98 Meridian modules, and every capability lost
in one week failed inside one of them. The rule here: a fallback must report. Nothing in
this service catches an exception around a data load and continues quietly.

These types exist so a caller can tell the difference between "the store does not hold
this" and "something went wrong reading the store". Collapsing those two into an empty
result set is how a tool comes to look confidently ignorant.
"""

from __future__ import annotations


class AskAviaError(Exception):
    """Base. Every message must be usable by the person who has to fix it."""


class StoreError(AskAviaError):
    """The store could not be read. Never raised for an empty but valid result."""


class SchemaContractError(StoreError):
    """The bound store does not carry a column the tools need.

    Raised loudly at bind time rather than at query time, and it names the store, the
    table and the missing columns. A missing table substitutes a neutral default in
    silence, which is the recurring bug shape in this estate.
    """

    def __init__(self, store: str, table: str, missing: tuple[str, ...], present: tuple[str, ...]):
        self.store, self.table, self.missing, self.present = store, table, missing, present
        super().__init__(
            f"{store}:{table} is missing required column(s) {list(missing)}. "
            f"Present: {list(present)}. "
            f"ask-avia will not substitute a default for a column it cannot read."
        )


class QuarantineError(AskAviaError):
    """A caller asked for a path inside the excluded Benchmark folder.

    Refused, never filtered. The folder holds the golden questions AND their verified
    answers: it is the exam paper, and a service that quietly returned nothing from it
    would still have read it.
    """

    def __init__(self, path: str):
        self.path = path
        super().__init__(
            f"Refused: {path} is inside a folder excluded from the corpus. "
            f"ask-avia never reads, indexes or returns content from it."
        )


class PermissionDenied(AskAviaError):
    """The caller's level does not reach the requested material.

    Note the tools do not raise this for a restricted PROJECT in a search: those return
    as a count only, so the existence of work is visible while its content is not.
    This is for a direct request to a specific restricted record.
    """


class WriteRefused(AskAviaError):
    """Something attempted to write to the store. There is no path that should.

    ask-avia is read-only over the extracted store. Corrections queue as reviewable
    proposals; the model never writes.
    """


class ValidationError(AskAviaError):
    """A tool argument did not validate. The message states what was expected."""


class ComparabilityError(AskAviaError):
    """Records cannot be aligned to one definition, unit, currency or basis.

    Raised rather than averaged. Where values differ in currency, coverage or
    denominator, the correct answer is the refusal to average, with the components
    shown. compare_evidence catches this and returns the components plus the flag.
    """
