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

## 6. Post-deploy cleanup

Done (branch `catalog-ui-cleanup`): removed the SQLite-only runtime-migration machinery now
that the catalog is Postgres/alembic-managed — deleted `catalog/viewer/migrations.py`
(`apply_runtime_migrations` / `migrate_review_queue_entity_types` / `apply_sql_migration`) and
the SQLite `.sql` sources (`catalog_schema.sql`, `new_tables.sql`). `ensure_intake_runtime_schema`
is trimmed to its Postgres-real work (per-DB review-link repair + stale-job recovery); the dead
18-query schema check and no-op migration calls are gone.

Still present intentionally:

- `skills_catalog.sqlite` — **keep** as the cold re-seed backup (gitignored, never read at
  runtime). Delete only if you accept losing the re-migration source for
  `scripts/migrate_catalog_to_postgres.py`.

## 7. Durable generation worker (survive restart/deploy)

**What / why.** Content generation is long-running (multi-step LLM calls). It runs in the
web process, so a restart, deploy, or crash mid-run would otherwise lose the in-flight
generation — the user waits for a result that never arrives. Per-node **checkpoints** are
always persisted to Postgres (`generation_workflow_states` + `_checkpoints`); the durable
worker adds **recovery** and **lease-based execution** on top so a run survives a restart.

Three pieces (all merged, all behind one flag):

1. **Lease** — a run is claimed by a worker with a time-boxed lease + heartbeat; a dead
   worker's lease is reclaimed (requeue with retries, or fail once exhausted).
2. **Recovery poller** — on boot, workflows the previous process left active are marked
   `interrupted`, then reclaimed and **auto-resumed from their last checkpoint** (bounded to
   10 per boot).
3. **Lease-based start** — new generations start under a lease too, so a crash on a fresh
   run also leaves recoverable checkpoints.

**How to enable (prod).**

```bash
# in the prod env (see .env.example)
WORKFLOW_RECOVERY_ON_STARTUP=true   # default true — keep on (feeds the recovery poller)
GENERATION_WORKER_ENABLED=true      # default false — turns on lease dispatch + auto-resume
# then restart the app process
```

- **Default OFF**: with `GENERATION_WORKER_ENABLED` unset/false the legacy in-process path
  is unchanged, byte-for-byte. Enabling and rolling back is a single env flip + restart — no
  code change, no redeploy.
- The lease columns ship in migration `019_generation_lease` (idempotent). Run
  `alembic upgrade head` as usual; the flag can be enabled independently afterward.
- **Scope**: this makes generation **restart-safe and reclaimable**. Execution is still
  single-process asyncio (not a separate worker process yet); a boot resume storm is bounded
  by the 10-per-boot limit. A separate worker process / periodic (not just startup) recovery
  loop / concurrency cap are possible follow-ups.

**Verify after enabling.** Start a generation, restart the app mid-run, confirm the run
resumes from its last checkpoint (status goes `interrupted` → `resuming` → completes) and the
startup log shows `♻️ Generation worker: dispatched=N ...`.
