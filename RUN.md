# Running ask-avia

The read-only MCP service over the Avia extraction store. Edited on the Dev PC, **run on
the workstation** where the store lives. Read-only throughout; it never writes the store.

## What it serves

Three tools today: `search_datapoints`, `get_source`, `compare_evidence`. All cited, all
audit-logged, all refusing the quarantined Benchmark folder. `find_precedents`,
`build_workbook`, `file_to_project` and `get_fact_of_day` are not built yet.

## Port and transport

- Transport: streamable HTTP (mcp 2.x), path `/mcp`.
- Port: **8040** (provisional; 8030 is DDFS). Override with `ASKAVIA_PORT`. Still
  unregistered in the estate service register (switch S4).
- Auth: bearer token, **enforced** by middleware. Every request needs
  `Authorization: Bearer <token>`; `/health` is the only open path. The service also
  refuses to start if `ASKAVIA_AUTH_TOKEN` is unset. It fails closed both ways.

## Provision on the workstation (one time)

```powershell
cd C:\src ; git clone https://github.com/Aviaacct1/ask-avia.git
cd C:\src\ask-avia
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Environment

Copy `.env.example`, or set in the service environment. Secrets never go in the repo.

```
AVIA_LOCAL_CACHE   = E:\Avia
ASKAVIA_STORE_PATH = E:\Avia\Extract\full\out\full_v2.duckdb   # pin the store explicitly
ASKAVIA_AUTH_TOKEN = <generate a long random token; keep in the password manager>
ASKAVIA_PORT       = 8040
ASKAVIA_HOSTNAME   = ask.aviacortex.com    # candidate, not yet allocated
# for get_source Egnyte locators / later file_to_project:
ASKAVIA_EGNYTE_DOMAIN = aviasolutions.egnyte.com
ASKAVIA_EGNYTE_TOKEN  = <token>
```

`ASKAVIA_STORE_PATH` is pinned deliberately (switch S1): `Extract\full\out` can hold both
`full.duckdb` (the frozen v1) and `full_v2.duckdb`, and landmark discovery would bind the
wrong one alphabetically. Point it at `full_v2.duckdb` explicitly.

## Preflight (no secrets needed)

```powershell
.venv\Scripts\python.exe check_env.py --probe-only
```

Reports the interpreter, dependencies, the quarantine check, and the bound store's kind
and row count. Expect `full_v2.duckdb`, circa 722m rows, and `harvest_manifest` present.

## Start

```powershell
.venv\Scripts\python.exe -m askavia.server
```

It prints the bound store, then serves on `0.0.0.0:8040/mcp` with auth enforced. Check it:

```powershell
curl http://localhost:8040/health          # 200, reports the bound store
curl http://localhost:8040/mcp             # 401 without a bearer token
```

## Connect Claude

Register a remote MCP connector on **John's Pro Max account** (Team deferred, per the
handover) pointing at `https://<hostname>/mcp` with the bearer token. Reach it over the
existing Cloudflare or Tailscale tunnel that already serves DDFS on 8030; whether ingress
for 8040 already exists is unverified (switch S4). The acceptance run (22 golden questions)
runs through Pro Max with ONLY this connector attached.

## Selftests

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q      # expect 19 passing
```

## Service install (deferred)

NSSM, per the handover. No install script yet; run in a console for now.

Copyright Avia Solutions Limited. All rights reserved.
