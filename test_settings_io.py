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
  - settings_io.resolve_fixed_entry_engine (#207): the landing table for the mode
    flip -- a remember->fixed click over a keyless seed moves the selection to the
    first keyed engine in carousel order, while a loaded pin (whose flip-away-and-back
    must stay byte-identical, D-002), a keyed seed and an all-keyless environment
    never move -- plus the AST guard that _on_mode applies it with mode_loaded from
    the frozen load state, and that _render_engine_control stays move-free.
  - the push-to-talk toggle's persistence (#233, D-002 addendum): the three-valued
    `ptt_enabled` merge -- an untouched toggle leaves the file byte-identical (a
    hand-typed invalid `enabled` included), a bool writes ONLY `enabled` so a
    hand-tuned block's _comment, trigger, insert and three timings survive an
    off-and-on round trip, a fresh block carries the example's _comment lead and
    nothing beyond `enabled`, an untouched toggle creates no block at all, and one
    save carrying all three managed keys at once lands each of them. Plus
    read_ptt_enabled (what the toggle SHOWS: config.py's JSON-boolean-only rule, so
    it can never show ON for a file the tool reads as OFF) and the pure
    resolve_ptt_save_signal table behind the byte-identity guarantee. And the save
    call site the whole guarantee hangs on, pinned statically on
    thoughtborne_settings.py's syntax tree (the GUI is hands-on only): _save must
    pass the RESOLVED signal.
  - settings_io.write_ui_language (#239, D-002/D-014): the SILENT language-toggle
    persist, gated. A corrupt-but-decodable target -- the bytes read fine, the JSON is
    invalid -- is left BYTE-identical and unwritten, so a language click can no longer
    skeleton over hand-written vocabulary / soniox_endpointing; warn-then-overwrite
    stays the explicit Save's branch alone. A healthy target still takes the surgical
    ui.language write with every other block as found, a missing one still takes the
    first-run skeleton lane (the gate keys on the warning, not on empty state), and an
    undecodable one still raises for the caller's best-effort lane. Plus that
    writer's signature, which stays too narrow to write anything but ui.language --
    that the toggle really goes through it is driven on the real window in
    test_settings_visibility.py.
  - the data-safety regressions (check_regressions): a CRLF .env round-trips
    byte-faithfully (S5), duplicate managed-key lines are ALL rewritten (S3), a
    whitespace-only value is dropped and a pasted key stripped (S4), a UTF-8 BOM is
    tolerated on read and healed on write for both files (S6), a present-but-
    unreadable file aborts the save instead of clobbering it (B1, chmod-guarded), and
    a non-UTF-8 (ANSI/cp1252) config file does not crash the readers and aborts the
    save byte-unchanged rather than destroying its vocabulary (B3).
  - the pure hotkey helpers: normalize_combo (the canonicalizer of #275, with its
    never-raise fallback for the diff path), validate_combo, decode_key_event on
    synthetic Tk events, and the diff <-> apply_hotkey_overrides round-trip
    (exercising BOTH bare-F-key and modifier-chord shapes plus the list shape,
    and that an alias-spelled default diffs to nothing).
  - key_check.classify_http (pure), the empty-key short-circuit, a non-HTTP
    response decoding to UNREACHABLE rather than crashing (B2, localhost socket), a
    malformed key (embedded newline / non-latin-1 glyph) rejected as INVALID without
    an exception escaping the worker thread, and a padded key stripped before the
    Authorization header (localhost capture).
  - key_check's #205 honesty contract: an ANSWERED status is never UNREACHABLE (swept
    over 200..599, the rule rather than examples -- a 403/404/429/5xx is INCONCLUSIVE,
    the server said something that is simply not about the key), and _check_bearer
    really puts the explicit User-Agent on the wire (localhost capture) -- urllib's
    default one is WAF-banned at Groq and was the whole cause of the false verdict.
    Plus the coupling nobody else enforces: every KeyStatus member has a row in
    _render_indicator's verdict table (a static source check -- a missing row is a
    KeyError inside the render, i.e. a dead "Test key" button) and a test.<value>
    string in both languages.
  - every string key the app hands a widget exists in the table (#299): the sinks
    are derived from thoughtborne_settings' own signatures (a parameter named
    `key` or `*_key`), their literal arguments collected off the syntax tree and
    each looked up the way check_verdict_coverage looks up its four. t() falls
    back to the KEY ITSELF on purpose, so a typo neither raises nor blanks the
    widget -- the dotted key name simply stands on the page, which no other check
    on either side of the ladder can see. A planted probe module proves per run
    that the collector still reads all three of its sites.
  - settings_strings i18n (#144): the DE and EN tables carry the identical key set
    (a missing translation fails here, not silently at runtime), every value is a
    non-empty string, the t() lang -> EN -> key-itself fallback chain, the
    engine.desc.* EN wording tracks config.API_DISPLAY, and the retired
    detect_ui_language() stays gone (D-015: English default, no system-language
    detection).
  - settings_io.write_personal_settings ui.language merge (#144, F6): ui_language
    None preserves an existing ui block untouched (and creates none when absent),
    "de"/"en" sets ui.language while preserving sibling keys + the _comment, and an
    absent-file write with a language seeds a fresh ui block. hotkeys_effective=None
    (the D-014 language-toggle self-persist write, #221) leaves every non-ui block
    exactly as found -- a non-canonical hotkeys value survives verbatim, not
    re-normalized -- and a three-None call is a content no-op.
  - settings_io.resolve_first_run / env_has_key (#163): the settings app's window-
    mode decision -- flag OR no stored key -> the first-run wizard, a stored key with
    no flag -> the plain dialog; the shared key-presence predicate and the read_env
    seam (a readable keyed .env -> plain, an ANSI .env -> wizard, matching
    _had_stored_key).
  - the Machine Room reset (#282, D-020): the one forced write that puts the four
    app-managed keys back to the shipped state -- over a dirty file (every hand-
    written block, `_comment` and parked `_` hotkey key preserved, order included,
    an unknown block among them), over the hand-typed invalid values no ordinary
    save can clear (the reason the values are forced rather than derived, asserted
    against the two save signals that return None there), over no file at all
    (the canonical all-defaults file, still without the example's placeholder
    vocabulary), twice in a row (idempotent bytes), and with a .env beside it that
    must not move. Plus the part of the call site the display lane in
    test_settings_visibility.py cannot see, statically: the two shipped values written
    as literals (its fixture cannot tell a forced one from a derived one), no
    write_env, no engine-memory write, no window destroy, the confirmation's warning
    icon and preselected answer, and the restart as the tail.
  - the save pre-flight and its dialog (#291): settings_io.unreadable_save_target
    fires exactly where a writer would abort on a target whose bytes cannot be read
    or UTF-8-decoded and stays silent exactly where a write goes through -- each case
    asserted against the real writers on the same fixture, so the .env probe cannot
    drift from write_env's own guard read. With the two controls that a naive fix
    breaks: a corrupt-but-decodable personal_settings.json stays on D-002's warn-then-
    overwrite branch, and an unreadable .env this save would not write at all (both
    key fields blank) does not block it. Plus the two halves no display lane reaches,
    statically: the probe asks with the update set the write uses, and both write
    branches keep dlg.savefail. (That the probe runs before the first write, names the
    file and uses its own title is driven on the real window in
    test_settings_visibility.py.)
  - the no-key confirmation's second text (#294): settings_io.env_read_failure tells
    a present-but-unreadable .env (ANSI, UTF-16, locked) from a missing one, which
    read_env alone cannot -- it degrades both to {} -- so the keyless save stops
    claiming that no key was found anywhere over a file that may hold one. Asserted
    against read_env on every fixture, with the two controls that must keep the
    original text (no file, and a readable keyless file) and the complementarity with
    the #291 pre-flight on one and the same broken .env. Plus the call site: _save
    asks about the .env and still names both bodies.
  - the "every save restarts" invariant (#271, D-014): the retired save-action
    resolver and its btn.save / btn.save_close strings stay gone, and the two places
    the invariant lives are pinned on thoughtborne_settings.py's syntax tree -- _save
    calls _restart_and_relaunch and never destroys the window itself, and both rails
    name btn.save_restart.

Hands-on gates (a separate test issue, not reachable here): the real Tk state-bit
values in decode_key_event, and the live "Test key" round-trip against real keys.
"""
import ast
import inspect
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
    # The example's untouched `GROQ_API_KEY=` line is a blank value, i.e. no key at
    # all -- the shared reader leaves it out of the dict entirely (D-017), so "no
    # key" has exactly one shape for every consumer.
    check(sio.read_env(p) == {"SONIOX_API_KEY": "xyz"},
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

    # S3 -- the reader is last-wins, so EVERY duplicate managed-key line must be
    # rewritten; a stale later duplicate would otherwise keep being read.
    p = tmp / "env_dup"
    p.write_text("GROQ_API_KEY=first\nFOO=bar\nGROQ_API_KEY=second\n", encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": "new"})
    got = p.read_text(encoding="utf-8")
    check(got == "GROQ_API_KEY=new\nFOO=bar\nGROQ_API_KEY=new\n",
          f"S3: duplicate managed-key lines not all replaced: {got!r}")
    check("first" not in got and "second" not in got,
          f"S3: a stale duplicate value survived: {got!r}")

    # S3b -- an `export KEY=old` line is a managed key line for the writer too since
    # #269 (the shared reader honours that form), so a rotation rewrites it IN PLACE
    # and its `export` survives: no second, bare KEY= line appended beside a stale
    # one, and a .env someone sources from a shell stays sourceable (D-002 -- change
    # the value, not the line's form).
    p = tmp / "env_export"
    p.write_text("export GROQ_API_KEY=old\nFOO=bar\n", encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": "new"})
    got = p.read_text(encoding="utf-8")
    check(got == "export GROQ_API_KEY=new\nFOO=bar\n",
          f"S3b: an export line was not rewritten in place: {got!r}")
    check(sio.read_env(p) == {"GROQ_API_KEY": "new"},
          f"S3b: the rewritten export line does not read back: {sio.read_env(p)}")

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


# ---- the save pre-flight (#291) ----------------------------------------------
def check_save_preflight(tmp):
    """settings_io.unreadable_save_target: the pre-flight an explicit save runs BEFORE
    it writes anything (#291), so a target whose bytes cannot be read is named as the
    read failure it is -- and named while `.env` is still untouched, which is what lets
    the dialog say that nothing was changed.

    Every fixture is asserted against what the real writers do with it: the pre-flight
    has to fire exactly where a write aborts and stay silent exactly where one goes
    through, which is what keeps the `.env` probe from drifting away from write_env's
    own guard read. The cp1252 lane carries the proof -- a Windows "another program
    holds it locked" has no equivalent here, so the chmod(0) lane is guarded by
    _still_unreadable (it is a no-op as root) exactly like the B1 lane above.

    Two control cases matter as much as the failures. A corrupt-but-decodable
    personal_settings.json reads fine at the byte level and must stay on D-002's
    warn-then-overwrite branch rather than be diverted into a read failure. And an
    unreadable `.env` that this save would not write at all (both key fields blank)
    must not block it: write_env is a no-op there, so a pure hotkey save over a cp1252
    `.env` works today and has to keep working."""
    P = sio.unreadable_save_target
    A_KEY = {"GROQ_API_KEY": "gsk_new"}
    BLANK = {"GROQ_API_KEY": "  ", "SONIOX_API_KEY": ""}
    GOOD_JSON = '{\n  "vocabulary": {"terms": ["keepme"]}\n}\n'
    ANSI_JSON = '{\n  "vocabulary": {"terms": ["Grüße", "Präfix"]}\n}\n'.encode("cp1252")
    ANSI_ENV = "# Umlaut-Kommentar: Präfix\nGROQ_API_KEY=secret\n".encode("cp1252")

    # 1 -- both readable, a key to write: nothing to report, and both writers then
    # really go through (the dead guard against a pre-flight that blocks everything).
    env_ok = tmp / "pf_ok.env"
    env_ok.write_text("GROQ_API_KEY=gsk_old\n", encoding="utf-8")
    ps_ok = tmp / "pf_ok.json"
    ps_ok.write_text(GOOD_JSON, encoding="utf-8")
    check(P(env_path=env_ok, env_updates=A_KEY, ps_path=ps_ok) is None,
          "pre-flight: a readable pair was reported as a read failure")
    sio.write_env(env_ok, A_KEY)
    sio.write_personal_settings(ps_ok, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS)
    check(sio.read_env(env_ok) == {"GROQ_API_KEY": "gsk_new"},
          "pre-flight: the healthy save it cleared did not write the key")

    # 2 -- a first run: neither file exists. A MISSING target is the writers' normal
    # seed case, not a failure, so the wizard's first save must pass.
    env_new = tmp / "pf_new.env"
    ps_new = tmp / "pf_new.json"
    check(P(env_path=env_new, env_updates=A_KEY, ps_path=ps_new) is None,
          "pre-flight: a first run (neither file present) was reported as a failure")
    sio.write_env(env_new, A_KEY, example_path=EXAMPLE_ENV)
    sio.write_personal_settings(ps_new, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS)
    check(env_new.exists() and ps_new.exists(),
          "pre-flight: the first-run save it cleared wrote nothing")

    # 3 -- an ANSI/cp1252 personal_settings.json: its German vocabulary is intact, just
    # in the wrong encoding (B3), so the write aborts. The pre-flight names it, names
    # it as a decoding failure, and writes nothing while doing so.
    env_3 = tmp / "pf_ansips.env"
    env_3.write_text("GROQ_API_KEY=gsk_old\n", encoding="utf-8")
    ps_3 = tmp / "pf_ansi.json"
    ps_3.write_bytes(ANSI_JSON)
    env_before, ps_before = env_3.read_bytes(), ps_3.read_bytes()
    got = P(env_path=env_3, env_updates=A_KEY, ps_path=ps_3)
    check(isinstance(got, tuple) and len(got) == 2 and Path(got[0]) == ps_3,
          f"pre-flight: an ANSI personal_settings.json was not reported as the failing "
          f"file: {got!r}")
    check(got is not None and isinstance(got[1], UnicodeDecodeError),
          f"pre-flight: the reported error is not the decoding failure the user has to "
          f"read about: {(got[1] if got else got)!r}")
    check(env_3.read_bytes() == env_before and ps_3.read_bytes() == ps_before,
          "pre-flight: the probe itself wrote something -- it exists precisely so that "
          "the abort happens before the first write")
    raised = False
    try:
        sio.write_personal_settings(ps_3, hotkeys_effective=sio.preset_ctrl_alt(),
                                    default_api=None, example_path=EXAMPLE_PS)
    except (UnicodeError, OSError):
        raised = True
    check(raised and ps_3.read_bytes() == ps_before,
          "pre-flight fixture: write_personal_settings no longer aborts on an ANSI "
          "file, so the pre-flight would now be predicting a failure that never comes")

    # 4 -- CONTROL: corrupt but decodable. The bytes read fine; this is D-002's
    # warn-then-overwrite branch, which the explicit save takes after the app has
    # warned -- diverting it into a read failure would take the way out away.
    ps_4 = tmp / "pf_corrupt.json"
    ps_4.write_text('{\n  "vocabulary": {"terms": ["keepme"]},\n', encoding="utf-8")
    _data, warn = sio.read_personal_settings(ps_4)
    check(isinstance(warn, str) and warn,
          "pre-flight fixture: the corrupt-but-decodable file should warn, not raise")
    check(P(env_path=env_ok, env_updates=A_KEY, ps_path=ps_4) is None,
          "pre-flight: a corrupt-but-decodable personal_settings.json was reported as "
          "unreadable -- its bytes read fine, and it belongs on D-002's warn-then-"
          "overwrite branch that the explicit save is allowed to take")
    sio.write_personal_settings(ps_4, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS)
    _d2, warn2 = sio.read_personal_settings(ps_4)
    check(warn2 is None,
          "pre-flight: the save it cleared did not overwrite the corrupt file")

    # 5 -- an ANSI/cp1252 .env with a key to write: the same abort at the other file.
    env_5 = tmp / "pf_ansi.env"
    env_5.write_bytes(ANSI_ENV)
    ps_5 = tmp / "pf_ansienv.json"
    ps_5.write_text(GOOD_JSON, encoding="utf-8")
    got = P(env_path=env_5, env_updates=A_KEY, ps_path=ps_5)
    check(isinstance(got, tuple) and Path(got[0]) == env_5,
          f"pre-flight: an ANSI .env was not reported as the failing file: {got!r}")
    check(got is not None and isinstance(got[1], UnicodeDecodeError),
          f"pre-flight: the reported .env error is not the decoding failure: "
          f"{(got[1] if got else got)!r}")
    check(env_5.read_bytes() == ANSI_ENV, "pre-flight: the .env probe wrote something")
    raised = False
    try:
        sio.write_env(env_5, A_KEY)
    except (UnicodeError, OSError):
        raised = True
    check(raised and env_5.read_bytes() == ANSI_ENV,
          "pre-flight fixture: write_env no longer aborts on an ANSI .env, so the "
          "probe would now be predicting a failure that never comes")

    # 6 -- CONTROL: the same broken .env, but nothing to write to it. write_env is a
    # no-op for two blank fields, so this save never touches the file and must not be
    # blocked over it -- today a pure hotkey save over a cp1252 .env goes through.
    check(P(env_path=env_5, env_updates=BLANK, ps_path=ps_5) is None,
          "pre-flight: an unreadable .env this save would not write at all blocked the "
          "save -- a hotkey-only save over a cp1252 .env has to stay possible")
    sio.write_env(env_5, BLANK)
    check(env_5.read_bytes() == ANSI_ENV,
          "pre-flight fixture: write_env is no longer a no-op for a blank update set, "
          "so the rule the probe skips the file on has changed under it")

    # 7 -- both unreadable: the file named is the one written first, so repairing it
    # is what the next click needs. With nothing to write to .env, the other one.
    ps_7 = tmp / "pf_both.json"
    ps_7.write_bytes(ANSI_JSON)
    got = P(env_path=env_5, env_updates=A_KEY, ps_path=ps_7)
    check(isinstance(got, tuple) and Path(got[0]) == env_5,
          f"pre-flight: with both files unreadable it must name .env, the one written "
          f"first and therefore the one this save fails on: {got!r}")
    got = P(env_path=env_5, env_updates=BLANK, ps_path=ps_7)
    check(isinstance(got, tuple) and Path(got[0]) == ps_7,
          f"pre-flight: with .env out of the picture the unreadable "
          f"personal_settings.json must be the one named: {got!r}")

    # 8 -- the locked lane. chmod(0) only enforces this where the filesystem and the
    # user honor it (never as root), so guard it and skip loudly rather than pass
    # falsely -- the same shape the B1 lane above uses.
    env_8 = tmp / "pf_locked.env"
    env_8.write_bytes(b"GROQ_API_KEY=secret\n")
    os.chmod(env_8, 0)
    if not _still_unreadable(env_8):
        os.chmod(env_8, stat.S_IRUSR | stat.S_IWUSR)
        print("  (skipped #291 locked .env pre-flight test: fs doesn't enforce chmod)")
    else:
        got = P(env_path=env_8, env_updates=A_KEY, ps_path=ps_ok)
        os.chmod(env_8, stat.S_IRUSR | stat.S_IWUSR)
        check(isinstance(got, tuple) and Path(got[0]) == env_8
              and isinstance(got[1], OSError),
              f"pre-flight: a locked .env was not reported as an unreadable target "
              f"(the Windows case this dialog exists for): {got!r}")
    ps_8 = tmp / "pf_locked.json"
    ps_8.write_text(GOOD_JSON, encoding="utf-8")
    os.chmod(ps_8, 0)
    if not _still_unreadable(ps_8):
        os.chmod(ps_8, stat.S_IRUSR | stat.S_IWUSR)
        print("  (skipped #291 locked personal_settings pre-flight test: fs doesn't "
              "enforce chmod)")
    else:
        got = P(env_path=env_ok, env_updates=BLANK, ps_path=ps_8)
        os.chmod(ps_8, stat.S_IRUSR | stat.S_IWUSR)
        check(isinstance(got, tuple) and Path(got[0]) == ps_8
              and isinstance(got[1], OSError),
              f"pre-flight: a locked personal_settings.json was not reported as an "
              f"unreadable target: {got!r}")


# ---- the no-key claim over an unreadable .env (#294) --------------------------
def check_nokey_unreadable(tmp):
    """settings_io.env_read_failure: the question the save's no-key confirmation has
    to ask before it speaks (#294).

    read_env answers a locked or non-UTF-8 `.env` with {} -- the very same answer it
    gives for a missing one -- so a keyless save over such a file told the user "no
    key is entered, and none was found on this PC" while their only key sat in that
    file, in that folder. Every fixture below is therefore asserted twice: that
    read_env really cannot tell the two states apart (the reason this helper exists at
    all) and that env_read_failure can, reporting the failure the remedy hangs on -- a
    decoding failure is repaired by re-saving as UTF-8, a lock by closing the other
    program, and the dialog names both.

    The controls weigh as much as the failures. A MISSING `.env` and a readable one
    holding no key are the genuine "none was found" and must keep the original text; a
    false read failure would be the mirror image of the bug. And the complementarity
    with #291 is pinned directly: for one and the same broken file, the save pre-flight
    reports it when a key is typed and passes it over when both fields are blank --
    that pass-over is exactly the gap this helper covers, so the two can never both go
    silent."""
    F = sio.env_read_failure

    # 1 -- CONTROL: no file at all. The genuine "none was found on this PC".
    missing = tmp / "nokey_missing.env"
    check(sio.read_env(missing) == {}, "nokey fixture: a missing .env should read as {}")
    check(F(missing) is None,
          "nokey: a missing .env was reported as a read failure -- that is the one "
          "case where 'none was found' is the true sentence")

    # 2 -- CONTROL: readable, but genuinely keyless (comments and a blank value only).
    keyless = tmp / "nokey_keyless.env"
    keyless.write_text("# no key yet\nGROQ_API_KEY=\n", encoding="utf-8")
    check(sio.read_env(keyless) == {}, "nokey fixture: a blank value is no key")
    check(F(keyless) is None,
          "nokey: a readable keyless .env was reported as a read failure -- nothing is "
          "wrong with that file, the user simply has no key yet")

    # 3 -- CONTROL: readable and keyed. The dialog never fires here, but a false
    # failure would mean the probe fires on healthy files.
    keyed = tmp / "nokey_keyed.env"
    keyed.write_text("GROQ_API_KEY=gsk_real\n", encoding="utf-8")
    check(F(keyed) is None, "nokey: a healthy keyed .env was reported as a read failure")

    # 4 -- an ANSI/cp1252 .env: a German comment above a perfectly good key. read_env
    # sees nothing, so without this helper the dialog denies the key exists.
    ansi = tmp / "nokey_ansi.env"
    ansi.write_bytes("# Umlaut-Kommentar: Präfix\nSONIOX_API_KEY=secret\n".encode("cp1252"))
    check(sio.read_env(ansi) == {},
          "nokey fixture: an ANSI .env should degrade to {} -- if it no longer does, "
          "the false claim this helper repairs cannot arise the way it did")
    check(isinstance(F(ansi), UnicodeDecodeError),
          f"nokey: an ANSI .env holding a key must be reported as a DECODING failure "
          f"(re-save as UTF-8 is the remedy the dialog offers): {F(ansi)!r}")

    # 5 -- a UTF-16 .env: what `"KEY=..." > .env` writes under Windows PowerShell 5.1,
    # the likeliest real route into this state -- every editor shows it fine.
    utf16 = tmp / "nokey_utf16.env"
    utf16.write_bytes("SONIOX_API_KEY=secret\n".encode("utf-16"))
    check(sio.read_env(utf16) == {}, "nokey fixture: a UTF-16 .env should degrade to {}")
    check(isinstance(F(utf16), UnicodeDecodeError),
          f"nokey: a UTF-16 .env holding a key must be reported as a decoding failure: "
          f"{F(utf16)!r}")

    # 6 -- the complementarity with the #291 pre-flight, on one file: with a key typed
    # the pre-flight names it and this branch is never reached; with both fields blank
    # the pre-flight deliberately passes it over -- and that is the save this helper
    # has to speak for.
    ps_ok = tmp / "nokey_ps.json"
    ps_ok.write_text('{\n  "vocabulary": {"terms": ["keepme"]}\n}\n', encoding="utf-8")
    typed = sio.unreadable_save_target(env_path=ansi, env_updates={"GROQ_API_KEY": "k"},
                                       ps_path=ps_ok)
    check(isinstance(typed, tuple) and Path(typed[0]) == ansi,
          f"nokey fixture: with a key typed the #291 pre-flight must still name the "
          f"broken .env -- that path is what makes this one the leftover: {typed!r}")
    blank = sio.unreadable_save_target(env_path=ansi,
                                       env_updates={"GROQ_API_KEY": "", "SONIOX_API_KEY": ""},
                                       ps_path=ps_ok)
    check(blank is None and F(ansi) is not None,
          "nokey: with both key fields blank the pre-flight passes the broken .env over "
          "(write_env would not touch it) -- if env_read_failure went silent there too, "
          "nothing in the app would say the file exists")

    # 7 -- the locked lane, the Windows case the dialog's other remedy is written for.
    # chmod(0) is a no-op as root / on filesystems that ignore it, so skip loudly
    # rather than pass falsely (the idiom of the B1 and #291 lanes above).
    locked = tmp / "nokey_locked.env"
    locked.write_bytes(b"SONIOX_API_KEY=secret\n")
    os.chmod(locked, 0)
    if not _still_unreadable(locked):
        os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR)
        print("  (skipped #294 locked .env no-key test: fs doesn't enforce chmod)")
    else:
        got, empty = F(locked), sio.read_env(locked)
        os.chmod(locked, stat.S_IRUSR | stat.S_IWUSR)
        check(empty == {}, "nokey fixture: a locked .env should degrade to {}")
        check(isinstance(got, OSError),
              f"nokey: a locked .env must be reported as an OSError -- 'close the other "
              f"program' is the remedy for that half of the dialog: {got!r}")


# ---- the no-key dialog's fork (#294) ------------------------------------------
def check_nokey_wiring():
    """The #294 fork, pinned on thoughtborne_settings.py's syntax tree -- the same
    idiom as check_readfail_wiring, and the only coverage the GUI half can have
    without a display.

    Pinned: both texts exist in both languages, _save asks env_read_failure about the
    `.env` (asking about any other file would make the new sentence as false as the
    old one), and it still names BOTH bodies -- dropping the plain one would tell a
    user on a fresh machine, who has no `.env` at all, that a file could not be
    read."""
    for key in ("dlg.nokey.title", "dlg.nokey.body",
                "dlg.nokey.title_unreadable", "dlg.nokey.body_unreadable"):
        check(key in sstr._EN and key in sstr._DE,
              f"nokey-wiring: {key} is missing a string in EN or DE")
    for lang in ("en", "de"):
        check(".env" in sstr.t("dlg.nokey.body_unreadable", lang),
              f"nokey-wiring: dlg.nokey.body_unreadable ({lang}) does not name .env -- "
              "the user has to know WHICH file to repair, and it is the one file the "
              "app's keys may ever come from (D-017)")

    methods = _settings_app_methods("nokey-wiring")
    if not methods:
        return
    save = methods.get("_save")
    if save is None:
        failures.append("nokey-wiring: SettingsApp._save not found -- the path was "
                        "renamed and this guard no longer guards it")
        return

    probes = _calls_to(save, "env_read_failure")
    check(len(probes) == 1,
          f"nokey-wiring: expected exactly one env_read_failure call in _save, found "
          f"{len(probes)} -- without it the keyless save is back to claiming that no "
          f"key was found anywhere, over a .env that may hold one (#294)")
    if len(probes) == 1:
        arg = ast.unparse(probes[0].args[0]) if probes[0].args else ""
        check(".env" in arg,
              f"nokey-wiring: _save asks env_read_failure about {arg!r}, not about the "
              ".env -- the dialog would then blame the wrong file")
    consts = {n.value for n in ast.walk(save)
              if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    for key in ("dlg.nokey.body", "dlg.nokey.body_unreadable"):
        check(key in consts,
              f"nokey-wiring: _save never names {key} -- the fork between 'no key "
              "anywhere' and 'the key file cannot be read' is gone, and one of the two "
              "situations is now described by the other's text (#294)")


# ---- pure hotkey helpers -----------------------------------------------------
def check_hotkey_helpers():
    check(sio.normalize_combo("Ctrl + Alt + P") == "ctrl+alt+p", "normalize_combo spaces/case")
    check(sio.normalize_combo(" F9 ") == "f9", "normalize_combo bare f-key")
    # #275: it is the one canonicalizer now, so aliases and modifier order
    # collapse here exactly as they do in apply_hotkey_overrides
    check(sio.normalize_combo("Control + Alt + P") == "ctrl+alt+p", "normalize_combo alias")
    check(sio.normalize_combo("alt+ctrl+w") == "ctrl+alt+w", "normalize_combo modifier order")
    # ... and it sits in the diff path, where it must never raise: an unparseable
    # combo falls back to comparing as its plain lowercase self
    check(sio.normalize_combo("Ctrl + Alt") == "ctrl+alt", "normalize_combo fallback (no key)")
    check(sio.normalize_combo("ctrl+alt+a+b") == "ctrl+alt+a+b",
          "normalize_combo fallback (two keys)")

    for good in ("ctrl+alt+p", "ctrl+alt+6", "f9", "ctrl+alt+f12"):
        ok, msg = sio.validate_combo(good)
        check(ok, f"validate_combo rejected a good combo {good!r}: {msg}")
    # D-023 (#317): no layout-resolved keys -- the umlaut and its old 'ue' alias
    # are rejected like any other non-static key
    for bad in ("", "   ", "ctrl+alt", "ctrl+alt+p+q", "ctrl+alt+notakey", "@#$",
                "ctrl+alt+ü", "ctrl+alt+ue"):
        ok, _ = sio.validate_combo(bad)
        check(not ok, f"validate_combo accepted a bad combo {bad!r}")

    C, A, S = sio.TK_STATE_CONTROL, sio.TK_STATE_ALT, sio.TK_STATE_SHIFT
    cases = [
        ((C | A, "p", "\x10"), "ctrl+alt+p"),
        ((0, "F9", ""), "f9"),                    # bare F-key
        ((C | A, "6", ""), "ctrl+alt+6"),
        ((C | A, "udiaeresis", ""), None),        # the ü lane is gone (D-023)
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

    # #275: an alias-spelled or reordered *default* is the default, so it drops
    # out of the diff instead of being frozen into personal_settings.json as an
    # override that only looks different.
    aliased = sio.preset_ctrl_alt()
    aliased["start_recording"] = "ALT + Control + W"
    check(sio.hotkeys_diff_vs_default(aliased, config.DEFAULT_HOTKEYS) == {},
          "an aliased spelling of a default should not diff")

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
    check(kc.classify_http(403) == KeyStatus.INCONCLUSIVE,
          "classify_http 403 -> INCONCLUSIVE (the WAF ban of #205: the server answered, "
          "but not about the key)")
    for code in (400, 402, 404, 405, 418, 429, 500, 502, 503):
        check(kc.classify_http(code) == KeyStatus.INCONCLUSIVE,
              f"classify_http {code} -> INCONCLUSIVE")
    # The rule that makes the two grey-ish verdicts distinguishable at all (#205): an
    # ANSWERED status is never UNREACHABLE. UNREACHABLE now means no answer arrived,
    # which only _check_bearer's transport-exception path can produce. Swept rather than
    # sampled, so a future "let's special-case 5xx again" fails here.
    for code in range(200, 600):
        check(kc.classify_http(code) != KeyStatus.UNREACHABLE,
              f"classify_http({code}) returned UNREACHABLE -- a status the server "
              "answered must never be reported as a network failure")
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


def check_key_check_user_agent():
    # #205, the root cause: urllib's default UA ("Python-urllib/3.x") is banned outright
    # by Groq's WAF, which then answers a fast, key-INDEPENDENT 403 -- the false
    # UNREACHABLE a perfectly working key produced. _check_bearer must therefore send an
    # explicit User-Agent; without this check the fix has no automated cover at all and a
    # later tidy-up of the header dict would re-open the bug silently. Capture what a
    # localhost server really receives; no outside network, pure stdlib.
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
                if raw.lower().startswith(b"user-agent:"):
                    # Keep the VALUE only. The header NAME's casing on the wire is a
                    # urllib/http.client detail (it .title()s whatever it is handed), so
                    # the filter above matches the name case-insensitively and the
                    # assertion below pins the value exactly -- the part a WAF grades.
                    captured["ua"] = raw.decode("latin-1").split(":", 1)[1].strip()
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
        except OSError:
            pass
        finally:
            conn.close()

    t = threading.Thread(target=_serve, daemon=True)
    t.start()
    kc._check_bearer(f"http://127.0.0.1:{port}/", "gsk_dummy", timeout=5.0)
    t.join(timeout=2.0)
    srv.close()
    check(captured.get("ua") == kc.USER_AGENT,
          f"_check_bearer must send the explicit User-Agent, got {captured.get('ua')!r}")
    check("urllib" not in captured.get("ua", "").lower(),
          "the request went out with urllib's default User-Agent -- exactly the "
          "signature Groq's WAF bans (#205)")
    check(kc.USER_AGENT.strip() == kc.USER_AGENT and kc.USER_AGENT != ""
          and "\n" not in kc.USER_AGENT and "\r" not in kc.USER_AGENT,
          f"USER_AGENT must be a single, non-empty, unpadded header line: "
          f"{kc.USER_AGENT!r}")


def check_verdict_coverage():
    # A KeyStatus member nobody adds to _render_indicator's verdict table makes its hard
    # table[state] lookup raise KeyError INSIDE the render -- the "Test key" button would
    # die silently on exactly the verdict just introduced (#205). thoughtborne_settings
    # imports tkinter at module level and cannot be imported from this ladder, so the
    # coverage is pinned statically, on the source text (the idiom of the
    # thoughtborne.py guards in test_restart_signal.py).
    src = (config.SCRIPT_DIR / "thoughtborne_settings.py").read_text(encoding="utf-8")
    for member in KeyStatus:
        check(f"KeyStatus.{member.name}:" in src,
              f"_render_indicator has no verdict row for KeyStatus.{member.name} -- "
              "table[state] would raise KeyError on that verdict")
        # ... and the string it names must exist in BOTH languages. The verdict table
        # keys its strings off the enum VALUE ("test.<value>"), so enum and string table
        # are coupled by convention; this is what enforces it.
        key = f"test.{member.value}"
        for lang in ("en", "de"):
            check(sstr.t(key, lang) != key, f"i18n: missing {key} ({lang})")
        check(f'"{key}"' in src,
              f"the verdict table never names {key!r} -- the string exists but no "
              "verdict renders it")


# A synthetic module carrying one real and three unknown string keys, planted at each
# of the three sites the collector reads: a sink derived from its own signature, a
# strings.t() call and _TAB_KEYS. check_string_keys runs it every time as the proof
# that the collector still detects -- the #289 idiom, since a guard that quietly stops
# finding anything is indistinguishable from a clean app.
_KEY_PROBE_SRC = '''
_TAB_KEYS = ("probe.tab",)


class SettingsApp:
    def _prose(self, parent, key, surface=""):
        pass

    def _build(self, parent):
        self._prose(parent, "btn.back")
        self._prose(parent, "probe.prose")
        strings.t("probe.t", self.lang)
'''


def _collect_string_keys(src, prefix):
    """Every string-key LITERAL the settings app hands a text sink, as (key, line,
    sink). The sinks are DERIVED from the app's own signatures instead of being listed
    here: a parameter named `key` or `*_key` is one, so a helper added later is covered
    without anyone remembering this guard, and a renamed argument cannot leave the
    guard silently reading the wrong position. `t`'s own position comes from
    settings_strings.t. A key argument counts as a literal when it is a string constant
    or a conditional between two of them ("app.title.firstrun" if first_run else
    "app.title.settings"); anything computed -- an f-string, a dict lookup -- is out of
    reach, so this is a floor on what is checked, not a census of every key."""
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        failures.append(f"{prefix}: could not parse the source: {e}")
        return []

    def key_params(fn, offset):
        # offset 1 for a method: the bound call site passes no self.
        return {a.arg: i - offset
                for i, a in enumerate(fn.args.posonlyargs + fn.args.args)
                if a.arg == "key" or a.arg.endswith("_key")}

    sinks = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef):
                    params = key_params(item, 1)
                    if params:
                        sinks[item.name] = params
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            params = key_params(node, 0)
            if params:
                sinks[node.name] = params
    t_params = list(inspect.signature(sstr.t).parameters)
    sinks.setdefault("t", {"key": t_params.index("key")})

    def literals(node):
        # Only dotted literals are keys: every table entry carries a dot, and `key`
        # is the commonest parameter name in a tkinter app -- a bind helper handed
        # "<Return>" must not be read as a string key and go falsely red.
        if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and "." in node.value:
            return [(node.value, node.lineno)]
        if isinstance(node, ast.IfExp):
            return literals(node.body) + literals(node.orelse)
        return []

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
            params = sinks.get(name)
            if not params:
                continue
            by_index = {i: p for p, i in params.items()}
            for i, arg in enumerate(node.args):
                if i in by_index:
                    found += [(k, ln, f"{name}({by_index[i]}=)")
                              for k, ln in literals(arg)]
            for kw in node.keywords:
                if kw.arg in params:
                    found += [(k, ln, f"{name}({kw.arg}=)")
                              for k, ln in literals(kw.value)]
        elif isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_TAB_KEYS" for t in node.targets):
            # render_all pushes every _TAB_KEYS entry through t() for the tab strip.
            for element in getattr(node.value, "elts", []):
                found += [(k, ln, "_TAB_KEYS") for k, ln in literals(element)]
    return found, sinks


def check_string_keys():
    # What check_verdict_coverage does for one string family, for every text sink in
    # the app (#299). t() falls back to the KEY ITSELF on purpose (a missing string is
    # never a crash), so a mistyped key neither raises nor leaves the widget blank: the
    # dotted key name is what stands on the page, non-empty -- invisible to the
    # empty-text sweep in test_settings_visibility.py and to everything else the ladder
    # has. Static, on the syntax tree, since thoughtborne_settings imports tkinter at
    # module level and cannot be imported here.
    src = (config.SCRIPT_DIR / "thoughtborne_settings.py").read_text(encoding="utf-8")
    found, sinks = _collect_string_keys(src, "string-key guard")
    check(found, "the string-key guard read no key literal at all out of "
                 "thoughtborne_settings.py -- it is passing vacuously")
    # Deriving the sinks from the signatures means a renamed `key` parameter drops
    # that helper's whole family from the guard without a sound; the helpers the
    # app is known to speak through are pinned, so the drop is loud instead.
    missing = sorted({"t", "_reg", "_prose", "_section", "_card", "_link", "_tab_link"}
                     - set(sinks))
    check(not missing,
          f"the string-key guard no longer derives {missing} as text sinks -- a "
          "`key`/`*_key` parameter was renamed, so every key that helper is handed "
          "goes unchecked; name it `key` again, or widen this list on purpose")
    for key, lineno, sink in found:
        # Both tables, the idiom check_verdict_coverage uses; EN carries the decision
        # because t() falls back there, and check_i18n holds DE to the same key set.
        check(all(sstr.t(key, lang) != key for lang in ("en", "de")),
              f"thoughtborne_settings.py:{lineno}: {sink} is handed {key!r}, a key no "
              f"string table has -- t() falls back to the key itself, so '{key}' is "
              "what stands on the page where a sentence belongs")

    # The per-run proof that the collector still detects, with its control: the three
    # planted unknown keys must come back and be rejected, the real one must not.
    seen = {key for key, _, _ in _collect_string_keys(_KEY_PROBE_SRC, "string-key probe")[0]}
    check(seen == {"btn.back", "probe.prose", "probe.t", "probe.tab"},
          f"the collector no longer reads all three planted sites, only {sorted(seen)} "
          "-- a derived method sink, a strings.t() call and _TAB_KEYS were planted, and "
          "a collector that misses one of them can go green on a typo instead")
    check(sorted(k for k in seen if sstr.t(k, "en") == k)
          == ["probe.prose", "probe.t", "probe.tab"],
          "the table lookup no longer separates a planted unknown key from a real one")

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
    # (btn.back as the probe: present in both tables with DIFFERENT values, so the
    # unknown-lang assert cannot pass on a fallback that returned the DE string --
    # which is why lang.de / lang.en, verbatim-identical in both tables, are no probe.)
    check(sstr.t("btn.back", "de") == sstr._DE["btn.back"], "t(): DE lookup wrong")
    check(sstr.t("btn.back", "en") == sstr._EN["btn.back"], "t(): EN lookup wrong")
    check(sstr.t("btn.back", "fr") == sstr._EN["btn.back"],
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

    # #233: the push-to-talk fine print points the user at the JSON block by name in
    # both languages -- guard that coupling so a rename of the block can never leave
    # the UI naming a block that no longer exists (same style as the guards above).
    for lang in ("en", "de"):
        check("push_to_talk" in sstr.t("hotkeys.ptt.fine", lang),
              f"i18n: hotkeys.ptt.fine ({lang}) must name the push_to_talk block verbatim, "
              "since that block is where the trigger key and the timings stay hand-edited")

    # D-015: the settings app defaults to English; the system-language detection is
    # retired on purpose. Guard the removal so it cannot quietly come back and flip
    # the default on German Windows again.
    check(not hasattr(sstr, "detect_ui_language"),
          "detect_ui_language() is retired (D-015) and must not return")

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
    check(set(re.findall(r"{(\w+)}", sstr._EN["machine.version.body"])) == {"version"},
          "machine.version.body must use exactly {version}")


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

    # (f) hotkeys_effective=None (the D-014 language-toggle self-persist write, #221)
    # leaves every non-ui block exactly as found. The give-away that this is a
    # leave-as-found path and not a re-diff: a NON-canonical hotkeys value ("Ctrl+Alt+W")
    # survives VERBATIM instead of being normalized to lowercase. Only ui.language moves.
    p = tmp / "ps_ui_only.json"
    p.write_text(json.dumps(
        {"hotkeys": {"start_recording": "Ctrl+Alt+W"},
         "defaults": {"api": "groq"},
         "vocabulary": {"terms": ["keepme"]}},
        indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=None, default_api=None,
                                ui_language="de", example_path=EXAMPLE_PS)
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "UI-only: file did not reload as valid JSON")
    check(data.get("hotkeys") == {"start_recording": "Ctrl+Alt+W"},
          f"UI-only: hotkeys_effective=None rewrote/normalized the hotkeys block: {data.get('hotkeys')}")
    check(data.get("defaults", {}).get("api") == "groq",
          f"UI-only: defaults.api was touched: {data.get('defaults')}")
    check(data.get("vocabulary", {}).get("terms") == ["keepme"],
          "UI-only: vocabulary clobbered")
    check(data.get("ui", {}).get("language") == "de",
          f"UI-only: ui.language not written: {data.get('ui')}")

    # (g) hotkeys_effective=None, default_api=None, ui_language=None is a content no-op:
    # nothing but a ui block is even eligible to change, and with ui_language=None that
    # stays too -- the "everything untouched" shape the toggle path relies on.
    p = tmp / "ps_ui_noop.json"
    original = {"hotkeys": {"start_recording": "Ctrl+Alt+W"},
                "defaults": {"api": "groq"},
                "vocabulary": {"terms": ["keepme"]}}
    p.write_text(json.dumps(original, indent=2, ensure_ascii=False) + "\n",
                 encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=None, default_api=None,
                                ui_language=None, example_path=EXAMPLE_PS)
    data, _ = sio.read_personal_settings(p)
    check(data.get("hotkeys") == original["hotkeys"], "UI-noop: hotkeys block changed")
    check(data.get("defaults") == original["defaults"], "UI-noop: defaults block changed")
    check(data.get("vocabulary") == original["vocabulary"], "UI-noop: vocabulary changed")
    check("ui" not in data, f"UI-noop: a ui block appeared out of nowhere: {data.get('ui')}")

    # (h) the FRESH wizard-mode toggle path (#221, D-014): a language toggle in the
    # first-run wizard self-persists onto an ABSENT personal_settings.json -- the same
    # ui.language-only write (hotkeys_effective=None, default_api=None, ui_language set)
    # as settings-mode, but here the skeleton path runs because the file does not exist
    # yet. The skeleton seeds only the managed blocks' _comment leads (NEVER the
    # example's placeholder vocabulary), the hotkeys_effective=None / default_api=None
    # guards leave those blocks with no real action, and only ui.language is set.
    p = tmp / "ps_ui_wizard_absent.json"
    sio.write_personal_settings(p, hotkeys_effective=None, default_api=None,
                                ui_language="de", example_path=EXAMPLE_PS)
    check(p.exists(), "UI-wizard-absent: file not created")
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "UI-wizard-absent: file did not reload as valid JSON")
    check(data.get("ui", {}).get("language") == "de",
          f"UI-wizard-absent: ui.language not written: {data.get('ui')}")
    hk_entries = {k: v for k, v in data.get("hotkeys", {}).items() if not k.startswith("_")}
    check(hk_entries == {},
          f"UI-wizard-absent: hotkeys_effective=None wrote a real hotkey action: {hk_entries}")
    check("api" not in data.get("defaults", {}),
          f"UI-wizard-absent: default_api=None wrote a defaults.api pin: {data.get('defaults')}")
    check("vocabulary" not in data,
          "UI-wizard-absent: absent-file write seeded a placeholder vocabulary block")


# ---- the gated language-toggle persist (#239, D-002/D-014) -------------------
def check_ui_language_gate(tmp):
    """write_ui_language: the silent D-014 toggle write, gated (#239). Everything
    above proves the merge is surgical *when there is something to be surgical about*;
    a corrupt-but-decodable target has nothing, so write_personal_settings starts from
    a bare skeleton and a language click -- which no user reads as saving -- destroys
    hand-written vocabulary / soniox_endpointing. Warn-then-overwrite is the explicit
    Save's branch alone (D-002), so the gated write must leave such a file BYTE-
    identical, while healthy and missing targets keep persisting exactly as before."""
    # (1) The acceptance case: a truncated-but-UTF-8-valid file carrying exactly the
    # hand-written blocks the issue names. Byte-identical, and the caller is told.
    p = tmp / "ps_gate_corrupt.json"
    corrupt = ('{\n  "vocabulary": {"terms": ["Grüße", "Präfix"]},\n'
               '  "soniox_endpointing": {\n')
    p.write_text(corrupt, encoding="utf-8")
    before = p.read_bytes()
    ok = sio.write_ui_language(p, "de", example_path=EXAMPLE_PS)
    check(ok is False, "GATE-corrupt: write_ui_language must report False, not write")
    check(p.read_bytes() == before,
          "GATE-corrupt: a language toggle skeletoned over a corrupt file and destroyed "
          "its hand-written vocabulary / soniox_endpointing (#239)")

    # (2) The second warning lane (a valid-JSON non-object top level) gates the same way.
    p = tmp / "ps_gate_nonobject.json"
    p.write_text("[1, 2, 3]\n", encoding="utf-8")
    before = p.read_bytes()
    check(sio.write_ui_language(p, "de", example_path=EXAMPLE_PS) is False,
          "GATE-nonobject: a non-object top level must gate like corrupt JSON")
    check(p.read_bytes() == before, "GATE-nonobject: the file was overwritten")

    # (3) A HEALTHY file still takes the surgical write, unchanged (the D-002/#221
    # guarantee this fix must not cost): only ui.language moves, every other block --
    # a non-canonical hotkey value verbatim, the pin, the vocabulary, a hand-tuned
    # push_to_talk block with its _comment -- comes back exactly as loaded.
    p = tmp / "ps_gate_healthy.json"
    loaded = {"hotkeys": {"start_recording": "Ctrl+Alt+W"},
              "defaults": {"api": "groq"},
              "vocabulary": {"terms": ["keepme"]},
              "push_to_talk": {"_comment": "hand-tuned", "trigger": "ctrl",
                               "hold_ms": 250, "enabled": True}}
    p.write_text(json.dumps(loaded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    check(sio.write_ui_language(p, "de", example_path=EXAMPLE_PS) is True,
          "GATE-healthy: a healthy file must still persist the language")
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "GATE-healthy: file did not reload as valid JSON")
    check(data.get("ui", {}).get("language") == "de",
          f"GATE-healthy: ui.language not written: {data.get('ui')}")
    check(data.get("hotkeys") == {"start_recording": "Ctrl+Alt+W"},
          f"GATE-healthy: the non-canonical hotkey did not survive verbatim -- this is "
          f"a leave-as-found write, not a re-diff: {data.get('hotkeys')}")
    rest = {k: v for k, v in data.items() if k != "ui"}
    check(rest == loaded,
          f"GATE-healthy: a block other than ui changed: {rest} != {loaded}")

    # (4) A MISSING file is not a warning: the first-run wizard toggle must keep
    # writing (managed skeleton + ui.language, never the example's placeholder
    # vocabulary). The gate keys on the warning, not on "existing came back empty" --
    # which is the whole reason it can tell this case from (1).
    p = tmp / "ps_gate_missing.json"
    check(sio.write_ui_language(p, "de", example_path=EXAMPLE_PS) is True,
          "GATE-missing: an absent file must still be created (the first-run lane)")
    check(p.exists(), "GATE-missing: file not created")
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "GATE-missing: created file is not valid JSON")
    check(data.get("ui", {}).get("language") == "de",
          f"GATE-missing: ui.language not written: {data.get('ui')}")
    check("vocabulary" not in data,
          "GATE-missing: the skeleton seeded a placeholder vocabulary block")

    # (5) An UNDECODABLE file (ANSI/cp1252, its German vocabulary intact) raises out
    # of the probe rather than being swallowed here: the abort is B1/D-002's, and
    # swallowing belongs to the caller's best-effort lane (D-014). Bytes untouched.
    p = tmp / "ps_gate_ansi.json"
    ansi_bytes = '{\n  "vocabulary": {"terms": ["Grüße", "Präfix"]}\n}\n'.encode("cp1252")
    p.write_bytes(ansi_bytes)
    raised = False
    try:
        sio.write_ui_language(p, "de", example_path=EXAMPLE_PS)
    except (UnicodeError, OSError):
        raised = True
    check(raised, "GATE-undecodable: write_ui_language swallowed the read error instead "
                  "of propagating it to the caller's best-effort lane")
    check(p.read_bytes() == ansi_bytes,
          "GATE-undecodable: the ANSI file was clobbered (vocabulary destroyed)")


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


# ---- fixed-mode entry move (#207) --------------------------------------------
def check_fixed_entry_engine():
    """resolve_fixed_entry_engine: where the fixed-mode selection lands when the user
    clicks a mode radio (#207). It moves only when entering fixed mode from a LOADED
    remember state over a keyless shown engine, and then onto the first keyed engine
    in AVAILABLE_APIS order; a loaded pin never moves (its flip-away-and-back
    round-trip has to stay byte-identical, D-002), a keyed seed stays put, and an
    all-keyless environment has nowhere to land. The sweep at the end pins the
    property the D-002 argument rests on: a move can only fire in the one
    (mode_now, mode_loaded) cell whose save writes the pin unconditionally anyway,
    and it lands on a selectable, keyed engine that is exactly what that save writes.
    """
    F = sio.resolve_fixed_entry_engine
    SON, GRQ = "SONIOX_API_KEY", "GROQ_API_KEY"
    empty = {SON: "", GRQ: ""}
    grq_stored = {SON: "", GRQ: "g_stored"}

    # The #207 gap: Groq-only, no pin loaded, the seed sitting on the keyless built-in
    # default -> land on groq-large, the FIRST keyed engine in carousel order (the one
    # the #200 fall-through starts and #178 preselects), never groq.
    check(F(mode_now="fixed", mode_loaded="remember", shown_api="soniox-live",
            live_fields=empty, stored_env=grq_stored) == "groq-large",
          "entry: a Groq-only remember->fixed flip must land on groq-large (the first "
          "keyed engine in carousel order), not stay on the keyless seed")

    # A LOADED pin is the user's own state: re-entering fixed mode over it shows it
    # unmoved, keyless or not, so the round-trip save stays (None, None) (D-002).
    check(F(mode_now="fixed", mode_loaded="fixed", shown_api="soniox-live",
            live_fields=empty, stored_env=grq_stored) is None,
          "entry: a loaded (now-keyless) pin must never be moved off -- its "
          "flip-away-and-back round-trip has to stay byte-identical (D-002)")

    # A keyed shown engine stays -- the flip inherits a working selection.
    check(F(mode_now="fixed", mode_loaded="remember", shown_api="soniox-live",
            live_fields=empty, stored_env={SON: "s_stored", GRQ: ""}) is None,
          "entry: a keyed shown engine must be left where it is")

    # All-keyless: nowhere to land -> None, never an invented target (and never an
    # AVAILABLE_APIS.index(None) crash in the caller).
    check(F(mode_now="fixed", mode_loaded="remember", shown_api="soniox-live",
            live_fields=empty, stored_env=empty) is None,
          "entry: an all-keyless environment must return None, not a bogus target")

    # Flipping TO remember never moves, whatever the key state.
    check(F(mode_now="remember", mode_loaded="remember", shown_api="soniox-live",
            live_fields=empty, stored_env=grq_stored) is None,
          "entry: entering remember mode must never move the selection")

    # A key typed this session counts like a stored one (engine_keyed's live lane), so
    # the flip right after entering the first key lands correctly.
    check(F(mode_now="fixed", mode_loaded="remember", shown_api="soniox-live",
            live_fields={SON: "", GRQ: "g_typed"}, stored_env=empty) == "groq-large",
          "entry: a Groq key typed this session must key the landing spot")

    # Blank-over-stored stays keyed (mirrors engine_keyed), so a blanked Soniox field
    # over a stored Soniox key leaves the shown engine keyed -> no move.
    check(F(mode_now="fixed", mode_loaded="remember", shown_api="soniox-live",
            live_fields={SON: "   ", GRQ: "g"},
            stored_env={SON: "s_stored", GRQ: ""}) is None,
          "entry: a blanked field over a stored key keeps the shown engine keyed "
          "(a blank never clobbers) -> no move")

    # The invariant sweep: across every flip state, seed and key layout, a move can
    # only fire in the remember->fixed cell -- the one whose save writes the pin
    # unconditionally -- so it can change WHICH engine an inevitable write records,
    # never whether an untouched save writes at all (D-002). And whatever it returns
    # must be selectable, keyed, and exactly what that save then writes.
    envs = (empty, grq_stored, {SON: "s", GRQ: ""}, {SON: "s", GRQ: "g"})
    for mn in ("fixed", "remember"):
        for ml in ("fixed", "remember"):
            for shown in config.AVAILABLE_APIS:
                for stored in envs:
                    got = F(mode_now=mn, mode_loaded=ml, shown_api=shown,
                            live_fields=empty, stored_env=stored)
                    if got is None:
                        continue
                    check((mn, ml) == ("fixed", "remember"),
                          f"entry: a move fired outside the remember->fixed flip: "
                          f"{(mn, ml)} -- only that cell's save writes the pin anyway")
                    check(got in config.AVAILABLE_APIS,
                          f"entry: {got!r} is not a selectable engine id -- the app "
                          "indexes AVAILABLE_APIS with it")
                    check(sio.engine_keyed(got, empty, stored),
                          f"entry: landed on {got!r}, which has no key -- pinning "
                          "exactly that is what #207 is about")
                    check(sio.resolve_engine_save_signal(
                              mode_now=mn, mode_loaded=ml, engine_now=got,
                              engine_loaded=shown,
                              remember_display_now=config.BUILTIN_DEFAULT_API,
                              remember_display_loaded=config.BUILTIN_DEFAULT_API)
                          == (got, None),
                          f"entry: the moved engine {got!r} is not what the flip's save "
                          "writes -- the shown selection and the written pin must agree")


# ---- settings_io push_to_talk.enabled merge (#233, D-002) --------------------
def check_ptt_toggle(tmp):
    """The three-valued `ptt_enabled` contract (#233, D-002 addendum). `None` means
    "the toggle was not touched": the whole `push_to_talk` block is left exactly as
    found (and none is created), so an unrelated save stays byte-identical there and a
    hand-typed invalid `enabled` survives. A bool writes ONLY `enabled`, preserving the
    `_comment` and every sibling -- the trigger, insert path and three thresholds a
    maintainer hand-tuned -- so switching the feature off and on again costs none of
    them."""
    # The hand-tuned shape this has to protect: a full push_to_talk block with a
    # _comment lead and all six settings, in a file that carries NO hotkeys and NO
    # defaults block (what a personal_settings.json written by hand actually looks
    # like -- those two blocks only appear once the settings app has written them).
    live_ptt = {
        "_comment": "Opt-in push-to-talk (Issue #66): tap the trigger key, release, "
                    "then press-and-HOLD it.",
        "enabled": True, "trigger": "rctrl", "insert": "type",
        "tap_window_s": 0.35, "min_hold_s": 0.25, "release_tail_s": 0.18,
    }
    siblings = {k: v for k, v in live_ptt.items() if k != "enabled"}

    # (a) an untouched toggle on a hand-tuned file -> byte-identical.
    p = tmp / "ps_ptt_untouched.json"
    raw = json.dumps({"push_to_talk": live_ptt, "vocabulary": {"terms": ["keepme"]}},
                     indent=2, ensure_ascii=False) + "\n"
    p.write_text(raw, encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS,
                                ui_language=None, ptt_enabled=None)
    check(p.read_text(encoding="utf-8") == raw,
          "PTT-none: an untouched toggle did not leave the file byte-identical: "
          f"{p.read_text(encoding='utf-8')!r}")

    # (b) switching it OFF writes only `enabled`; every hand-tuned sibling stays.
    p = tmp / "ps_ptt_off.json"
    p.write_text(json.dumps({"push_to_talk": live_ptt, "vocabulary": {"terms": ["keepme"]}},
                            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS,
                                ptt_enabled=False)
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "PTT-off: file did not reload as valid JSON")
    block = data.get("push_to_talk", {})
    check(block.get("enabled") is False, f"PTT-off: enabled not written as False: {block}")
    check({k: v for k, v in block.items() if k != "enabled"} == siblings,
          f"PTT-off: a hand-tuned sibling or the _comment was lost: {block}")
    check(data.get("vocabulary", {}).get("terms") == ["keepme"], "PTT-off: vocabulary clobbered")

    # (c) and back ON -- the off/on round trip a hand-tuned block must survive.
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS,
                                ptt_enabled=True)
    data, _ = sio.read_personal_settings(p)
    block = data.get("push_to_talk", {})
    check(block.get("enabled") is True, f"PTT-on: enabled not written as True: {block}")
    check({k: v for k, v in block.items() if k != "enabled"} == siblings,
          f"PTT-on: the off/on round trip cost a hand-tuned value: {block}")

    # (d) no block at all + True -> one created with `enabled` and the example's
    # _comment lead, and NOTHING else: config's defaults fill trigger/insert/timings,
    # so writing them would freeze today's defaults into the user's file.
    p = tmp / "ps_ptt_new.json"
    p.write_text(json.dumps({"vocabulary": {"terms": ["x"]}}, indent=2) + "\n",
                 encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS,
                                ptt_enabled=True)
    data, _ = sio.read_personal_settings(p)
    block = data.get("push_to_talk", {})
    check(block.get("enabled") is True, f"PTT-new: enabled not written: {block}")
    check("_comment" in block, "PTT-new: example _comment lead not carried")
    check(set(block) == {"_comment", "enabled"},
          f"PTT-new: a fresh block wrote more than enabled + the comment: {sorted(block)}")

    # (e) absent file + True: the block is created and the placeholder vocabulary
    # still never leaks (the D-002 data bug, guarded on every write path).
    p = tmp / "ps_ptt_absent.json"
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS,
                                ptt_enabled=True)
    raw = p.read_text(encoding="utf-8")
    check("Project Name" not in raw and "Company Name" not in raw,
          "PTT-absent: the write leaked the placeholder vocabulary (DATA BUG)")
    data, _ = sio.read_personal_settings(p)
    check(data.get("push_to_talk", {}).get("enabled") is True,
          f"PTT-absent: enabled not written on an absent file: {data.get('push_to_talk')}")

    # (f) no block + None -> none is created (the untouched toggle on a fresh install,
    # the twin of the "no ui block" rule).
    p = tmp / "ps_ptt_none_absent.json"
    p.write_text(json.dumps({"vocabulary": {"terms": ["x"]}}, indent=2) + "\n",
                 encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS, ptt_enabled=None)
    data, _ = sio.read_personal_settings(p)
    check("push_to_talk" not in data,
          f"PTT-none-absent: an untouched toggle created a block: {data.get('push_to_talk')}")

    # (g) a block that carries only a _comment (no `enabled` key at all): untouched
    # stays byte-identical, then True adds the key beside the comment.
    p = tmp / "ps_ptt_comment_only.json"
    raw = json.dumps({"push_to_talk": {"_comment": "mine"}}, indent=2,
                     ensure_ascii=False) + "\n"
    p.write_text(raw, encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=None, default_api=None,
                                example_path=EXAMPLE_PS, ptt_enabled=None)
    check(p.read_text(encoding="utf-8") == raw,
          "PTT-comment-only: an untouched toggle rewrote a comment-only block")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS, ptt_enabled=True)
    data, _ = sio.read_personal_settings(p)
    check(data.get("push_to_talk") == {"_comment": "mine", "enabled": True},
          f"PTT-comment-only: enabled not added beside the comment: {data.get('push_to_talk')}")

    # (h) a NON-dict block is replaced by a fresh one -- the rule ui already applies.
    # The tool ignores such a block entirely, so nothing of value is lost.
    p = tmp / "ps_ptt_nondict.json"
    p.write_text(json.dumps({"push_to_talk": "yes"}, indent=2) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS, ptt_enabled=True)
    data, _ = sio.read_personal_settings(p)
    check(data.get("push_to_talk", {}).get("enabled") is True,
          f"PTT-nondict: a non-dict block was not replaced: {data.get('push_to_talk')}")

    # (i) an untouched toggle preserves an INVALID hand-typed `enabled` verbatim.
    # Deliberate, and the twin of the invalid-pin rule: the tool warns about it at
    # every start; correcting what the user typed, on a save about something else,
    # is the worse behavior.
    p = tmp / "ps_ptt_invalid.json"
    p.write_text(json.dumps({"push_to_talk": {"enabled": "yes"}}, indent=2) + "\n",
                 encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api=None, example_path=EXAMPLE_PS, ptt_enabled=None)
    data, _ = sio.read_personal_settings(p)
    check(data.get("push_to_talk", {}).get("enabled") == "yes",
          f"PTT-invalid: an untouched save destroyed a hand-typed value: {data.get('push_to_talk')}")

    # (j) all three on-demand keys in ONE write. Each is merged by its own block, so
    # nothing couples them in the code -- which is exactly why a case that exercises
    # them together is worth having: a future refactor that shares state between the
    # three merges would show up here and nowhere else.
    p = tmp / "ps_ptt_combined.json"
    p.write_text(json.dumps({"push_to_talk": live_ptt, "ui": {"language": "en"},
                             "defaults": {"api": "groq"},
                             "vocabulary": {"terms": ["keepme"]}},
                            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                default_api="soniox", example_path=EXAMPLE_PS,
                                ui_language="de", ptt_enabled=False)
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "PTT-combined: file did not reload as valid JSON")
    check(data.get("push_to_talk", {}).get("enabled") is False,
          f"PTT-combined: the toggle did not land: {data.get('push_to_talk')}")
    check({k: v for k, v in data.get("push_to_talk", {}).items() if k != "enabled"} == siblings,
          f"PTT-combined: a hand-tuned push_to_talk sibling was lost: {data.get('push_to_talk')}")
    check(data.get("ui", {}).get("language") == "de",
          f"PTT-combined: ui.language did not land: {data.get('ui')}")
    check(data.get("defaults", {}).get("api") == "soniox",
          f"PTT-combined: defaults.api did not land: {data.get('defaults')}")
    check(data.get("vocabulary", {}).get("terms") == ["keepme"],
          "PTT-combined: the unmanaged vocabulary was clobbered")


def check_reset_defaults(tmp):
    """The write contract behind the Machine Room reset (#282, D-020): the one
    write_personal_settings call _reset_to_defaults makes, over the files it will
    realistically meet. Both halves of the promise need proving -- that the four
    managed keys really land at their shipped values, and that nothing else in the
    file moves -- and the second half is the one a user notices."""
    # Exactly the call _reset_to_defaults makes (test_settings_visibility's
    # test_reset_with_display drives the real button and asserts the file it leaves
    # behind): the shipped hotkeys, no pin, English, push-to-talk off.
    RESET = dict(hotkeys_effective=config.DEFAULT_HOTKEYS,
                 default_api=sio.REMOVE_API_PIN, example_path=EXAMPLE_PS,
                 ui_language="en", ptt_enabled=False)

    # (a) The realistically dirty file: every managed block carries a non-default
    # value, every hand-written one carries something worth keeping -- including an
    # `experimental` block nothing in the code knows, which proves preservation is
    # structural and not a whitelist.
    dirty = {
        "_comment": "Meine eigenen Notizen ganz oben in der Datei",
        "vocabulary": {"general": "Diktat über Softwareentwicklung",
                       "terms": ["Thoughtborne", "Soniox", "Grüße"],
                       "text": "Ein Beispielsatz für den Kontext."},
        "soniox_endpointing": {"_comment": "hand-tuned", "silence_ms": 700,
                               "min_speech_ms": 120},
        "push_to_talk": {"_comment": "Opt-in push-to-talk (Issue #66)",
                         "enabled": True, "trigger": "rctrl", "insert": "type",
                         "tap_window_s": 0.35, "min_hold_s": 0.25,
                         "release_tail_s": 0.18},
        "hotkeys": {"_comment": "Overrides for the shipped scheme",
                    "start_recording": "ctrl+alt+p", "exit_program": "f9",
                    "_disabled_exit_program": "ctrl+alt+4"},
        "defaults": {"_comment": "The engine to start on", "api": "groq-large"},
        "ui": {"_comment": "The language of the settings window", "language": "de"},
        "experimental": {"nobody_knows": [1, 2, 3]},
    }
    p = tmp / "ps_reset_dirty.json"
    p.write_text(json.dumps(dirty, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, **RESET)
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "reset-dirty: the file did not reload as valid JSON")
    # The hotkeys block keeps its comment lead and the parked "_" key -- the latter is
    # a comment to apply_hotkey_overrides, not a binding, so it is hand-written
    # content like any other (D-020) -- and loses every real override.
    check(data.get("hotkeys") == {"_comment": dirty["hotkeys"]["_comment"],
                                  "_disabled_exit_program": "ctrl+alt+4"},
          f"reset-dirty: the hotkeys block is not back at the shipped scheme (or lost "
          f"its _comment / its parked '_' key): {data.get('hotkeys')}")
    check("api" not in data.get("defaults", {}),
          f"reset-dirty: the startup pin survived the reset: {data.get('defaults')}")
    check(data.get("defaults", {}).get("_comment") == dirty["defaults"]["_comment"],
          f"reset-dirty: dropping the pin cost the defaults block's _comment: "
          f"{data.get('defaults')}")
    check(data.get("ui", {}).get("language") == "en",
          f"reset-dirty: the window language is not back at the D-015 default: "
          f"{data.get('ui')}")
    check(data.get("ui", {}).get("_comment") == dirty["ui"]["_comment"],
          f"reset-dirty: the ui block's _comment was lost: {data.get('ui')}")
    check(data.get("push_to_talk", {}).get("enabled") is False,
          f"reset-dirty: push-to-talk was not switched off: {data.get('push_to_talk')}")
    check({k: v for k, v in data.get("push_to_talk", {}).items() if k != "enabled"}
          == {k: v for k, v in dirty["push_to_talk"].items() if k != "enabled"},
          f"reset-dirty: the reset cost a hand-tuned push-to-talk value -- only "
          f"`enabled` is a setting, the trigger and the three timings are the user's "
          f"(D-020): {data.get('push_to_talk')}")
    # The unmanaged half, compared as SERIALIZED json so a reordering fails too -- a
    # bare == would not notice one, and the user reads this file.
    for key in ("_comment", "vocabulary", "soniox_endpointing", "experimental"):
        check(json.dumps(data.get(key), indent=2, ensure_ascii=False)
              == json.dumps(dirty[key], indent=2, ensure_ascii=False),
              f"reset-dirty: the reset changed the hand-written {key!r} -- the reset "
              f"is about settings, never the user's data (D-020): {data.get(key)!r}")

    # (b) The case the whole "forced, not derived" design exists for: values no save
    # can clear. A hand-typed invalid pin and a quoted `enabled` are DISPLAYED as the
    # default they produce, so the form's controls never move over them, the save
    # signals return None, and D-002's leave-as-found rule keeps exactly the junk the
    # tool warns about at every start. The reset is the only way out.
    check(sio.resolve_engine_save_signal(
        mode_now="remember", mode_loaded="remember", engine_now="soniox-live",
        engine_loaded="soniox-live", remember_display_now="groq",
        remember_display_loaded="groq") == (None, None),
        "reset-invalid: an untouched engine control no longer signals (None, None) -- "
        "the premise of the forced write below has changed, re-read D-002/D-008")
    check(sio.resolve_ptt_save_signal(enabled_now=False, enabled_loaded=False) is None,
          "reset-invalid: an untouched push-to-talk toggle no longer signals None -- "
          "the premise of the forced write below has changed, re-read D-002")
    p = tmp / "ps_reset_invalid.json"
    p.write_text(json.dumps({"defaults": {"api": "grok"},
                             "push_to_talk": {"enabled": "yes"},
                             "vocabulary": {"terms": ["keepme"]}},
                            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, **RESET)
    data, _ = sio.read_personal_settings(p)
    check("api" not in data.get("defaults", {}),
          f"reset-invalid: an unknown engine id survived the reset -- no ordinary save "
          f"can clear it either, so nothing would: {data.get('defaults')}")
    check(data.get("push_to_talk", {}).get("enabled") is False,
          f"reset-invalid: a hand-typed non-boolean `enabled` survived the reset: "
          f"{data.get('push_to_talk')}")
    check(data.get("vocabulary", {}).get("terms") == ["keepme"],
          "reset-invalid: the vocabulary was clobbered")

    # (c) No file at all: the reset writes the canonical all-defaults file, self-
    # documenting through the example's _comment leads -- and WITHOUT the example's
    # placeholder vocabulary, which would otherwise become live Soniox vocabulary.
    p = tmp / "ps_reset_fresh.json"
    sio.write_personal_settings(p, **RESET)
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "reset-fresh: the written file is not valid JSON")
    check("vocabulary" not in data,
          f"reset-fresh: the reset seeded the example's placeholder vocabulary: "
          f"{data.get('vocabulary')}")
    check("api" not in data.get("defaults", {}) and data.get("ui", {}).get("language") == "en"
          and data.get("push_to_talk", {}).get("enabled") is False
          and not [k for k in data.get("hotkeys", {}) if not k.startswith("_")],
          f"reset-fresh: the four managed facts are not all at their shipped value: {data}")

    # (d) Idempotence -- the honest form of the issue's "byte-for-byte unchanged".
    # write_personal_settings re-serializes the whole file, so a hand-formatted one
    # comes back normalized on the FIRST reset; from there the bytes must stand still.
    p = tmp / "ps_reset_twice.json"
    p.write_text(json.dumps(dirty, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    sio.write_personal_settings(p, **RESET)
    once = p.read_bytes()
    sio.write_personal_settings(p, **RESET)
    check(p.read_bytes() == once,
          "reset-twice: a second reset changed the file -- the reset is a forced "
          "state, so repeating it must be a no-op on the bytes")

    # (e) .env is untouched. Trivially true (the reset never calls write_env, which
    # check_reset_wiring pins on the source), but it is the feature's loudest promise.
    env = tmp / "ps_reset.env"
    env.write_text("GROQ_API_KEY=gsk_secret\nSONIOX_API_KEY=so_secret\n", encoding="utf-8")
    env_before = env.read_bytes()
    sio.write_personal_settings(tmp / "ps_reset_dirty.json", **RESET)
    check(env.read_bytes() == env_before,
          "reset-env: the settings write reached the .env -- the keys are the user's "
          "data and the reset must never write them (D-011, D-020)")


def check_reset_wiring():
    """The part of the reset's call site that no run of the real window can show
    (#282, D-020). test_settings_visibility.test_reset_with_display drives the button
    itself -- the confirmation gate, the file the confirmed reset leaves behind, the
    vocabulary and the .env left alone, the restart reached, the button frozen against
    a second click -- and what that lane catches was dropped here (#309). What is left
    is what stayed green when each half was measured against it: the two confirmation
    bodies (their key reaches t() through a variable, so the literal-reading
    check_string_keys never sees them, and the lane compares t() against t(), where a
    missing key reads the same on both sides), the body2 wording, the two shipped
    values whose FORCING its fixture cannot tell from a derivation, two writers that
    must be UNREACHABLE rather than merely unused, the two confirmation keywords its
    askyesno stub throws away with its **k, the restart's POSITION, which the stub
    registers wherever it stands, and the absence of a window destroy in the one abort
    branch no lane provokes."""
    for key in ("dlg.reset.body", "dlg.reset.body_corrupt"):
        check(key in sstr._EN and key in sstr._DE,
              f"reset-wiring: {key} is missing a string in EN or DE")
    # The "this is not a wipe" pointer names both files verbatim in both languages --
    # it is the only place the app says how to get a genuinely empty slate, and the
    # reset deliberately is not it (same coupling style as the hotkeys.ptt.fine guard).
    for lang in ("en", "de"):
        for name in (".env", "personal_settings.json"):
            check(name in sstr.t("machine.reset.body2", lang),
                  f"reset-wiring: machine.reset.body2 ({lang}) must name {name} "
                  "verbatim -- it is the app's only pointer to a true wipe")

    methods = _settings_app_methods("reset-wiring")
    if not methods:
        return
    reset = methods.get("_reset_to_defaults")
    if reset is None:
        failures.append("reset-wiring: SettingsApp._reset_to_defaults not found -- the "
                        "reset was renamed and this guard no longer guards anything")
        return

    # The two shipped values are written as LITERALS, not derived from the form. The
    # display lane reads the file a confirmed reset leaves behind, but it runs with
    # app.lang == "en" and an untouched push-to-talk switch, so a derived `self.lang` /
    # `self.ptt_var.get()` produces the same bytes there and it stays green (measured,
    # #309) -- while a user resetting out of the German window would keep German
    # (D-015) and one with push-to-talk on would keep it on. hotkeys_effective and
    # default_api need no literal here: the lane's fixture differs from the shipped
    # value for both, so deriving either turns it red.
    writes = _calls_to(reset, "write_personal_settings")
    check(len(writes) == 1,
          f"reset-wiring: _reset_to_defaults makes {len(writes)} settings writes, "
          "expected exactly one -- the reset is one forced write, not a sequence")
    if len(writes) == 1:
        kw = {k.arg: k.value for k in writes[0].keywords}
        for name, want in (("ui_language", "en"), ("ptt_enabled", False)):
            node = kw.get(name)
            check(isinstance(node, ast.Constant) and type(node.value) is type(want)
                  and node.value == want,
                  f"reset-wiring: {name}= is not the literal {want!r} -- the shipped "
                  "state is written unconditionally, never diffed (D-015/D-002)")
    # Both writers below are checked for UNREACHABILITY, which is why a lane that
    # watches the files cannot stand in: in the reset scenario the key fields are empty
    # and the engine memory is not read back, so an added write moves no bytes there.
    check(not _calls_to(reset, "write_env"),
          "reset-wiring: _reset_to_defaults calls write_env -- the API keys are safe "
          "STRUCTURALLY, by this method never reaching the only .env writer, and that "
          "is a stronger promise than any dialog wording (D-011, D-020)")
    check(not _calls_to(reset, "write_last_engine"),
          "reset-wiring: _reset_to_defaults writes the engine memory -- "
          "runtime_state.json records what the user did and is not a setting; dropping "
          "a pin leaves the memory alone and it keeps deciding (D-008)")
    # The confirmation is not optional, and its preselected answer is the preserving
    # one: an Enter or a reflex click must never reset (D-011's shape). The VALUES are
    # what carry that promise -- a `default=` that happens to say YES would satisfy a
    # guard that only looks for the keyword, and D-020 lists exactly that among the
    # things not to reintroduce -- so both keywords are pinned to their attribute.
    asks = _calls_to(reset, "askyesno")
    ask_kw = {k.arg: k.value for k in asks[0].keywords} if len(asks) == 1 else {}
    check(len(asks) == 1
          and all(isinstance(ask_kw.get(arg), ast.Attribute)
                  and ask_kw[arg].attr == attr
                  and getattr(ask_kw[arg].value, "id", None) == "messagebox"
                  for arg, attr in (("default", "NO"), ("icon", "WARNING"))),
          "reset-wiring: the reset's askyesno is not asked with "
          "`icon=messagebox.WARNING, default=messagebox.NO` -- an action this "
          "irreversible needs a confirmation that looks like a warning and whose "
          "destructive answer is never the preselected one (D-011, D-020)")
    # The restart lane is reused, not copied: the tail is the handshake. That is also
    # what makes the frozen _loaded snapshots harmless -- every exit from
    # _restart_and_relaunch ends the window, so there is no next save to lie to.
    tail = reset.body[-1]
    tail_call = tail.value if isinstance(tail, ast.Expr) else None
    check(isinstance(tail_call, ast.Call)
          and getattr(tail_call.func, "attr", None) == "_restart_and_relaunch",
          "reset-wiring: _reset_to_defaults does not END in _restart_and_relaunch -- "
          "pickup is start-based (D-002), so a reset that does not restart would "
          "silently defer itself to the user's next manual start")
    # And nothing here destroys the window. The lane walks three of the four exits and
    # dies with a TclError on a destroy in any of them -- but not the WRITE-failure
    # branch, which no display lane provokes, so a destroy THERE is caught by this and
    # by nothing else (measured, #309).
    destroys = [n for n in ast.walk(reset)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "destroy"
                and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "root"]
    check(not destroys,
          "reset-wiring: _reset_to_defaults destroys the window itself -- every exit "
          "of a completed reset belongs to _restart_and_relaunch; the aborts (a "
          "declined dialog, a read or write failure) leave the window open on purpose")


# ---- the read-failure dialog's two call sites (#291) --------------------------
def check_readfail_wiring():
    """The two halves of #291 that no run of the real window reaches: the pre-flight
    probing with the update set the write uses, and both WRITE-failure branches still
    naming dlg.savefail.title.

    The rest is driven for real -- test_settings_visibility's
    test_save_readfail_with_display for the everyday save, test_reset_with_display for
    the reset's own read branch: that the pre-flight runs BEFORE the first write, that
    the failure arrives under the read title, and that it names the file. Both halves
    left here were measured against those lanes and stayed green (#309): all four of
    the save lane's cases either fill the Groq field or leave both blank, so a probe
    looking at only the Groq half agrees with the write in every one of them; and no
    display lane ever makes a WRITE fail, so a rename carrying both branches over to
    the read title -- the plausible one, since they sit in the same method and read
    almost alike -- goes unnoticed everywhere else. The string keys are
    check_string_keys' (both titles stand as literals in these two methods) and
    check_i18n's (the {file} placeholder is held to EN by the lane, to DE by the
    placeholder parity)."""
    methods = _settings_app_methods("readfail-wiring")
    if not methods:
        return
    for name in ("_save", "_reset_to_defaults"):
        method = methods.get(name)
        if method is None:
            failures.append(f"readfail-wiring: SettingsApp.{name} not found -- the "
                            "path was renamed and this guard no longer guards it")
            return
        consts = {n.value for n in ast.walk(method)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        check("dlg.savefail.title" in consts,
              f"readfail-wiring: {name} no longer names dlg.savefail.title -- the "
              "WRITE-failure branch was renamed along with the read one, and there the "
              "old title is the correct one")

    save = methods["_save"]
    probes = _calls_to(save, "unreadable_save_target")
    check(len(probes) == 1,
          f"readfail-wiring: expected exactly one unreadable_save_target call in _save, "
          f"found {len(probes)} -- the check below reads that one probe's arguments")
    writes = _calls_to(save, "write_env")
    check(len(writes) == 1,
          f"readfail-wiring: expected exactly one write_env call in _save, found "
          f"{len(writes)} -- this guard assumes the single .env write")
    if len(probes) == 1 and len(writes) == 1:
        probed = {k.arg: k.value for k in probes[0].keywords if k.arg}
        updates = probed.get("env_updates")
        written = writes[0].args[1] if len(writes[0].args) > 1 else None
        check(updates is not None and written is not None
              and ast.unparse(updates) == ast.unparse(written),
              "readfail-wiring: _save probes with "
              f"{ast.unparse(updates) if updates is not None else '<missing>'} but "
              f"writes {ast.unparse(written) if written is not None else '<missing>'} "
              "-- the probe would decide about a different update set than the write, "
              "so the .env no-op rule it leans on could be answered for the wrong one")


def check_ptt_read():
    """read_ptt_enabled: what the settings toggle SHOWS for a given file, by exactly
    the rule config.py applies -- a real JSON boolean or nothing. The point is that the
    toggle can never show ON for a file the tool reads as OFF."""
    R = sio.read_ptt_enabled
    check(R({}) is False, "ptt-read: an empty settings dict must read as OFF")
    check(R({"push_to_talk": {}}) is False, "ptt-read: an empty block must read as OFF")
    check(R({"push_to_talk": {"enabled": True}}) is True,
          "ptt-read: a real JSON true must read as ON")
    check(R({"push_to_talk": {"enabled": False}}) is False,
          "ptt-read: a real JSON false must read as OFF")
    for junk in ("yes", "true", "True", 1, 0, None, [], {}):
        check(R({"push_to_talk": {"enabled": junk}}) is False,
              f"ptt-read: {junk!r} must read as OFF (config.py honors only a JSON boolean)")
    for junk in ("yes", None, [], 3):
        check(R({"push_to_talk": junk}) is False,
              f"ptt-read: a non-dict block ({junk!r}) must read as OFF")
    # The shipped example ships the feature OFF -- the file the user reads, and the
    # one whose _comment lead a freshly created block inherits.
    example, _ = sio.read_personal_settings(EXAMPLE_PS)
    check(R(example) is False,
          "ptt-read: personal_settings.example.json must ship push-to-talk OFF")

    # The ship default itself (#233's premise, and what the fine print states as
    # fact), checked as SOURCE rather than as config.PTT_ENABLED: the imported
    # attribute is the EFFECTIVE value -- this checkout's own personal_settings.json
    # overrides it at import -- so asserting the attribute would fail on every machine
    # whose user switched the gesture on. What must not change is the module-level
    # initializer; flipping it to True would arm the trigger polling for every install
    # unasked, which is the one thing default-off exists to prevent.
    config_src = (config.SCRIPT_DIR / "config.py").read_text(encoding="utf-8")
    toplevel = re.findall(r"^PTT_ENABLED\s*=\s*(\S+)", config_src, re.MULTILINE)
    check(toplevel == ["False"],
          f"ptt-read: config.py must carry exactly one module-level PTT_ENABLED "
          f"initializer and it must be False, found {toplevel}")


def check_ptt_save_signal():
    """resolve_ptt_save_signal across its whole (loaded, now) table. Identity asserts,
    not truthiness: a stray 0 or "" returned instead of False would still write, and
    the None row is exactly the D-002 byte-identity guarantee."""
    R = sio.resolve_ptt_save_signal
    check(R(enabled_now=False, enabled_loaded=False) is None,
          "ptt-signal: untouched OFF must leave the block as found")
    check(R(enabled_now=True, enabled_loaded=True) is None,
          "ptt-signal: untouched ON must leave the block as found")
    check(R(enabled_now=True, enabled_loaded=False) is True,
          "ptt-signal: switching it on must write True")
    check(R(enabled_now=False, enabled_loaded=True) is False,
          "ptt-signal: switching it off must write False")


def _settings_app_methods(prefix):
    """The SettingsApp methods of thoughtborne_settings.py as a {name: FunctionDef}
    map, or {} after recording a failure. The settings app imports tkinter at module
    level and cannot be imported from this ladder, so its call sites are checked on
    the source -- as a syntax tree rather than as text, so renaming a local or
    reflowing a call proves nothing while a real regression still goes red (the idiom
    of the thoughtborne.py guards in test_restart_signal.py, one step more precise)."""
    src_path = config.SCRIPT_DIR / "thoughtborne_settings.py"
    try:
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
    except Exception as e:
        failures.append(f"{prefix}: could not parse thoughtborne_settings.py: "
                        f"{type(e).__name__}: {e}")
        return {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "SettingsApp":
            return {n.name: n for n in node.body if isinstance(n, ast.FunctionDef)}
    failures.append(f"{prefix}: class SettingsApp not found in thoughtborne_settings.py")
    return {}


def _calls_to(method, name):
    """Every `<something>.name(...)` call inside one method's syntax tree."""
    return [c for c in ast.walk(method) if isinstance(c, ast.Call)
            and getattr(c.func, "attr", None) == name]


def check_ptt_wiring():
    """The save write call site in thoughtborne_settings.py, pinned statically (#233).
    Everything above proves the MERGE keeps its promises; what nothing else can catch
    is the writer being fed the wrong thing. The push-to-talk byte-identity guarantee
    lives in the call site alone: _save must hand `ptt_enabled=` the RESOLVED signal --
    the raw toggle state would rewrite the block on every save (D-002). (The language
    toggle's own wiring moved to check_lang_writer_signature with the #239 gate: since
    it no longer calls write_personal_settings at all, "passes no ptt_enabled" is now a
    property of write_ui_language's signature, asserted there.)"""
    methods = _settings_app_methods("ptt-wiring")
    if not methods:
        return

    def writes_in(method):
        """Every write_personal_settings call inside one method."""
        return _calls_to(method, "write_personal_settings")

    # ---- _save: ptt_enabled= gets the resolver's result, never the raw toggle ----
    save = methods.get("_save")
    if save is None:
        failures.append("ptt-wiring: SettingsApp._save not found -- the save path was "
                        "renamed and this guard no longer guards anything")
        return
    resolved = set()
    for node in ast.walk(save):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "attr", None) == "resolve_ptt_save_signal"):
            resolved |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    check(resolved,
          "ptt-wiring: _save never assigns settings_io.resolve_ptt_save_signal(...) to "
          "a local -- without it the toggle state reaches the writer unresolved and "
          "every save rewrites the push_to_talk block (D-002)")
    save_writes = writes_in(save)
    check(len(save_writes) == 1,
          f"ptt-wiring: expected exactly one write_personal_settings call in _save, "
          f"found {len(save_writes)} -- this guard assumes the single save write")
    for call in save_writes:
        passed = {k.arg: k.value for k in call.keywords if k.arg}
        check("ptt_enabled" in passed,
              "ptt-wiring: _save's write_personal_settings call passes no ptt_enabled -- "
              "the toggle would never persist")
        value = passed.get("ptt_enabled")
        check(isinstance(value, ast.Name) and value.id in resolved,
              "ptt-wiring: _save passes ptt_enabled="
              f"{ast.unparse(value) if value is not None else '<missing>'}, not the "
              f"resolve_ptt_save_signal result ({' / '.join(sorted(resolved)) or 'none'}) -- "
              "an untouched toggle would then rewrite the block on every save (D-002)")


# ---- the silent language writer's signature (#239) ---------------------------
def check_lang_writer_signature():
    """How narrow settings_io.write_ui_language is -- a path, a language, an example
    path, and nothing else -- which is what the D-014 lane's safety rests on (#239).

    That the toggle goes through this writer at all, and that it leaves a corrupt
    personal_settings.json byte-identical, is driven on the real window by
    test_settings_visibility.test_language_toggle_gate_with_display. What no run can
    show is a WIDENED writer: a parameter for another block, handed through from the
    call site, writes that block for every user whose file carries one -- and a fixture
    that carries none stays green either way (measured, #309). Narrowness is a property
    of the signature, so the signature is where it is asserted.

    Let go with the rest of the old AST half, deliberately: "exactly one
    write_ui_language call". A second, identical write is idempotent, so nothing
    observable is lost -- measured (#309), and recorded here rather than dropped in
    silence, since bringing it back would mean parsing the app source again for a
    fault with no consequence."""
    code = sio.write_ui_language.__code__
    params = list(code.co_varnames[:code.co_argcount])
    check(params == ["path", "language", "example_path"],
          f"lang-writer: settings_io.write_ui_language takes {params} -- the silent "
          "D-014 lane's writer must stay narrow (path, language, example_path); a "
          "parameter that writes another block would put the push_to_talk / hotkeys / "
          "engine-pin risk back into a toggle (D-002)")
    # co_varnames[:co_argcount] above sees the POSITIONAL parameters only, so the
    # narrowness check would miss a keyword-only or catch-all one (0x04 CO_VARARGS,
    # 0x08 CO_VARKEYWORDS).
    extras = list(code.co_varnames[code.co_argcount:
                                   code.co_argcount + code.co_kwonlyargcount])
    if code.co_flags & 0x04:
        extras.append("*args")
    if code.co_flags & 0x08:
        extras.append("**kwargs")
    check(not extras,
          f"lang-writer: settings_io.write_ui_language also takes {extras} -- a "
          "keyword-only or catch-all parameter widens the silent lane's writer just as "
          "a positional one does (D-002)")


# ---- the fixed-mode entry move's wiring (#207) -------------------------------
def check_mode_flip_wiring():
    """That _on_mode really applies resolve_fixed_entry_engine's verdict to
    engine_index, pinned statically for the same reason as check_ptt_wiring: the table
    above is decoration unless the mode click uses it, and feeding the resolver
    `mode_var` instead of `_mode_loaded` -- the click path instead of the loaded state
    -- would move a loaded pin on flip-away-and-back and rewrite it on save (D-002).
    Plus the other half of that guarantee: _render_engine_control must stay MOVE-free.
    "A programmatic set is not a pick" is what keeps an untouched save byte-identical,
    which is exactly why #207 puts the move in the click handler alone. And one crash
    lane rather than a D-002 one: the move has to stay behind a test on the resolver's
    verdict, since all-keyless -- the first-run wizard's normal state -- resolves to
    None."""
    methods = _settings_app_methods("mode-flip-wiring")
    if not methods:
        return
    on_mode = methods.get("_on_mode")
    if on_mode is None:
        failures.append("mode-flip-wiring: SettingsApp._on_mode not found -- the mode "
                        "handler was renamed and this guard no longer guards anything")
        return

    calls = _calls_to(on_mode, "resolve_fixed_entry_engine")
    check(len(calls) == 1,
          f"mode-flip-wiring: expected exactly one resolve_fixed_entry_engine call in "
          f"_on_mode, found {len(calls)} -- without it a remember->fixed flip pins "
          "whatever keyless engine the seed happened to show (#207)")
    for call in calls:
        passed = {k.arg: k.value for k in call.keywords if k.arg}
        ml = passed.get("mode_loaded")
        check(isinstance(ml, ast.Attribute) and ml.attr == "_mode_loaded"
              and isinstance(ml.value, ast.Name) and ml.value.id == "self",
              "mode-flip-wiring: the resolver must be fed mode_loaded=self._mode_loaded, "
              f"not {ast.unparse(ml) if ml is not None else '<missing>'} -- the CLICK "
              "path (mode_var) would move a loaded pin on flip-away-and-back and let "
              "the save rewrite it (D-002)")
        # The mirror image, and the one the checks below cannot notice: mode_now is the
        # live click path. Hand it anything else -- most plausibly self._mode_loaded,
        # once someone "unifies" the two arguments -- and the resolver's first clause
        # answers None to every click (either mode_now is not "fixed", or mode_loaded
        # is), so the move never fires again while everything else here stays green:
        # the call is present, its result is assigned, engine_index is set.
        mn = passed.get("mode_now")
        check(isinstance(mn, ast.Call) and getattr(mn.func, "attr", None) == "get"
              and isinstance(mn.func.value, ast.Attribute)
              and mn.func.value.attr == "mode_var"
              and isinstance(mn.func.value.value, ast.Name)
              and mn.func.value.value.id == "self",
              "mode-flip-wiring: the resolver must be fed mode_now=self.mode_var.get(), "
              f"not {ast.unparse(mn) if mn is not None else '<missing>'} -- only the "
              "live click path can tell the resolver the user is ENTERING fixed mode; "
              "fed the loaded state instead it returns None on every click and #207's "
              "move dies in silence (#207)")

    resolved = set()
    for node in ast.walk(on_mode):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "attr", None) == "resolve_fixed_entry_engine"):
            resolved |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    check(resolved,
          "mode-flip-wiring: _on_mode never assigns resolve_fixed_entry_engine(...) to a "
          "local -- its None case (all-keyless) has to be handled before the result can "
          "index AVAILABLE_APIS (#207)")
    moves = []
    for node in ast.walk(on_mode):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if (isinstance(t, ast.Attribute) and t.attr == "engine_index"
                    and isinstance(t.value, ast.Name) and t.value.id == "self"
                    and {n.id for n in ast.walk(node.value)
                         if isinstance(n, ast.Name)} & resolved):
                moves.append(node)
    check(moves,
          "mode-flip-wiring: _on_mode never assigns self.engine_index from the "
          "resolver's result -- the decision table above then guards nothing (#207)")

    # ...and that move stays behind a decision on the resolver's local. The verdict is
    # None whenever there is nothing keyed to land on -- the all-keyless environment a
    # first-run wizard normally starts in -- so an unguarded
    # AVAILABLE_APIS.index(target) raises ValueError on the very first fixed click,
    # with every check above still green. Both guard shapes count (the assignment
    # nested under an `if <local>...`, and the `if <local> is None: return` clause a
    # refactor might prefer); what may not vanish is the decision itself.
    parents = {}
    for node in ast.walk(on_mode):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def _reads_local(node):
        return bool({n.id for n in ast.walk(node) if isinstance(n, ast.Name)} & resolved)

    def _guarded(assign):
        node = parents.get(assign)
        while node is not None:          # nested under a test on the local
            if isinstance(node, ast.If) and _reads_local(node.test):
                return True
            node = parents.get(node)
        return any(isinstance(n, ast.If) and _reads_local(n.test)   # or a guard clause
                   and any(isinstance(s, (ast.Return, ast.Raise)) for s in n.body)
                   and (n.end_lineno or n.lineno) <= assign.lineno
                   for n in ast.walk(on_mode))

    check(all(_guarded(m) for m in moves),
          "mode-flip-wiring: _on_mode assigns self.engine_index from the resolver's "
          "result without ever testing that result -- an all-keyless environment "
          "(the first-run wizard's normal state) resolves to None, and "
          "AVAILABLE_APIS.index(None) then raises ValueError on the first click into "
          "fixed mode (#207)")

    render = methods.get("_render_engine_control")
    if render is None:
        failures.append("mode-flip-wiring: SettingsApp._render_engine_control not found "
                        "-- the engine renderer was renamed and the move-free half of "
                        "this guard no longer guards anything")
        return
    dirty = sorted({t.attr for node in ast.walk(render) if isinstance(node, ast.Assign)
                    for t in node.targets
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                    and t.value.id == "self"
                    and t.attr in ("engine_index", "_engine_user_chose")})
    check(not dirty and not _calls_to(render, "resolve_fixed_entry_engine"),
          f"mode-flip-wiring: _render_engine_control gained a selection side effect "
          f"({dirty or 'a resolve_fixed_entry_engine call'}) -- the renderer must stay "
          "move-free so a programmatic set never counts as a pick (D-002)")


# ---- every save restarts (#271) ----------------------------------------------
def check_save_always_restarts():
    """The invariant that replaced the save-action table: every save restarts -- wizard
    or everyday, keyed or keyless (#271, D-014). Nothing pure is left to decide, so
    this guards the retirement (the resolver and its two branch strings gone) and pins
    the two places the invariant now lives, statically on thoughtborne_settings.py's
    syntax tree -- which is what replaces the off-Windows coverage the removed resolver
    used to carry (the GUI itself is hands-on only)."""
    # The resolver is gone from settings_io -- not left returning a constant, which is
    # exactly the residue D-014's step-1 doctrine forbids.
    check(not hasattr(sio, "resolve_save_action"),
          "settings_io.resolve_save_action is back -- #271 removed the save-action "
          "branch outright, and a resolver with one answer is not a removal")
    # The one surviving save label exists in both languages ...
    check("btn.save_restart" in sstr._EN and "btn.save_restart" in sstr._DE,
          "btn.save_restart is missing a string in EN or DE")
    # ... and the strings of the retired branches stay gone: btn.save / btn.save_close
    # with #271's keyless-close branch, the other three with the #223 standalone lane.
    for dead in ("btn.save", "btn.save_close",
                 "btn.save_start", "footer.next_start", "footer.restart"):
        check(dead not in sstr._EN and dead not in sstr._DE,
              f"{dead} must be gone from both string tables (#271 / #223, D-014)")

    # The wiring: _save ends in the restart and never in a bare window destroy, and
    # both rails name the one label.
    methods = _settings_app_methods("save-wiring")
    if not methods:
        return
    save = methods.get("_save")
    if save is None:
        failures.append("save-wiring: SettingsApp._save not found -- the save path was "
                        "renamed and this guard no longer guards anything")
        return
    check(_calls_to(save, "_restart_and_relaunch"),
          "save-wiring: _save never calls _restart_and_relaunch -- a save that does not "
          "restart defers every saved hotkey to the user's next manual start with "
          "nothing on screen saying so (#271, D-002)")
    # ... and every completed save REACHES it. The check above only asks whether the
    # call appears somewhere, which a save that returns before it would satisfy too --
    # the shape the retired keyless branch had (write, then done). What pins the reach
    # is the top level of _save: its statement sequence runs straight through to the
    # restart, and every abort sits inside a branch below it (the declined no-key
    # dialog, the declined hotkey warning, the write failure -- each one leaves the
    # window open on purpose and is no completed save). So the tail is the restart
    # call, which owns the window from there on (freeze the rail, wait the tool out,
    # relaunch, destroy), and nothing at that level returns before reaching it.
    tail = save.body[-1]
    tail_call = tail.value if isinstance(tail, ast.Expr) else None
    check(isinstance(tail_call, ast.Call)
          and getattr(tail_call.func, "attr", None) == "_restart_and_relaunch",
          "save-wiring: _save does not END in _restart_and_relaunch -- a statement "
          "standing after it, or in its place, is a completed save that skips the "
          "restart and defers the user's saved hotkeys to their next manual start "
          "(#271, D-002)")
    check(not [st for st in save.body if isinstance(st, ast.Return)],
          "save-wiring: _save returns from its top level -- that is a completed save "
          "walking past the restart, and the call further down would still look wired "
          "(#271, D-002). Aborts belong inside their branch, where the window stays "
          "open; the top-level sequence has to end in _restart_and_relaunch")
    # _save has no nested def, so an ast.walk over it is exact.
    destroys = [n for n in ast.walk(save)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "destroy"
                and isinstance(n.func.value, ast.Attribute)
                and n.func.value.attr == "root"]
    check(not destroys,
          "save-wiring: _save destroys the window itself -- the keyless close branch is "
          "back (#271). Every exit from a completed save belongs to "
          "_restart_and_relaunch; the early returns (a declined dialog, a write "
          "failure) leave the window open on purpose and destroy nothing")
    for name in ("update_rail", "_render_rail"):
        rail = methods.get(name)
        if rail is None:
            failures.append(f"save-wiring: SettingsApp.{name} not found -- the rail "
                            "renderer was renamed and this guard no longer guards it")
            continue
        labels = {n.value for n in ast.walk(rail)
                  if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        check("btn.save_restart" in labels,
              f"save-wiring: {name} does not name btn.save_restart -- the two rails "
              "carry the label literally, one each, so nothing but this pins them "
              "together (#271)")


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
        check_ui_language_gate(tmp)
        check_engine_pin(tmp)
        check_ptt_toggle(tmp)
        check_reset_defaults(tmp)
        check_save_preflight(tmp)
        check_nokey_unreadable(tmp)
        check_first_run_decision(tmp)
        check_regressions(tmp)
        leftovers = [x.name for x in tmp.iterdir() if x.name.endswith(".tmp")]
        check(not leftovers, f"atomic write left temp files behind: {leftovers}")
    check_hotkey_helpers()
    check_key_check()
    check_key_check_socket()
    check_key_check_malformed()
    check_key_check_strip()
    check_key_check_user_agent()
    check_verdict_coverage()
    check_string_keys()
    check_i18n()
    check_preselect()
    check_engine_keyed()
    check_engine_save_signal()
    check_fixed_entry_engine()
    check_ptt_read()
    check_ptt_save_signal()
    check_ptt_wiring()
    check_lang_writer_signature()
    check_mode_flip_wiring()
    check_save_always_restarts()
    check_reset_wiring()
    check_readfail_wiring()
    check_nokey_wiring()

    if SHOW:
        _show()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures[:60]:
            print("  " + f)
        return 1
    print("OK: all settings_io / key_check checks pass")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
