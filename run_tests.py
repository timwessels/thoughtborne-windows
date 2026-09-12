#!/usr/bin/env python3
"""Runs the whole off-Windows test ladder in one command.

The ladder is a set of standalone drivers (`test_*.py` beside this file), each a
plain script with its own main() that prints a final `OK: ...` line and exits 0 --
deliberately no pytest, so every driver still runs and reads alone. What was
missing was a single entry point: nothing ran them together, so nothing could gate
a push on them. This is that entry point, and `.github/workflows/tests.yml` runs it
on every push and pull request.

    python3 run_tests.py          # one verdict line per driver, then a summary
    python3 run_tests.py -v       # plus each driver's own output, as it runs

Exit status is 0 only when every driver passed; a failing driver's full captured
output is printed under its verdict line, so a CI log carries the whole story
without a re-run.

Some checks gate themselves on a display or on a filesystem that enforces chmod and
then skip -- and a driver's closing `OK:` line reads the same either way, so a
skipped check would otherwise look like a passed one. The verdict line therefore
counts the `(skipped ...)` notes a driver printed.

So the display-gated ones need not skip, this file re-execs itself once under
`xvfb-run` wherever that can give Python a display. An existing X server is a reason
to take over rather than to stand back: a window manager can clamp the geometry steps
the display checks drive, and a clamped step measures the window manager instead of the
app -- one lane skips when that happens, and #301 reports another going red that way on
a box with a real display. Neither shows up on every box, and that is the argument: Xvfb
has no window manager, so the same command means the same thing everywhere, which is
worth more than any single failure it avoids. The `Display:` line under the header says
which of the three cases a run was in, so a green run never leaves open whether they
ran. Set THOUGHTBORNE_LADDER_XVFB to anything and the ladder leaves the screen as it
finds it -- the way to aim it at a real X server, or to get today's skipping back. One
side effect: xvfb-run runs its command as `"$@" 2>&1`, so the ladder's stderr arrives
on stdout.

The ladder also watches one file it must never touch: the checkout's own
`thoughtborne.log`, whose mtime is the heartbeat the *While the tool is running* gate in
AGENTS.md reads. A driver that moves it fails, by name (#267).

Stdlib only and Python 3.10+, and it must keep running off Windows: verifying the
Windows tool from a Linux box is the whole point of the ladder.
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DETAIL_WIDTH = 84  # the drivers' OK lines run to ~300 chars; the verdict line is a glance
CHECKOUT_LOG = ROOT / "thoughtborne.log"    # watched, never written -- see run_one
XVFB_FLAG = "THOUGHTBORNE_LADDER_XVFB"      # the human's escape hatch, at any value
TAKEN_ARG = "--under-xvfb"                  # this file's marker on the run it re-execs
PROBE_SECONDS = 60   # a ceiling on the display probe below, not a budget
# Pinned rather than left to xvfb-run's default, which is a distribution's choice.
# `_size_window` clamps the settings window's minimum width to the screen minus 80, and
# once that floor falls under what the six tab labels need, clam squeezes and clips them
# silently -- the #281 lane then goes red over the screen rather than over the app
# (measured: red up to 913px wide, green from 914). 1280x1024 is what xvfb-run defaults
# to here, so the pin changes nothing today and keeps the threshold far away. The depth
# has to stay in the string: without it Xvfb rejects the screen and starts no server.
SCREEN = "-screen 0 1280x1024x24"
HEARTBEAT_FAULT = (
    "this driver moved the checkout's own thoughtborne.log -- its mtime is what the "
    "*While the tool is running* gate in AGENTS.md reads as the tool's heartbeat, and "
    "no test run may fake that signal (#267). Either a lane built the app outside its "
    "sandbox, or the tool really is running from this checkout: if so, stop it and "
    "re-run.")


def take_over_display(under_xvfb):
    """Put the whole ladder under `xvfb-run`; return why that did not happen.

    Returns only when it did not: on success os.execvp replaces this process, so the
    inner run inherits this one's stdout, terminal, stdin, argv and exit status. It
    carries TAKEN_ARG, which is both the re-entry guard and what the `Display:` line
    reads -- the escape hatch below stops the exec but says nothing about a run's screen.
    """
    if under_xvfb:
        return "this run is already the takeover"
    if os.environ.get(XVFB_FLAG):
        return f"{XVFB_FLAG} is set"
    xvfb = shutil.which("xvfb-run")
    if not xvfb:
        return "xvfb-run is not installed"
    inner = [xvfb, "-a", "-s", SCREEN, sys.executable]
    # Probe before the exec, which is one-way: xvfb-run can be installed and still not
    # deliver a display (no Xvfb binary, an unwritable temp dir, no _tkinter). Without
    # the probe such a box gets a run that claims the display and then exits 1 having
    # proved nothing -- where falling through to today's skipping is the honest answer.
    # A probe that hangs is treated as one that failed, and for the same reason: it opens
    # a window and closes it, so a wedged xvfb-run would otherwise hang the ladder BEFORE
    # its first line, which is the one outcome that says nothing at all. The ceiling is
    # set where no cold or loaded box can reach it, not where a healthy probe lands.
    try:
        probe = subprocess.run(inner + ["-c", "import tkinter; tkinter.Tk().destroy()"],
                               capture_output=True, timeout=PROBE_SECONDS)
    except subprocess.TimeoutExpired:
        return f"xvfb-run did not answer the display probe in {PROBE_SECONDS}s"
    if probe.returncode != 0:
        return "xvfb-run cannot give this Python a display"
    os.execvp(xvfb, inner + [str(Path(__file__).resolve()), TAKEN_ARG, *sys.argv[1:]])


def display_line(under_xvfb, reason):
    """The one line that says whether the display-gated checks ran for real.

    It reads this file's own flag, never XVFB_FLAG: that one is documented as "set it to
    anything", so a person is free to set it to whatever value a value-test would take
    for the takeover -- and the line would then claim a screen that is not there.
    """
    if under_xvfb:
        return "Display: xvfb-run -- every display-gated check runs."
    return (f"Display: {os.environ.get('DISPLAY') or 'none'} -- {reason}, "
            "so display-gated checks run there or skip.")


def log_stamp():
    """The checkout log's mtime, or None where there is no log -- creating one counts."""
    try:
        return CHECKOUT_LOG.stat().st_mtime_ns
    except OSError:
        return None


def run_one(path, verbose):
    """Run one driver as a subprocess; return (passed, seconds, output_lines).

    The checkout's own thoughtborne.log is sampled around each driver rather than once
    around the whole run: the same catch either way, but this way the verdict line names
    the driver that moved it.
    """
    started = time.monotonic()
    log_before = log_stamp()
    # stderr folded into stdout so a traceback lands in order with the driver's own
    # prints, and decoded leniently -- the console-charset driver can emit CP437.
    proc = subprocess.Popen(
        [sys.executable, path.name], cwd=str(ROOT),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    lines = []
    for line in proc.stdout:
        lines.append(line.rstrip("\n"))
        if verbose:
            print("  | " + lines[-1])
    passed = proc.wait() == 0
    if log_stamp() != log_before:
        passed = False
        lines.append(HEARTBEAT_FAULT)
    return passed, time.monotonic() - started, lines


def detail(lines, passed):
    """The driver's own last word: its final `OK:` line, else its last output line.

    Only a passing driver gets its `OK:` line: a driver the ladder failed over the
    heartbeat exited 0 and printed one, and the verdict line would otherwise read as if
    nothing had happened.
    """
    ok_lines = [ln for ln in lines if ln.startswith("OK:")] if passed else []
    text = ok_lines[-1] if ok_lines else next(
        (ln for ln in reversed(lines) if ln.strip()), "(no output)")
    return text if len(text) <= DETAIL_WIDTH else text[:DETAIL_WIDTH - 3] + "..."


def main():
    ap = argparse.ArgumentParser(description="Run the off-Windows test ladder.")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="stream each driver's own output as it runs")
    # Set by the re-exec in take_over_display and by nothing else: this run's own record
    # that it IS the takeover. Hidden because there is nothing here for a person to type.
    ap.add_argument(TAKEN_ARG, action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()

    reason = take_over_display(args.under_xvfb)   # returns only when it did not take over

    drivers = sorted(ROOT.glob("test_*.py"))
    if not drivers:
        print("FAIL: no test_*.py drivers found next to run_tests.py")
        return 1

    print(f"Test ladder: {len(drivers)} drivers on Python {sys.version.split()[0]}")
    print(display_line(args.under_xvfb, reason))
    print()
    failed = []
    skipped = 0
    started = time.monotonic()
    for path in drivers:
        if args.verbose:
            print("--- " + path.name)
        passed, secs, lines = run_one(path, args.verbose)
        notes = sum(1 for ln in lines if ln.lstrip().startswith("(skipped"))
        skipped += notes
        note = f"[{notes} skipped] " if notes else ""
        status = "PASS" if passed else "FAIL"
        print(f"{status}  {path.name:<32} {secs:5.1f}s  {note}{detail(lines, passed)}")
        if not passed:
            failed.append(path.name)
            if not args.verbose:  # verbose already showed it
                for ln in lines:
                    print("  | " + ln)

    total = time.monotonic() - started
    print()
    if skipped:
        print(f"Note: {skipped} check(s) skipped -- the [n skipped] verdict lines say "
              "which driver; the Display line above says what the display was.")
    if failed:
        print(f"FAIL: {len(failed)}/{len(drivers)} drivers failed in {total:.1f}s: "
              f"{', '.join(failed)}")
        return 1
    print(f"OK: all {len(drivers)} drivers pass in {total:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
