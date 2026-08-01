#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT

fake_gh="$tmpdir/gh"
cat >"$fake_gh" <<'FAKE_GH'
#!/usr/bin/env bash
set -euo pipefail

state=${FAKE_GH_STATE:?}
mkdir -p "$state"
log="$state/calls.log"

endpoint=""
method="GET"
while [ "$#" -gt 0 ]; do
  case "$1" in
    api)
      shift
      endpoint=$1
      ;;
    --method)
      shift
      method=$1
      ;;
  esac
  shift || true
done

printf '%s %s\n' "$method" "$endpoint" >>"$log"

case "$method $endpoint" in
  "GET repos/example/repo/git/ref/tags/prod-applied/component/123-1-abcdef0")
    printf '{"message":"Not Found","documentation_url":"https://docs.github.com/rest/git/refs#get-a-reference","status":"404"}\n'
    exit 1
    ;;
  "POST repos/example/repo/git/tags")
    printf 'tag-object-sha\n'
    ;;
  "POST repos/example/repo/git/refs")
    printf '{}\n'
    ;;
  *)
    printf 'unexpected gh api call: %s %s\n' "$method" "$endpoint" >&2
    exit 99
    ;;
esac
FAKE_GH
chmod +x "$fake_gh"

export PATH="$tmpdir:$PATH"
export FAKE_GH_STATE="$tmpdir/state"
export GITHUB_REPOSITORY=example/repo

output=$("$repo_root/scripts/create_marker_tag.sh" \
  prod-applied/component/123-1-abcdef0 \
  abcdef0123456789abcdef0123456789abcdef01 \
  "test marker")

printf '%s\n' "$output"

grep -q 'Created prod-applied/component/123-1-abcdef0 at abcdef0123456789abcdef0123456789abcdef01' <<<"$output"
grep -q 'POST repos/example/repo/git/tags' "$FAKE_GH_STATE/calls.log"
grep -q 'POST repos/example/repo/git/refs' "$FAKE_GH_STATE/calls.log"

if grep -q 'Existing prod-applied/component/123-1-abcdef0 points to {"message":"Not Found"' <<<"$output"; then
  echo '404 response body was treated as an existing ref' >&2
  exit 1
fi

echo 'create_marker_tag 404 regression passed'
