#!/usr/bin/env bash
#
# Build and verify the release install assets -- it does NOT tag or publish.
#
# Part of the maintainer release ritual (see RELEASING.md). Given a ref or tag
# (default HEAD), it produces the two assets a GitHub release carries:
#
#   dist/thoughtborne.zip   the whole tracked tree at the ref, built with
#                           `git archive` so .gitattributes is applied and the
#                           .bat files keep their CRLF line endings (a
#                           filesystem zip would ship LF and cmd.exe would
#                           mis-parse the labels)
#   dist/setup.ps1          the same ref's setup.ps1 as a standalone asset, so
#                           the one-liner install lane and the copy inside the
#                           ZIP can never drift (#157)
#
# It then dry-run verifies the ZIP: extract, py_compile every .py, assert the
# must-have files are present, assert no user data / .git leaked in, and assert
# the .bat CRLF / .py+.ps1 LF split. Prints a PASS/FAIL verdict and exits
# non-zero on any failure. Output goes to dist/ (gitignored) -- never into the
# tree, never committed. Real releasing (git tag, gh release create) stays a
# deliberate manual step in RELEASING.md; this script never performs it.
#
# Usage:  bash build-release-zip.sh [ref]      # ref defaults to HEAD
#
set -euo pipefail

REF="${1:-HEAD}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

DIST="$REPO_ROOT/dist"
ZIP="$DIST/thoughtborne.zip"
PS1="$DIST/setup.ps1"

mkdir -p "$DIST"

# --- Build -----------------------------------------------------------------
echo "Building assets from ref: $REF"
git archive --format=zip -o "$ZIP" "$REF"
git show "$REF:setup.ps1" > "$PS1"
echo "  wrote $ZIP"
echo "  wrote $PS1"

# --- Verify ----------------------------------------------------------------
# Extract to a scratch dir (unzip is not always present; python3 -m zipfile is).
EXTRACT="$(mktemp -d)"
trap 'rm -rf "$EXTRACT"' EXIT
python3 -m zipfile -e "$ZIP" "$EXTRACT"

fail=0
note() { echo "  FAIL: $*"; fail=1; }

# Syntax over the whole tree. py_compile is syntax-only, so Windows-only imports
# never load -- it only proves the shipped .py files parse.
py_count=$(find "$EXTRACT" -name '*.py' | wc -l)
if ! find "$EXTRACT" -name '*.py' -print0 | xargs -0 python3 -m py_compile 2>/tmp/pycompile.err; then
    note "py_compile failed:"; sed 's/^/    /' /tmp/pycompile.err
fi

# Must-have files -- the payload the tool and installer depend on.
must_have=(
    test_audio.mp3
    assets/logo/favicon.ico
    Thoughtborne.bat
    Thoughtborne-Settings.bat
    setup.bat
    setup.ps1
    pyproject.toml
    uv.lock
    .env.example
    personal_settings.example.json
    thoughtborne.py
)
for f in "${must_have[@]}"; do
    [ -e "$EXTRACT/$f" ] || note "missing must-have file: $f"
done

# No user data or repo metadata may leak into the asset.
must_not_have=(
    .git .env personal_settings.json runtime_state.json history .venv CLAUDE.local.md
)
for f in "${must_not_have[@]}"; do
    [ -e "$EXTRACT/$f" ] && note "leaked into asset: $f"
done

# Line-ending split: .bat must carry CRLF (cmd.exe needs it), .py/.ps1 must not.
# This is what proves the ZIP was built with git archive (which applies
# .gitattributes) rather than a filesystem zip.
python3 - "$EXTRACT" <<'PY' || fail=1
import sys, pathlib
root = pathlib.Path(sys.argv[1])
ok = True
for n in ("Thoughtborne.bat", "setup.bat"):
    if b"\r" not in (root / n).read_bytes():
        print(f"  FAIL: {n}: expected CRLF, found none"); ok = False
for n in ("thoughtborne.py", "setup.ps1"):
    if b"\r" in (root / n).read_bytes():
        print(f"  FAIL: {n}: unexpected CR (should be LF)"); ok = False
sys.exit(0 if ok else 1)
PY

file_count=$(git archive --format=tar "$REF" | tar -tf - | grep -vc '/$')
size=$(du -h "$ZIP" | cut -f1)

echo
echo "Asset:      $ZIP ($size, $file_count files, $py_count .py compiled)"
echo "Standalone: $PS1"
if [ "$fail" -ne 0 ]; then
    echo "VERDICT: FAIL"
    exit 1
fi
echo "VERDICT: PASS"
