#!/usr/bin/env bash
# Create or update this repository's rulesets from the JSON files next to
# this script (the same shape GitHub's Settings > Rules export/import
# uses). Needs `gh` logged in as a repo admin — rulesets require
# administration rights, which a workflow's GITHUB_TOKEN cannot get, so
# run it by hand after cloning to a new org or changing a JSON file.
#
#   .github/rulesets/apply.sh            # current repo
#   .github/rulesets/apply.sh owner/repo # another one
set -euo pipefail
dir=$(cd "$(dirname "$0")" && pwd)
repo=${1:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}

existing=$(gh api "repos/$repo/rulesets" --paginate)
for f in "$dir"/*.json; do
  name=$(jq -r .name "$f")
  id=$(jq -r --arg n "$name" '.[] | select(.name == $n) | .id' <<<"$existing")
  if [ -n "$id" ]; then
    gh api -X PUT "repos/$repo/rulesets/$id" --input "$f" >/dev/null
    echo "updated  $repo ruleset '$name' (id $id)"
  else
    id=$(gh api -X POST "repos/$repo/rulesets" --input "$f" -q .id)
    echo "created  $repo ruleset '$name' (id $id)"
  fi
done
