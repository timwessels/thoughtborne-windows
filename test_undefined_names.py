#!/usr/bin/env python3
"""Off-Windows guard against the one bug class py_compile cannot see: an undefined
name (#289).

`py_compile` parses a file; it never resolves a name in it. And `thoughtborne.py` --
the largest module, and the one holding the Win32 startup paths -- is imported by no
driver, because importing it pulls in keyboard/pyaudio/win32. Between those two facts
sat a blind spot the size of the program: a `NameError` waiting on a Windows-only
branch was invisible to every automated check the project has. Not hypothetical --
while #269 was built, a local `optout` assignment was replaced and two of its uses
were left standing in `_second_instance_running` (`return not optout`, `if optout:`);
on Windows both would have thrown into that function's fail-open `except` and quietly
switched off the single-instance guard D-004 exists for. A human reader caught it;
this lane catches it in half a second.

What it does:

  - every .py file in the checkout goes through pyflakes. The set is WALKED, not
    listed, so a new module is covered the day it lands: only `.`/`_` directories
    (the maintainer's gitignored workspaces), docs/, sandbox/ and the runtime data
    folders are skipped, and two sanity checks make sure the walk cannot quietly
    collect nothing;
  - FAIL_ON is a deliberately short, closed list of messages that mean the code is
    broken rather than untidy: an undefined name, a local read before assignment, an
    `__all__` entry that does not exist, a star-import (which would make undefined
    names undetectable), a duplicated argument, a `raise NotImplemented` (a TypeError
    at the moment it fires, where the author meant NotImplementedError). It is not
    every message one could argue belongs there -- pyflakes has dozens, and several
    of the rest are real bugs too -- and it is not meant to grow into one: a short
    list someone widens on purpose beats a long one an upgrade widens by itself.
    Everything else pyflakes has an opinion about (unused imports, f-strings without
    placeholders) is counted and named in the OK line but never gates: this guards
    against bugs, not against untidiness, and a check that goes red on untidiness
    goes red on an upgrade too. Gating on message CLASSES rather than message text is
    what makes an unpinned pyflakes safe here -- a renamed class fails loudly at
    import;
  - a file that does not parse counts as a failure, for the whole tree rather than
    for the files someone remembered to name. On CI's 3.10 leg that also catches
    3.11+-only syntax, which the Python floor forbids;
  - the lane proves its own detection power on every run: the #269 mutation, a clean
    twin and a file that does not parse go through the same `check_file()` as the
    sweep, in a tempdir. Without the clean twin the positive case would prove
    nothing; without the broken one, a collector that only overrode `flake()` would
    count an unparseable file as spotless.

What it does NOT do: it resolves names, not types or values, and it says nothing
about a name that exists but holds the wrong thing. It is a floor under the ladder,
not a review.

pyflakes is a test-only dependency, deliberately not in pyproject.toml or
requirements.txt (those are the shipped runtime set, and `test_deps_sync.py` holds
them in lockstep). CI installs it beside soundfile and groq; without it this driver
skips cleanly with a visible note, the way the groq lane does.

Note for a later reader: pyflakes does not honour `# noqa` -- that is flake8/ruff.
It costs nothing while the gate is the short list above, which is why the four
deliberate side-effect imports in `test_audio_stall.py` and
`test_retry_marker_lifecycle.py` still show up in the counted-but-not-gated tally. If
the gate is ever widened to every message, three things become necessary at once: a
way to mark deliberate cases at the site (a small noqa filter here, or a switch to
`ruff check --select F`), a pinned pyflakes version, and a cleanup pass -- see #261.

    python3 test_undefined_names.py           # verify, exit non-zero on any violation
    python3 test_undefined_names.py --show    # also list the counted, non-gating notes
"""
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

# A presence probe, not a try/except around the imports below: an
# `except ModuleNotFoundError` there would also swallow a pyflakes that IS installed
# but restructured or broken, and print "not installed" while the lane ran green on
# nothing -- the exact shape of silent pass this driver exists to prevent. Here the
# skip note means what it says, and every other import problem raises.
HAVE_PYFLAKES = importlib.util.find_spec("pyflakes") is not None

if HAVE_PYFLAKES:
    from pyflakes.api import checkPath
    # Imported by name so a rename in a future pyflakes fails here, loudly, with a
    # traceback -- rather than leaving the gate silently empty.
    from pyflakes.messages import (
        UndefinedName, UndefinedLocal, UndefinedExport,
        ImportStarUsed, ImportStarUsage, DuplicateArgument, RaiseNotImplemented,
    )
    FAIL_ON = (UndefinedName, UndefinedLocal, UndefinedExport,
               ImportStarUsed, ImportStarUsage, DuplicateArgument, RaiseNotImplemented)
else:
    FAIL_ON = ()

ROOT = Path(__file__).resolve().parent
SHOW = "--show" in sys.argv

# Directories holding no checked file: the maintainer's gitignored `_*` workspaces
# and every dot-dir (.git, .venv -- third-party code) are handled by the prefix rule
# below; these are the named rest. Same policy as [tool.pytest.ini_options]
# norecursedirs in pyproject.toml, which exists for pytest's convenience while this
# one is the lane's reach -- whoever changes one checks the other.
SKIP_DIRS = {"docs", "sandbox", "history", "dist", "build", "node_modules"}

failures = []


def check(cond, msg):
    if not cond:
        failures.append(msg)


class _Collector:
    """Keeps pyflakes' findings instead of printing them.

    Duck-typed rather than a `pyflakes.reporter.Reporter` subclass on purpose: these
    three methods are the whole interface `pyflakes.api.check` calls, and inheriting
    would let a future fourth one write into a stream nobody reads. Unknown here
    means an AttributeError, which is loud.
    """

    def __init__(self):
        self.messages = []
        self.errors = []

    def flake(self, message):
        self.messages.append(message)

    def syntaxError(self, filename, msg, lineno, offset, text):
        self.errors.append(f"{filename}:{lineno or '?'}: does not parse: {msg}")

    def unexpectedError(self, filename, msg):
        self.errors.append(f"{filename}: pyflakes could not process it: {msg}")


def check_file(path):
    """(gating, other, errors) for one file.

    The ONE code path the sweep and the self-check below share -- which is what makes
    the self-check evidence about this lane rather than about pyflakes.
    """
    reporter = _Collector()
    checkPath(str(path), reporter)
    gating = [m for m in reporter.messages if isinstance(m, FAIL_ON)]
    other = [m for m in reporter.messages if not isinstance(m, FAIL_ON)]
    return gating, other, reporter.errors


def python_files():
    """Every .py file in the checkout the lane is responsible for.

    Pruned while walking, not filtered afterwards: an rglob would descend into .venv/
    and the maintainer's gitignored workspaces first, which on a Windows drive seen
    through WSL costs ten times the checking itself.
    """
    found = []
    for base, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs
                   if d not in SKIP_DIRS and not d.startswith((".", "_"))]
        found.extend(Path(base) / n for n in names if n.endswith(".py"))
    return sorted(found)


def describe(message):
    """A pyflakes message as `file:line:col: text`, relative to the checkout."""
    try:
        where = Path(message.filename).resolve().relative_to(ROOT)
    except ValueError:
        where = Path(message.filename).name
    return f"{where}:{message.lineno}:{message.col + 1}: {message.message % message.message_args}"


def sweep():
    """The lane proper: the whole checkout through pyflakes.

    Returns (number of files swept, number of counted-but-not-gating notes)."""
    files = python_files()
    check(len(files) >= 30,
          f"only {len(files)} Python file(s) collected -- the walk is broken, and a "
          f"lane that sweeps nothing passes for exactly the same reason a clean tree does")
    check(any(f.name == "thoughtborne.py" for f in files),
          "thoughtborne.py is not in the swept set -- it is the module this lane exists for")

    notes = []
    for path in files:
        gating, other, errors = check_file(path)
        notes.extend(other)
        for e in errors:
            failures.append(e)
        for m in gating:
            failures.append(describe(m))
    if SHOW:
        print(f"Swept {len(files)} Python file(s); {len(notes)} counted, non-gating note(s):")
        for m in notes:
            print("  " + describe(m))
        print()
    return len(files), len(notes)


# The #269 shape, and the two controls that keep the positive case meaningful. They
# live in a tempdir, never in the checkout: a file with an undefined name in the repo
# root would be collected by the sweep above and turn the lane red on itself.
MUTANT = '''\
"""Fixture: the #269 shape -- the local assignment gone, its two uses left standing."""


def second_instance_running(env):
    if optout:
        return False
    return not optout
'''

CLEAN = '''\
"""Fixture: the same function with the name defined -- the negative control."""


def second_instance_running(env):
    optout = bool(env)
    if optout:
        return False
    return not optout
'''

BROKEN = '''\
"""Fixture: a file that does not parse."""


def second_instance_running(env)
    return env
'''


def self_check():
    """Prove on every run that the wiring still detects what it is here for."""
    with tempfile.TemporaryDirectory(prefix="tb_undefined_names_") as tmp:
        mutant = Path(tmp) / "mutant_module.py"
        mutant.write_text(MUTANT, encoding="utf-8")
        gating, _other, errors = check_file(mutant)
        check(not errors, f"the #269 fixture did not even parse: {errors}")
        flagged = sorted({m.message_args[0] for m in gating if isinstance(m, UndefinedName)})
        check(len(gating) == 2 and flagged == ["optout"],
              f"the lane no longer flags the #269 mutation -- it would have missed the "
              f"bug it was built for; got {[describe(m) for m in gating]}")

        clean = Path(tmp) / "clean_module.py"
        clean.write_text(CLEAN, encoding="utf-8")
        gating, other, errors = check_file(clean)
        check(not gating and not other and not errors,
              f"the clean control was flagged, so the positive case above proves nothing: "
              f"{[describe(m) for m in gating + other] + errors}")

        broken = Path(tmp) / "broken_module.py"
        broken.write_text(BROKEN, encoding="utf-8")
        gating, _other, errors = check_file(broken)
        check(errors,
              "a file that does not parse came back clean -- pyflakes reports that "
              "through syntaxError(), not flake(), so a collector that lost it would "
              "count every unparseable file as spotless")


def main():
    if not HAVE_PYFLAKES:
        print("      (skipped: pyflakes is not installed here -- `pip install pyflakes`; "
              "CI installs it, so the gate still runs on every push)")
        print("OK: undefined-name lane skipped, pyflakes is not installed")
        return 0

    self_check()
    swept, notes = sweep()

    if failures:
        print(f"FAIL: {len(failures)} violation(s)")
        for f in failures:
            print("  " + f)
        return 1
    print(f"OK: no undefined names in {swept} Python files ({notes} other pyflakes "
          f"note(s), counted but not gated), and the #269 mutation is still detected")
    return 0


def test_all():
    """The pytest entry point (#242): the whole driver as one collected test."""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
