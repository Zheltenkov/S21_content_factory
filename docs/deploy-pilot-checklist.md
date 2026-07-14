# Deploy — Pilot Checklist (methodologist-in-the-loop)

Target: `77.110.123.227` (Ubuntu 24.04, `sch21gen.aeza.network`). Product model is a
**controlled pilot with a methodologist in the loop** — not autonomous generation.

## Stack
- **app** — `content_factory.api.main:app` in Docker (editable install), single-process asyncio.
- **db** — `postgres:16` in Docker, named volume `cf_pgdata`.
- **nginx** — on host, TLS termination (self-signed for the IP pilot), proxy → `127.0.0.1:8000`.
- Config: `Dockerfile`, `deploy/docker-compose.yml`, `deploy/nginx-cf.conf`, `deploy/env.production.sample`.

## Pre-deploy decisions (locked for this pilot)
- [x] `GENERATION_WORKER_ENABLED=false` — durable STATE only (checkpoints + mark-interrupted on boot),
      no auto-resume. A hard restart mid-generation leaves an `interrupted` run that is NOT
      auto-resumed. Acceptable under human-in-the-loop; flip to `true` later to enable resume.
- [x] Registration + password reset restricted to `@21-school.ru` (default in `auth.py`).
- [x] Old DB dumped to a backup file before wipe, then fresh `postgres:16` + `alembic upgrade head`.
- [x] TLS: self-signed cert on `:443`, `AUTH_COOKIE_SECURE=true`.
- [x] LLM key: `POLZA_AI_API_KEY` reused from local `.env`.

## Deploy steps
1. **Backup** old DB: `docker exec content-pg pg_dump -U content_user content_generator | gzip > /root/backup_old_$(date +%F).sql.gz`.
2. **Remove old**: stop+disable `content-generator.service`; stop+rm `content-pg` container + old volume;
   `rm -rf /opt/Content_generator_ver1`; remove old nginx site.
3. **Ship code**: `git archive HEAD` tarball → `/opt/s21_content_factory` (clean, committed tree only).
4. **Server `.env`**: from `deploy/env.production.sample` with real POLZA key, random `JWT_SECRET_KEY`,
   random `POSTGRES_PASSWORD` (mirrored into `DATABASE_URL`).
5. **Up**: `docker compose -f deploy/docker-compose.yml up -d --build` (runs `alembic upgrade head` on boot).
6. **TLS + nginx**: self-signed cert to `/etc/ssl/certs/cf-selfsigned.crt` + key; install `deploy/nginx-cf.conf`;
   `nginx -t && systemctl reload nginx`.

## Post-deploy verification
- [ ] `docker compose ps` — `cf-db` healthy, `cf-app` up.
- [ ] Migration head: `docker exec cf-app alembic current` shows `021...`.
- [ ] `https://77.110.123.227/` serves (self-signed warning expected).
- [ ] Register `test@21-school.ru` → OK; register `x@gmail.com` → 400 domain rejected.
- [ ] `GENERATION_WORKER_ENABLED=false` in container env; `WORKFLOW_RECOVERY_ON_STARTUP=true`.
- [ ] `python scripts/production_check.py` passes (no auth bypass / insecure cookie / default secret).

## Post-pilot follow-ups (not blocking this deploy)
- Rotate the root password / switch to SSH key + disable password auth (root creds were shared in plaintext).
- Enable `ufw` (allow 22/80/443 only).
- Downstream baseline rubеж: УП #61 → 2-3 разнотипных проекта → hand-audit contract↔fact
  (see [[downstream-contract-gap]]).
- Real domain → Let's Encrypt (replace self-signed).
- Backup/restore rehearsal; consider a periodic `pg_dump` cron.
