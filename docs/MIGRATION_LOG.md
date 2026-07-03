# Migration log — three modules → unified `content_factory`

Running decision/status log for the merge & refactor. Plan:
`~/.claude/plans/streamed-foraging-spark.md`.

## Target decisions (approved)
1. One installable package + one FastAPI app (single deployable).
2. Merge the Spravochnik SQLite catalog into Postgres as real relational tables.
3. Rewrite the legacy audit/catalog web UIs into FastAPI gradually.
4. Fresh git repo; originals archived.
5. Unified Python floor: **3.12** (Proverka/Spravochnik use `StrEnum` / `datetime.UTC`).

## Phase 0 — repo & safety net  (DONE)
- Full history of the three originals archived as `git bundle` in
  `../S21_content_factory_archive/` (see its README). All three bundles verified
  "records a complete history".
- Removed nested `.git` from each subfolder and the empty parent `.git`.
- Fresh `git init -b main` at repo root; identity set.
- Merged root `.gitignore`; verified all `.env` files and `*.sqlite` are ignored
  (the catalog `Spravochnik/artifacts/skills_catalog.sqlite` stays on disk as the
  Phase-4 data source but is untracked).

### Baseline test status (green, before any restructuring)
Run with the existing generator venv (`Content_generator_ver1/.venv`, Python 3.12.8):

| Module | Tests | Result |
|--------|-------|--------|
| Content_generator_ver1 | 691 | passed (21.6s) |
| Proverka (`content-audit`) | 172 | passed (2.9s) |
| Spravochnik | 53 | passed (14.9s) |

Proverka & Spravochnik passed on the generator venv → dependency union largely
already satisfied; low integration risk.

### Env consolidation
- Root `.env` = union of the three module `.env` files (106 unique keys, **no value
  conflicts**; `POLZA_AI_API_KEY` shared by all three). Git-ignored.
- Root `.env.example` = generator template + appended Audit/Catalog blocks, using the
  **original** per-module var names for now (a single root `.env` feeds all three during
  transition); Phase 2 unifies/namespaces them under one `Settings`.
- Per-module `.env` files left in place (still read by each module until Phase 2 rewire).

## Phase 1 — unified package skeleton + generator moved  (DONE)
- Created `src/content_factory/` (src-layout) with root `pyproject.toml` (py3.12,
  `pythonpath=["src"]`, unified ruff/black/mypy/isort/pytest config).
- Relocated the generator:
  - `api/` → `src/content_factory/api/`
  - `content_gen/` → `src/content_factory/generation/`  (renamed)
  - `utils/` → `src/content_factory/utils/`
  - `config/` (model_registry.yaml) → `src/content_factory/config/`  (keeps `__file__` paths)
  - `didactics/` data bundle → `src/content_factory/didactics/`  (resolved by composer at package root)
- Support files to repo root: `migrations/`, `alembic.ini`, `tests/`, `static/`,
  `scripts/`, `evals/`, `examples/`, `thematic_blocks.json`, `run.py`.
- Docs → `docs/generator/`; design mockups / screenshots / Russian source dirs → `legacy/generator/`.
- Codemod: rewrote **555 import lines / 177 files** (`content_gen`→`content_factory.generation`,
  `api`→`content_factory.api`, `utils`→`content_factory.utils`) + quoted module strings
  (mock.patch targets, paused-codec type names) across 29 more files.
- Data-path fixes: `content_gen/` → `generation/` in 12 config yaml/json files (prompt paths);
  `project_paths.py` sibling depth `parents[2]`→`parents[4]`; `alembic.ini prepend_sys_path = . src`;
  `run.py` entrypoint `content_factory.api.main:app` + `src` on sys.path; `LLM_MODEL_REGISTRY`
  default path in `.env.example`.
- **Verification:** all **691 tests green** from repo root; `import content_factory.api.main`
  boots the FastAPI app (83 routes, Spravochnik mount + auditor route intact via patched
  `project_paths`).
- `content_audit.*` bridge in `api/routers/auditor.py` left intact (Proverka still a sibling;
  folded in Phase 3). `Content_generator_ver1/` now holds only git-ignored runtime dirs
  (`.venv`, caches) — kept temporarily as the working venv; delete once a root venv exists.

## Phase 2 — platform core (LLM + observability + exceptions)  (DONE)
- Created `src/content_factory/platform/` as the shared-core home, with placeholder
  subpackages `config/`, `cache/`, `prompts/`, `db/`, `domain/` (filled as the duplicated
  code from audit/catalog folds in during Phases 3–4 — deliberately not pre-built).
- Moved the genuinely-shared infrastructure into platform (it was the top cross-module
  duplication target):
  - `generation/llm/` → `platform/llm/`  (multi-provider gateway, model registry, structured runner)
  - `generation/observability.py` → `platform/observability.py`
  - `generation/exceptions.py` → `platform/exceptions.py`
- Left thin **re-export shims** at `generation/exceptions.py` and `generation/observability.py`
  so their existing importers (14 + 14) keep working unchanged; verified the shims re-export
  the *same* class objects (isinstance-safe). Shims flagged for removal in Phase 6.
- Rewired `llm` importers (22 files, absolute + relative) to `content_factory.platform.llm`.
  `platform/llm` is self-contained: its only intra-repo deps (`..exceptions`, observability)
  now resolve inside `platform`; `model_registry.py`'s `__file__`-relative path still lands on
  `content_factory/config/model_registry.yaml`.
- **Verification:** all **691 tests green**; app still boots (83 routes); `platform.llm`,
  `platform.observability`, `platform.exceptions` import cleanly.
- Unified `Settings` (pydantic-settings) intentionally deferred: the app currently reads
  `os.getenv` directly and there is no second consumer yet — it lands with the audit/catalog
  config merge so it is designed against real duplication rather than speculatively.

## Phase 3 — Proverka folded in as content_factory.audit  (DONE)
- `Proverka/src/content_audit/` → `src/content_factory/audit/`; rewired `content_audit` →
  `content_factory.audit` (46 files, all imports were absolute). `avatar-placeholder.jpg`
  moved into the package; `web_app.AVATAR_PATH` now `__file__`-relative.
- Tests `Proverka/tests/` → `tests/audit/` (172 tests). Added an autouse `_isolate_audit_env`
  fixture: `api.db.session` calls `load_dotenv()` at import, so the unified root `.env` leaks
  OpenRouter/AUTH keys into `os.environ`; the fixture clears them so the audit env/credentials
  tests stay hermetic (they passed alone but failed in the combined run — classic env bleed).
- **Dropped the sys.path + importlib bridge** in `api/routers/auditor.py`: `_load_auditor_web_app`
  and the domain/env/exporters/orchestrator block now use direct `from content_factory.audit import …`;
  removed `import importlib`, `ensure_import_path`, `proverka_src_root`; auditor `.env` read now
  uses `WORKSPACE_ROOT/.env`.
- Remaining Proverka assets (metrics gold corpus, adjudication/eval scripts, prompts json, docs)
  → `legacy/proverka/`. `Proverka/` now holds only git-ignored runtime dirs.
- **Verification:** unified suite **863 passed** (691 + 172); app boots (83 routes); audit package
  direct-imports; auditor router no longer references importlib.
- **Deferred to a cleanup slice:** audit still ships its own `openrouter.py` / `cache.py` / `env.py`.
  They are OpenAI-compatible and should become a provider adapter + shared cache under
  `platform/` — folded together with the catalog LLM client in Phase 4/6 so the unification is
  designed against both duplicates at once.

## Phase 4a — Spravochnik folded in as content_factory.catalog (code)  (DONE)
- `Spravochnik/spravochnik_intake/` → `src/content_factory/catalog/` (pipeline);
  `Spravochnik/viewer/` → `src/content_factory/catalog/viewer/`;
  `Spravochnik/sql/catalog_schema.sql` + `artifacts/` → into the catalog package.
- Rewired `spravochnik_intake` → `content_factory.catalog`, `viewer` →
  `content_factory.catalog.viewer` across src + tests; fixed the codemod's collateral hits
  on path-segment strings (viewer `INTAKE_SCHEMA_SQL`, test schema path).
- `.gitignore`: `*.sql` (a generator dump rule) was hiding the catalog DDL — added
  `!src/content_factory/**/sql/*.sql` so `catalog_schema.sql` / `new_tables.sql` are tracked.
- **Dropped the WSGI sys.path bridge:** `build_spravochnik_app` now imports the viewer directly
  (`from content_factory.catalog.viewer.app import create_app, DEFAULT_DB, DEFAULT_SUMMARY`);
  removed `ensure_import_path`/`spravochnik_root` from the curriculum-sync path too. `project_paths.py`
  rewritten: dead sibling-resolution helpers gone; `spravochnik_sqlite_path/summary_path` now point at
  `catalog/artifacts/` (env-overridable).
- Tests `Spravochnik/tests/` → `tests/catalog/` (53). Remaining Spravochnik assets (Excel КПшки,
  scripts, docs) → `legacy/spravochnik/`.
- **Verification:** unified suite **916 passed** (691 + 172 + 53); app boots (83 routes) with the
  Spravochnik mount + auditor route intact; catalog imports resolve.
- Catalog still runs on its **SQLite** file (`catalog/artifacts/skills_catalog.sqlite`); the WSGI
  viewer is still mounted (rewritten to FastAPI in Phase 5). The relational SQLite→Postgres merge
  is Phase 4b.

## Phase 4b — catalog SQLite -> Postgres (hybrid)  (SCHEMA DONE; DATA = user step)
Decisions: target DB = **Neon**; cutover = **hybrid** (canonical catalog in Postgres,
intake pipeline stays on SQLite).

- **Neon project created:** `s21-content-factory` (project `twilight-brook-84308101`,
  org Zheltenkov). Default branch `main` (`br-lively-feather-aj1i8slh`); dry-run branch
  `catalog-migration-dryrun` (`br-small-king-aje5agkp`).
- **Catalog schema ported to Postgres** under a dedicated `catalog` schema (clean separation,
  zero name clash with app tables): 28 tables + 2 views + 17 indexes, translated from the
  SQLite DDL (INTEGER→integer, REAL→double precision, CURRENT_TIMESTAMP defaults dropped since
  data carries values, refs schema-qualified). **Verified: 28 tables created on the dry-run
  branch** via Neon MCP.
- Source of truth in-repo: `src/content_factory/catalog/sql/catalog_schema_postgres.sql` +
  Alembic migration `migrations/versions/014_catalog_schema.py` (down_revision 013; upgrade
  op.execute's the DDL, downgrade drops the schema; parser verified → 49 statements).
- **Data-migration tool:** `scripts/migrate_catalog_to_postgres.py` (psycopg2 + execute_values,
  explicit ids, FK-safe parent-first order, single transaction, row-count self-check,
  `--truncate` guard). Catalog data volume: **36,267 rows** across the 28 tables (largest:
  indicator_level_cell 16,467, indicator_row 15,332, competency_skill 1,225, skill 1,199).
- **Environment limit:** sustained Postgres connections from this sandbox to Neon drop
  (`SSL SYSCALL error: EOF`) — reliable only for the initial handshake. Schema went in via the
  Neon MCP (server-side); bulk data load can't run from here and inlining 4.5 MB of INSERTs
  through MCP is not viable. **The data load is a one-command user step** (their machine reaches
  Neon fine). See follow-ups.

### Remaining in 4b (next slices)
- Replace the lossy JSON-blob mirror (`SpravochnikCatalogEntity` + `spravochnik_curriculum_sync`)
  with reads from the real `catalog.*` tables (generator side). Needs the data loaded to verify.
- Cut `DATABASE_URL` over to Neon once app schema + catalog are applied there.

## Phase 6 (partial) — Polza default + root docs  (DONE)
- **Unified default LLM provider = Polza** across all three modules. Generation and catalog
  already defaulted to Polza (`LLM_PROVIDER=polza`, `POLZA_AI_API_KEY`); audit now does too:
  `OpenRouterClient` default `base_url` → `https://polza.ai/api/v1/chat/completions`
  (`DEFAULT_BASE_URL`), and `POLZA_AI_API_KEY` is the first key fallback in the auditor router,
  `web_app`, and `cli`. Polza is OpenAI-compatible and proxies the same models
  (gpt-5.4-mini, perplexity/sonar, qwen/qwen3-coder). Tests mock at `requests.post`/pass keys
  explicitly, so all 172 audit tests stay green.
- Added a **root `README.md`** documenting the unified architecture, setup, routes, and the
  catalog data-load command (the repo had no root readme).
- **Verification:** 916 tests green; app boots (83 routes); `audit.openrouter.DEFAULT_BASE_URL`
  resolves to the Polza gateway.

### Phase 6 remaining (deferred, larger/risky)
- Physically merge the three LLM clients (audit `openrouter.py`, catalog `pipeline/llm.py`)
  into one `platform/llm` OpenAI-compatible client — needs updating their test mock boundaries;
  bounded but touches audit/catalog internals.
- Remove dead audit `http.server` (`AuditWebHandler`/`main`) — unused in the app (only the
  legacy console-script referenced it); safe but low priority.
- Replace the JSON-blob catalog mirror with real `catalog.*` reads — blocked on the Phase-4c
  data load.

## Phase 5.1 — catalog UI foundation + read-only pages on FastAPI  (DONE)
- New package `src/content_factory/catalog/web/`: `rendering.py` (module-level Jinja env
  mirroring the viewer's `create_app` render — same templates/filters/shared context, plus a
  `base` URL-prefix global), `deps.py` (`get_conn` per-request SQLite), `routers/pages.py`.
- Ported the purely-GET catalog pages to native FastAPI (no POST siblings, safe to split):
  `/competencies`, `/competencies/{id}`, `/profiles`, `/profiles/{id}` — reusing the viewer's
  existing data functions and templates unchanged (visual parity).
- `main.py`: native `StaticFiles` at `/app/spravochnik/static` + `include_router(pages)` are
  registered **before** the WSGI mount, so these paths are served natively and everything else
  falls through to the still-mounted viewer.
- Templates: added `{{ base }}` prefix to `base.html` (nav/static/wordmark) and the 4 ported
  pages' links; the WSGI env now sets `base=""` (PrefixRewrite still adds the prefix for mounted
  pages) — both render paths correct.
- Tests: `tests/catalog/test_web_pages.py` (6, TestClient, temp catalog SQLite, `DISABLE_AUTH`).
- **Verification:** 922 tests green (916 + 6); app boots (88 routes) with native catalog pages
  and the WSGI mount coexisting.
- Remaining slices: 5.2 catalog-admin, 5.3 intake, 5.4 reviews+up, 5.5 cutover (remove mount).
  Plan: [PHASE5_UI_MIGRATION_PLAN.md](PHASE5_UI_MIGRATION_PLAN.md).

## Phase 5.2 — catalog-admin on FastAPI (GET pages + POST/PRG forms)  (DONE)
- `catalog/web/routers/catalog_admin.py`: 15 native routes (GET + POST) for
  candidate-competencies, archive, artifact-templates, skillsets(+detail), groups(+detail),
  skills(detail). POST reads `await request.form()` (like the old `parse_post_data`),
  dispatches on `action`, and redirects 303 (PRG) — reusing all the viewer's mutation/query
  functions + intake `storage`/`competency_catalog`.
- Prefixed 41 internal links across the 8 `catalog_admin_*.html` templates with `{{ base }}`.
- `open_db` got a `check_same_thread` kwarg (default True keeps WSGI parity); `get_conn` opens
  with `check_same_thread=False` so FastAPI's threadpool→event-loop handoff is safe for the
  per-request SQLite connection.
- Tests: `tests/catalog/test_web_admin.py` (9) — GET pages, root redirect, group create+detail,
  skill create, 404.
- **Verification:** 931 tests green (922 + 9); app boots (103 routes). Remaining: 5.3 intake,
  5.4 reviews+up, 5.5 cutover.

### Phase 5.3 — intake native FastAPI
- `catalog/web/routers/intake.py`: 10 native routes covering the full intake workspace —
  GET `/intake` + job view `/intake/jobs/{id}`, POST `/intake` (multipart brief upload via
  Starlette `UploadFile` → legacy `UploadedFile`, same `load_brief_text` path), the JSON
  `/status` poll (identical field contract the inline `intake.html` JS depends on),
  the workflow POSTs (`next-step`/`build-dag`/`apply-catalog`/`candidate-decision`, the last
  returns JSON for `X-Requested-With: fetch`), `/plan.csv` export, and `/intake/jobs/clear`.
  All pipeline/data logic reuses the viewer functions unchanged; only transport differs.
- Prefixed ~24 internal links in `intake.html` with `{{ base }}` (incl. the JS `fetch()`
  polling URL) — shared template stays 1:1 with the WSGI viewer (`base=""`).
- Job queuing still goes through the legacy `queue_intake_job` (ThreadPoolExecutor) so the LLM
  pipeline behaviour is untouched. Tests patch it to a no-op (no real LLM calls).
- Tests: `tests/catalog/test_web_intake.py` (10) — empty workspace render, text-brief POST
  (create+queue), empty-brief 400, file-upload POST, `/status` JSON contract + 404, job detail
  render, clear redirect, plan.csv 404-without-plan + CSV export.
- **Legacy parity:** the WSGI viewer still serves every `/intake*` route unchanged. Run it
  standalone for side-by-side comparison:
  `python -m content_factory.catalog.viewer.app --db <catalog.sqlite> --summary <summary.json> --port 8010`.
- **Verification:** 941 tests green (931 + 10); app boots (113 routes; 10 native intake routes
  registered before the WSGI mount). Remaining: 5.4 reviews+up, 5.5 cutover.

### Phase 5.4 — reviews + curriculum-plan (УП) native FastAPI
- `catalog/web/routers/reviews.py`: 4 routes — GET `/reviews` (filters via query params),
  POST `/reviews` (status update, PRG preserving the active filters), POST
  `/reviews/build-dag` + `/reviews/apply-catalog` (brief-level actions that jump to the
  resulting intake job).
- `catalog/web/routers/up.py`: 12 routes — index, cleanup-empty, plan detail, CSV export
  (409 when the plan status is `invalid`), row create/edit/delete (edit re-renders 400 on
  `ValueError`), and the artifact-template proposal workflow (generate / save / accept / reject).
  `/rows/new` is declared before `/rows/{row_id}` so the literal segment isn't swallowed by
  the int converter; plan detail is declared last for the same reason.
- Prefixed all internal links in `reviews.html`, `up_index.html`, `up_detail.html`,
  `up_row_edit.html`, `up_template_proposals.html` with `{{ base }}` — shared 1:1 with WSGI.
- Tests: `tests/catalog/test_web_reviews_up.py` (12) — reviews render + filters, invalid-status
  404, PRG redirect with filters; up index/cleanup redirect, missing-plan 404 (detail/csv),
  delete redirect, row-new 404, plan detail render, CSV 409-on-invalid.
- **Verification:** 953 tests green (941 + 12); app boots (129 routes; 16 native reviews/up
  routes before the WSGI mount). Legacy WSGI still serves every route (shared templates,
  `base=""`). Remaining: 5.5 cutover (drop the WSGI mount / PrefixRewrite once profiles/
  competencies read-paths + any residual routes are all native).

## Open follow-ups for the user
- **Apply the unified schema to Neon** (from your machine, reliable network):
  `DATABASE_URL="<neon-direct-url>?sslmode=require" alembic upgrade head`  (applies 001–014).
- **Load the catalog data:**
  `python scripts/migrate_catalog_to_postgres.py --sqlite src/content_factory/catalog/artifacts/skills_catalog.sqlite --pg-url "<neon-url>?sslmode=require"`
  (prints per-table counts and a sqlite-vs-postgres row-count match check).
- Neon connection string (has a live password — keep in the git-ignored `.env`, do not commit).
- **Rotate secrets:** live API keys + a deploy password were present in
  `Proverka/.env` and `Spravochnik/.env` (now git-ignored, never committed here, but
  they lived in the original repos' history). Rotate them.
- Authorize the **Neon MCP** in an interactive session before the Phase-4 DB dry-run.
