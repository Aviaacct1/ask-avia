"""ask-avia: the Avia data-library MCP service.

Read-only over the extracted store. Internal only, never sold.

Standing constraints, from HANDOVER_Ask_Avia_08Aug2026.md:
  - no store writes, ever; corrections queue as reviewable proposals
  - no model calls inside this service, and no per-token API usage anywhere
  - the Benchmark folder is never read or indexed; it is the exam paper
  - no uncited figures; every record carries class, verification status and source
  - incomparable figures are not averaged, and conflicting versions are disclosed
    as versions rather than collapsed to one number
"""

__version__ = "0.1.0.dev0"
