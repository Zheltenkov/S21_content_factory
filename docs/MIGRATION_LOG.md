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

## Open follow-ups for the user
- **Rotate secrets:** live API keys + a deploy password were present in
  `Proverka/.env` and `Spravochnik/.env` (now git-ignored, never committed here, but
  they lived in the original repos' history). Rotate them.
- Authorize the **Neon MCP** in an interactive session before the Phase-4 DB dry-run.
