#!/bin/sh
# verify-publish.sh — prove the PUBLISHED artifact works, not the local copy.
# Replays exactly what a user/cloner sees: fresh clone -> compile -> behavior.
# This is the automated version of Felipe's manual curl+py_compile+repro.
# Usage: ./verify-publish.sh [repo-url] [branch]
REPO=${1:-https://github.com/basebalance/x402-paywall}
BRANCH=${2:-main}
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
echo "== cloning PUBLISHED $BRANCH from $REPO =="
git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$TMP"
SHA=$(git -C "$TMP" rev-parse --short HEAD)
echo "cloned $SHA"
echo "== py_compile every .py (catches indentation/splice bugs) =="
FAIL=0
for f in $(find "$TMP" -name '*.py' -not -path '*/__pycache__/*'); do
  if python3 -m py_compile "$f"; then echo "OK  $f"; else echo "ERR $f"; FAIL=1; fi
done
[ $FAIL -eq 0 ] || { echo "PUBLISH-VERIFY FAIL: compile errors"; exit 1; }
echo "== behavioral regression (Felipe's cookie-less repro) =="
[ -f "$TMP/tests/ci_test.py" ] && (cd "$TMP" && python3 tests/ci_test.py)
echo "== PUBLISH-VERIFY PASS ($BRANCH @ $SHA) =="
