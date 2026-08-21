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

# #209: the GUI uninstaller.
UNINSTALL = "uninstall.ps1"


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
    assert len(names) == 1, f"expected exactly one shortcut, found {len(names)}"
    assert "Name = 'Thoughtborne'" in text, "missing 'Thoughtborne' shortcut"
    # The standalone settings shortcut is retired (#223, D-014). Assert the exact
    # shortcut-definition absence, NOT a bare 'Thoughtborne Settings' substring -- the
    # stale-removal line legitimately names 'Thoughtborne Settings.lnk' (.lnk != .bat).
    assert "Name = 'Thoughtborne Settings'" not in text, "the standalone settings shortcut must be gone (#223)"
    assert "'Thoughtborne.bat'" in text, "shortcut does not reference Thoughtborne.bat"
    # The retired standalone launcher must not be referenced anywhere in the installer
    # (shortcut, hand-off, comment). Compose the forbidden name so this guard does not
    # itself trip the repo-wide grep-proof for the literal (#223, D-014).
    retired_launcher = "Thoughtborne-Settings" + ".bat"
    assert retired_launcher not in text, f"setup.ps1 must not reference the retired {retired_launcher} (#223)"
    assert "cmd.exe" in text, "shortcut target is not cmd.exe"
    assert "favicon.ico" in text, "shortcut carries no favicon.ico icon"
    assert "'/c \"'" in text, "shortcut does not use the cmd /c \"...\" form (#140)"
    # In-place updates strip a stale standalone-settings shortcut from older installs
    # (#223, D-014); the dry-run plan announces it. Positive assertion on the removal.
    assert "[dry-run] would remove any legacy settings shortcut" in text, \
        "setup.ps1 does not remove the legacy 'Thoughtborne Settings' shortcut on update (#223)"


def test_handoff_starts_tool():
    # The post-install hand-off starts the TOOL (Thoughtborne.bat), not a standalone
    # settings app (#223, D-014): a keyless install opens the tool's #200 shop window,
    # which auto-launches the first-run wizard. Positive assertions on the $toolBat
    # resolution, the dry-run plan line, and the real Start-Process -- the complement to
    # test_shortcuts' negative guard that the retired launcher is referenced nowhere.
    text = read_text("setup.ps1")
    assert re.search(r"\$toolBat\s*=\s*Join-Path\s+\$installDir\s+'Thoughtborne\.bat'", text), \
        "the hand-off does not resolve $toolBat to the tool's Thoughtborne.bat (#223)"
    assert "[dry-run] would start Thoughtborne" in text, \
        "the hand-off has no '[dry-run] would start Thoughtborne' plan line (#223)"
    assert re.search(r"Start-Process\s+-FilePath\s+\$toolBat", text), \
        "the hand-off does not Start-Process the tool ($toolBat) (#223)"


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
    for name in ("Thoughtborne.bat",):
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


# ======================================================================
# #209 uninstall story: per-user Apps-list registration (setup.ps1) +
# the GUI uninstaller (uninstall.ps1). Static drift alarms -- the real
# registry write and removal are hands-on / sandbox, like the rest here.
# ======================================================================

def test_uninstall_ascii_only_no_bom():
    # #209: the new uninstaller holds the same ASCII/no-BOM invariant as setup.ps1
    # (it ships as a release-ZIP file and is edited by hand -- keep it 7-bit ASCII).
    data = read_bytes(UNINSTALL)
    for bom in _BOMS:
        assert not data.startswith(bom), f"{UNINSTALL}: starts with a BOM ({bom!r})"
    bad = [i for i, b in enumerate(data) if b >= 0x80]
    assert not bad, f"{UNINSTALL}: non-ASCII byte(s) at offset(s) {bad[:5]}"


def test_registry_write_present():
    # setup.ps1 registers a per-user Apps-list entry with registry cmdlets (never a
    # file write), pointing the uninstall command at uninstall.ps1.
    text = read_text("setup.ps1")
    assert r"\Uninstall\Thoughtborne" in text, "no HKCU Uninstall\\Thoughtborne registry key"
    assert "DisplayName" in text, "registry entry sets no DisplayName"
    assert "UninstallString" in text, "registry entry sets no UninstallString"
    assert "New-ItemProperty" in text, "registry write is not cmdlet-based (New-ItemProperty)"
    assert "New-Item -Path" in text, "registry key is not created with New-Item"
    assert "uninstall.ps1" in text, "uninstall string does not reference uninstall.ps1"


def test_registry_quiet_lane_silent():
    # The automation/winget lane (QuietUninstallString) exists and carries -Silent
    # -- and NO delete-data flag, so it can never remove user data (D-011).
    text = read_text("setup.ps1")
    assert "QuietUninstallString" in text, "no QuietUninstallString for the automation lane"
    assert re.search(r"\$qStr\s*=\s*'[^']*-Silent", text), \
        "the quiet uninstall string is not built as the uninstall string + -Silent"


def test_registry_write_dryrun_gated():
    # The registry step is reachable behind a $DryRun gate (it prints a plan line
    # and writes nothing on dry-run), keeping test_dryrun_present satisfied.
    text = read_text("setup.ps1")
    assert "[dry-run] would register" in text, \
        "registry helper prints no '[dry-run] would register' line -- not DryRun-gated"
    gates = len(re.findall(r"if\s*\(\s*\$DryRun\s*\)", text))
    assert gates >= 4, f"expected several $DryRun-gated side effects, found {gates}"


def test_displayversion_from_pyproject():
    # DisplayVersion reflects the INSTALLED pyproject.toml, not a hardcoded literal:
    # it is assigned the parsed $ver, and a  version = "..."  regex parse is present.
    text = read_text("setup.ps1")
    assert "DisplayVersion" in text, "registry entry sets no DisplayVersion"
    assert re.search(r"DisplayVersion'?\s*\]?\s*=\s*\$ver", text), \
        "DisplayVersion is not set from the parsed $ver (a version literal would be wrong)"
    assert "pyproject.toml" in text, "version is not read from pyproject.toml"
    assert r'version\s*=\s*"' in text, "no  version = \"...\"  regex parse of pyproject.toml"


def test_displayicon_real_path():
    # DisplayIcon points at the real shipped icon, and that asset exists in the tree.
    text = read_text("setup.ps1")
    assert "DisplayIcon" in text, "registry entry sets no DisplayIcon"
    assert r"assets\logo\favicon.ico" in text, \
        "DisplayIcon does not reference the real assets\\logo\\favicon.ico"
    assert (REPO / "assets" / "logo" / "favicon.ico").exists(), \
        "assets/logo/favicon.ico is missing from the tree"


def test_uninstall_keeplist_covers_user_data_excludes_venv():
    # The data-safety spine (D-011), mirror of test_denylist_covers_user_data: the
    # uninstaller's keep-list keeps every user-data name AND removes .venv
    # (rebuildable tooling) and .env.example (a shipped template).
    text = read_text(UNINSTALL)
    m = re.search(r"#\s*KEEPLIST-BEGIN(.*?)#\s*KEEPLIST-END", text, re.S)
    assert m, "KEEPLIST-BEGIN/END sentinels not found in uninstall.ps1"
    globs = re.findall(r"'([^']+)'", m.group(1))
    assert globs, "no keep-list patterns parsed between the sentinels"
    user_data = [".env", ".env.local", ".env.dev.local",
                 "personal_settings.json", "runtime_state.json", "history",
                 "thoughtborne.log", "thoughtborne.log.1",
                 "voice_archive", "text_archive"]
    for path in user_data:
        assert any(fnmatch(path, g) for g in globs), \
            f"user-data name {path!r} matched no keep glob {globs}"
    assert not any(fnmatch(".venv", g) for g in globs), \
        f".venv is caught by a keep glob {globs} -- it must be removed with the app"
    assert not any(fnmatch(".env.example", g) for g in globs), \
        f".env.example is caught by a keep glob {globs} -- the template must go with the app"


def test_uninstall_removes_registry_key():
    # The uninstaller deletes its own HKCU Uninstall\Thoughtborne key -- the launch
    # is fire-and-forget, so the entry vanishes only when the uninstaller removes it.
    text = read_text(UNINSTALL)
    assert r"\Uninstall\Thoughtborne" in text, "uninstaller names no Uninstall\\Thoughtborne key"
    assert re.search(r"Remove-Item\s+-LiteralPath\s+\$RegPath", text), \
        "uninstaller does not Remove-Item its own registry key"


def test_uninstall_removes_shortcuts():
    text = read_text(UNINSTALL)
    assert "Thoughtborne.lnk" in text, "uninstaller does not remove Thoughtborne.lnk"
    assert "Thoughtborne Settings.lnk" in text, "uninstaller does not remove the Settings shortcut"
    assert r"Start Menu\Programs" in text, "uninstaller does not target the Start-menu folder"
    assert "Remove-Item" in text, "uninstaller has no Remove-Item for the shortcuts"


def test_uninstall_running_guard():
    # The running guard uses the log heartbeat (like setup.ps1), points at the
    # Ctrl+Alt+4 exit, and NEVER kills the tool.
    text = read_text(UNINSTALL)
    assert "thoughtborne.log" in text, "running guard: no log reference"
    assert "Program ended" in text, "running guard: no 'Program ended' check"
    assert "LastWriteTime" in text, "running guard: no mtime/heartbeat check"
    assert "Ctrl+Alt+4" in text, "running guard does not point at the Ctrl+Alt+4 exit"
    for banned in ("Stop-Process", "taskkill"):
        assert banned not in text, f"uninstaller must never kill the tool (found {banned})"
    assert not re.search(r"Get-Process[^\n]*\|\s*Stop", text), \
        "uninstaller pipes Get-Process into a stop -- it must never kill the tool"


def test_uninstall_self_copy_temp():
    # Self-copy to %TEMP% + relaunch, threading the captured -InstallDir so a future
    # refactor can't reintroduce the hardcoded-install-dir bug the plan fixed.
    text = read_text(UNINSTALL)
    assert "$env:TEMP" in text, "uninstaller does not stage a copy in %TEMP%"
    assert re.search(r"\[switch\]\s*\$FromTemp", text), "no -FromTemp switch to mark the temp copy"
    assert re.search(r"Start-Process\s+-FilePath\s+'powershell", text), \
        "uninstaller does not relaunch via Start-Process powershell.exe"
    # The captured install dir is threaded into the relaunch as a quoted -InstallDir
    # argument, fed from $InstallDir (guards against the hardcoded-path bug and the
    # array-join-no-quote Start-Process footgun on a space-bearing install path).
    assert '-InstallDir "{1}"' in text and re.search(r"-f\s+\$tmpScript\s*,\s*\$InstallDir", text), \
        "the relaunch does not thread the captured -InstallDir (quoted, from $InstallDir)"


def test_uninstall_console_hidden():
    text = read_text(UNINSTALL)
    assert "-WindowStyle Hidden" in text, "uninstaller launch is not -WindowStyle Hidden"
    assert "ShowWindow" in text and "GetConsoleWindow" in text, \
        "uninstaller does not hide its own console (ShowWindow/GetConsoleWindow)"


def test_uninstall_leaves_uv_untouched():
    # Negative: the uninstaller must never remove uv or its managed Python (shared
    # per-user tooling outside the install dir). Only the in-dir .venv goes.
    text = read_text(UNINSTALL)
    for banned in ("uv.exe", r".local\bin", r"%USERPROFILE%\.local", "uv python"):
        assert banned not in text, \
            f"uninstaller references uv-managed tooling ({banned!r}) -- it must stay untouched"


def test_uninstall_silent_keeps_data():
    # Checkbox variant (D-011): the delete branch is gated on BOTH (-not $Silent)
    # and the checkbox state; the checkbox is created UNCHECKED; and the -Silent
    # lane never builds the checkbox (Show-ConfirmDialog is only called under a
    # -not $Silent guard), so an automated/silent uninstall can never delete data.
    text = read_text(UNINSTALL)
    assert "New-Object System.Windows.Forms.CheckBox" in text, "no opt-in delete checkbox"
    assert re.search(r"\$chk\.Checked\s*=\s*\$false", text), \
        "the delete checkbox does not default to unchecked (Checked = $false)"
    assert re.search(r"\$deleteUserData\s*=\s*\$false", text), \
        "the delete flag is not initialized to $false"
    # Anchor on the delete-branch IF-STATEMENT, not a bare  (-not $Silent) -and
    # $deleteUserData  substring: FIX 2 added an assignment  $userDataRemoved =
    # ((-not $Silent) -and $deleteUserData)  further down, so a substring match would
    # re-satisfy here even if the real  if ((-not $Silent) -and $deleteUserData)  gate
    # were weakened to  if ($deleteUserData) . The  if (  prefix pins the gate itself.
    assert re.search(r"if\s*\(\s*\(\s*-not\s+\$Silent\s*\)\s*-and\s+\$deleteUserData\s*\)", text), \
        "the delete branch (if-statement) is not gated on both (-not $Silent) and the checkbox state"
    assert text.count("New-Object System.Windows.Forms.CheckBox") == 1, \
        "the checkbox is built in more than one place -- can't prove the silent lane skips it"
    call_idx = text.index("Show-ConfirmDialog -Dir")
    guard_idx = text.rindex("if (", 0, call_idx)
    assert "-not $Silent" in text[guard_idx:call_idx], \
        "the confirmation dialog is not guarded by -not $Silent -- the silent lane could build it"


def test_uninstall_delete_flag_assignment_exclusivity():
    # Z1 (#209): a later-added unconditional  $deleteUserData = $true  outside the
    # silent guard would silently defeat the data-safety gate and pass every other
    # test. Pin it: EXACTLY two assignments to $deleteUserData -- the $false init and
    # the one derived from the confirm dialog ($answer.DeleteData). (?!=) excludes a
    # comparison; PowerShell has no ==, so this is belt-and-suspenders.
    text = read_text(UNINSTALL)
    assigns = re.findall(r"\$deleteUserData\s*=(?!=)", text)
    assert len(assigns) == 2, \
        f"expected exactly two $deleteUserData assignments (init + checkbox result), found {len(assigns)}"
    assert re.search(r"\$deleteUserData\s*=\s*\$false", text), \
        "the delete flag is not initialized to $false"
    assert re.search(r"\$deleteUserData\s*=\s*\$answer\.DeleteData", text), \
        "the delete flag is not set from the confirm dialog's $answer.DeleteData"


def test_uninstall_checkbox_checked_assignment_exclusivity():
    # Z3 (#209), analog of the $deleteUserData exclusivity: a later-added second
    #  $chk.Checked = $true  (e.g. a "remember my last choice" feature) would win by
    # last-assignment and default the delete checkbox CHECKED -- a pure Enter/click-
    # through would then delete user data, breaking the hard D-011 gate. Pin it:
    # EXACTLY one $chk.Checked assignment, and it is  = $false . (?!=) excludes a
    # comparison; the  ($proceed -and $chk.Checked)  read carries no '=' and is skipped.
    text = read_text(UNINSTALL)
    assigns = re.findall(r"\$chk\.Checked\s*=(?!=)", text)
    assert len(assigns) == 1, \
        f"expected exactly one $chk.Checked assignment (the unchecked default), found {len(assigns)}"
    assert re.search(r"\$chk\.Checked\s*=\s*\$false", text), \
        "the checkbox's single assignment is not  = $false  -- it must default unchecked"


def test_uninstall_confirm_dialog_default_keep():
    # Z2 (#209): the confirm dialog must default to KEEP with no click-through to
    # deletion. OK is the AcceptButton and takes initial focus (Enter/click -> keep);
    # the checkbox starts unchecked and sits AFTER OK in the tab order. Any of the
    # plausible sabotages -- ActiveControl = $chk, a checked box, a checkbox TabIndex
    # ahead of OK -- opens a focus/tab path onto deletion and fails here.
    text = read_text(UNINSTALL)
    assert re.search(r"\$form\.AcceptButton\s*=\s*\$ok", text), \
        "OK is not the form AcceptButton (Enter would not map to the keep-default Remove)"
    assert re.search(r"\$form\.ActiveControl\s*=\s*\$ok", text), \
        "initial focus is not the OK button"
    assert not re.search(r"\$form\.ActiveControl\s*=\s*\$chk", text), \
        "initial focus is on the checkbox -- a click-through could check it"
    assert re.search(r"\$chk\.Checked\s*=\s*\$false", text), \
        "the delete checkbox does not start unchecked"
    m_ok = re.search(r"\$ok\.TabIndex\s*=\s*(\d+)", text)
    m_chk = re.search(r"\$chk\.TabIndex\s*=\s*(\d+)", text)
    assert m_ok and m_chk, "OK and/or checkbox TabIndex not set explicitly"
    assert int(m_chk.group(1)) > int(m_ok.group(1)), \
        f"checkbox TabIndex ({m_chk.group(1)}) is not after OK ({m_ok.group(1)}) -- tab could land on delete first"


def test_uninstall_confirm_dialog_single_guarded_call():
    # Z3 (#209): exactly ONE call site for Show-ConfirmDialog, and it is under a
    # -not $Silent guard. A second, unguarded call would let the silent lane build
    # the delete checkbox. (The '-Dir' arg distinguishes the call from the function
    # definition and the explanatory comment.)
    text = read_text(UNINSTALL)
    calls = re.findall(r"Show-ConfirmDialog\s+-Dir", text)
    assert len(calls) == 1, f"expected exactly one Show-ConfirmDialog call, found {len(calls)}"
    idx = text.index("Show-ConfirmDialog -Dir")
    guard_idx = text.rindex("if (", 0, idx)
    assert "-not $Silent" in text[guard_idx:idx], \
        "the Show-ConfirmDialog call is not guarded by -not $Silent"


def test_uninstall_fingerprint_guard():
    # FUND 2 (#209): the uninstaller checks that its target dir looks like a
    # Thoughtborne install BEFORE any deletion (thoughtborne.py, or a pyproject.toml
    # naming thoughtborne -- the same fingerprint setup.ps1 uses), so the ad-hoc
    # "copied elsewhere and run there" lane can never empty a stray folder. The guard
    # must sit ahead of the file-removal step in phase 2.
    text = read_text(UNINSTALL)
    assert "Test-IsThoughtborneDir" in text, "no install-dir fingerprint guard (Test-IsThoughtborneDir)"
    assert "thoughtborne.py" in text, "fingerprint guard: no thoughtborne.py check"
    guard_idx = text.index("if (-not (Test-IsThoughtborneDir")
    remove_idx = text.index("Remove-InstallTree -Dir $InstallDir")
    assert guard_idx < remove_idx, \
        "the fingerprint guard does not run before the install-tree removal"


def test_uninstall_registry_key_gated_on_remnants():
    # FIX 2 (#209): the registry-last property is actually delivered -- the Apps-list
    # key is dropped ONLY when no app remnants survive (a locked leftover keeps the
    # entry so Uninstall can be re-run). The RegPath removal must sit under a
    # no-remnants guard that reuses Test-KeepMatch, never touching files/user data.
    text = read_text(UNINSTALL)
    assert "$appRemnants" in text, "no app-remnants check gating the registry removal"
    m = re.search(r"if\s*\(\s*-not\s+\$appRemnants\s*\)\s*\{[^}]*Remove-Item\s+-LiteralPath\s+\$RegPath",
                  text, re.S)
    assert m, "the $RegPath removal is not gated on (-not $appRemnants)"
    # the remnant scan reuses the keep-list predicate (kept user data must not keep
    # the entry alive) and is driven by the effective delete decision.
    remnant_region = text[text.index("$userDataRemoved ="):text.index("if (-not $appRemnants)")]
    assert "Test-KeepMatch" in remnant_region, \
        "the remnant scan does not reuse Test-KeepMatch -- kept user data could wrongly count as a remnant"
    assert re.search(r"\(\s*-not\s+\$Silent\s*\)\s*-and\s+\$deleteUserData", remnant_region), \
        "the remnant scan's delete-mode is not the effective (-not $Silent)-and-checkbox decision"


def test_uninstall_final_removedir_not_recursive():
    # FUND 1 (#209): the final "remove the now-empty install dir" must be
    # NON-recursive, so a misread of a populated dir as empty (an ACL/enum error)
    # cannot take kept data with it. The per-entry removal keeps -Recurse (it removes
    # whole subtrees like history/ in the delete variant) -- only the $Dir self-remove
    # is disarmed.
    text = read_text(UNINSTALL)
    assert re.search(r"Remove-Item\s+-LiteralPath\s+\$Dir\s+-Force", text), \
        "the final install-dir removal is not the non-recursive Remove-Item -LiteralPath $Dir -Force"
    # Order-independent: match -Recurse anywhere on the $Dir self-remove line, so a
    # ` -Force -Recurse ` swap can't slip a recursive weapon past a fixed-order regex.
    # ($Dir\b excludes $_.FullName / $RegPath / $InstallDir, whose lines may carry it.)
    assert not re.search(r"Remove-Item\s+-LiteralPath\s+\$Dir\b[^\n]*-Recurse", text), \
        "the final install-dir removal carries -Recurse (any flag order) -- a falsely-empty read could delete kept data"


def test_reinstall_accepts_denylist_residue():
    # FIX 1 (#209): after a keep-data uninstall the install dir holds ONLY denylist
    # residue (.env, history/, ...) with the fingerprint files gone. setup.ps1's
    # step-2 guard must not refuse that -- a dir whose EVERY entry matches the data
    # denylist is a re-installable Thoughtborne residue, so the install proceeds.
    # Assert the residue escape hatch lives inside step 2, reuses Test-DenylistMatch
    # over the existing entries, and still keeps the refuse path for a foreign file.
    text = read_text("setup.ps1")
    m = re.search(r"# 2\) Fingerprint.*?# 3\) Running", text, re.S)
    assert m, "could not isolate setup.ps1 step 2 (the fingerprint/refuse guard)"
    step2 = m.group(0)
    assert "Test-DenylistMatch" in step2, \
        "step 2 does not reuse Test-DenylistMatch for the denylist-residue check (FIX 1)"
    assert re.search(r"foreach\s*\(\s*\$\w+\s+in\s+\$existing\s*\)", step2), \
        "step 2 does not iterate $existing to classify the residue"
    assert re.search(r"if\s*\(\s*\(\s*-not\s+\$isThoughtborne\s*\)\s*-and\s+\(\s*-not\s+\$isDataResidue\s*\)\s*\)", step2), \
        "the refuse is not gated on (-not fingerprint) AND (-not residue) -- residue would still be refused or the guard weakened"
    assert "refusing to install" in step2, "step 2 lost its refuse path for a genuinely foreign folder"


def test_reinstall_residue_keeps_installwasfresh_false():
    # FIX 1 safety invariant (#209), the load-bearing half of the residue escape hatch.
    # $installWasFresh gates step 5's abort cleanup, which WIPES a partial fresh install
    # on a mid-copy failure. On a keep-data reinstall the dir holds real user-data
    # residue (.env, history/), so it MUST NOT count as fresh -- else an aborted copy
    # would delete that residue. It is initialized $true unconditionally, then set
    # $false the moment the dir is found non-empty (residue included); the residue
    # branch must never flip it back. Verified data-destroying mutation:
    # inserting  if ($isDataResidue) { $installWasFresh = $true }  passes every other
    # case -- so pin it here.
    text = read_text("setup.ps1")
    # Exactly one  $installWasFresh = $true  in the whole file (the init). (?!=) guards
    # against a hypothetical comparison (PowerShell has no ==, so belt-and-suspenders).
    true_assigns = re.findall(r"\$installWasFresh\s*=(?!=)\s*\$true", text)
    assert len(true_assigns) == 1, \
        f"expected exactly one  $installWasFresh = $true  (the unconditional init), found {len(true_assigns)}"
    # ...and it precedes the non-empty branch, so the init cannot be the one inside it.
    init_idx = text.index("$installWasFresh = $true")
    branch_idx = text.index("if ($existing.Count -gt 0)")
    assert init_idx < branch_idx, \
        "the  $installWasFresh = $true  init does not precede the non-empty branch"
    # Inside the non-empty branch (where residue is classified) it is only set $false.
    # Isolate step 2 so a later step's use of the variable can't mask a stray $true.
    m = re.search(r"# 2\) Fingerprint.*?# 3\) Running", text, re.S)
    assert m, "could not isolate setup.ps1 step 2 (the fingerprint/refuse guard)"
    branch = m.group(0)[m.group(0).index("if ($existing.Count -gt 0)"):]
    assert "$installWasFresh = $false" in branch, \
        "the non-empty branch does not mark the install non-fresh ($installWasFresh = $false)"
    assert "$installWasFresh = $true" not in branch, \
        "the non-empty/residue branch sets $installWasFresh back to $true -- the step-5 abort cleanup could then wipe kept user-data residue"


CASES = [
    test_ascii_only_no_bom,
    test_no_ungated_exit,
    test_denylist_covers_user_data,
    test_fingerprint_refusal_present,
    test_running_instance_guard_present,
    test_dryrun_present,
    test_shortcuts,
    test_handoff_starts_tool,
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
    test_uninstall_ascii_only_no_bom,
    test_registry_write_present,
    test_registry_quiet_lane_silent,
    test_registry_write_dryrun_gated,
    test_displayversion_from_pyproject,
    test_displayicon_real_path,
    test_uninstall_keeplist_covers_user_data_excludes_venv,
    test_uninstall_removes_registry_key,
    test_uninstall_removes_shortcuts,
    test_uninstall_running_guard,
    test_uninstall_self_copy_temp,
    test_uninstall_console_hidden,
    test_uninstall_leaves_uv_untouched,
    test_uninstall_silent_keeps_data,
    test_uninstall_delete_flag_assignment_exclusivity,
    test_uninstall_checkbox_checked_assignment_exclusivity,
    test_uninstall_confirm_dialog_default_keep,
    test_uninstall_confirm_dialog_single_guarded_call,
    test_uninstall_fingerprint_guard,
    test_uninstall_registry_key_gated_on_remnants,
    test_uninstall_final_removedir_not_recursive,
    test_reinstall_accepts_denylist_residue,
    test_reinstall_residue_keeps_installwasfresh_false,
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
