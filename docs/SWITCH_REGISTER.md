# ask-avia switch register

Version 0.1 | 8 August 2026 | Owner: John Carter unless stated

Why this file exists. Meridian had five verified improvements switched off, each with
sound reasoning in its own docstring, none re-baselined: the whole Observatory deck path,
the full-year capacity provider, DOT and DB1B for US markets, the haul trim and the
frequency discount. The process was disciplined and missing its last move.

**A default-off switch is a temporary state with an expiry, not a resting place.** Every
entry below names the test that would let it be turned on or retired. A switch with no
test named against it is unfinished work with a lid on, and it does not belong here.

| # | Switch | State | Why it is off | The test that retires it | Owner |
|---|---|---|---|---|---|
| S1 | `ASKAVIA_STORE_PATH` explicit store binding | Available, expected to be used | The 25-year harvest was being rebuilt on DONATELLO on 8 August. Explicit binding pins the service to a store known not to be under write. | Landmark discovery under `AVIA_LOCAL_CACHE` binds the intended store on DONATELLO with `ASKAVIA_STORE_PATH` unset, and `check_env.py` reports the expected store kind and row count. Then unset it in the service environment. | JC |
| S2 | Parquet (pilot) store support | OFF, raises | The pilot store at `Extract\pilot\out\store_parts` is pre-resolve: `metric_code`, `temporality`, `project_id` and `entity` are blank (taxonomy note 03 section 6.6). A service reading it would return nothing for every metric question and look confidently ignorant. `bind()` refuses and says why. | Resolve has been run over the pilot store and a probe shows `metric_code` populated above zero. Only then is parquet reading worth writing. | JC |
| S3 | `check_env.py --probe-only` (secrets not required) | Available, not a production path | It exists so the store can be inspected before secrets are provisioned on a host. It reports missing secrets as WARN rather than FAIL. | Not applicable: this is a diagnostic mode, permanently. Listed so nobody mistakes it for a way to run the service without auth. The service itself fails closed and has no equivalent flag. | JC |
| S4 | Service port 8040 | Provisional, unregistered | 8030 is DDFS. 8040 was chosen to avoid it, but the workstation service register named in AIP Note 6 v2.0 task 6 as "avia-workstation" does not appear in estate index v10 or the naming register, so nothing has been registered. | The register is located (question outstanding with the Meridian session), the port is recorded there and in `RUN.md`, and the hostname resolves over the existing Cloudflare or Tailscale plumbing. | JC |
| S5 | Egnyte permission enforcement | NOT YET BUILT | Depends on the store carrying a project or folder reference that maps to an Egnyte path. Whether it does is a fact about the rebuilt harvest, not yet observed. | Store probe confirms a project or path concept is present and populated; then the restricted-project count-only selftest passes. Note the model gap: Falcon (AG Capital Plovdiv) and Albatross (3i) are still fully readable in Egnyte (Note 2, O11), which voids the permission model until closed, whatever this service does. | JC |

## Standing gaps that are not switches

These are open facts, recorded so a later session does not rediscover them.

- **The skip manifest.** Note 2 open issue O2 requires it queryable. No table matching
  `skip` was found in any store read so far. `get_source` cannot disclose "3 of 41
  in-scope documents skipped" without it, and the disclosure is a design requirement,
  not a nicety.
- **The benchmark quarantine** is enforced in `config.EXCLUDED_CORPUS_PATHS` and checked
  by `check_env.py`, which satisfies O9's requirement that the exclusion live in
  configuration rather than in a note. It is checked on every start.
- **Class and verification status.** `data_class` and `verification_status` are optional
  concepts in `column_aliases.tsv` and were absent from every store read on 8 August.
  Evidence-before-eloquence requires both on every record, so if the rebuilt harvest does
  not carry them, that is a pipeline question and not something this service can supply.

Copyright Avia Solutions Limited. All rights reserved.
