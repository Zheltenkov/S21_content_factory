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

## Open follow-ups for the user
- **Rotate secrets:** live API keys + a deploy password were present in
  `Proverka/.env` and `Spravochnik/.env` (now git-ignored, never committed here, but
  they lived in the original repos' history). Rotate them.
- Authorize the **Neon MCP** in an interactive session before the Phase-4 DB dry-run.
