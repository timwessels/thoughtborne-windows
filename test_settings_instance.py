#!/usr/bin/env python3
"""Off-Windows verification of the settings single-instance guard (#196, D-009).

`settings_instance` is stdlib-only: its Windows calls (`ctypes.WinDLL(...)` and the
mutex/window APIs) live lazily *inside* functions guarded by `os.name == "nt"`, never
at module top, so `import settings_instance` succeeds on plain Python and the two
Windows entry points fail open (never raise, never claim a running instance)
off-Windows. What can be checked without Windows:

  - the module imports at all AND binds no ctypes-family name at module top. `import
    ctypes` is stdlib and succeeds everywhere; what would break off-Windows (and the
    D-005 system-Python rescue lane) is a module-top `ctypes.WinDLL`/`windll` call --
    actually loading a Windows DLL. This test proves those stay lazy by asserting the
    module never binds `ctypes` (or `wintypes`) into its own namespace;
  - `settings_window_titles()` returns exactly the four localized titles the window
    can carry (settings/first-run x DE/EN), computed FROM the string table so the
    focus match can't silently drift from what the window sets;
  - `SETTINGS_MUTEX_NAME` is distinct from the tool's own mutex name -- a static
    guard so a later copy-paste can't make the tool and the app block each other;
  - off-Windows, `create_instance_mutex()` -> (None, False) and
    `focus_existing_settings_window()` -> FOCUS_NOT_FOUND (fail-open; a guard fault
    never costs a launch -- since #203 the focus path returns a category string, not
    a bool, so the caller can log found/raised/focused/refused);
  - the FOCUS_* outcome categories are distinct, non-empty strings (#203).

The real ctypes mutex/focus behavior on Windows is hands-on (#199).

    python3 test_settings_instance.py    # verify, exit non-zero on any violation
"""
import sys

import settings_instance as si
import settings_strings as strings

# The tool's single-instance mutex name (D-004), hard-coded here as a fixture: the
# whole point is that the settings mutex must NEVER equal it. Kept literal so a rename
# of either name that accidentally aligned them still trips this test.
TOOL_MUTEX_NAME = "Thoughtborne-SingleInstance"

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


def test_ctypes_stays_lazy():
    # The real lazy-import contract (not "import ctypes breaks off-Windows" -- it
    # doesn't; it's stdlib): the module must never bind a ctypes-family name into its
    # own namespace, proving the WinDLL/windll calls live inside the os.name-guarded
    # functions. Robust against a transitive import having pulled ctypes into
    # sys.modules -- hasattr on the module object checks the actual top-level binding.
    check(not hasattr(si, "ctypes"),
          "settings_instance binds `ctypes` at module top -- the WinDLL calls must "
          "stay lazy inside the Windows functions (D-005 rescue lane)")
    check(not hasattr(si, "wintypes"),
          "settings_instance binds `wintypes` at module top -- keep the ctypes "
          "family lazy inside the Windows functions")


def test_titles_track_the_string_table():
    got = si.settings_window_titles()
    # A tuple, sorted, no duplicates.
    check(isinstance(got, tuple), f"settings_window_titles() is not a tuple: {type(got)}")
    check(len(got) == len(set(got)), f"duplicate titles: {got}")
    check(list(got) == sorted(got), f"titles not sorted: {got}")

    # Exactly the four key x language combinations, taken from the live string table
    # (no hardcode in the helper): settings/first-run x DE/EN.
    expected = {strings.t(k, lang)
                for k in ("app.title.settings", "app.title.firstrun")
                for lang in ("de", "en")}
    check(set(got) == expected,
          f"titles do not match the string table.\n  got:      {sorted(got)}\n"
          f"  expected: {sorted(expected)}")
    check(len(got) == 4, f"expected 4 distinct titles, got {len(got)}: {got}")

    # A human-readable anchor for the current known titles -- both twins, both
    # brands. If the app is ever renamed this fails deliberately, flagging that the
    # focus-match set changed and wants a conscious look.
    known = {"Thoughtborne Settings", "Thoughtborne Setup",
             "Thoughtborne-Einstellungen", "Thoughtborne-Einrichtung"}
    check(set(got) == known,
          f"the known-titles anchor no longer matches: {sorted(got)}")


def test_mutex_name_is_distinct():
    check(si.SETTINGS_MUTEX_NAME != TOOL_MUTEX_NAME,
          "SETTINGS_MUTEX_NAME equals the tool's mutex name -- they would block each "
          "other (D-004/D-009)")
    check(isinstance(si.SETTINGS_MUTEX_NAME, str) and si.SETTINGS_MUTEX_NAME,
          "SETTINGS_MUTEX_NAME is not a non-empty string")
    # Session-scoped, like the tool's mutex: no Global\ prefix.
    check(not si.SETTINGS_MUTEX_NAME.startswith("Global\\"),
          "SETTINGS_MUTEX_NAME must stay session-scoped (no Global\\ prefix)")


def test_windows_functions_fail_open_off_windows():
    # These run their real bodies only on Windows; off-Windows they must short-circuit
    # to a fail-open answer without importing ctypes or raising.
    check(si.create_instance_mutex() == (None, False),
          "create_instance_mutex() is not fail-open off-Windows")
    # Since #203 the focus path returns a FOCUS_* category, not a bool; off-Windows it
    # fails open to FOCUS_NOT_FOUND (no window, guard never costs a launch).
    check(si.focus_existing_settings_window() == si.FOCUS_NOT_FOUND,
          "focus_existing_settings_window() is not fail-open (FOCUS_NOT_FOUND) "
          "off-Windows")


def test_focus_outcome_constants_are_distinct_strings():
    # The caller logs these verbatim (#203, D-009), so they must be distinct, non-empty
    # strings -- a copy-paste collision would blur two real outcomes in the log.
    cats = [si.FOCUS_NOT_FOUND, si.FOCUS_RAISED, si.FOCUS_FOCUSED, si.FOCUS_REFUSED]
    for c in cats:
        check(isinstance(c, str) and c, f"FOCUS_* category is not a non-empty string: {c!r}")
    check(len(set(cats)) == len(cats), f"FOCUS_* categories are not all distinct: {cats}")


def main():
    test_ctypes_stays_lazy()
    test_titles_track_the_string_table()
    test_mutex_name_is_distinct()
    test_windows_functions_fail_open_off_windows()
    test_focus_outcome_constants_are_distinct_strings()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print("OK: lazy import, the four titles track the string table, a distinct "
          "session-scoped mutex name, distinct FOCUS_* outcome categories, and "
          "off-Windows fail-open all pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
