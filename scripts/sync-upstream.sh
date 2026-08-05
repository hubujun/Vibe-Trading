#!/usr/bin/env bash
#
# Sync this fork with HKUDS/Vibe-Trading and replay local work on top.
#
# WHY THIS SCRIPT EXISTS
# ----------------------
# Upstream rewrites published history (it force-pushes `main`). On 2026-08-05 a
# plain `git pull` reported 1187 upstream-only vs 1036 "local-only" commits even
# though this fork had authored *zero* commits — every one of those 1036 was an
# upstream commit whose SHA had changed. Merging that is meaningless work.
#
# So this repo keeps a strict split:
#
#   main            an exact, read-only MIRROR of upstream/main. Never commit
#                   here. Because nothing local lives on it, it can always be
#                   safely `reset --hard` onto whatever upstream now claims.
#   dev/local-work  every local commit, rebased onto the mirror on each sync.
#
# INVARIANT (this is the important bit)
#   The work branch is always based on the *current* tip of `main`.
# That is what lets us rebase with `--onto`, replaying only local commits. A
# plain `git rebase main` would instead use the true merge base (~Apr 2026) and
# try to replay upstream's own rewritten commits back on top of upstream.
#
# USAGE
#   scripts/sync-upstream.sh            # sync + rebase local work
#   DRY_RUN=1 scripts/sync-upstream.sh  # report what would happen, change nothing
#
set -euo pipefail

UPSTREAM_REMOTE=${UPSTREAM_REMOTE:-upstream}
MIRROR_BRANCH=${MIRROR_BRANCH:-main}
WORK_BRANCH=${WORK_BRANCH:-dev/local-work}
DRY_RUN=${DRY_RUN:-}

die() { printf 'error: %s\n' "$1" >&2; exit 1; }
note() { printf '\033[1m==>\033[0m %s\n' "$1"; }

cd "$(git rev-parse --show-toplevel)"

git remote get-url "$UPSTREAM_REMOTE" >/dev/null 2>&1 \
  || die "no '$UPSTREAM_REMOTE' remote. Add it:
  git remote add $UPSTREAM_REMOTE https://github.com/HKUDS/Vibe-Trading.git"

git show-ref --verify --quiet "refs/heads/$WORK_BRANCH" \
  || die "work branch '$WORK_BRANCH' does not exist"

# A rebase rewrites the working tree; refuse to start from a dirty one.
[[ -z "$(git status --porcelain --untracked-files=no)" ]] \
  || die "uncommitted tracked changes present. Commit or stash them first."

STARTING_BRANCH=$(git rev-parse --abbrev-ref HEAD)
OLD_BASE=$(git rev-parse "$MIRROR_BRANCH")

# Enforce the invariant *before* touching anything: if the work branch is not
# descended from the mirror tip, `--onto` would silently drop or duplicate
# commits. Bail out and let a human look instead.
git merge-base --is-ancestor "$OLD_BASE" "$WORK_BRANCH" || die \
"invariant violated: '$WORK_BRANCH' is not based on the current tip of
'$MIRROR_BRANCH' ($(git rev-parse --short "$OLD_BASE")).
Someone committed to $MIRROR_BRANCH, or the branch was rebased by hand.
Resolve manually; refusing to guess."

note "Fetching $UPSTREAM_REMOTE (--force: upstream rewrites history)"
git fetch --prune --prune-tags --tags --force "$UPSTREAM_REMOTE"

NEW_BASE=$(git rev-parse "$UPSTREAM_REMOTE/$MIRROR_BRANCH")
if [[ "$OLD_BASE" == "$NEW_BASE" ]]; then
  note "Already in sync at $(git rev-parse --short "$NEW_BASE"). Nothing to do."
  exit 0
fi

LOCAL_COMMITS=$(git rev-list --count "$OLD_BASE..$WORK_BRANCH")
note "Upstream moved $(git rev-parse --short "$OLD_BASE") -> $(git rev-parse --short "$NEW_BASE")"
note "Will replay $LOCAL_COMMITS local commit(s) from '$WORK_BRANCH'"
git --no-pager log --oneline --no-decorate "$OLD_BASE..$WORK_BRANCH" | sed 's/^/      /'

if [[ -n "$DRY_RUN" ]]; then
  note "DRY_RUN set — stopping before making changes."
  exit 0
fi

# Cheap, durable undo point. Even a botched rebase stays reachable via this tag.
BACKUP_TAG="sync-backup/$(date +%Y%m%d-%H%M%S)"
git tag "$BACKUP_TAG" "$WORK_BRANCH"
note "Backup tag: $BACKUP_TAG -> $(git rev-parse --short "$WORK_BRANCH")"

note "Resetting '$MIRROR_BRANCH' to be an exact mirror of $UPSTREAM_REMOTE/$MIRROR_BRANCH"
git switch --quiet "$MIRROR_BRANCH"
git reset --hard --quiet "$NEW_BASE"

note "Rebasing '$WORK_BRANCH' onto the new mirror"
git switch --quiet "$WORK_BRANCH"
if ! git rebase --onto "$MIRROR_BRANCH" "$OLD_BASE"; then
  cat >&2 <<EOF

Rebase stopped on conflicts — expected when upstream edits the same files.

  1. resolve the listed files, then: git add <files> && git rebase --continue
  2. to abandon and return to where you started:
       git rebase --abort && git switch $STARTING_BRANCH

Your pre-sync state is preserved at tag: $BACKUP_TAG
EOF
  exit 1
fi

note "Done. '$WORK_BRANCH' now sits on $(git rev-parse --short "$MIRROR_BRANCH")."
cat <<EOF

Verify before trusting the result:
  (cd agent     && ../.venv/bin/python -m pytest tests -q)
  (cd frontend  && npx tsc --noEmit -p tsconfig.json && npx vitest run)

Publishing to your fork rewrites its history, so it needs a lease-checked force:
  git push --force-with-lease origin $MIRROR_BRANCH
  git push --force-with-lease origin $WORK_BRANCH

Drop the backup tag once you are happy:
  git tag -d $BACKUP_TAG
EOF
