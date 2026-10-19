#!/usr/bin/env bash
# Commit what this job wrote and push it to the branch the job runs on.
#
#   bash .github/scripts/commit_and_push.sh MESSAGE TRIGGER FILE...
#
# FILE... is the set of files this job owns (for a FLoRA release, every file in
# .github/release-files.txt): written, changed or deleted, the job's result is the
# state of all of them together. Commits only when TRIGGER, one of them, changed; an
# empty TRIGGER means any of them.
#
# Three jobs push to this branch: the nightly sync, Publish FLoRA and the daily
# export. When another one pushed after this job's checkout, the push is rejected
# and retried on the new tip, where every FILE is set to exactly this job's
# version, present or absent, so two jobs' files are never mixed and nothing is
# merged line by line. Files outside FILE... keep the tip's.
#
# With RELEASE_MANIFEST set (a FILE holding a release manifest), a retry first
# compares data versions and leaves the tip alone when it holds newer data: a
# slower job must not replace a newer flora.csv with an older one.
set -euo pipefail

message=$1
trigger=$2
shift 2
branch=${GITHUB_REF_NAME:-main}
python=${PYTHON:-python}

git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

# Every owned file as it is on disk: added, changed, or deleted. A file that
# neither exists nor is tracked is skipped (git add would refuse its pathspec).
stage() {
  local file
  for file in "$@"; do
    if [ -e "$file" ] || git ls-files --error-unmatch -- "$file" >/dev/null 2>&1; then
      git add -A -- "$file"
    fi
  done
}

# Staged first: an unstaged diff does not see a file git does not track yet.
stage "$@"
if git diff --cached --quiet -- ${trigger:+"$trigger"}; then
  echo "Nothing to commit${trigger:+: $trigger is unchanged}."
  exit 0
fi
git commit -q -m "$message"
# This job's result, kept by name so it survives the resets below.
git update-ref refs/commit-and-push/mine HEAD

work=$(mktemp -d)
for attempt in 1 2 3 4 5; do
  if git push -q origin "HEAD:$branch"; then
    echo "Pushed: $message"
    exit 0
  fi
  echo "Push rejected (attempt $attempt): another job pushed first."
  sleep $((attempt * 5))
  git fetch -q --depth=1 origin "$branch"
  git reset -q --hard FETCH_HEAD

  if [ -n "${RELEASE_MANIFEST:-}" ] \
      && git cat-file -e "FETCH_HEAD:$RELEASE_MANIFEST" 2>/dev/null \
      && git cat-file -e "refs/commit-and-push/mine:$RELEASE_MANIFEST" 2>/dev/null; then
    git show "refs/commit-and-push/mine:$RELEASE_MANIFEST" > "$work/mine.json"
    git show "FETCH_HEAD:$RELEASE_MANIFEST" > "$work/tip.json"
    if "$python" "$(dirname "$0")/../../release_helpers.py" older "$work/mine.json" "$work/tip.json"; then
      echo "The new tip holds a release of newer data; leaving it in place."
      exit 0
    fi
  fi

  echo "Setting this job's files on the new tip."
  for file in "$@"; do
    if git cat-file -e "refs/commit-and-push/mine:$file" 2>/dev/null; then
      git checkout -q refs/commit-and-push/mine -- "$file"
    else
      git rm -q --ignore-unmatch -- "$file"
    fi
  done
  if git diff --cached --quiet; then
    echo "The new tip already holds this job's files; nothing to push."
    exit 0
  fi
  git commit -q -m "$message"
done
echo "Push still rejected after 5 attempts." >&2
exit 1
