#!/usr/bin/env python3
"""Off-Windows verification of the hardened personal_settings.json reader (#206).

`personal_settings.json` is the one config file users are invited to hand-edit, so
a hand-editing mistake must never cost a start (VISION principle #1). Before #206
four plausible mistakes did: a UTF-8 BOM silently disabled every personalization
while the settings app kept showing it as active, an ANSI/cp1252 encoding aborted
`import config` (and with it the tool AND the settings app that would repair it), a
non-object top level crashed on the first `.get`, and a wrongly shaped `vocabulary`
block killed both Soniox transcribers in their constructor.

`config._load_personal_settings` is deterministic and never raises -- a path in,
(dict, warnings) out -- so all of that is checked here on plain Python against
tempdir fixtures, and the acceptance clause "importing `config` succeeds" is then
taken literally: one subprocess `import config` per fixture, against a copy of
`config.py` in a tempdir, which runs the module-level code below the reader too.
Checked alongside it: the parity with `settings_io.read_personal_settings` (both
readers must yield the SAME dict for the same bytes, which is what makes the D-002
"can never show ON for a file the tool reads as OFF" invariant true across the two
processes), the `IMPORT_WARNINGS` / `replay_import_warnings()` contract that gets
import-time warnings into thoughtborne.log instead of stderr, and two static guards
that keep both halves wired.

The Soniox constructor lane needs `groq` (transcriber's only third-party import off
Windows) and skips cleanly without it. Starting the real tool with a broken file and
reading the resulting thoughtborne.log line stays hands-on (Windows-only start).

    python3 test_config_loading.py          # verify, exit non-zero on any violation
    python3 test_config_loading.py --show   # also print each fixture's warnings
"""
import ast
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import config
import settings_io as sio

SHOW = "--show" in sys.argv

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


VALID = {
    "vocabulary": {"terms": ["Claude-MD", "WSL2"]},
    "push_to_talk": {"enabled": True},
    "defaults": {"api": "groq"},
}
VALID_BYTES = json.dumps(VALID).encode("utf-8")


def load(d, raw, name="personal_settings.json"):
    """Write `raw` as the settings file and run the production reader on it."""
    path = Path(d) / name
    path.write_bytes(raw)
    values, warnings = config._load_personal_settings(path)
    check(isinstance(values, dict), f"{name}: reader returned {type(values).__name__}, not a dict")
    check(isinstance(warnings, list) and all(isinstance(w, str) for w in warnings),
          f"{name}: warnings must be a list of strings, got {warnings!r}")
    if SHOW:
        print(f"    {raw[:48]!r} -> {len(values)} key(s), warnings: {warnings}")
    return values, warnings


# ======================================================================
# The file-level lane: encoding and top-level shape
# ======================================================================

def test_valid_file(d):
    """The control: a plain UTF-8 file loads verbatim and says nothing."""
    values, warnings = load(d, VALID_BYTES)
    check(values == VALID, f"a valid file did not round-trip: {values!r}")
    check(warnings == [], f"a valid file warned: {warnings}")
    # Both readers on the same bytes -- the parity that keeps the two processes
    # from disagreeing about the user's file (D-002).
    data, note = sio.read_personal_settings(Path(d) / "personal_settings.json")
    check(data == values, f"settings_io read something else from the same bytes: {data!r}")
    check(note is None, f"settings_io flagged a valid file: {note!r}")


def test_bom(d):
    """A UTF-8 BOM (PowerShell 5.1 / older Notepad default) is tolerated, warned
    about, and -- the machine-checked half of the acceptance -- read into the SAME
    dict the settings app reads, so the tool can never run on defaults while the
    settings app displays the file's values as active."""
    values, warnings = load(d, b"\xef\xbb\xbf" + VALID_BYTES)
    check(values == VALID, f"a BOM file did not round-trip: {values!r}")
    check(len(warnings) == 1, f"a BOM file produced {len(warnings)} warnings: {warnings}")
    check(any("BOM" in w for w in warnings), f"the BOM warning does not name the BOM: {warnings}")

    data, note = sio.read_personal_settings(Path(d) / "personal_settings.json")
    check(data == values,
          f"the two readers disagree on a BOM file: config={values!r} settings_io={data!r}")
    check(note is None, f"settings_io flagged the BOM file: {note!r}")
    # The invariant read_ptt_enabled states about itself, on this file.
    check(sio.read_ptt_enabled(data) is True and values["push_to_talk"]["enabled"] is True,
          "the settings toggle and the tool disagree about push_to_talk on a BOM file")


def test_undecodable(d):
    """Bytes that are not utf-8 (an ANSI/cp1252 or UTF-16 save) warn and fall back
    to defaults. Before #206 the UnicodeDecodeError escaped the handler -- it is a
    ValueError, not an OSError -- and aborted the import."""
    for label, raw in (
        ("cp1252", '{"vocabulary": {"terms": ["Munster"]}, "x": "ü"}'.encode("cp1252")),
        ("utf-16", VALID_BYTES.decode("utf-8").encode("utf-16")),
    ):
        values, warnings = load(d, raw)
        check(values == {}, f"{label}: expected the empty default dict, got {values!r}")
        check(len(warnings) == 1 and "Could not load" in warnings[0],
              f"{label}: expected one 'Could not load' warning, got {warnings}")


def test_broken_json(d):
    """Unparseable-but-decodable bytes keep the old warn-and-default behaviour."""
    for label, raw in (("empty", b""),
                       ("truncated", b'{"vocabulary": {"terms": ['),
                       ("trailing comma", b'{"defaults": {"api": "groq"},}')):
        values, warnings = load(d, raw)
        check(values == {}, f"{label}: expected the empty default dict, got {values!r}")
        check(len(warnings) == 1 and "Could not load" in warnings[0],
              f"{label}: expected one 'Could not load' warning, got {warnings}")


def test_non_object_root(d):
    """A JSON document whose root is not an object is ignored with a warning that
    names what was found. Before #206 the very next `.get` raised AttributeError."""
    for raw, kind in ((b'["Claude", "WSL2"]', "list"),
                      (b'"hello"', "str"),
                      (b'42', "int"),
                      (b'null', "NoneType")):
        values, warnings = load(d, raw)
        check(values == {}, f"root {kind}: expected the empty default dict, got {values!r}")
        check(len(warnings) == 1, f"root {kind}: expected one warning, got {warnings}")
        check(warnings and "JSON object at the top level" in warnings[0] and kind in warnings[0],
              f"root {kind}: warning does not name the shape problem: {warnings}")


def test_bom_and_undecodable(d):
    """Both file-level faults at once: two warnings, deterministic order."""
    values, warnings = load(d, b"\xef\xbb\xbf" + "ü".encode("cp1252"))
    check(values == {}, f"expected the empty default dict, got {values!r}")
    check(len(warnings) == 2, f"expected two warnings, got {warnings}")
    check(len(warnings) == 2 and "BOM" in warnings[0] and "Could not load" in warnings[1],
          f"warnings are not in production order (BOM first): {warnings}")


def test_absent_and_unreadable(d):
    """An absent file is the normal state of a fresh install: defaults, silently.
    An unreadable one warns (a directory in its place is the portable stand-in for
    'the OS refuses these bytes')."""
    values, warnings = config._load_personal_settings(Path(d) / "nothing-here.json")
    check(values == {} and warnings == [],
          f"an absent file must load silently, got ({values!r}, {warnings})")

    blocker = Path(d) / "as-a-directory"
    blocker.mkdir()
    values, warnings = config._load_personal_settings(blocker)
    check(values == {}, f"an unreadable path returned {values!r}")
    check(len(warnings) == 1 and "Could not load" in warnings[0],
          f"an unreadable path produced {warnings}")


# ======================================================================
# The vocabulary lane
# ======================================================================

def test_vocabulary_shape(d):
    """`vocabulary` is the only block whose consumers dereference it blindly, so a
    wrong shape is dropped here: the reader hands out the absent-block None, both
    Soniox constructors stay alive, and the file's other blocks survive."""
    for value, kind in ((["Claude", "WSL2"], "list"),  # the plausible mistake: the inner list, one level too high
                        ("Claude", "str"),
                        (42, "int")):
        raw = json.dumps({"vocabulary": value, "defaults": {"api": "groq"},
                          "push_to_talk": {"enabled": True}}).encode("utf-8")
        values, warnings = load(d, raw)
        check(values.get("vocabulary") is None,
              f"vocabulary as {kind} survived as {values.get('vocabulary')!r} -- "
              f"SONIOX_CONTEXT would be truthy and both Soniox constructors would crash")
        check(values.get("defaults") == {"api": "groq"} and values.get("push_to_talk") == {"enabled": True},
              f"vocabulary as {kind} took the other blocks down with it: {values!r}")
        check(len(warnings) == 1, f"vocabulary as {kind}: expected one warning, got {warnings}")
        check(warnings and f"'vocabulary' must be an object; ignoring (got {kind})" in warnings[0],
              f"vocabulary as {kind}: warning wording drifted from the sibling blocks: {warnings}")

    # An explicit null is not a mistake -- it means "no personalization", silently.
    values, warnings = load(d, b'{"vocabulary": null}')
    check(values.get("vocabulary") is None and warnings == [],
          f"an explicit null vocabulary should be silent, got ({values!r}, {warnings})")

    # A well-formed block is untouched.
    values, warnings = load(d, VALID_BYTES)
    check(values.get("vocabulary") == VALID["vocabulary"] and warnings == [],
          f"a valid vocabulary block was not passed through: ({values!r}, {warnings})")


def test_never_raises(d):
    """The whole point, swept: whatever the file holds, the reader returns rather
    than raising. The lane below takes the same question to the real `import
    config`; this one covers far more payloads than a subprocess per fixture."""
    payloads = [
        VALID_BYTES, b"", b"\xef\xbb\xbf", b"\x00\xff\xfe{", b"{",
        b"[]", b"null", b"true", b'{"vocabulary": []}', b'{"hotkeys": 5}',
        b"\xff" * 64, "üäö".encode("cp1252"),
        json.dumps({"push_to_talk": "on"}).encode("utf-8"),
        VALID_BYTES.decode("utf-8").encode("utf-32"),
    ]
    path = Path(d) / "sweep.json"
    for raw in payloads:
        path.write_bytes(raw)
        try:
            values, warnings = config._load_personal_settings(path)
        except Exception as e:
            failures.append(f"the reader raised {type(e).__name__} on {raw[:24]!r}: {e}")
            continue
        check(isinstance(values, dict) and isinstance(warnings, list),
              f"the reader returned ({type(values).__name__}, {type(warnings).__name__}) on {raw[:24]!r}")


# ======================================================================
# The import lane: `import config` itself, per fixture, in a subprocess
# ======================================================================

# What the child reports back: the warning count, whether the vocabulary block
# reached SONIOX_CONTEXT, and the engine the defaults block pinned. ASCII only,
# so the child's stdout encoding can never be the thing that fails.
_IMPORT_PROBE = (
    "import config;"
    "print('WARNINGS', len(config.IMPORT_WARNINGS));"
    "print('SONIOX_NONE', config.SONIOX_CONTEXT is None);"
    "print('DEFAULT_API', config.DEFAULT_API)"
)


def test_import_subprocess():
    """The acceptance clause read literally: for each broken file, `import config`
    SUCCEEDS -- not just the reader function. `config.py` and `hotkey_parse.py` are
    copied into a tempdir (SCRIPT_DIR is `Path(__file__).parent`, so the copy reads
    the fixture beside it) and imported by a fresh interpreter; a non-zero exit is
    the tool refusing to start.

    Unlike the sweep above this also runs the module-level code BELOW the reader --
    the push-to-talk, endpointing, hotkeys and defaults blocks -- against the
    fixture's contents, so a future raise source there cannot hide. The reported
    values pin that the file was really applied rather than quietly dropped: the
    BOM fixture must arrive as a live `defaults.api` pin (pre-#206 it did not), and
    the vocabulary fixture must leave SONIOX_CONTEXT None while its sibling blocks
    survive. Needs no network, no key, and writes nothing into the checkout."""
    builtin = config.BUILTIN_DEFAULT_API
    voc_fixture = json.dumps({"vocabulary": ["Claude", "WSL2"],
                              "defaults": {"api": "groq"}}).encode("utf-8")
    # (label, file bytes or None for "no file", warnings, SONIOX_CONTEXT is None, DEFAULT_API)
    cases = [
        ("absent", None, 0, True, builtin),
        ("valid", VALID_BYTES, 0, False, "groq"),
        ("bom", b"\xef\xbb\xbf" + VALID_BYTES, 1, False, "groq"),
        ("cp1252", '{"defaults": {"api": "groq"}, "x": "ü"}'.encode("cp1252"), 1, True, builtin),
        ("root array", b'["Claude", "WSL2"]', 1, True, builtin),
        ("vocabulary as list", voc_fixture, 1, True, "groq"),
    ]

    tmp = tempfile.mkdtemp(prefix="tb_config_import_")
    try:
        root = Path(__file__).resolve().parent
        for module in ("config.py", "hotkey_parse.py"):
            shutil.copy(root / module, tmp)
        settings = Path(tmp) / "personal_settings.json"
        # Inherit the environment (config reads env vars) minus the .pyc writes.
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for label, raw, n_warnings, soniox_none, default_api in cases:
            if raw is None:
                settings.unlink(missing_ok=True)
            else:
                settings.write_bytes(raw)
            try:
                proc = subprocess.run([sys.executable, "-c", _IMPORT_PROBE],
                                      cwd=tmp, env=env, capture_output=True,
                                      text=True, timeout=120)
            except subprocess.TimeoutExpired:
                failures.append(f"{label}: `import config` did not finish within 120 s")
                continue
            if proc.returncode != 0:
                failures.append(
                    f"{label}: `import config` exited {proc.returncode} -- the tool "
                    f"would not start on this file: {proc.stderr.strip()[-300:]}")
                continue
            reported = dict(line.split(" ", 1) for line in proc.stdout.split("\n") if " " in line)
            if SHOW:
                print(f"    {label}: {reported}")
            check(reported.get("WARNINGS") == str(n_warnings),
                  f"{label}: expected {n_warnings} import warning(s), got "
                  f"{reported.get('WARNINGS')}")
            check(reported.get("SONIOX_NONE") == str(soniox_none),
                  f"{label}: SONIOX_CONTEXT is None -> {reported.get('SONIOX_NONE')}, "
                  f"expected {soniox_none}")
            check(reported.get("DEFAULT_API") == default_api,
                  f"{label}: the file's engine pin did not reach DEFAULT_API "
                  f"({reported.get('DEFAULT_API')}, expected {default_api})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# The replay lane: import-time warnings reach the log, not stderr
# ======================================================================

def test_replay():
    """`replay_import_warnings()` re-emits every collected warning through the
    Thoughtborne.Config logger -- in collection order, at WARNING -- so the records
    reach the file handler main() has by then attached (before #206 they fell to
    logging.lastResort and never appeared in thoughtborne.log)."""
    check(isinstance(config.IMPORT_WARNINGS, list),
          f"IMPORT_WARNINGS is {type(config.IMPORT_WARNINGS).__name__}, not the list #238 appends to")

    seen = []

    class Capture(logging.Handler):
        def emit(self, record):
            seen.append((record.levelno, record.getMessage()))

    lg = logging.getLogger('Thoughtborne.Config')
    saved_warnings = list(config.IMPORT_WARNINGS)
    saved_level = lg.level
    handler = Capture()
    lg.addHandler(handler)
    lg.setLevel(logging.WARNING)
    try:
        # In place: replay_import_warnings reads the module global by name.
        config.IMPORT_WARNINGS[:] = ["sentinel-A", "sentinel-B"]
        returned = config.replay_import_warnings()
        check([m for _, m in seen] == ["sentinel-A", "sentinel-B"],
              f"the replay did not log both warnings in order: {seen}")
        check(all(level == logging.WARNING for level, _ in seen),
              f"the replay logged at another level than WARNING: {seen}")
        check(returned is config.IMPORT_WARNINGS,
              "replay_import_warnings does not return the collection it replayed")
    finally:
        lg.removeHandler(handler)
        lg.setLevel(saved_level)
        config.IMPORT_WARNINGS[:] = saved_warnings


# ======================================================================
# Static guards: both halves stay wired
# ======================================================================

def test_source_guards():
    """Read as source, never imported (thoughtborne.py pulls in Windows-only
    modules): main() must call the replay, and config.py must keep logging nothing
    at import time -- an import-time log call has no handler yet and would fall to
    stderr, which is exactly the lane #206 closed (and the one #238 inherits)."""
    root = Path(__file__).resolve().parent
    try:
        src = (root / "thoughtborne.py").read_text(encoding="utf-8")
    except Exception as e:
        failures.append(f"could not read thoughtborne.py: {type(e).__name__}: {e}")
    else:
        check("replay_import_warnings," in src,
              "thoughtborne.py no longer imports replay_import_warnings from config")
        call = src.find("replay_import_warnings()")
        main_def = src.find("def main()")
        check(call != -1, "thoughtborne.py never calls replay_import_warnings() -- "
                          "import-time config warnings would stay out of the log")
        check(main_def != -1 and call > main_def,
              "the replay_import_warnings() call is not inside main()")

    try:
        tree = ast.parse((root / "config.py").read_text(encoding="utf-8"))
    except Exception as e:
        failures.append(f"could not parse config.py: {type(e).__name__}: {e}")
        return
    scopes = [n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "_config_logger"):
            continue
        inside = any(s.lineno <= node.lineno <= (s.end_lineno or s.lineno) for s in scopes)
        check(inside,
              f"config.py:{node.lineno} logs at import time (_config_logger."
              f"{node.func.attr}) -- no handler exists yet, so the record falls to "
              f"stderr and never reaches thoughtborne.log; append to IMPORT_WARNINGS instead")

    check(any(isinstance(n, ast.FunctionDef) and n.name == "_load_personal_settings"
              for n in tree.body),
          "config.py no longer defines _load_personal_settings at module level")


# ======================================================================
# The consumer lane: both Soniox constructors survive a dropped vocabulary
# ======================================================================

def test_soniox_constructors():
    """The acceptance clause behind the vocabulary check: with SONIOX_CONTEXT None
    (what a wrongly shaped block now yields) both Soniox transcribers construct
    normally -- and with the pre-#206 value (the raw list) both still raise, which
    is why the shape check sits in the reader rather than in each constructor.

    Neither constructor touches the network or the microphone; the fake key never
    leaves the process, and the archive folder is redirected so the test writes
    nothing into the checkout."""
    try:
        import transcriber
    except ModuleNotFoundError as e:
        print(f"    (skipped Soniox constructor lane: '{e.name}' is not installed off Windows)")
        return

    classes = (transcriber.SonioxAsyncTranscriber, transcriber.SonioxLiveTranscriber)
    saved = (transcriber.SONIOX_CONTEXT, transcriber.SONIOX_API_KEY,
             transcriber.TEXT_ARCHIVE_FOLDER)
    tmp = tempfile.mkdtemp(prefix="tb_config_loading_tx_")
    try:
        transcriber.SONIOX_API_KEY = "not-a-real-key"
        transcriber.TEXT_ARCHIVE_FOLDER = Path(tmp) / "transcripts"
        for ctx in (None, {"terms": ["Claude-MD"]}):
            transcriber.SONIOX_CONTEXT = ctx
            for cls in classes:
                try:
                    cls()
                except Exception as e:
                    failures.append(f"{cls.__name__} failed to construct with "
                                    f"SONIOX_CONTEXT={ctx!r}: {type(e).__name__}: {e}")
        transcriber.SONIOX_CONTEXT = ["Claude", "WSL2"]
        for cls in classes:
            try:
                cls()
                failures.append(f"{cls.__name__} no longer raises on a list-shaped "
                                f"context -- the reader's shape check is the only "
                                f"thing standing between the user and that crash")
            except AttributeError:
                pass
            except Exception as e:
                failures.append(f"{cls.__name__} raised {type(e).__name__} on a "
                                f"list-shaped context, expected AttributeError: {e}")
    finally:
        (transcriber.SONIOX_CONTEXT, transcriber.SONIOX_API_KEY,
         transcriber.TEXT_ARCHIVE_FOLDER) = saved
        shutil.rmtree(tmp, ignore_errors=True)


TEMPDIR_CASES = [
    test_valid_file,
    test_bom,
    test_undecodable,
    test_broken_json,
    test_non_object_root,
    test_bom_and_undecodable,
    test_absent_and_unreadable,
    test_vocabulary_shape,
    test_never_raises,
]
PLAIN_CASES = [
    test_import_subprocess,
    test_replay,
    test_source_guards,
    test_soniox_constructors,
]


def main():
    d = tempfile.mkdtemp(prefix="tb_config_loading_")
    try:
        for case in TEMPDIR_CASES + PLAIN_CASES:
            before = len(failures)
            if SHOW:
                print(f"----- {case.__name__} -----")
            try:
                case(d) if case in TEMPDIR_CASES else case()
            except Exception as e:  # a crash is a failure like any other
                failures.append(f"{case.__name__} crashed: {type(e).__name__}: {e}")
            new = failures[before:]
            print(f"{'PASS' if not new else 'FAIL'}  {case.__name__}")
            for f in new:
                print("  " + f)
    finally:
        shutil.rmtree(d, ignore_errors=True)

    if failures:
        print(f"\nFAIL: {len(failures)} violation(s)")
        return 1
    print(f"\nOK: all {len(TEMPDIR_CASES) + len(PLAIN_CASES)} personal_settings "
          f"reader cases pass (encoding, top-level shape, vocabulary shape, the "
          f"settings_io parity, a real `import config` per fixture, the warning "
          f"replay, and the wiring guards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
