# Deploy — catalog on Postgres (SQLite cutover)

The catalog (Spravochnik) now runs only on Postgres. Deploying the cutover is **schema first,
then data, then restart** — enforced by `scripts/deploy_catalog_pg.py`.

## 1. Merge the branches (GitHub)

Order (each is stacked on `catalog-pg-cutover`):

1. `catalog-pg-cutover` → `main` — the cutover epic.
2. `didactic-severity-contract` → `main` — gate severity fix.
3. `freeze-migrations` → `main` — sha256-frozen migrations + 017 downgrade fix.
4. `deploy-runbook` → `main` — this doc + the deploy script.

After #1 merges, re-target the follow-up PRs at `main` if GitHub shows the cutover commits in
their diff.

## 2. Environment (prod host)

- `DATABASE_URL` (or `CATALOG_DATABASE_URL`) → the Postgres instance. Use `127.0.0.1`, not
  `localhost` (the container listens IPv4; Windows resolves localhost to `::1` first).
- `CATALOG_DB` — no longer needed; it defaults to `postgres`. Setting `sqlite` is unsupported.
- Do **not** commit secrets; `.env` stays gitignored.

## 3. Run the deploy

```bash
git pull                                   # main with the merged cutover
python scripts/deploy_catalog_pg.py        # alembic upgrade head -> migrate --truncate -> verify
# then restart the app process
```

`deploy_catalog_pg.py` enforces the strict order and refuses to finish unless
`catalog.skill_group` / `catalog.indicator` exist and the id sequences advanced past the copied
rows. Re-runnable: `alembic` no-ops at head, `migrate --truncate` reloads.

> ⚠️ Never run the data migrate before `alembic upgrade head`. Without migration **017** the
> canonical tables have no IDENTITY and `skill_group`/`indicator` don't exist, so every
> catalog-admin / ingest write fails with a NOT NULL id violation.

Flags: `--verify-only` (just the post-deploy checks), `--no-truncate` (append instead of reload).

## 4. Verify (after restart)

- Pages return 200: `/app/spravochnik/{competencies,reviews,up,intake,catalog-admin/groups,catalog-admin/skillsets}` and `/app/auditor`.
- Write path: create a group / skill in catalog-admin (this is what the cutover repaired).

## 5. Rollback

- Schema: `alembic downgrade 016` — removes 017's IDENTITY from the canonical + curriculum_plan
  tables and drops `skill_group`/`indicator`; the migration-016 working tables keep their
  IDENTITY (validated). Then redeploy the previous app revision.
- The original catalog data is preserved in `src/content_factory/catalog/artifacts/skills_catalog.sqlite`
  (gitignored) — the migrate source, kept as a cold backup.

## 6. Post-deploy cleanup (GATED — only after prod is confirmed on PG)

Still present intentionally until prod is stable:

- `skills_catalog.sqlite` — **keep** as the cold re-seed backup (gitignored, never read at
  runtime). Delete only if you accept losing the re-migration source.
- SQLite `.sql` (`catalog_schema.sql`, `new_tables.sql`) + the dead `apply_runtime_migrations` /
  `ensure_intake_runtime_schema` no-op path — safe to remove in a follow-up once PG is confirmed.
