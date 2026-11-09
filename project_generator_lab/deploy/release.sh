#!/usr/bin/env bash
# Publish a release of «Подготовка проекта» on the production host. Run as root on the host:
#
#   bash release.sh /tmp/s21-project-lab-<REL>.tgz <REL>
#
# Steps, each stops the script on failure before anything is switched:
#   0. ask the running service whether somebody's job is going on right now: the switch would cut it (see below);
#   1. unpack into /opt/s21_project_lab/releases/<REL> (a release directory is never overwritten);
#   2. release.env from the current release with a new image tag;
#   3. back up the data volume (runs, threads, content drafts) to /opt/s21_project_lab/backups/<REL>;
#   4. build the image on the verified base image;
#   5. pre-flight inside the new image: libraries, production entrypoint, every stored run and draft readable; then
#      a free run of the service on the libraries of that image (smoke.py: no model, no network, its own folders);
#   6. switch the container, wait for it, check that the API answers 401 without a session;
#   7. only then move the `current` link. The main application, its database, nginx and the menu are not touched.
# Rollback: printed at the end; the data volume is never replaced by the backup automatically.
set -euo pipefail

ARCHIVE=${1:?usage: release.sh <archive.tgz> <release-id>}
REL=${2:?usage: release.sh <archive.tgz> <release-id>}
# The id goes into paths, the image tag and a sed expression: only a plain id is accepted.
[[ "$REL" =~ ^[0-9]{8}-[0-9]{2}$ ]] || { echo "release id must look like 20261001-01, got: $REL"; exit 1; }
ROOT=/opt/s21_project_lab
PROJECT=project-lab
VOLUME=project-lab_project_lab_data
PREV=$(readlink -f "$ROOT/current")
DIR="$ROOT/releases/$REL"
BACKUP="$ROOT/backups/$REL"
compose() { docker compose --project-name "$PROJECT" --env-file "$1/release.env" -f "$1/project_generator_lab/deploy/compose.yml" "${@:2}"; }

echo "== previous release: $PREV"
[ -e "$DIR" ] && { echo "release $REL already exists: choose a new id"; exit 1; }

# The switch restarts the service, and a job going on at that moment is cut: a preparation becomes «interrupted», a
# content step is offered again. Nothing stored is lost, but its model calls are paid for nothing and somebody is
# waiting for the answer right now. So the script looks first — before anything is created on the host — and stops
# when there is such a job. ALLOW_ACTIVE=1 publishes anyway. Only ids are printed: the texts are the methodologists'.
# A job that begins while the image is being built (a minute or two) is not seen; it is offered again all the same.
echo "== work in progress on the running service"
ACTIVE=$(docker exec cf-project-lab python -c '
import json, pathlib
root = pathlib.Path("/data/project-lab")
found = []
for kind, folder, going in (("preparation", "runs", lambda d: d.get("status") == "running"), ("content", "content", lambda d: bool(d.get("busy"))),
                            ("evaluation", "content/evaluations", lambda d: d.get("state") == "running")):
    for path in sorted((root / folder).glob("*/*.json" if kind == "evaluation" else "*.json")):
        try:
            if going(json.loads(path.read_text(encoding="utf-8"))):
                found.append(kind + " " + path.stem)
        except (ValueError, OSError, AttributeError):
            pass
print("\n".join(found))
') || { echo "could not ask the running container (stopped?): going on without this check"; ACTIVE="not checked"; }
# A chat answer is a model call inside a request: it leaves no mark in the files, only a line in the log when it ends.
echo "model calls ended in the last 3 minutes: $(docker logs --since 3m cf-project-lab 2>&1 | grep -c model_call || true) (a chat answer in flight is not seen by this look)"
if [ "$ACTIVE" = "not checked" ]; then
  echo "not checked"
elif [ -n "$ACTIVE" ]; then
  echo "$ACTIVE"
  if [ "${ALLOW_ACTIVE:-0}" != "1" ]; then
    echo "!! these jobs are going on right now and the switch would cut them. Nothing has been changed on the host."
    echo "   Run the release later, or with ALLOW_ACTIVE=1 to publish anyway (the cut jobs are offered again)."
    exit 3
  fi
  echo "ALLOW_ACTIVE=1: publishing anyway"
else
  echo "none"
fi
sha256sum "$ARCHIVE"
# The run before the switch (smoke.py) answers for the models by the fixtures of the tests: an archive without them
# would fail there, with the release folder already made.
LISTING=$(tar -tzf "$ARCHIVE")
for needed in project_generator_lab/smoke.py project_generator_lab/tests/content/fixtures.py project_generator_lab/deploy/compose.yml; do
  grep -qxF "$needed" <<<"$LISTING" || { echo "archive has no $needed"; exit 1; }
done
mkdir -p "$DIR" "$BACKUP"
# Backups hold every methodologist's materials: root only. (Not a global umask: the image build copies the release
# files, and the container user must be able to read them.)
chmod 700 "$BACKUP"
tar -xzf "$ARCHIVE" -C "$DIR"
[ -d "$DIR/project_generator_lab" ] || { echo "archive has no project_generator_lab/"; exit 1; }
[ ! -e "$DIR/project_generator_lab/data" ] || { echo "archive contains data/: local runs must not reach production"; exit 1; }
sed "s/^PROJECT_LAB_IMAGE=.*/PROJECT_LAB_IMAGE=s21-project-lab:$REL/" "$PREV/release.env" > "$DIR/release.env"
grep -q "^PROJECT_LAB_IMAGE=s21-project-lab:$REL$" "$DIR/release.env"
BASE=$(grep '^PROJECT_LAB_BASE_IMAGE=' "$DIR/release.env" | cut -d= -f2-)
echo "== base image: $BASE"

echo "== backup of $VOLUME"
docker run --rm --user 0:0 --entrypoint tar -v "$VOLUME":/data:ro -v "$BACKUP":/backup "$BASE" czf /backup/project_lab_data.tgz -C /data .
docker inspect cf-project-lab > "$BACKUP/container-before.json"
cp "$PREV/release.env" "$BACKUP/release-before.env"
chmod 600 "$BACKUP"/*
ls -la "$BACKUP"

echo "== build"
compose "$DIR" build

echo "== pre-flight in the new image"
docker run --rm --entrypoint python -e PROJECT_LAB_AUTH_URL=http://cf-app:8000/api/v1/auth/me \
  -e PROJECT_LAB_RUNS_DIR=/data/project-lab/runs -e PROJECT_LAB_CONTENT_DIR=/data/project-lab/content \
  -e PROJECT_LAB_USAGE_DIR=/data/project-lab/usage \
  -v "$VOLUME":/data/project-lab:ro "s21-project-lab:$REL" -c '
import json, pathlib
import jinja2, yaml, httpx, docx, openpyxl, pypdf, dotenv, openai  # noqa: F401 - the content stage needs them
import project_generator_lab.production  # noqa: F401 - the entrypoint with the session guard
from project_generator_lab.concept.models import Run
from project_generator_lab.concept.thread import Thread
from project_generator_lab.content.models import ContentDraft
from project_generator_lab.core.contracts import STORED_SCHEMA
from project_generator_lab.core.storage import parse_stored
from project_generator_lab.app import PROMPT_VERSION, CONTENT_VERSION
root = pathlib.Path("/data/project-lab")
# Read as the service reads them (a file of a newer release without the fields this one does not know).
runs = [parse_stored(Run, p.read_text(encoding="utf-8")) for p in (root / "runs").glob("*.json")]
threads = [parse_stored(Thread, p.read_text(encoding="utf-8")) for p in (root / "runs" / "threads").glob("*.json")]
drafts = [p for p in (root / "content").glob("*.json")] if (root / "content").exists() else []
old = []
for p in drafts:
    try:
        parse_stored(ContentDraft, p.read_text(encoding="utf-8"))
    except Exception:
        old.append(p.name)  # an older content schema is set aside by the app on start, not rewritten
print(f"rules {PROMPT_VERSION}, content {CONTENT_VERSION}, stored schema {STORED_SCHEMA}: runs {len(runs)}, threads {len(threads)}, drafts {len(drafts)}, older drafts {len(old)}")
# Uploads are parsed in a child interpreter under memory and CPU limits: it must start and parse in this image.
from project_generator_lab.core import isolated
from project_generator_lab.concept.ingestion import extract_material
import pypdf
parsed = isolated.run(extract_material, "check.md", "# Проверка\nтекст материала".encode())
assert isolated.available() and parsed.passages, "isolated parsing does not work in this image"
print(f"isolated parsing ok (limit {isolated.MEMORY_MB} MB, {isolated.CPU_SECONDS} s), pypdf {pypdf.__version__}")
'

# The image has its own Python and libraries, not the ones the tests ran on: the route of a content draft, the session
# guard and the account of model calls are run on them before the switch (smoke.py). As the service runs: the image
# read-only, /tmp in memory. No network at all — the models, the auth service and the pages are answered in-process —
# and no volume: nothing of the service can be read, written or paid for by this run. It takes seconds; ten minutes is
# the limit after which it is taken for hung.
echo "== smoke run in the new image"
timeout 600 docker run --rm --network none --read-only --tmpfs /tmp:size=64m,mode=1777 --entrypoint python "s21-project-lab:$REL" -m project_generator_lab.smoke \
  || { echo "!! the smoke run failed in the new image: the service is NOT switched and still runs $PREV"
       echo "   The release folder $DIR is made: publish the fix under a new id, or remove the folder to use this id again."; exit 1; }

echo "== switch"
compose "$DIR" up -d --no-build
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8011/api/config || true)
  [ "$code" = "401" ] && break
  sleep 2
done
echo "api/config without a session: $code (expected 401)"
docker logs --tail 30 cf-project-lab
if [ "$code" != "401" ]; then
  echo "!! the new release does not answer as expected: rolling back to $PREV"
  compose "$PREV" up -d --no-build
  exit 1
fi
ln -sfn "$DIR" "$ROOT/current-next" && mv -Tf "$ROOT/current-next" "$ROOT/current"
echo "== current -> $(readlink -f "$ROOT/current")"
cat <<EOF
Rollback (code only; the data volume stays as it is):
  docker compose --project-name $PROJECT --env-file $PREV/release.env -f $PREV/project_generator_lab/deploy/compose.yml up -d --no-build
  ln -sfn $PREV $ROOT/current-next && mv -Tf $ROOT/current-next $ROOT/current
Data backup: $BACKUP/project_lab_data.tgz
EOF
