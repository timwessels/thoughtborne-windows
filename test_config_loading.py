#!/usr/bin/env python3
"""Off-Windows verification of how config.py reads the two files users hand-edit:
personal_settings.json (#206) and .env (#238).

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

`.env` (#238) gets the same treatment for the same reason -- it is written by hand or
by an assisting agent, on a Windows whose PowerShell defaults are ANSI, UTF-16 and
UTF-8-with-BOM. Its lane checks what the load is anchored on and how tolerant it is:
one `import config` per fixture reporting the env-var names the import created, so a
broken `.env` is a warning rather than a traceback, a BOM'd one yields the same key
names `settings_io.read_env` reads from the same bytes, and an ancestor directory's
`.env` is no longer picked up. Two static guards back it up on boxes where the
behavioural lanes skip: the call shape in `config.py`, and `.env.example` staying
plain ASCII so a copy of it cannot become the undecodable file.

The Soniox constructor lane needs `groq` (transcriber's only third-party import off
Windows) and skips cleanly without it, as do the `.env` lanes without `python-dotenv`
(both are installed in CI). Starting the real tool with a broken file and reading the
resulting thoughtborne.log line stays hands-on (Windows-only start).

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


def _copy_config_into(dest):
    """Put an importable `config` in `dest`: config.py plus the one first-party
    module it imports. SCRIPT_DIR is `Path(__file__).parent`, so the copy reads the
    fixture files beside it rather than the checkout's."""
    root = Path(__file__).resolve().parent
    for module in ("config.py", "hotkey_parse.py"):
        shutil.copy(root / module, dest)


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
        _copy_config_into(tmp)
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
# The .env lane: which file is loaded, in which encoding (#238)
# ======================================================================

# _IMPORT_PROBE's sibling, plus the one thing the .env lane is about: the env-var
# names `import config` created (an os.environ diff around the import). ASCII-escaped
# on the way out -- a BOM glued to a key name then prints as a readable escape
# instead of breaking the pipe, and that escape IS the pre-#238 failure.
_ENV_PROBE = (
    "import os;"
    "before = set(os.environ);"
    "import config;"
    "esc = lambda s: s.encode('ascii', 'backslashreplace').decode();"
    "print('WARNINGS', len(config.IMPORT_WARNINGS));"
    "print('W0', esc(config.IMPORT_WARNINGS[0]) if config.IMPORT_WARNINGS else '-');"
    "print('NEWVARS', ','.join(esc(n) for n in sorted(set(os.environ) - before)) or '-')"
)

_ENV_GOOD = b"GROQ_API_KEY=dummy-a\nSONIOX_API_KEY=dummy-b\n"


def test_env_loading_subprocess():
    """`.env` is loaded from the install directory, tolerantly, and never fatally.

    The keys live in `.env`, and it is the one file a first-run user (or an agent
    following llms-install.md) writes by hand or by script -- with a PowerShell 5.1
    whose three defaults are ANSI, UTF-16 and UTF-8-WITH-BOM. Before #238 the bare
    `load_dotenv()` met that with no path, no encoding and an ImportError-only guard:
    an ANSI/UTF-16 file killed `import config` with a traceback before any log
    handler existed (taking the settings app that would repair it down with it), a
    BOM'd file silently renamed the FIRST key so the console said "no key" while the
    settings window showed one, and a keyless install nested in another code tree
    inherited an ancestor directory's `.env` no repair surface would ever show.

    Each case is a real `import config` in a fresh interpreter against a copy in a
    tempdir, reporting the env-var names the import created. Three traps are baked
    into the fixtures and must survive future tidying:

    * The unreadable case is a **chmod'd file**, not the directory-in-its-place trick
      test_absent_and_unreadable uses: python-dotenv's `os.path.isfile` check treats a
      directory as *absent* and stays silent, so that stand-in would test nothing.
    * The layout is **two-level** -- the sentinel `.env` sits in the PARENT of the
      copy's directory -- and the ancestor case's child runs FROM that parent, reaching
      the copy through PYTHONPATH instead of through its cwd. Both halves carry weight.
      The layout is what makes the case red on the unfixed code: the old upward search
      starts at the child's cwd for a `-c` start and takes the first `.env` at or above
      it. The cwd is what makes it red on a `config.py` whose `SCRIPT_DIR / ".env"` was
      later "tidied" to a cwd-relative `Path(".env")` -- run from the install copy like
      its siblings, that regression would read the very directory it is supposed to and
      leave this lane (and the call-shape guard below, which leaves the argument's
      shape free) green. The sentinel is present in every case, so any lane that ever
      falls back to searching says so by carrying it into NEWVARS.
    * The child environment keeps **HOME and PATH** and only scrubs the two API keys
      plus the sentinel. python-dotenv may live in user site-packages (it does on the
      maintainer's box), so a minimal env would silently cost the child its dotenv and
      turn every lane into a hollow pass -- the utf-8 control case, which REQUIRES two
      names to appear, is the canary for that and for a future dotenv API break.

    The scrub is also what keeps the workshop's real keys out of a failure message:
    fixtures carry obviously fake values, and the real `.env` is never read."""
    try:
        import dotenv  # noqa: F401  -- the lanes below need the real loader
    except ModuleNotFoundError as e:
        print(f"    (skipped .env loading lanes: '{e.name}' is not installed off Windows)")
        return

    keys = {"GROQ_API_KEY", "SONIOX_API_KEY"}
    # (label, file bytes or None for "no .env", warnings, warning fragment, new names)
    cases = [
        ("utf-8", _ENV_GOOD, 0, None, keys),
        ("bom", b"\xef\xbb\xbf" + _ENV_GOOD, 1, "BOM", keys),
        ("cp1252", '# Schlüssel\nGROQ_API_KEY=dummy-a\n'.encode("cp1252"),
         1, "Could not load", set()),
        ("unreadable", _ENV_GOOD, 1, "Could not load", set()),
        ("ancestor", None, 0, None, set()),
    ]

    tmp = tempfile.mkdtemp(prefix="tb_config_env_")
    inst = Path(tmp) / "install"
    env_file = inst / ".env"
    try:
        (Path(tmp) / ".env").write_bytes(b"TB238_ANCESTOR_SENTINEL=1\n")
        inst.mkdir()
        _copy_config_into(inst)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for name in sorted(keys) + ["TB238_ANCESTOR_SENTINEL"]:
            env.pop(name, None)

        for label, raw, n_warnings, fragment, expected in cases:
            if raw is None:
                env_file.unlink(missing_ok=True)
            else:
                env_file.write_bytes(raw)
            if label == "unreadable":
                os.chmod(env_file, 0o000)
                if os.access(env_file, os.R_OK):
                    print("    (skipped the unreadable .env case: this FS/user "
                          "ignores chmod)")
                    os.chmod(env_file, 0o600)
                    continue
            if label == "bom":
                # Parity on the very bytes just written: whatever key names the
                # settings app reads out of this file, the tool must end up with
                # (D-002 -- the two must never disagree about whether a key exists).
                expected = set(sio.read_env(env_file))
                check(expected == keys,
                      f"settings_io read {expected} from the BOM fixture, not {keys} "
                      f"-- the parity assert below would compare against nothing")
            child_cwd, child_env = inst, env
            if label == "ancestor":
                # Run from the sentinel's directory (see the docstring): only from
                # here can this lane tell SCRIPT_DIR-anchoring from cwd-anchoring.
                # PYTHONPATH is prepended, not set -- python-dotenv itself may be
                # reachable only through an inherited one.
                inherited = env.get("PYTHONPATH", "")
                child_cwd = tmp
                child_env = dict(env, PYTHONPATH=os.pathsep.join(
                    [str(inst)] + ([inherited] if inherited else [])))
            try:
                proc = subprocess.run([sys.executable, "-c", _ENV_PROBE],
                                      cwd=child_cwd, env=child_env, capture_output=True,
                                      text=True, timeout=120)
            except subprocess.TimeoutExpired:
                failures.append(f".env {label}: `import config` did not finish within 120 s")
                continue
            finally:
                if label == "unreadable":
                    os.chmod(env_file, 0o600)
            if proc.returncode != 0:
                failures.append(
                    f".env {label}: `import config` exited {proc.returncode} -- the "
                    f"tool would not start on this .env: {proc.stderr.strip()[-300:]}")
                continue
            reported = dict(line.split(" ", 1) for line in proc.stdout.split("\n") if " " in line)
            if SHOW:
                print(f"    {label}: {reported}")
            check(reported.get("WARNINGS") == str(n_warnings),
                  f".env {label}: expected {n_warnings} import warning(s), got "
                  f"{reported.get('WARNINGS')} ({reported.get('W0')})")
            if fragment:
                check(fragment in reported.get("W0", ""),
                      f".env {label}: the warning does not mention '{fragment}': "
                      f"{reported.get('W0')}")
            names = reported.get("NEWVARS", "")
            got = set() if names in ("", "-") else set(names.split(","))
            check(got == expected,
                  f".env {label}: `import config` created {sorted(got)}, expected "
                  f"{sorted(expected)}"
                  + (" -- an ancestor directory's .env is being picked up again"
                     if "TB238_ANCESTOR_SENTINEL" in got else ""))
    finally:
        try:
            if env_file.exists():
                os.chmod(env_file, 0o600)
        except OSError:
            pass
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

    # #238's call shape, pinned statically because every behavioural .env lane above
    # skips on a box without python-dotenv -- a regression to the bare `load_dotenv()`
    # would ride a green local ladder otherwise. Only the two properties the fix is
    # about: an explicit path argument (whatever it is named) and the tolerant
    # encoding. Left free on purpose: the argument's shape, and everything else.
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "load_dotenv"]
    check(len(calls) == 1,
          f"config.py makes {len(calls)} load_dotenv() calls, expected exactly one")
    for call in calls:
        check(not any(s.lineno <= call.lineno <= (s.end_lineno or s.lineno) for s in scopes),
              f"config.py:{call.lineno}: load_dotenv() moved inside a function -- the "
              f"keys must reach os.environ at import time, before the constants below "
              f"read them")
        check(bool(call.args),
              f"config.py:{call.lineno}: load_dotenv() is called without a path -- it "
              f"would search UPWARD from here again and could load an ancestor "
              f"directory's .env instead of the install directory's (#238)")
        enc = [kw.value for kw in call.keywords if kw.arg == "encoding"]
        check(len(enc) == 1 and isinstance(enc[0], ast.Constant)
              and enc[0].value == "utf-8-sig",
              f"config.py:{call.lineno}: load_dotenv() no longer passes "
              f"encoding=\"utf-8-sig\" -- a BOM'd .env would rename the first key and "
              f"the console would report 'no key' while the settings window shows one")


def test_env_example_ascii():
    """`.env.example` is the seed for every `.env` (llms-install.md has the assisting
    agent copy it), so it stays plain ASCII: a non-ASCII byte in a comment is what an
    ANSI or UTF-16 save turns into the undecodable file the lane above covers, and it
    would arrive that way through no fault of the user. A BOM is three non-ASCII
    bytes, so this check covers that too. Use ` -- ` where an em dash is tempting."""
    path = Path(__file__).resolve().parent / ".env.example"
    try:
        raw = path.read_bytes()
    except OSError as e:
        failures.append(f"could not read .env.example: {type(e).__name__}: {e}")
        return
    offenders = [str(i) for i, line in enumerate(raw.split(b"\n"), 1)
                 if any(b > 0x7F for b in line)]
    check(not offenders,
          f".env.example is no longer pure ASCII (line(s) {', '.join(offenders)})")


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
    test_env_loading_subprocess,
    test_replay,
    test_source_guards,
    test_env_example_ascii,
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
    print(f"\nOK: all {len(TEMPDIR_CASES) + len(PLAIN_CASES)} config-reading cases "
          f"pass (personal_settings: encoding, top-level shape, vocabulary shape, the "
          f"settings_io parity, a real `import config` per fixture; .env: the install "
          f"directory as the only source, BOM key-name parity, a broken file warned "
          f"about instead of fatal; plus the warning replay and the static guards)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
