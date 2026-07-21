# verify-in-sandbox.ps1 -- #76 install verification inside Windows Sandbox.
#
# WORKING SKELETON + TODO markers. This runs INSIDE a fresh Windows Sandbox
# (no Python/uv/git, default Restricted execution policy) launched by
# thoughtborne-install-test.wsb, drives the real install path end to end, and
# writes a PASS/FAIL sentinel back to the mapped host folder. It cannot be
# exercised off-Windows, and its exact launch/poll/screenshot timing needs
# validation on a real Win11 Pro box -- hence the TODO markers below.
#
# ASCII-only by house style (this file is dropped in via the mapped folder, not
# fetched as a release asset, so the setup.ps1 BOM/charset constraint does not
# strictly apply here -- but keeping it ASCII matches the rest of the harness).

param(
    # 'local'  -> install from the setup.ps1 in the mapped folder (works offline;
    #             use before the first release exists, or to test a WIP script).
    # 'oneliner' -> fetch and run the published setup.ps1 from the release URL
    #             (the real user path; needs a published release with the two
    #             assets -- #145 / WP6 -- otherwise the fetch 404s).
    [ValidateSet('local', 'oneliner')]
    [string]$Mode = 'local'
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

function Copy-Artifacts {
    # Pull logs out for the host to inspect regardless of verdict.
    if (Test-Path -LiteralPath $LogFile) {
        Copy-Item -LiteralPath $LogFile -Destination $OutDir -Force -ErrorAction SilentlyContinue
    }
    # TODO (Win11 box): capture a screenshot of the Cockpit / any dialog into
    # $OutDir. No dependency-free screenshot API is settled yet -- candidates are
    # a .NET Graphics.CopyFromScreen snippet or nircmd. Decide on the real box.
}

# --- 1) Temporary API key ---------------------------------------------------
# The key must be present BEFORE the tool launches: on a keyless start the tool
# opens the #144 wizard and exits(0) *before* hotkey registration
# (thoughtborne.py:1224-1230), so the 'All hotkeys registered' assertion would
# never fire. Physically the install dir must exist first, so we drop the .env
# right after the install (step 2) and before the launch (step 3).
#
# Drop a file named 'temp.env' in the mapped folder holding one working key line
# (e.g. SONIOX_API_KEY=... or GROQ_API_KEY=...). NEVER committed.
$KeyFile = Join-Path $Share 'temp.env'
if (-not (Test-Path -LiteralPath $KeyFile)) {
    Write-Result 'SKIP' "No temp.env in $Share -- cannot verify hotkey registration without a key. See sandbox/README.md."
    Copy-Artifacts
    return
}

# --- 2) Run the install path ------------------------------------------------
try {
    if ($Mode -eq 'oneliner') {
        # TODO (needs a published release, #145): the real user path.
        $url = 'https://github.com/timwessels/thoughtborne-windows/releases/latest/download/setup.ps1'
        Invoke-RestMethod -Uri $url | Invoke-Expression
    } else {
        $localSetup = Join-Path $Share 'setup.ps1'
        if (-not (Test-Path -LiteralPath $localSetup)) {
            Write-Result 'FAIL' "Mode 'local' but no setup.ps1 in $Share."
            Copy-Artifacts
            return
        }
        # NOTE: setup.ps1 still fetches the code ZIP from the release URL, so even
        # 'local' mode needs the published thoughtborne.zip asset to finish. Until
        # then this exercises the preamble/guards/uv bootstrap but not the copy.
        & powershell -NoProfile -ExecutionPolicy Bypass -File $localSetup
    }
} catch {
    Write-Result 'FAIL' ("install path threw: {0}" -f $_.Exception.Message)
    Copy-Artifacts
    return
}

if (-not (Test-Path -LiteralPath $InstallDir)) {
    Write-Result 'FAIL' "install dir not created: $InstallDir"
    Copy-Artifacts
    return
}

# Drop the throwaway key into the install dir before launch.
Copy-Item -LiteralPath $KeyFile -Destination (Join-Path $InstallDir '.env') -Force

# --- 3) Launch Thoughtborne -------------------------------------------------
$launcher = Join-Path $InstallDir 'Thoughtborne.bat'
if (-not (Test-Path -LiteralPath $launcher)) {
    Write-Result 'FAIL' "launcher missing: $launcher"
    Copy-Artifacts
    return
}
# TODO (Win11 box): confirm the launch mode. A shortcut launch (cmd /c) is the
# real user path; Start-Process on the .bat is simpler for the harness. Global
# hotkeys register once system-wide, so ensure no second instance is running.
Start-Process -FilePath $launcher -WorkingDirectory $InstallDir

# --- 4) Poll the log for successful hotkey registration ---------------------
# The exact substring the tool writes (thoughtborne.py:2449, file-only log line).
$needle = 'All hotkeys registered successfully'
$deadline = (Get-Date).AddSeconds(120)   # TODO: tune on the real box (uv sync + first Python download can be slow)
$found = $false
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $LogFile) {
        $log = Get-Content -LiteralPath $LogFile -Raw -ErrorAction SilentlyContinue
        if ($log -and ($log -match [regex]::Escape($needle))) { $found = $true; break }
    }
    Start-Sleep -Seconds 3
}

if (-not $found) {
    Write-Result 'FAIL' "did not observe '$needle' in $LogFile within the timeout"
    Copy-Artifacts
    return
}

# --- 5) (Optional) end-to-end self-test -------------------------------------
# TODO (Win11 box): fire Ctrl+Alt+U-umlaut to run the test_audio.mp3 self-test
# end to end (needs a valid key -- covered by temp.env). Sending the umlaut key
# reliably from automation needs SendKeys/keybd_event work -- settle on the box.

# --- 6) Start-menu shortcuts (informational, does not gate the verdict) ------
# setup.ps1 writes these two .lnk; note their presence in the result. setup.ps1's
# final step also auto-opens the #144 settings wizard -- expected here (same as a
# keyless first start), so screenshot timing must allow for that window.
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$lnkStatus = @('Thoughtborne.lnk', 'Thoughtborne Settings.lnk') | ForEach-Object {
    $p = Join-Path $startMenu $_
    '{0}={1}' -f $_, $(if (Test-Path -LiteralPath $p) { 'present' } else { 'MISSING' })
}
Write-Host ('shortcuts: ' + ($lnkStatus -join ', '))

# --- 7) Artifacts + verdict ------------------------------------------------
Copy-Artifacts
Write-Result 'PASS' ("installed to $InstallDir and observed '$needle' (" + ($lnkStatus -join ', ') + ")")
