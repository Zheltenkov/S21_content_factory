#!/usr/bin/env bash
# What the service says right after a release — without a model call or a session; of the projects only their number.
# Run as root on the host:
#
#   bash after_release.sh
#
# Nothing is changed and nothing here fails a release: every line is a look. «!!» marks what needs a person.
set -uo pipefail
ADDRESS=${PROJECT_LAB_ADDRESS:-https://77-110-123-227.nip.io/app/project-preparation}

echo "== after the release"
docker ps --filter name=cf-project-lab --format 'container: {{.Names}}  {{.Image}}  {{.Status}}'
echo "current -> $(readlink -f /opt/s21_project_lab/current)"

# The way a methodologist comes: through nginx and the address prefix. Without a session the API answers 401 and a
# page sends to the entrance (303) — anything else means the proxy does not reach the new container.
api=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$ADDRESS/api/config" || true)
page=$(curl -s -o /dev/null -m 15 -w '%{http_code}' -H 'Accept: text/html' "$ADDRESS/" || true)
echo "through the proxy, no session: api/config $api (expected 401), the page $page (expected 303)"
{ [ "$api" = "401" ] && [ "$page" = "303" ]; } || echo "!! the public address does not answer as expected: open $ADDRESS/ in a browser"

# The account of model calls is new in this release (core/usage.py). The server says in its log when it cannot write it.
# The log is read once into a variable: `docker logs | grep -q` under pipefail may answer «no» to a line that is there
# (grep leaves at the first match, docker logs dies on the closed pipe, the pipeline counts as failed).
LOG=$(docker logs cf-project-lab 2>&1 || true)
if grep -q "the account of model calls cannot be written" <<<"$LOG"; then
  echo "!! the account of model calls is NOT written:"
  grep "the account of model calls" <<<"$LOG" | tail -1
else
  echo "the account of model calls: $(docker exec cf-project-lab sh -c 'ls -ld /data/project-lab/usage' 2>&1)"
fi
docker exec cf-project-lab python -c '
import pathlib
root = pathlib.Path("/data/project-lab")
count = lambda folder, mask: len(list((root / folder).glob(mask)))
print("stored: runs", count("runs", "*.json"), "dialogues", count("runs/threads", "*.json"), "content drafts", count("content", "*.json"), "accounts", count("usage", "*.jsonl"))
' || echo "!! the container did not answer"
# Only the count and the lines that name an error: the lines after them may quote somebody's text.
errors=$(grep -c -E "Traceback|ERROR|Unreadable|not written" <<<"$LOG" || true)
echo "lines of the log that name an error, since this container started: $errors"
[ "$errors" = "0" ] || grep -E "Traceback|ERROR|Unreadable|not written" <<<"$LOG" | cut -c1-200 | tail -10
exit 0
