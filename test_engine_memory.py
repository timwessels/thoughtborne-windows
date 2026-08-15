#!/usr/bin/env python3
"""Off-Windows verification of the last-selected-engine memory (#193, cites D-008).

`engine_memory` is pure/stdlib -- it imports nothing from the project and nothing
Windows-only -- so the state file's round-trip, its validation on both sides, its
robustness against a corrupt or stale file, and the startup precedence rule are
checked on plain Python against a temp directory.

What this pins (D-008):
  - an explicit `defaults.api` outranks the remembered engine, the memory
    outranks the built-in default, and each answer names its own source;
  - the whitelist guards BOTH directions, so a retired engine id can neither be
    written nor read back;
  - anything unreadable resolves to None instead of raising -- a broken state
    file must never cost a start -- and is left on disk, never "repaired";
  - the write is atomic and leaves no temp files behind;
  - the shipped carousel rotation (what thoughtborne.py actually calls) reorders
    the lineup without ever dropping an engine;
  - config.py keeps `DEFAULT_API_IS_EXPLICIT = True` at module level, and its
    `= False` initializer a single top-level statement ahead of it -- checked
    statically, because as a function local it would invert D-008 in silence, and
    an initializer flipped to True would kill the memory for every install.

    python3 test_engine_memory.py    # verify, exit non-zero on any violation
"""
import os
import ast
import sys
import json
import shutil
import tempfile
from pathlib import Path

import engine_memory as em

# The shipped carousel, hard-coded as a fixture: this file tests the module, not
# config, and a future engine rename must not silently change what is asserted.
APIS = ["soniox-live", "soniox", "groq-large", "groq"]

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def temp_names(d):
    """Every leftover of an interrupted atomic write (the '.<name>.*.tmp' shape)."""
    return [p.name for p in Path(d).iterdir() if p.name != em.STATE_FILENAME]


def test_roundtrip(d):
    path = em.state_path(d)
    check(path.name == em.STATE_FILENAME, f"state_path built {path.name}")
    check(path.parent == Path(d), "state_path is not beside the given base dir")

    # 1. No file yet -- the first start of every install.
    check(em.read_last_engine(path, APIS) is None, "a missing state file did not read as None")

    # 2. Write -> read returns it, and the file is the documented shape.
    check(em.write_last_engine(path, "groq-large", APIS) is True, "write of a valid engine failed")
    check(em.read_last_engine(path, APIS) == "groq-large", "the written engine did not read back")
    raw = path.read_text(encoding="utf-8")
    check(raw.endswith("\n"), "the state file does not end with a newline")
    data = json.loads(raw)
    check(data.get("last_engine") == "groq-large", f"unexpected payload: {data}")
    check(data.get("_comment") == em.STATE_COMMENT, "the self-describing _comment is missing")
    check("personal_settings.json" in em.STATE_COMMENT,
          "the _comment does not point at the real settings file")

    # 3. Write again -> last value wins, exactly one file, no .tmp leftovers.
    check(em.write_last_engine(path, "soniox", APIS) is True, "second write failed")
    check(em.read_last_engine(path, APIS) == "soniox", "the second write did not win")
    check(temp_names(d) == [], f"atomic write left files behind: {temp_names(d)}")


def test_validation(d):
    path = em.state_path(d)
    em.write_last_engine(path, "groq", APIS)

    # Write side: the whitelist keeps junk out of the file entirely.
    for bad in ("whisper-local", "", "GROQ", None, 42, ["groq"]):
        check(em.write_last_engine(path, bad, APIS) is False, f"write accepted {bad!r}")
    check(em.read_last_engine(path, APIS) == "groq",
          "a rejected write changed the stored engine")

    # ...and it does not create the file when there was none.
    fresh = Path(d) / "absent.json"
    check(em.write_last_engine(fresh, "nonsense", APIS) is False, "write accepted a junk value")
    check(not fresh.exists(), "a rejected write created the state file")

    # Read side: a value the running build does not know (a retired or renamed
    # engine id) reads as None -- and the file is KEPT, never repaired or deleted.
    for stale in ("soniox-v3", None, 7, ["groq"]):
        path.write_text(json.dumps({"last_engine": stale}), encoding="utf-8")
        check(em.read_last_engine(path, APIS) is None, f"read accepted {stale!r}")
        check(path.exists(), f"reading {stale!r} deleted the state file")


def test_robustness(d):
    path = em.state_path(d)

    # Corrupt JSON, the wrong top-level shape, and a missing key: all None, no raise.
    for content in ("{not json", "", "[]", '"soniox"', "null", '{"other": "groq"}'):
        path.write_text(content, encoding="utf-8")
        try:
            got = em.read_last_engine(path, APIS)
        except Exception as e:                      # a broken file must never cost a start
            failures.append(f"read raised on {content!r}: {type(e).__name__}: {e}")
            continue
        check(got is None, f"read of {content!r} returned {got!r}")

    # A pathological file is still just "nothing remembered": deeply nested JSON
    # drives the parser into a RecursionError, which is neither OSError nor
    # ValueError -- and read_last_engine runs inside ThoughtborneApp.__init__,
    # where anything escaping aborts the whole start.
    path.write_text("[" * 10000 + "]" * 10000, encoding="utf-8")
    try:
        got = em.read_last_engine(path, APIS)
    except Exception as e:
        failures.append(f"read raised on deeply nested JSON: {type(e).__name__}: {e}")
    else:
        check(got is None, f"read of deeply nested JSON returned {got!r}")

    # Unknown extra keys stay harmless -- the object shape is forward-compatible.
    path.write_text(json.dumps({"last_engine": "soniox", "future_key": {"x": 1}}),
                    encoding="utf-8")
    check(em.read_last_engine(path, APIS) == "soniox",
          "an unknown extra key broke the read")

    # A directory where the file should be: unreadable, so None rather than a crash.
    blocked = Path(d) / "blocked"
    (blocked / em.STATE_FILENAME).mkdir(parents=True)
    check(em.read_last_engine(em.state_path(blocked), APIS) is None,
          "an unreadable state file did not read as None")

    # An unwritable target directory: best-effort write returns False, never raises.
    # Skipped as root (mode bits do not apply) and where the FS ignores chmod.
    readonly = Path(d) / "readonly"
    readonly.mkdir()
    os.chmod(readonly, 0o500)
    if os.access(readonly, os.W_OK):
        print("  (skipped: the unwritable-directory case -- this FS/user ignores chmod)")
    else:
        try:
            got = em.write_last_engine(em.state_path(readonly), "groq", APIS)
            check(got is False, "write into an unwritable directory did not report failure")
        except Exception as e:
            failures.append(f"write raised on an unwritable directory: {type(e).__name__}: {e}")
    os.chmod(readonly, 0o700)


def test_precedence():
    # D-008: config > memory > built-in default, each answer naming its source.
    cases = [
        (dict(remembered="groq", configured="soniox", builtin_default="soniox-live"),
         ("soniox", "config"), "an explicit defaults.api must outrank the memory"),
        (dict(remembered="groq", configured=None, builtin_default="soniox-live"),
         ("groq", "memory"), "the memory must apply where nothing is configured"),
        (dict(remembered=None, configured=None, builtin_default="soniox-live"),
         ("soniox-live", "default"), "nothing configured, nothing remembered -> the default"),
        (dict(remembered=None, configured="groq-large", builtin_default="soniox-live"),
         ("groq-large", "config"), "a pin without a memory must still win"),
        # Presence, not difference: a hand-written pin ON the built-in default is
        # explicit and must beat the memory (config.DEFAULT_API_IS_EXPLICIT).
        (dict(remembered="groq", configured="soniox-live", builtin_default="soniox-live"),
         ("soniox-live", "config"), "a pin on the built-in default lost to the memory"),
    ]
    for kwargs, expected, msg in cases:
        got = em.resolve_startup_engine(**kwargs)
        check(got == expected, f"{msg}: resolve{kwargs} -> {got}, expected {expected}")


def test_carousel_rotation():
    # The shipped candidate list -- thoughtborne._create_startup_transcriber calls
    # exactly this function, so what is asserted here is what starts the tool.
    # It only REORDERS the carousel: every engine is still tried exactly once, so
    # a remembered engine can never shorten a failure path.
    for remembered in APIS:
        start, _ = em.resolve_startup_engine(remembered=remembered, configured=None,
                                             builtin_default="soniox-live")
        candidates = em.carousel_from(start, APIS)
        check(candidates[0] == remembered, f"the carousel did not start at {remembered}")
        check(sorted(candidates) == sorted(APIS),
              f"the carousel from {remembered} is not a permutation: {candidates}")

    # Rotation, not a re-sort: the order after the start point is the carousel's.
    check(em.carousel_from("groq-large", APIS) == ["groq-large", "groq",
                                                   "soniox-live", "soniox"],
          f"unexpected rotation: {em.carousel_from('groq-large', APIS)}")

    # An unknown start (only reachable via a hand-edited config.DEFAULT_API) is
    # tried first anyway, so the factory's "Unknown API" error names it, and every
    # real entry still follows.
    odd = em.carousel_from("whisper-local", APIS)
    check(odd == ["whisper-local"] + APIS, f"unknown start not handled: {odd}")

    # The caller's list is never mutated or aliased.
    original = list(APIS)
    em.carousel_from("soniox", APIS).append("junk")
    check(APIS == original, f"carousel_from mutated the carousel: {APIS}")


def _stmt_lists(node):
    """Every statement list hanging off an AST node (a body, an else, a finally,
    an except handler) -- enough to tell "these two statements share a block"."""
    for field in ("body", "orelse", "finalbody"):
        block = getattr(node, field, None)
        if isinstance(block, list):
            yield block
    for handler in getattr(node, "handlers", None) or []:
        yield handler.body


def test_config_flag_is_module_level():
    """Static guard on config.py: `DEFAULT_API_IS_EXPLICIT = True` must stay a
    MODULE-level assignment, beside `DEFAULT_API = _api`, and its `= False`
    initializer must stay a single top-level statement ahead of it.

    Read as source, never imported -- config.py pulls in dotenv and Windows paths.
    The point: if that personal_settings parse block were ever folded into a
    function, the assignment would become a local, the flag would stay False for
    everyone, and D-008 would silently invert -- an explicit defaults.api losing to
    the remembered engine -- with every test here still green. Indentation is not
    the check (the assignment legitimately sits inside an `if`/`try` at module
    level); enclosure in a def/class is. The initializer is checked too, because
    the opposite mutation (initialize to True) kills the feature for everyone while
    leaving the `= True` the first half looks for exactly where it belongs.
    """
    source_path = Path(__file__).resolve().parent / "config.py"
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
    except Exception as e:
        failures.append(f"could not parse config.py: {type(e).__name__}: {e}")
        return

    def assigns(name, is_value):
        return [n for n in ast.walk(tree)
                if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets)
                and is_value(n.value)]

    flag = assigns("DEFAULT_API_IS_EXPLICIT",
                   lambda v: isinstance(v, ast.Constant) and v.value is True)
    applied = assigns("DEFAULT_API", lambda v: isinstance(v, ast.Name) and v.id == "_api")
    if not flag:
        failures.append("config.py no longer sets DEFAULT_API_IS_EXPLICIT = True "
                        "-- the D-008 precedence would fall back to the memory")
        return
    if not applied:
        failures.append("config.py no longer applies a defaults.api override "
                        "(DEFAULT_API = _api); the flag's block moved")
        return

    enclosing = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    for node in flag:
        for scope in enclosing:
            if scope.lineno <= node.lineno <= (scope.end_lineno or scope.lineno):
                failures.append(
                    f"DEFAULT_API_IS_EXPLICIT = True (config.py:{node.lineno}) sits "
                    f"inside {scope.name}() -- as a local it never reaches the "
                    f"module, and an explicit defaults.api would lose to the memory")
                break

    together = any(any(f in block for f in flag) and any(a in block for a in applied)
                   for n in ast.walk(tree) for block in _stmt_lists(n))
    if not together:
        failures.append("DEFAULT_API_IS_EXPLICIT = True no longer sits in the same "
                        "block as DEFAULT_API = _api -- the flag and the value it "
                        "describes must be set together")

    # The INVERSE mutation, which everything above would wave through: flip the
    # module-level initializer to True (or move it below the block that sets it)
    # and every install looks explicitly configured, so the remembered engine never
    # applies and #193 is dead -- with a `= True` still present, exactly where the
    # checks above look for it. Hence: exactly one initializer, top-level, first.
    init = assigns("DEFAULT_API_IS_EXPLICIT",
                   lambda v: isinstance(v, ast.Constant) and v.value is False)
    if len(init) != 1:
        failures.append(
            f"config.py must carry exactly ONE DEFAULT_API_IS_EXPLICIT = False "
            f"initializer, found {len(init)} -- without it every start counts as "
            f"explicitly configured and the #193 memory never applies")
    elif init[0] not in tree.body:
        failures.append(
            f"the DEFAULT_API_IS_EXPLICIT = False initializer (config.py:"
            f"{init[0].lineno}) is no longer a top-level statement -- a conditional "
            f"initializer leaves the flag True (or unset) on some paths")
    elif init[0].lineno > min(n.lineno for n in flag):
        failures.append(
            f"the DEFAULT_API_IS_EXPLICIT = False initializer (config.py:"
            f"{init[0].lineno}) now runs AFTER the block that sets it True -- it "
            f"would reset the flag, and an explicit defaults.api would lose to the "
            f"memory")


def main():
    d = tempfile.mkdtemp(prefix="tb_engine_memory_")
    try:
        test_roundtrip(d)
        test_validation(d)
        test_robustness(d)
        test_precedence()
        test_carousel_rotation()
        test_config_flag_is_module_level()
    finally:
        shutil.rmtree(d, ignore_errors=True)

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: state-file round-trip, whitelist validation, corrupt-file robustness, "
          "the carousel rotation, the D-008 precedence rule, and config.py's "
          "module-level explicit-default flag all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
