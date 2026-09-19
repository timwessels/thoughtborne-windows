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

Since #327 the BLOCK level of that file is covered too -- it used to be the silent
one. Every top-level entry now passes one gate in the reader (an unknown name warns
with a nearest-name hint, a known block of the wrong type warns and is dropped, a
`_`-prefixed key is never looked at), and the tuple that gate judges against is held
to the shipped example and to the reads of both programs so it cannot become a second
truth. The two blocks with real per-entry validation are checked where that validation
lives, at module level below the reader: `push_to_talk` and `soniox_endpointing`, one
fixture per rule family, each read back as the constants a running tool would use.
Three complete historical files go the same way on every run (D-026's frozen-fixture
rule -- the 2026-04 and 2026-07 shapes and a downgrade file), because a per-rule lane
cannot show what a user actually meets, which is one whole file meeting one program.
And the vocabulary's last mile is measured rather than assumed: what the reader kept
has to arrive in the real Soniox request -- the async body and the live WebSocket
config, the endpointing block riding along on the second. Since #329 that last mile
starts one step earlier, at the filter that builds the effective context: the block's
`_`-prefixed comment keys reach neither request, and a block holding nothing else
sends no context at all.

`.env` (#238, #269) gets the same treatment for the same reason -- it is written by
hand or by an assisting agent, on a Windows whose PowerShell defaults are ANSI,
UTF-16 and UTF-8-with-BOM. Since #269 it is also the ONLY place a key may come from
(D-017): `config.read_env_file` is the one parser for the whole product, the process
environment is neither fallback nor override, and nothing from the file is put into
`os.environ` -- which is what used to let a tool relaunched by the settings app keep
the key it inherited over the one just saved. The lanes here check all of that on a
real `import config` per fixture: what the load is anchored on, how tolerant it is,
what every parsing rule makes of a hand-edited line (asserted on the config constants
AND on `settings_io.read_env` of the same bytes -- equal by construction now, and
asserted anyway), that an inherited variable never wins, that the D-004 opt-out
follows the file, and that the import creates no environment variable at all. Static
guards back them up: the reader's call shape in `config.py`, no `dotenv` import left
in any driver or module, no process-environment read of the three `.env`-borne names
in either half, the single-instance guard in `thoughtborne.py` still consulting the
opt-out it is the consumption side of (#289), and `.env.example` staying plain ASCII
so a copy of it cannot become the undecodable file.

Since #281 the driver also covers the one file here that users do NOT edit:
`pyproject.toml`, the repo's single version string, which `config.read_version`
reads for the settings app's Machine-room tab and the startup log line. It belongs
in this driver for the same reason as the rest -- it is a fail-open read off the
install directory whose only correct behaviour on a broken file is to yield None and
cost nothing. Its lane checks the parsing rules against tempdir fixtures, that a
broken file still lets `import config` through, and, as the point of the exercise,
that the regex stays character-identical to the one `setup.ps1` uses for the
Installed-apps `DisplayVersion` and to the one `build-release-zip.sh --dev` stamps
with: two answers and, since #306, one writer, all about one file, none of which
may drift.

Beside it since #297 sits the other thing nobody edits and everything depends on:
the `.git` folder a checkout carries and an installed copy does not, which is what
tells the two apart when the masthead says which state is running. It is read with
the stdlib alone, in every shape git writes it, and the only correct behaviour on
anything else -- a torn reflog, a `ref:` pointing outside `refs/`, no `.git` at all
-- is to yield None and cost nothing, so it is checked exactly like the readers
above: against tempdir layouts, and once more through a real `import config`.

The three Soniox lanes need `groq` (transcriber's only third-party import off
Windows) and skip cleanly without it, and the checkout lane's one assertion against
the real repository skips where there is no `.git` to read (an exported tree); the
`.env` lanes need nothing beyond the stdlib and always run. Starting the real tool
with a broken file and reading the resulting thoughtborne.log line stays hands-on
(Windows-only start).

    python3 test_config_loading.py          # verify, exit non-zero on any violation
    python3 test_config_loading.py --show   # also print each fixture's warnings
"""
import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import datetime
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


def test_effective_soniox_context():
    """What the reader kept becomes what is sent (#329): `_`-prefixed keys are the
    file's comment convention, inside a block too, and used to go to Soniox verbatim
    as context. The function is the production path, so these cases are the contract
    both send sites and both `Context enabled` log lines inherit.

    Two of them are decisions rather than observations, pinned so that changing
    either is a decision too. A block left holding only comments yields None, not an
    empty object: it personalizes nothing, and "no context" keeps one shape -- both
    consumers gate on truthiness, so such a file sends no context field at all. And
    the filter is one level deep, the level the convention is documented on, so a
    `_`-prefixed key inside a `general` entry still goes out."""
    deep = {"general": [{"key": "domain", "value": "Dictation", "_note": "goes out"}]}
    cases = [
        (None, None),                       # absent block, and an explicit null
        ({}, None),
        ("Claude-MD", None),                # the loader warns about and drops these
        (["Claude-MD"], None),              # three before they could ever arrive;
        (42, None),                         # None here is the defensive twin, silent
        ({"_comment": "a note"}, None),
        ({"_comment": "a note", "terms": ["Claude-MD"]}, {"terms": ["Claude-MD"]}),
        ({"_a": 1, "_b": {"x": 2}, "_c": None, "terms": ["t"]}, {"terms": ["t"]}),
        (deep, deep),
    ]
    for value, expected in cases:
        got = config.effective_soniox_context(value)
        check(got == expected,
              f"effective_soniox_context({value!r}) -> {got!r}, expected {expected!r}")

    # The parsed settings are never mutated, the result is a new object, and the
    # file's key order survives -- #286's size guard measures json.dumps of this.
    full = {"_comment": "user prose", "general": [{"key": "domain", "value": "D"}],
            "terms": ["Claude-MD"], "text": "background"}
    snapshot = json.dumps(full)
    got = config.effective_soniox_context(full)
    check(json.dumps(full) == snapshot,
          f"the block handed in was modified: {full!r}")
    check(got is not full, "the context returned IS the parsed block, not a copy")
    check(list(got) == ["general", "terms", "text"],
          f"the block's key order did not survive the filter: {list(got)}")


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
# The block lane: the top-level gate every entry passes (#327)
# ======================================================================

def test_block_types(d):
    """A known block of the wrong type warns and is dropped -- what `vocabulary`
    has always done, now for all six, because a block dropped in silence is the
    gap D-026's warn duty closes. One warning per block, its siblings untouched,
    and the wording is the one `vocabulary` already had (the sibling blocks are
    not worth a second phrasing). An explicit null keeps its exemption: it says
    "nothing configured", which is not a mistake and reads like an absent block
    everywhere below."""
    for block in config.KNOWN_SETTINGS_BLOCKS:
        siblings = {k: {} for k in config.KNOWN_SETTINGS_BLOCKS if k != block}
        for value, kind in ((["Claude", "WSL2"], "list"), ("on", "str"),
                            (42, "int"), (True, "bool")):
            payload = dict(siblings, **{block: value, "_comment": "a note"})
            values, warnings = load(d, json.dumps(payload).encode("utf-8"))
            check(block not in values,
                  f"{block} as {kind} survived as {values.get(block)!r} -- every read "
                  f"below the loader would have to guard its own type again")
            check(len(warnings) == 1,
                  f"{block} as {kind}: expected one warning, got {warnings}")
            check(warnings and f"personal_settings '{block}' must be an object; "
                               f"ignoring (got {kind})" in warnings[0],
                  f"{block} as {kind}: warning wording drifted: {warnings}")
            check({k: v for k, v in values.items() if k != "_comment"} == siblings,
                  f"{block} as {kind} took the other blocks down with it: {values!r}")

    for block in config.KNOWN_SETTINGS_BLOCKS:
        values, warnings = load(d, json.dumps({block: None}).encode("utf-8"))
        check(values.get(block) is None and warnings == [],
              f"an explicit null {block} should be silent, got ({values!r}, {warnings})")


def test_unknown_blocks(d):
    """The typo class D-026 names by example: `hotkyes` used to be completely
    invisible -- no log line, no console line, the tool simply running on
    defaults. Now it warns, with the nearest known name where difflib finds one
    and the known list spelled out where it does not, so the answer is in the
    warning either way.

    Two things the gate deliberately does NOT do: it does not drop an unknown
    block (nothing reads it, and a reader never edits user land), and it never
    looks at a `_`-prefixed key -- that is the file's comment convention, so the
    value may be anything at all."""
    known = ", ".join(config.KNOWN_SETTINGS_BLOCKS)
    for name, fragment in (("hotkyes", "did you mean 'hotkeys'?"),
                           ("vocabluary", "did you mean 'vocabulary'?"),
                           ("pushtotalk", "did you mean 'push_to_talk'?"),
                           ("endpointing", "did you mean 'soniox_endpointing'?"),
                           ("dictation", f"(known blocks: {known})"),
                           ("engine", f"(known blocks: {known})")):
        values, warnings = load(d, json.dumps({name: {"x": 1}}).encode("utf-8"))
        check(len(warnings) == 1, f"unknown block {name}: expected one warning, got {warnings}")
        check(warnings and f"unknown block '{name}'" in warnings[0] and fragment in warnings[0],
              f"unknown block {name}: the warning does not carry {fragment!r}: {warnings}")
        check(values.get(name) == {"x": 1},
              f"unknown block {name} was dropped -- the reader leaves user land alone")

    values, warnings = load(d, json.dumps(
        {"_note": "a comment", "_comment": 42, "_x": ["anything"]}).encode("utf-8"))
    check(warnings == [], f"a `_`-prefixed key was flagged: {warnings}")
    check(len(values) == 3, f"a `_`-prefixed key was dropped: {values!r}")

    # Two of them: one warning each, in the file's own order (json.loads keeps it),
    # so a file with several typos reads top to bottom like the file does.
    values, warnings = load(d, b'{"zzz": {}, "aaa": {}}')
    check(len(warnings) == 2 and "'zzz'" in warnings[0] and "'aaa'" in warnings[1],
          f"two unknown blocks did not warn once each, in file order: {warnings}")

    # Both faults in one file: the unknown block and the wrongly typed known one.
    values, warnings = load(d, b'{"dictation": {}, "push_to_talk": "on"}')
    check(len(warnings) == 2 and "unknown block 'dictation'" in warnings[0]
          and "'push_to_talk' must be an object" in warnings[1],
          f"an unknown block beside a wrongly typed known one: {warnings}")


def test_known_blocks_drift():
    """`config.KNOWN_SETTINGS_BLOCKS` is the tuple the gate judges every user file
    against, so it has to stay the same set as the file surface it speaks for --
    checked from both sides, because either drift is silent and lands in a user's
    log rather than here.

    `personal_settings.example.json` is the documented surface: a block shipped
    there without a reader would make the tool warn about its own example, and a
    reader without a documented block would be undocumented. The `.get(...)` reads
    on the parsed dict are the live surface: a new read that forgets the tuple
    would make the tool warn about the very block it just started reading. Both
    settings-app modules that read a block are scanned too -- `ui` is read in the
    window and nowhere else, which is exactly the kind of block that goes missing
    from a list kept in the tool, and `settings_io` reads one in a pure helper
    (`read_ptt_enabled`). Its save lanes are deliberately not scanned: those merge
    into the user's file rather than read a block, and they say `data`/`found`."""
    root = Path(__file__).resolve().parent
    known = set(config.KNOWN_SETTINGS_BLOCKS)
    check(len(known) == len(config.KNOWN_SETTINGS_BLOCKS),
          f"KNOWN_SETTINGS_BLOCKS has a duplicate: {config.KNOWN_SETTINGS_BLOCKS}")
    try:
        example = json.loads((root / "personal_settings.example.json")
                             .read_text(encoding="utf-8-sig"))
    except Exception as e:
        failures.append(f"could not read personal_settings.example.json: "
                        f"{type(e).__name__}: {e}")
    else:
        documented = {k for k in example if not k.startswith("_")}
        check(documented == known,
              f"personal_settings.example.json documents {sorted(documented)} while "
              f"config.KNOWN_SETTINGS_BLOCKS holds {sorted(known)} -- a block in one "
              f"and not the other is either undocumented or warned about in every "
              f"user's log")

    for name, expression in (("config.py", r'_settings\.get\("([^"]+)"'),
                             ("thoughtborne_settings.py", r'personal\.get\("([^"]+)"'),
                             ("settings_io.py", r'personal\.get\("([^"]+)"')):
        try:
            src = (root / name).read_text(encoding="utf-8")
        except OSError as e:
            failures.append(f"could not read {name}: {type(e).__name__}: {e}")
            continue
        strays = sorted(set(re.findall(expression, src)) - known)
        check(not strays,
              f"{name} reads the personal_settings block(s) {strays} that "
              f"config.KNOWN_SETTINGS_BLOCKS does not know -- the loader would warn "
              f"about a block this very program reads")


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
        # Inherit the environment (HOME, PATH -- what the interpreter needs to run;
        # config itself reads no environment variable since D-017) minus the .pyc writes.
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
# The value lanes: what a file's push_to_talk / soniox_endpointing entries
# actually become, measured through a real `import config` (#327)
# ======================================================================

# Both validations live at MODULE level in config.py, below the reader, so the
# in-process lanes above cannot reach them at all -- only a real import runs them.
# The probe reports the constants a running tool would use, as one ASCII JSON line
# (json.dumps escapes non-ASCII, so the child's stdout encoding can never be the
# thing that fails). `ep` is the merged WS-config fragment rather than the three
# constants: what is SENT is the question, and an absent field is its own answer.
_VALUES_PROBE = (
    "import config, json;"
    "print('JSON', json.dumps({"
    "'warnings': config.IMPORT_WARNINGS,"
    "'ptt': [config.PTT_ENABLED, config.PTT_TRIGGER, config.PTT_TRIGGER_VK,"
    " config.PTT_INSERT, config.PTT_TAP_WINDOW_S, config.PTT_MIN_HOLD_S,"
    " config.PTT_RELEASE_TAIL_S],"
    "'ep': config.soniox_live_endpointing_params(),"
    "'hotkeys': config.HOTKEYS,"
    "'api': [config.DEFAULT_API, config.DEFAULT_API_IS_EXPLICIT],"
    "'context': config.SONIOX_CONTEXT}))"
)

# The shipped push-to-talk defaults, in the probe's order. Every invalid entry
# has to land back on exactly these -- that is what "warn and keep the default"
# means at the far end.
_PTT_DEFAULTS = [False, "lctrl", 0xA2, "clipboard", 0.30, 0.20, 0.15]


def _probe_settings(label, tmp, raw):
    """One real `import config` in a fresh interpreter, with `raw` written as the
    personal_settings.json beside the copied config (its SCRIPT_DIR is that
    tempdir). Returns the probe's report, or None after recording the failure --
    a non-zero exit is the tool refusing to start on that file."""
    (Path(tmp) / "personal_settings.json").write_bytes(raw)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        proc = subprocess.run([sys.executable, "-c", _VALUES_PROBE], cwd=tmp, env=env,
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        failures.append(f"{label}: `import config` did not finish within 120 s")
        return None
    if proc.returncode != 0:
        failures.append(
            f"{label}: `import config` exited {proc.returncode} -- the tool would not "
            f"start on this file: {proc.stderr.strip()[-300:]}")
        return None
    line = next((ln for ln in proc.stdout.split("\n") if ln.startswith("JSON ")), None)
    if line is None:
        failures.append(f"{label}: the probe printed no JSON line: {proc.stdout[-200:]!r}")
        return None
    report = json.loads(line[len("JSON "):])
    if SHOW:
        print(f"    {label}: {report}")
    return report


def _check_report(label, report, n_warnings, fragments, expected):
    """One probe report against its expectation: the exact warning count (the
    house style -- a warning appearing or vanishing is a change, not a detail),
    each expected fragment somewhere in them, and every named value."""
    got = report.get("warnings", [])
    check(len(got) == n_warnings,
          f"{label}: expected {n_warnings} import warning(s), got {len(got)}: {got}")
    for fragment in fragments:
        check(any(fragment in w for w in got),
              f"{label}: no warning says {fragment!r}: {got}")
    for key, want in expected.items():
        check(report.get(key) == want,
              f"{label}: {key} is {report.get(key)!r}, expected {want!r}")


# One fixture per rule family, each file complete. The push-to-talk rules are
# read-every-keypress territory (#66) and the endpointing ones go straight into
# a live WebSocket config (#121), so both validate client-side -- and both were
# untested until #327.
_VALUE_CASES = [
    # (label, file contents, warnings, warning fragments, expected probe values)
    ("ptt-valid",
     {"push_to_talk": {"enabled": True, "trigger": "RCtrl", "insert": "send",
                       "tap_window_s": 0.5, "min_hold_s": 1, "release_tail_s": 0.05}},
     0, [],
     # The trigger name collapses to lower case and carries its VK along; an int
     # threshold is accepted and floated. `ep` empty: nothing configured, nothing
     # sent, and the live config JSON stays byte-identical to an unpersonalized one.
     {"ptt": [True, "rctrl", 0xA3, "send", 0.5, 1.0, 0.05], "ep": {}}),
    ("ptt-invalid",
     {"push_to_talk": {"enabled": "true", "trigger": "ctrl", "insert": "paste",
                       "tap_window_s": 0, "min_hold_s": True, "release_tail_s": "0.2"}},
     6,
     # Each entry names itself, so a log line points at the key to fix. The two
     # traps: a quoted "true" must not switch on an off-by-default feature
     # (bool("false") is True in Python), and a JSON true must not pass as the
     # number 1 -- a 1 s min-hold would be a behaviour change, not a typo.
     ["push_to_talk.enabled 'true' invalid",
      "push_to_talk.trigger 'ctrl' unknown",
      "push_to_talk.insert 'paste' unknown",
      "push_to_talk.tap_window_s '0' invalid (need a positive number)",
      "push_to_talk.min_hold_s 'True' invalid",
      "push_to_talk.release_tail_s '0.2' invalid"],
     {"ptt": _PTT_DEFAULTS}),
    ("ptt-null",
     # The asymmetry is deliberate and pinned as such: `enabled` reads through a
     # .get(key, DEFAULT), so an explicit null arrives as a non-boolean and warns,
     # while a threshold's `elif _v is not None` lets the same null through in
     # silence. Both end on the default; only one says so.
     {"push_to_talk": {"enabled": None, "tap_window_s": None}},
     1, ["push_to_talk.enabled 'None' invalid"],
     {"ptt": _PTT_DEFAULTS}),
    ("ep-bounds-low",
     {"soniox_endpointing": {"endpoint_sensitivity": -1.0,
                             "endpoint_latency_adjustment_level": 0,
                             "max_endpoint_delay_ms": 500}},
     0, [],
     # Level 0 is the #121 core: the params are built with `is not None`, never
     # truthiness, so a configured zero is sent rather than silently dropped.
     {"ep": {"endpoint_sensitivity": -1.0, "endpoint_latency_adjustment_level": 0,
             "max_endpoint_delay_ms": 500}}),
    ("ep-bounds-high",
     {"soniox_endpointing": {"endpoint_sensitivity": 0.0,
                             "endpoint_latency_adjustment_level": 3,
                             "max_endpoint_delay_ms": 3000}},
     0, [],
     {"ep": {"endpoint_sensitivity": 0.0, "endpoint_latency_adjustment_level": 3,
             "max_endpoint_delay_ms": 3000}}),
    ("ep-float-delay",
     # A number, sent as integer milliseconds -- the one conversion in the block.
     {"soniox_endpointing": {"max_endpoint_delay_ms": 1500.7}},
     0, [], {"ep": {"max_endpoint_delay_ms": 1500}}),
    ("ep-invalid",
     {"soniox_endpointing": {"endpoint_sensitivity": 1.5,
                             "endpoint_latency_adjustment_level": 1.0,
                             "max_endpoint_delay_ms": 400}},
     3,
     ["soniox_endpointing.endpoint_sensitivity '1.5' invalid (need a number -1.0..1.0)",
      "soniox_endpointing.endpoint_latency_adjustment_level '1.0' invalid "
      "(need an integer 0..3)",
      "soniox_endpointing.max_endpoint_delay_ms '400' invalid (need a number 500..3000)"],
     # Nothing is sent: an out-of-range value reaching Soniox would be rejected
     # and could drop the live session, which is why the range lives here.
     {"ep": {}}),
    ("ep-bools",
     # True is an int in Python and 1 is inside both ranges, so without the
     # explicit bool rejection all three of these would be sent as numbers.
     {"soniox_endpointing": {"endpoint_sensitivity": True,
                             "endpoint_latency_adjustment_level": True,
                             "max_endpoint_delay_ms": True}},
     3, ["endpoint_sensitivity 'True' invalid",
         "endpoint_latency_adjustment_level 'True' invalid",
         "max_endpoint_delay_ms 'True' invalid"],
     {"ep": {}}),
]


def test_settings_values_subprocess():
    """Every push_to_talk and soniox_endpointing rule, end to end: the file on
    disk in, the constants a running tool would use out.

    These two blocks are the file's only ones with real per-entry validation, and
    until #327 none of it was covered: the rules sit at module level in config.py,
    below the reader the in-process lanes exercise, so nothing short of a real
    `import config` runs them. What each fixture pins is in its comment."""
    tmp = tempfile.mkdtemp(prefix="tb_config_values_")
    try:
        _copy_config_into(tmp)
        for label, payload, n_warnings, fragments, expected in _VALUE_CASES:
            report = _probe_settings(f"values {label}", tmp,
                                     json.dumps(payload).encode("utf-8"))
            if report is not None:
                _check_report(f"values {label}", report, n_warnings, fragments, expected)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_vocabulary_filter_subprocess():
    """The comment filter on the real import path (#329): a file with a `_comment`
    beside its terms, read by a running tool, and what SONIOX_CONTEXT then holds.

    The lane above it covers the function, this one the wiring -- the build sits at
    module level, so only a real `import config` runs it. Its own lane rather than a
    `_VALUE_CASES` entry: those are the two blocks with per-entry validation rules,
    and this is a whole block becoming (or not becoming) one object. Both fixtures
    are silent, which is half the point: a comment is not a discarded setting."""
    tmp = tempfile.mkdtemp(prefix="tb_config_vocabulary_")
    try:
        _copy_config_into(tmp)
        for label, payload, context in (
                ("beside terms",
                 {"vocabulary": {"_comment": "my notes about this block",
                                 "terms": ["Fixture Term"]}},
                 {"terms": ["Fixture Term"]}),
                # A block holding nothing else reads like no block at all, so the
                # request stays the unpersonalized one it always was.
                ("alone", {"vocabulary": {"_comment": "my notes"}}, None)):
            report = _probe_settings(f"vocabulary {label}", tmp,
                                     json.dumps(payload).encode("utf-8"))
            if report is not None:
                _check_report(f"vocabulary {label}", report, 0, [],
                              {"context": context})
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# Frozen historical settings files (D-026's evolution rule, #327)
#
# Complete files in shapes that older releases -- and their documentation --
# produced or recommended, run through the current reader on every ladder run.
# The per-function lanes already guard the old shapes one rule at a time (the
# D-024 list rules in test_hotkey_overrides.py, say); what these add is the
# whole-file proof, which is what a user actually has on disk.
#
# The values are invented, only the shapes are historical: nothing here comes
# from anyone's real file. Every deliberate NARROWING of the value space deposits
# its own whole-file fixture here (D-026), so this list only ever grows.
# ======================================================================

_FROZEN_2026_07 = b"""{
  "_comment": "frozen fixture: the #55-era file shape (2026-07)",
  "vocabulary": {
    "general": [{"key": "domain", "value": "Voice Typing / Dictation"}],
    "terms": ["Fixture Term"]
  },
  "hotkeys": {
    "_comment": "one-element list -- what the F-key preset and every settings-window rebind wrote",
    "cancel_recording": ["ctrl+f9"],
    "exit_program": ["ctrl+alt+4", "ctrl+alt+q"],
    "test_transcription": "ctrl+alt+ue"
  },
  "defaults": {"api": "soniox-live"}
}
"""

_FROZEN_2026_04 = b"""{
  "_comment": "frozen fixture: the initial-release file shape (2026-04)",
  "vocabulary": {
    "general": [
      {"key": "domain", "value": "Voice Typing / Dictation"},
      {"key": "topic", "value": "Fixture Topic"}
    ],
    "terms": ["Fixture Name", "Fixture Acronym"]
  }
}
"""

_FROZEN_DOWNGRADE = b"""{
  "_comment": "frozen fixture: D-026's downgrade case -- a file this reader does not fully know. 'soniox-v4' really was an engine id once (it left AVAILABLE_APIS before defaults.api existed, so no supported route ever wrote this exact file); the shape, a retired value beside a block from another version, is what a rollback or a cloned checkout produces.",
  "defaults": {"api": "soniox-v4"},
  "dictation": {"language": "de"}
}
"""


def _frozen_cases():
    """The frozen files with what the current reader must make of them. Built
    from config's own defaults rather than written out, so the expectation says
    "the shipped default" and not a value that has to be chased when one moves."""
    voc_2026_07 = {"general": [{"key": "domain", "value": "Voice Typing / Dictation"}],
                   "terms": ["Fixture Term"]}
    voc_2026_04 = {"general": [{"key": "domain", "value": "Voice Typing / Dictation"},
                               {"key": "topic", "value": "Fixture Topic"}],
                   "terms": ["Fixture Name", "Fixture Acronym"]}
    return [
        # (label, file bytes, warnings, warning fragments, expected probe values)
        ("2026-07 hotkey-override era", _FROZEN_2026_07, 2,
         # Both of today's narrowings, on one file: D-024 (an action binds exactly
         # one combo) and D-023 (the layout-resolved 'ue' key lane is gone). The
         # one-element list is the boundary rule and stays SILENT -- an update may
         # never reset a key the user is still pressing.
         ["hotkeys.exit_program: an action binds exactly one combo",
          "hotkeys.test_transcription: 'ctrl+alt+ue' has an unrecognized key 'ue'"],
         {"hotkeys": dict(config.DEFAULT_HOTKEYS, cancel_recording="ctrl+f9"),
          # A pin ON the built-in default is still a pin (D-008: presence decides),
          # which is what makes it outrank a remembered engine.
          "api": ["soniox-live", True],
          "context": voc_2026_07,
          "ptt": _PTT_DEFAULTS, "ep": {}}),
        ("2026-04 initial release", _FROZEN_2026_04, 0, [],
         # The point of this one is the silence: the oldest documented user file
         # must never acquire so much as one warning line from a later change.
         {"hotkeys": dict(config.DEFAULT_HOTKEYS), "api": [config.BUILTIN_DEFAULT_API, False],
          "context": voc_2026_04, "ptt": _PTT_DEFAULTS, "ep": {}}),
        ("downgrade: retired value + unknown block", _FROZEN_DOWNGRADE, 2,
         ["defaults.api 'soniox-v4' unknown", "unknown block 'dictation'"],
         # Warn, default, carry on -- and the file itself is left alone, so it
         # still works in the newer release it came from.
         {"hotkeys": dict(config.DEFAULT_HOTKEYS), "api": [config.BUILTIN_DEFAULT_API, False],
          "context": None, "ptt": _PTT_DEFAULTS, "ep": {}}),
    ]


def test_frozen_historical_files():
    """Whole historical files through the real full load path, every run.

    D-026 makes settings evolution additive and gives every narrowing of the value
    space a frozen fixture as proof -- an old file that the current reader still
    has to make sense of. Per-rule lanes cannot show what a user experiences,
    which is one file meeting one program: warnings that name what was ignored,
    and everything else in force. The `_comment` keys are part of that -- each of
    these files carries them the way the shipped example recommends, and the whole
    file has to pass without a word about them."""
    tmp = tempfile.mkdtemp(prefix="tb_config_frozen_")
    try:
        _copy_config_into(tmp)
        for label, raw, n_warnings, fragments, expected in _frozen_cases():
            report = _probe_settings(f"frozen [{label}]", tmp, raw)
            if report is not None:
                _check_report(f"frozen [{label}]", report, n_warnings, fragments, expected)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# The .env lane: which file is loaded, in which encoding (#238)
# ======================================================================

# _IMPORT_PROBE's sibling. It reports what the .env lanes are about: the two key
# constants and the D-004 opt-out `import config` derived from the file, plus the
# env-var names the import created (an os.environ diff around it) -- which since #269
# must be NONE in every case, because nothing from .env may reach the process
# environment (D-017). Values are ASCII-escaped on the way out: a BOM glued to a key
# name then prints as a readable escape instead of breaking the pipe, and that escape
# IS the pre-#238 failure. `-` is the "absent" sentinel, so no fixture may use it as
# a value.
_ENV_PROBE = (
    "import os;"
    "before = set(os.environ);"
    "import config;"
    "esc = lambda s: s.encode('ascii', 'backslashreplace').decode();"
    "print('WARNINGS', len(config.IMPORT_WARNINGS));"
    "print('W0', esc(config.IMPORT_WARNINGS[0]) if config.IMPORT_WARNINGS else '-');"
    "print('NEWVARS', ','.join(esc(n) for n in sorted(set(os.environ) - before)) or '-');"
    "print('GROQ', esc(config.GROQ_API_KEY) if config.GROQ_API_KEY else '-');"
    "print('SONIOX', esc(config.SONIOX_API_KEY) if config.SONIOX_API_KEY else '-');"
    "print('OPTOUT', config.ALLOW_SECOND_INSTANCE)"
)

_ENV_GOOD = b"GROQ_API_KEY=dummy-a\nSONIOX_API_KEY=dummy-b\n"

# The three names that may only ever come out of the install directory's .env.
_ENV_BORNE = ("GROQ_API_KEY", "SONIOX_API_KEY", "THOUGHTBORNE_ALLOW_SECOND_INSTANCE")


def _env_install_tmp(prefix):
    """A tempdir with an importable `config` copy in `install/`, plus the environment
    its subprocess import must run with. Returns (tmp, install, env).

    The child environment keeps HOME and PATH -- a minimal env would change what the
    interpreter can reach -- and scrubs the three .env-borne names. **That scrub is
    load-bearing and must not be rationalized away**: without it, the inherited lane
    below would run against whatever the box really exports, so on a machine with a
    real SONIOX_API_KEY in the environment -- the very state that opened #269 -- this
    issue's regression lane would be handling a live key and could print it in a
    failure message. Scrubbed first, then injecting one fake value, the lane knows
    exactly what it is testing. (The checkout's own `.env` is never read either way:
    the copy's SCRIPT_DIR is the tempdir.)"""
    tmp = Path(tempfile.mkdtemp(prefix=prefix))
    inst = tmp / "install"
    inst.mkdir()
    _copy_config_into(inst)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    for name in _ENV_BORNE:
        env.pop(name, None)
    return tmp, inst, env


def _run_env_probe(label, inst, env, cwd=None):
    """One `import config` in a fresh interpreter against the copy in `inst`. Returns
    the probe's reported dict, or None after recording the failure -- a non-zero exit
    means the tool would not start on this `.env` at all."""
    try:
        proc = subprocess.run([sys.executable, "-c", _ENV_PROBE],
                              cwd=str(cwd or inst), env=env, capture_output=True,
                              text=True, timeout=120)
    except subprocess.TimeoutExpired:
        failures.append(f".env {label}: `import config` did not finish within 120 s")
        return None
    if proc.returncode != 0:
        failures.append(
            f".env {label}: `import config` exited {proc.returncode} -- the tool would "
            f"not start on this .env: {proc.stderr.strip()[-300:]}")
        return None
    reported = dict(line.split(" ", 1) for line in proc.stdout.split("\n") if " " in line)
    if SHOW:
        print(f"    {label}: {reported}")
    return reported


def _check_no_new_vars(label, reported):
    """The inverted #269 invariant: `import config` must create no environment
    variable at all. The export into os.environ is what let the settings app's own
    restart hand the relaunched tool a stale (or empty) inherited key that the loader
    then refused to override (D-017)."""
    names = reported.get("NEWVARS", "")
    got = set() if names in ("", "-") else set(names.split(","))
    check(not got,
          f".env {label}: `import config` created the environment variable(s) "
          f"{sorted(got)} -- nothing from .env may reach os.environ, or a tool "
          f"relaunched by the settings app inherits stale keys again (D-017)")


def test_env_loading_subprocess():
    """`.env` is loaded from the install directory, tolerantly, never fatally, and
    into config's own dict rather than into the process environment.

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
    tempdir, reporting the keys config ended up with and the env-var names the import
    created -- the latter now an inverted invariant (#269): none, ever. Three traps
    are baked into the fixtures and must survive future tidying:

    * The unreadable case is a **chmod'd file**, not the directory-in-its-place trick
      test_absent_and_unreadable uses. Both now warn, so the stand-in would no longer
      test nothing -- but it would test a different failure than the realistic one: a
      locked or permission-denied file is what an unreadable `.env` is on a user's box.
    * The layout is **two-level** -- the ancestor `.env` sits in the PARENT of the
      copy's directory -- and the ancestor case's child runs FROM that parent, reaching
      the copy through PYTHONPATH instead of through its cwd. Both halves carry weight.
      The layout is what makes the case red on the unfixed code: the old upward search
      starts at the child's cwd for a `-c` start and takes the first `.env` at or above
      it. The cwd is what makes it red on a `config.py` whose `SCRIPT_DIR / ".env"` was
      later "tidied" to a cwd-relative `Path(".env")` -- run from the install copy like
      its siblings, that regression would read the very directory it is supposed to and
      leave this lane green. That ancestor file carries **keys of its own**, present in
      every case: since #269 nothing is exported, so an upward search can no longer be
      spotted in NEWVARS -- it shows up as `ancestor-*` in GROQ/SONIOX instead, which
      also catches a failure lane that "helpfully" falls back to searching.
    * The child environment keeps **HOME and PATH** and scrubs only the three
      .env-borne names (_env_install_tmp). The utf-8 control case, which REQUIRES both
      keys to arrive, is the canary for a scaffolding that silently stopped reading the
      fixture at all.

    Fixtures carry obviously fake values, and the real `.env` is never read: the copy's
    SCRIPT_DIR is the tempdir."""
    # (label, file bytes or None for "no .env", warnings, fragment, GROQ, SONIOX)
    cases = [
        ("utf-8", _ENV_GOOD, 0, None, "dummy-a", "dummy-b"),
        ("bom", b"\xef\xbb\xbf" + _ENV_GOOD, 1, "BOM", "dummy-a", "dummy-b"),
        ("cp1252", '# Schlüssel\nGROQ_API_KEY=dummy-a\n'.encode("cp1252"),
         1, "Could not load", "-", "-"),
        ("unreadable", _ENV_GOOD, 1, "Could not load", "-", "-"),
        ("ancestor", None, 0, None, "-", "-"),
    ]

    tmp, inst, env = _env_install_tmp("tb_config_env_")
    env_file = inst / ".env"
    try:
        (tmp / ".env").write_bytes(b"GROQ_API_KEY=ancestor-groq\n"
                                   b"SONIOX_API_KEY=ancestor-soniox\n"
                                   b"TB238_ANCESTOR_SENTINEL=1\n")
        env.pop("TB238_ANCESTOR_SENTINEL", None)

        for label, raw, n_warnings, fragment, want_groq, want_soniox in cases:
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
            child_cwd, child_env = inst, env
            if label == "ancestor":
                # Run from the ancestor's directory (see the docstring): only from here
                # can this lane tell SCRIPT_DIR-anchoring from cwd-anchoring. PYTHONPATH
                # is prepended, not set -- it may be carrying something the child needs.
                inherited = env.get("PYTHONPATH", "")
                child_cwd = tmp
                child_env = dict(env, PYTHONPATH=os.pathsep.join(
                    [str(inst)] + ([inherited] if inherited else [])))
            try:
                reported = _run_env_probe(label, inst, child_env, cwd=child_cwd)
            finally:
                if label == "unreadable":
                    os.chmod(env_file, 0o600)
            if reported is None:
                continue
            check(reported.get("WARNINGS") == str(n_warnings),
                  f".env {label}: expected {n_warnings} import warning(s), got "
                  f"{reported.get('WARNINGS')} ({reported.get('W0')})")
            if fragment:
                check(fragment in reported.get("W0", ""),
                      f".env {label}: the warning does not mention '{fragment}': "
                      f"{reported.get('W0')}")
            _check_no_new_vars(label, reported)
            for name, want, got in (("GROQ_API_KEY", want_groq, reported.get("GROQ")),
                                    ("SONIOX_API_KEY", want_soniox, reported.get("SONIOX"))):
                check(got == want,
                      f".env {label}: config.{name} is {got!r}, expected {want!r}"
                      + (" -- an ancestor directory's .env is being picked up again"
                         if (got or "").startswith("ancestor-") else ""))
            if label == "bom":
                # Parity on the very bytes just written: what the settings app reads out
                # of this file is what the tool ends up with (D-002 -- the two must never
                # disagree about whether a key exists). Since #269 they share the parser,
                # so this asserts that sameness across the process boundary as well.
                app = sio.read_env(env_file)
                tool = {k: v for k, v in (("GROQ_API_KEY", reported.get("GROQ")),
                                          ("SONIOX_API_KEY", reported.get("SONIOX")))
                        if v not in (None, "-")}
                check(app == tool,
                      f".env bom: settings_io read {app} while the tool got {tool} -- "
                      f"the two halves disagree about a BOM'd file")
    finally:
        try:
            if env_file.exists():
                os.chmod(env_file, 0o600)
        except OSError:
            pass
        shutil.rmtree(tmp, ignore_errors=True)


# Every parsing rule of config.read_env_file, one fixture per rule (#269). The value
# side is what the two halves used to disagree about: a quoted value reached the tool
# unquoted but the settings window WITH its quotes (so `Test key` failed on it), an
# `export ` line was invisible to the settings app entirely (wizard mode on a keyed
# tool), and `${OTHER}` was resolved out of the process environment -- the backdoor
# D-017 closes. `-` means "config has no such key".
# (label, .env text (str -> utf-8) or bytes, expected settings_io.read_env, warnings)
_ENV_CORPUS = [
    ("plain",              "GROQ_API_KEY=dummy-a",       {"GROQ_API_KEY": "dummy-a"}, 0),
    ("double-quoted",      'GROQ_API_KEY="dummy-a"',     {"GROQ_API_KEY": "dummy-a"}, 0),
    ("single-quoted",      "GROQ_API_KEY='dummy-a'",     {"GROQ_API_KEY": "dummy-a"}, 0),
    ("export",             "export GROQ_API_KEY=dummy-a", {"GROQ_API_KEY": "dummy-a"}, 0),
    ("export-quoted",      'export GROQ_API_KEY="dummy-a"', {"GROQ_API_KEY": "dummy-a"}, 0),
    ("comment-tail",       "GROQ_API_KEY=dummy-a # note", {"GROQ_API_KEY": "dummy-a"}, 0),
    ("comment-tail-tab",   "GROQ_API_KEY=dummy-a\t#note", {"GROQ_API_KEY": "dummy-a"}, 0),
    ("hash-in-value",      "GROQ_API_KEY=dummy#a",       {"GROQ_API_KEY": "dummy#a"}, 0),
    ("hash-in-quotes",     'GROQ_API_KEY="du # my"',     {"GROQ_API_KEY": "du # my"}, 0),
    ("comment-after-quote", 'GROQ_API_KEY="dummy-a" # note', {"GROQ_API_KEY": "dummy-a"}, 0),
    # An opening quote with no partner is no quoting at all: the character stays in
    # the value, and since #328 the reader says so rather than keeping it silently.
    # The semantics are pinned deliberately -- changing them would change what a
    # running tool reads out of files that exist today (stability first).
    ("unterminated-quote", 'GROQ_API_KEY="dummy-a', {"GROQ_API_KEY": '"dummy-a'}, 1),
    ("unterminated-quote-comment", 'GROQ_API_KEY="dummy #x',
     {"GROQ_API_KEY": '"dummy'}, 1),
    ("interpolation",      "GROQ_API_KEY=${SONIOX_API_KEY}\nSONIOX_API_KEY=dummy-b",
     {"GROQ_API_KEY": "${SONIOX_API_KEY}", "SONIOX_API_KEY": "dummy-b"}, 0),
    ("blank",              "GROQ_API_KEY=",              {}, 0),
    ("blank-spaces",       "GROQ_API_KEY=   ",           {}, 0),
    ("empty-quotes",       'GROQ_API_KEY=""',            {}, 0),
    ("placeholder-comment", "GROQ_API_KEY= # paste your key here", {}, 0),
    ("no-equals",          "GROQ_API_KEY",               {}, 0),
    ("commented-out",      "#GROQ_API_KEY=dummy-a",      {}, 0),
    ("duplicate",          "GROQ_API_KEY=first\nGROQ_API_KEY=second",
     {"GROQ_API_KEY": "second"}, 0),
    ("duplicate-blank-last", "GROQ_API_KEY=first\nGROQ_API_KEY=", {}, 0),
    ("crlf",               b"GROQ_API_KEY=dummy-a\r\n",  {"GROQ_API_KEY": "dummy-a"}, 0),
    ("bom",                b"\xef\xbb\xbfGROQ_API_KEY=dummy-a\n",
     {"GROQ_API_KEY": "dummy-a"}, 1),
    ("tabs",               "\tGROQ_API_KEY\t=\tdummy-a\t", {"GROQ_API_KEY": "dummy-a"}, 0),
    ("indented",           "  GROQ_API_KEY=dummy-a",     {"GROQ_API_KEY": "dummy-a"}, 0),
]


def test_env_corpus_subprocess():
    """One parser, one meaning: every rule checked on the tool's side AND the settings
    app's, on the same bytes (#269 / D-017).

    Before #269 the tool parsed `.env` with python-dotenv and the settings app with its
    own stdlib reader, and they disagreed exactly where a hand-edited file gets
    creative -- an `export ` line the app could not see at all, a quoted value it showed
    WITH the quotes, a comment tail it kept. Each disagreement is a split brain the user
    cannot see: the masthead says keyed, the settings window says keyless, and both are
    right about their own source. `settings_io.read_env` is now a thin call into
    `config.read_env_file`, so parity holds by construction -- and is asserted anyway,
    across the process boundary, because "by construction" is a claim about today's
    code.

    The tool's side is a real `import config` per fixture (the same ~35 ms subprocess
    the lane above uses), so what is asserted is the constant a transcriber would
    actually get, not the reader in isolation. The settings app's side runs in-process
    against the fixture path -- never against the checkout's own `.env`, whose real keys
    must never reach a failure message."""
    tmp, inst, env = _env_install_tmp("tb_config_corpus_")
    env_file = inst / ".env"
    try:
        for label, raw, expected, n_warnings in _ENV_CORPUS:
            env_file.write_bytes(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
            reported = _run_env_probe(f"corpus {label}", inst, env)
            if reported is None:
                continue
            check(reported.get("WARNINGS") == str(n_warnings),
                  f".env corpus {label}: expected {n_warnings} import warning(s), got "
                  f"{reported.get('WARNINGS')} ({reported.get('W0')})")
            _check_no_new_vars(f"corpus {label}", reported)
            for name, probe_key in (("GROQ_API_KEY", "GROQ"), ("SONIOX_API_KEY", "SONIOX")):
                want = expected.get(name, "-")
                got = reported.get(probe_key)
                check(got == want,
                      f".env corpus {label} ({raw!r}): config.{name} is {got!r}, "
                      f"expected {want!r}")
            app = sio.read_env(env_file)
            check(app == expected,
                  f".env corpus {label} ({raw!r}): the settings app reads {app}, "
                  f"expected {expected} -- the two halves parse the same bytes "
                  f"differently again")
            if label == "interpolation":
                # The reason this row exists: python-dotenv expanded ${OTHER} against
                # os.environ, so a .env could pull a value out of the very environment
                # D-017 rules out (single quotes did not protect it either).
                check(reported.get("GROQ") != "dummy-b",
                      ".env corpus interpolation: ${SONIOX_API_KEY} was expanded -- a "
                      "value must be used exactly as written (D-017)")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_env_inherited_subprocess():
    """The regression lane for #269: a key in the process environment never counts.

    Two real incidents, one mechanism. A leftover Windows user variable
    `SONIOX_API_KEY` made the tool start keyed while the settings app -- which only
    ever read the file -- opened its first-run wizard with empty fields. And the
    settings app's own restart lane did it without any Windows variable at all: the
    wizard's first save seeded `.env` from the example, whose blank `SONIOX_API_KEY=`
    line the old loader exported as an EMPTY STRING; the settings app inherited it, the
    tool it relaunched inherited it in turn, and `override=False` kept that empty
    string over the real key the same save had just written. Nothing on screen said so,
    and only a cold start ever fixed it -- variant (c) is exactly that case.

    The child environment here carries the variable on purpose (the one place this
    driver does not scrub it), so a regression to any environment fallback is red."""
    variants = [
        # (label, inherited value, .env bytes or None, expected config.SONIOX_API_KEY)
        ("no-file", "from-env", None, "-"),
        ("different", "from-env", b"SONIOX_API_KEY=from-file\n", "from-file"),
        ("empty", "", b"SONIOX_API_KEY=from-file\n", "from-file"),
    ]
    tmp, inst, env = _env_install_tmp("tb_config_inherited_")
    env_file = inst / ".env"
    try:
        for label, inherited, raw, want in variants:
            if raw is None:
                env_file.unlink(missing_ok=True)
            else:
                env_file.write_bytes(raw)
            child_env = dict(env, SONIOX_API_KEY=inherited)
            reported = _run_env_probe(f"inherited {label}", inst, child_env)
            if reported is None:
                continue
            got = reported.get("SONIOX")
            check(got == want,
                  f".env inherited {label}: config.SONIOX_API_KEY is {got!r}, expected "
                  f"{want!r} -- the process environment is not a key source (D-017)")
            _check_no_new_vars(f"inherited {label}", reported)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_env_optout_subprocess():
    """The D-004 developer opt-out keeps its documented `.env` route (#166, #269).

    It used to work only as a side effect: the old loader put `.env` into os.environ
    and `thoughtborne.py` read it back with `os.getenv`. With nothing exported any
    more, that route had to be rebuilt deliberately -- `config.ALLOW_SECOND_INSTANCE`
    now comes from the same parsed dict as the keys, with the same truthiness rule.
    The last case is the other half of D-017: set only in the shell, the opt-out no
    longer counts (a documented consequence, not an oversight)."""
    cases = [
        # (label, .env bytes, inherited value or None, expected ALLOW_SECOND_INSTANCE)
        ("on", b"THOUGHTBORNE_ALLOW_SECOND_INSTANCE=1\n", None, "True"),
        ("absent", b"GROQ_API_KEY=dummy-a\n", None, "False"),
        ("blank", b"THOUGHTBORNE_ALLOW_SECOND_INSTANCE=\n", None, "False"),
        ("false", b"THOUGHTBORNE_ALLOW_SECOND_INSTANCE=false\n", None, "False"),
        ("shell-only", b"GROQ_API_KEY=dummy-a\n", "1", "False"),
    ]
    tmp, inst, env = _env_install_tmp("tb_config_optout_")
    env_file = inst / ".env"
    try:
        for label, raw, inherited, want in cases:
            env_file.write_bytes(raw)
            child_env = env if inherited is None else dict(
                env, THOUGHTBORNE_ALLOW_SECOND_INSTANCE=inherited)
            reported = _run_env_probe(f"optout {label}", inst, child_env)
            if reported is None:
                continue
            got = reported.get("OPTOUT")
            check(got == want,
                  f".env optout {label}: config.ALLOW_SECOND_INSTANCE is {got}, "
                  f"expected {want}"
                  + (" -- a shell variable of that name must not bypass the "
                     "single-instance guard (D-017)" if label == "shell-only" else ""))
            _check_no_new_vars(f"optout {label}", reported)
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
    modules): main() must call the replay, config.py must keep logging nothing at
    import time -- an import-time log call has no handler yet and would fall to
    stderr, which is exactly the lane #206 closed (and the one #238 inherits) -- and
    the four guards below keep the .env reader anchored (G1), python-dotenv out of
    the tree (G2), the process environment out of the key path (G3, D-017) and the
    D-004 opt-out consulted where it is consumed (G4)."""
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

    # G1 -- the reader's call shape (#238, #269). The behavioural lanes above cover
    # this too, and no longer skip on any box, but the shape is cheap to pin and says
    # in one place what the import-time contract is: exactly one call, at module level
    # (the constants 200 lines below read its result), anchored on the install
    # directory, in the tolerant encoding.
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "read_env_file"]
    check(len(calls) == 1,
          f"config.py makes {len(calls)} read_env_file() calls, expected exactly one")
    for call in calls:
        check(not any(s.lineno <= call.lineno <= (s.end_lineno or s.lineno) for s in scopes),
              f"config.py:{call.lineno}: read_env_file() moved inside a function -- the "
              f"keys must be parsed at import time, before the constants below read them")
        arg = call.args[0] if call.args else None
        check(isinstance(arg, ast.BinOp) and isinstance(arg.op, ast.Div)
              and isinstance(arg.left, ast.Name) and arg.left.id == "SCRIPT_DIR"
              and isinstance(arg.right, ast.Constant) and arg.right.value == ".env",
              f"config.py:{call.lineno}: read_env_file() is no longer called on "
              f"SCRIPT_DIR / \".env\" -- a cwd-relative path or an upward search would "
              f"load some other directory's .env, which no repair surface shows (#238)")
        enc = [kw.value for kw in call.keywords if kw.arg == "encoding"]
        check(len(enc) == 1 and isinstance(enc[0], ast.Constant)
              and enc[0].value == "utf-8-sig",
              f"config.py:{call.lineno}: read_env_file() no longer passes "
              f"encoding=\"utf-8-sig\" -- a BOM'd .env would rename the first key and "
              f"the console would report 'no key' while the settings window shows one")

    # G2 -- python-dotenv is gone (#269), and must not creep back into a driver either:
    # the .env lanes above used to skip without it, which is exactly the hollow pass a
    # re-added import would restore. Every *.py in the repo root, not a hand-kept list.
    offenders = []
    for path in sorted(root.glob("*.py")):
        try:
            t = ast.parse(path.read_text(encoding="utf-8"))
        except Exception as e:
            failures.append(f"could not parse {path.name}: {type(e).__name__}: {e}")
            continue
        for n in ast.walk(t):
            if isinstance(n, ast.Import):
                if any(a.name.split(".")[0] == "dotenv" for a in n.names):
                    offenders.append(f"{path.name}:{n.lineno}")
            elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] == "dotenv":
                offenders.append(f"{path.name}:{n.lineno}")
    check(not offenders,
          f"python-dotenv is imported again at {', '.join(offenders)} -- config."
          f"read_env_file is the one .env parser since #269, and the package is no "
          f"longer a dependency (D-017)")

    # G3 -- no process-environment read of the three .env-borne names, in either half
    # (#269 / D-017). Name-based on purpose, not shape-based: `{**os.environ, ...}` in
    # the settings-app spawn and the THOUGHTBORNE_SPAWN_TS read in the app are process
    # plumbing that must keep working, so a blanket "no os.environ here" guard would be
    # wrong. Catches `os.getenv(NAME)`, `os.environ.get(NAME)` and `os.environ[NAME]`.
    def _reads_os_environ(node):
        """(kind, name) if `node` reads a process-environment variable by literal
        name, else None."""
        def _is_environ(n):
            return (isinstance(n, ast.Attribute) and n.attr == "environ"
                    and isinstance(n.value, ast.Name) and n.value.id == "os")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            getter = ((f.attr == "getenv" and isinstance(f.value, ast.Name)
                       and f.value.id == "os")
                      or (f.attr == "get" and _is_environ(f.value)))
            if getter and node.args and isinstance(node.args[0], ast.Constant):
                return (f"os.{f.attr}", node.args[0].value)
        if isinstance(node, ast.Subscript) and _is_environ(node.value) \
                and isinstance(node.slice, ast.Constant):
            return ("os.environ[...]", node.slice.value)
        return None

    trees = {}   # kept for G4 below, which reads one of these files again
    for name in ("config.py", "transcriber.py", "thoughtborne.py", "settings_io.py",
                 "thoughtborne_settings.py"):
        try:
            t = ast.parse((root / name).read_text(encoding="utf-8"))
        except Exception as e:
            failures.append(f"could not parse {name}: {type(e).__name__}: {e}")
            continue
        trees[name] = t
        for n in ast.walk(t):
            hit = _reads_os_environ(n)
            if hit and hit[1] in _ENV_BORNE:
                failures.append(
                    f"{name}:{n.lineno}: {hit[0]}({hit[1]!r}) -- {hit[1]} comes from the "
                    f"install directory's .env and from nowhere else (D-017); an "
                    f"inherited value silently defeating a key rotation is the failure "
                    f"#269 closed")

    # G4 -- the consumption side of the D-004 opt-out (#269, #289). The production side
    # is checked above (config.ALLOW_SECOND_INSTANCE follows the .env); nothing said
    # thoughtborne.py still CONSULTS it, and a bypass removed wholesale would stay
    # silent off Windows -- the function is Win32/ctypes and cannot be imported here.
    # Static, and honest about it: it says the wiring exists and that the guard still
    # fails open, not that the mutex behaves. That stays hands-on Windows work.
    t_tree = trees.get("thoughtborne.py")
    if t_tree is None:
        return   # the loop above already parsed it, and already reported the failure
    fn = next((n for n in ast.walk(t_tree) if isinstance(n, ast.FunctionDef)
               and n.name == "_second_instance_running"), None)
    check(fn is not None,
          "thoughtborne.py no longer defines _second_instance_running -- the D-004 guard "
          "is the one thing keeping a second, hotkey-deaf instance from starting")
    if fn is None:
        return
    # Load context, so the check says what its message says: a leftover assignment or
    # a name in dead code would satisfy a bare "the name appears here".
    check(any(isinstance(n, ast.Name) and n.id == "ALLOW_SECOND_INSTANCE"
              and isinstance(n.ctx, ast.Load)
              for n in ast.walk(fn)),
          "_second_instance_running no longer reads ALLOW_SECOND_INSTANCE -- the documented "
          "developer opt-out (D-004, routed through .env since D-017) would be gone with "
          "nothing on any surface saying so")
    # The function's OWN try/except, not any nested one: an inner handler returning
    # False says nothing about whether the function as a whole still fails open.
    handlers = [h for n in fn.body if isinstance(n, ast.Try) for h in n.handlers]
    check(any(isinstance(s, ast.Return) and isinstance(s.value, ast.Constant)
              and s.value.value is False
              for h in handlers for s in ast.walk(h)),
          "_second_instance_running's except handler no longer returns False -- D-004 "
          "requires the guard to fail open, so a fault in it can never block a legitimate start")


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
# The version lane: pyproject.toml, read exactly as setup.ps1 reads it (#268, #281)
# ======================================================================

# What both halves match on. Kept as one literal so the drift guard below can look
# for the very same characters in the PowerShell source.
_VERSION_PATTERN = r'''^\s*version\s*=\s*"([^"]+)"'''


def test_version_reader(d):
    """`config.read_version` against hand-shaped pyproject.toml fixtures.

    The real file first (the version the settings app will show must be a stripped,
    non-empty string), then the cases that decide whether a display is a fact or a
    guess. Fail-open is the rule: absent, undecodable, a directory in the file's
    place, no `version =` line at all, or an empty value all yield None -- "no
    version" has exactly one shape, so no surface can invent a distinction between
    missing and empty. And the line anchor has to hold: a commented-out version above
    the real one must not win (`#` is not whitespace), and `requires-python` must
    never be mistaken for it."""
    real = config.read_version()
    check(isinstance(real, str) and real == real.strip() and real,
          f"the checkout's own pyproject.toml yields {real!r}, not a clean version string")

    pp = Path(d) / "pyproject.toml"
    cases = [
        ("plain", b'[project]\nname = "thoughtborne"\nversion = "1.2.3"\n', "1.2.3"),
        ("indented", b'[project]\n  version   =   "1.2.3"  \n', "1.2.3"),
        ("crlf", b'[project]\r\nversion = "1.2.3"\r\n', "1.2.3"),
        ("bom", b'\xef\xbb\xbf[project]\nversion = "1.2.3"\n', "1.2.3"),
        ("commented-out above", b'# version = "9.9.9"\nversion = "1.2.3"\n', "1.2.3"),
        ("requires-python only",
         b'[project]\nrequires-python = ">=3.10,<3.14"\n', None),
        ("empty value", b'[project]\nversion = ""\n', None),
        ("no version line", b'[project]\nname = "thoughtborne"\n', None),
        ("undecodable", '[project]\nversion = "1.2.3"\n'.encode("utf-16"), None),
        ("empty file", b"", None),
    ]
    for label, raw, expected in cases:
        pp.write_bytes(raw)
        got = config.read_version(pp)
        if SHOW:
            print(f"    {label}: {raw[:40]!r} -> {got!r}")
        check(got == expected,
              f"pyproject.toml [{label}]: read_version -> {got!r}, expected {expected!r}")

    pp.unlink()
    check(config.read_version(pp) is None,
          "an absent pyproject.toml must read as None, not raise")
    check(config.read_version(Path(d)) is None,
          "a directory in the file's place must read as None, not raise")


def test_version_drift_guard():
    """The point of the lane: `setup.ps1` writes the same version into the
    Installed-apps `DisplayVersion` (its Get-InstalledVersion), read out of the same
    installed `pyproject.toml`. Two readers of one fact drift silently -- a `[tool.x]`
    table gaining a `version =` line would move one and not the other -- so the
    pattern is pinned character for character in both. Since #306 there is also a
    WRITER: `build-release-zip.sh --dev` stamps `+dev.<sha>` onto that same line. If
    its expression drifts, the stamp lands on a line neither reader reads and the
    dev install reports the bare release number -- silently the very thing #306
    exists to prevent. Read as text; no PowerShell and no bash run here."""
    check(_VERSION_PATTERN in config._VERSION_RE.pattern,
          f"config._VERSION_RE is {config._VERSION_RE.pattern!r}, which no longer "
          f"contains setup.ps1's pattern {_VERSION_PATTERN!r}")
    path = Path(__file__).resolve().parent / "setup.ps1"
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as e:
        failures.append(f"could not read setup.ps1: {type(e).__name__}: {e}")
        return
    check(_VERSION_PATTERN in src,
          "setup.ps1 no longer matches the version with "
          f"{_VERSION_PATTERN!r} -- it and config.read_version must read "
          "pyproject.toml the same way")
    builder = Path(__file__).resolve().parent / "build-release-zip.sh"
    try:
        bsrc = builder.read_text(encoding="utf-8")
    except OSError as e:
        failures.append(f"could not read build-release-zip.sh: {type(e).__name__}: {e}")
        return
    check(_VERSION_PATTERN in bsrc,
          "build-release-zip.sh --dev no longer stamps the version with "
          f"{_VERSION_PATTERN!r} -- the stamp would land on a line neither reader reads")


def test_version_import_subprocess():
    """The acceptance clause, literally: a broken pyproject.toml must not cost a
    start. A fresh interpreter imports the copied config beside each fixture and
    reports config.VERSION -- a non-zero exit is the tool refusing to start over a
    cosmetic file, and a warning would be noise about a file the user never edits."""
    probe = ("import config;"
             "print('VERSION', config.VERSION if config.VERSION else '-');"
             "print('WARNINGS', len(config.IMPORT_WARNINGS))")
    cases = [
        ("valid", b'[project]\nversion = "9.8.7"\n', "9.8.7"),
        ("absent", None, "-"),
        ("undecodable", b'\xff\xfe[project]\nversion = "9.8.7"\n', "-"),
        ("unparseable", b"not a toml file at all\n", "-"),
    ]
    tmp = tempfile.mkdtemp(prefix="tb_config_version_")
    try:
        _copy_config_into(tmp)
        pp = Path(tmp) / "pyproject.toml"
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for label, raw, expected in cases:
            if raw is None:
                pp.unlink(missing_ok=True)
            else:
                pp.write_bytes(raw)
            proc = subprocess.run([sys.executable, "-c", probe], cwd=tmp, env=env,
                                  capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                failures.append(
                    f"version [{label}]: `import config` exited {proc.returncode} -- "
                    f"an unreadable version must never cost a start: "
                    f"{proc.stderr.strip()[-300:]}")
                continue
            reported = dict(line.split(" ", 1) for line in proc.stdout.split("\n") if " " in line)
            if SHOW:
                print(f"    {label}: {reported}")
            check(reported.get("VERSION") == expected,
                  f"version [{label}]: config.VERSION is {reported.get('VERSION')!r}, "
                  f"expected {expected!r}")
            check(reported.get("WARNINGS") == "0",
                  f"version [{label}]: a missing version is cosmetic and must not "
                  f"add an import warning (got {reported.get('WARNINGS')})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ======================================================================
# The checkout lane: the `.git` beside the script, read with the stdlib (#297)
# ======================================================================

# The reflog timestamp is formatted in the reader's local time zone, so the
# expectation is derived the same way rather than written out -- CI runs in UTC.
_TS = 1757356080
_WHEN = datetime.fromtimestamp(_TS).strftime("%Y-%m-%d %H:%M")
_SHA = b"aa8f43a1c0ffee00d15ea5e0000000000badc0de"
_SHORT = "aa8f43a"
_REFLOG = (b"0" * 40 + b" " + _SHA + b" Tim Wessels <t@example.com> "
           + str(_TS).encode() + b" +0200\tcheckout: moving from x to main\n")


def _write_tree(root, files):
    """Write one fixture layout: {relative path: bytes}, directories as needed.
    A path the dict does not name is a file the layout simply does not have."""
    for rel, raw in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(raw)


def test_checkout_reader(d):
    """`config.read_checkout_state` against hand-built `.git` layouts.

    Every shape git itself writes has to resolve -- a loose ref, a packed one, a
    detached HEAD, a `.git` file pointing elsewhere -- and every other shape has to
    yield None rather than raise, because this display is cosmetic and must never
    cost a start. The two halves fail independently in both directions: an
    unreadable reflog still leaves the commit, an unreadable HEAD still leaves the
    timestamp. A linked worktree is the one shape git writes that the reader gives
    up on -- its branch ref lives in the main gitdir, so it names no commit and
    reads like an install; pinned here so the giving-up stays a decision. Two cases
    are about where the reader may look: a `ref:` value outside `refs/` is never
    followed (git writes nothing else there, and following one would read a foreign
    file), and a `.git` in a PARENT directory is not this checkout's -- #238's
    lesson, where an install nested in another code tree inherited that tree's
    state. Each of those puts a sha-looking file at the foreign target, so the case
    goes red when the guard goes instead of passing on the target's shape.
    """
    loose = {".git/HEAD": b"ref: refs/heads/main\n",
             ".git/refs/heads/main": _SHA + b"\n",
             ".git/logs/HEAD": _REFLOG}
    cases = [
        ("loose ref + reflog", loose, (_SHORT, _WHEN)),
        ("detached HEAD", {".git/HEAD": _SHA + b"\n"}, (_SHORT, None)),
        # git writes its shas lowercase; the reader shows what it validated.
        ("detached HEAD, uppercase", {".git/HEAD": _SHA.upper() + b"\n"},
         (_SHORT, None)),
        ("packed-refs", {".git/HEAD": b"ref: refs/heads/main\n",
                         ".git/packed-refs": (b"# pack-refs with: peeled fully-peeled sorted\n"
                                              + _SHA + b" refs/heads/main\n"
                                              + b"^" + b"0" * 40 + b"\n")},
         (_SHORT, None)),
        ("packed-refs without the ref",
         {".git/HEAD": b"ref: refs/heads/main\n",
          ".git/packed-refs": _SHA + b" refs/heads/other\n"}, (None, None)),
        ("unborn branch", {".git/HEAD": b"ref: refs/heads/main\n"}, (None, None)),
        (".git file, relative gitdir",
         {".git": b"gitdir: real-git\n", "real-git/HEAD": _SHA + b"\n"}, (_SHORT, None)),
        # As `git worktree add` writes it: HEAD and the reflog in the worktree's
        # own gitdir, the branch ref one `commondir` hop away in the main one.
        ("linked worktree",
         {".git": b"gitdir: gitmain/worktrees/side\n",
          "gitmain/refs/heads/side": _SHA + b"\n",
          "gitmain/worktrees/side/HEAD": b"ref: refs/heads/side\n",
          "gitmain/worktrees/side/commondir": b"../..\n",
          "gitmain/worktrees/side/logs/HEAD": _REFLOG}, (None, _WHEN)),
        (".git file with junk", {".git": b"not a gitdir line\n"}, (None, None)),
        ("no .git", {}, (None, None)),
        (".git without HEAD", {".git/config": b"[core]\n"}, (None, None)),
        ("HEAD junk", {".git/HEAD": b"\x01\x02 not a ref\n"}, (None, None)),
        ("HEAD sha too short", {".git/HEAD": b"aa8f43\n"}, (None, None)),
        ("crlf throughout", {".git/HEAD": b"ref: refs/heads/main\r\n",
                             ".git/refs/heads/main": _SHA + b"\r\n",
                             ".git/logs/HEAD": _REFLOG.replace(b"\n", b"\r\n")},
         (_SHORT, _WHEN)),
        ("undecodable HEAD",
         {".git/HEAD": "ref: refs/heads/main\n".encode("utf-16")}, (None, None)),
        ("undecodable HEAD, intact reflog",
         {".git/HEAD": "ref: refs/heads/main\n".encode("utf-16"),
          ".git/logs/HEAD": _REFLOG}, (None, _WHEN)),
        ("empty reflog", dict(loose, **{".git/logs/HEAD": b""}), (_SHORT, None)),
        ("reflog junk in the timestamp field",
         dict(loose, **{".git/logs/HEAD": b"0 1 Tim <t@e> nine +0200\tcheckout\n"}),
         (_SHORT, None)),
        ("reflog timestamp out of range",
         dict(loose, **{".git/logs/HEAD": b"0 1 Tim <t@e> 99999999999 +0200\tcheckout\n"}),
         (_SHORT, None)),
        ("reflog cut mid-line",
         dict(loose, **{".git/logs/HEAD": _REFLOG + b"0 1 Tim Wessels <t@e> 17629"}),
         (_SHORT, None)),
        ("no logs/ at all", {k: v for k, v in loose.items() if "logs" not in k},
         (_SHORT, None)),
        ("ref: outside refs/", {".git/HEAD": b"ref: objects/evil\n",
                                ".git/objects/evil": _SHA + b"\n"}, (None, None)),
        ("ref: with a .. traversal",
         {".git/HEAD": b"ref: refs/../sneaky\n", ".git/refs/heads/main": _SHA + b"\n",
          ".git/sneaky": _SHA + b"\n"}, (None, None)),
    ]
    root = Path(d) / "checkout"
    for label, files, expected in cases:
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True)
        _write_tree(root, files)
        got = config.read_checkout_state(root)
        if SHOW:
            print(f"    {label}: -> {got!r}")
        check(got == expected,
              f".git [{label}]: read_checkout_state -> {got!r}, expected {expected!r}")

    # An absolute gitdir: written after the tree exists, so the path is real.
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    real = Path(d) / "elsewhere"
    _write_tree(real, {"HEAD": _SHA + b"\n"})
    (root / ".git").write_text(f"gitdir: {real}\n", encoding="utf-8")
    got = config.read_checkout_state(root)
    check(got == (_SHORT, None),
          f".git [file, absolute gitdir]: read_checkout_state -> {got!r}")

    # A .git one level up belongs to the tree around the install, not to it.
    shutil.rmtree(root, ignore_errors=True)
    nested = root / "thoughtborne"
    nested.mkdir(parents=True)
    _write_tree(root, loose)
    got = config.read_checkout_state(nested)
    check(got == (None, None),
          f".git [in the parent directory]: read_checkout_state -> {got!r}, and an "
          f"install nested in another code tree must never report that tree's state")
    shutil.rmtree(root, ignore_errors=True)

    # The checkout this driver runs in, when it is one: a real repository has to
    # resolve, or the reader works on fixtures alone. An exported tree has no
    # `.git` and skips -- run_tests.py counts the note.
    here = Path(__file__).resolve().parent
    if (here / ".git").exists():
        sha, _moved = config.read_checkout_state(here)
        check(isinstance(sha, str) and len(sha) == 7
              and all(c in "0123456789abcdef" for c in sha),
              f"this checkout's own .git yields {sha!r}, not a short commit id")
    else:
        print("    (skipped the real-repository case: no .git beside this driver)")


def test_checkout_import_subprocess():
    """The two display strings as a fresh interpreter builds them, which is the
    acceptance clause: an installed copy (no `.git`) shows the release version and
    nothing more, a checkout adds the commit it points at, and the log line carries
    the precision the masthead has no room for. A broken or absent `.git` is as
    cosmetic as an unreadable version -- exit 0, and no import warning either."""
    probe = ("import config;"
             "print('DISPLAY', config.VERSION_DISPLAY or '-');"
             "print('LOG', config.VERSION_LOG);"
             "print('WARNINGS', len(config.IMPORT_WARNINGS))")
    checkout = {".git/HEAD": b"ref: refs/heads/main\n",
                ".git/refs/heads/main": _SHA + b"\n",
                ".git/logs/HEAD": _REFLOG}
    cases = [
        ("checkout", b'[project]\nversion = "9.8.7"\n', checkout,
         f"v9.8.7+{_SHORT}", f"9.8.7 (checkout {_SHORT}, last moved {_WHEN})"),
        ("installed copy", b'[project]\nversion = "9.8.7"\n', {}, "v9.8.7", "9.8.7"),
        ("broken .git", b'[project]\nversion = "9.8.7"\n',
         {".git/HEAD": b"garbage\n"}, "v9.8.7", "9.8.7"),
        ("checkout, no version", None, checkout,
         "-", f"unknown (checkout {_SHORT}, last moved {_WHEN})"),
    ]
    tmp = tempfile.mkdtemp(prefix="tb_config_checkout_")
    try:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for label, raw, files, want_display, want_log in cases:
            root = Path(tmp) / "tree"
            shutil.rmtree(root, ignore_errors=True)
            root.mkdir(parents=True)
            _copy_config_into(root)
            if raw is not None:
                (root / "pyproject.toml").write_bytes(raw)
            _write_tree(root, files)
            proc = subprocess.run([sys.executable, "-c", probe], cwd=root, env=env,
                                  capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:
                failures.append(
                    f"checkout [{label}]: `import config` exited {proc.returncode} -- "
                    f"an unreadable checkout state must never cost a start: "
                    f"{proc.stderr.strip()[-300:]}")
                continue
            reported = dict(line.split(" ", 1)
                            for line in proc.stdout.split("\n") if " " in line)
            if SHOW:
                print(f"    {label}: {reported}")
            check(reported.get("DISPLAY") == want_display,
                  f"checkout [{label}]: config.VERSION_DISPLAY is "
                  f"{reported.get('DISPLAY')!r}, expected {want_display!r}")
            check(reported.get("LOG") == want_log,
                  f"checkout [{label}]: config.VERSION_LOG is "
                  f"{reported.get('LOG')!r}, expected {want_log!r}")
            check(reported.get("WARNINGS") == "0",
                  f"checkout [{label}]: a missing or broken .git is cosmetic and must "
                  f"not add an import warning (got {reported.get('WARNINGS')})")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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


# ======================================================================
# The consumer lane, part two: the vocabulary's last mile into the request
# ======================================================================

class _FakeResponse:
    """The two httpx.Response members the Soniox async path touches."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeWebSocket:
    """A websockets sync connection as start_session uses it: it sends the config
    JSON, then the receiver thread reads until a 'finished' message. Answering
    that on the first recv ends the receiver at once, so the lane needs no
    timeouts and leaves no thread behind."""

    def __init__(self, sent):
        self._sent = sent

    def send(self, message):
        self._sent.append(message)

    def recv(self, timeout=None):
        return json.dumps({"finished": True})

    def close(self):
        return None


def test_soniox_context_async_request():
    """The vocabulary block's last mile: what the reader kept has to arrive in the
    real request body, and what it dropped has to be absent from it.

    The reader lane above stops at SONIOX_CONTEXT; the constructor lane stops at
    the constructor. In between sits the one line that decides whether a user's
    terms are actually used -- and its `if SONIOX_CONTEXT:` is also what keeps an
    unpersonalized request byte-identical to what it always was. Driven through
    the production `transcribe()` with httpx's three verbs replaced, so no
    request leaves the machine; the fake key never leaves the process and the
    archive folder is redirected, so nothing is written into the checkout."""
    try:
        import transcriber
    except ModuleNotFoundError as e:
        print(f"    (skipped the Soniox async request lane: '{e.name}' is not "
              f"installed off Windows)")
        return
    import httpx

    bodies = []

    def fake_post(url, **kwargs):
        if url.endswith("/v1/files"):
            return _FakeResponse({"id": "file-fixture"})
        bodies.append(kwargs.get("json"))
        return _FakeResponse({"id": "tx-fixture"})

    def fake_get(url, **kwargs):
        if url.endswith("/transcript"):
            return _FakeResponse({"text": "fixture transcript"})
        return _FakeResponse({"status": "completed"})

    def fake_delete(url, **kwargs):
        return _FakeResponse({})

    saved_tx = (transcriber.SONIOX_CONTEXT, transcriber.SONIOX_API_KEY,
                transcriber.TEXT_ARCHIVE_FOLDER)
    saved_httpx = (httpx.post, httpx.get, httpx.delete)
    tmp = tempfile.mkdtemp(prefix="tb_config_async_ctx_")
    try:
        transcriber.SONIOX_API_KEY = "not-a-real-key"
        transcriber.TEXT_ARCHIVE_FOLDER = Path(tmp) / "transcripts"
        httpx.post, httpx.get, httpx.delete = fake_post, fake_get, fake_delete
        audio = Path(tmp) / "fixture.wav"
        audio.write_bytes(b"")
        for label, ctx in (
                ("with a vocabulary", {"terms": ["Claude-MD"]}),
                # Built by the production filter (#329), so filter and request meet
                # in one lane: the user's comment is not in what leaves the machine.
                ("with a commented vocabulary", config.effective_soniox_context(
                    {"_comment": "user prose", "terms": ["Claude-MD"]})),
                ("without one", None)):
            bodies.clear()
            transcriber.SONIOX_CONTEXT = ctx
            text = transcriber.SonioxAsyncTranscriber().transcribe(str(audio), 1.0)
            check(text == "fixture transcript",
                  f"async {label}: transcribe() returned {text!r} -- the lane is no "
                  f"longer driving the real path it means to assert on")
            check(len(bodies) == 1,
                  f"async {label}: {len(bodies)} transcription request(s), expected one")
            body = bodies[0] if bodies else {}
            check(body.get("model") == config.SONIOX_ASYNC_MODEL,
                  f"async {label}: the request names model {body.get('model')!r}")
            check(body.get("language_hints") == config.SONIOX_LANGUAGE_HINTS,
                  f"async {label}: the request's language_hints are "
                  f"{body.get('language_hints')!r}")
            if ctx is None:
                check("context" not in body,
                      f"async {label}: a context field was sent for a file with no "
                      f"vocabulary: {body.get('context')!r}")
            else:
                check(body.get("context") == ctx,
                      f"async {label}: the request carries context "
                      f"{body.get('context')!r}, expected {ctx!r}")
                check("_comment" not in body.get("context", {}),
                      f"async {label}: the request carries the block's comment as "
                      f"context: {body.get('context')!r}")
    finally:
        (transcriber.SONIOX_CONTEXT, transcriber.SONIOX_API_KEY,
         transcriber.TEXT_ARCHIVE_FOLDER) = saved_tx
        httpx.post, httpx.get, httpx.delete = saved_httpx
        shutil.rmtree(tmp, ignore_errors=True)


def test_soniox_context_live_config():
    """The same last mile on the live path, where the file's endpointing block
    rides along: both personalizations end up in one WebSocket config JSON, and
    the tool sends it before a single audio chunk moves.

    `websockets` is injected into sys.modules rather than installed -- the import
    in start_session is lazy, so the fake is what it finds, and the lane runs
    wherever the ladder runs. Everything is restored afterwards: a leftover entry
    there would poison every later import in this process."""
    try:
        import transcriber
    except ModuleNotFoundError as e:
        print(f"    (skipped the Soniox live config lane: '{e.name}' is not "
              f"installed off Windows)")
        return

    sent = []
    fake_client = types.ModuleType("websockets.sync.client")
    fake_client.connect = lambda url: _FakeWebSocket(sent)
    absent = object()
    saved_modules = {name: sys.modules.get(name, absent)
                     for name in ("websockets", "websockets.sync",
                                  "websockets.sync.client")}
    saved_tx = (transcriber.SONIOX_CONTEXT, transcriber.SONIOX_API_KEY,
                transcriber.TEXT_ARCHIVE_FOLDER)
    saved_ep = (config.SONIOX_ENDPOINT_SENSITIVITY,
                config.SONIOX_ENDPOINT_LATENCY_ADJUSTMENT_LEVEL,
                config.SONIOX_MAX_ENDPOINT_DELAY_MS)
    tmp = tempfile.mkdtemp(prefix="tb_config_live_ctx_")
    try:
        sys.modules["websockets.sync.client"] = fake_client
        transcriber.SONIOX_API_KEY = "not-a-real-key"
        transcriber.TEXT_ARCHIVE_FOLDER = Path(tmp) / "transcripts"
        # (label, context, endpointing constants, fields the config JSON must carry)
        cases = [
            ("personalized", {"terms": ["Claude-MD"]}, (-0.3, 0, 900),
             {"context": {"terms": ["Claude-MD"]}, "endpoint_sensitivity": -0.3,
              "endpoint_latency_adjustment_level": 0, "max_endpoint_delay_ms": 900}),
            # Built by the production filter (#329), so filter and config JSON meet
            # in one lane: the user's comment is not in what leaves the machine.
            ("commented", config.effective_soniox_context(
                {"_comment": "user prose", "terms": ["Claude-MD"]}),
             (None, None, None), {"context": {"terms": ["Claude-MD"]}}),
            ("plain", None, (None, None, None), {}),
        ]
        for label, ctx, endpointing, expected in cases:
            sent.clear()
            transcriber.SONIOX_CONTEXT = ctx
            (config.SONIOX_ENDPOINT_SENSITIVITY,
             config.SONIOX_ENDPOINT_LATENCY_ADJUSTMENT_LEVEL,
             config.SONIOX_MAX_ENDPOINT_DELAY_MS) = endpointing
            tx = transcriber.SonioxLiveTranscriber()
            try:
                check(tx.start_session() is True,
                      f"live {label}: start_session() did not report success")
                check(len(sent) >= 1,
                      f"live {label}: nothing was sent on the WebSocket")
                body = json.loads(sent[0]) if sent else {}
                check(body.get("model") == config.SONIOX_RT_MODEL,
                      f"live {label}: the config names model {body.get('model')!r}")
                check(body.get("enable_endpoint_detection") is True,
                      f"live {label}: endpoint detection is "
                      f"{body.get('enable_endpoint_detection')!r}")
                for field, want in expected.items():
                    check(body.get(field) == want,
                          f"live {label}: the config's {field} is "
                          f"{body.get(field)!r}, expected {want!r}")
                check("_comment" not in (body.get("context") or {}),
                      f"live {label}: the config carries the block's comment as "
                      f"context: {body.get('context')!r}")
                if not expected:
                    strays = sorted(f for f in body if f == "context"
                                    or f.startswith("endpoint_")
                                    or f == "max_endpoint_delay_ms")
                    check(not strays,
                          f"live {label}: an unconfigured file sent {strays} -- with "
                          f"nothing personalized the config JSON stays what it was "
                          f"before #121/#206, field for field")
            finally:
                tx.cancel_session()
    finally:
        for name, module in saved_modules.items():
            if module is absent:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module
        (transcriber.SONIOX_CONTEXT, transcriber.SONIOX_API_KEY,
         transcriber.TEXT_ARCHIVE_FOLDER) = saved_tx
        (config.SONIOX_ENDPOINT_SENSITIVITY,
         config.SONIOX_ENDPOINT_LATENCY_ADJUSTMENT_LEVEL,
         config.SONIOX_MAX_ENDPOINT_DELAY_MS) = saved_ep
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
    test_block_types,
    test_unknown_blocks,
    test_version_reader,
    test_checkout_reader,
]
PLAIN_CASES = [
    test_known_blocks_drift,
    test_effective_soniox_context,
    test_import_subprocess,
    test_settings_values_subprocess,
    test_vocabulary_filter_subprocess,
    test_frozen_historical_files,
    test_env_loading_subprocess,
    test_env_corpus_subprocess,
    test_env_inherited_subprocess,
    test_env_optout_subprocess,
    test_replay,
    test_source_guards,
    test_env_example_ascii,
    test_version_drift_guard,
    test_version_import_subprocess,
    test_checkout_import_subprocess,
    test_soniox_constructors,
    test_soniox_context_async_request,
    test_soniox_context_live_config,
]

# These take a tempdir positionally and run via main(); they are not pytest items (#242).
for _helper in TEMPDIR_CASES:
    _helper.__test__ = False


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
          f"pass (personal_settings: encoding, top-level shape, the block gate with "
          f"its known-blocks guards, vocabulary shape, the push_to_talk and "
          f"soniox_endpointing value rules, three frozen historical files, the "
          f"comment filter on the vocabulary's way out and its arrival in both "
          f"real Soniox requests, the "
          f"settings_io parity, a real `import config` per fixture; .env: the install "
          f"directory as the only source, every parsing rule read the same way by both "
          f"halves, an inherited variable that never counts, the D-004 opt-out's .env "
          f"route, a broken file warned about instead of fatal; pyproject.toml: the "
          f"version reader's fail-open rules, its regex twin in setup.ps1 and the "
          f"--dev stamp that writes the same line, plus the "
          f"checkout state read from a `.git` beside the script, fail-open in every "
          f"shape it comes in; plus the warning replay and the static guards)")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
