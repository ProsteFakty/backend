#!/usr/bin/env bash
# Deploy on the server: move the checkout to a commit, then reload/restart/rebuild only what changed.
#
#   ./deploy.sh          latest origin/main
#   ./deploy.sh <sha>    that commit (must be on origin/main); an older sha rolls back
#
# Leaves the checkout on a detached HEAD, so don't `git pull` by hand: run ./deploy.sh.
# Run by .github/workflows/deploy.yml through SSM; its "deploy:" / "action:" lines go into the job summary.
set -euo pipefail

# Everything is inside main() so bash has read the whole script before git checkout replaces this file.
main() {
  cd "$(dirname "$0")"

  if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "ERROR: working tree has local changes, refusing to deploy:" >&2
    git status --short --untracked-files=no >&2
    exit 1
  fi

  local old new target changed
  old=$(git rev-parse HEAD)
  git fetch --quiet origin main

  if [ -n "${1:-}" ]; then
    target=$(git rev-parse --verify --quiet "$1^{commit}") || { echo "ERROR: unknown commit: $1" >&2; exit 1; }
    if ! git merge-base --is-ancestor "$target" origin/main; then
      echo "ERROR: $target is not on origin/main" >&2
      exit 1
    fi
  else
    target=$(git rev-parse origin/main)
  fi

  echo "deploy: ${old:0:12} -> ${target:0:12}"
  if [ "$old" = "$target" ]; then
    echo "action: none (already at ${target:0:12})"
    return
  fi

  git checkout --quiet --detach "$target"
  new=$(git rev-parse HEAD)
  # old vs new in either direction, so a rollback across a requirements change rebuilds too.
  changed=$(git diff --name-only "$old" "$new")

  if echo "$changed" | grep -qE '^Caddyfile$'; then
    echo "action: reload caddy"
    docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile
  fi

  if echo "$changed" | grep -qE '^docker-compose\.yml$'; then
    echo "action: rebuild all (docker-compose.yml changed)"
    docker compose up -d --build
  elif echo "$changed" | grep -qE '^(requirements.*\.txt|Dockerfile)$'; then
    echo "action: rebuild backend (dependencies changed)"
    docker compose up -d --build backend
  else
    echo "action: restart backend"
    docker compose restart backend
  fi
}

main "$@"
