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
# With --dev it builds the same ZIP from the same ref, then stamps
# <version>+dev.<short sha> into the pyproject.toml and uv.lock INSIDE it (both:
# a lockfile still naming the old version is the trap RELEASING.md warns about)
# and writes to dist/dev/ instead. Such a build installs through setup.ps1's
# THOUGHTBORNE_ZIP lane and reports its own stamped version everywhere the tool
# reports one -- a test build, never a release asset, never published (#306).
#
# It then dry-run verifies the ZIP: extract, py_compile every .py, assert the
# must-have files are present, assert no user data / .git leaked in, and assert
# the .bat CRLF / .py+.ps1 LF split. Prints a PASS/FAIL verdict and exits
# non-zero on any failure. Output goes to dist/ (gitignored) -- never into the
# tree, never committed. Real releasing (git tag, gh release create) stays a
# deliberate manual step in RELEASING.md; this script never performs it.
#
# Usage:  bash build-release-zip.sh [--dev] [ref]   # ref defaults to HEAD
#
set -euo pipefail

DEV=0
REF=""
FIT_WARNING=""
for arg in "$@"; do
    case "$arg" in
        --dev) DEV=1 ;;
        -*)    echo "unknown option: $arg" >&2; exit 2 ;;
        # A second ref is refused rather than quietly winning: "v1.1.0 v1.0.9" would
        # otherwise build one of the two without saying which, on the lane that
        # produces release assets.
        *)     if [ -n "$REF" ]; then
                   echo "unexpected extra argument: $arg (one ref at most)" >&2; exit 2
               fi
               REF="$arg" ;;
    esac
done
REF="${REF:-HEAD}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

DIST="$REPO_ROOT/dist"
# A dev build never lands beside the release assets: dist/ is what RELEASING.md's
# publish command uploads from, so a stamped ZIP must not be able to sit there
# under the name that command expects (#306).
if [ "$DEV" -eq 1 ]; then DIST="$DIST/dev"; fi
ZIP="$DIST/thoughtborne.zip"
PS1="$DIST/setup.ps1"

mkdir -p "$DIST"

# --- Build -----------------------------------------------------------------
echo "Building assets from ref: $REF"
git archive --format=zip -o "$ZIP" "$REF"
git show "$REF:setup.ps1" > "$PS1"
echo "  wrote $ZIP"
echo "  wrote $PS1"

# git archive reads the ref out of the object database, never the working copy, so
# an uncommitted change is not in this ZIP -- while a --dev stamp still names the
# HEAD sha and makes the build look like the tree you are sitting in. Only worth
# saying when the ref IS that state; building an older tag says nothing about it.
if [ "$(git rev-parse --verify --quiet "$REF^{commit}" || true)" \
     = "$(git rev-parse --verify --quiet "HEAD^{commit}" || true)" ]; then
    dirty="$( (git status --porcelain 2>/dev/null || true) | wc -l )"
    if [ "$dirty" -gt 0 ]; then
        echo "  NOTE: $dirty uncommitted change(s) in this checkout are NOT in the build"
        echo "        -- git archive reads the commit, not the working tree."
    fi
fi

if [ "$DEV" -eq 1 ]; then
    # Peel to the commit: `git rev-parse --short <annotated tag>` yields the TAG
    # object's id, not the commit's, and --short is adaptive -- so peel and cut to
    # the fixed 7 config.read_checkout_state uses, and a dev install and a checkout
    # at the same commit then name the same id (D-021).
    DEV_SHA="$(git rev-parse "$REF^{commit}" | cut -c1-7)"
    # The delimiter is not 'PY', and no here-doc below uses one either: inside a
    # $( ) substitution bash ends a here-doc at any line merely BEGINNING with the
    # delimiter word (bash 5.2.21), and this body has a line starting PYPROJECT_RE
    # -- with 'PY' the substitution swallows it and dies on the remainder.
    DEV_VERSION="$(python3 - "$ZIP" "$DEV_SHA" <<'PYSTAMP'
import re, sys, zipfile

zip_path, sha = sys.argv[1], sys.argv[2]
# The pattern config.read_version and setup.ps1's Get-InstalledVersion both use,
# character for character, so the stamp lands on the line those two read (pinned
# from the other side by test_config_loading.test_version_drift_guard).
PYPROJECT_RE = re.compile(r'(?m)^\s*version\s*=\s*"([^"]+)"')
# uv.lock's own entry, anchored on the package name: the file opens with a bare
# `version = 1` (the lockfile format) and every dependency carries a version too.
LOCK_RE = re.compile(r'(?m)^name = "thoughtborne"\nversion = "([^"]+)"')


def stamp(text, rx, what, unique=False):
    """Return the text with +dev.<sha> appended to the version, and the base."""
    hits = list(rx.finditer(text))
    if not hits:
        sys.exit("stamp: %s: no version line found" % what)
    if unique and len(hits) != 1:
        sys.exit("stamp: %s: expected one version line, found %d" % (what, len(hits)))
    m = hits[0]
    return text[:m.start(1)] + m.group(1) + "+dev." + sha + text[m.end(1):], m.group(1)


with zipfile.ZipFile(zip_path) as zin:
    comment = zin.comment                      # git archive puts the commit sha here
    entries = [(i, zin.read(i)) for i in zin.infolist()]

base = lock_base = None
out = []
for info, data in entries:
    if info.filename == "pyproject.toml":
        text, base = stamp(data.decode("utf-8"), PYPROJECT_RE, "pyproject.toml")
        data = text.encode("utf-8")
    elif info.filename == "uv.lock":
        text, lock_base = stamp(data.decode("utf-8"), LOCK_RE, "uv.lock", unique=True)
        data = text.encode("utf-8")
    out.append((info, data))
for what, found in (("pyproject.toml", base), ("uv.lock", lock_base)):
    if found is None:
        sys.exit("stamp: the archive carries no %s" % what)
def comparable(v):
    """One version's two legal spellings, reduced to one string.

    uv writes PEP 440's normal form into the lockfile, which drops the separator
    before a pre-release segment: `1.1.0-rc2` in pyproject.toml comes back as
    `1.1.0rc2`, and comparing those raw calls one version two -- which would fail
    every dev build made during an rc cycle. Applied to BOTH sides, so any
    separator difference collapses the same way while a real drift (1.2.0 against
    1.1.0) still shows; no rewrite here touches a digit, so two different releases
    can never meet. What PEP 440 also normalizes but this does not -- the alias
    spellings (`alpha` for `a`, `beta` for `b`, `c`/`pre`/`preview` for `rc`, `rev`
    for `post`) and a separator INSIDE the segment (`1.1.0.rc.1` against
    `1.1.0rc1`) -- is left alone on purpose: each of those stops the build, and
    that is the direction of being wrong that says "check your lockfile".
    """
    return re.sub(r"[-_.]+(?=[a-z])", "", v.strip().lower())


if comparable(lock_base) != comparable(base):
    # The forgotten self-pin RELEASING.md warns about. Caught here rather than as a
    # failing `uv sync` at the far end of a full install run, which is a much more
    # expensive way to learn that the two files disagree.
    sys.exit("stamp: uv.lock names %r but pyproject.toml names %r -- run `uv lock`"
             % (lock_base, base))

with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zout:
    zout.comment = comment
    for info, data in out:
        attr = info.external_attr
        zout.writestr(info, data)
        info.external_attr = attr    # zipfile stamps 0o600 onto a zero attr, and
                                     # git archive writes 0 for every file entry
print(base + "+dev." + sha)
PYSTAMP
)"
    echo "  DEV BUILD -- stamped $DEV_VERSION; a test build, never a release asset"

    # Will the console masthead still show it? console_ui drops an over-budget
    # version WHOLE rather than truncating it (D-021: a cut-off number would be a
    # false statement), so one column too many means the startup screen shows no
    # version at all -- the very thing a dev install exists to display. Reachable in
    # practice: a pre-release number pushes v<x>-rc2+dev.<sha> past the budget. Asked
    # of the renderer shipping IN this ZIP -- pure stdlib and nothing but tables and
    # functions at module level, so running its body costs nothing and changes
    # nothing -- rather than of a copy of its arithmetic. Advisory only: the artifact
    # is correct, and the log, the settings window and the Apps list all still name
    # the version.
    FIT_WARNING="$(python3 - "$ZIP" "v$DEV_VERSION" <<'PYFIT'
import sys, zipfile

zip_path, display = sys.argv[1], sys.argv[2]
try:
    ns = {}
    with zipfile.ZipFile(zip_path) as z:
        exec(z.read("console_ui.py").decode("utf-8"), ns)

    def fits(token):
        # Both masthead forms: the ANSI one hangs the token on the wordmark's right
        # edge, the plain twin trails the tagline, and they must agree.
        return all(token in "".join(ns["_masthead_wordmark"](
                       ns["ACTIVE_LOGO_MARK"], token, ansi)) for ansi in (True, False))

    # The budget is probed, not calculated, so no arithmetic is duplicated here.
    budget = max((n for n in range(1, 64) if fits("v" + "9" * (n - 1))), default=0)
except Exception as e:
    print("could not check the masthead fit: %s: %s" % (type(e).__name__, e))
else:
    if not fits(display):
        print("%s is %d columns and the masthead budget is %d, so the startup screen "
              "will show NO version (console_ui drops an over-long one whole). The log, "
              "the settings window and the Apps list still name it."
              % (display, len(display), budget))
PYFIT
)"
    if [ -n "$FIT_WARNING" ]; then echo "  WARNING: $FIT_WARNING"; fi
fi

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
    assets/logo/thoughtborne.ico
    Thoughtborne.bat
    setup.bat
    setup.ps1
    pyproject.toml
    uv.lock
    .env.example
    personal_settings.example.json
    thoughtborne.py
    console_ui.py        # no start without it, and --dev reads it out of the ZIP
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
if [ "$DEV" -eq 1 ]; then
    echo "DEV BUILD:  $DEV_VERSION -- install it with THOUGHTBORNE_ZIP; never publish it"
    if [ -n "$FIT_WARNING" ]; then
        echo "            (see the masthead WARNING above)"
    fi
fi
if [ "$fail" -ne 0 ]; then
    echo "VERDICT: FAIL"
    exit 1
fi
echo "VERDICT: PASS"
