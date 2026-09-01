#!/usr/bin/env bash
set -euo pipefail

if git grep -I -l -E '(/Users/|/home/|[A-Za-z]:\\Users\\)' -- ':!scripts/check_public_tree.sh'; then
  echo "Personal absolute path found in tracked content" >&2
  exit 1
fi
if git ls-files | grep -E '(^|/)(credentials\.json|token\.json|.*\.db|.*\.sqlite|.*\.pem|.*\.key)$'; then
  echo "Credential or local-data artifact is tracked" >&2
  exit 1
fi
if git ls-files | grep -E '(^|/)(Evaluation|Portfolio)/|(^|/)(memo_private|memo_public|decision|score_report|research_memo)\.(md|json)$|\.(pdf|xlsx)$'; then
  echo "Confidential deal-room or generated memo material is tracked" >&2
  exit 1
fi
