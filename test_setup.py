#!/usr/bin/env python3
"""Structural guard for the #76 install mechanics (setup.ps1 / setup.bat).

Runs on plain Python -- no Windows, no PowerShell -- so it is a deliberately
STATIC check: it reads the shipped installer scripts as text/bytes and asserts
their structure and hard invariants. It does NOT execute PowerShell (pwsh is not
available on the Linux dev box), so real behavior -- the execution-policy bypass
on a Restricted client, the uv bootstrap and `uv sync`, the ZIP fetch/extract/
strip, the actual DryRun *output*, shortcut creation and the "Run as
administrator" verb, the guard actually refusing -- is out of reach here and
belongs to the hands-on / Windows-Sandbox `test` issue (see sandbox/). This
guard is a drift alarm for the invariants, not a correctness proof.

    python3 test_setup.py           # verify, exit non-zero on failure
    python3 test_setup.py --show    # also print the parsed denylist + shortcuts

Sibling of test_console_ui.py / test_hotkey_overrides.py: a CASES list, PASS/
FAIL print, non-zero exit on failure.
"""
import re
import sys
from fnmatch import fnmatch
from pathlib import Path

REPO = Path(__file__).resolve().parent
SHOW = "--show" in sys.argv


def read_bytes(name):
    return (REPO / name).read_bytes()


def read_text(name):
    # setup.ps1 / setup.bat are ASCII by invariant; decode strictly so a stray
    # non-ASCII byte surfaces here too, not just in the byte-level case.
    return (REPO / name).read_text(encoding="ascii")


_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff",
         b"\xff\xfe", b"\xfe\xff")


# ======================================================================
# Hard invariants
# ======================================================================

def test_ascii_only_no_bom():
    for name in ("setup.ps1", "setup.bat"):
        data = read_bytes(name)
        for bom in _BOMS:
            assert not data.startswith(bom), f"{name}: starts with a BOM ({bom!r})"
        bad = [i for i, b in enumerate(data) if b >= 0x80]
        assert not bad, f"{name}: non-ASCII byte(s) at offset(s) {bad[:5]}"


def test_no_ungated_exit():
    # iex-safety: an `exit` reached via  irm | iex  closes the user's whole
    # PowerShell session, so setup.ps1 must unwind with `return` on every path the
    # pipe lane can reach. The ONE permitted exit is the process-exit-code signal
    # for the setup.bat (-File) lane, gated behind THOUGHTBORNE_FROM_BAT -- an env
    # var only setup.bat sets, never the pipe. So: no line-starting `exit`, and any
    # `exit $...` / `{ exit` embedded mid-line must carry that gate on the same line.
    saw_gated = False
    for i, line in enumerate(read_text("setup.ps1").splitlines(), 1):
        s = line.strip()
        is_exit_stmt = (s.lower().startswith("exit")
                        or re.search(r"\bexit\b\s+\$", s) is not None
                        or re.search(r"\{\s*exit\b", s) is not None)
        if not is_exit_stmt:
            continue
        assert "THOUGHTBORNE_FROM_BAT" in s, \
            f"setup.ps1:{i}: ungated exit -- would close the iex session: {s!r}"
        saw_gated = True
    assert saw_gated, "expected the THOUGHTBORNE_FROM_BAT-gated setup.bat-lane exit, found none"


def test_denylist_covers_user_data():
    text = read_text("setup.ps1")
    m = re.search(r"#\s*DENYLIST-BEGIN(.*?)#\s*DENYLIST-END", text, re.S)
    assert m, "DENYLIST-BEGIN/END sentinels not found in setup.ps1"
    globs = re.findall(r"'([^']+)'", m.group(1))
    assert globs, "no denylist patterns parsed between the sentinels"
    user_data = [".env", ".env.local", ".env.dev.local",
                 "personal_settings.json", "history",
                 "thoughtborne.log", "thoughtborne.log.1", ".venv",
                 "voice_archive", "text_archive"]
    for path in user_data:
        assert any(fnmatch(path, g) for g in globs), \
            f"user-data name {path!r} matched no denylist glob {globs}"
    # ...but the shipped template MUST survive the copy. .env.example is not user
    # data, and a too-broad glob like `.env*` would wrongly eat it (#76 finding 10).
    assert not any(fnmatch(".env.example", g) for g in globs), \
        f".env.example is caught by a denylist glob {globs} -- the template would be dropped"


# ======================================================================
# Structural presence (drift alarms -- behavior lives in the sandbox)
# ======================================================================

def test_fingerprint_refusal_present():
    text = read_text("setup.ps1")
    assert "pyproject.toml" in text, "no pyproject.toml fingerprint reference"
    assert re.search(r"thoughtborne", text), "no thoughtborne name reference"
    assert "thoughtborne.py" in text, "no thoughtborne.py fingerprint reference"
    assert re.search(r"refus", text, re.I), "no refuse path for a non-Thoughtborne dir"


def test_running_instance_guard_present():
    text = read_text("setup.ps1")
    assert "thoughtborne.log" in text, "running-instance guard: no log reference"
    assert "Program ended" in text, "running-instance guard: no 'Program ended' check"
    assert "LastWriteTime" in text, "running-instance guard: no mtime/heartbeat check"


def test_dryrun_present():
    text = read_text("setup.ps1")
    assert re.search(r"param\s*\(\s*\[switch\]\s*\$DryRun", text), \
        "no [switch]$DryRun param declared"
    assert "$env:THOUGHTBORNE_DRYRUN" in text, "$env:THOUGHTBORNE_DRYRUN not honored"
    gates = len(re.findall(r"if\s*\(\s*\$DryRun\s*\)", text))
    assert gates >= 4, f"expected several $DryRun-gated side effects, found {gates}"
    # The gated side effects must actually exist to be gated.
    assert "DownloadFile" in text, "no download step to gate"
    assert re.search(r"&\s*\$uv\s+sync", text), "no 'uv sync' step to gate"


def test_shortcuts():
    text = read_text("setup.ps1")
    names = re.findall(r"@\{\s*Name\s*=", text)
    assert len(names) == 2, f"expected exactly two shortcuts, found {len(names)}"
    assert "Name = 'Thoughtborne'" in text, "missing 'Thoughtborne' shortcut"
    assert "Name = 'Thoughtborne Settings'" in text, "missing 'Thoughtborne Settings' shortcut"
    assert "'Thoughtborne.bat'" in text, "shortcut does not reference Thoughtborne.bat"
    assert "'Thoughtborne-Settings.bat'" in text, "shortcut does not reference Thoughtborne-Settings.bat"
    assert "cmd.exe" in text, "shortcut target is not cmd.exe"
    assert "favicon.ico" in text, "shortcut carries no favicon.ico icon"
    assert "'/c \"'" in text, "shortcut does not use the cmd /c \"...\" form (#140)"


def test_no_secret_collection():
    # respects D-002: the settings app is the only config writer. setup.ps1 must
    # never collect a key or write a config file.
    text = read_text("setup.ps1")
    assert "Read-Host" not in text, "setup.ps1 must not prompt for input (no Read-Host)"
    assert "Set-Content" not in text, "setup.ps1 must not write files (no Set-Content)"
    assert "Out-File" not in text, "setup.ps1 must not write files (no Out-File)"
    for key in ("SONIOX_API_KEY", "GROQ_API_KEY"):
        assert key not in text, f"setup.ps1 must not reference {key}"


def test_launcher_astral_fallback():
    astral = r"%USERPROFILE%\.local\bin\uv.exe"
    for name in ("Thoughtborne.bat", "Thoughtborne-Settings.bat"):
        assert astral in read_text(name), \
            f"{name}: no Astral per-user uv fallback ({astral})"


def test_setup_bat_wrapper():
    text = read_text("setup.bat")
    assert "%~dp0setup.ps1" in text, "setup.bat does not invoke the co-located setup.ps1"
    assert "-ExecutionPolicy Bypass" in text, "setup.bat does not pass -ExecutionPolicy Bypass"
    assert "-File" in text, "setup.bat does not use -File"
    assert "%*" in text, "setup.bat does not forward its args (%*)"


def test_setup_bat_error_handling():
    # The double-click / ZIP lane must let the user READ a failure (the cmd window
    # would otherwise close instantly) and hand a real exit code back: a -File run
    # reports errorlevel 0 unless the script exits, so setup.bat signals setup.ps1
    # via THOUGHTBORNE_FROM_BAT and pauses on a nonzero code (#76 finding 2).
    text = read_text("setup.bat")
    assert "THOUGHTBORNE_FROM_BAT" in text, \
        "setup.bat does not signal the -File lane (THOUGHTBORNE_FROM_BAT) for a real exit code"
    assert "errorlevel" in text.lower(), "setup.bat does not branch on the exit code (errorlevel)"
    assert "pause" in text.lower(), "setup.bat does not pause on failure (error would be unreadable)"


def test_setup_ps1_bat_lane_exit_signal():
    # The mirror of test_no_ungated_exit: the single gated exit must actually exist,
    # and the success/dry-run paths must set LASTEXITCODE=0 so the signal is never a
    # stale value from the user's session (#76 findings 2 + 12).
    text = read_text("setup.ps1")
    assert re.search(r"THOUGHTBORNE_FROM_BAT.*exit\s+\$Global:LASTEXITCODE", text), \
        "setup.ps1 has no THOUGHTBORNE_FROM_BAT-gated 'exit $Global:LASTEXITCODE' signal"
    assert re.search(r"\$Global:LASTEXITCODE\s*=\s*0", text), \
        "setup.ps1 never sets LASTEXITCODE=0 on success -- a stale value could leak (#76 finding 12)"


def test_gitignore_covers_sandbox_secrets():
    # The sandbox harness writes a real-key temp.env and per-run out-*/ folders into
    # the tracked sandbox/ dir, and a `local` run drops throwaway setup.ps1/.bat
    # copies there. None are caught by the plain `.env` / `*.log` rules, so a stray
    # `git add -A` would stage the key unless pinned here (#76 finding 1).
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pat in ("sandbox/temp.env", "sandbox/out-*", "sandbox/setup.ps1", "sandbox/setup.bat"):
        assert pat in gi, f".gitignore is missing {pat!r} -- a sandbox artifact could be committed"


CASES = [
    test_ascii_only_no_bom,
    test_no_ungated_exit,
    test_denylist_covers_user_data,
    test_fingerprint_refusal_present,
    test_running_instance_guard_present,
    test_dryrun_present,
    test_shortcuts,
    test_no_secret_collection,
    test_launcher_astral_fallback,
    test_setup_bat_wrapper,
    test_setup_bat_error_handling,
    test_setup_ps1_bat_lane_exit_signal,
    test_gitignore_covers_sandbox_secrets,
]


def main():
    if SHOW:
        text = read_text("setup.ps1")
        m = re.search(r"#\s*DENYLIST-BEGIN(.*?)#\s*DENYLIST-END", text, re.S)
        globs = re.findall(r"'([^']+)'", m.group(1)) if m else []
        print("denylist globs:", ", ".join(globs))
        print("shortcuts:", ", ".join(re.findall(r"Name\s*=\s*'([^']+)'", text)[:2]))
        print()

    failures = []
    for case in CASES:
        try:
            case()
            print(f"PASS  {case.__name__}")
        except AssertionError as e:
            failures.append((case.__name__, str(e)))
            print(f"FAIL  {case.__name__}: {e}")
        except Exception as e:  # a crash is also a failure
            failures.append((case.__name__, f"{type(e).__name__}: {e}"))
            print(f"ERROR {case.__name__}: {type(e).__name__}: {e}")

    if failures:
        print(f"\nFAIL: {len(failures)}/{len(CASES)} case(s) failed")
        return 1
    print(f"\nOK: all {len(CASES)} setup-mechanics cases pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
