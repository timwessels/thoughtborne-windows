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
    vs config.DEFAULT_HOTKEYS (default scheme -> no entries), and the three-valued
    defaults.api contract (#193/#198, D-008/D-002): `default_api=None` (the untouched
    engine field) leaves the file's value exactly as found -- a hand-written or even
    invalid pin included; a real id is written VERBATIM, the built-in default
    included (the two-mode fixed pin -- "always start with X"); and REMOVE_API_PIN
    force-drops the key, preserving siblings + _comment. Plus the absent-file minimal
    dict that must NOT contain the example's placeholder vocabulary (a real data
    bug), and the corrupt-JSON warning (not a crash).
  - settings_io.resolve_engine_save_signal (#198): the pure on-save derivation of
    (default_api_signal, memory_api) across the whole two-mode decision table,
    including the #201 named regression that an untouched fixed pin on a now-keyless
    engine still resolves to (None, None) -- defaults.api byte-identical (D-002).
  - settings_io.engine_keyed (#201): the per-engine "has a usable key" predicate the
    key-aware engine control greys off -- stored vs live field per provider, a blank
    field falling back to the stored key, all-keyless, and an unknown engine id.
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
  - settings_strings i18n (#144): the DE and EN tables carry the identical key set
    (a missing translation fails here, not silently at runtime), every value is a
    non-empty string, the t() lang -> EN -> key-itself fallback chain, the
    engine.desc.* EN wording tracks config.API_DISPLAY, and detect_ui_language()'s
    off-Windows branch returns "de"/"en".
  - settings_io.write_personal_settings ui.language merge (#144, F6): ui_language
    None preserves an existing ui block untouched (and creates none when absent),
    "de"/"en" sets ui.language while preserving sibling keys + the _comment, and an
    absent-file write with a language seeds a fresh ui block.
  - settings_io.resolve_first_run / env_has_key (#163): the settings app's window-
    mode decision -- flag OR no stored key -> the first-run wizard, a stored key with
    no flag -> the plain dialog; the shared key-presence predicate and the read_env
    seam (a readable keyed .env -> plain, an ANSI .env -> wizard, matching
    _had_stored_key).

Hands-on gates (a separate test issue, not reachable here): the real Tk state-bit
values in decode_key_event, and the live "Test key" round-trip against real keys.
"""
import json
import logging
import os
import re
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
import settings_strings as sstr
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

    # B -- Ctrl+Alt preset -> no hotkey entries; the built-in default -> written
    # VERBATIM as the pin (the #198 fixed-mode widening: "always start with the
    # default" is exactly the frozen copy the old diff-against-builtin rule dropped).
    p = tmp / "ps_b.json"
    p.write_text(EXAMPLE_PS.read_text(encoding="utf-8"), encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API, example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    hk_entries = {k: v for k, v in data.get("hotkeys", {}).items() if not k.startswith("_")}
    check(hk_entries == {}, f"B: default scheme wrote hotkey entries: {hk_entries}")
    check("_comment" in data.get("hotkeys", {}), "B: hotkeys _comment dropped")
    check(data.get("defaults", {}).get("api") == config.BUILTIN_DEFAULT_API,
          "B: the built-in default was not written verbatim (the fixed-mode widening)")
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

    # #211: PRESET_FKEYS is a SECOND hard-coded default source -- its housekeeping
    # keys are documented as identical to the shipped Ctrl+Alt scheme, so they must
    # not drift (nothing enforced this before).
    for a in ("open_history", "open_settings", "test_transcription", "exit_program"):
        check(sio.PRESET_FKEYS[a] == config.DEFAULT_HOTKEYS[a],
              f"PRESET_FKEYS[{a}] drifted from DEFAULT_HOTKEYS")


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


# ---- settings_strings i18n (#144) --------------------------------------------
def check_i18n():
    check(set(sstr.available_languages()) == {"de", "en"},
          f"available_languages() should be de+en, got {sstr.available_languages()}")

    en, de = set(sstr._EN), set(sstr._DE)
    check(en - de == set(), f"i18n: keys present in EN but missing in DE: {sorted(en - de)}")
    check(de - en == set(), f"i18n: keys present in DE but missing in EN: {sorted(de - en)}")

    for table_name, table in (("EN", sstr._EN), ("DE", sstr._DE)):
        for k, v in table.items():
            check(isinstance(v, str) and v.strip() != "",
                  f"i18n: {table_name}[{k!r}] is empty / not a string")

    # every default hotkey action has a display name in both languages
    for action in config.DEFAULT_HOTKEYS:
        check(f"action.{action}" in sstr._EN and f"action.{action}" in sstr._DE,
              f"i18n: missing action.{action} string")

    # t() fallback chain: direct lookup, unknown-lang -> EN, missing key -> the key
    check(sstr.t("btn.save", "de") == sstr._DE["btn.save"], "t(): DE lookup wrong")
    check(sstr.t("btn.save", "en") == sstr._EN["btn.save"], "t(): EN lookup wrong")
    check(sstr.t("btn.save", "fr") == sstr._EN["btn.save"],
          "t(): unknown lang should fall back to EN")
    check(sstr.t("no.such.key", "de") == "no.such.key",
          "t(): a missing key should fall back to the key itself")

    # engine.desc.* EN must equal config.API_DISPLAY's descriptors (one wording,
    # two surfaces -- the console lineup and the settings engine radios).
    for api, disp in config.API_DISPLAY.items():
        check(sstr.t(f"engine.desc.{api}", "en") == disp["descriptor"],
              f"i18n: engine.desc.{api} EN must equal API_DISPLAY descriptor "
              f"({sstr.t(f'engine.desc.{api}', 'en')!r} != {disp['descriptor']!r})")

    # behavior.engine.keyless (#201) names the Provider tab by its label; guard that
    # coupling in BOTH languages so a future rename of provider.tab can't leave the
    # guidance line silently pointing at a tab name that no longer exists (same coupling
    # style as the engine.desc guard above).
    for lang in ("en", "de"):
        check(sstr.t("provider.tab", lang) in sstr.t("behavior.engine.keyless", lang),
              f"i18n: behavior.engine.keyless ({lang}) must name the provider tab exactly "
              f"as provider.tab renders it ({sstr.t('provider.tab', lang)!r})")

    check(sstr.detect_ui_language() in ("de", "en"),
          "detect_ui_language() off-Windows must return 'de' or 'en'")

    # Placeholder parity (#178): the key-set check above proves DE and EN carry the
    # same keys, but not that a format string uses the same {…} tokens in both -- a
    # mismatch passes i18n and then crashes .format() in one language at runtime.
    # Guard every string generically -- covers existing, new, and future format
    # strings -- then pin the exact render contract of the #178 ones below. The
    # key-set equality asserted above makes sstr._DE[k] safe while iterating _EN.
    for k in sstr._EN:
        en = set(re.findall(r"{(\w+)}", sstr._EN[k]))
        de = set(re.findall(r"{(\w+)}", sstr._DE[k]))
        check(en == de,
              f"i18n: placeholder mismatch in {k}: EN{sorted(en)} DE{sorted(de)}")
    check(set(re.findall(r"{(\w+)}", sstr._EN["done.loop.body"])) == {"start", "stop"},
          "done.loop.body must use exactly {start} and {stop}")
    check(set(re.findall(r"{(\w+)}", sstr._EN["welcome.loop.body"])) == {"start", "stop"},
          "welcome.loop.body must use exactly {start} and {stop}")
    check(set(re.findall(r"{(\w+)}", sstr._EN["done.controls.body"]))
          == {"exit_key", "settings_key"},
          "done.controls.body must use exactly {exit_key} and {settings_key}")
    check(set(re.findall(r"{(\w+)}", sstr._EN["hotkeys.capture_limit"])) == {"exit_key"},
          "hotkeys.capture_limit must use exactly {exit_key}")
    check(set(re.findall(r"{(\w+)}", sstr._EN["behavior.engine.remember.current"])) == {"engine"},
          "behavior.engine.remember.current must use exactly {engine}")
    check(set(re.findall(r"{(\w+)}", sstr._EN["behavior.engine.remember.none"])) == {"engine"},
          "behavior.engine.remember.none must use exactly {engine}")


# ---- settings_io ui.language merge (#144, F6) --------------------------------
def check_ui_language(tmp):
    # (a) ui_language=None preserves an existing ui block untouched.
    p = tmp / "ps_ui_keep.json"
    original_ui = {"_comment": "keep me", "language": "en", "theme": "dark"}
    p.write_text(json.dumps({"ui": original_ui, "vocabulary": {"terms": ["x"]}},
                            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API,
                                example_path=EXAMPLE_PS, ui_language=None)
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "UI-none: file did not reload as valid JSON")
    check(data.get("ui") == original_ui,
          f"UI-none: ui block not preserved untouched: {data.get('ui')}")

    # (b) 'de' sets ui.language, preserving the _comment and sibling keys.
    p = tmp / "ps_ui_set.json"
    p.write_text(json.dumps({"ui": {"_comment": "c", "theme": "dark"},
                             "vocabulary": {"terms": ["keepme"]}},
                            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API,
                                example_path=EXAMPLE_PS, ui_language="de")
    data, _ = sio.read_personal_settings(p)
    ui = data.get("ui", {})
    check(ui.get("language") == "de", f"UI-set: language not written: {ui}")
    check(ui.get("_comment") == "c", "UI-set: ui _comment not preserved")
    check(ui.get("theme") == "dark", "UI-set: ui sibling key not preserved")
    check(data.get("vocabulary", {}).get("terms") == ["keepme"],
          "UI-set: vocabulary clobbered")

    # (c) a second write with 'en' updates the existing language in place.
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API,
                                example_path=EXAMPLE_PS, ui_language="en")
    data, _ = sio.read_personal_settings(p)
    check(data.get("ui", {}).get("language") == "en", "UI-set: language not updated to en")
    check(data.get("ui", {}).get("theme") == "dark", "UI-set: sibling lost on update")

    # (d) no ui block + None -> none is created (the no-toggle first-run case).
    p = tmp / "ps_ui_absent.json"
    p.write_text('{\n  "vocabulary": {"terms": ["x"]}\n}\n', encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API,
                                example_path=EXAMPLE_PS, ui_language=None)
    data, _ = sio.read_personal_settings(p)
    check("ui" not in data, f"UI-absent: a ui block was created for ui_language=None: {data.get('ui')}")

    # (e) absent file + 'de' -> a fresh ui block with the language (and the
    # example's _comment lead, since EXAMPLE_PS carries one).
    p = tmp / "ps_ui_new.json"
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API,
                                example_path=EXAMPLE_PS, ui_language="de")
    data, _ = sio.read_personal_settings(p)
    check(data.get("ui", {}).get("language") == "de",
          "UI-new: language missing on absent-file write")
    check("_comment" in data.get("ui", {}), "UI-new: example _comment lead not carried")
    check("vocabulary" not in data, "UI-new: absent-file write seeded a vocabulary block")


# ---- startup-engine preselection (#178) --------------------------------------
def check_preselect():
    P = sio.preselect_startup_api
    check(P(True, False) == "groq-large", "preselect: Groq-only -> groq-large")
    check(P(False, True) == config.BUILTIN_DEFAULT_API,
          "preselect: Soniox-only -> built-in default")
    check(P(True, True) == config.BUILTIN_DEFAULT_API,
          "preselect: both keys -> built-in default (explicit pick wins in the UI)")
    check(P(False, False) == config.BUILTIN_DEFAULT_API,
          "preselect: neither key -> built-in default")
    # Both returned tokens must be selectable engines -- the UI does
    # AVAILABLE_APIS.index(target), which would raise on an unknown token.
    for token in ("groq-large", config.BUILTIN_DEFAULT_API):
        check(token in config.AVAILABLE_APIS,
              f"preselect: {token!r} is not in AVAILABLE_APIS")


# ---- key-aware engine control predicate (#201) -------------------------------
def check_engine_keyed():
    """engine_keyed(api, live_fields, stored_env): the per-engine "has a usable key"
    test the key-aware engine control greys off (#201). live_fields/stored_env map
    {ENV_VAR: value}; a non-blank live field OR a stored key on the engine's backing
    var means keyed, with a blank live field falling back to the stored value (a blank
    never clobbers a stored key). Delegates to config.engine_has_key so the settings
    control and the #200 console lineup can never disagree."""
    E = sio.engine_keyed
    SON, GRQ = "SONIOX_API_KEY", "GROQ_API_KEY"
    empty = {SON: "", GRQ: ""}
    stored_son = {SON: "s_stored", GRQ: ""}
    check(E("soniox-live", empty, stored_son) and E("soniox", empty, stored_son),
          "keyed: a stored Soniox key keys both Soniox engines")
    check(not E("groq", empty, stored_son) and not E("groq-large", empty, stored_son),
          "keyed: a stored Soniox key does not key the Groq engines")
    live_grq = {SON: "", GRQ: "g_typed"}
    check(E("groq", live_grq, empty) and E("groq-large", live_grq, empty),
          "keyed: a typed Groq field keys both Groq engines live")
    check(not E("soniox-live", live_grq, empty),
          "keyed: a typed Groq field does not key Soniox")
    check(E("soniox", {SON: "  "}, {SON: "s_stored"}),
          "keyed: a blank field over a stored key stays keyed (blank never clobbers)")
    check(not E("soniox", {SON: "   "}, empty),
          "keyed: a whitespace-only field with nothing stored is not keyed")
    both = {SON: "s", GRQ: "g"}
    check(all(E(a, empty, both) for a in config.AVAILABLE_APIS),
          "keyed: both stored keys key all four engines")
    check(not any(E(a, empty, empty) for a in config.AVAILABLE_APIS),
          "keyed: no key anywhere -> every engine keyless (the guidance-line case)")
    check(not E("whisper-9000", both, both),
          "keyed: an unknown engine id is never keyed")


# ---- settings_io defaults.api merge (#193, D-008) ----------------------------
def check_engine_pin(tmp):
    """The three-valued `default_api` contract (#193/#198, D-008/D-002).
    `default_api=None` means "the engine field was not touched": the file's
    `defaults.api` is left exactly as found -- without that, an untouched save would
    silently delete a hand-written pin (a value equal to the built-in default
    included, which with a remembered engine present flips the next start) and an
    invalid hand-typed value. A real id is written VERBATIM, the built-in default
    included (the #198 fixed-mode "always start with X" pin). `REMOVE_API_PIN`
    force-drops the key (remember-mode chosen over a pin), preserving siblings +
    `_comment`."""
    # (a) untouched save leaves a pin ON the built-in default byte-identical.
    p = tmp / "ps_pin_builtin.json"
    original = {"defaults": {"_comment": "keep me", "api": config.BUILTIN_DEFAULT_API},
                "vocabulary": {"terms": ["keepme"]}}
    raw = json.dumps(original, indent=2, ensure_ascii=False) + "\n"
    p.write_text(raw, encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS,
                                ui_language=None)
    check(p.read_text(encoding="utf-8") == raw,
          "PIN-none: an untouched save did not leave the file byte-identical "
          f"(a hand-written pin on the built-in default was rewritten): {p.read_text(encoding='utf-8')!r}")

    # (b) untouched save preserves an INVALID api value. Deliberate: the tool warns
    # about it at every start; deleting what the user typed, on a save about
    # something else, is the worse behavior.
    p = tmp / "ps_pin_invalid.json"
    p.write_text(json.dumps({"defaults": {"api": "whisper-9000"}}, indent=2) + "\n",
                 encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS,
                                ui_language=None)
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "PIN-invalid: file did not reload as valid JSON")
    check(data.get("defaults", {}).get("api") == "whisper-9000",
          f"PIN-invalid: an untouched save destroyed a hand-typed value: {data.get('defaults')}")

    # (c) untouched save keeps a normal pin, and creates no defaults block where
    # the file has none (the no-pin, memory-decides case).
    p = tmp / "ps_pin_other.json"
    p.write_text(json.dumps({"defaults": {"api": "groq"}}, indent=2) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_fkeys(),
                                default_api=None, example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    check(data.get("defaults", {}).get("api") == "groq",
          f"PIN-keep: an untouched save dropped an existing pin: {data.get('defaults')}")
    check(data.get("hotkeys", {}).get("start_recording") == "f9",
          "PIN-keep: the hotkeys diff was not written alongside the untouched engine")
    p = tmp / "ps_pin_absent.json"
    p.write_text(json.dumps({"vocabulary": {"terms": ["x"]}}, indent=2) + "\n",
                 encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    check("defaults" not in data,
          f"PIN-absent: an untouched save created a defaults block: {data.get('defaults')}")

    # (d) an active fixed pick: a real id OVERWRITES, the built-in default is WRITTEN
    # verbatim (the #198 widening -- picking Soniox Live in "always start with" pins
    # it so it survives a later Ctrl+Alt+L switch), and REMOVE_API_PIN DROPS the pin
    # (remember-mode chosen over it), the _comment preserved in every case.
    p = tmp / "ps_pin_active.json"
    p.write_text(json.dumps({"defaults": {"_comment": "c", "api": "groq"}}, indent=2) + "\n",
                 encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api="soniox", example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    check(data.get("defaults", {}).get("api") == "soniox",
          f"PIN-active: an active pick did not overwrite the pin: {data.get('defaults')}")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API,
                                example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    check(data.get("defaults", {}).get("api") == config.BUILTIN_DEFAULT_API,
          f"PIN-active: the built-in default was not written verbatim: {data.get('defaults')}")
    check(data.get("defaults", {}).get("_comment") == "c",
          "PIN-active: the defaults _comment was dropped writing the built-in pin")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=sio.REMOVE_API_PIN, example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    check("api" not in data.get("defaults", {}),
          f"PIN-active: REMOVE_API_PIN did not drop the pin: {data.get('defaults')}")
    check(data.get("defaults", {}).get("_comment") == "c",
          "PIN-active: REMOVE_API_PIN dropped the defaults _comment")

    # (e) force-write the built-in default from a NO-PIN / absent file -> present
    # (the "always start with the default" acceptance, #198).
    p = tmp / "ps_pin_write_builtin.json"
    p.write_text(json.dumps({"vocabulary": {"terms": ["x"]}}, indent=2) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=config.BUILTIN_DEFAULT_API, example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    check(data.get("defaults", {}).get("api") == config.BUILTIN_DEFAULT_API,
          f"PIN-write-builtin: the built-in default was not written from a no-pin file: {data.get('defaults')}")

    # (f) REMOVE_API_PIN over a file with NO defaults block is a no-op (creates none);
    # over a block with siblings it drops only api and keeps the rest.
    p = tmp / "ps_pin_remove_absent.json"
    p.write_text(json.dumps({"vocabulary": {"terms": ["x"]}}, indent=2) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=sio.REMOVE_API_PIN, example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    check("defaults" not in data,
          f"PIN-remove-absent: REMOVE_API_PIN created a defaults block on a file with none: {data.get('defaults')}")
    p = tmp / "ps_pin_remove_siblings.json"
    p.write_text(json.dumps({"defaults": {"_comment": "c", "api": "groq", "other": 1}}, indent=2) + "\n",
                 encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=sio.REMOVE_API_PIN, example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    check("api" not in data.get("defaults", {}), "PIN-remove-siblings: api not removed")
    check(data.get("defaults", {}).get("_comment") == "c"
          and data.get("defaults", {}).get("other") == 1,
          f"PIN-remove-siblings: sibling keys not preserved: {data.get('defaults')}")


# ---- on-save engine signal (#198, D-008/D-002) -------------------------------
def check_engine_save_signal():
    """resolve_engine_save_signal across the whole two-mode decision table -- the
    riskiest logic in the field, exhaustively tested off-Windows (the GUI itself is
    hands-on only). Returns (default_api_signal, memory_api): None=leave /
    REMOVE_API_PIN=drop / an id=verbatim-write for the pin, and an id or None for
    the memory. The two never fire together."""
    R = sio.resolve_engine_save_signal
    B = config.BUILTIN_DEFAULT_API

    # fixed, untouched (mode + engine unchanged) -> leave the pin, no memory
    check(R(mode_now="fixed", mode_loaded="fixed", engine_now="groq",
            engine_loaded="groq", remember_display_now=B, remember_display_loaded=B)
          == (None, None),
          "signal: untouched fixed should leave the pin (None) and write no memory")

    # fixed, engine changed -> write the new id verbatim, no memory
    check(R(mode_now="fixed", mode_loaded="fixed", engine_now="soniox",
            engine_loaded="groq", remember_display_now=B, remember_display_loaded=B)
          == ("soniox", None),
          "signal: a changed fixed engine should write it verbatim, no memory")

    # remember -> fixed, any engine incl. the built-in default -> write it verbatim
    check(R(mode_now="fixed", mode_loaded="remember", engine_now=B,
            engine_loaded=B, remember_display_now=B, remember_display_loaded=B)
          == (B, None),
          "signal: flipping to fixed on the built-in default should write it verbatim, no memory")

    # fixed -> remember (a pin was left) -> REMOVE the pin, no memory
    check(R(mode_now="remember", mode_loaded="fixed", engine_now="groq",
            engine_loaded="groq", remember_display_now=B, remember_display_loaded=B)
          == (sio.REMOVE_API_PIN, None),
          "signal: leaving a pin for remember-mode should drop it (REMOVE_API_PIN), no memory")

    # remember, untouched -> touch neither file
    check(R(mode_now="remember", mode_loaded="remember", engine_now="groq",
            engine_loaded="groq", remember_display_now=B, remember_display_loaded=B)
          == (None, None),
          "signal: an untouched remember save should touch neither file")

    # remember, wizard preselect moved the remembered display -> memory only, no pin
    check(R(mode_now="remember", mode_loaded="remember", engine_now="groq-large",
            engine_loaded=B, remember_display_now="groq-large", remember_display_loaded=B)
          == (None, "groq-large"),
          "signal: a moved wizard preselect should write the memory only, no pin")

    # round-trip fixed -> remember -> fixed, same engine -> no spurious rewrite
    check(R(mode_now="fixed", mode_loaded="fixed", engine_now="soniox-live",
            engine_loaded="soniox-live", remember_display_now=B, remember_display_loaded=B)
          == (None, None),
          "signal: a same-engine fixed round-trip should not rewrite the pin")

    # #201: a fixed pin on an engine that is NOW keyless (its key was removed) is still
    # an untouched save when nothing moved -> (None, None) -> defaults.api left byte-
    # identical (D-002). The signal derivation is key-agnostic on purpose; the greying
    # is display-only, so showing a greyed selected pin-radio must not read as a pick.
    check(R(mode_now="fixed", mode_loaded="fixed", engine_now="soniox-live",
            engine_loaded="soniox-live", remember_display_now=B, remember_display_loaded=B)
          == (None, None),
          "signal: an untouched fixed pin (even on a now-keyless engine) leaves it as found")

    # REMOVE and the memory write are mutually exclusive by construction -- even
    # when the display also moved, a fixed->remember flip drops the pin and never
    # records a memory.
    sig, mem = R(mode_now="remember", mode_loaded="fixed", engine_now="groq",
                 engine_loaded="soniox", remember_display_now="groq-large",
                 remember_display_loaded=B)
    check(sig is sio.REMOVE_API_PIN and mem is None,
          "signal: REMOVE must never coincide with a memory write")


# ---- save-action decision (#202) ---------------------------------------------
def check_save_action():
    """resolve_save_action across all 8 combinations of (first_run, has_key,
    tool_running). Each token maps 1:1 to a btn.* key and to _save's behavior; the
    decision is pure so the whole table is off-Windows tested (the GUI is hands-on)."""
    R = sio.resolve_save_action
    table = [
        # first_run, has_key, tool_running -> token
        (True,  True,  True,  "save_restart"),   # wizard, keyed, running -> restart (#200 shop window)
        (True,  True,  False, "save_start"),     # wizard, keyed, not running -> launch (#178)
        (True,  False, True,  "save_close"),     # wizard, keyless, running -> close (no keyless relaunch loop)
        (True,  False, False, "save_close"),     # wizard, keyless, not running -> close (#178)
        (False, True,  True,  "save_restart"),   # everyday, keyed, running -> restart
        (False, True,  False, "save"),           # everyday, keyed, not running -> plain save
        (False, False, True,  "save"),           # everyday, keyless, running -> plain save (degenerate)
        (False, False, False, "save"),           # everyday, keyless, not running -> plain save
    ]
    seen = set()
    for first_run, has_key, running, expected in table:
        got = R(first_run=first_run, has_key=has_key, tool_running=running)
        check(got == expected,
              f"save_action(first_run={first_run}, has_key={has_key}, "
              f"tool_running={running}) -> {got!r}, expected {expected!r}")
        seen.add(got)
    # The token space is exactly the four btn.* keys the app can render.
    check(seen == {"save", "save_close", "save_start", "save_restart"},
          f"save_action produced an unexpected token set: {sorted(seen)}")
    # A running tool + a key is a RESTART regardless of mode -- the half of #202 that
    # unblocks the wizard's launch over the #200 keyless shop window.
    check(R(first_run=True, has_key=True, tool_running=True) == "save_restart"
          and R(first_run=False, has_key=True, tool_running=True) == "save_restart",
          "a running keyed tool must resolve to save_restart in BOTH modes")
    # Every save_restart token has its btn.* string in both languages (the label the
    # rail sets is 'btn.' + token).
    for tok in ("save", "save_close", "save_start", "save_restart"):
        check(f"btn.{tok}" in sstr._EN and f"btn.{tok}" in sstr._DE,
              f"btn.{tok} is missing a string in EN or DE")


# ---- first-run mode decision (#163) ------------------------------------------
def check_first_run_decision(tmp):
    # env_has_key: the shared key-presence predicate (also feeds the GUI's
    # _had_stored_key, so the mode decision can never drift from it).
    check(not sio.env_has_key({}), "env_has_key: empty env is no key")
    check(sio.env_has_key({"GROQ_API_KEY": "g"}), "env_has_key: Groq key not seen")
    check(sio.env_has_key({"SONIOX_API_KEY": "s"}), "env_has_key: Soniox key not seen")
    check(not sio.env_has_key({"GROQ_API_KEY": "   "}), "env_has_key: whitespace is no key")
    check(not sio.env_has_key({"GROQ_API_KEY": ""}), "env_has_key: empty string is no key")

    # resolve_first_run: flag OR no-key -> wizard; stored key + no flag -> plain dialog.
    check(sio.resolve_first_run(False, {}) is True,
          "fresh install (no key, no flag) must open the wizard")            # criterion 1
    check(sio.resolve_first_run(False, {"GROQ_API_KEY": "g"}) is False,
          "re-run over a keyed install must open the plain dialog")          # criterion 2
    check(sio.resolve_first_run(False, {"SONIOX_API_KEY": "s"}) is False,
          "a stored Soniox key with no flag must open the plain dialog")
    check(sio.resolve_first_run(True, {"GROQ_API_KEY": "g"}) is True,
          "explicit --first-run must win even when a key is stored")
    check(sio.resolve_first_run(True, {}) is True,
          "explicit --first-run with no key must open the wizard")
    check(sio.resolve_first_run(False, {"GROQ_API_KEY": "  "}) is True,
          "a whitespace-only key is no key -> wizard")

    # Seam to the real reader: read_env feeds the decision as it does _had_stored_key.
    p = tmp / "env_fr_key"
    p.write_text("GROQ_API_KEY=gsk_real\n", encoding="utf-8")
    check(sio.resolve_first_run(False, sio.read_env(p)) is False,
          "a readable .env with a key -> plain dialog")
    # an ANSI/cp1252 .env degrades to {} in read_env -> no key -> wizard, matching
    # _had_stored_key (reuses the B3 cp1252 pattern from check_regressions).
    p = tmp / "env_fr_ansi"
    p.write_bytes("# Umlaut-Kommentar: Präfix\nGROQ_API_KEY=secret\n".encode("cp1252"))
    check(sio.resolve_first_run(False, sio.read_env(p)) is True,
          "an ANSI .env reads as no key -> wizard (consistent with _had_stored_key)")


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
        check_ui_language(tmp)
        check_engine_pin(tmp)
        check_first_run_decision(tmp)
        check_regressions(tmp)
        leftovers = [x.name for x in tmp.iterdir() if x.name.endswith(".tmp")]
        check(not leftovers, f"atomic write left temp files behind: {leftovers}")
    check_hotkey_helpers()
    check_key_check()
    check_key_check_socket()
    check_key_check_malformed()
    check_key_check_strip()
    check_i18n()
    check_preselect()
    check_engine_keyed()
    check_engine_save_signal()
    check_save_action()

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
