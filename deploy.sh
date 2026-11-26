#!/usr/bin/env bash
#
# deploy.sh - Deploy the StockGame project to the main branch.
#
# Stages all changes, commits them (unless the tree is already clean), syncs
# with origin/main, and pushes. The push to main triggers the GitHub Actions
# workflow (.github/workflows/deploy.yml), which rolls the change out to
# production over SSH.
#
# Usage:
#   ./deploy.sh                       # commit with an auto-generated message
#   ./deploy.sh "feat: my change"     # commit with a custom message
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BRANCH="main"
REMOTE="origin"
COMMIT_MSG="${1:-deploy: update $(date -u +'%Y-%m-%d %H:%M:%S UTC')}"

if ! command -v git >/dev/null 2>&1; then
  echo "Error: git is not installed or not on PATH." >&2
  exit 1
fi

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "Error: $SCRIPT_DIR is not a git repository." >&2
  exit 1
fi

CURRENT_BRANCH="$(git symbolic-ref --short HEAD)"
if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  echo "Switching from '$CURRENT_BRANCH' to '$BRANCH'..."
  git checkout "$BRANCH"
fi

echo "Staging changes..."
git add -A

if git diff --cached --quiet; then
  echo "No changes to commit; pushing current $BRANCH state."
else
  echo "Committing: $COMMIT_MSG"
  git commit -m "$COMMIT_MSG"
fi

echo "Syncing with $REMOTE/$BRANCH..."
git fetch "$REMOTE" "$BRANCH"
git pull --rebase "$REMOTE" "$BRANCH"

echo "Pushing to $REMOTE/$BRANCH..."
git push "$REMOTE" "$BRANCH"

echo "Deployment pushed. GitHub Actions will deploy to production."
