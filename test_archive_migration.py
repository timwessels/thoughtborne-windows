#!/usr/bin/env python3
"""Off-Windows verification of the legacy archive migration (#50, #242).

`config.migrate_legacy_archives()` moves years of a user's recordings
(voice_archive/ + text_archive/ -> history/audio/ + history/transcripts/) and
already carries its own test seam: `base_dir` parameterizes the layout root for
exactly this purpose, and the function returns its user-facing event strings so
a driver can assert on outcomes. Nothing in it is Windows-bound, so every layout
below is a plain temp directory.

What the driver is actually guarding: the rmdir branch cannot delete a non-empty
directory -- that is an OS guarantee, not a promise this code makes -- so the
risk here is NOT "recordings get deleted". It is "recordings end up somewhere
unexpected, silently": a pair that reports success without moving anything, a
half-migrated layout that the next start no longer recognizes, or a failure that
takes the whole start down with it. Hence every case checks the files on disk,
not just the events, and `_run()` turns any escaping exception into a failure --
"never raises" is asserted in all cases at once, not in one dedicated case.

Covered: both/one/no legacy folders, the empty-new-side heal, the BOTH-exist
refusal (whole tree byte-identical afterwards), a half-finished run, a simulated
cross-volume EXDEV failure plus the retry that heals it, the benign
another-instance-won race, the non-OSError catch-all, and a sweep of hostile
layouts (a file where a folder is expected).

Event assertions are by SUBSTRING throughout ("Archive migrated to the new
layout", "BOTH exist", "Could not migrate", "Unexpected error"): the full texts
are user-facing prose and may be reworded without breaking this contract.

    python3 test_archive_migration.py          # verify, exit non-zero on any violation
    python3 test_archive_migration.py --show   # print the events each case produced
"""
import sys
import errno
import shutil
import pathlib
import logging
import tempfile
import contextlib
from pathlib import Path

# Silence before importing config: the migration logs through 'Thoughtborne.Config',
# and the failure lanes deliberately log a WARNING (and, in the catch-all, a full
# traceback). A driver run should print its own verdict, nothing else.
logging.getLogger("Thoughtborne").setLevel(logging.CRITICAL)

import config

SHOW = "--show" in sys.argv

# Captured before anything patches it; check_rename_patch_restored() compares
# against this identity at the end of the run.
_ORIGINAL_RENAME = pathlib.Path.rename

failures = []
shown = []  # (case label, events) pairs, printed by --show


def check(cond, msg):
    if not cond:
        failures.append(msg)


# ---------------------------------------------------------------- helpers

def _case(root, name):
    """A fresh, empty layout root for one case -- no case ever sees another's state."""
    d = Path(root) / name
    d.mkdir()
    return d


def _seed(folder, rel_names):
    """Create `folder` with one small file per relative name (sub/dirs allowed)."""
    folder.mkdir(parents=True, exist_ok=True)
    for rel in rel_names:
        p = folder / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"content of {rel}", encoding="utf-8")


def _tree(root):
    """Relative path -> file bytes (files) / None (directories).

    Compared before and after a call, this is the "nothing was touched" proof --
    it sees content changes, moves, deletions and stray new folders alike.
    """
    out = {}
    for p in sorted(Path(root).rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = p.read_bytes() if p.is_file() else None
    return out


def _run(label, base):
    """Call the migration for one case; an escaping exception IS the violation.

    The function's own docstring promises it never raises -- a rescue path must
    not block the start -- so the guarantee is checked on every single call here
    rather than in one separate sweep.
    """
    try:
        events = config.migrate_legacy_archives(base_dir=base)
    except BaseException as e:  # noqa: BLE001 -- the whole point of the check
        failures.append(f"{label}: migration raised {type(e).__name__}: {e}")
        return []
    shown.append((label, events))
    return events


def _one(events, needle, label):
    """Assert exactly one event carries `needle`, and return it (or '')."""
    hits = [e for e in events if needle in e]
    check(len(hits) == 1,
          f"{label}: expected exactly one event containing {needle!r}, got {events!r}")
    return hits[0] if hits else ""


@contextlib.contextmanager
def _patched_rename(fake):
    """Swap pathlib.Path.rename for the duration of one call, always restoring it.

    A class-level patch rather than a Path subclass on purpose: subclassing
    pathlib.Path only became supportable in 3.12 and CI also runs 3.10. The
    try/finally is not decoration -- a forgotten restore would poison every later
    case in this process, and under pytest even other drivers; that is what
    check_rename_patch_restored() confirms at the end.
    """
    pathlib.Path.rename = fake
    try:
        yield
    finally:
        pathlib.Path.rename = _ORIGINAL_RENAME


def _raise_exdev(self, target):
    """Stand-in for the cross-volume case: os.rename() across drives is EXDEV.

    Honest about what this is: off Windows (and inside one temp directory) a real
    cross-volume move cannot be produced, so this SIMULATES the failure to check
    the DEGRADATION CONTRACT -- warn, touch nothing, never raise, retry next
    start. It does not prove anything about Windows' actual behaviour when a user
    moves the install to another drive; that lane stays hands-on.
    """
    raise OSError(errno.EXDEV, "Invalid cross-device link", str(self), None, str(target))


def _rename_after_moving(self, target):
    """Stand-in for the benign race: another instance migrated this pair first.

    Performs the move for real, then reports the folder as gone -- that is the
    exact state config.py's `not old.exists() and new.is_dir()` branch looks for.
    shutil.move goes through os.rename, not Path.rename, so the patch cannot
    recurse into itself.
    """
    shutil.move(str(self), str(target))
    raise OSError(errno.ENOENT, "No such file or directory", str(self))


def _raise_runtime(self, target):
    """A non-OSError failure: the catch-all lane, which has no race branch."""
    raise RuntimeError("simulated non-OSError failure")


# ---------------------------------------------------------------- cases

def check_both_folders_migrate(root):
    """The ordinary upgrade: both legacy folders, files intact at the new place."""
    base = _case(root, "both")
    # A .partial sidecar (#49) and a file in a subfolder: both must travel along,
    # and both must be seen by the rglob file count the event reports.
    _seed(base / "voice_archive", ["20260101_120000.wav", "20260101_120500.wav",
                                   "20260101_121000.partial", "sub/older.wav"])
    _seed(base / "text_archive", ["20260101_120000.txt", "sub/older.txt"])

    events = _run("both folders", base)

    check(len(events) == 2, f"both folders: expected 2 events, got {events!r}")
    audio_ev = _one(events, "voice_archive", "both folders")
    text_ev = _one(events, "text_archive", "both folders")
    for ev in (audio_ev, text_ev):
        check("Archive migrated to the new layout" in ev,
              f"both folders: event does not announce a migration: {ev!r}")
    check("(4 files)" in audio_ev, f"both folders: wrong audio file count: {audio_ev!r}")
    check("(2 files)" in text_ev, f"both folders: wrong transcript file count: {text_ev!r}")

    check(not (base / "voice_archive").exists(), "both folders: voice_archive survived")
    check(not (base / "text_archive").exists(), "both folders: text_archive survived")
    for rel in ("20260101_120000.wav", "20260101_120500.wav",
                "20260101_121000.partial", "sub/older.wav"):
        p = base / "history" / "audio" / rel
        check(p.is_file(), f"both folders: {rel} missing under history/audio")
        if p.is_file():
            check(p.read_text(encoding="utf-8") == f"content of {rel}",
                  f"both folders: {rel} changed content while moving")
    for rel in ("20260101_120000.txt", "sub/older.txt"):
        check((base / "history" / "transcripts" / rel).is_file(),
              f"both folders: {rel} missing under history/transcripts")


def check_one_folder_only(root):
    """Only one legacy folder: exactly its pair moves, the other side stays absent."""
    base = _case(root, "one")
    _seed(base / "voice_archive", ["a.wav"])

    events = _run("one folder", base)

    check(len(events) == 1, f"one folder: expected 1 event, got {events!r}")
    _one(events, "Archive migrated to the new layout", "one folder")
    check((base / "history" / "audio" / "a.wav").is_file(),
          "one folder: a.wav did not arrive under history/audio")
    check(not (base / "history" / "transcripts").exists(),
          "one folder: history/transcripts was created for a pair that had nothing to move")


def check_both_exist_untouched(root):
    """Legacy AND a populated new side: refuse, explain, touch nothing at all."""
    base = _case(root, "collision")
    _seed(base / "voice_archive", ["old.wav"])
    _seed(base / "history" / "audio", ["new.wav"])
    before = _tree(base)

    events = _run("both exist", base)

    check(len(events) == 1, f"both exist: expected 1 event, got {events!r}")
    _one(events, "BOTH exist", "both exist")
    # The strongest form of "never deletes user data": the entire layout root,
    # both sides included, is bit-for-bit what it was before the call.
    check(_tree(base) == before, "both exist: the layout changed despite the refusal")


def check_empty_newside_healed(root):
    """The failed-first-attempt state: an EMPTY new folder is removed, then the move runs."""
    base = _case(root, "heal")
    _seed(base / "voice_archive", ["a.wav", "b.wav"])
    (base / "history" / "audio").mkdir(parents=True)

    events = _run("empty new side", base)

    check(len(events) == 1, f"empty new side: expected 1 event, got {events!r}")
    ev = _one(events, "Archive migrated to the new layout", "empty new side")
    check("(2 files)" in ev, f"empty new side: wrong file count: {ev!r}")
    check(not (base / "voice_archive").exists(), "empty new side: voice_archive survived")
    for rel in ("a.wav", "b.wav"):
        check((base / "history" / "audio" / rel).is_file(),
              f"empty new side: {rel} missing after the heal")


def check_fresh_start_noop(root):
    """A fresh install: no events, and above all nothing created."""
    base = _case(root, "fresh")

    events = _run("fresh start", base)

    check(events == [], f"fresh start: expected no events, got {events!r}")
    check(_tree(base) == {}, f"fresh start: something was created: {sorted(_tree(base))!r}")


def check_half_finished(root):
    """One pair already migrated, the other still legacy: only the legacy one moves."""
    base = _case(root, "half")
    _seed(base / "history" / "audio", ["already.wav"])
    _seed(base / "text_archive", ["a.txt"])

    events = _run("half finished", base)

    check(len(events) == 1, f"half finished: expected 1 event, got {events!r}")
    ev = _one(events, "text_archive", "half finished")
    check("Archive migrated to the new layout" in ev,
          f"half finished: event does not announce a migration: {ev!r}")
    check((base / "history" / "audio" / "already.wav").is_file(),
          "half finished: the already-migrated audio side was disturbed")
    check((base / "history" / "transcripts" / "a.txt").is_file(),
          "half finished: a.txt did not arrive under history/transcripts")
    check(not (base / "text_archive").exists(), "half finished: text_archive survived")


def check_cross_volume_exdev(root):
    """A cross-volume rename fails: warn per pair, leave the files, heal on retry.

    SIMULATED (see _raise_exdev): what is verified is the degradation contract,
    not Windows' real cross-drive behaviour.
    """
    base = _case(root, "exdev")
    _seed(base / "voice_archive", ["a.wav", "sub/b.wav"])
    _seed(base / "text_archive", ["a.txt"])
    legacy_before = {"voice_archive": _tree(base / "voice_archive"),
                     "text_archive": _tree(base / "text_archive")}

    with _patched_rename(_raise_exdev):
        events = _run("exdev", base)

    check(len(events) == 2, f"exdev: expected one event per pair, got {events!r}")
    for name in ("voice_archive", "text_archive"):
        ev = _one(events, name, "exdev")
        check("Could not migrate" in ev, f"exdev: event does not warn: {ev!r}")
        check(_tree(base / name) == legacy_before[name],
              f"exdev: {name} was disturbed by the failed migration")
    # The new side must hold nothing. history/ itself exists -- the mkdir runs
    # before the rename -- and that is precisely the empty-new-side state the
    # rmdir branch (check_empty_newside_healed) heals on a later start.
    check(not (base / "history" / "audio").exists(),
          "exdev: history/audio exists after a failed migration")
    check(not (base / "history" / "transcripts").exists(),
          "exdev: history/transcripts exists after a failed migration")

    # "the next start retries the migration" -- the docstring's promise, as a case.
    retry = _run("exdev retry", base)
    check(len(retry) == 2, f"exdev retry: expected 2 events, got {retry!r}")
    for ev in retry:
        check("Archive migrated to the new layout" in ev,
              f"exdev retry: the retry did not migrate: {ev!r}")
    check((base / "history" / "audio" / "sub" / "b.wav").is_file(),
          "exdev retry: sub/b.wav did not arrive under history/audio")
    check((base / "history" / "transcripts" / "a.txt").is_file(),
          "exdev retry: a.txt did not arrive under history/transcripts")
    check(not (base / "voice_archive").exists(), "exdev retry: voice_archive survived")


def check_race_loser_benign(root):
    """A second instance migrated first: no user-facing event, files at the new place."""
    base = _case(root, "race")
    _seed(base / "voice_archive", ["a.wav"])

    with _patched_rename(_rename_after_moving):
        events = _run("race loser", base)

    # The benign-race branch logs INFO and continues -- deliberately silent
    # towards the user, because nothing went wrong.
    check(events == [], f"race loser: expected no user-facing event, got {events!r}")
    check((base / "history" / "audio" / "a.wav").is_file(),
          "race loser: a.wav is not at the new location")
    check(not (base / "voice_archive").exists(), "race loser: voice_archive survived")


def check_unexpected_error_contained(root):
    """A non-OSError failure is caught too: a rescue path must never block the start."""
    base = _case(root, "catchall")
    _seed(base / "voice_archive", ["a.wav"])
    before = _tree(base / "voice_archive")

    with _patched_rename(_raise_runtime):
        events = _run("catch-all", base)

    check(len(events) == 1, f"catch-all: expected 1 event, got {events!r}")
    ev = _one(events, "Unexpected error", "catch-all")
    check("voice_archive" in ev, f"catch-all: event does not name the folder: {ev!r}")
    check(_tree(base / "voice_archive") == before,
          "catch-all: voice_archive was disturbed by the failed migration")


def check_hostile_layouts_sweep(root):
    """Layouts where a file sits where a folder belongs -- degrade, never raise.

    Cheap breadth over the guard clause and the two OSError exits: the escaping
    exception check lives in _run(), so these cases assert the outcome shape and
    that user files stay put.
    """
    # 1. voice_archive is a FILE: the is_dir() guard skips the pair entirely.
    base = _case(root, "sweep_file_legacy")
    (base / "voice_archive").write_text("not a folder", encoding="utf-8")
    events = _run("sweep: legacy is a file", base)
    check(events == [], f"sweep: a file named voice_archive produced events: {events!r}")
    check((base / "voice_archive").read_text(encoding="utf-8") == "not a folder",
          "sweep: the file named voice_archive was touched")

    # 2. history is a FILE: the parent mkdir fails -> warn, leave the legacy folder.
    base = _case(root, "sweep_file_history")
    _seed(base / "voice_archive", ["a.wav"])
    (base / "history").write_text("not a folder", encoding="utf-8")
    events = _run("sweep: history is a file", base)
    _one(events, "Could not migrate", "sweep: history is a file")
    check((base / "voice_archive" / "a.wav").is_file(),
          "sweep: a.wav vanished when history was a file")

    # 3. history/audio is a FILE: the rename itself fails -> warn, leave everything.
    base = _case(root, "sweep_file_newside")
    _seed(base / "voice_archive", ["a.wav"])
    (base / "history").mkdir()
    (base / "history" / "audio").write_text("not a folder", encoding="utf-8")
    events = _run("sweep: new side is a file", base)
    _one(events, "Could not migrate", "sweep: new side is a file")
    check((base / "voice_archive" / "a.wav").is_file(),
          "sweep: a.wav vanished when history/audio was a file")
    check((base / "history" / "audio").read_text(encoding="utf-8") == "not a folder",
          "sweep: the file at history/audio was overwritten")


def check_rename_patch_restored(root):
    """The driver leaves no trace of its own patch in this process.

    Three of the cases above replace pathlib.Path.rename class-wide. Under pytest
    every driver shares one interpreter, so a leaked patch would not fail here --
    it would fail somewhere else, much later. Identity plus one real rename.
    """
    check(pathlib.Path.rename is _ORIGINAL_RENAME,
          "pathlib.Path.rename was left patched by this driver")
    base = _case(root, "restored")
    src = base / "before.txt"
    src.write_text("payload", encoding="utf-8")
    dst = base / "after.txt"
    try:
        src.rename(dst)
    except Exception as e:  # noqa: BLE001 -- a patched rename would surface here
        failures.append(f"a real Path.rename no longer works after the patches: {e}")
        return
    check(dst.is_file() and not src.exists() and dst.read_text(encoding="utf-8") == "payload",
          "a real Path.rename did not move the file after the patches")


def main():
    root = tempfile.mkdtemp(prefix="tb_archive_migration_")
    try:
        check_both_folders_migrate(root)
        check_one_folder_only(root)
        check_both_exist_untouched(root)
        check_empty_newside_healed(root)
        check_fresh_start_noop(root)
        check_half_finished(root)
        check_cross_volume_exdev(root)
        check_race_loser_benign(root)
        check_unexpected_error_contained(root)
        check_hostile_layouts_sweep(root)
        check_rename_patch_restored(root)
    finally:
        shutil.rmtree(root, ignore_errors=True)

    if SHOW:
        for label, events in shown:
            print(f"--- {label}")
            for e in events:
                print("    " + e)
            if not events:
                print("    (no events)")
        print()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: the legacy-archive migration moves both pairs with their files intact, "
          "heals an empty new side, refuses a populated one without touching a byte, "
          "and degrades to a warning on a simulated cross-volume failure, a lost race, "
          "a non-OSError error and hostile layouts -- never raising")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
