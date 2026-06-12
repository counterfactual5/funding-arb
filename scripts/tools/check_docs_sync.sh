#!/usr/bin/env bash
# Regenerate docs/*.md from web content and fail if the tree is out of date.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
npx --yes tsx scripts/tools/export_docs_md.mts >/dev/null
if ! git diff --quiet -- docs/; then
  echo "docs/ is out of sync with web/src/content/docs — run: npx tsx scripts/tools/export_docs_md.mts" >&2
  git diff --stat -- docs/ >&2
  exit 1
fi
echo "docs/ in sync"
