#!/usr/bin/env python3
"""Guard that the two dependency-declaration lanes never silently drift (#173).

The project lists its dependencies twice by design: pyproject.toml (the uv
source of truth) and requirements.txt (the pip fallback lane). Comments in both
files mandate keeping them in sync -- nothing machine-checked it, so a drift
would land only on pip-lane users, the cohort we exercise least. This is a
text-level consistency check: no network, no uv, no live resolution. It asserts
the pyproject and pip lanes stay in lockstep:

    pyproject [project] dependencies        == requirements.txt

compared as a normalized requirement set -- PEP-503 name (case- and separator-
insensitive) plus a whitespace- and order-insensitive version specifier -- so a
mismatch is a genuine drift, not a reformatting. A failure names the drifting
package and which file holds which version, on both sides.

    python3 test_deps_sync.py           # verify, exit non-zero on failure
    python3 test_deps_sync.py --show    # also print the parsed sets

Pure stdlib, Python 3.11+ off-Windows (WSL). pyproject is read with tomllib.

Known, deliberate limits (widen the comparison if a marker ever appears in the
two lists): environment markers (`; python_version < ...`) are stripped, so a
marker-only drift would be masked -- none exist today. Extras (`pkg[extra]`)
are not masked: the name regex stops at the `[`, so `[extra]` folds into the
"specifier" and an extras-only difference reads as a genuine specifier drift
and is caught.

Sibling of test_setup.py / test_settings_io.py: a CASES list, PASS/FAIL print,
non-zero exit on failure. Mutating any single entry (name or specifier) in
either list makes it fail naming file + entry.
"""
import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent
SHOW = "--show" in sys.argv


def _read(name):
    return (REPO / name).read_text(encoding="utf-8")


# ======================================================================
# pyproject parsing
# ======================================================================

def _parse_pyproject(text):
    return list(tomllib.loads(text)["project"]["dependencies"])


# ======================================================================
# requirements parsing + requirement normalization
# ======================================================================

def _parse_requirements(text):
    out = []
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()   # full-line + inline comments, blanks
        if line:
            out.append(line)
    return out


_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*")


def _split_req(req):
    """(normalized name, normalized specifier). Name per PEP 503 (`-_.` runs to
    `-`, lowercased); specifier whitespace-stripped and its comma-separated
    constraints sorted, so order and spacing never read as a drift."""
    req = req.split("#", 1)[0].split(";", 1)[0].strip()
    m = _NAME.match(req)
    if not m:
        raise ValueError(f"unparsable requirement: {req!r}")
    name = re.sub(r"[-_.]+", "-", m.group(1)).lower()
    spec = re.sub(r"\s+", "", req[m.end():])
    spec = ",".join(sorted(p for p in spec.split(",") if p))
    return name, spec


def _norm_set(raws):
    return {_split_req(r) for r in raws}


def _raw_by_name(raws):
    d = {}
    for r in raws:
        name, _ = _split_req(r)
        d.setdefault(name, []).append(r)
    for k in d:
        d[k].sort()
    return d


def _drift_report(label_a, raws_a, label_b, raws_b):
    """Empty when the two sides carry the same normalized requirement set;
    otherwise one line per offending package naming both sides' raw strings."""
    if _norm_set(raws_a) == _norm_set(raws_b):
        return []
    by_a, by_b = _raw_by_name(raws_a), _raw_by_name(raws_b)
    lines = []
    for name in sorted(set(by_a) | set(by_b)):
        a, b = by_a.get(name), by_b.get(name)
        na = {_split_req(x)[1] for x in a} if a else None
        nb = {_split_req(x)[1] for x in b} if b else None
        if na == nb:
            continue
        if a is None:
            lines.append(f"  {name}: only in {label_b} ({', '.join(b)}); missing from {label_a}")
        elif b is None:
            lines.append(f"  {name}: only in {label_a} ({', '.join(a)}); missing from {label_b}")
        else:
            lines.append(f"  {name}: specifier differs -- {label_a} has {', '.join(a)}, "
                         f"{label_b} has {', '.join(b)}")
    return lines


# ======================================================================
# Cases
# ======================================================================

def test_project_deps_match_requirements():
    deps = _parse_pyproject(_read("pyproject.toml"))
    reqs = _parse_requirements(_read("requirements.txt"))
    drift = _drift_report("pyproject [project] dependencies", deps,
                          "requirements.txt", reqs)
    assert not drift, "dependency lists drifted:\n" + "\n".join(drift)


CASES = [
    test_project_deps_match_requirements,
]


def main():
    if SHOW:
        deps = _parse_pyproject(_read("pyproject.toml"))
        reqs = _parse_requirements(_read("requirements.txt"))
        print("[project] dependencies :", sorted(_norm_set(deps)))
        print("requirements.txt       :", sorted(_norm_set(reqs)))
        print()

    failures = []
    for case in CASES:
        try:
            case()
            print(f"PASS  {case.__name__}")
        except AssertionError as e:
            failures.append((case.__name__, str(e)))
            print(f"FAIL  {case.__name__}: {e}")
        except Exception as e:  # a crash is also a failure
            failures.append((case.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERROR {case.__name__}: {type(e).__name__}: {e}")

    if failures:
        print(f"\nFAIL: {len(failures)}/{len(CASES)} case(s) failed")
        return 1
    print("\nOK: pyproject.toml and requirements.txt declare the same dependencies")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
