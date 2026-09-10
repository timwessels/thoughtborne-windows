# Releasing Thoughtborne

The maintainer checklist for cutting a tagged GitHub release. Plain Markdown —
humans and coding agents both read it.

## Why a deliberate release asset

The guided installer (`setup.ps1`, #76) fetches an **immutable snapshot**, not
moving `main`: it downloads the code ZIP and the standalone `setup.ps1` from a
release's assets via the stable alias
`https://github.com/timwessels/thoughtborne-windows/releases/latest/download/<asset>`.
GitHub's own auto-generated tag archives ("Source code (zip)") are documented as
**hash-unstable** — the same tag can yield a different byte stream over time — so
they cannot back a verifiable install. A release therefore carries two
**deliberately built** assets with fixed names:

- **`thoughtborne.zip`** — the whole tracked tree at the tag, built with
  `git archive` (see the invariants box for *why* `git archive` and not a
  filesystem zip).
- **`setup.ps1`** — the same tag's installer script as a standalone asset, so the
  one-liner `irm … | iex` lane and the copy embedded in the ZIP can never drift.

The names and the ZIP layout are a contract with the installer — recorded as
**D-006** in [`DECISIONS.md`](DECISIONS.md). Do not rename or restructure them
casually.

## Preconditions

- On the commit you intend to release (normally `main`), working tree clean.
- `gh` installed and authenticated (`gh auth status`).
- The off-Windows test ladder is green (`python3 run_tests.py`), and the `Tests`
  workflow is green on `main` — CI runs the same ladder
  ([`.github/workflows/tests.yml`](.github/workflows/tests.yml)).
- You have decided the version `X.Y.Z` (semver, with pre-releases spelled the
  PEP 440 way — see step 1; the first asset-carrying release was the tag
  `v1.1.0-rc2`, #104, which predates that rule).

## The ritual

Substitute the real version for `X.Y.Z` throughout (e.g. `1.1.0`).

### 1. Bump the version, move the CHANGELOG block, commit

The tag must point at a commit that *already* carries the bumped version and the
finalized CHANGELOG — otherwise the shipped ZIP lags behind its own tag.

- `pyproject.toml`: set `version = "X.Y.Z"` (the single version string in the
  repo — there is no `__version__` in any `.py`). Spell a pre-release the
  PEP 440 way, without a separator — `1.1.0rc2`, not `1.1.0-rc2`: a `--dev`
  build appends `+dev.<sha>`, which leaves the number itself eight columns in
  the console masthead, and an over-long one is dropped from that screen
  entirely rather than truncated (D-021 — the log, the settings window and the
  Apps list still name it).
- `uv.lock`: the lockfile carries the project's own version too (the
  `[[package]] name = "thoughtborne"` entry). `uv lock` updates it — that works
  off-Windows and offline, since the lockfile is already complete — or edit the
  one `version = ...` line by hand, to the same string. A forgotten self-pin
  ships a ZIP whose lockfile still names the previous version.
- `CHANGELOG.md`: insert a `## [X.Y.Z] - YYYY-MM-DD` heading above the current
  entries, moving everything under `## [Unreleased]` beneath it and leaving
  `## [Unreleased]` empty above it (Keep a Changelog).

```bash
git add pyproject.toml uv.lock CHANGELOG.md
git commit -m "Release vX.Y.Z (#145)"
git push
```

### 2. Tag the release commit and push the tag

```bash
git tag -a vX.Y.Z -m "Thoughtborne X.Y.Z"
git push origin vX.Y.Z
```

### 3. Build and verify the assets from the tag

`build-release-zip.sh` builds both assets from the tag into `dist/` (gitignored)
and dry-run verifies the ZIP — extract, `py_compile` every `.py`, must-have-file
and no-user-data assertions, and the `.bat` CRLF / `.py`+`.ps1` LF check. It
never tags or publishes.

```bash
bash build-release-zip.sh vX.Y.Z      # must print "VERDICT: PASS"
```

The equivalent by hand, if you prefer the explicit commands:

```bash
mkdir -p dist
git archive --format=zip -o dist/thoughtborne.zip vX.Y.Z
git show vX.Y.Z:setup.ps1 > dist/setup.ps1
```

Do not proceed to step 4 unless the verification passes.

**Not part of the ritual —** `bash build-release-zip.sh --dev [ref]` builds the
same ZIP and then stamps `<version>+dev.<short sha of the ref's commit>` into the
`pyproject.toml` and `uv.lock` inside it, writing both assets to `dist/dev/` so a
test build can never sit where step 4 uploads from. It answers "what does the next
version's install actually look like" without a tag: point `THOUGHTBORNE_ZIP` at
the built ZIP and run `setup.ps1`, and the installed copy reports
`vX.Y.Z+dev.<sha>` wherever it reports a version (D-021) — the console masthead
excepted when the stamped number outgrows it (step 1), which the build warns
about. A `--dev` build is a test build, never a release asset, and is never
published (#306).

### 4. Create the GitHub release with both assets

Write the release notes the way v1.1.0-rc2 did (`gh release view v1.1.0-rc2`):
two or three sentences on what this release is and whom it is for, the install
one-liner, a highlights line, and a link to this version's `CHANGELOG.md` block
— not the block itself, which runs to hundreds of lines. Save them as
`dist/release-notes.md`. The release **must** be published as **Latest**, not as
a pre-release: `releases/latest/download/` resolves only to the newest
non-prerelease, so a pre-release would leave the installer's fetch URL pointing
at the previous (assetless) release and 404.

```bash
gh release create vX.Y.Z dist/thoughtborne.zip dist/setup.ps1 \
    --repo timwessels/thoughtborne-windows \
    --title "Thoughtborne X.Y.Z" \
    --notes-file dist/release-notes.md \
    --latest
```

## Verification

- **Before publishing (automated):** `build-release-zip.sh` (step 3) is the
  dry-run gate — a green run proves the ZIP extracts, every `.py` compiles, the
  payload is complete, no user data leaked in, and the line endings are right.
- **After publishing (manual, first real release):** confirm the stable alias
  actually resolves to the new asset —

  ```bash
  curl -sIL https://github.com/timwessels/thoughtborne-windows/releases/latest/download/thoughtborne.zip
  ```

  expect a `200` and a size near the built ZIP (about 1.1 MB as of v1.1.0). A
  `404` means the
  release was published as a pre-release (see step 4) or the asset name drifted.
  The full end-to-end install on a fresh machine is #76's acceptance, not this
  checklist's.

## Follow-ups

- **First execution was v1.1.0-rc2** (2026-08-23, #104) — the first release to
  carry these assets, published deliberately as **Latest** (not a pre-release) so
  the stable alias resolves for the installer. The v1.1.0 final follows the same
  ritual and supersedes it as Latest. (v1.0.0 predates the ritual and has no such
  assets.)
- **Site Download-ZIP button (#103)** — settled with site stage 2 (b77004b,
  2026-08-23): the button points at the `thoughtborne.zip` release asset, and the
  README's ZIP step is the matching one (extract, double-click `setup.bat`).
- **Scoop fast-follow (#51)** consumes the same `thoughtborne.zip` asset
  downstream.

## Invariants (do not regress) — see D-006

- Exactly two assets, named **`thoughtborne.zip`** and **`setup.ps1`**, served via
  `releases/latest/download/<name>`. `setup.ps1` (its `latest/download` and
  versioned `thoughtborne.zip` fetch URLs) and the sandbox harness hard-depend on
  these exact names.
- The ZIP is the **whole tracked tree at the tag**, built with **`git archive`** —
  flat (no wrapper dir), fixed name (never version-stamped), never trimmed via
  `export-ignore`, never a filesystem zip. `git archive` is what applies
  `.gitattributes` (`*.bat text eol=crlf`); a filesystem zip would ship LF `.bat`
  files and cmd.exe would mis-parse the launcher labels.
- The standalone `setup.ps1` asset comes from the **same tag** as the ZIP
  (`git show vX.Y.Z:setup.ps1`), so it is byte-identical to the copy inside the
  ZIP.
- The release is published as **Latest** (not a pre-release), or
  `releases/latest/download/` breaks.
