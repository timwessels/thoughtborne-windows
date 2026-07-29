#!/usr/bin/env python3
"""Guard that the two dependency-declaration lanes never silently drift (#173).

The project lists its dependencies twice by design: pyproject.toml (the uv
source of truth) and requirements.txt / requirements-optional.txt (the pip
fallback lane). Comments in both files mandate keeping them in sync -- nothing
machine-checked it, so a drift would land only on pip-lane users, the cohort we
exercise least. This is a text-level consistency check: no network, no uv, no
live resolution. It asserts two lanes stay in lockstep:

    pyproject [project] dependencies        == requirements.txt
    pyproject [dependency-groups] soniox    == requirements-optional.txt

compared as normalized requirement sets -- PEP-503 name (case- and separator-
insensitive) plus a whitespace- and order-insensitive version specifier -- so a
mismatch is a genuine drift, not a reformatting. A failure names the drifting
package and which file holds which version, on both sides.

    python3 test_deps_sync.py           # verify, exit non-zero on failure
    python3 test_deps_sync.py --show    # also print the parsed sets + active parser

Pure stdlib, Python 3.10+ off-Windows (WSL). pyproject is read with tomllib
where present (3.11+); on 3.10, where tomllib does not exist, a small targeted
line parser reads the two flat quoted-string arrays instead. Both are exercised:
on 3.11+ the fallback is cross-checked against tomllib (a reference oracle);
on 3.10 the fallback IS the live parser the two comparison cases run through.

Known, deliberate limits (widen the parser if a marker ever appears in the four
lists): environment markers (`; python_version < ...`) are stripped, so a
marker-only drift would be masked -- none exist today, and the tomllib
cross-check on 3.11+ is a canary that would flag the dumb fallback losing such a
token. Extras (`pkg[extra]`) are not masked: the name regex stops at the `[`, so
`[extra]` folds into the "specifier" and an extras-only difference reads as a
genuine specifier drift and is caught. Only the `soniox` group is checked
against requirements-optional.txt; a second future dependency group would need
its own case here.

Sibling of test_setup.py / test_settings_io.py: a CASES list, PASS/FAIL print,
non-zero exit on failure. Mutating any single entry (name or specifier) in any
of the four lists makes it fail naming file + entry.
"""
import re
import sys
from pathlib import Path

try:
    import tomllib
    HAVE_TOMLLIB = True
except ModuleNotFoundError:      # Python 3.10 ships no tomllib
    HAVE_TOMLLIB = False

REPO = Path(__file__).resolve().parent
SHOW = "--show" in sys.argv


def _read(name):
    return (REPO / name).read_text(encoding="utf-8")


# ======================================================================
# pyproject parsing -- tomllib where present, a targeted fallback on 3.10
# ======================================================================

def _parse_pyproject_tomllib(text):
    data = tomllib.loads(text)
    return list(data["project"]["dependencies"]), list(data["dependency-groups"]["soniox"])


_QUOTED = re.compile(r"""["']([^"']*)["']""")


def _section_body(text, section):
    """Lines from `[section]` up to the next top-level `[header]` or EOF.
    Section-scoped so `[project.urls]` never counts as `[project]`."""
    out, in_sec = [], False
    hdr = re.compile(r"^\s*\[([^\]]+)\]\s*$")
    for ln in text.splitlines():
        m = hdr.match(ln)
        if m:
            in_sec = (m.group(1) == section)
            continue
        if in_sec:
            out.append(ln)
    return out


def _extract_array(text, section, key):
    """The quoted strings of the array `key = [ ... ]` inside `[section]`.
    Array-scoped (only the named array, never every string in the section) so
    `requires-python`/`name` are not mistaken for requirements, and the opening
    `key =` is anchored at line start so `default-groups = ["soniox"]` (soniox
    as a value, not the array key) is never read as the soniox array."""
    lines = _section_body(text, section)
    keypat = re.compile(r"^\s*" + re.escape(key) + r"\s*=\s*\[")
    start = next((i for i, ln in enumerate(lines) if keypat.match(ln)), None)
    if start is None:
        raise ValueError(f"array [{section}].{key} not found in pyproject.toml")
    lines = lines[start:]
    lines[0] = lines[0].split("[", 1)[1]
    items = []
    for ln in lines:
        ln = ln.split("#", 1)[0]
        items += _QUOTED.findall(ln)
        if "]" in _QUOTED.sub("", ln):   # a ']' only outside the quoted spans closes the array
            break
    return [x for x in items if x.strip()]


def _parse_pyproject_fallback(text):
    return (_extract_array(text, "project", "dependencies"),
            _extract_array(text, "dependency-groups", "soniox"))


def _parse_pyproject(text):
    if HAVE_TOMLLIB:
        return _parse_pyproject_tomllib(text)
    return _parse_pyproject_fallback(text)


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
    deps, _ = _parse_pyproject(_read("pyproject.toml"))
    reqs = _parse_requirements(_read("requirements.txt"))
    drift = _drift_report("pyproject [project] dependencies", deps,
                          "requirements.txt", reqs)
    assert not drift, "dependency lists drifted:\n" + "\n".join(drift)


def test_soniox_group_matches_optional():
    _, soniox = _parse_pyproject(_read("pyproject.toml"))
    opt = _parse_requirements(_read("requirements-optional.txt"))
    drift = _drift_report("pyproject [dependency-groups] soniox", soniox,
                          "requirements-optional.txt", opt)
    assert not drift, "optional dependency lists drifted:\n" + "\n".join(drift)


def test_fallback_parser_matches_tomllib():
    # Reference cross-check: on 3.11+ the 3.10 fallback must extract byte-identical
    # tokens to tomllib, so the 3.10-only path is verified here on 3.11+ too. On
    # 3.10 there is no oracle, but there the fallback IS the live parser of the two
    # comparison cases above -- it carries the load either way, never skipped-and-idle.
    if not HAVE_TOMLLIB:
        print("      (skipped: no tomllib here; the fallback parser IS the live "
              "parser exercised by the two comparison cases on 3.10)")
        return
    text = _read("pyproject.toml")
    t_deps, t_soniox = _parse_pyproject_tomllib(text)
    f_deps, f_soniox = _parse_pyproject_fallback(text)
    assert sorted(f_deps) == sorted(t_deps), (
        "fallback parser extracted different [project] dependencies than tomllib:\n"
        f"  tomllib : {sorted(t_deps)}\n  fallback: {sorted(f_deps)}")
    assert sorted(f_soniox) == sorted(t_soniox), (
        "fallback parser extracted different soniox group than tomllib:\n"
        f"  tomllib : {sorted(t_soniox)}\n  fallback: {sorted(f_soniox)}")


def test_python_floor_declared():
    # This test's whole 3.10 rationale (the tomllib fallback) dies the day the
    # floor rises above 3.10; keep the two in step. pyproject only -- the README's
    # prose version range is not machine-asserted (a reformatting must not fail here).
    text = _read("pyproject.toml").replace(" ", "")
    assert ">=3.10" in text, (
        "pyproject requires-python no longer admits 3.10 -- the tomllib fallback "
        "in test_deps_sync.py would be dead code; revisit it")


CASES = [
    test_project_deps_match_requirements,
    test_soniox_group_matches_optional,
    test_fallback_parser_matches_tomllib,
    test_python_floor_declared,
]


def main():
    if SHOW:
        deps, soniox = _parse_pyproject(_read("pyproject.toml"))
        reqs = _parse_requirements(_read("requirements.txt"))
        opt = _parse_requirements(_read("requirements-optional.txt"))
        print("pyproject parser:", "tomllib" if HAVE_TOMLLIB else "fallback")
        print("[project] dependencies :", sorted(_norm_set(deps)))
        print("requirements.txt       :", sorted(_norm_set(reqs)))
        print("[soniox] group         :", sorted(_norm_set(soniox)))
        print("requirements-optional  :", sorted(_norm_set(opt)))
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
    print(f"\nOK: all {len(CASES)} dependency-sync cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
