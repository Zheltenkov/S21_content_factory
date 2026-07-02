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

## Open follow-ups for the user
- **Rotate secrets:** live API keys + a deploy password were present in
  `Proverka/.env` and `Spravochnik/.env` (now git-ignored, never committed here, but
  they lived in the original repos' history). Rotate them.
- Authorize the **Neon MCP** in an interactive session before the Phase-4 DB dry-run.
