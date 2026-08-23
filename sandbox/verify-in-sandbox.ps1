# verify-in-sandbox.ps1 -- #76/#181 install verification inside Windows Sandbox.
#
# Runs INSIDE a fresh Windows Sandbox (no Python/uv/git, default Restricted
# execution policy) launched by thoughtborne-install-test.wsb via the host
# launcher run-sandbox.ps1. It drives the real install path end to end and writes
# a verdict sentinel back to the mapped host folder for the host to poll.
#
# Verdict vocabulary (line 1 of RESULT.txt), so a reader can tell the class of a
# non-pass apart at a glance:
#   PASS    -- install + hotkeys registered + self-test transcribed.
#   PARTIAL -- install + hotkeys OK, but the self-test could not be confirmed
#              transcribing (injection unconfirmed, or fired-but-no-transcription).
#              The detail line names the cause. A real-box look settles it.
#   FAIL    -- install / boot / hotkey registration is broken (release-blocking).
#   SKIP    -- no temp.env, so the run cannot even reach hotkey registration.
# The detail line (line 2) always names the reached/failed stage (install /
# launch / hotkeys / self-test).
#
# It cannot be exercised off-Windows. The two things only the first real sandbox
# pass can settle are called out inline: the injected-input -> RegisterHotKey
# self-test path, and the exact launch/poll timing under a first-run uv sync.
#
# ASCII-only by house style (dropped in via the mapped folder, not fetched as a
# release asset, so the setup.ps1 BOM/charset constraint does not strictly apply
# -- but ASCII keeps it consistent with the rest of the harness).

param(
    # 'local'    -> install from the setup.ps1 in the mapped folder (works offline;
    #               use before the first release exists, or to test a WIP script).
    # 'oneliner' -> fetch and run the published setup.ps1 from the release URL
    #               (the real user path; needs a published release with the two
    #               assets -- #145 -- otherwise the fetch 404s).
    [ValidateSet('local', 'oneliner')]
    [string]$Mode = 'local',

    # Full release tag incl. the leading 'v' (e.g. v1.1.0-rc). Empty => latest.
    # Threaded through to reach a pre-release without moving latest/ (D-006).
    [string]$Version = '',

    # Post-launch wait for hotkey registration. The slow, variable phase (uv sync +
    # Python download) already ran synchronously inside the install call (step 2),
    # so by here the tool only needs seconds to boot -- this is a modest guard.
    [int]$LaunchTimeoutSec = 120
)

$ErrorActionPreference = 'Continue'

$Share = 'C:\thoughtborne-share'
$OutDir = Join-Path $Share ('out-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\Thoughtborne'
$LogFile = Join-Path $InstallDir 'thoughtborne.log'
$Sentinel = Join-Path $OutDir 'RESULT.txt'

New-Item -ItemType Directory -Path $OutDir -Force | Out-Null

function Write-Result {
    param([string]$Verdict, [string]$Detail)
    Set-Content -LiteralPath $Sentinel -Value ("{0}`n{1}" -f $Verdict, $Detail) -Encoding ascii
    Write-Host "RESULT: $Verdict -- $Detail"
}

function Save-Screenshot {
    # Best-effort full-virtual-screen capture using the .NET GUI assemblies that
    # ship on every Windows -- no external dependency. A capture failure is a lost
    # diagnostic only and must NEVER change the verdict, so the whole block is
    # swallowed.
    param([string]$Tag)
    try {
        Add-Type -AssemblyName System.Windows.Forms -ErrorAction Stop
        Add-Type -AssemblyName System.Drawing -ErrorAction Stop
        $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
        $gfx = [System.Drawing.Graphics]::FromImage($bmp)
        $gfx.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
        $shot = Join-Path $OutDir ('screen-' + $Tag + '-' + (Get-Date -Format 'HHmmss') + '.png')
        $bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
        $gfx.Dispose(); $bmp.Dispose()
        Write-Host "screenshot: $shot"
    } catch {
        Write-Host ("screenshot ({0}) skipped: {1}" -f $Tag, $_.Exception.Message)
    }
}

function Copy-Artifacts {
    # Pull the log out for the host to inspect regardless of verdict, and grab a
    # screenshot of whatever is on screen at this exit point (Cockpit, or the
    # setup-opened wizard). The tool does not log key VALUES, so the copied log
    # carries no secret -- confirm that on the first real pass before trusting it.
    if (Test-Path -LiteralPath $LogFile) {
        Copy-Item -LiteralPath $LogFile -Destination $OutDir -Force -ErrorAction SilentlyContinue
    }
    Save-Screenshot 'exit'
}

# --- 1) Temporary API key (placed BEFORE the install) -----------------------
# Since #223/D-014 setup.ps1's final step hands off by starting the tool itself
# (step 8), and THAT hand-off is what this harness verifies. So the key must be in
# place before setup.ps1 runs, making the handed-off instance a KEYED one that
# registers hotkeys AND has an engine for the self-test -- rather than the #200
# keyless shop window (which registers hotkeys too, but has nothing to transcribe
# with).
#
# We pre-create the install dir and drop the .env there before the install. That is
# safe against setup.ps1's CURRENT guards: a dir holding only .env is denylist residue
# (D-011), which the step-2 fingerprint guard accepts as a re-installable folder, and
# the denylist copy preserves the .env untouched -- so when step 8 starts the tool the
# key is present. The step-3 running guard passes too (no log yet, nothing running).
# "Current" is load-bearing: a pre-#209 published release ran an older fingerprint
# guard that would REFUSE an .env-only dir. That is not a gap -- this harness verifies
# the NEXT release by design (the local setup.ps1 before the tag, the versioned
# one-liner after the asset upload), so it is coupled to the working-tree setup.ps1
# contract, never an old published one.
#
# temp.env in the mapped folder holds one working key line (e.g. GROQ_API_KEY=...
# or SONIOX_API_KEY=...). With a Groq key the engine carousel falls through to
# groq-large and the self-test transcribes via Groq. NEVER committed.
$KeyFile = Join-Path $Share 'temp.env'
if (-not (Test-Path -LiteralPath $KeyFile)) {
    Write-Result 'SKIP' "stage=preflight: no temp.env in $Share -- cannot reach hotkey registration without a key. See sandbox/README.md."
    Copy-Artifacts
    return
}
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
Copy-Item -LiteralPath $KeyFile -Destination (Join-Path $InstallDir '.env') -Force

# --- 2) Run the install path ------------------------------------------------
# Thread the release version into setup.ps1's own ZIP fetch (both modes need it:
# even 'local' fetches the code ZIP from the release URL). Empty $Version leaves
# setup.ps1 on its 'latest' default.
if ($Version) { $env:THOUGHTBORNE_VERSION = $Version }

try {
    if ($Mode -eq 'oneliner') {
        # The real user path. Build the setup.ps1 fetch URL versioned when a tag is
        # given: latest/download resolves to the newest non-prerelease Latest
        # (v1.1.0-rc2 and later carry the assets); a tag published as pre-release
        # is reachable ONLY at its versioned URL (respects D-006).
        if ($Version) {
            $url = "https://github.com/timwessels/thoughtborne-windows/releases/download/$Version/setup.ps1"
        } else {
            $url = 'https://github.com/timwessels/thoughtborne-windows/releases/latest/download/setup.ps1'
        }
        Invoke-RestMethod -Uri $url | Invoke-Expression
    } else {
        $localSetup = Join-Path $Share 'setup.ps1'
        if (-not (Test-Path -LiteralPath $localSetup)) {
            Write-Result 'FAIL' "stage=install: mode 'local' but no setup.ps1 in $Share."
            Copy-Artifacts
            return
        }
        # NOTE: setup.ps1 still fetches the code ZIP from the release URL, so even
        # 'local' mode needs a published thoughtborne.zip asset to finish the copy.
        & powershell -NoProfile -ExecutionPolicy Bypass -File $localSetup
    }
} catch {
    Write-Result 'FAIL' ("stage=install: install path threw: {0}" -f $_.Exception.Message)
    Copy-Artifacts
    return
}

# Install-success probe. Step 1 pre-creates $InstallDir, so a bare dir-exists check
# can never fail now -- assert the install actually LANDED THE SOURCE instead: the
# thoughtborne.py fingerprint (setup.ps1's own re-install fingerprint). Any install
# failure -- a 404 ZIP, a uv error, or a setup.ps1 guard refusal -- leaves the
# pre-created dir holding only our .env and no source, and fails HERE as stage=install
# rather than falling silently through to the stage=launch Thoughtborne.bat check below
# (the detail line must name the reached stage). That later .bat check stays as a
# backstop for a partial copy that somehow lands .py but not .bat.
if (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'thoughtborne.py'))) {
    Write-Result 'FAIL' "stage=install: install left no thoughtborne.py in $InstallDir -- the copy did not complete (404 ZIP, uv error, or a setup.ps1 guard refusal)"
    Copy-Artifacts
    return
}

# The throwaway key was already dropped into the install dir in step 1 (before the
# install), so the setup.ps1 hand-off below starts a keyed instance.

# --- 3) The installer hand-off already started the tool ---------------------
# setup.ps1's final step (step 8, #223/D-014) starts Thoughtborne.bat itself, and
# THAT hand-off is what this harness verifies -- so we no longer launch the tool
# ourselves. A second launch would only hit the D-004 second-instance refusal (#166):
# the handed-off instance already owns the exclusive global hotkeys and the mutex.
# With the key placed in step 1 that instance is KEYED, so it runs as the Cockpit (not
# the wizard) and reaches hotkey registration + the self-test. A broken hand-off shows
# up honestly below as a missing 'hotkeys registered' needle (step 4).
$launcher = Join-Path $InstallDir 'Thoughtborne.bat'
$launchNote = 'started by the installer hand-off (setup.ps1 step 8)'
if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Result 'FAIL' "stage=launch: installer left no Thoughtborne.bat in $InstallDir -- nothing for the hand-off to start"
    Copy-Artifacts
    return
}

# --- 4) Poll the log for successful hotkey registration ---------------------
# The exact substring the tool writes (thoughtborne.py:2565, file-only log line).
# Get-Content -Raw with SilentlyContinue: Python is actively writing the log, so a
# transient sharing violation just retries next tick.
$needle = 'All hotkeys registered successfully'
$deadline = (Get-Date).AddSeconds($LaunchTimeoutSec)
$found = $false
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $LogFile) {
        $log = Get-Content -LiteralPath $LogFile -Raw -ErrorAction SilentlyContinue
        if ($log -and ($log -match [regex]::Escape($needle))) { $found = $true; break }
    }
    Start-Sleep -Seconds 3
}

if (-not $found) {
    Write-Result 'FAIL' "stage=hotkeys: did not observe '$needle' in $LogFile within ${LaunchTimeoutSec}s"
    Copy-Artifacts
    return
}

# --- 5) Start-menu shortcut (informational) ---------------------------------
# setup.ps1 writes ONE .lnk since #223/D-014 (the standalone settings shortcut is
# retired); note its presence. Its final step (step 8) starts the tool itself -- with
# the key in place that instance is the Cockpit, and it owns the hotkeys + mutex (it
# IS the instance under test), so it is expected on screen, not a stray window.
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$lnkStatus = @('Thoughtborne.lnk') | ForEach-Object {
    $p = Join-Path $startMenu $_
    '{0}={1}' -f $_, $(if (Test-Path -LiteralPath $p) { 'present' } else { 'MISSING' })
}
$shortcuts = 'shortcuts: ' + ($lnkStatus -join ', ')
Write-Host $shortcuts

# --- 6) End-to-end self-test (gates PASS vs PARTIAL) ------------------------
# Fire the test_transcription hotkey from inside the sandbox by synthesizing the
# EXACT modifier+VK the tool logged it registered -- a layout-independent method
# that removes the "is the sandbox US or German?" guess. After the needle, the
# log already carries (file-only, hotkey_manager.py:328) a line like
#   Registered: ctrl+alt+t -> test_transcription (id=7, mod=0x4003, vk=0x54)
# We parse mod+vk from the ASCII portion and inject that chord. RegisterHotKey
# fires on injected input regardless of focus, and the injecting PowerShell and
# the tool share the same (non-elevated) integrity level in the sandbox, so no
# UIPI barrier -- we do not need to focus the Cockpit.
#
# ONLY-REAL-BOX CAVEAT: this injected-input -> RegisterHotKey path is reasoned
# from the code, not run. The first real sandbox pass confirms it; if injection
# ever fails to trip the hotkey, this stays PARTIAL (not FAIL -- install is fine)
# and the cause is named, with a documented hands-on keypress as the backstop.
$selfTest = 'unknown'
$selfDetail = ''
try {
    $logNow = Get-Content -LiteralPath $LogFile -Raw -ErrorAction SilentlyContinue
    if ($logNow -match '->\s*test_transcription\s*\(id=\d+,\s*mod=0x([0-9A-Fa-f]{1,4}),\s*vk=0x([0-9A-Fa-f]{1,2})\)') {
        $mod = [Convert]::ToInt32($matches[1], 16)
        $vk  = [Convert]::ToInt32($matches[2], 16)

        # Open + focus a plain Notepad so the tool's typed insert lands there and is
        # screenshot-able. The chord itself is swallowed by RegisterHotKey, so
        # Notepad only ever receives the later transcription text.
        Start-Process notepad.exe
        Start-Sleep -Seconds 2

        Add-Type @"
using System;
using System.Runtime.InteropServices;
public static class Kb {
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
"@
        $KEYUP = [uint32]2
        # Map the logged modifier bits to virtual-key codes; ignore MOD_NOREPEAT.
        $mods = @()
        if ($mod -band 0x0002) { $mods += 0x11 }  # MOD_CONTROL -> VK_CONTROL
        if ($mod -band 0x0001) { $mods += 0x12 }  # MOD_ALT     -> VK_MENU
        if ($mod -band 0x0004) { $mods += 0x10 }  # MOD_SHIFT   -> VK_SHIFT
        if ($mod -band 0x0008) { $mods += 0x5B }  # MOD_WIN     -> VK_LWIN

        foreach ($m in $mods) { [Kb]::keybd_event([byte]$m, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30 }
        [Kb]::keybd_event([byte]$vk, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 40
        [Kb]::keybd_event([byte]$vk, 0, $KEYUP, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30
        $rev = $mods.Clone(); [array]::Reverse($rev)
        foreach ($m in $rev) { [Kb]::keybd_event([byte]$m, 0, $KEYUP, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30 }

        # Poll for the self-test outcome. Success: 'Test transcription successful'
        # (thoughtborne.py:1181). Fired-but-empty: 'Test: no transcription received'
        # (1195) / 'Test file not found' (1209). Give the Groq call ~60 s.
        $stDeadline = (Get-Date).AddSeconds(60)
        while ((Get-Date) -lt $stDeadline) {
            $log2 = Get-Content -LiteralPath $LogFile -Raw -ErrorAction SilentlyContinue
            if ($log2) {
                if ($log2 -match 'Test transcription successful') { $selfTest = 'pass'; break }
                if (($log2 -match 'Test: no transcription received') -or ($log2 -match 'Test file not found')) {
                    $selfTest = 'fired-no-transcription'; break
                }
            }
            Start-Sleep -Seconds 3
        }
        if ($selfTest -eq 'unknown') {
            $selfDetail = "self-test injection unconfirmed within 60s (no success/failure line) -- verify the injection path on the real box"
        } elseif ($selfTest -eq 'fired-no-transcription') {
            $selfDetail = "self-test fired but no transcription received (check the temp.env key / sandbox network)"
        }
    } else {
        $selfDetail = "could not parse the test_transcription registration line from the log -- self-test not fired"
    }
} catch {
    $selfDetail = ("self-test injection errored: {0}" -f $_.Exception.Message)
}

# Second screenshot after the self-test so the inserted Notepad text is captured.
Save-Screenshot 'selftest'

# --- 7) Artifacts + verdict -------------------------------------------------
Copy-Artifacts
if ($selfTest -eq 'pass') {
    Write-Result 'PASS' ("stage=self-test: installed to $InstallDir; hotkeys registered; self-test transcribed; $shortcuts; $launchNote")
} else {
    Write-Result 'PARTIAL' ("stage=self-test: installed to $InstallDir; hotkeys registered; $selfDetail; $shortcuts; $launchNote")
}
