# ask-avia status handover

Version 1.0 | 9 August 2026 | Avia Solutions | For a fresh session that has the repo and no chat history

Read this, then the canonical specs in section 7. This file records state, not design; where
this file and a spec disagree, the spec wins and this file is wrong.

Repo `github.com/Aviaacct1/ask-avia`, private, branch `main`. Four commits, working tree clean:

| Commit | Content |
|---|---|
| `c3f8ff8` | .gitignore, .gitattributes, config, check_env with store probe (root commit) |
| `680f5f3` | store adapter with concept binding, alias table, audit log, errors |
| `aad2dc9` | switch register |
| `cba302d` | pin mcp major to 2.x, record resolved versions, report distribution version |

Twelve tracked files, 1,150 lines of Python and TSV. Clone-that-runs proved partially: the repo
was cloned and `check_env.py` and the store adapter both ran from the clone. Full task 7 proof
(workstation, venv, real store) NOT done.

---

## 1. Where it stands, module by module

**Nothing serves. There is no server, no tool, and no committed test.** What exists is the
foundation those three sit on, and it is verified working.

| Module | Lines | State | What runs today |
|---|---|---|---|
| `askavia/config.py` | 235 | IMPLEMENTED | Env var resolution, `AVIA_LOCAL_CACHE` as single path hinge, landmark store discovery reporting every path tried on failure, `EXCLUDED_CORPUS_PATHS` + `is_excluded()`, auth fails closed via `_require_env`. Exercised on the Dev PC and in a Linux sandbox. |
| `askavia/store.py` | 298 | IMPLEMENTED FOR BINDING ONLY | `load_alias_table`, `map_columns`, `Store.bind()`, `_measure_resolve`, `refuse_write`. Binds a real DuckDB store, maps concepts to actual columns, measures resolve state, raises `SchemaContractError` naming store/table/concept on a required gap. **NO QUERY METHODS EXIST.** There is no search, no fetch-by-id, no filter, no row return. A tool cannot be written against this module today without adding a query layer first. |
| `askavia/audit.py` | 124 | IMPLEMENTED | `AuditLog.record()` writing JSON Lines, one file per UTC day, secret-named keys redacted at any depth, store binding stamped on every entry, degraded mode reporting to stderr rather than failing the call. Verified including the unwritable-directory path. |
| `askavia/errors.py` | 83 | IMPLEMENTED (definitions) | Seven exception types with messages. No logic beyond message construction. `SchemaContractError` and `QuarantineError` are raised by real code; `PermissionDenied`, `ValidationError` and `ComparabilityError` are defined and NOT YET RAISED ANYWHERE. |
| `askavia/column_aliases.tsv` | 47 | IMPLEMENTED (data) | 23 concepts, 6 required: `value`, `year`, `metric_code`, `entity`, `unit`, `source`. Tracked data file, not code, so a store that renames a column needs a row not a commit. |
| `check_env.py` | 349 | IMPLEMENTED | Interpreter and venv check, dependency import with distribution-version fallback, quarantine check with four probes, config load, read-only store probe, pipeline reference file check. Exits non-zero on any FAIL. `--probe-only` runs without secrets. |
| `askavia/server.py` | none | **DOES NOT EXIST** | No entry point, no transport, no MCP registration, no auth middleware. |
| `askavia/tools/` | none | **DOES NOT EXIST** | No package. None of the seven tools has a file. |
| `tests/` | none | **DOES NOT EXIST** | **No selftests are committed.** Verification so far was interactive in a sandbox and is not reproducible by a later session. This is a real gap against Note 6 task 5 and should be treated as such, not as "tests to add later". |
| `deploy/` | none | **DOES NOT EXIST** | No NSSM install script, no deploy script. |
| `RUN.md` | none | **DOES NOT EXIST** | Start command and port not documented. |

**Provisioning state.** Dev PC (`DESKTOP-3R7OQVJ`) provisioned: `C:\src\ask-avia\.venv`, Python
3.12.10, own virtualenv, dependencies installed, `check_env.py --probe-only` returns 11 pass,
0 warn, 1 fail. The single fail is the true statement that this machine holds no extraction
store. Workstation (`DONATELLO`) NOT provisioned: not cloned, no venv, never run there.

**Resolved dependency set, Dev PC, 8 August 2026, Python 3.12.10:** duckdb 1.5.5, openpyxl
3.1.5, mcp 2.0.0 (mcp-types 2.0.0), httpx 0.28.1, starlette 1.6.0, uvicorn 0.52.1, pydantic
2.13.4. `mcp` resolved to **2.0.0, not 1.x**, against a bare `mcp>=1.2`; the major is now pinned
`>=2.0,<3`. Any server code must target the 2.x API. Do not assume 1.x examples apply.

---

## 2. The seven tools

Specified in AIP Note 3 and restated in Note 6 v2.0 PROMPT A. **All seven are NOT STARTED.**
Nothing below is scaffolded, stubbed or partially written; there are no files.

All are read-only. None writes to the store. None calls a model. Every call is audit-logged with
user, tool, filters, record IDs returned and timestamp.

| # | Name | Answers | Inputs | Outputs | State |
|---|---|---|---|---|---|
| 1 | `search_datapoints` | Structured query over the store | `metric`, `entity`, `geography`, `year_range`, `class`, `status` | Records with value, unit, year, project, source document and location, class, verification status, **plus the parsed filters echoed back** so the model can state "Understood as" and the user can correct in plain language | NOT STARTED |
| 2 | `find_precedents` | Which past engagements are comparable | `asset`, `region`, `size`, `topic` | Project-level results ranked by comparability (asset size in engagement year, topic tags, carrier type). **Projects above the caller's permission level return as a COUNT ONLY**, so the existence of work is visible and its content is not | NOT STARTED |
| 3 | `compare_evidence` | Are these figures actually comparable | `record_ids` | Deterministic alignment to one definition, unit, currency and basis, with false-comparability flags. **Alignment rules are code, never model judgement.** Where values differ in currency, coverage or denominator the correct output is the refusal to average, with components shown | NOT STARTED |
| 4 | `get_source` | What does the document actually say | `record_id`, and a scope for the manifest | Verbatim extract and Egnyte locator; **plus the skip manifest for any scope** ("3 of 41 in-scope documents skipped") | NOT STARTED |
| 5 | `build_workbook` | Turn records into an Avia deliverable | `template`, `record_ids`, `project` | One template in v1, the precedent comparison: comparison sheet, basis sheet, source register. Deterministic code from the Avia template. Validations: opens, recalculates, units aligned, sources on every sheet, author "Avia Solutions", en-GB proofing language | NOT STARTED |
| 6 | `file_to_project` | Put the work where it belongs | `project`, `files`, `summary` | Writes outputs and a session summary to the project's Data and Analysis folder on Egnyte under the user's name, with duplicate and version check before writing (D9) | NOT STARTED |
| 7 | `get_fact_of_day` | The card the frame shows on open | none | Reads the current card from the store. **Generation is a separate scheduled task on a seat, not part of this service** | NOT STARTED |

**Deliberately out of v1** (Note 3): the full-text document index (layer 2 proper, which rides the
Egnyte connector's own search meanwhile); any store write access; a curation interface; record-level
permissions beyond folder inheritance; workbook templates two and three; the Relationships
workstream; anything TAO-facing.

---

## 3. Runtime and access

**There is no entry point yet.** Everything in this section is intent from Note 6 v2.0 task 6 plus
one provisional decision, not implemented behaviour.

- **Transport**: remote MCP endpoint over HTTPS. Note 6 specifies remote, not stdio, because a Claude
  connector must reach it over the network. With `mcp` 2.0.0 that means the streamable HTTP transport
  on starlette/uvicorn, both already installed as `mcp` dependencies. **The specific transport class
  has not been chosen or written.**
- **Port**: **8040, PROVISIONAL and registered nowhere.** 8030 is DDFS (`ddfs.aviacortex.com`). Set
  as `DEFAULT_PORT` in `askavia/config.py`, overridable by `ASKAVIA_PORT`.
- **Hostname**: not allocated. DDFS and Atlas use `*.aviacortex.com`; the obvious candidate is
  `ask.aviacortex.com` or similar, NOT chosen.
- **Authentication**: bearer token in `ASKAVIA_AUTH_TOKEN`. `config.load()` raises `ConfigError` if
  it is unset, so the service **fails closed** by construction. **Nothing enforces it yet** because
  there is no request path. The enforcement point must be written with the server.
- **Egnyte identity**: `ASKAVIA_EGNYTE_DOMAIN` and `ASKAVIA_EGNYTE_TOKEN`, required by
  `config.load()`, consumed by nothing yet. They are for `get_source` locators and `file_to_project`
  writes.
- **Reachability**: through the existing Cloudflare or Tailscale plumbing that already serves DDFS
  on 8030. **Which of the two, and whether ingress already exists or must be added, is unverified.**
  The DDFS Cockpit note records "same tunnel, ingress already exists" for 8030; do not assume that
  extends to a new port.
- **Service install**: NSSM, per Note 6. No script written.
- **Deploy**: workstation pulls from GitHub and restarts the service. Editing on the Dev PC, running
  on DONATELLO.
- **Connector registration**: **amended by John.** The Claude Team account does not yet exist, so
  register the connector to **John's Pro Max account only**; Team is deferred until purchase. The
  acceptance run (task 9) also runs through Pro Max.

---

## 4. Store binding

**Bound to nothing in production. The service has never read the real extraction store.**

**How binding works.** `config.discover_store()` searches by landmark under `AVIA_LOCAL_CACHE`, in
this order:

1. `<root>\Extract\proof` for `*.duckdb`
2. `<root>\Extract\pilot\out` for `*.duckdb`, then `store_parts\*.parquet`

`ASKAVIA_STORE_PATH` overrides discovery and binds one path explicitly. On failure every path tried
is printed. `Store.bind()` then picks the points table by column signature (`value`, `year`,
`metric_code`), maps concepts through `column_aliases.tsv`, and measures how populated
`metric_code`, `temporality`, `project_id` and `entity` actually are.

**What has been read.** Only Meridian operational stores on the Dev PC, as a shape test:
`dot_atomic.duckdb` (44,316 rows, taxonomy-shaped, 11 concepts mapped) and `casm_benchmark.duckdb`
(480 rows, correctly rejected as not a points table). **No proof store and no pilot store has been
opened.** `E:\Avia\Extract` was confirmed to exist on 8 August with `proof\` and `pilot\` present as
directories; **their contents were not enumerated** because the mount was too slow to scan.

**To point it at the rebuilt 25-year store**, on DONATELLO:

```powershell
cd C:\src ; git clone https://github.com/Aviaacct1/ask-avia.git
cd C:\src\ask-avia
py -3.12 -m venv C:\src\ask-avia\.venv
C:\src\ask-avia\.venv\Scripts\python.exe -m pip install -r C:\src\ask-avia\requirements.txt
$env:AVIA_LOCAL_CACHE = "E:\Avia"
C:\src\ask-avia\.venv\Scripts\python.exe C:\src\ask-avia\check_env.py --probe-only
```

Read-only throughout, takes no lock, safe against a rebuild in progress. If the rebuild writes into
`Extract\proof`, pin a known-quiet store with `ASKAVIA_STORE_PATH` instead (switch S1).

**Concept dependencies the store may not carry.** Required concepts are satisfied by every store
read so far. These are the ones that were ABSENT everywhere and that specified behaviour depends on:

| Concept | Needed by | Status |
|---|---|---|
| `data_class` (fact / assumption / forecast) | Every record, per the handover's evidence rule | ABSENT from every store read. Optional in the alias table. |
| `verification_status` | Every record; the store is described as pre-resolve so status must travel | ABSENT from every store read. Optional in the alias table. |
| skip manifest (a table, not a column) | `get_source` scope disclosure; AIP Note 2 **O2** | NO table matching `skip` or `manifest` found in any store read. `Store.bind()` records `skip_table=None`. |
| `project_id` / `project_name` | `find_precedents` ranking; permission inheritance; `file_to_project` routing | ABSENT from `dot_atomic`. Unknown in the real store. |
| `egnyte_path` | `get_source` locator; permission inheritance from the source folder | ABSENT from `dot_atomic`. Unknown in the real store. If the store does not carry it, the locator must be derived and that derivation is unspecified. |
| `source_type_flag` | External-use filtering; `bench_public_` k-anonymity views | In the taxonomy (note 03 section 3.6) but ABSENT from `dot_atomic`. |

**If the rebuilt harvest does not carry `data_class`, `verification_status`, a project reference or
an Egnyte path, that is a pipeline question before it is a service question.** This service cannot
supply what the store does not hold, and inventing any of them would break the one rule the tool
exists to enforce.

---

## 5. Open issues and switches

### Live blockers

| Blocker | Effect | Owner |
|---|---|---|
| Store not verified | Tools 1 to 4 cannot be written honestly. This is the top of the critical path. | Probe on DONATELLO |
| No committed tests | Note 6 task 5 unmet. Nothing a later session can re-run. | Next session |
| No server | Nothing is reachable. Auth is defined but unenforced. | Next session |

### AIP Note 2 open issues that bear on release

| Ref | Issue | State |
|---|---|---|
| **O2** | Skip manifest queryable | **OPEN and blocking.** No manifest found. `get_source` is specified to disclose "N of M in-scope documents skipped"; without a manifest that disclosure cannot be made, and the disclosure is a design requirement rather than a nicety. Confirm with the pipeline. |
| **O9** | Benchmark folder excluded from corpus in pipeline CONFIG, checked not assumed | **CLOSED FOR THIS SERVICE.** `config.EXCLUDED_CORPUS_PATHS` plus `is_excluded()`, verified by four probes in `check_env.py` including the backslash form and a negative control. **Still open for the extraction pipeline itself**, which is a separate codebase. |
| **O11** | Egnyte restrictions on Project Falcon (AG Capital Plovdiv) and Project Albatross (3i special missions) | **OPEN and blocking team onboarding.** Both folders are currently fully readable, which voids the permission model whatever this service enforces. Needs no code. Could close while the store rebuilds. |
| **O1** | Terms verification bundle (training use, Team chat sharing, embedded-browser use) | OPEN. Gated on the Team purchase. Blocks ONBOARDING, not the build or acceptance, which run on Pro Max. |
| **O12** | Benchmark placeholders Q5, Q13, Q14 | OPEN, non-blocking. 22 of 25 questions are scoreable and suffice. |
| **O3** | `#006D9F` small-text contrast | OPEN, belongs to `ask-avia-desktop` (Prompt B), not this repo. |

### Switch register (`docs/SWITCH_REGISTER.md`)

Every entry carries the test that retires it. A switch with no test named against it is unfinished
work with a lid on.

| # | Switch | State | Retiring test |
|---|---|---|---|
| S1 | `ASKAVIA_STORE_PATH` explicit binding | Available, expected in use | Landmark discovery binds the intended store on DONATELLO with the variable unset, and `check_env.py` reports the expected store kind and row count. Then unset it in the service environment. |
| S2 | Parquet (pilot) store support | OFF, `bind()` raises | Resolve has been run over the pilot store and a probe shows `metric_code` populated above zero. Only then is parquet reading worth writing. |
| S3 | `check_env.py --probe-only` | Diagnostic, permanent | Not applicable. Listed so nobody mistakes it for a way to run the service without auth. The service itself fails closed and has no equivalent flag. |
| S4 | Port 8040 | Provisional, unregistered | The register is located, the port recorded there and in `RUN.md`, and the hostname resolves over the existing tunnel. |
| S5 | Egnyte permission enforcement | NOT BUILT | Store probe confirms a project or path concept is present and populated, then the restricted-project count-only selftest passes. |

### Decisions waiting on John

1. **Where the workstation service and port register actually lives.** Note 6 v2.0 task 6 names
   `avia-workstation`; that name appears in no other document, not estate index v11 and not
   `NAMING_AND_STRUCTURE_REGISTER_08Aug2026.md`. A prompt was drafted for the Meridian session to
   establish it. Until answered, S4 stands.
2. **Which store v1 binds to** once the rebuild finishes: the rebuilt 25-year store, or the proof
   stores. The probe output should decide it.
3. **Hostname** for the service.
4. **`ask-avia-desktop` repo name confirmation**, still PENDING per estate index. Blocks Prompt B,
   not this repo.
5. **O11 closure** before any team onboarding.

### Standing rule for the acceptance run

The golden question set AND its verified answers live in
`/Shared/Company Data/14 Avia/AI_System/AIP/Benchmark - EXCLUDED FROM CORPUS`. It is the exam paper.
**The session that ANSWERS the questions must never have read the answers**, and must run with only
this connector attached, not with the Egnyte connector, which would let it retrieve the answers from
the source documents and pass without the service working. Scoring may see both. Neither this file
nor the sessions that produced it has read that folder.

---

## 6. Definition of done, first team-usable release

"Complete" for the first release the team can use, per Note 6 v2.0 PROMPT A tasks 4 to 10 and the
Note 5 scoring rules:

1. Seven tools implemented with typed schemas, validation and error handling. No swallowed
   exceptions around data loads; every fallback reports.
2. Every call audit-logged: user, tool, filters, record IDs returned, timestamp.
3. Selftests with pinned fixtures for every tool, **all green**, including: restricted-project
   count-only behaviour, skip-manifest disclosure, false-comparability flag, workbook validation
   suite, filing dedupe.
4. Served as a remote MCP endpoint over HTTPS, auth enforced and failing closed, NSSM install and
   deploy scripts in `deploy/`, `RUN.md` with the exact start command and port, port and hostname
   registered.
5. Completeness proof: fresh clone to a temp path, provisioned per the five steps, `check_env`
   passes and selftests green **from the clone**.
6. Connector registered to John's Pro Max account (Team deferred).
7. Acceptance: the 22 scoreable golden questions run through Claude with only this connector.
   **9/10 average to pass. Zero uncited figures. ANY hallucination fails the entire run.** Every
   no-answer question must return "no evidence held". Scores reported per question.
8. **Look at the artefacts**: open the generated workbook and read it. A green validation list
   proves nothing about what the workbook looks like. Six of eight Meridian defects on 8 August
   passed a green suite and were caught only by looking at rendered output.
9. AVIA TOOL AUDIT block reported, plus anything that differed from Prompt A or the handover.

Beyond the release but before team onboarding: O11 closed, O1 terms bundle done once Team exists.

### Remaining work in priority order

1. **Probe the rebuilt store on DONATELLO.** Everything downstream is guesswork without it.
2. **Add a query layer to `askavia/store.py`.** Parameterised, read-only, concept-based. No tool can
   be written until records can be fetched.
3. **`search_datapoints` and `get_source` first**, with pinned fixtures and committed tests. They are
   the pair the citation rule depends on, and most golden questions need only those two.
4. **`compare_evidence`**, whose alignment rules are code and which must refuse to average rather
   than average badly.
5. **The server**: transport, auth enforcement, tool registration against the `mcp` 2.x API.
6. **`find_precedents`** with the permission model, gated on the store carrying a project reference.
7. **`build_workbook`**, including the author metadata and en-GB proofing checks, then open the file
   and read it.
8. **`file_to_project`** and **`get_fact_of_day`**.
9. `deploy/`, `RUN.md`, NSSM, port registration.
10. Clone-that-runs on the workstation, connector registration, acceptance run, audit block.

---

## 7. Canonical documents

Read these directly. Do not rely on this summary where it disagrees with them.

### Governing

| Document | Path |
|---|---|
| Estate index (authority on names, locations, state) | `/Shared/Company Data/14 Avia/AI_System/ESTATE-INDEX.md` (**version 11**, 8 August 2026) |
| Ask Avia programme handover (decisions AND their reasons; do not reopen) | `/Shared/Company Data/14 Avia/AI_System/AIP/HANDOVER_Ask_Avia_08Aug2026.md` |
| Tool-to-git process rules (governs the build) | `/Shared/Company Data/14 Avia/AI_System/HANDOVER_Tool_To_Git_08Aug2026.md` |
| Naming and structure register | `/Shared/Company Data/14 Avia/AI_System/NAMING_AND_STRUCTURE_REGISTER_08Aug2026.md` |

### AIP notes

| Note | Path |
|---|---|
| 1, data access design (two layers, routing, citation rule) | `.../AI_System/AIP/AIP Note 1 - Data Access Design - 31 July 2026.md` |
| 2 v1.1, decision, assumption, open issue and risk registers | `.../AI_System/AIP/AIP Note 2 - Decision and Assumptions Registers - 1 August 2026.md` |
| 3, Cortex MCP v1 scope (the seven tools) | `.../AI_System/AIP/AIP Note 3 - Cortex MCP v1 Scope - 1 August 2026.md` |
| 4, frame build (Prompt B territory) | `.../AI_System/AIP/AIP Note 4 - Frame Build - 1 August 2026.md` |
| 5 v1.1, golden question benchmark and scoring rubric | `.../AI_System/AIP/Benchmark - EXCLUDED FROM CORPUS/` **QUARANTINED. Do not read from a session that will answer the questions.** |
| 6 v2.0, build kickoff prompts (PROMPT A is this build) | `.../AI_System/AIP/AIP Note 6 - Build Kickoff Prompts - 1 August 2026.md` (file name says 1 August; the current version is 2.0 of 8 August) |
| 7, tool migration audit prompt | `.../AI_System/AIP/AIP Note 7 - Tool Migration Audit Prompt - 8 August 2026.md` |

### Data contract

| Document | Path |
|---|---|
| Shared taxonomy v1.0 (metric codes, the store's fields, section 6.6 on resolve state) | `.../AI_System/Knowledge Programme/03 Shared Taxonomy v1.0 - 15 July 2026.md` |
| WS1 ingestion schema v1.1 (T1 to T6; T1 defines `source` as the mandatory named source) | `.../AI_System/Knowledge Programme/11 WS1 Ingestion Schema v1 - 16 July 2026.md` |
| Benchmark query pack v1 | `.../AI_System/Knowledge Programme/05 Benchmark Query Pack v1.sql` |
| Coherence rule catalogue v1 | `.../AI_System/Knowledge Programme/12 WS12 Coherence Rule Catalogue v1 - 16 July 2026.tsv` |
| Entity reference decision (airport atomic on IATA, catchments as mapping sets) | `.../AI_System/Knowledge Programme/28 Decision Note - Entity Reference, Catchment Sets and Name Lookup - 18 July 2026.md` |
| Metric code extensions ready to merge | `.../AI_System/Knowledge Programme/metric_codes_extension_v1.tsv` and `metric_entity_type_extension_v1.tsv` |

### Superseded, do not build from

`Build Weekend Runbook - 2 August 2026.md` and its `OPERATOR GUIDE` in the AIP folder. Both carry
supersession notices; their steps use retired names and pre-handover prompts. The build runs on
Note 6 v2.0 plus `HANDOVER_Tool_To_Git_08Aug2026.md`.

### In this repo

`docs/SWITCH_REGISTER.md`, `.env.example` (variable names only), `askavia/column_aliases.tsv`
(the concept-to-column contract), `requirements.txt` (carries the resolved version set and why the
`mcp` major is pinned).

### Canonical build state

`C:\Users\Carte\OneDrive\Avia\Model_refs\Avia Knowledge Programme - Session State.md`, append-only,
**newest block at the top**. The HUNDREDTH UPDATE of 8 August records this build. Note the file's own
gap: nothing between 25 July and 8 August is in it.

---

Copyright Avia Solutions Limited. All rights reserved.
