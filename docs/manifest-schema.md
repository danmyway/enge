# Manifest schema and write contract

Full contract for the JSON manifest format written under
`~/.local/share/enge/runs/<run_id>.json`, referenced from CLAUDE.md's
"Manifest schema" pointer. See `docs/results-json-schema.md` for how
`results.json`'s per-task fields are copied from this shape at harvest
time — this file does not restate that contract.

## Schema version

`schema_version` is the literal integer `1` (`src/enge/utils/manifest.py:8`,
exported as `SCHEMA_VERSION` and emitted by `ManifestWriter.to_dict` at
line 74). `migrate/__main__.py` does not import this constant; it
hardcodes the same literal `1` at its own envelope construction
(`migrate/__main__.py:90`). A bump would signal a breaking change to the
envelope or `requests[]` shape that existing readers cannot tolerate
un-migrated. Consistent with the established precedent in the
results.json contract (`f8fcdfb`, 2026-08-25; `cb01b83`, 2026-08-31 —
both promoted a field to required-key/nullable-value, a reader
relaxation): no `schema_version` bump has ever been taken for a reader
relaxation.

## Envelope

Written by `ManifestWriter.to_dict` (`utils/manifest.py:72`) for native
manifests and hand-built at `migrate/__main__.py:89` for migrated ones.
The two constructions currently emit the same 10 keys; nothing enforces
that they always will, and L14 (`feat/manifest-config-snapshot`,
maintainer-ruled 2026-08-31: audit-only, a new top-level key) is queued
to add an 11th. Treat this list as open to future top-level keys, not
closed.

- `run_id` (str, ULID) — required, non-null on both origins. Matches
  the manifest's filename and the `latest` pointer's target. Native:
  supplied by the writer's caller at construction
  (`dispatch/__main__.py:358`, `rerun/__main__.py:833`, both via
  `generate_ulid()`). Migrated: freshly generated at
  `migrate/__main__.py:88`, matching the migrated file's own name — a
  migrated manifest's `run_id` has no relationship to the original
  legacy archive filename. This is the one envelope field guaranteed
  present and non-null regardless of origin — see "Shape by origin"
  below for why that matters.
- `created_at` (str, ISO 8601 `%Y-%m-%dT%H:%M:%SZ`, UTC) — required,
  non-null. Native: computed in `ManifestWriter.__init__`
  (`utils/manifest.py:27`) at writer-construction time, i.e. when the
  dispatch/rerun invocation begins building its manifest — NOT when
  `flush()` later writes the file to disk. Migrated: parsed from the
  legacy archive filename's embedded timestamp when the
  `enge_jobs_archive_<14 digits>` pattern matches
  (`migrate/__main__.py:75-83`); falls back to
  `datetime.now(timezone.utc)` at migration time only when the filename
  doesn't match.
- `command` (str) — required, non-null. Native: the writer's caller
  passes the CLI subcommand's dispatch verb literally — `"test"` from
  dispatch (`dispatch/__main__.py:359`), `"rerun"` from rerun
  (`rerun/__main__.py:834`). Migrated: hardcoded `"test"`
  (`migrate/__main__.py:93`) — a migrated manifest cannot know whether
  the original archive came from a dispatch or a since-superseded
  legacy rerun, so it always claims dispatch origin.
- `argv` (list of str) — required, non-null (may be empty). Native:
  the real `sys.argv` at invocation. Migrated: hardcoded `[]`
  (`migrate/__main__.py:94`) — no original argv survives in a legacy
  archive file.
- `tags` (list of str) — required, non-null (may be empty). Native
  dispatch: `--set-tag` values. Native rerun: inherited parent tags
  (only when lineage resolves — see `requests[]` below) plus the
  invoking run's own tags plus the literal `"rerun"`. Migrated: tags
  extracted from the legacy archive filename
  (`extract_tags_from_filename`).
- `parent_run_id` (nullable str, ULID) — required key, nullable value.
  Native dispatch: always `null` (a dispatch is never a rerun of
  anything). Native rerun: from `_resolve_parent_lineage`
  (`rerun/__main__.py:86`) — see "Rerun lineage" under `requests[]`
  below for when this resolves vs. stays `null`. Migrated: hardcoded
  `null` (`migrate/__main__.py:96`).
- `origin` (str enum: `"native"` | `"migrated"`) — required, non-null.
  The discriminator for every shape difference in this document.
  Hardcoded per writer (`utils/manifest.py:81`,
  `migrate/__main__.py:97`).
- `context` (dict) — required, non-null (may be empty `{}`). See
  "Context" below.
- `requests` (list of dict) — required, non-null (may be empty). See
  "Requests entry" below.

## Context

`context` is a free-form dict at the writer level — nothing in
`ManifestWriter` enforces a closed key set, and it should not be
documented as one. In practice, the observed keys are `event`,
`source`, `target`, `tiers`, `architectures`, and `set`.

Native dispatch (`dispatch/__main__.py:328`, `_build_manifest_writer`)
populates `event`/`source`/`target`/`tiers`/`architectures` from the
resolved test attributes, and `set` only when `--set` was passed on the
CLI (`:340-341`, holding the first requested set name — a convenience
value for the fast-path filter below, not necessarily representative of
every request in a multi-set run). Native rerun constructs its
`ManifestWriter` with no `context` argument at all
(`rerun/__main__.py:832-837`), so every rerun manifest's `context` is
`{}`. Migrated manifests populate `set`, `tiers`, `architectures` only,
via `_parse_context_from_tags` (`migrate/__main__.py:19-38`) —
`event`/`source`/`target` are never derivable from a legacy archive
filename and are always absent.

**`context` is a query surface, not a dumping ground.**
`ManifestReader.find_runs` (`utils/manifest.py:167`) reads `context.set`
as a fast-path filter before falling back to scanning `requests[]`;
adding unrelated data to `context` risks colliding with or degrading
that filter.

## Requests entry

Each writer builds one dict per TF task and appends it to `requests[]`.
Field-by-field, type / nullability / producing writer:

- `task_id` (str) — required, non-null. The TF request UUID; join key
  to `results.json`. Native (dispatch and rerun): parsed from the tail
  of the just-submitted request's `log_artifact_url`
  (`dispatch/set_flow.py:505-507`, `rerun/__main__.py:950-952`) — a
  request is only appended to `requests[]` at all when this parse
  succeeds (`set_flow.py:509`, `rerun/__main__.py:953`), so `task_id`
  and `artifacts_url` are never independently missing on the native
  path. Migrated: the literal UUID line from the legacy archive file.
- `set` (nullable str; `add_request`'s `set_name` keyword, stored under
  the key `set`) — required key, nullable value. Native dispatch:
  `spec.set_name`, `null` for a no-set CLI invocation (the
  test-development workflow). Native rerun: `parent_entry.get("set")`
  from the parent manifest's matching request
  (`rerun/__main__.py:959`) — `null` whenever rerun lineage did not
  resolve (see "Rerun lineage" below), regardless of what the original
  dispatch's `set` was. Migrated: `context.get("set")`
  (`migrate/__main__.py:108`) — `null` unless a tag matched as the set
  name.
- `tier` (nullable str) — required key, nullable value. Native
  dispatch: `spec.tier`, `null` for `--plan`-only dispatch. Native
  rerun: `parent_entry.get("tier")` — same lineage-resolution
  dependency as `set`. Migrated: the first entry of
  `context.get("tiers")`, `null` if absent.
- `arch` (nullable str) — required key, nullable value. Native
  dispatch: `spec.arch`. Native rerun: the rerun payload's OWN
  environment arch (`request_data["architectures"][0]`,
  `rerun/__main__.py:938,961-965`) — unlike `set`/`tier`, `arch` is
  never parent-inherited; it travels with the rerun payload itself.
  Migrated: the first entry of `context.get("architectures")`, `null`
  if absent.
- `plan` (nullable str) — required key, nullable value. Native
  dispatch: `spec.plan`. Native rerun: built from the rerun payload's
  own `test.fmf.name` (`rerun/__main__.py:900-909`). Migrated: always
  `null` (`migrate/__main__.py:119`) — never known from a legacy
  archive.
- `source_compose` (nullable str) — required key, nullable value.
  Native dispatch: `submit_test.compose`. Native rerun: the rerun
  payload's own re-pinned compose (`request_data["compose"]`,
  `rerun/__main__.py:930`). Migrated: always `null`.
- `target_compose` (nullable str) — required key, nullable value.
  Native dispatch: `submit_test.target_compose`. Native rerun:
  `parent_entry.get("target_compose")` — inherited from the parent,
  same lineage-resolution dependency as `set`/`tier`. Migrated: always
  `null`.
- `artifacts_url` (nullable str) — required key, nullable value.
  Native dispatch and rerun: the submitting request's own
  `log_artifact_url` (`dispatch/set_flow.py:520`,
  `rerun/__main__.py:969`) — see the `task_id` note above; in practice
  always non-null when the entry exists at all on the native path.
  Migrated: `f"{log_base}/{task_id}"` constructed from
  `testing_farm_endpoint.log_artifact_baseurl`
  (`migrate/__main__.py:104`), or `null` if that config value is
  falsy.
- `dispatched_at` (str, ISO 8601) — required, non-null; defaulted by
  `add_request` itself (`utils/manifest.py:60-61`) to
  `datetime.now(timezone.utc)` when the caller doesn't pass it. Neither
  dispatch nor rerun passes `dispatched_at` explicitly, so on the
  native path this is the real per-request submission time (the moment
  `add_request` runs, immediately after that request's own TF submit
  call) — NOT the run-level `created_at`. Migrated: passed explicitly
  as the same value as the envelope's `created_at`
  (`migrate/__main__.py:123`) — every request in a migrated manifest
  shares one timestamp, even if the original requests were dispatched
  at slightly different times.
- `launch_uuid` (nullable str) — optional key (`.get` default `None`).
  Native dispatch and rerun: the ReportPortal launch UUID for this
  request, `null` when no RP launch applies. Migrated: key absent
  entirely.
- `rerun_of` (nullable str) — optional key. The parent task's own UUID
  being rerun. Native rerun: `original_uuid`, popped from the payload's
  `_original_uuid` (set at `rerun/__main__.py:618`, consumed at `:843`)
  — this is INDEPENDENT of `parent_run_id`/lineage resolution, so by
  code reading it should be populated even on an `-i`/raw-input rerun
  that has no resolved `parent_run_id`. **This population rule is OPEN
  (ledger L8)**: a production report states `rerun_of` was absent on
  such a manifest, contradicting the code reading above, and this has
  not been reconciled. Do not treat either reading as settled. Native
  dispatch: always `null` (a first dispatch is never a rerun).
  Migrated: key absent entirely.
- `source` (nullable str) — optional key. The upgrade-path source value
  (e.g. `"9.9"`). Native dispatch: threaded down from the resolved test
  attributes. Native rerun: `env_vars.get("SOURCE_RELEASE")` from the
  rerun payload's own `tmt.environment` variables
  (`rerun/__main__.py:972`) — the payload's own value, not
  parent-inherited. Migrated: key absent entirely.
- `target` (nullable str) — optional key. Mirrors `source`: native
  dispatch from resolved test attributes, native rerun from
  `env_vars.get("TARGET_RELEASE")` (`rerun/__main__.py:973`), migrated
  always absent.
- `git_ref` (nullable str) — optional key. Native dispatch:
  `submit_test.tests_git_ref`. Native rerun:
  `request_data["tests_git_ref"]`, the rerun payload's own
  `test.fmf.ref` (`rerun/__main__.py:923`). Migrated: key absent
  entirely.
- `event` (nullable str) — optional key. Native dispatch: the
  dispatch's resolved event. Native rerun: the rerun payload's own
  `tmt.context.event` (`rerun/__main__.py:944`). Migrated: key absent
  entirely.
- `build_ids` (list of str, default `[]`, never `null` when the key is
  present) — optional key, absent entirely on migrated manifests.
  Native dispatch and rerun: `[a["id"] for a in <artifacts>]`
  (`dispatch/set_flow.py:526`, `rerun/__main__.py:976`, filtered to
  entries with an `id` on the rerun side). Format is
  `"<build_id>:<chroot>"` for COPR-resolved artifacts
  (`utils/tf_artifact.py:434`). **Caution**: the chroot suffix is NOT
  an arch signal for the run as a whole — an `s390x` run can
  legitimately carry an `epel-9-x86_64`-suffixed build ID (ledger L9;
  whether any downstream consumer relies on parsing this suffix is
  unverified). State this as a caution only — this document asserts
  nothing about consumers.

### Rerun lineage (set/tier/target_compose inheritance)

`_resolve_parent_lineage` (`rerun/__main__.py:86`) is the sole source of
`parent_run_id`, and it returns `(None, [])` in three cases: (i)
`task_source` is not a `"manifest:<id>"` string (the `-i`/raw-input
path), (ii) `task_source == "manifest:latest"` (the default `enge
rerun` with no selector), (iii) `task_source == "manifest:filter"` (any
`--run`-multi / `--set` / `--tier` / `--tag` / date-driven rerun). Only
a lone explicit `--run <id>` rerun resolves lineage. When it doesn't,
`_build_parent_request_index` (`rerun/__main__.py:109`) returns `{}`
and the rerun manifest's `set`/`tier`/`target_compose` are all `null`
for every request in that manifest.

As of `f8fcdfb` (2026-08-25, `set`) and `cb01b83` (2026-08-31, `tier`),
all three of these fields are required-key/nullable-value on both the
manifest and the `results.json` side, so a lineage-unresolved rerun no
longer crashes `enge report`'s cache — it reports with null
coordinates. The remaining consequence is that `enge report --set`
does not match most rerun tasks, not a crash.

## Shape by origin

Native `requests[]` entries carry 16 keys (`task_id` plus the 15
optional keyword fields of `add_request`). Migrated entries carry 9:
`task_id`, `set`, `tier`, `arch`, `plan`, `source_compose`,
`target_compose`, `artifacts_url`, `dispatched_at`. The 7 keys migrated
entries omit (`launch_uuid`, `rerun_of`, `source`, `target`, `git_ref`,
`event`, `build_ids`) are ABSENT, not `null` and not `[]`.

**Hard consumer invariant: use `.get(key, default)` on `requests[]`
entries, never `[key]`.** Bracket access on any of the 7
migrate-omitted keys raises `KeyError` on a legitimate manifest.

`utils/task_resolver.py:84` reads `m['run_id']` with bracket access
over the manifests returned by `select_runs`, and this is legitimate —
it is NOT an exception to the rule above, because `run_id` is an
**envelope** field, and the Envelope section above establishes it as
the one field guaranteed present and non-null on both origins. The
`.get()` rule binds on `requests[]` entry fields, where shape actually
diverges by origin; it does not extend to envelope fields whose
presence is already guaranteed. Do not generalize this line as license
for bracket access elsewhere.

## Dated-ruling provenance

- `f8fcdfb` (2026-08-25) / `cb01b83` (2026-08-31): promoted
  `results.json`'s `set`/`tier` to required-key/nullable-value,
  removing the crash that a lineage-unresolved rerun manifest used to
  cause — see "Rerun lineage" above.
- L14 (`feat/manifest-config-snapshot`, maintainer-ruled 2026-08-31):
  queued to add one new top-level envelope key (a redacted config
  snapshot); the Envelope section above is written to admit that
  without restructuring.
- Ledger L8 (maintainer, 2026-08-25, parked; narrowed 2026-09-01):
  rerun `set`/`tier`/`target_compose` inheritance is settled as
  conditional on lineage resolution (documented above);
  `rerun_of`'s population rule remains OPEN.
- Ledger L9 (unverified): the `build_ids` chroot-suffix caution above.
