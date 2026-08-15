# run-sandbox.ps1 -- #181 host-side launcher for the #76 install verification.
#
# Runs on the HOST. Its only job is to START a throwaway Windows Sandbox and
# REPORT the verdict the in-sandbox driver writes back. It deliberately NEVER
# runs setup.ps1, setup.bat, or verify-in-sandbox.ps1 on the host -- the whole
# point of the harness is that the real install path executes only inside the
# disposable sandbox, never against the maintainer's own Windows account.
#
# What it does:
#   1) preflight (Windows Sandbox present? throwaway key present? local setup?)
#   2) copy the portable .wsb template to %TEMP%, filling the real host path in
#      and appending -Mode/-Version to the LogonCommand
#   3) launch the sandbox by CLI (async -- the sandbox desktop stays open)
#   4) poll the mapped folder for the newest out-*/RESULT.txt and print it
#
# Agent-drivable from WSL:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass \
#     -File "$(wslpath -w .../sandbox/run-sandbox.ps1)" -Mode oneliner -Version v1.1.0-rc
# The verdict is a file (out-*/RESULT.txt, readable via /mnt/...), so the run
# never blocks on sandbox-desktop GUI time.
#
# Host exit codes: 0 PASS, 1 FAIL, 2 PARTIAL, 3 SKIP, 4 no result within timeout,
# 5 unrecognized verdict. PARTIAL is non-zero on purpose so a bare exit-code check
# never waves through a run whose self-test could not be confirmed.
#
# ASCII-only by house style (matches the rest of the harness).

param(
    # 'local'    -> the sandbox runs the setup.ps1 copied into this folder (offline
    #               WIP-script testing; needs sandbox\setup.ps1 present).
    # 'oneliner' -> the sandbox fetches and runs the published setup.ps1 (the real
    #               end-user path; needs a published release carrying the assets).
    [ValidateSet('local', 'oneliner')]
    [string]$Mode = 'local',

    # Full release tag incl. the leading 'v' (e.g. v1.1.0-rc). Empty => 'latest'.
    # Threaded into the sandbox so a pre-release can be verified without moving the
    # latest/ alias (respects D-006).
    [string]$Version = '',

    # Overall host-side wait for RESULT.txt. Generous by default: the first run
    # does a real uv sync with a ~22 MB Python download inside the install call.
    [int]$ResultTimeoutSec = 900
)

$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$template = Join-Path $here 'thoughtborne-install-test.wsb'
$placeholder = 'SANDBOX_HOSTFOLDER_ABS_PATH'

# --- 1) Preflight (fail fast, clear messages, no side effects) --------------

# Windows Sandbox present? (Win11 Pro/Enterprise + the feature enabled.)
$sandboxExe = Join-Path $env:SystemRoot 'System32\WindowsSandbox.exe'
if (-not (Test-Path -LiteralPath $sandboxExe)) {
    $cmd = Get-Command 'WindowsSandbox.exe' -ErrorAction SilentlyContinue
    if ($cmd) {
        $sandboxExe = $cmd.Source
    } else {
        Write-Host "ERROR: WindowsSandbox.exe not found. Windows Sandbox needs Win11 Pro/Enterprise with the 'Windows Sandbox' feature enabled (see sandbox/README.md). If it is unavailable on this machine, use the hands-on VM fallback."
        exit 1
    }
}

if (-not (Test-Path -LiteralPath $template)) {
    Write-Host "ERROR: template not found: $template"
    exit 1
}

# Throwaway key present? The E2E cannot reach hotkey registration or the self-test
# without a key. NEVER read or echo its contents -- presence only.
$keyFile = Join-Path $here 'temp.env'
if (-not (Test-Path -LiteralPath $keyFile)) {
    Write-Host "ERROR: no temp.env in $here. The end-to-end run needs one working key line (e.g. GROQ_API_KEY=...) so the tool starts past the onboarding wizard and can run the self-test. Drop sandbox\temp.env (never committed) and retry."
    exit 1
}

# 'local' mode: the sandbox sees only the mapped folder, so the installer copy
# must be here. 'oneliner' fetches the published script instead.
if ($Mode -eq 'local') {
    $localSetup = Join-Path $here 'setup.ps1'
    if (-not (Test-Path -LiteralPath $localSetup)) {
        Write-Host "ERROR: -Mode local needs a setup.ps1 copy in $here (the mapped folder is all the sandbox sees). Copy the repo-root setup.ps1 here, or use -Mode oneliner against a published release."
        exit 1
    }
}

# --- 2) Generate the run .wsb in %TEMP% -------------------------------------
# Literal string .Replace (not -replace): $here holds backslashes, which are
# regex-replacement metacharacters -- a literal replace avoids mangling the path.
$wsbText = (Get-Content -LiteralPath $template -Raw).Replace($placeholder, $here)

$argSuffix = " -Mode $Mode"
if ($Version) { $argSuffix += " -Version $Version" }
# The template's command ends exactly with 'verify-in-sandbox.ps1</Command>';
# splice the run args in just before the close tag.
$wsbText = $wsbText.Replace('verify-in-sandbox.ps1</Command>', "verify-in-sandbox.ps1$argSuffix</Command>")

$runWsb = Join-Path $env:TEMP ('thoughtborne-sandbox-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.wsb')
Set-Content -LiteralPath $runWsb -Value $wsbText -Encoding ascii

# --- 3) Launch the sandbox (async; the ONLY external process we start) -------
# Snapshot existing out-* folders so we only accept a result produced by THIS run.
$preExisting = @(Get-ChildItem -LiteralPath $here -Directory -Filter 'out-*' -ErrorAction SilentlyContinue |
                 Select-Object -ExpandProperty Name)

# Quote the .wsb path in embedded double-quotes: %TEMP% sits under a spaced
# profile path (e.g. C:\Users\First Last\AppData\Local\Temp), and Start-Process
# does NOT quote a single space-containing -ArgumentList string -- WindowsSandbox.exe
# would otherwise receive the path split at the space and fail to find the config.
# The `" ... `" is PowerShell backtick-escaping, so the string physically carries the
# quotes; they are harmless for space-free paths (WindowsSandbox.exe strips them).
Start-Process -FilePath $sandboxExe -ArgumentList "`"$runWsb`""
$verLabel = if ($Version) { $Version } else { 'latest' }
Write-Host "Windows Sandbox launched (mode=$Mode, version=$verLabel)."
Write-Host "Generated config: $runWsb"
Write-Host "Waiting up to $ResultTimeoutSec s for a new out-*/RESULT.txt in $here ..."

# --- 4) Poll the mapped folder for this run's RESULT.txt --------------------
$deadline = (Get-Date).AddSeconds($ResultTimeoutSec)
$resultFile = $null
while ((Get-Date) -lt $deadline) {
    $resultFile = Get-ChildItem -LiteralPath $here -Directory -Filter 'out-*' -ErrorAction SilentlyContinue |
        Where-Object { $preExisting -notcontains $_.Name } |
        ForEach-Object { Join-Path $_.FullName 'RESULT.txt' } |
        Where-Object { Test-Path -LiteralPath $_ } |
        Get-Item -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($resultFile) { break }
    Start-Sleep -Seconds 5
}

if (-not $resultFile) {
    Write-Host ""
    Write-Host "TIMEOUT: no new RESULT.txt within $ResultTimeoutSec s. Inspect the open sandbox window and the newest out-* folder in $here. A slow first run (uv sync + Python download inside the install) may need a larger -ResultTimeoutSec."
    exit 4
}

# --- 5) Report the verdict + artifact location ------------------------------
# @(...) forces an array even for a one-line file, so $lines[0] is the first LINE
# (not the first character of a lone string).
$lines = @(Get-Content -LiteralPath $resultFile -ErrorAction SilentlyContinue)
$verdict = if ($lines.Count -ge 1) { ([string]$lines[0]).Trim() } else { 'UNKNOWN' }
$detail  = if ($lines.Count -ge 2) { (($lines[1..($lines.Count - 1)]) -join ' ').Trim() } else { '' }
$outDir  = Split-Path -Parent $resultFile

Write-Host ""
Write-Host "RESULT:  $verdict"
Write-Host "DETAIL:  $detail"
Write-Host "ARTIFACTS (log + screenshots): $outDir"

switch ($verdict) {
    'PASS'    { exit 0 }
    'FAIL'    { exit 1 }
    'PARTIAL' { exit 2 }
    'SKIP'    { exit 3 }
    default   { exit 5 }
}
