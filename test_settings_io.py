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
    empty-updates no-op, empty-value-omitted (a blank value is no instruction), and
    no temp file left behind (atomicity).
  - the `.env` round trip (#328, check_env_quoting): a value that needs quoting --
    a `#`, spaces, a leading quote -- comes back out of the REAL reader unchanged
    and leaves it without a warning, while an ordinary key still writes bare; the
    issue's own scene (a quoted value read, shown and saved back) holds; the values
    this grammar cannot carry are refused loudly, with the file untouched and the
    value itself nowhere in the message; a line-boundary character riding in on a
    paste is folded away before the speller can quote it into a line that falls
    apart (the scrub splits the reader's way, so CR/LF are not the whole list);
    and a sweep over every short string of the characters that can break a line
    holds the whole rule -- refused only with cause, or read back exactly and
    without a warning.
  - the `.env` deletion lane (#328, D-026, check_env_delete + check_env_save_updates):
    the REMOVE_ENV_KEY sentinel takes out every uncommented line of a key, the
    `export` form and all duplicates included, while a commented `# KEY=` note and
    every other byte stay; a removal over a missing file creates nothing; and an
    empty STRING still writes nothing and deletes nothing -- the two are held apart
    by identity. Plus the pure difference rule that derives the sentinel: a field
    that was filled and is now empty is an instruction, one that was never filled or
    is empty only because the reader failed is not.
  - settings_io.write_personal_settings / read_personal_settings: surgical merge
    (every unmanaged block + every _comment preserved), hotkeys written as a diff
    vs config.DEFAULT_HOTKEYS (default scheme -> no entries), and the three-valued
    defaults.api contract (#193/#198, D-008/D-002): `default_api=None` (the untouched
    engine field) leaves the file's value exactly as found -- a hand-written or even
    invalid pin included; a real id is written VERBATIM, the built-in default
    included (the two-mode fixed pin -- "always start with X"); and REMOVE_API_PIN
    force-drops the key, preserving siblings + _comment. Plus the absent-file minimal
    dict that must NOT contain the example's placeholder vocabulary (a real data
    bug), the whole-file warning (corrupt JSON / non-object / non-UTF-8 -- never a
    crash), and the D-026 hardening: the raw writer REFUSES such a file (ValueError)
    instead of skeletoning over it -- only the backup lane below may rewrite it.
  - settings_io.save_personal_settings, the D-026 backup lane (check_backup_lane):
    backup before loss, no backup -> no overwrite. A file the save cannot carry --
    corrupt JSON, non-object, undecodable bytes, or invalid entries in the owned
    surfaces -- is renamed to a timestamped personal_settings.backup-...json
    (collision -> -2/-3 suffix) with the old bytes byte-exact, then rewritten; a
    failed rename aborts the save with the target untouched; a vanished target
    saves on without a backup; a failed write after a successful backup rolls the
    backup back and re-raises. Invalid owned entries with a leave-as-found signal
    are normalized to the shown defaults (no pin / OFF / "en"), siblings and
    _comments preserved. The controls weigh as much: a healthy file, a
    default-equal hotkey entry, a deliberately dropped pin, a BOM and the D-024
    list collapse create NO backup, and the silent language toggle never enters
    the lane at all. Plus the #294 seed hardening: a cp1252 example file can no
    longer abort a save with a raw exception, on either writer.
  - settings_io.resolve_engine_save_signal (#198, D-008/D-028): the pure on-save
    derivation of the single `defaults.api` signal across the whole two-mode decision
    table, including that a flip into fixed mode pins whichever engine the list shows
    -- a keyless one included, since no key state reaches the decision. Key-agnosticism
    is pinned on the signature itself, and a sweep over every cell holds the shape of
    the answer (None / REMOVE_API_PIN / an engine id -- never a tuple again). Plus the
    three helpers D-028 retired (engine_keyed, preselect_startup_api and
    resolve_fixed_entry_engine), which must stay gone.
  - the window's "an unsaved edit has no effect" wiring (D-028), pinned on
    thoughtborne_settings.py's syntax tree -- the GUI is hands-on only, and a stale
    call in a Tk callback raises into the log rather than into any test: a field edit
    touches nothing but its own verdict, a mode switch never moves the selection, the
    renderer stays move-free, _save hands the writer the RESOLVED engine signal, no
    retired helper is called anywhere in the file, and the app writes no engine
    memory at all (it only reads it, for the remember-mode display).
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
    persist, gated. A target it cannot carry -- corrupt JSON, a non-object top
    level, undecodable bytes (one warning class since D-026) -- is left
    BYTE-identical and unwritten with NO backup created: a language click may
    neither skeleton over hand-written vocabulary / soniox_endpointing nor take
    the backup lane, which belongs to the deliberate actions alone. A healthy
    target still takes the surgical ui.language write with every other block as
    found, a missing one still takes the first-run skeleton lane (the gate keys
    on the warning, not on empty state), and an unREADABLE one (OSError) still
    raises for the caller's best-effort lane. Plus that
    writer's signature, which stays too narrow to write anything but ui.language --
    that the toggle really goes through it is driven on the real window in
    test_settings_visibility.py.
  - the data-safety regressions (check_regressions): a CRLF .env round-trips
    byte-faithfully (S5), duplicate managed-key lines are ALL rewritten (S3), a
    whitespace-only value is dropped and a pasted key stripped (S4), a UTF-8 BOM is
    tolerated on read and healed on write for both files (S6), a present-but-
    unreadable file aborts the save instead of clobbering it (B1, chmod-guarded, on
    the raw writer AND the backup lane -- no backup attempt over unreadable bytes),
    and the non-UTF-8 (ANSI/cp1252) lane (B3): read_env still degrades to {}
    without crashing, an ANSI .env still aborts write_env byte-unchanged, while an
    ANSI personal_settings.json now WARNS out of the reader, makes the raw writer
    refuse (ValueError), and rides the backup lane -- its intact vocabulary
    byte-exact in the backup instead of dead-ending in an abort (D-026).
  - the pure hotkey helpers: normalize_combo (the canonicalizer of #275, with its
    never-raise fallback for the diff path), validate_combo (including the #325
    dead-combo rejection of ctrl+pause / ctrl+scrolllock), decode_key_event on
    synthetic keycode events (#325: VK ints, the exact values Windows Tk puts in
    event.keycode), and the diff <-> apply_hotkey_overrides round-trip
    (exercising BOTH bare-F-key and modifier-chord combos, that an alias-spelled
    default diffs to nothing, and that a pre-D-024 file whose value is still a
    one-element list loads and saves back as that one combo).
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
    detection) alongside the retired hotkeys.more_suffix (D-024: one combo per
    action, so no "(+n more)" to show) and the five retired file-health keys
    (#326, D-026: the window says nothing about the file behind it). A broken table
    must REPORT, not raise (#313): the placeholder loop walks only the shared keys,
    the format pins and the t() probes name a retired key as a failure of their own,
    and a per-run proof plants both gap shapes in memory to hold check_i18n to
    returning with the message recorded.
  - the README anchors behind the settings links (#316): every url.* value that opens
    a README twin at a #anchor is held to a heading that really stands in that twin,
    since GitHub derives the anchor from the heading text -- a renamed or translated
    heading otherwise leaves the link opening a 300-line file at the top, silently.
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
    seam (a readable keyed .env -> plain, an ANSI .env -> wizard, the same way the
    window's own key fields come up empty over one).
  - the Machine Room reset (#282, D-020): the one forced write -- via the D-026
    backup lane since #263 -- that puts the four app-managed keys back to the
    shipped state: over a dirty-but-valid file (every hand-written block,
    `_comment` and parked `_` hotkey key preserved, order included, an unknown
    block among them, and NO backup -- valid values a reset changes are
    instruction, not loss), over the hand-typed invalid values no ordinary save
    can clear (the reason the values are forced rather than derived, asserted
    against the two save signals that return None there -- backup owed and
    byte-exact), over no file at all (the canonical all-defaults file, still
    without the example's placeholder vocabulary), twice in a row (idempotent
    bytes, no backup either time), and with a .env beside it that
    must not move. Plus the part of the call site the display lane in
    test_settings_visibility.py cannot see, statically: the two shipped values written
    as literals (its fixture cannot tell a forced one from a derived one), the
    backup lane called and the raw writer unreachable, no
    write_env, no engine-memory write, no window destroy, the confirmation's warning
    icon and preselected answer, and the restart as the tail.
  - the save pre-flight and its dialog (#291): settings_io.unreadable_save_target
    fires exactly where a writer would abort on its target and stays silent
    exactly where a write goes through -- each case asserted against the real
    writers on the same fixture, so the .env probe cannot drift from write_env's
    own guard read. For .env that is unreadable OR undecodable bytes; for
    personal_settings.json only unreadable ones since D-026 -- an ANSI or corrupt
    file is a save that goes through, over the backup lane. With the controls a
    naive fix breaks: an unreadable .env this save has no instruction for does not
    block it, while a pending key DELETION over one does (#328 -- a removal edits the
    found file like any write). Plus the two halves no display lane reaches,
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
values in decode_key_event, the keycode-is-the-VK contract behind it (#325 --
Windows Tk stores the WM_KEYDOWN wParam in event.keycode; off Windows the field
carries hardware codes, so only a real-Windows keypress proves it), and the live
"Test key" round-trip against real keys.
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
import types
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


def check_env_quoting(tmp):
    """write_env's spelling of a value against the parser that reads it back (#328).

    The bug this closes: the writer wrote every value bare while the shared reader
    understands quotes and comment tails, so a value carrying a `#` came back cut --
    silently, on the NEXT read, which is the worst shape a key loss can have. The
    proof is a round trip through the real reader rather than an assertion about the
    line's text: config.format_env_value claims to be that parser's inverse, and only
    the parser can confirm it. The warning count matters as much -- a line this
    writer produced must never make the tool warn at startup about its own file --
    and the sweep at the end holds that half as a rule rather than as examples, which
    is what the hand-picked corpus missed for two leading-quote values.

    Values are invented (`dummy-`, `gsk_`), never a real key. The normal case is
    pinned too: an ordinary key still writes bare, so nobody's `.env` grows quotes it
    never had.
    """
    hard = ["du # my", "#lead", "dummy#a", "has space", "tab\there", "two  spaces",
            'du"my', "du'my", "d\"u'y", "'lead", '"lead', '"abc', "${OTHER}"]
    for i, value in enumerate(hard):
        p = tmp / f"quote{i}.env"
        p.write_text("GROQ_API_KEY=placeholder\n", encoding="utf-8")
        sio.write_env(p, {"GROQ_API_KEY": value})
        line = p.read_text(encoding="utf-8")
        values, warnings = config.read_env_file(p)
        check(values.get("GROQ_API_KEY") == value,
              f".env round trip lost a value: wrote {value!r}, read back "
              f"{values.get('GROQ_API_KEY')!r} from the line {line!r}")
        check(not warnings,
              f".env round trip: the line this writer produced for {value!r} makes "
              f"the reader warn at startup: {warnings}")
        check(sio.read_env(p) == values,
              f".env round trip: the settings app reads {sio.read_env(p)} where the "
              f"tool reads {values} -- the two halves disagree about {line!r}")

    # A line-boundary character that rode in on a paste has to be folded away BEFORE
    # the speller sees it, and the fold has to know every character the reader splits
    # on -- `str.splitlines()` breaks at a vertical tab, a form feed, a NEL and more,
    # not just at CR/LF. Scrubbing only CR/LF left the rest to the speller, which
    # quotes them as whitespace, so the value went to disk in a line the reader then
    # tore in two: a truncated key plus a startup warning over the writer's own file
    # (#328). Each of these must come back as one clean line, silently.
    for i, pasted in enumerate(["dum\x0bmy", "dum\x0cmy", "dum\x1cmy", "dum\x85my",
                                "dum\u2028my", "dum\r\nmy"]):
        p = tmp / f"quote_fold{i}.env"
        p.write_text("GROQ_API_KEY=placeholder\n", encoding="utf-8")
        sio.write_env(p, {"GROQ_API_KEY": pasted})
        line = p.read_text(encoding="utf-8")
        values, warnings = config.read_env_file(p)
        check(values.get("GROQ_API_KEY") == "dummy" and not warnings,
              f".env: a value carrying {pasted!r} was written as {line!r} and reads "
              f"back as {values.get('GROQ_API_KEY')!r} with warnings {warnings} -- "
              f"the scrub has to fold it into a single clean line")
        check(line == "GROQ_API_KEY=dummy\n",
              f".env: the line written for {pasted!r} is {line!r}")

    # The counter-case, so the fold stays the reader's split and does not grow into
    # "drop every whitespace character": a unit separator is whitespace to the speller
    # but no line boundary, so it belongs to the value and the quoted line carries it.
    p = tmp / "quote_fold_keep.env"
    p.write_text("GROQ_API_KEY=placeholder\n", encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": "dum\x1fmy"})
    values, warnings = config.read_env_file(p)
    check(values.get("GROQ_API_KEY") == "dum\x1fmy" and not warnings,
          f".env: an inner non-breaking control character was dropped from the value: "
          f"{values.get('GROQ_API_KEY')!r} with warnings {warnings}")

    # An ordinary key stays bare, byte for byte: quoting is for the values that need
    # it, not a new house style for every line.
    p = tmp / "quote_plain.env"
    p.write_text("GROQ_API_KEY=old\n", encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": "gsk_plain"})
    check(p.read_text(encoding="utf-8") == "GROQ_API_KEY=gsk_plain\n",
          f".env: an ordinary key no longer writes bare: "
          f"{p.read_text(encoding='utf-8')!r}")

    # The issue's own scene: a quoted value already in the file, read into the window
    # and saved back unchanged by a save that has something to write.
    p = tmp / "quote_existing.env"
    p.write_text('GROQ_API_KEY="du # my"\nSONIOX_API_KEY=sx_old\n', encoding="utf-8")
    loaded = sio.read_env(p)
    check(loaded.get("GROQ_API_KEY") == "du # my",
          f".env quoting fixture: the reader no longer sees the quoted value: {loaded}")
    sio.write_env(p, loaded)
    check(sio.read_env(p) == loaded,
          f".env read -> save -> read is lossy for a quoted value: {sio.read_env(p)} "
          f"instead of {loaded} (line: {p.read_text(encoding='utf-8')!r})")

    # The values this grammar cannot carry: they hold BOTH quote characters, so no
    # pair is free to enclose them, and a leading quote or a comment `#` rules bare
    # out too. A loud refusal, with the file untouched and the value itself nowhere
    # in the message (it is an API key field). The two leading-quote forms are the
    # subtle ones: bare DOES give them back whole, but the reader then warns about an
    # unterminated quote at every start -- over a line this writer had produced.
    for i, (bad, fragment) in enumerate([("'a \"b #c'", "b #c"),
                                         ("\"a'b", "a'b"),
                                         ("'a\"b", 'a"b')]):
        p = tmp / f"quote_bad{i}.env"
        original = "GROQ_API_KEY=keepme\n"
        p.write_text(original, encoding="utf-8")
        raised = None
        try:
            sio.write_env(p, {"GROQ_API_KEY": bad})
        except ValueError as e:
            raised = e
        check(raised is not None,
              f"write_env accepted {bad!r}, a value no .env spelling carries -- the "
              f"silent truncation or the startup warning this change exists to end")
        check(p.read_text(encoding="utf-8") == original,
              f".env: the refused write of {bad!r} touched the file anyway")
        check(raised is None
              or (bad not in str(raised) and fragment not in str(raised)),
              f"the refusal echoes the value into a dialog and the log: {raised}")

    # The rule behind those examples, swept rather than sampled: over every string up
    # to three characters long built from the ones that can break a `.env` line (a
    # letter, both quotes, a `#`, a space), a value is either refused or comes back
    # out of the REAL reader exactly AND without a warning. The second half is what
    # the corpus above missed for two values: bare carried "a'b and 'a"b faithfully,
    # and the tool still warned at every start over a line this writer had produced.
    # The counter-rule keeps the refusal honest in the other direction, and it asks
    # for the WHOLE justification rather than half of it: a value may be turned away
    # only when no quote pair is free to enclose it AND bare is out too (it opens
    # with a quote, or carries a `#` where the comment rule cuts). A future speller
    # that refused more than that would be refusing values it could have carried,
    # and the old "holds both quote characters" half would have waved it through.
    alphabet = "a\"'# "
    sweep = [a for a in alphabet]
    sweep += [a + b for a in alphabet for b in alphabet]
    sweep += [a + b + c for a in alphabet for b in alphabet for c in alphabet]
    p = tmp / "quote_sweep.env"
    for value in sweep:
        if value.strip() != value:
            continue        # write_env strips; such a value never reaches the speller
        try:
            spelled = config.format_env_value(value)
        except ValueError:
            no_free_pair = '"' in value and "'" in value
            bare_is_out = value[:1] in ("'", '"') or re.search(r"(^|\s)#", value)
            check(no_free_pair and bare_is_out,
                  f"format_env_value refused {value!r}, which one of its spellings "
                  f"carries (free quote pair: {not no_free_pair}, bare would do: "
                  f"{not bare_is_out})")
            continue
        p.write_text(f"GROQ_API_KEY={spelled}\n", encoding="utf-8")
        values, warnings = config.read_env_file(p)
        check(values.get("GROQ_API_KEY") == value and not warnings,
              f".env: the spelling {spelled!r} of {value!r} reads back as "
              f"{values.get('GROQ_API_KEY')!r} with warnings {warnings}")


def check_env_delete(tmp):
    """write_env's deletion lane: the REMOVE_ENV_KEY sentinel takes a key's line out
    of the file (#328, D-026's WYSIWYG deletion), and nothing else moves.

    Deletion is the one operation that cannot be undone from the window, so the
    surface is pinned close: ALL uncommented occurrences go (the reader is last-wins,
    so one survivor would revive the value), an `export` form counts, a commented
    `# KEY=...` note is the user's and stays, the rest of the file is byte-exact, and
    a delete over a missing file creates nothing -- not even the example seed.

    The control weighs as much as the deletions: an empty STRING still writes nothing
    and clobbers nothing. That guard did not move with D-026; what changed is that
    the app no longer sends an empty string where it means "remove", it sends the
    sentinel (resolve_env_save_updates), and the two are held apart by identity."""
    R = sio.REMOVE_ENV_KEY

    # 1. the line goes, every other line / comment / blank byte-exact
    p = tmp / "del1.env"
    original = ("# header comment\n"
                "FOO=bar\n"
                "\n"
                "GROQ_API_KEY=old_groq\n"
                "# a comment\n"
                "SONIOX_API_KEY=old_soniox\n"
                "UNRELATED=keepme\n")
    p.write_text(original, encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": R})
    check(p.read_text(encoding="utf-8") == original.replace("GROQ_API_KEY=old_groq\n", ""),
          f".env delete is not byte-preserving elsewhere: "
          f"{p.read_text(encoding='utf-8')!r}")
    check(sio.read_env(p) == {"SONIOX_API_KEY": "old_soniox"},
          f".env delete: the key is still readable afterwards: {sio.read_env(p)}")

    # 2. every duplicate, the export form included, and the CRLF endings of the
    # surviving lines untouched -- while the commented note stays.
    p = tmp / "del2.env"
    original2 = ("KEEP=1\r\n"
                 "GROQ_API_KEY=first\r\n"
                 "  export GROQ_API_KEY=second\r\n"
                 "#GROQ_API_KEY=my own note\r\n"
                 "SONIOX_API_KEY=s\r\n")
    p.write_bytes(original2.encode("utf-8"))
    sio.write_env(p, {"GROQ_API_KEY": R})
    check(p.read_bytes() == b"KEEP=1\r\n#GROQ_API_KEY=my own note\r\nSONIOX_API_KEY=s\r\n",
          f".env delete: duplicates / export form / CRLF / comment wrong: "
          f"{p.read_bytes()!r}")
    check(sio.read_env(p) == {"SONIOX_API_KEY": "s"},
          f".env delete: a duplicate line revived the value: {sio.read_env(p)}")

    # 3. deleting a key the file does not assign changes no byte
    p = tmp / "del3.env"
    original3 = "# only soniox here\nSONIOX_API_KEY=s\n"
    p.write_text(original3, encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": R})
    check(p.read_text(encoding="utf-8") == original3,
          f".env delete of an absent key changed the file: "
          f"{p.read_text(encoding='utf-8')!r}")

    # 4. deletions only, no file: nothing to delete, and nothing may be CREATED for
    # the sake of it -- least of all the example seed.
    p = tmp / "del4.env"
    sio.write_env(p, {"GROQ_API_KEY": R, "SONIOX_API_KEY": R}, example_path=EXAMPLE_ENV)
    check(not p.exists(),
          ".env delete over a missing file created one -- a removal must never seed")

    # 5. a delete beside a write over a missing file: the seed happens for the write,
    # and the seed's own placeholder line for the deleted key goes with it. (The
    # app's difference rule never produces this pair -- an empty field over an empty
    # load is no instruction -- so this pins the writer's rule, not a user's path.)
    p = tmp / "del5.env"
    sio.write_env(p, {"GROQ_API_KEY": R, "SONIOX_API_KEY": "xyz"},
                  example_path=EXAMPLE_ENV)
    got = p.read_text(encoding="utf-8")
    check(p.exists() and "Groq API Key" in got,
          f".env delete+write over a missing file lost the seeded header: {got!r}")
    check("GROQ_API_KEY=" not in got,
          f".env delete+write: the deleted key's seeded line survived: {got!r}")
    check(sio.read_env(p) == {"SONIOX_API_KEY": "xyz"},
          f".env delete+write read wrong: {sio.read_env(p)}")

    # 6. the two spellings on one fixture: an empty string is not an instruction and
    # never clobbers, the sentinel removes. Only identity tells them apart.
    p = tmp / "del6.env"
    original6 = "GROQ_API_KEY=keepme\n"
    p.write_text(original6, encoding="utf-8")
    sio.write_env(p, {"GROQ_API_KEY": ""})
    check(p.read_text(encoding="utf-8") == original6,
          ".env: an empty string deleted a key -- only the sentinel may")
    sio.write_env(p, {"GROQ_API_KEY": R})
    check(p.read_text(encoding="utf-8") == "",
          f".env: the sentinel did not remove the line: "
          f"{p.read_text(encoding='utf-8')!r}")
    check(p.exists(), ".env: the delete removed the file instead of the line")
    # idempotent: saving the same pending delete again is a no-op
    sio.write_env(p, {"GROQ_API_KEY": R})
    check(p.read_text(encoding="utf-8") == "", ".env: a repeated delete is not a no-op")


def check_env_save_updates():
    """settings_io.resolve_env_save_updates: what a save has to say about `.env`,
    derived as the DIFFERENCE between the live fields and the state the window was
    loaded and shown with (#328, D-026).

    The difference is the whole point, and the reason the rule is not the literal
    "an empty field means delete": `read_env` degrades an unreadable or ANSI `.env`
    to {}, so both fields come up empty without anyone clearing anything, and a
    literal reading would take a briefly locked file as an order to wipe both keys.
    Three cases have to stay apart, and every row below is one of them -- a field
    that was filled and is now empty (an instruction), a field that was never filled
    (nothing), and a field that is empty only because the reader failed (nothing).

    Pure, so the whole table runs off Windows. The seam to the real window -- that
    the app builds this set from its load-time snapshot and hands the same one to
    both the pre-flight and the writer -- is pinned in check_readfail_wiring and
    driven for real in test_settings_visibility.test_env_delete_with_display."""
    R = sio.REMOVE_ENV_KEY
    G, S = "GROQ_API_KEY", "SONIOX_API_KEY"

    def live(g="", s=""):
        return {G: g, S: s}

    cases = [
        # label, live fields, loaded snapshot, expected update set
        ("wizard, nothing filled in", live(), {}, {}),
        ("a key typed into an empty field", live(g="gsk_new"), {}, {G: "gsk_new"}),
        ("both fields untouched", live(g="gsk_a", s="sx_b"), {G: "gsk_a", S: "sx_b"}, {}),
        ("one key rotated", live(g="gsk_new", s="sx_b"),
         {G: "gsk_a", S: "sx_b"}, {G: "gsk_new"}),
        ("one field cleared", live(s="sx_b"), {G: "gsk_a", S: "sx_b"}, {G: R}),
        ("cleared with spaces left behind", live(g="   ", s="sx_b"),
         {G: "gsk_a", S: "sx_b"}, {G: R}),
        ("retyped with padding, same key", live(g=" gsk_a ", s="sx_b"),
         {G: "gsk_a", S: "sx_b"}, {}),
        ("both fields cleared", live(), {G: "gsk_a", S: "sx_b"}, {G: R, S: R}),
        ("empty because the .env could not be read", live(), {}, {}),
    ]
    for label, live_fields, loaded, expected in cases:
        got = sio.resolve_env_save_updates(live_fields, loaded)
        check(got == expected,
              f"env save updates ({label}): resolved {got}, expected {expected}")

    # A key the app does not manage cannot ride in through the live fields, and the
    # managed key missing from them entirely is no instruction either.
    got = sio.resolve_env_save_updates({G: "gsk_a", "OTHER": "x"}, {})
    check(got == {G: "gsk_a"},
          f"env save updates: an unmanaged key survived the resolver: {got}")

    # The docstring's defensive half, pinned: a managed key MISSING from the live
    # fields reads as a cleared field, exactly like one cleared by hand -- so a
    # caller that builds the dict from only the widgets it touched would delete the
    # other key. That is why the app's _live_env always builds both.
    got = sio.resolve_env_save_updates({G: "dummy-x"},
                                       {G: "dummy-y", S: "dummy-s"})
    check(got == {G: "dummy-x", S: R},
          f"env save updates: a managed key absent from the live fields resolved to "
          f"{got}, not to the deletion its absence claims to mean")
    check(got.get(S) is sio.REMOVE_ENV_KEY,
          f"env save updates: the absent key resolved to {got.get(S)!r} rather than "
          f"the REMOVE_ENV_KEY sentinel itself")

    # The removal signal is the sentinel ITSELF -- write_env matches it by identity,
    # so a value that merely compares equal to something must not do.
    got = sio.resolve_env_save_updates(live(), {G: "gsk_a"})
    check(got.get(G) is sio.REMOVE_ENV_KEY,
          f"env save updates: a cleared field resolved to {got.get(G)!r} rather than "
          f"the REMOVE_ENV_KEY sentinel itself")


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

    # D -- corrupt file -> warning (not a crash). The raw writer REFUSES it (the
    # D-026 hardening: no overwrite path over a broken file but the backup lane),
    # and the backup lane rewrites it cleanly with the old bytes in the backup.
    p = tmp / "ps_bad.json"
    corrupt_bytes = "{ this is : not valid json ".encode("utf-8")
    p.write_bytes(corrupt_bytes)
    data, warn = sio.read_personal_settings(p)
    check(data == {} and isinstance(warn, str) and warn, "D: corrupt file did not warn")
    raised = False
    try:
        sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                    default_api=config.BUILTIN_DEFAULT_API,
                                    example_path=EXAMPLE_PS)
    except ValueError:
        raised = True
    check(raised and p.read_bytes() == corrupt_bytes,
          "D: the raw writer overwrote a corrupt file -- since D-026 only the "
          "backup lane may rewrite one")
    bak, losses = sio.save_personal_settings(
        p, hotkeys_effective=sio.preset_ctrl_alt(),
        default_api=config.BUILTIN_DEFAULT_API, example_path=EXAMPLE_PS)
    check(bak is not None and bak.exists() and bak.read_bytes() == corrupt_bytes,
          f"D: the backup lane did not park the corrupt bytes byte-exact: {bak}")
    check(losses and "personal_settings.json" in losses[0],
          f"D: the whole-file loss was not returned for the caller's log: {losses}")
    _, warn2 = sio.read_personal_settings(p)
    check(warn2 is None, "D: the backup-lane rewrite did not produce valid JSON")
    check("Project Name" not in p.read_text(encoding="utf-8"),
          "D: overwrite leaked the placeholder vocabulary")
    bak.unlink()   # keep the tempdir glob-clean for the later backup asserts

    # E -- a pre-#318 file: the F-key preset used to write a one-element LIST for
    # cancel_recording. It must load as the identical binding, silently (D-024),
    # and the next save must write it back in string shape.
    p = tmp / "ps_e.json"
    p.write_text(json.dumps({"hotkeys": {"cancel_recording": ["ctrl+f9"],
                                         "start_recording": "f9"}}),
                 encoding="utf-8")
    data, warn = sio.read_personal_settings(p)
    check(warn is None, "E: legacy file did not load")
    eff, warns = config.apply_hotkey_overrides(config.DEFAULT_HOTKEYS, data["hotkeys"])
    check(eff["cancel_recording"] == "ctrl+f9" and not warns,
          f"E: legacy one-element list did not collapse silently (warns={warns})")
    bak, losses = sio.save_personal_settings(p, hotkeys_effective=eff,
                                             default_api=None,
                                             example_path=EXAMPLE_PS)
    check(bak is None and losses == [],
          f"E: the D-024 one-element-list collapse counted as a loss -- shape "
          f"normalization is meaning-preserving and owes no backup: {losses}")
    data2, _ = sio.read_personal_settings(p)
    check(data2["hotkeys"].get("cancel_recording") == "ctrl+f9",
          f"E: the save kept a list shape: {data2['hotkeys'].get('cancel_recording')!r}")
    eff2, warns2 = config.apply_hotkey_overrides(config.DEFAULT_HOTKEYS, data2["hotkeys"])
    check(eff2 == eff and not warns2, "E: legacy round-trip changed the effective binding")

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
        # ...and so must the D-026 backup lane: unreadable bytes leave nothing to
        # decide, so the OSError propagates BEFORE any backup attempt.
        raised_save = False
        try:
            sio.save_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                       default_api=config.BUILTIN_DEFAULT_API,
                                       example_path=EXAMPLE_PS)
        except OSError:
            raised_save = True
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
        check(raised_read, "B1: read_personal_settings on an unreadable file did not raise")
        check(raised_write, "B1: write_personal_settings over an unreadable file did not raise")
        check(raised_save, "B1: save_personal_settings over an unreadable file did not raise")
        check(not list(tmp.glob("ps_locked.backup-*.json")),
              "B1: save_personal_settings attempted a backup of unreadable bytes")
        check(p.read_text(encoding="utf-8") == orig,
              "B1: write_personal_settings clobbered an unreadable file")

    # B3 -- a non-UTF-8 (ANSI/cp1252) config file must not crash the readers. An
    # undecodable personal_settings holds INTACT recoverable data (German vocabulary
    # in the wrong encoding): since D-026 that is one whole-file loss class with
    # corrupt JSON -- warn, refuse the raw writer, back up + rewrite on the
    # deliberate lane -- while an undecodable .env stays B1's abort.
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

    # (b) the raw writer still REFUSES an ANSI file byte-unchanged -- as the D-026
    # ValueError hardening now, not a UnicodeDecodeError -- while the backup lane
    # carries it: the save GOES THROUGH with the intact vocabulary byte-exact in
    # the backup, which is the whole point of D-026's reversal of the old abort.
    p = tmp / "ps_ansi.json"
    ansi_bytes = '{\n  "vocabulary": {"terms": ["Grüße", "Präfix"]}\n}\n'.encode("cp1252")
    p.write_bytes(ansi_bytes)
    raised_w = False
    try:
        sio.write_personal_settings(p, hotkeys_effective=sio.preset_ctrl_alt(),
                                    default_api=config.BUILTIN_DEFAULT_API,
                                    example_path=EXAMPLE_PS)
    except ValueError:   # covers the UnicodeDecodeError subclass too
        raised_w = True
    check(raised_w, "B3: write_personal_settings over an ANSI file did not refuse")
    check(p.read_bytes() == ansi_bytes,
          "B3: write_personal_settings clobbered an ANSI file (destroyed vocabulary)")
    bak, losses = sio.save_personal_settings(
        p, hotkeys_effective=sio.preset_ctrl_alt(),
        default_api=config.BUILTIN_DEFAULT_API, example_path=EXAMPLE_PS)
    check(bak is not None and bak.read_bytes() == ansi_bytes,
          "B3: save_personal_settings did not park the ANSI bytes byte-exact -- "
          "the intact vocabulary is exactly what the backup exists for (D-026)")
    check(sio.read_personal_settings(p)[1] is None,
          "B3: the backup-lane rewrite over an ANSI file is not valid JSON")
    bak.unlink()

    # (c) read_personal_settings on an ANSI file WARNS -- one whole-file loss
    # class with corrupt JSON since D-026, no longer a raise. The warning must
    # name the encoding: it is what the backup log line carries.
    p2 = tmp / "ps_ansi_read.json"
    p2.write_bytes(ansi_bytes)
    data_r, warn_r = sio.read_personal_settings(p2)
    check(data_r == {} and isinstance(warn_r, str) and "UTF-8" in warn_r,
          f"B3: read_personal_settings on an ANSI file should warn naming the "
          f"encoding, got ({data_r!r}, {warn_r!r})")


# ---- the D-026 backup lane (#263) --------------------------------------------
def check_backup_lane(tmp):
    """settings_io.save_personal_settings, D-026's "backup before loss -- no
    backup, no overwrite" write lane, completely off-Windows. Every fixture lives
    in its own subdirectory so the backup globs cannot see each other.

    The mechanics: a corrupt file is renamed to its timestamped backup byte-exact
    and rewritten to the managed state, the return carries (Path, losses) for the
    caller's log; a name collision (timestamp frozen via a patched sio.time) walks
    -2/-3; a failed rename (os.rename -> PermissionError) aborts the save with
    the target byte-unchanged and NOTHING else in the directory; the vanished
    race (os.rename -> FileNotFoundError) saves on and returns (None, losses);
    a failed atomic write AFTER a successful backup rolls the backup back and
    re-raises, leaving the target at its place and no backup behind.

    Normalize-on-save: each invalid owned entry with a leave-as-found signal --
    an unknown engine pin, a quoted `enabled`, an unknown ui.language, a
    non-dict block of any of the four surfaces, and the hotkey loss classes
    (unknown action, unparseable combo, collision, multi-element list) -- lands
    at the shown default with siblings and `_comment` preserved, backup owed and
    byte-exact each time.

    The controls carry D-026's no-backup clause: a healthy file, a default-equal
    hotkey entry falling out of the diff, a valid pin deliberately dropped via
    REMOVE_API_PIN, and a BOM heal create NO backup; an EMPTY file is corrupt
    like any other and backs up its zero bytes (uniform, deliberately no special
    case). The #294 seeds: a cp1252 example file aborts neither writer. And the
    silent lane never enters: write_ui_language over a corrupt file returns
    False, byte-still, with no backup anywhere."""
    corrupt = b'{\n  "vocabulary": { "terms": ["Gr\xc3\xbc\xc3\x9fe"],\n'

    # 1 -- corrupt -> backup + rewrite.
    d = tmp / "bl1"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_bytes(corrupt)
    bak, losses = sio.save_personal_settings(
        ps, hotkeys_effective=sio.preset_fkeys(), default_api="groq",
        example_path=EXAMPLE_PS)
    check(bak is not None and bak.parent == d
          and re.fullmatch(r"personal_settings\.backup-\d{4}-\d{2}-\d{2}_\d{6}\.json",
                           bak.name) is not None,
          f"BL1: the backup name is not the D-026 shape: {bak}")
    check(bak is not None and bak.read_bytes() == corrupt,
          "BL1: the old bytes are not byte-exact in the backup")
    check(len(losses) == 1 and "not valid JSON" in losses[0],
          f"BL1: the whole-file loss was not returned: {losses}")
    data, warn = sio.read_personal_settings(ps)
    check(warn is None and data.get("hotkeys", {}).get("start_recording") == "f9"
          and data.get("defaults", {}).get("api") == "groq",
          f"BL1: the rewrite is not the managed state: {data}")

    # 2 -- collision: timestamp frozen, two names taken -> -3. sio.time is
    # replaced by a shim (not a strftime patch on the real module, which every
    # other module shares) and restored in finally.
    d = tmp / "bl2"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_bytes(corrupt)
    taken = d / "personal_settings.backup-2026-09-19_120000.json"
    taken.write_bytes(b"taken")
    taken2 = d / "personal_settings.backup-2026-09-19_120000-2.json"
    taken2.write_bytes(b"taken2")
    real_time = sio.time
    sio.time = types.SimpleNamespace(strftime=lambda fmt: "2026-09-19_120000")
    try:
        bak, _ = sio.save_personal_settings(
            ps, hotkeys_effective=sio.preset_ctrl_alt(), default_api=None,
            example_path=EXAMPLE_PS)
    finally:
        sio.time = real_time
    check(bak is not None
          and bak.name == "personal_settings.backup-2026-09-19_120000-3.json"
          and bak.read_bytes() == corrupt,
          f"BL2: the collision suffix did not walk to -3: {bak}")
    check(taken.read_bytes() == b"taken" and taken2.read_bytes() == b"taken2",
          "BL2: an existing backup was clobbered -- backups are never overwritten")

    # 3 -- a failed rename aborts the save: no backup, no overwrite (D-026's
    # core clause). os.rename is the only rename in the lane (_atomic_write uses
    # os.replace, tempfile no rename), so the patch is narrow; restored in finally.
    d = tmp / "bl3"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_bytes(corrupt)

    def _deny(*a, **k):
        raise PermissionError("locked by another process (test)")

    real_rename = os.rename
    os.rename = _deny
    try:
        raised = False
        try:
            sio.save_personal_settings(ps, hotkeys_effective=sio.preset_ctrl_alt(),
                                       default_api=None, example_path=EXAMPLE_PS)
        except PermissionError:
            raised = True
    finally:
        os.rename = real_rename
    check(raised, "BL3: a failed backup rename did not abort the save")
    check(ps.read_bytes() == corrupt
          and sorted(x.name for x in d.iterdir()) == ["personal_settings.json"],
          f"BL3: 'no backup, no overwrite' violated -- directory holds "
          f"{sorted(x.name for x in d.iterdir())}")

    # 4 -- the vanished race: what was deleted externally no rename can save, so
    # the save goes through without a backup and says so in the return.
    d = tmp / "bl4"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_bytes(corrupt)

    def _vanish(*a, **k):
        raise FileNotFoundError("vanished between probe and rename (test)")

    real_rename = os.rename
    os.rename = _vanish
    try:
        bak, losses = sio.save_personal_settings(
            ps, hotkeys_effective=sio.preset_ctrl_alt(), default_api=None,
            example_path=EXAMPLE_PS)
    finally:
        os.rename = real_rename
    check(bak is None and losses,
          f"BL4: the vanished race must save on and return (None, losses): "
          f"({bak}, {losses})")
    check(sio.read_personal_settings(ps)[1] is None
          and not list(d.glob("personal_settings.backup-*")),
          "BL4: the vanished race left no clean file, or a phantom backup")

    # 5 -- a failed write AFTER a successful backup rolls the backup back: the
    # target must not be missing from its place after an aborted save.
    d = tmp / "bl5"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_bytes(corrupt)

    def _diskfull(path, content):
        raise OSError("no space left on device (test)")

    real_aw = sio._atomic_write
    sio._atomic_write = _diskfull
    try:
        raised = False
        try:
            sio.save_personal_settings(ps, hotkeys_effective=sio.preset_ctrl_alt(),
                                       default_api=None, example_path=EXAMPLE_PS)
        except OSError:
            raised = True
    finally:
        sio._atomic_write = real_aw
    check(raised, "BL5: the failed write was swallowed")
    check(ps.exists() and ps.read_bytes() == corrupt,
          "BL5: the rollback did not put the target back in place -- the savefail "
          "dialog's 'everything is still there' would be false of the file")
    check(sorted(x.name for x in d.iterdir()) == ["personal_settings.json"],
          f"BL5: the rollback left a backup behind: "
          f"{sorted(x.name for x in d.iterdir())}")

    # 6 -- healthy -> no backup, content carried (D-026's no-backup clause).
    d = tmp / "bl6"; d.mkdir()
    ps = d / "personal_settings.json"
    healthy = {"vocabulary": {"terms": ["keepme"]}, "defaults": {"api": "groq"}}
    ps.write_text(json.dumps(healthy, indent=2, ensure_ascii=False) + "\n",
                  encoding="utf-8")
    bak, losses = sio.save_personal_settings(
        ps, hotkeys_effective=sio.preset_ctrl_alt(), default_api=None,
        example_path=EXAMPLE_PS)
    check(bak is None and losses == []
          and sorted(x.name for x in d.iterdir()) == ["personal_settings.json"],
          f"BL6: a save over a healthy file owed a backup: ({bak}, {losses})")
    data, _ = sio.read_personal_settings(ps)
    check(data.get("vocabulary") == healthy["vocabulary"]
          and data.get("defaults", {}).get("api") == "groq",
          f"BL6: the healthy save did not leave content as found: {data}")

    # 7 -- normalize-on-save: invalid owned entries with a leave-as-found signal
    # land at the shown defaults, siblings + _comment preserved, backup owed and
    # byte-exact each time. Losses are checked by substring: the very loader
    # warning the tool would log is what the backup log line carries.
    def norm(name, content, expect_loss, verify, **overrides):
        nd = tmp / name
        nd.mkdir()
        nps = nd / "personal_settings.json"
        raw = (json.dumps(content, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        nps.write_bytes(raw)
        kwargs = dict(hotkeys_effective=sio.preset_ctrl_alt(), default_api=None,
                      example_path=EXAMPLE_PS)
        kwargs.update(overrides)
        nbak, nlosses = sio.save_personal_settings(nps, **kwargs)
        check(nbak is not None and nbak.read_bytes() == raw,
              f"{name}: backup missing or not byte-exact: {nbak}")
        check(any(expect_loss in l for l in nlosses),
              f"{name}: expected a loss naming {expect_loss!r}, got {nlosses}")
        ndata, nwarn = sio.read_personal_settings(nps)
        check(nwarn is None, f"{name}: the rewrite is not valid JSON")
        verify(ndata)

    norm("bl7_pin", {"defaults": {"_comment": "keep", "api": "grok", "note": 1},
                     "vocabulary": {"terms": ["keepme"]}},
         "unknown engine",
         lambda data: check(
             "api" not in data.get("defaults", {})
             and data.get("defaults", {}).get("_comment") == "keep"
             and data.get("defaults", {}).get("note") == 1
             and data.get("vocabulary", {}).get("terms") == ["keepme"],
             f"bl7_pin: the invalid pin was not normalized away with siblings "
             f"kept: {data}"))
    norm("bl7_ptt", {"push_to_talk": {"_comment": "keep", "enabled": "yes",
                                      "trigger": "rctrl", "tap_window_s": 0.5}},
         "JSON boolean",
         lambda data: check(
             data.get("push_to_talk", {}).get("enabled") is False
             and data.get("push_to_talk", {}).get("_comment") == "keep"
             and data.get("push_to_talk", {}).get("trigger") == "rctrl"
             and data.get("push_to_talk", {}).get("tap_window_s") == 0.5,
             f"bl7_ptt: the quoted enabled was not normalized to the shown OFF "
             f"with siblings kept: {data}"))
    norm("bl7_lang", {"ui": {"_comment": "keep", "language": "fr"}},
         "ui.language",
         lambda data: check(
             data.get("ui", {}).get("language") == "en"
             and data.get("ui", {}).get("_comment") == "keep",
             f"bl7_lang: the unknown language was not normalized to the shown "
             f"English: {data}"))
    norm("bl7_defjunk", {"defaults": "junk"}, "not a JSON object",
         lambda data: check("defaults" not in data,
                            f"bl7_defjunk: the non-dict defaults survived: {data}"))
    norm("bl7_uijunk", {"ui": ["x"]}, "not a JSON object",
         lambda data: check(data.get("ui", {}).get("language") == "en",
                            f"bl7_uijunk: the non-dict ui block was not replaced "
                            f"by the shown English: {data}"))
    norm("bl7_pttjunk", {"push_to_talk": 5}, "not a JSON object",
         lambda data: check(data.get("push_to_talk", {}).get("enabled") is False,
                            f"bl7_pttjunk: the non-dict push_to_talk block was "
                            f"not replaced by the shown OFF: {data}"))
    norm("bl7_hkjunk", {"hotkeys": "x"}, "not a JSON object",
         lambda data: check("hotkeys" not in data,
                            f"bl7_hkjunk: the non-dict hotkeys block survived: "
                            f"{data}"))
    norm("bl7_action", {"hotkeys": {"strat_recording": "ctrl+alt+w"}},
         "unknown action",
         lambda data: check("hotkeys" not in data,
                            f"bl7_action: the unknown action survived: {data}"))
    norm("bl7_combo", {"hotkeys": {"start_recording": "ctrl+alt"}},
         "not a valid combo",
         lambda data: check("hotkeys" not in data,
                            f"bl7_combo: the unparseable combo survived: {data}"))
    norm("bl7_clash", {"hotkeys": {"start_recording": "ctrl+alt+4"}},
         "collides",
         lambda data: check("hotkeys" not in data,
                            f"bl7_clash: the collision loser survived: {data}"))
    norm("bl7_list", {"hotkeys": {"cancel_recording": ["ctrl+f9", "f9"]}},
         "exactly one combo",
         lambda data: check("hotkeys" not in data,
                            f"bl7_list: the multi-element list survived: {data}"))

    # 8 -- the no-backup controls. (a) a valid entry equal to its default falls
    # out of the diff -- meaning preserved, no backup owed.
    d = tmp / "bl8a"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_text(json.dumps({"hotkeys": {"start_recording":
                                          config.DEFAULT_HOTKEYS["start_recording"]},
                              "vocabulary": {"terms": ["keepme"]}},
                             indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    eff = config.apply_hotkey_overrides(
        config.DEFAULT_HOTKEYS,
        json.loads(ps.read_text(encoding="utf-8"))["hotkeys"])[0]
    bak, losses = sio.save_personal_settings(ps, hotkeys_effective=eff,
                                             default_api=None,
                                             example_path=EXAMPLE_PS)
    check(bak is None and losses == [],
          f"BL8a: a default-equal hotkey entry counted as a loss -- falling out "
          f"of the diff preserves its meaning: ({bak}, {losses})")
    # (b) a valid pin deliberately dropped: instruction, not inability.
    d = tmp / "bl8b"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_text(json.dumps({"defaults": {"api": "groq"}}) + "\n", encoding="utf-8")
    bak, losses = sio.save_personal_settings(ps, hotkeys_effective=sio.preset_ctrl_alt(),
                                             default_api=sio.REMOVE_API_PIN,
                                             example_path=EXAMPLE_PS)
    check(bak is None and losses == []
          and "api" not in sio.read_personal_settings(ps)[0].get("defaults", {}),
          f"BL8b: dropping a VALID pin on the caller's signal owed a backup -- "
          f"a deliberate change is no loss: ({bak}, {losses})")
    # (c) a BOM heals without a backup (tolerated on read, dropped on write).
    d = tmp / "bl8c"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_bytes(b"\xef\xbb\xbf" + json.dumps({"vocabulary": {"terms": ["k"]}})
                   .encode("utf-8"))
    bak, losses = sio.save_personal_settings(ps, hotkeys_effective=sio.preset_ctrl_alt(),
                                             default_api=None, example_path=EXAMPLE_PS)
    check(bak is None and losses == []
          and not ps.read_bytes().startswith(b"\xef\xbb\xbf"),
          f"BL8c: the BOM heal owed a backup, or did not heal: ({bak}, {losses})")
    # (d) an EMPTY file is corrupt like any other: its zero bytes go to a backup
    # -- uniform, deliberately no special case.
    d = tmp / "bl8d"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_bytes(b"")
    bak, losses = sio.save_personal_settings(ps, hotkeys_effective=sio.preset_ctrl_alt(),
                                             default_api=None, example_path=EXAMPLE_PS)
    check(bak is not None and bak.read_bytes() == b"" and losses,
          f"BL8d: an empty file must back up its zero bytes like any corrupt "
          f"one: ({bak}, {losses})")

    # 9 -- the #294 seeds: a cp1252 example file aborts neither writer.
    d = tmp / "bl9"; d.mkdir()
    env_example = d / "env.example"
    env_example.write_bytes("# Umlaut-Präfix\nGROQ_API_KEY=\n".encode("cp1252"))
    envp = d / ".env"
    sio.write_env(envp, {"GROQ_API_KEY": "gsk_x"}, example_path=env_example)
    check(envp.exists() and sio.read_env(envp) == {"GROQ_API_KEY": "gsk_x"},
          "BL9: a cp1252 .env.example aborted the seed write (#294)")
    ps_example = d / "ps.example.json"
    ps_example.write_bytes(json.dumps({"hotkeys": {"_comment": "Präfix"}},
                                      ensure_ascii=False).encode("cp1252"))
    psp = d / "personal_settings.json"
    bak, losses = sio.save_personal_settings(psp, hotkeys_effective=sio.preset_fkeys(),
                                             default_api=None,
                                             example_path=ps_example)
    check(bak is None and losses == [] and psp.exists()
          and sio.read_personal_settings(psp)[1] is None,
          "BL9: a cp1252 personal_settings example aborted the absent-file save "
          "(#294) -- the skeleton just loses its seeded comments")

    # 10 -- the silent lane never enters: gated False, byte-still, NO backup.
    d = tmp / "bl10"; d.mkdir()
    ps = d / "personal_settings.json"
    ps.write_bytes(corrupt)
    check(sio.write_ui_language(ps, "de", example_path=EXAMPLE_PS) is False,
          "BL10: the silent toggle wrote over a corrupt file")
    check(ps.read_bytes() == corrupt
          and sorted(x.name for x in d.iterdir()) == ["personal_settings.json"],
          f"BL10: the silent lane took the backup lane -- D-026 reserves it for "
          f"the deliberate actions: {sorted(x.name for x in d.iterdir())}")


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

    Two control cases matter as much as the failures. A corrupt or ANSI
    personal_settings.json is no read failure since D-026 -- it belongs on the
    backup lane, and diverting it here would resurrect the retired abort. And an
    unreadable `.env` this save has no instruction for must not block it: write_env
    is a no-op there, so a pure hotkey save over a cp1252 `.env` works today and has to
    keep working. What is NOT such a case is a pending deletion (#328): it edits the
    found file like any write, so it probes -- and aborts -- with it."""
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

    # 3 -- an ANSI/cp1252 personal_settings.json: its German vocabulary is intact,
    # just in the wrong encoding. Since D-026 that is no abort case -- the save
    # goes through over the backup lane -- so the pre-flight must NOT report it.
    env_3 = tmp / "pf_ansips.env"
    env_3.write_text("GROQ_API_KEY=gsk_old\n", encoding="utf-8")
    ps_3 = tmp / "pf_ansi.json"
    ps_3.write_bytes(ANSI_JSON)
    env_before, ps_before = env_3.read_bytes(), ps_3.read_bytes()
    check(P(env_path=env_3, env_updates=A_KEY, ps_path=ps_3) is None,
          "pre-flight: an ANSI personal_settings.json was reported as a read "
          "failure -- since D-026 it rides the backup lane, and blocking it here "
          "would resurrect the retired undecodable-file abort")
    check(env_3.read_bytes() == env_before and ps_3.read_bytes() == ps_before,
          "pre-flight: the probe itself wrote something")
    bak, _losses = sio.save_personal_settings(
        ps_3, hotkeys_effective=sio.preset_ctrl_alt(), default_api=None,
        example_path=EXAMPLE_PS)
    check(bak is not None and bak.read_bytes() == ps_before,
          "pre-flight fixture: the save the pre-flight now clears must really go "
          "through with the ANSI bytes parked byte-exact in the backup")
    bak.unlink()

    # 4 -- corrupt but decodable: the same non-failure, now uniformly on the
    # backup lane. Diverting it into a read failure would take the way out away.
    ps_4 = tmp / "pf_corrupt.json"
    ps_4.write_text('{\n  "vocabulary": {"terms": ["keepme"]},\n', encoding="utf-8")
    _data, warn = sio.read_personal_settings(ps_4)
    check(isinstance(warn, str) and warn,
          "pre-flight fixture: the corrupt-but-decodable file should warn, not raise")
    check(P(env_path=env_ok, env_updates=A_KEY, ps_path=ps_4) is None,
          "pre-flight: a corrupt-but-decodable personal_settings.json was reported as "
          "unreadable -- it belongs on D-026's backup lane, which the explicit save "
          "is allowed to take")
    bak, _losses = sio.save_personal_settings(
        ps_4, hotkeys_effective=sio.preset_ctrl_alt(), default_api=None,
        example_path=EXAMPLE_PS)
    _d2, warn2 = sio.read_personal_settings(ps_4)
    check(bak is not None and warn2 is None,
          "pre-flight: the save it cleared did not back up + overwrite the corrupt file")
    bak.unlink()

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

    # 7 -- both files ANSI: with a key to write, .env -- the one written first and
    # still an abort case -- is the one named. With nothing to write to .env, the
    # ANSI personal_settings.json blocks nothing anymore: the save rides its
    # backup lane (the ps-names-itself case lives on in the locked lane below,
    # where the bytes really cannot be read).
    ps_7 = tmp / "pf_both.json"
    ps_7.write_bytes(ANSI_JSON)
    got = P(env_path=env_5, env_updates=A_KEY, ps_path=ps_7)
    check(isinstance(got, tuple) and Path(got[0]) == env_5,
          f"pre-flight: with a key to write the ANSI .env must be named, the one "
          f"file this save still aborts on: {got!r}")
    check(P(env_path=env_5, env_updates=BLANK, ps_path=ps_7) is None,
          "pre-flight: with .env out of the picture an ANSI personal_settings.json "
          "blocked the save -- since D-026 it goes through over the backup lane")

    # 8 -- a pending DELETION is a touch (#328): nothing to write, a key to remove,
    # and the same cp1252 .env. It has to abort exactly like a write would --
    # D-026's "a present-but-unreadable or undecodable .env still aborts the save"
    # holds for removals too, and a delete line-edits the found file just as much.
    REMOVE = {"GROQ_API_KEY": sio.REMOVE_ENV_KEY}
    got = P(env_path=env_5, env_updates=REMOVE, ps_path=ps_5)
    check(isinstance(got, tuple) and Path(got[0]) == env_5
          and isinstance(got[1], UnicodeDecodeError),
          f"pre-flight: a pending .env deletion over an unreadable file was waved "
          f"through -- the blank-field skip rule must not swallow a removal: {got!r}")
    check(env_5.read_bytes() == ANSI_ENV, "pre-flight: the .env probe wrote something")

    # 9 -- and over a healthy .env the same removal clears the pre-flight, with the
    # write that follows really taking the line out (the writer half of the pair).
    env_10 = tmp / "pf_delete.env"
    env_10.write_text("GROQ_API_KEY=gsk_old\nSONIOX_API_KEY=sx_old\n", encoding="utf-8")
    check(P(env_path=env_10, env_updates=REMOVE, ps_path=ps_ok) is None,
          "pre-flight: a removal over a readable .env was reported as a failure")
    sio.write_env(env_10, REMOVE)
    check(sio.read_env(env_10) == {"SONIOX_API_KEY": "sx_old"},
          f"pre-flight fixture: the removal it cleared did not remove the key: "
          f"{sio.read_env(env_10)}")

    # 10 -- the locked lane. chmod(0) only enforces this where the filesystem and the
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
          "nokey: with nothing to write the pre-flight passes the broken .env over "
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

    for good in ("ctrl+alt+p", "ctrl+alt+6", "f9", "ctrl+alt+f12",
                 # #325: the extended set, bare included -- both lanes permissive
                 "ctrl+alt+home", "pause", "scrolllock", "num5", "shift+insert"):
        ok, msg = sio.validate_combo(good)
        check(ok, f"validate_combo rejected a good combo {good!r}: {msg}")
    # D-023 (#317): no layout-resolved keys -- the umlaut and its old 'ue' alias
    # are rejected like any other non-static key
    for bad in ("", "   ", "ctrl+alt", "ctrl+alt+p+q", "ctrl+alt+notakey", "@#$",
                "ctrl+alt+ü", "ctrl+alt+ue"):
        ok, _ = sio.validate_combo(bad)
        check(not ok, f"validate_combo accepted a bad combo {bad!r}")
    # #325's one dead combo class, via the shared hotkey_parse.dead_combo_reason:
    # rejected with the message the capture feedback shows verbatim.
    for dead in ("ctrl+pause", "ctrl+shift+scrolllock"):
        ok, msg = sio.validate_combo(dead)
        check(not ok and "never fire" in msg,
              f"validate_combo must reject {dead!r} as dead, got ({ok}, {msg!r})")

    C, A, S = sio.TK_STATE_CONTROL, sio.TK_STATE_ALT, sio.TK_STATE_SHIFT
    # Keycode-driven since #325: the second field is the virtual-key code
    # (event.keycode IS the VK on Windows Tk; off Windows these are synthetic).
    cases = [
        ((C | A, 0x50), "ctrl+alt+p"),
        ((0, 0x78), "f9"),                  # bare F-key
        ((C | A, 0x36), "ctrl+alt+6"),
        ((C | A | S, 0x41), "ctrl+alt+shift+a"),
        ((C | A, 0x67), "ctrl+alt+num7"),   # numpad -- capturable at all since #325
        ((0, 0x24), "home"),                # bare nav key: permissive in both lanes
        ((0, 0x91), "scrolllock"),          # no longer swallowed as a "modifier"
        ((C | A, 0x51), "ctrl+alt+q"),      # AltGr+Q reports Ctrl+Alt -- binds as
                                            # the combo it is and fires as (#325)
        ((C, 0x03), None),                  # VK_CANCEL: what a physical Ctrl+Pause
                                            # actually sends -- unbindable
        ((0, 0x11), None),                  # bare Ctrl: only a modifier is down
        ((0, 0xA0), None),                  # side-specific Shift, same
        ((C | A, 0xDE), None),              # OEM key (the ü position) -- D-023
        ((0, 0x14), None),                  # Caps Lock: honestly unbindable now
    ]
    for (state, keycode), expected in cases:
        got = sio.decode_key_event(state, keycode)
        check(got == expected,
              f"decode_key_event({state:#x}, {keycode:#x}) = {got!r}, expected {expected!r}")
    # The widget's stay-armed-silently branch keys on MODIFIER_VKS: the real
    # modifiers are in, the lock keys are not (they report as unbindable).
    for vk in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA5):
        check(vk in sio.MODIFIER_VKS, f"MODIFIER_VKS is missing {vk:#x}")
    for vk in (0x13, 0x14, 0x90, 0x91):
        check(vk not in sio.MODIFIER_VKS,
              f"MODIFIER_VKS must not swallow {vk:#x} -- Pause/locks are keys, "
              "not modifiers (#325)")

    # round-trip: the F-key preset diff, fed back through the production loader,
    # reproduces the preset -- exercising both the bare and the chord shapes.
    diff = sio.hotkeys_diff_vs_default(sio.preset_fkeys(), config.DEFAULT_HOTKEYS)
    check(diff.get("start_recording") == "f9" and diff.get("stop_recording_clipboard") == "f10",
          "diff lost the bare-F-key core ops")
    check(diff.get("cancel_recording") == "ctrl+f9",
          "diff lost the cancel_recording rebind")
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
    probe_ok = "btn.back" in sstr._EN and "btn.back" in sstr._DE
    check(probe_ok, "i18n: the t() probe btn.back is gone from a table -- restore it, "
                    "or pick a new probe that sits in both tables with different "
                    "values (#313)")
    if probe_ok:
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

    # D-028: the engine picker is key-agnostic, so the window carries no all-keyless
    # guidance line any more -- the line whose wording this table used to couple to the
    # Provider tab's name. A returning key means a returning surface, i.e. the window
    # reacting to unsaved key fields again.
    check("behavior.engine.keyless" not in sstr._EN
          and "behavior.engine.keyless" not in sstr._DE,
          "behavior.engine.keyless is back -- D-028 removed the window's key-aware "
          "guidance under the engine control (#332)")

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

    # D-024: one action binds one combo, so the hotkey rows have no second binding
    # to hint at and the "(+n more)" suffix went with the list shape. Same kind of
    # guard as the one above -- a returning key means a returning multi-combo display.
    check("hotkeys.more_suffix" not in sstr._EN and "hotkeys.more_suffix" not in sstr._DE,
          "hotkeys.more_suffix is back -- D-024 retired the multi-combo display (#318)")

    # #326, D-026: the settings window makes no statements about file health -- the
    # always-green hotkey status line, its dead save-time dialog and the pre-open
    # corruption strip went entirely. A returning key means a returning surface.
    for key in ("hotkeys.status.ok", "hotkeys.status.warn_prefix",
                "dlg.hotkeywarn.title", "dlg.hotkeywarn.body", "warn.corrupt"):
        check(key not in sstr._EN and key not in sstr._DE,
              f"{key} is back -- #326/D-026 removed the settings app's file-health "
              "surfaces; the log is the user's window into file problems")

    # Placeholder parity (#178): the key-set check above guards that DE and EN carry
    # the same keys, but not that a format string uses the same {…} tokens in both -- a
    # mismatch passes i18n and then crashes .format() in one language at runtime.
    # Guard every string generically -- covers existing, new, and future format
    # strings -- then pin the exact render contract of the #178 ones below. The
    # parity checks above only COLLECT (#313): a key can be missing from DE right
    # here, so walk the shared keys only -- the parity message already names the
    # orphan, and the driver has to reach its verdict either way.
    for k in sstr._EN:
        if k not in sstr._DE:
            continue
        en = set(re.findall(r"{(\w+)}", sstr._EN[k]))
        de = set(re.findall(r"{(\w+)}", sstr._DE[k]))
        check(en == de,
              f"i18n: placeholder mismatch in {k}: EN{sorted(en)} DE{sorted(de)}")
    # A pin looks its key up directly, so a key retired from BOTH tables -- parity
    # green, nothing else recorded -- must fail readably here, not die as the
    # KeyError that swallows every check block after check_i18n (#313).
    def pin(key, *tokens):
        if key not in sstr._EN:
            check(False, f"i18n: pinned format key {key} is gone from EN -- "
                         "retire the pin together with the string")
            return
        check(set(re.findall(r"{(\w+)}", sstr._EN[key])) == set(tokens),
              f"{key} must use exactly "
              + " and ".join("{" + tok + "}" for tok in sorted(tokens)))

    pin("done.loop.body", "start", "stop")
    pin("welcome.loop.body", "start", "stop")
    pin("done.controls.body", "exit_key", "settings_key")
    pin("behavior.engine.remember.current", "engine")
    pin("behavior.engine.remember.none", "engine")
    pin("machine.version.body", "version")


def check_i18n_gap_proof():
    """#313 per-run proof: check_i18n must REPORT a broken table, not die on it.

    Two in-memory mutations, each undone before the next check block runs. An EN
    key with no DE partner (the #313 shape) must leave the parity message naming
    the key; a pinned format key retired from BOTH tables -- the case the parity
    checks cannot see -- must leave the pin's own message. In both cases
    check_i18n has to return normally: an escaping KeyError would end the driver
    as a traceback and swallow every check block after it plus the failure print.
    """
    def run_and_expect(*fragments):
        # All fragments in ONE message, rather than one literal message: the parity
        # check lists every orphan it found, so a table that is ALREADY missing a DE
        # key -- the very run this proof matters in -- would otherwise fail the proof
        # over the neighbouring key in the same line.
        before = len(failures)
        raised = None
        try:
            check_i18n()
        except Exception as e:
            raised = f"{type(e).__name__}: {e}"
        provoked = failures[before:]
        del failures[before:]
        check(raised is None,
              f"check_i18n raised on a broken table instead of reporting: {raised}")
        check(any(all(f in m for f in fragments) for m in provoked),
              f"check_i18n recorded no message carrying {list(fragments)} for the "
              "planted gap")

    saved = sstr._DE.pop("btn.back", None)
    try:
        run_and_expect("missing in DE", "'btn.back'")
    finally:
        if saved is not None:
            sstr._DE["btn.back"] = saved

    saved_en = sstr._EN.pop("done.loop.body", None)
    saved_de = sstr._DE.pop("done.loop.body", None)
    try:
        run_and_expect("pinned format key done.loop.body is gone")
    finally:
        if saved_en is not None:
            sstr._EN["done.loop.body"] = saved_en
        if saved_de is not None:
            sstr._DE["done.loop.body"] = saved_de


# ---- README anchors behind the settings links (#316) -------------------------
_README_LINK_RE = re.compile(
    r"thoughtborne-windows/blob/main/(README(?:\.de)?\.md)#(\S+)\Z")


def _gh_anchor(heading):
    """GitHub's heading anchor: lowercased, punctuation dropped, spaces to hyphens.
    `\\w` is Unicode-aware here, which is what GitHub does with umlauts too."""
    return re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")


def check_readme_anchors():
    # A url.* value that carries a #anchor points at a README HEADING, and GitHub
    # derives that anchor from the heading text -- rename or translate the heading and
    # the link silently opens the file at the top instead of the section, with nothing
    # raising and nothing to see. Same coupling style as the guards in check_i18n,
    # across a file boundary: hold every such link to a heading that really stands in
    # the twin it names.
    seen = set()
    for lang, table in (("en", sstr._EN), ("de", sstr._DE)):
        for key, value in sorted(table.items()):
            m = _README_LINK_RE.search(value) if key.startswith("url.") else None
            if not m:
                continue
            seen.add((key, lang))
            readme, anchor = m.group(1), m.group(2)
            text = (config.SCRIPT_DIR / readme).read_text(encoding="utf-8")
            headings = {_gh_anchor(line.lstrip("#").strip())
                        for line in text.splitlines() if re.match(r"#{1,6} ", line)}
            check(anchor in headings,
                  f"i18n: {key} ({lang}) points at {readme}#{anchor}, but no heading in "
                  f"{readme} carries that GitHub anchor -- it was renamed, and the link "
                  "now opens the file at the top instead of the section")
    # #316: the mouse-button tip is the reason this guard exists, so a value that stops
    # resolving as an anchor into a README twin must be loud rather than skipped.
    for lang in ("en", "de"):
        check(("url.mouse_hotkeys", lang) in seen,
              f"url.mouse_hotkeys ({lang}) no longer reads as a README-twin anchor "
              f"({sstr.t('url.mouse_hotkeys', lang)!r}) -- the anchor guard just went "
              "silent for the link the hotkey tab's tip box opens")


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

    # (5) An UNDECODABLE file (ANSI/cp1252, its German vocabulary intact) gates
    # like corrupt JSON since D-026 -- one whole-file warning class -- instead of
    # raising: False, bytes untouched, and NO backup (rescuing a broken file
    # belongs to the deliberate actions, never to the silent lane).
    p = tmp / "ps_gate_ansi.json"
    ansi_bytes = '{\n  "vocabulary": {"terms": ["Grüße", "Präfix"]}\n}\n'.encode("cp1252")
    p.write_bytes(ansi_bytes)
    check(sio.write_ui_language(p, "de", example_path=EXAMPLE_PS) is False,
          "GATE-undecodable: an ANSI file must gate the silent toggle (False), "
          "the D-026 warning class it now shares with corrupt JSON")
    check(p.read_bytes() == ansi_bytes,
          "GATE-undecodable: the ANSI file was clobbered (vocabulary destroyed)")
    check(not list(tmp.glob("ps_gate_ansi.backup-*.json")),
          "GATE-undecodable: the silent toggle created a backup -- the backup lane "
          "belongs to the deliberate actions alone (D-026)")


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


# ---- on-save engine signal (#198, D-008/D-002/D-028) -------------------------
def check_engine_save_signal():
    """resolve_engine_save_signal across the whole two-mode decision table -- the
    riskiest logic in the field, exhaustively tested off-Windows (the GUI itself is
    hands-on only). One answer: None=leave defaults.api as found / REMOVE_API_PIN=drop
    the pin / an engine id=write it verbatim. No key state enters this decision and
    none may (D-028) -- a pin on an engine without a key is written like any other and
    resolves at the next start through the carousel (#40/#200), which is why the
    signature itself is pinned below."""
    R = sio.resolve_engine_save_signal
    B = config.BUILTIN_DEFAULT_API

    # fixed, untouched (mode + engine unchanged) -> leave the pin exactly as found
    check(R(mode_now="fixed", mode_loaded="fixed", engine_now="groq",
            engine_loaded="groq") is None,
          "signal: untouched fixed should leave the pin as found (None)")

    # fixed, engine changed -> write the new id verbatim
    check(R(mode_now="fixed", mode_loaded="fixed", engine_now="soniox",
            engine_loaded="groq") == "soniox",
          "signal: a changed fixed engine should write it verbatim")

    # remember -> fixed: whatever the list shows is pinned VERBATIM -- every engine,
    # the built-in default and a keyless one included. This is the acceptance
    # criterion of D-028's key-agnostic picker, stated as a table rather than as an
    # example: the resolver cannot even tell which engines have a key.
    for api in config.AVAILABLE_APIS:
        check(R(mode_now="fixed", mode_loaded="remember", engine_now=api,
                engine_loaded=api) == api,
              f"signal: flipping to fixed on {api!r} must pin exactly that engine "
              "verbatim -- key state is none of this decision's business (D-028)")

    # fixed -> remember (a pin was left) -> REMOVE the pin. Identity, not equality:
    # the sentinel is what the writer recognizes.
    check(R(mode_now="remember", mode_loaded="fixed", engine_now="groq",
            engine_loaded="groq") is sio.REMOVE_API_PIN,
          "signal: leaving a pin for remember-mode should drop it (REMOVE_API_PIN)")

    # remember, untouched -> write nothing at all, even when the (inactive) fixed
    # list shows another engine than the one it was seeded with.
    check(R(mode_now="remember", mode_loaded="remember", engine_now="groq",
            engine_loaded=B) is None,
          "signal: an untouched remember save should write nothing")

    # round-trip fixed -> remember -> fixed, same engine -> no spurious rewrite
    check(R(mode_now="fixed", mode_loaded="fixed", engine_now="soniox-live",
            engine_loaded="soniox-live") is None,
          "signal: a same-engine fixed round-trip should not rewrite the pin")

    # Key-agnostic BY SIGNATURE, and with no memory signal left: the settings app
    # writes no engine memory at all since D-028 retired the #178 preselect, its one
    # write. A parameter added here re-opens that decision rather than adding a
    # detail, so the whole parameter set is pinned.
    params = set(inspect.signature(R).parameters)
    check(params == {"mode_now", "mode_loaded", "engine_now", "engine_loaded"},
          f"signal: resolve_engine_save_signal takes {sorted(params)} -- it is handed "
          "neither key state nor memory state on purpose (D-028)")

    # The SHAPE of every answer, swept over the whole table: one signal, never a tuple
    # again, and an engine id only ever one the app can index AVAILABLE_APIS with.
    for mn in ("fixed", "remember"):
        for ml in ("fixed", "remember"):
            for now in config.AVAILABLE_APIS:
                for loaded in config.AVAILABLE_APIS:
                    got = R(mode_now=mn, mode_loaded=ml, engine_now=now,
                            engine_loaded=loaded)
                    check(got is None or got is sio.REMOVE_API_PIN
                          or got in config.AVAILABLE_APIS,
                          f"signal: {(mn, ml, now, loaded)} answered {got!r} -- the "
                          "only answers are None, REMOVE_API_PIN and a selectable "
                          "engine id")

    # The three helpers D-028 retired, guarded the way check_i18n guards the retired
    # detect_ui_language. A returning name means a returning behaviour: the window
    # judging engines by keys (#201, with the stored-.env fallback behind #332's
    # original report), moving the selection on a mode flip (#207), or preselecting an
    # engine from a key that is not saved yet (#178).
    for name in ("engine_keyed", "preselect_startup_api", "resolve_fixed_entry_engine"):
        check(not hasattr(sio, name),
              f"settings_io.{name} is back -- D-028 retired it (#332): the engine "
              "picker is key-agnostic and an unsaved edit moves nothing")


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
    # behind): the shipped hotkeys, no pin, English, push-to-talk off -- through
    # the D-026 backup lane, like every deliberate write since #263.
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
    bak, losses = sio.save_personal_settings(p, **RESET)
    check(bak is None and losses == [] and not list(tmp.glob("ps_reset_dirty.backup-*")),
          f"reset-dirty: a reset over a healthy, fully-understood file owed a "
          f"backup -- valid values a deliberate action changes are instruction, "
          f"not loss (D-026): {losses}")
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
        engine_loaded="soniox-live") is None,
        "reset-invalid: an untouched engine control no longer signals None -- "
        "the premise of the forced write below has changed, re-read D-002/D-008")
    check(sio.resolve_ptt_save_signal(enabled_now=False, enabled_loaded=False) is None,
          "reset-invalid: an untouched push-to-talk toggle no longer signals None -- "
          "the premise of the forced write below has changed, re-read D-002")
    p = tmp / "ps_reset_invalid.json"
    p.write_text(json.dumps({"defaults": {"api": "grok"},
                             "push_to_talk": {"enabled": "yes"},
                             "vocabulary": {"terms": ["keepme"]}},
                            indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    invalid_before = p.read_bytes()
    bak, losses = sio.save_personal_settings(p, **RESET)
    check(bak is not None and bak.read_bytes() == invalid_before,
          "reset-invalid: clearing hand-typed invalid values owed a backup with "
          "the old bytes byte-exact (D-026) -- the reset is what discards them")
    check(len(losses) == 2,
          f"reset-invalid: expected the two invalid entries as losses: {losses}")
    bak.unlink()
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
    bak, losses = sio.save_personal_settings(p, **RESET)
    check(bak is None and losses == [],
          f"reset-fresh: a reset over NO file owed a backup -- nothing exists to "
          f"lose: {losses}")
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
    sio.save_personal_settings(p, **RESET)
    once = p.read_bytes()
    bak, _losses = sio.save_personal_settings(p, **RESET)
    check(p.read_bytes() == once,
          "reset-twice: a second reset changed the file -- the reset is a forced "
          "state, so repeating it must be a no-op on the bytes")
    check(bak is None and not list(tmp.glob("ps_reset_twice.backup-*")),
          "reset-twice: a repeated reset owed a backup over its own output")

    # (e) .env is untouched. Trivially true (the reset never calls write_env, which
    # check_reset_wiring pins on the source), but it is the feature's loudest promise.
    env = tmp / "ps_reset.env"
    env.write_text("GROQ_API_KEY=gsk_secret\nSONIOX_API_KEY=so_secret\n", encoding="utf-8")
    env_before = env.read_bytes()
    sio.save_personal_settings(tmp / "ps_reset_dirty.json", **RESET)
    check(env.read_bytes() == env_before,
          "reset-env: the settings write reached the .env -- the keys are the user's "
          "data and the reset must never write them (D-011, D-020)")


def check_reset_wiring():
    """What the reset's call site must guarantee beyond the reach of the real window
    (#282, D-020). test_settings_visibility.test_reset_with_display drives the button
    itself -- the confirmation gate, the file the confirmed reset leaves behind, the
    vocabulary and the .env left alone, the restart reached, the button frozen against
    a second click -- and what that lane catches was dropped here (#309). What is left
    is what stayed green when each half was measured against it: the two confirmation
    bodies (their key reaches t() through a variable, so the literal-reading
    check_string_keys never sees them, and the lane compares t() against t(), where a
    missing key reads the same on both sides), the body2 wording, two writers that
    must be UNREACHABLE rather than merely unused, the two confirmation keywords its
    askyesno stub throws away with its **k, the restart's POSITION, which the stub
    registers wherever it stands, and the absence of a window destroy in the one abort
    branch no lane provokes -- plus the two shipped values, which stand here for a
    different reason: since #263 the lane's fixture opens German with push-to-talk on
    and drives both derivations red itself (re-measured, #312), but only where a
    display exists; without one the lane skips, and on such a checkout these pins are
    the only net for a D-015 regression."""
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
    # display lane sees a derivation now: since #263 its fixture opens German with
    # push-to-talk on and drives `self.lang` / `self.ptt_var.get()` red as per-run
    # mutations (re-measured, #312). But that lane needs a display -- without one it
    # skips (run_tests.py re-execs under xvfb-run only where one exists), and on such
    # a checkout these two pins are the only net against a derived write: a user
    # resetting out of the German window would keep German (D-015) and one with
    # push-to-talk on would keep it on. hotkeys_effective and default_api need no
    # literal here: the lane's fixture differs from the shipped value for both, so
    # deriving either turns it red where a display exists -- beyond that they share
    # the display gate with everything else the lane carries (#309's trade).
    writes = _calls_to(reset, "save_personal_settings")
    check(len(writes) == 1,
          f"reset-wiring: _reset_to_defaults makes {len(writes)} backup-lane writes, "
          "expected exactly one -- the reset is one forced write, not a sequence")
    check(not _calls_to(reset, "write_personal_settings"),
          "reset-wiring: _reset_to_defaults reaches the raw writer -- a deliberate "
          "write goes through the D-026 backup lane (save_personal_settings) and "
          "nothing else, or a corrupt file is discarded without a backup")
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
    left here were measured against those lanes and stayed green (#309; re-measured
    after #263 rebuilt them, #312): the probe only asks whether the cleaned update
    set is EMPTY, and in every one of the save lane's five cases the Groq half alone
    decides that answer (none fills Soniox and leaves Groq blank), so a probe reduced
    to the Groq half still agrees with the write in every one of them; and no
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
    # The D-026 pins (#263): _save writes personal settings through the backup
    # lane, exactly once, and never reaches the raw writer -- the structural
    # guarantee behind "no save discards content without a successful backup".
    check(len(_calls_to(save, "save_personal_settings")) == 1,
          "readfail-wiring: _save does not make exactly one save_personal_settings "
          "call -- the D-026 backup lane is the only personal-settings write a "
          "deliberate save may take (#263)")
    check(not _calls_to(save, "write_personal_settings"),
          "readfail-wiring: _save reaches write_personal_settings -- the raw writer "
          "refuses a broken file, and routing the save through it would either "
          "crash there or bypass the backup (D-026, #263)")
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


def _settings_app_tree(prefix):
    """thoughtborne_settings.py as a syntax tree, or None after recording a failure.
    The settings app imports tkinter at module level and cannot be imported from this
    ladder, so its call sites are checked on the source -- as a syntax tree rather
    than as text, so renaming a local or reflowing a call proves nothing while a real
    regression still goes red (the idiom of the thoughtborne.py guards in
    test_restart_signal.py, one step more precise)."""
    src_path = config.SCRIPT_DIR / "thoughtborne_settings.py"
    try:
        return ast.parse(src_path.read_text(encoding="utf-8"))
    except Exception as e:
        failures.append(f"{prefix}: could not parse thoughtborne_settings.py: "
                        f"{type(e).__name__}: {e}")
        return None


def _settings_app_methods(prefix):
    """The SettingsApp methods of thoughtborne_settings.py as a {name: FunctionDef}
    map, or {} after recording a failure."""
    tree = _settings_app_tree(prefix)
    if tree is None:
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


def _self_calls(method):
    """The names of the own methods one method calls -- `self.name(...)` only, so a
    `self._dict[key].config(...)` on a widget is not one of them."""
    return {c.func.attr for c in ast.walk(method) if isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and isinstance(c.func.value, ast.Name) and c.func.value.id == "self"}


def _self_assigns(method):
    """The `self.<attr>` names one method assigns to (plain and augmented). A
    `self.<dict>[key] = ...` is a subscript, not an attribute, so it is none of
    these -- which is what lets a guard speak about window STATE alone."""
    names = set()
    for node in ast.walk(method):
        targets = (node.targets if isinstance(node, ast.Assign)
                   else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign))
                   else [])
        for t in targets:
            if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                    and t.value.id == "self"):
                names.add(t.attr)
    return names


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
        """Every save_personal_settings call inside one method (the D-026 backup
        lane -- the only personal-settings write a deliberate save takes, #263)."""
        return _calls_to(method, "save_personal_settings")

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
          f"ptt-wiring: expected exactly one save_personal_settings call in _save, "
          f"found {len(save_writes)} -- this guard assumes the single save write")
    for call in save_writes:
        passed = {k.arg: k.value for k in call.keywords if k.arg}
        check("ptt_enabled" in passed,
              "ptt-wiring: _save's save_personal_settings call passes no ptt_enabled -- "
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


# ---- the engine control's D-028 wiring ---------------------------------------
def check_engine_control_wiring():
    """That the window really leaves an unsaved edit without effect (D-028), pinned
    statically on thoughtborne_settings.py's syntax tree. Nothing else can see it: the
    window is hands-on only, and an exception raised inside a Tk callback lands in the
    log rather than in any test run -- a call left behind to a retired helper would
    therefore leave the whole ladder green while the control is broken in the field.

    One guard per way the retired behaviour could return: a key-field edit touches
    nothing but its own verdict (#178's preselect and #201's re-render are gone from
    it), a mode click only re-renders (#207's auto-move is gone), the renderer never
    moves the selection (D-002 -- a programmatic set is not a pick), _save hands the
    writer the RESOLVED signal, no retired helper is called anywhere in the file, and
    the app writes no engine memory at all while still READING one for the display."""
    methods = _settings_app_methods("engine-control-wiring")
    if not methods:
        return
    tree = _settings_app_tree("engine-control-wiring")
    if tree is None:
        return
    # The engine control's own state: what a move would have to touch to survive the
    # save. The window may render freely; these are the values the save reads.
    SELECTION = {"engine_index", "_engine_index_loaded", "_mode_loaded",
                 "_remember_display_api"}

    # ---- a field edit touches nothing but its own verdict ----
    edit = methods.get("_on_field_edit")
    if edit is None:
        failures.append("engine-control-wiring: SettingsApp._on_field_edit not found -- "
                        "the key-field callback was renamed and this guard no longer "
                        "guards anything")
    else:
        # An allowlist, not a denylist, and deliberately so: D-028 says an edit changes
        # nothing but its own field, so a NEW callee is the thing to notice. Whoever
        # needs one extends this set together with the reason it does not cross the
        # rule. The equality also keeps the handler from going empty, which would make
        # the verdict invalidation itself disappear unnoticed.
        callees = _self_calls(edit)
        check(callees == {"_render_indicator"},
              f"engine-control-wiring: _on_field_edit calls {sorted(callees)} -- the "
              "one thing a key-field edit may touch is its own verdict indicator "
              "(D-028: an unsaved edit has no other effect; the #178 preselect and the "
              "#201 key-aware re-render were removed from here)")
        moved = _self_assigns(edit) & SELECTION
        check(not moved,
              f"engine-control-wiring: _on_field_edit assigns {sorted(moved)} -- typing "
              "a key may not move the engine selection (D-028)")

    # ---- a mode click re-renders, and moves nothing ----
    on_mode = methods.get("_on_mode")
    if on_mode is None:
        failures.append("engine-control-wiring: SettingsApp._on_mode not found -- the "
                        "mode handler was renamed and this guard no longer guards "
                        "anything")
    else:
        moved = _self_assigns(on_mode) & SELECTION
        check(not moved,
              f"engine-control-wiring: _on_mode assigns {sorted(moved)} -- switching "
              "between remember- and fixed-mode may never move the selection by itself "
              "(D-028 retired #207's keyed-engine auto-move): what 'always start with' "
              "pins is the engine the list shows, keyed or not")
        check("_render_engine_control" in _self_calls(on_mode),
              "engine-control-wiring: _on_mode no longer re-renders the engine control "
              "-- the radios would then keep the previous mode's enabled state, and the "
              "guard above would pass over a handler that does nothing at all")

    # ---- the renderer stays move-free (D-002) and key-blind (D-028) ----
    render = methods.get("_render_engine_control")
    if render is None:
        failures.append("engine-control-wiring: SettingsApp._render_engine_control not "
                        "found -- the engine renderer was renamed and the move-free "
                        "half of this guard no longer guards anything")
    else:
        dirty = sorted(_self_assigns(render) & SELECTION)
        check(not dirty,
              f"engine-control-wiring: _render_engine_control gained a selection side "
              f"effect ({dirty}) -- the renderer must stay move-free so a programmatic "
              "set never counts as a pick and an untouched save stays byte-identical "
              "(D-002)")
        # The greying regression itself, caught where the greying would live: the two
        # key fields, their load-time snapshot, the names the retired key-aware control
        # read, and the ways past all of them straight to the .env or the process
        # environment. Reading any of these here is the picker judging engines by keys
        # again -- a D-028 supersede discussion, not a detail.
        #
        # Unlike the _on_field_edit allowlist above this is a denylist, and it can only
        # name routes somebody has thought of: a renderer that reaches key state some
        # other way walks past it. What closes that gap is the real window in
        # test_settings_visibility.py, which measures the greying itself rather than the
        # way to it; this half is the one that also runs where no display does.
        KEY_STATE = {"engine_has_key", "env_has_key", "_live_env", "_has_any_key",
                     "_stored_env", "groq_var", "soniox_var",
                     "read_env", "environ", "getenv"}
        reads = sorted({n.attr for n in ast.walk(render) if isinstance(n, ast.Attribute)
                        and n.attr in KEY_STATE})
        check(not reads,
              f"engine-control-wiring: _render_engine_control reads {reads} -- the "
              "engine picker is key-agnostic (D-028): every engine stays selectable "
              "whatever the key fields hold, and a pin without a key resolves at the "
              "next start through the carousel (#40/#200)")

    # ---- _save writes the RESOLVED signal, not the raw selection ----
    save = methods.get("_save")
    if save is None:
        failures.append("engine-control-wiring: SettingsApp._save not found -- the save "
                        "path was renamed and this guard no longer guards anything")
    else:
        resolved = set()
        for node in ast.walk(save):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Call)
                    and getattr(node.value.func, "attr", None)
                    == "resolve_engine_save_signal"):
                resolved |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        check(resolved,
              "engine-control-wiring: _save never assigns "
              "settings_io.resolve_engine_save_signal(...) to a local -- the table above "
              "is decoration unless the save actually asks it (#198, D-008)")
        writes = _calls_to(save, "save_personal_settings")
        check(writes,
              "engine-control-wiring: _save makes no save_personal_settings call -- the "
              "settings write was renamed or routed elsewhere, and the default_api "
              "guard below would then hold an empty set to account (#263 put the save "
              "through the D-026 backup lane, which is this call)")
        for call in writes:
            value = {k.arg: k.value for k in call.keywords if k.arg}.get("default_api")
            check(isinstance(value, ast.Name) and value.id in resolved,
                  "engine-control-wiring: _save passes default_api="
                  f"{ast.unparse(value) if value is not None else '<missing>'}, not the "
                  f"resolve_engine_save_signal result "
                  f"({' / '.join(sorted(resolved)) or 'none'}) -- the raw selection "
                  "would rewrite defaults.api on every save (D-002)")

    # ---- nothing calls what D-028 retired, anywhere in the file ----
    # pyflakes reports undefined NAMES, not missing attributes, so a leftover
    # `settings_io.engine_keyed(...)` or `self._maybe_preselect_engine()` is legal to
    # every other lane on the ladder and raises only in the user's log. This is the
    # lane that sees it.
    retired = {"engine_keyed": "#201's key-aware greying",
               "preselect_startup_api": "#178's wizard preselect",
               "resolve_fixed_entry_engine": "#207's mode-flip auto-move",
               "_maybe_preselect_engine": "#178's wizard preselect",
               "_engine_user_chose": "#178's explicit-pick lock",
               "_remember_display_loaded_api": "the retired memory-write comparison",
               "engine_guidance": "#201's all-keyless guidance line"}
    seen = sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                   and n.attr in retired})
    check(not seen,
          "engine-control-wiring: thoughtborne_settings.py still names "
          + ", ".join(f"{n} ({retired[n]})" for n in seen)
          + " -- D-028 retired these (#332), and a stale reference here raises inside a "
            "Tk callback, i.e. into the log and past every test")

    # ---- the app reads the engine memory, and never writes it ----
    writes = _calls_to(tree, "write_last_engine")
    check(not writes,
          "engine-control-wiring: the settings app writes the engine memory -- since "
          "D-028 it writes none at all (runtime_state.json records what the user "
          "switched to while dictating, and this window never moves that)")
    check(len(_calls_to(tree, "read_last_engine")) == 1,
          f"engine-control-wiring: expected exactly one read_last_engine call (the "
          f"remember-mode display), found {len(_calls_to(tree, 'read_last_engine'))} -- "
          "without it the guard above would pass over an app that no longer touches "
          "the memory at all, and the remember radio would name the wrong engine")


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
    # env_has_key: the shared key-presence predicate behind the window-mode decision.
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

    # Seam to the real reader: read_env feeds the decision, exactly as it feeds the
    # window's key fields.
    p = tmp / "env_fr_key"
    p.write_text("GROQ_API_KEY=gsk_real\n", encoding="utf-8")
    check(sio.resolve_first_run(False, sio.read_env(p)) is False,
          "a readable .env with a key -> plain dialog")
    # an ANSI/cp1252 .env degrades to {} in read_env -> no key -> wizard, the same way
    # the window's fields come up empty over one (the B3 cp1252 pattern again).
    p = tmp / "env_fr_ansi"
    p.write_bytes("# Umlaut-Kommentar: Präfix\nGROQ_API_KEY=secret\n".encode("cp1252"))
    check(sio.resolve_first_run(False, sio.read_env(p)) is True,
          "an ANSI .env reads as no key -> wizard (as the empty key fields over one)")


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
        check_env_quoting(tmp)
        check_env_delete(tmp)
        check_personal_settings(tmp)
        check_ui_language(tmp)
        check_ui_language_gate(tmp)
        check_engine_pin(tmp)
        check_ptt_toggle(tmp)
        check_reset_defaults(tmp)
        check_backup_lane(tmp)
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
    check_i18n_gap_proof()
    check_readme_anchors()
    check_env_save_updates()
    check_engine_save_signal()
    check_ptt_read()
    check_ptt_save_signal()
    check_ptt_wiring()
    check_lang_writer_signature()
    check_engine_control_wiring()
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
