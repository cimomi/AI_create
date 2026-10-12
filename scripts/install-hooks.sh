#!/usr/bin/env bash
# Enable the repository's security hooks. core.hooksPath is local
# configuration and is not distributed with the repository, so this has to be
# run once on every fresh clone.
set -euo pipefail
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
git config core.hooksPath .githooks
chmod +x .githooks/* scripts/*.sh scripts/*.py 2>/dev/null || true
echo "Security hooks enabled: core.hooksPath = .githooks"
echo "  pre-commit  fast check before committing (no history scan)"
echo "  pre-push    full audit before pushing (whole history)"
