#!/usr/bin/env python3
"""Off-Windows verification for the settings/onboarding IO core (#144).

Runs on plain Python -- no Windows, no tkinter, no network -- so the whole
data-loss / key-clobbering / comment-loss risk surface is machine-checked (the
sibling of test_console_ui.py). All file ops happen in a TemporaryDirectory.

    python3 test_settings_io.py          # verify, exit non-zero on any violation
    python3 test_settings_io.py --show   # also print a sample .env + settings write

What is covered:
  - settings_io.write_env / read_env: byte-preserving update of one managed key,
    append-when-absent, absent-file seed from the example, malformed-line skip,
    empty-updates no-op, empty-value-omitted (a blank field never clobbers a
    stored key), and no temp file left behind (atomicity).
  - settings_io.write_personal_settings / read_personal_settings: surgical merge
    (every unmanaged block + every _comment preserved), hotkeys written as a diff
    vs config.DEFAULT_HOTKEYS (default scheme -> no entries), defaults.api omitted
    when it equals the built-in default, the absent-file minimal dict that must
    NOT contain the example's placeholder vocabulary (a real data bug), and the
    corrupt-JSON warning (not a crash).
  - the data-safety regressions (check_regressions): a CRLF .env round-trips
    byte-faithfully (S5), duplicate managed-key lines are ALL rewritten (S3), a
    whitespace-only value is dropped and a pasted key stripped (S4), a UTF-8 BOM is
    tolerated on read and healed on write for both files (S6), a present-but-
    unreadable file aborts the save instead of clobbering it (B1, chmod-guarded), and
    a non-UTF-8 (ANSI/cp1252) config file does not crash the readers and aborts the
    save byte-unchanged rather than destroying its vocabulary (B3).
  - the pure hotkey helpers: normalize_combo, validate_combo, decode_key_event on
    synthetic Tk events, and the diff <-> apply_hotkey_overrides round-trip
    (exercising BOTH bare-F-key and modifier-chord shapes plus the list shape).
  - key_check.classify_http (pure), the empty-key short-circuit, a non-HTTP
    response decoding to UNREACHABLE rather than crashing (B2, localhost socket), a
    malformed key (embedded newline / non-latin-1 glyph) rejected as INVALID without
    an exception escaping the worker thread, and a padded key stripped before the
    Authorization header (localhost capture).

Hands-on gates (a separate test issue, not reachable here): the real Tk state-bit
values in decode_key_event, and the live "Test key" round-trip against real keys.
"""
import logging
import os
import socket
import stat
import sys
import tempfile
import threading
from pathlib import Path

# Silence config's import-time settings warnings -- importing config parses the
# repo's real personal_settings.json, which may legitimately warn; irrelevant to
# these pure-function tests (same approach test_hotkey_overrides.py uses).
logging.getLogger('Thoughtborne.Config').setLevel(logging.CRITICAL)

import config
import key_check as kc
import settings_io as sio
from key_check import KeyStatus

SHOW = "--show" in sys.argv
EXAMPLE_ENV = config.SCRIPT_DIR / ".env.example"
EXAMPLE_PS = config.SCRIPT_DIR / "personal_settings.example.json"

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---- .env --------------------------------------------------------------------
def check_env(tmp):
    # 1. update one key: everything else byte-for-byte, the other key untouched
    original = (
        "# header comment\n"
        "FOO=bar\n"
        "\n"
        "GROQ_API_KEY=old_groq\n"
        "# a comment\n"
        "SONIOX_API_KEY=old_soniox\n"
        "UNRELATED=keepme\n"
    )
    p = tmp / "env1"
    p.write_text(original, encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": "new_groq"})
    got = p.read_text(encoding="utf-8")
    expected = original.replace("GROQ_API_KEY=old_groq", "GROQ_API_KEY=new_groq")
    check(got == expected, f".env update-one-key not byte-preserving: {got!r}")
    check(sio.read_env(p) == {"GROQ_API_KEY": "new_groq", "SONIOX_API_KEY": "old_soniox"},
          ".env read after single update wrong")

    # 2. append when the key is absent
    p = tmp / "env2"
    p.write_text("FOO=bar\n", encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": "g", "SONIOX_API_KEY": "s"})
    got = p.read_text(encoding="utf-8")
    check(got.startswith("FOO=bar\n"), ".env append: leading content lost")
    check(sio.read_env(p) == {"GROQ_API_KEY": "g", "SONIOX_API_KEY": "s"},
          ".env append read wrong")

    # 2b. append onto a file whose last line has no trailing newline
    p = tmp / "env2b"
    p.write_text("FOO=bar", encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": "g"})
    check(p.read_text(encoding="utf-8") == "FOO=bar\nGROQ_API_KEY=g\n",
          f".env append without trailing newline wrong: {p.read_text(encoding='utf-8')!r}")

    # 3. absent file -> seed from the real .env.example, keep its header, set key
    p = tmp / "env3"
    sio.write_env(p, {"SONIOX_API_KEY": "xyz"}, example_path=EXAMPLE_ENV)
    check(p.exists(), ".env absent-seed: file not created")
    got = p.read_text(encoding="utf-8")
    check("Groq API Key" in got, ".env absent-seed: example header comments lost")
    check(sio.read_env(p) == {"GROQ_API_KEY": "", "SONIOX_API_KEY": "xyz"},
          f".env absent-seed read wrong: {sio.read_env(p)}")

    # 4. malformed line skipped (read_env), not fatal
    p = tmp / "env4"
    p.write_text("GROQ_API_KEY=g\nthis is not a valid line\nSONIOX_API_KEY=s\n", encoding="utf-8")
    check(sio.read_env(p) == {"GROQ_API_KEY": "g", "SONIOX_API_KEY": "s"},
          ".env malformed-line not skipped")

    # 5. empty updates no-op; empty value must never clobber a stored key
    p = tmp / "env5"
    original5 = "GROQ_API_KEY=keepme\nSONIOX_API_KEY=keepme2\n"
    p.write_text(original5, encoding="utf-8")
    sio.write_env(p, {})
    check(p.read_text(encoding="utf-8") == original5, ".env empty-updates not a no-op")
    sio.write_env(p, {"GROQ_API_KEY": ""})
    check(p.read_text(encoding="utf-8") == original5, ".env empty-value clobbered a stored key")
    sio.write_env(p, {"GROQ_API_KEY": "", "SONIOX_API_KEY": "new2"})
    got = p.read_text(encoding="utf-8")
    check("GROQ_API_KEY=keepme" in got and "SONIOX_API_KEY=new2" in got,
          f".env mixed empty+value wrong: {got!r}")

    # 6. read: missing file -> {}, commented key ignored
    check(sio.read_env(tmp / "nope") == {}, ".env read missing file not empty")
    p = tmp / "env6"
    p.write_text("#GROQ_API_KEY=commented\nSONIOX_API_KEY=real\n", encoding="utf-8")
    check(sio.read_env(p) == {"SONIOX_API_KEY": "real"}, ".env commented key not ignored")


# ---- personal_settings.json --------------------------------------------------
def check_personal_settings(tmp):
    example, ex_warn = sio.read_personal_settings(EXAMPLE_PS)
    check(ex_warn is None and isinstance(example, dict) and "vocabulary" in example,
          "example personal_settings.example.json did not load")

    # A -- existing file: preserve unmanaged blocks + _comments, write hotkeys diff
    p = tmp / "ps_a.json"
    p.write_text(EXAMPLE_PS.read_text(encoding="utf-8"), encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_fkeys(),
                                default_api="groq", example_path=EXAMPLE_PS)
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "A: written file did not reload as valid JSON")
    check(data.get("vocabulary") == example["vocabulary"], "A: vocabulary not preserved")
    check(data.get("push_to_talk") == example["push_to_talk"], "A: push_to_talk not preserved")
    check(data.get("soniox_endpointing") == example["soniox_endpointing"],
          "A: soniox_endpointing not preserved")
    check(data.get("_comment") == example["_comment"], "A: top-level _comment not preserved")
    check(data["hotkeys"].get("_comment") == example["hotkeys"]["_comment"],
          "A: hotkeys _comment not preserved")
    check(data["hotkeys"].get("start_recording") == "f9", "A: hotkeys diff not written")
    check(data["defaults"].get("api") == "groq", "A: defaults.api not written")
    check(data["defaults"].get("_comment") == example["defaults"]["_comment"],
          "A: defaults _comment not preserved")
    # the written diff round-trips back into the preset
    eff, warns = config.apply_hotkey_overrides(config.DEFAULT_HOTKEYS, data["hotkeys"])
    check(eff == sio.preset_fkeys() and not warns, "A: hotkeys diff round-trip mismatch")

    # B -- Ctrl+Alt preset -> no hotkey entries; built-in api -> defaults.api omitted
    p = tmp / "ps_b.json"
    p.write_text(EXAMPLE_PS.read_text(encoding="utf-8"), encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API, example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    hk_entries = {k: v for k, v in data.get("hotkeys", {}).items() if not k.startswith("_")}
    check(hk_entries == {}, f"B: default scheme wrote hotkey entries: {hk_entries}")
    check("_comment" in data.get("hotkeys", {}), "B: hotkeys _comment dropped")
    check("api" not in data.get("defaults", {}), "B: defaults.api written for the built-in default")
    check("_comment" in data.get("defaults", {}), "B: defaults _comment dropped")
    check(data.get("vocabulary") == example["vocabulary"], "B: vocabulary not preserved")

    # C -- absent file -> minimal managed dict, NO placeholder vocabulary
    p = tmp / "ps_absent.json"
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_fkeys(),
                                default_api="groq", example_path=EXAMPLE_PS)
    raw = p.read_text(encoding="utf-8")
    check("Project Name" not in raw and "Company Name" not in raw,
          "C: absent-file write leaked the placeholder vocabulary (DATA BUG)")
    data, _ = sio.read_personal_settings(p)
    check("vocabulary" not in data, "C: absent-file write seeded a vocabulary block")
    check("push_to_talk" not in data and "soniox_endpointing" not in data,
          "C: absent-file write seeded unmanaged blocks")
    check(data["hotkeys"].get("start_recording") == "f9", "C: absent-file hotkeys diff missing")
    check(data["hotkeys"].get("_comment") == example["hotkeys"]["_comment"],
          "C: absent-file hotkeys _comment lead missing")
    check(data["defaults"].get("api") == "groq", "C: absent-file defaults.api missing")
    check(data["defaults"].get("_comment") == example["defaults"]["_comment"],
          "C: absent-file defaults _comment lead missing")

    # D -- unreadable file -> warning (not a crash); a save overwrites it cleanly
    p = tmp / "ps_bad.json"
    p.write_text("{ this is : not valid json ", encoding="utf-8")
    data, warn = sio.read_personal_settings(p)
    check(data == {} and isinstance(warn, str) and warn, "D: unreadable file did not warn")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API, example_path=EXAMPLE_PS)
    _, warn2 = sio.read_personal_settings(p)
    check(warn2 is None, "D: write over an unreadable file did not produce valid JSON")
    check("Project Name" not in p.read_text(encoding="utf-8"),
          "D: overwrite leaked the placeholder vocabulary")

    # missing file -> ({}, None): a first run is normal, not a warning
    md, mw = sio.read_personal_settings(tmp / "nope.json")
    check(md == {} and mw is None, "read_personal_settings(missing) should be ({}, None)")


# ---- data-safety regressions (B1 / S3 / S4 / S5 / S6) ------------------------
def _still_unreadable(p) -> bool:
    """True only if chmod(0) actually blocked reading the bytes (it does not when
    the test runs as root, or on a filesystem that ignores POSIX perms)."""
    try:
        with open(p, "rb") as f:
            f.read()
        return False
    except OSError:
        return True


def check_regressions(tmp):
    # S5 -- a CRLF .env keeps its \r\n endings, the updated line included. (Before
    # the fix, Path.read_text's universal-newline translation silently rewrote it
    # to LF.) Byte-exact via read_bytes/write_bytes.
    p = tmp / "env_crlf"
    p.write_bytes(b"FOO=bar\r\nGROQ_API_KEY=old\r\nUNRELATED=x\r\n")
    sio.write_env(p, {"GROQ_API_KEY": "new"})
    raw = p.read_bytes()
    check(raw == b"FOO=bar\r\nGROQ_API_KEY=new\r\nUNRELATED=x\r\n",
          f"S5: CRLF .env not byte-preserved: {raw!r}")

    # S3 -- python-dotenv is last-wins, so EVERY duplicate managed-key line must be
    # rewritten; a stale later duplicate would otherwise keep being read.
    p = tmp / "env_dup"
    p.write_text("GROQ_API_KEY=first\nFOO=bar\nGROQ_API_KEY=second\n", encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": "new"})
    got = p.read_text(encoding="utf-8")
    check(got == "GROQ_API_KEY=new\nFOO=bar\nGROQ_API_KEY=new\n",
          f"S3: duplicate managed-key lines not all replaced: {got!r}")
    check("first" not in got and "second" not in got,
          f"S3: a stale duplicate value survived: {got!r}")

    # S4 -- a whitespace-only value is treated as empty (dropped, stored key
    # untouched); a padded real value is stored stripped.
    p = tmp / "env_ws"
    p.write_text("GROQ_API_KEY=keepme\n", encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": "   "})
    check(p.read_text(encoding="utf-8") == "GROQ_API_KEY=keepme\n",
          "S4: whitespace-only value clobbered a stored key")
    sio.write_env(p, {"GROQ_API_KEY": "\n"})
    check(p.read_text(encoding="utf-8") == "GROQ_API_KEY=keepme\n",
          "S4: newline-only value clobbered a stored key")
    sio.write_env(p, {"GROQ_API_KEY": "  sk-123  "})
    check(sio.read_env(p) == {"GROQ_API_KEY": "sk-123"},
          f"S4: pasted key not stripped: {sio.read_env(p)}")

    # S6 -- a UTF-8 BOM must not be mistaken for corruption. A Notepad "UTF-8 with
    # BOM" personal_settings.json reads as valid (its vocabulary preserved on save),
    # and the rewrite heals the BOM.
    p = tmp / "ps_bom.json"
    body = '{\n  "vocabulary": {\n    "terms": ["keepme"]\n  }\n}\n'
    p.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    data, warn = sio.read_personal_settings(p)
    check(warn is None and data.get("vocabulary", {}).get("terms") == ["keepme"],
          f"S6: BOM personal_settings misread (warn={warn!r}, data={data!r})")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_fkeys(),
                                default_api="groq", example_path=EXAMPLE_PS)
    data2, warn2 = sio.read_personal_settings(p)
    check(warn2 is None and data2.get("vocabulary", {}).get("terms") == ["keepme"],
          "S6: BOM personal_settings vocabulary lost on save")
    check(not p.read_bytes().startswith(b"\xef\xbb\xbf"),
          "S6: personal_settings save did not strip the BOM")
    # a BOM'd .env: the stored key is still read, and an update keeps it + heals BOM
    p = tmp / "env_bom"
    p.write_bytes(b"\xef\xbb\xbfGROQ_API_KEY=frombom\n")
    check(sio.read_env(p) == {"GROQ_API_KEY": "frombom"},
          f"S6: BOM .env key not read: {sio.read_env(p)}")
    sio.write_env(p, {"SONIOX_API_KEY": "s"})
    check(sio.read_env(p) == {"GROQ_API_KEY": "frombom", "SONIOX_API_KEY": "s"},
          "S6: BOM .env update lost the existing key")
    check(not p.read_bytes().startswith(b"\xef\xbb\xbf"),
          "S6: .env save did not strip the BOM")

    # B1 -- a present-but-unreadable file must NOT be silently overwritten: the save
    # aborts (raises) and the bytes stay intact. chmod(0) only enforces this on a
    # POSIX fs that honors it, so guard it and skip loudly rather than pass falsely.
    p = tmp / "env_locked"
    p.write_bytes(b"GROQ_API_KEY=secret\n")
    os.chmod(p, 0)
    if not _still_unreadable(p):
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        print("  (skipped B1 .env unreadable test: fs doesn't enforce chmod)")
    else:
        raised = False
        try:
            sio.write_env(p, {"GROQ_API_KEY": "new"})
        except OSError:
            raised = True
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)   # restore so we can read + clean up
        check(raised, "B1: write_env over an unreadable file did not raise")
        check(p.read_bytes() == b"GROQ_API_KEY=secret\n",
              "B1: write_env clobbered an unreadable file")

    p = tmp / "ps_locked.json"
    orig = '{"vocabulary": {"terms": ["keepme"]}}\n'
    p.write_text(orig, encoding="utf-8")
    os.chmod(p, 0)
    if not _still_unreadable(p):
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        print("  (skipped B1 personal_settings unreadable test: fs doesn't enforce chmod)")
    else:
        # the read must NOT masquerade an unreadable file as absent...
        raised_read = False
        try:
            sio.read_personal_settings(p)
        except OSError:
            raised_read = True
        # ...and the write must abort rather than skeleton over it
        raised_write = False
        try:
            sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                        default_api=config.BUILTIN_DEFAULT_API,
                                        example_path=EXAMPLE_PS)
        except OSError:
            raised_write = True
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        check(raised_read, "B1: read_personal_settings on an unreadable file did not raise")
        check(raised_write, "B1: write_personal_settings over an unreadable file did not raise")
        check(p.read_text(encoding="utf-8") == orig,
              "B1: write_personal_settings clobbered an unreadable file")

    # B3 -- a non-UTF-8 (ANSI/cp1252) config file must not crash the readers, and an
    # undecodable personal_settings holds INTACT recoverable data (German vocabulary in
    # the wrong encoding) -> it is treated like B1 (abort the save, never overwrite),
    # NOT like corrupt-JSON warn-then-overwrite.
    # (a) read_env on a cp1252 .env (umlaut in a comment) returns {} without raising.
    p = tmp / "env_cp1252"
    p.write_bytes("# Umlaut-Kommentar: Präfix\nGROQ_API_KEY=secret\n".encode("cp1252"))
    raised_e = False
    got_e = None
    try:
        got_e = sio.read_env(p)
    except Exception:
        raised_e = True
    check(not raised_e, "B3: read_env on a cp1252 file raised instead of returning {}")
    check(got_e == {}, f"B3: read_env on a cp1252 file should return {{}}, got {got_e!r}")

    # (b) write_personal_settings over an ANSI file ABORTS with the file byte-unchanged
    # (same shape as the chmod-0 B1 test) -- overwriting would destroy the vocabulary.
    p = tmp / "ps_ansi.json"
    ansi_bytes = '{\n  "vocabulary": {"terms": ["Grüße", "Präfix"]}\n}\n'.encode("cp1252")
    p.write_bytes(ansi_bytes)
    raised_w = False
    try:
        sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                    default_api=config.BUILTIN_DEFAULT_API,
                                    example_path=EXAMPLE_PS)
    except (UnicodeError, OSError):
        raised_w = True
    check(raised_w, "B3: write_personal_settings over an ANSI file did not abort/raise")
    check(p.read_bytes() == ansi_bytes,
          "B3: write_personal_settings clobbered an ANSI file (destroyed vocabulary)")

    # (c) read_personal_settings on that file RAISES -- it must not return a
    # skeleton-triggering ({}, None) that would let a save skeleton over it.
    raised_r = False
    try:
        sio.read_personal_settings(p)
    except UnicodeError:
        raised_r = True
    check(raised_r, "B3: read_personal_settings on an ANSI file did not raise")


# ---- pure hotkey helpers -----------------------------------------------------
def check_hotkey_helpers():
    check(sio.normalize_combo("Ctrl + Alt + P") == "ctrl+alt+p", "normalize_combo spaces/case")
    check(sio.normalize_combo(" F9 ") == "f9", "normalize_combo bare f-key")
    check(sio.normalize_combo("CTRL+ALT+Ü") == "ctrl+alt+ü", "normalize_combo umlaut")

    for good in ("ctrl+alt+p", "ctrl+alt+6", "f9", "ctrl+alt+f12", "ctrl+alt+ü"):
        ok, msg = sio.validate_combo(good)
        check(ok, f"validate_combo rejected a good combo {good!r}: {msg}")
    for bad in ("", "   ", "ctrl+alt", "ctrl+alt+p+q", "ctrl+alt+notakey", "@#$"):
        ok, _ = sio.validate_combo(bad)
        check(not ok, f"validate_combo accepted a bad combo {bad!r}")

    C, A, S = sio.TK_STATE_CONTROL, sio.TK_STATE_ALT, sio.TK_STATE_SHIFT
    cases = [
        ((C | A, "p", "\x10"), "ctrl+alt+p"),
        ((0, "F9", ""), "f9"),                    # bare F-key
        ((C | A, "6", ""), "ctrl+alt+6"),
        ((C | A, "udiaeresis", ""), "ctrl+alt+ü"),  # umlaut is never filtered
        ((C | A | S, "A", ""), "ctrl+alt+shift+a"),
        ((C | A, "at", "@"), None),               # AltGr-typed symbol -> filtered
        ((C | A, "Alt_L", ""), None),             # only modifiers down
        ((C, "Control_L", ""), None),             # only modifiers down
        ((0, "period", "."), None),               # non-bindable key
    ]
    for (state, keysym, char), expected in cases:
        got = sio.decode_key_event(state, keysym, char)
        check(got == expected,
              f"decode_key_event({state:#x}, {keysym!r}) = {got!r}, expected {expected!r}")

    # round-trip: the F-key preset diff, fed back through the production loader,
    # reproduces the preset -- exercising both the bare and the chord shapes.
    diff = sio.hotkeys_diff_vs_default(sio.preset_fkeys(), config.DEFAULT_HOTKEYS)
    check(diff.get("start_recording") == "f9" and diff.get("stop_recording_clipboard") == "f10",
          "diff lost the bare-F-key core ops")
    check(diff.get("cancel_recording") == ["ctrl+f9"],
          "diff lost the list shape for cancel_recording")
    eff, warns = config.apply_hotkey_overrides(config.DEFAULT_HOTKEYS, diff)
    check(eff == sio.preset_fkeys() and not warns,
          f"F-key preset round-trip mismatch (warns={warns})")
    # the Ctrl+Alt preset equals the defaults -> an empty diff (no frozen copy)
    check(sio.hotkeys_diff_vs_default(sio.preset_ctrl_alt(), config.DEFAULT_HOTKEYS) == {},
          "default scheme should diff to {}")


# ---- key_check ---------------------------------------------------------------
def check_key_check():
    check(kc.classify_http(200) == KeyStatus.VALID, "classify_http 200 -> VALID")
    check(kc.classify_http(204) == KeyStatus.VALID, "classify_http 204 -> VALID")
    check(kc.classify_http(401) == KeyStatus.INVALID, "classify_http 401 -> INVALID")
    check(kc.classify_http(403) == KeyStatus.UNREACHABLE, "classify_http 403 -> UNREACHABLE")
    check(kc.classify_http(500) == KeyStatus.UNREACHABLE, "classify_http 500 -> UNREACHABLE")
    # an empty key short-circuits without a network call
    check(kc.check_groq_key("").status == KeyStatus.INVALID,
          "check_groq_key('') should be INVALID without touching the network")


def check_key_check_socket():
    # B2 -- a non-HTTP response (garbage bytes from a captive portal / proxy) makes
    # http.client raise BadStatusLine (an HTTPException, NOT an OSError). _check_bearer
    # must catch it and return UNREACHABLE, not let it escape and kill the worker
    # thread. Pure stdlib, localhost only.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def _serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        try:
            conn.sendall(b"HELLO THIS IS NOT HTTP\r\n\r\n")
        except OSError:
            pass
        finally:
            conn.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    crashed = False
    res = None
    try:
        res = kc._check_bearer(f"http://127.0.0.1:{port}/", "dummy-key", timeout=5.0)
    except BaseException:   # the whole point of the test is that nothing escapes
        crashed = True
    check(not crashed, "B2: _check_bearer let a non-HTTP response exception escape")
    check(res is not None and res.status == KeyStatus.UNREACHABLE,
          f"B2: a non-HTTP response should decode to UNREACHABLE, got {res}")
    t.join(timeout=2.0)
    srv.close()


def check_key_check_malformed():
    # A malformed pasted key must not crash the worker thread when urllib composes the
    # Authorization header. An embedded newline/CR (ValueError "Invalid header value")
    # and a non-latin-1 glyph (a smart quote copied off a rendered page ->
    # UnicodeEncodeError) are rejected up front as INVALID, offline, before any request
    # is built -- the URL below is never contacted. The key is never echoed.
    for bad in ("gsk_line1\ngsk_line2", "gsk_\rabc", "gsk_“smart”"):
        crashed = False
        res = None
        try:
            res = kc._check_bearer("http://127.0.0.1:1/", bad, timeout=0.1)
        except BaseException:   # the whole point: nothing escapes to kill the thread
            crashed = True
        check(not crashed, f"malformed key {bad!r} let an exception escape _check_bearer")
        check(res is not None and res.status == KeyStatus.INVALID,
              f"malformed key {bad!r} should be INVALID, got {res}")
        check(res is None or bad not in res.detail, "malformed-key detail echoed the key")


def check_key_check_strip():
    # A padded valid-shaped key is stripped before the Authorization header is built
    # (mirroring the .env writer's .strip()), so it doesn't test as a spurious INVALID
    # from a padded-header 401. Capture the header a localhost server actually receives.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    captured = {}

    def _serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        try:
            data = conn.recv(4096)
            for raw in data.split(b"\r\n"):
                if raw.lower().startswith(b"authorization:"):
                    captured["auth"] = raw.decode("latin-1")
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass
        finally:
            conn.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    res = kc._check_bearer(f"http://127.0.0.1:{port}/", "  gsk_padded  ", timeout=5.0)
    t.join(timeout=2.0)
    srv.close()
    check(captured.get("auth") == "Authorization: Bearer gsk_padded",
          f"padded key not stripped before the header: {captured.get('auth')!r}")
    check(res is not None and res.status == KeyStatus.VALID,
          f"stripped padded key should get a 200 VALID, got {res}")


def _show():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        p = tmp / ".env"
        sio.write_env(p, {"SONIOX_API_KEY": "so_xxx"}, example_path=EXAMPLE_ENV)
        print("----- .env (absent-file seed + one key) -----")
        print(p.read_text(encoding="utf-8"))
        q = tmp / "personal_settings.json"
        sio.write_personal_settings(q, hotkeys_effective=sio.preset_fkeys(),
                                    default_api="groq", example_path=EXAMPLE_PS)
        print("----- personal_settings.json (F-key preset + defaults.api, absent file) -----")
        print(q.read_text(encoding="utf-8"))


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        check_env(tmp)
        check_personal_settings(tmp)
        check_regressions(tmp)
        leftovers = [x.name for x in tmp.iterdir() if x.name.endswith(".tmp")]
        check(not leftovers, f"atomic write left temp files behind: {leftovers}")
    check_hotkey_helpers()
    check_key_check()
    check_key_check_socket()
    check_key_check_malformed()
    check_key_check_strip()

    if SHOW:
        _show()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures[:60]:
            print("  " + f)
        return 1
    print("OK: all settings_io / key_check checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
