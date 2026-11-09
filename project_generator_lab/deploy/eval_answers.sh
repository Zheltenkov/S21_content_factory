#!/usr/bin/env bash
# Chat scenarios of the concept chat (free-form answers, «поясни вопрос», off-topic, methodology questions) on a copy of a production concept thread, in a one-off container of the
# release image. The data volume is mounted read-only and the running service is not touched: the scenarios work on
# a copy in the container's /tmp, and the container is removed afterwards. Run as root on the host:
#
#   bash eval_answers.sh <REL> <thread-id> [runs]
#
# The thread's texts go to the same model gateway the service already uses; the report is printed here.
set -euo pipefail

REL=${1:?usage: eval_answers.sh <release-id> <thread-id> [runs]}
THREAD=${2:?usage: eval_answers.sh <release-id> <thread-id> [runs]}
RUNS=${3:-3}
ROOT=/opt/s21_project_lab
VOLUME=project-lab_project_lab_data
ENV_FILE=$(grep '^PROJECT_LAB_ENV_FILE=' "$ROOT/current/release.env" | cut -d= -f2-)
[ -f "$ENV_FILE" ] || { echo "no env file: $ENV_FILE"; exit 1; }
[[ "$THREAD" =~ ^[0-9a-f-]{36}$ ]] || { echo "thread id expected, got: $THREAD"; exit 1; }
[[ "$RUNS" =~ ^[1-9]$ ]] || { echo "runs: 1..9"; exit 1; }

echo "== free-form answers: release $REL, thread $THREAD, $RUNS run(s) each"
docker run --rm --entrypoint sh --env-file "$ENV_FILE" \
  -e PROJECT_LAB_RUNS_DIR=/data/project-lab/runs -e PROJECT_LAB_CONTENT_DIR=/data/project-lab/content \
  -v "$VOLUME":/data/project-lab:ro "s21-project-lab:$REL" -c \
  "python -m project_generator_lab.chat_eval --draft $THREAD --only free_answer free_answers explain concept_joke concept_method concept_question --runs $RUNS --out /tmp/eval && cat /tmp/eval/*/report.md"
