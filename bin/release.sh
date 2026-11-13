#!/usr/bin/env bash
# Dispatch a release for a renewvan repo with the version resolved up front, so the Actions run
# is titled "Release v0.37.0" instead of "Release patch bump" (GitHub fixes a run's title when the
# run starts, and the workflow only learns the version afterwards).
#
#   bin/release.sh <repo> [patch|minor|major|X.Y.Z[-pre]] [--prerelease] [--ref <branch>]
#
# <repo> is the repo name under renewvan (dashboard, driver-autoterm, hub, ...). The bump defaults
# to patch and is applied to the repo's latest stable tag. A pre-release needs an explicit version.
set -euo pipefail

OWNER=renewvan
usage() { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }

repo="" ; spec=patch ; pre=false ; ref=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) usage 0 ;;
    --prerelease) pre=true ;;
    --ref) ref="${2:?--ref needs a branch}"; shift ;;
    -*) echo "unknown option $1" >&2; usage ;;
    *) if [ -z "$repo" ]; then repo="$1"; else spec="$1"; fi ;;
  esac
  shift
done
[ -n "$repo" ] || usage

export GH_TOKEN="${GH_TOKEN:-$(gh auth token --user "$OWNER")}"

case "$spec" in
  patch|minor|major)
    if [ "$pre" = true ]; then echo "a pre-release needs an explicit version (e.g. 0.5.0-beta.1)" >&2; exit 1; fi
    latest="$(gh api "repos/$OWNER/$repo/tags" --paginate --jq '.[].name' \
      | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n1 || true)"
    IFS=. read -r major minor patch <<< "${latest#v}"
    major="${major:-0}"; minor="${minor:-0}"; patch="${patch:-0}"
    case "$spec" in
      major) version="$((major + 1)).0.0" ;;
      minor) version="${major}.$((minor + 1)).0" ;;
      patch) version="${major}.${minor}.$((patch + 1))" ;;
    esac
    echo "latest ${latest:-none} + $spec -> v$version"
    ;;
  *) version="${spec#v}" ;;
esac

args=(-f "version=$version" -f "prerelease=$pre")
[ -z "$ref" ] || args+=(--ref "$ref")
gh workflow run release.yml --repo "$OWNER/$repo" "${args[@]}"
echo "dispatched Release v$version on $OWNER/$repo"
