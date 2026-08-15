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
import xml.etree.ElementTree as ET
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

# #181: the sandbox install-verification harness files.
WSB = "sandbox/thoughtborne-install-test.wsb"
VERIFY = "sandbox/verify-in-sandbox.ps1"
LAUNCHER = "sandbox/run-sandbox.ps1"
WSB_HOST_PLACEHOLDER = "SANDBOX_HOSTFOLDER_ABS_PATH"


# ======================================================================
# Hard invariants
# ======================================================================

def test_ascii_only_no_bom():
    # Thoughtborne.bat joins the installer scripts here because the #188 ZIP-lane
    # guard adds new echo/comment text to it, and the cmd default codepage garbles
    # non-ASCII (see the file's own header comment).
    for name in ("setup.ps1", "setup.bat", "Thoughtborne.bat"):
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
                 "personal_settings.json", "runtime_state.json", "history",
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


def test_zip_lane_guard():
    # #188: a fail-open guard at the top of Thoughtborne.bat warns when the tool is
    # launched from an unpacked release ZIP (git archive = a clone minus .git) sitting
    # in a download folder next to setup.bat, so its .venv/history/.env do not scatter
    # there. Static drift alarm for the three AND-chained conditions, a pause, and the
    # fail-open structure (the guard jumps nowhere). That installed copies and git
    # clones actually stay silent is behavior for the sandbox / hands-on lane.
    text = read_text("Thoughtborne.bat")
    m = re.search(r'(if /I not "%~dp0".*?\n\))', text, re.S)
    assert m, "no ZIP-lane guard if-block found in Thoughtborne.bat"
    guard = m.group(1)
    # the three detection components, AND-chained on the if line
    assert r'"%LOCALAPPDATA%\Programs\Thoughtborne\"' in guard, \
        r"guard does not compare against the install dir %LOCALAPPDATA%\Programs\Thoughtborne"
    assert r'if not exist "%~dp0.git\"' in guard, \
        "guard does not check for an absent .git directory"
    assert r'if exist "%~dp0setup.bat"' in guard, \
        "guard does not check for setup.bat sitting beside it"
    # it pauses so the note is readable, then continues
    assert "pause" in guard, "guard does not pause for the note to be read"
    # fail-open: the guard block must never abort -- no exit, no goto out of it
    assert not re.search(r"\bexit\b", guard, re.I), \
        "guard contains an 'exit' -- it must fail open (one keypress continues)"
    assert "goto" not in guard.lower(), \
        "guard contains a 'goto' -- it must fall through, never jump away"


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


def test_inplace_wrapper_protected():
    # #157 (D-007): the paste-free in-place update must not overwrite the setup.bat
    # cmd.exe is still streaming by byte offset -- that would misparse its tail.
    # setup.ps1 skips setup.bat from the copy when the copy target is the script's
    # own folder ($PSScriptRoot == $installDir, the in-place lane). Drift alarm only
    # -- the real update-lane behavior is a sandbox / hands-on `test`-issue check.
    text = read_text("setup.ps1")
    assert "$PSScriptRoot" in text, "no $PSScriptRoot reference -- cannot detect the in-place lane"
    assert "ExcludeName" in text, "Copy-TreeWithDenylist has no ExcludeName exclusion path"
    assert re.search(r"@\(\s*'setup\.bat'\s*\)", text), \
        "setup.bat is not the excluded name on the in-place lane"


def test_gitignore_covers_sandbox_secrets():
    # The sandbox harness writes a real-key temp.env and per-run out-*/ folders into
    # the tracked sandbox/ dir, and a `local` run drops throwaway setup.ps1/.bat
    # copies there. None are caught by the plain `.env` / `*.log` rules, so a stray
    # `git add -A` would stage the key unless pinned here (#76 finding 1).
    gi = (REPO / ".gitignore").read_text(encoding="utf-8")
    for pat in ("sandbox/temp.env", "sandbox/out-*", "sandbox/setup.ps1",
                "sandbox/setup.bat", "sandbox/*.local.wsb"):
        assert pat in gi, f".gitignore is missing {pat!r} -- a sandbox artifact could be committed"


# ======================================================================
# #181 sandbox install-verification harness (drift alarms -- behavior
# lives in the sandbox / hands-on, same as the rest of this file)
# ======================================================================

def test_wsb_template_portable():
    # The committed .wsb must ship portable -- no maintainer-specific absolute host
    # path, no __EDIT_ME__. run-sandbox.ps1 fills the real path into a %TEMP% copy
    # at run time; the tracked template carries only the placeholder token.
    wsb = read_text(WSB)
    assert "__EDIT_ME__" not in wsb, "sandbox .wsb still carries __EDIT_ME__"
    # [^<]* (not .*? with DOTALL): the class cannot span another tag, so a comment
    # that mentions the element cannot swallow the match -- robust by construction.
    m = re.search(r"<HostFolder>([^<]*)</HostFolder>", wsb)
    assert m, "sandbox .wsb has no <HostFolder> element"
    host = m.group(1).strip()
    assert not re.match(r"^[A-Za-z]:\\", host), \
        f"committed .wsb carries an absolute host path {host!r} -- must stay a placeholder"
    assert host == WSB_HOST_PLACEHOLDER, \
        f".wsb host folder is {host!r}, expected placeholder {WSB_HOST_PLACEHOLDER!r}"


def test_wsb_networking_default():
    # Drift alarm for the #181 E2E finding: the committed .wsb must declare
    # Networking=Default, never Enable. On the current Windows Sandbox Store app,
    # Enable silently breaks the LogonCommand / mapped-folder writeback (the sandbox
    # boots but never writes a verdict back to the host); Default gives the same
    # networking without the bug. A revert to Enable would reintroduce a verdictless,
    # hard-to-diagnose failure -- fail loudly here.
    wsb = read_text(WSB)
    assert "<Networking>Default</Networking>" in wsb, \
        "sandbox .wsb must declare <Networking>Default</Networking>"
    assert "<Networking>Enable</Networking>" not in wsb, \
        "sandbox .wsb declares Enable networking -- breaks the mapped-folder writeback on the current Store app; use Default"


def test_wsb_logoncommand_waits_for_mount():
    # Drift alarm (#181 E2E): the LogonCommand must WAIT for the mapped folder to
    # mount before running the driver. On the current Windows Sandbox Store app the
    # LogonCommand can fire before C:\thoughtborne-share is mounted; a bare -File
    # load of the driver then fails instantly with no retry, the sandbox boots but
    # never writes out-*/RESULT.txt. A revert to the bare form reintroduces that
    # silent, verdictless stall -- fail loudly here. Extract the <Command> so the
    # explanatory comment above it cannot satisfy the check.
    wsb = read_text(WSB)
    m = re.search(r"<Command>(.*?)</Command>", wsb, re.S)
    assert m, "sandbox .wsb has no <Command> in its LogonCommand"
    cmd = m.group(1)
    assert "verify-in-sandbox.ps1" in cmd, "LogonCommand does not run verify-in-sandbox.ps1"
    assert "Test-Path" in cmd and "Start-Sleep" in cmd, \
        "LogonCommand lacks the mapped-folder mount-wait poll loop -- races the mount, no verdict"


def test_wsb_well_formed_xml():
    # Drift alarm (#181): the committed .wsb must be well-formed XML. The current
    # Windows Sandbox Store app parses it with a strict parser and rejects invalid
    # XML -- e.g. a `--` inside a comment, which XML forbids -- reporting the
    # rejection only as a GUI dialog that an automated/scripted run never sees, so
    # the sandbox never boots and the run stays verdictless. expat (stdlib
    # ElementTree) enforces the same rules, so a real parse here catches it in-test.
    try:
        ET.parse(str(REPO / WSB))
    except ET.ParseError as e:
        raise AssertionError(f"sandbox .wsb is not well-formed XML: {e}")


def test_sandbox_scripts_ascii():
    # House style: every committed sandbox script stays ASCII / no BOM, like setup.ps1.
    for name in (VERIFY, LAUNCHER, WSB):
        data = read_bytes(name)
        for bom in _BOMS:
            assert not data.startswith(bom), f"{name}: starts with a BOM"
        bad = [i for i, b in enumerate(data) if b >= 0x80]
        assert not bad, f"{name}: non-ASCII byte(s) at offset(s) {bad[:5]}"


def test_sandbox_launcher_present_and_safe():
    # The host launcher exists and only STARTS the throwaway sandbox: it references
    # Windows Sandbox and names verify-in-sandbox.ps1 (the driver it splices args
    # into). Host-safety -- it must never CALL the installer on the host -- is
    # carried by review + the launcher's structure; a narrow negative check backs
    # it up without tripping on comments or the local-mode presence probe.
    text = read_text(LAUNCHER)
    assert "WindowsSandbox" in text, "launcher does not start Windows Sandbox"
    assert "verify-in-sandbox.ps1" in text, \
        "launcher does not reference the in-sandbox driver"
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            continue                      # comments may name setup.ps1
        assert not re.search(r"(&\s*|Start-Process\s+|-File\s+|-FilePath\s+)\S*setup\.ps1", s), \
            f"launcher appears to run the installer on the host: {s!r}"


def test_verify_threads_version():
    # The in-sandbox driver threads a release version so a pre-release can be tested
    # (D-006): it exports THOUGHTBORNE_VERSION and builds a versioned release URL,
    # not only latest/download.
    text = read_text(VERIFY)
    assert "THOUGHTBORNE_VERSION" in text, "driver never sets THOUGHTBORNE_VERSION"
    assert "releases/download/" in text, "driver builds no versioned release URL"


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
    test_zip_lane_guard,
    test_setup_bat_wrapper,
    test_setup_bat_error_handling,
    test_setup_ps1_bat_lane_exit_signal,
    test_inplace_wrapper_protected,
    test_gitignore_covers_sandbox_secrets,
    test_wsb_template_portable,
    test_wsb_networking_default,
    test_wsb_logoncommand_waits_for_mount,
    test_wsb_well_formed_xml,
    test_sandbox_scripts_ascii,
    test_sandbox_launcher_present_and_safe,
    test_verify_threads_version,
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
