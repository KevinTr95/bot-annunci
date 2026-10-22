#!/usr/bin/env bash
# Stato del bot (profilo + annunci già visti) nel branch `bot-state` del repo.
#   scripts/state.sh load   → scarica il branch nella cartella data/
#   scripts/state.sh save   → pubblica data/ come unico commit del branch (force push:
#                             la storia non serve e così il repo non cresce)
set -euo pipefail
BRANCH=bot-state

case "${1:-}" in
  load)
    mkdir -p data
    if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
      git fetch -q --depth=1 origin "$BRANCH"
      git archive FETCH_HEAD | tar -x -C data
      echo "stato caricato"
    else
      echo "nessuno stato salvato: primo avvio"
    fi
    ;;
  save)
    export GIT_INDEX_FILE="${RUNNER_TEMP:-/tmp}/bot-state.index"
    export GIT_AUTHOR_NAME=job-bot GIT_AUTHOR_EMAIL=job-bot@users.noreply.github.com
    export GIT_COMMITTER_NAME=job-bot GIT_COMMITTER_EMAIL=job-bot@users.noreply.github.com
    rm -f "$GIT_INDEX_FILE"
    git --work-tree=data add -A -f .
    tree=$(git write-tree)
    commit=$(git commit-tree "$tree" -m "stato $(date -u +%FT%TZ)")
    git push -q -f origin "$commit:refs/heads/$BRANCH"
    echo "stato salvato"
    ;;
  *)
    echo "uso: $0 load|save" >&2
    exit 1
    ;;
esac
