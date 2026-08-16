#!/usr/bin/env bash
# One-command development setup: install dev dependencies and enable the
# pre-commit hook so every commit gets focused validation routing.
#
# Usage:
#   scripts/setup-dev.sh            # pip install -e ".[dev]" + enable hook
#   scripts/setup-dev.sh --hook-only  # only enable the pre-commit hook
#   scripts/setup-dev.sh --help
#
# The pre-commit hook (scripts/git-hooks/pre-commit) runs focused tests based
# on staged file types; skip it for a single commit with `git commit --no-verify`.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

usage() {
  cat <<USAGE
Usage: scripts/setup-dev.sh [--hook-only]

Install dev dependencies (pip install -e ".[dev]") and enable the pre-commit
hook via \`git config core.hooksPath scripts/git-hooks\`.

Options:
  --hook-only  Skip pip install; only enable and verify the pre-commit hook.
  --help       Show this help.
USAGE
}

install_dev_deps() {
  echo "[setup-dev] Installing dev dependencies (pip install -e \".[dev]\")..."
  pip install -e ".[dev]"
}

enable_precommit_hook() {
  echo "[setup-dev] Enabling pre-commit hook (git config core.hooksPath scripts/git-hooks)..."
  git config core.hooksPath scripts/git-hooks

  local actual
  actual="$(git config core.hooksPath)"
  if [[ "$actual" != "scripts/git-hooks" ]]; then
    echo "[setup-dev] ERROR: core.hooksPath is '$actual', expected 'scripts/git-hooks'" >&2
    return 1
  fi
  if [[ ! -x scripts/git-hooks/pre-commit ]]; then
    echo "[setup-dev] ERROR: scripts/git-hooks/pre-commit missing or not executable" >&2
    return 1
  fi

  echo "[setup-dev] Pre-commit hook active (core.hooksPath=$actual)."
}

case "${1:-}" in
  --hook-only) enable_precommit_hook ;;
  -h|--help|help) usage ;;
  "")
    install_dev_deps
    enable_precommit_hook
    ;;
  *)
    echo "unknown option: $1" >&2
    usage >&2
    exit 2
    ;;
esac
