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
counts the `(skipped ...)` notes a driver printed. Under `xvfb-run -a` (what CI
does) the display-gated ones stop skipping.

Stdlib only and Python 3.10+, and it must keep running off Windows: verifying the
Windows tool from a Linux box is the whole point of the ladder.
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DETAIL_WIDTH = 84  # the drivers' OK lines run to ~300 chars; the verdict line is a glance


def run_one(path, verbose):
    """Run one driver as a subprocess; return (passed, seconds, output_lines)."""
    started = time.monotonic()
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
    return proc.wait() == 0, time.monotonic() - started, lines


def detail(lines):
    """The driver's own last word: its final `OK:` line, else its last output line."""
    ok_lines = [ln for ln in lines if ln.startswith("OK:")]
    text = ok_lines[-1] if ok_lines else next(
        (ln for ln in reversed(lines) if ln.strip()), "(no output)")
    return text if len(text) <= DETAIL_WIDTH else text[:DETAIL_WIDTH - 3] + "..."


def main():
    ap = argparse.ArgumentParser(description="Run the off-Windows test ladder.")
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="stream each driver's own output as it runs")
    args = ap.parse_args()

    drivers = sorted(ROOT.glob("test_*.py"))
    if not drivers:
        print("FAIL: no test_*.py drivers found next to run_tests.py")
        return 1

    print(f"Test ladder: {len(drivers)} drivers on Python {sys.version.split()[0]}")
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
        print(f"{status}  {path.name:<32} {secs:5.1f}s  {note}{detail(lines)}")
        if not passed:
            failed.append(path.name)
            if not args.verbose:  # verbose already showed it
                for ln in lines:
                    print("  | " + ln)

    total = time.monotonic() - started
    print()
    if skipped:
        print(f"Note: {skipped} check(s) skipped -- the [n skipped] verdict lines say "
              "which driver; display-gated ones run under `xvfb-run -a`.")
    if failed:
        print(f"FAIL: {len(failed)}/{len(drivers)} drivers failed in {total:.1f}s: "
              f"{', '.join(failed)}")
        return 1
    print(f"OK: all {len(drivers)} drivers pass in {total:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
