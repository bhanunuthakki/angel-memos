#!/usr/bin/env bash
set -euo pipefail

if git grep -I -l -E '(/Users/|/home/|[A-Za-z]:\\Users\\)' -- ':!scripts/check_public_tree.sh'; then
  echo "Personal absolute path found in tracked content" >&2
  exit 1
fi
if git grep -I -l -E "(-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----|AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|xox[baprs]-[A-Za-z0-9-]{10,}|(api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password)[[:space:]]*[=:][[:space:]]*[\"']?[A-Za-z0-9_./+=-]{16,}[\"']?)" -- ':!scripts/check_public_tree.sh' ':!scripts/test_public_tree_guard.sh'; then
  echo "Credential material found in tracked content" >&2
  exit 1
fi
if git grep -I -l -i -E '(cost[ _-]*basis|account[ _-]*balance|share[ _-]*quantity|shares|quantity|account[ _-]*(id|number))[[:space:]\"'"'"']*[:=][[:space:]\"'"'"']*[$]?[0-9A-Za-z]' -- '*.md' '*.json' '*.csv' '*.tsv' '*.yaml' '*.yml' '*.txt' ':!scripts/check_public_tree.sh' ':!scripts/test_public_tree_guard.sh'; then
  echo "Personal account-level fact found in tracked content" >&2
  exit 1
fi
if git grep -I -l -i -E '(position[ _-]*(value|size))[[:space:]\"'"'"']*[:=][[:space:]\"'"'"']*([$][0-9]|[0-9][0-9,.]*[[:space:]]*(USD|dollars?))' -- '*.md' '*.json' '*.csv' '*.tsv' '*.yaml' '*.yml' '*.txt' ':!scripts/check_public_tree.sh' ':!scripts/test_public_tree_guard.sh'; then
  echo "Currency-denominated position value found in tracked content" >&2
  exit 1
fi
if git grep -I -l -i -E '(bhanu|nuthakki)[A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -- ':!scripts/check_public_tree.sh'; then
  echo "Personal email found in tracked content" >&2
  exit 1
fi
if git ls-files | grep -E '(^|/)(credentials\.json|token\.json|.*\.db|.*\.sqlite3?|.*\.pem|.*\.key)$'; then
  echo "Credential or local-data artifact is tracked" >&2
  exit 1
fi
if git ls-files | grep -E '(^|/)(Evaluation|Portfolio)/|(^|/)(memo_private|memo_public|decision|score_report|research_memo)\.(md|json)$|\.(pdf|xlsx|docx|zip)$'; then
  echo "Confidential deal-room or generated memo material is tracked" >&2
  exit 1
fi
