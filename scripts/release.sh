#!/usr/bin/env bash
# Cut a Story Studio release: preflight, set the version, date the CHANGELOG section, commit
# "Release X.Y.Z", tag vX.Y.Z and (after confirmation) push both, which starts
# .github/workflows/release.yml. Same behaviour as scripts/release.ps1. See docs/RELEASING.md.
#
#   scripts/release.sh [--dry-run] [--yes] <version>
#
#   --dry-run  run every check and show the changes and commands; change nothing
#   --yes      push without asking
set -euo pipefail

usage() { echo "usage: scripts/release.sh [--dry-run] [--yes] <version>   (e.g. 0.2.0 or 0.3.0-beta.1)"; }

dry_run=0 yes=0 version=''
while [ $# -gt 0 ]; do
  case "$1" in
    -n|--dry-run) dry_run=1 ;;
    -y|--yes) yes=1 ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) if [ -n "$version" ]; then usage >&2; exit 2; fi; version="$1" ;;
  esac
  shift
done
if [ -z "$version" ]; then usage >&2; exit 2; fi
version="${version#v}"
tag="v$version"

cd "$(dirname "$0")/.."
py="$(command -v python3 || command -v python || true)"
if [ -z "$py" ]; then echo "Python 3 not found" >&2; exit 1; fi
tool() { "$py" -B scripts/release_tool.py "$@"; }

failures=0
ok() { echo "  ok    $*"; }
fail() { echo "  FAIL  $*"; failures=$((failures + 1)); }

echo "Story Studio release $version$([ $dry_run = 1 ] && echo ' (dry run: nothing is changed)')"
echo
echo "Preflight"

branch="$(git symbolic-ref --quiet --short HEAD || echo '(detached HEAD)')"
if [ "$branch" = main ]; then ok "on main"; else fail "on main (on $branch)"; fi

if [ -z "$(git status --porcelain)" ]; then
  ok "working tree clean"
else
  fail "working tree clean:"; git status --short | sed 's/^/          /'
fi

if git fetch --quiet origin main; then
  head="$(git rev-parse HEAD)" remote="$(git rev-parse origin/main)"
  if [ "$head" = "$remote" ]; then
    ok "up to date with origin/main"
  else
    fail "up to date with origin/main (HEAD ${head:0:7}, origin/main ${remote:0:7}; pull or push first)"
  fi
else
  fail "could not fetch origin/main"
fi

if git rev-parse -q --verify "refs/tags/$tag" >/dev/null; then
  fail "tag $tag already exists locally"
elif [ -n "$(git ls-remote --tags origin "refs/tags/$tag")" ]; then
  fail "tag $tag already exists on origin"
else
  ok "tag $tag is new"
fi

log="$(mktemp)"
trap 'rm -f "$log"' EXIT
if "$py" -B -m unittest discover -s tests >"$log" 2>&1; then
  ok "tests pass ($(grep -Eo '^Ran [0-9]+ tests?' "$log" || echo 'ran'))"
else
  fail "tests pass:"; tail -n 30 "$log" | sed 's/^/          /'
fi

# Version is valid semver and newer; CHANGELOG has entries under "## Unreleased". Prints the diff.
if ! tool prepare "$version" --dry-run; then failures=$((failures + 1)); fi

origin_url="$(git remote get-url origin 2>/dev/null || true)"
slug="$(printf '%s' "$origin_url" | sed -nE 's#^(https://|git@|ssh://git@)github\.com[:/]([^/]+/[^/]+)$#\2#p' | sed 's/\.git$//')"
actions_url="https://github.com/${slug:-soenderby/story-studio}/actions/workflows/release.yml"
push=(git push --atomic origin main "$tag")
push_cmd="${push[*]}"

echo
if [ $dry_run = 1 ]; then
  echo "Would then run:"
  echo "  write src/story_studio/version.py and CHANGELOG.md (diff above)"
  echo "  git commit -m \"Release $version\" -- src/story_studio/version.py CHANGELOG.md"
  echo "  git tag -a $tag -m \"Story Studio $version\""
  echo "  $push_cmd   (after confirmation, unless --yes)"
  echo "  watch: $actions_url"
  echo
  if [ $failures -gt 0 ]; then echo "Dry run: $failures check(s) failed; a real release would stop here."; exit 1; fi
  echo "Dry run: all checks passed. Nothing was changed."
  exit 0
fi

if [ $failures -gt 0 ]; then echo "$failures check(s) failed. Nothing was changed."; exit 1; fi

tool prepare "$version"
git add -- src/story_studio/version.py CHANGELOG.md
git commit --quiet -m "Release $version" -- src/story_studio/version.py CHANGELOG.md
git tag -a "$tag" -m "Story Studio $version"
echo "Committed $(git rev-parse --short HEAD) \"Release $version\" and tagged $tag."
echo

if [ $yes = 0 ]; then
  answer=''
  read -r -p "Push main and $tag to origin? This starts the release build, which publishes the release. [y/N] " answer || true
  case "$answer" in
    y|Y|yes|YES) ;;
    *)
      echo "Not pushed. Push later with:  $push_cmd"
      echo "Or undo with:                 git tag -d $tag && git reset --hard HEAD~1"
      exit 0
      ;;
  esac
fi

"${push[@]}"
echo
echo "Pushed. Watch the release build: $actions_url"
echo "(or: gh run watch \$(gh run list --workflow release.yml --limit 1 --json databaseId --jq '.[0].databaseId'))"
