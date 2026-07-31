#!/usr/bin/env bash
#
# Usage: create_marker_tag.sh <tag> <commit-sha> <message>
#
# Creates a promotion marker tag through the git database API instead of
# `git push`. A marker usually points at an approved SHA that is older than the
# tip of the default branch, and pushing a ref whose `.github/workflows/*`
# content differs from the current one looks to GitHub like the Actions App
# token is rewriting CI. That needs a `workflows` permission GITHUB_TOKEN
# cannot be granted, so the push is rejected. Referencing a commit the
# repository already has introduces no content and stays within
# `contents: write`.
#
# The tag is annotated on purpose: the readers in prod_components.py sort and
# filter markers on taggerdate and skip any tag that has none.
#
# Re-running is safe. An existing tag on the same commit is left alone; one
# pointing somewhere else is an error, never a silent overwrite.
set -euo pipefail

TAG=$1
SHA=$2
MESSAGE=$3

BOT_NAME="github-actions[bot]"
BOT_EMAIL="41898282+github-actions[bot]@users.noreply.github.com"

existing_object=$(
  gh api "repos/${GITHUB_REPOSITORY}/git/ref/tags/${TAG}" \
    --jq '.object.sha' 2>/dev/null || true
)

if [ -n "$existing_object" ]; then
  # An annotated tag ref points at the tag object, so dereference to the commit.
  existing_commit=$(
    gh api "repos/${GITHUB_REPOSITORY}/git/tags/${existing_object}" \
      --jq '.object.sha' 2>/dev/null || echo "$existing_object"
  )
  if [ "$existing_commit" != "$SHA" ]; then
    echo "::error::Existing ${TAG} points to ${existing_commit}, not ${SHA}"
    exit 1
  fi
  echo "${TAG} already marks ${SHA}"
  exit 0
fi

tag_object=$(
  jq -n \
    --arg tag "$TAG" \
    --arg message "$MESSAGE" \
    --arg object "$SHA" \
    --arg name "$BOT_NAME" \
    --arg email "$BOT_EMAIL" \
    '{
      tag: $tag,
      message: $message,
      object: $object,
      type: "commit",
      tagger: { name: $name, email: $email },
    }' |
    gh api "repos/${GITHUB_REPOSITORY}/git/tags" \
      --method POST --input - --jq '.sha'
)

jq -n --arg ref "refs/tags/${TAG}" --arg sha "$tag_object" \
  '{ ref: $ref, sha: $sha }' |
  gh api "repos/${GITHUB_REPOSITORY}/git/refs" \
    --method POST --input - >/dev/null

echo "Created ${TAG} at ${SHA}"
