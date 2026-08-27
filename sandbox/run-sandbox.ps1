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
#   4) wait for evidence that it actually came up, and say so clearly if nothing
#      ever ran (#191) instead of sitting out the full result timeout -- while a
#      sandbox that is merely slow is waited out, never stopped
#   5) poll the mapped folder for the newest out-*/RESULT.txt and print it
#   6) stop the sandbox this run started, and write a HOST.txt run record
#
# Agent-drivable from WSL:
#   powershell.exe -NoProfile -ExecutionPolicy Bypass \
#     -File "$(wslpath -w .../sandbox/run-sandbox.ps1)" -Mode oneliner -Version v1.1.0-rc
# The verdict is a file (out-*/RESULT.txt, readable via /mnt/...), so the run
# never blocks on sandbox-desktop GUI time.
#
# Host exit codes: 0 PASS, 1 FAIL, 2 PARTIAL, 3 SKIP, 4 booted but no result
# within the timeout, 5 no usable verdict (an unrecognized RESULT.txt line, or the
# launcher itself erroring out), 6 the sandbox never came up (#191 -- distinct from
# 4 on purpose: 4 means the run was alive and slow or stuck, 6 means nothing ever
# ran). 6 is reached only when the boot window expires with no sandbox alive at
# all, or when one IS alive but never produces the driver's out-* folder within the
# run's whole budget -- a slow boot is waited out, never beheaded. PARTIAL is
# non-zero on purpose so a bare exit-code check never waves through a run whose
# self-test could not be confirmed.
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
    [int]$ResultTimeoutSec = 900,

    # Fail-fast window for "did anything start at all?" (#191). The driver creates
    # its out-<timestamp> folder as its very first act, so a new one appearing proves
    # boot + mapped-folder mount + LogonCommand + PowerShell in a single signal.
    # Generous enough for a slow boot plus the template's mount wait, far below the
    # result timeout. It is NOT a kill deadline: if it expires while a sandbox is
    # demonstrably alive, the run keeps waiting for that folder up to the boot+result
    # total (the .wsb's own mount wait alone can burn ~180 s before the driver runs).
    [int]$BootTimeoutSec = 300,

    # Leave the sandbox running after the verdict (for a hands-on look). The default
    # is to stop the sandbox this run started, so an unattended run leaves no orphan.
    [switch]$KeepSandbox
)

$ErrorActionPreference = 'Stop'

$here = $PSScriptRoot
$template = Join-Path $here 'thoughtborne-install-test.wsb'
$placeholder = 'SANDBOX_HOSTFOLDER_ABS_PATH'

function Get-NewOutDirs {
    # The out-* folders that appeared since the pre-launch snapshot, i.e. this run's.
    param([string[]]$Known)
    @(Get-ChildItem -LiteralPath $here -Directory -Filter 'out-*' -ErrorAction SilentlyContinue |
      Where-Object { $Known -notcontains $_.Name })
}

function Get-SandboxIds {
    # Store-app CLI. Absent on a classic-only box -> fail open with an empty list:
    # this is diagnostic colour and cleanup scoping, never a verdict input.
    $wsb = Get-Command 'wsb.exe' -ErrorAction SilentlyContinue
    if (-not $wsb) { return @() }
    try {
        $raw = (& $wsb.Source list --raw 2>$null) | Out-String
        if (-not $raw.Trim()) { return @() }
        $envs = (ConvertFrom-Json $raw).WindowsSandboxEnvironments
        return @($envs | ForEach-Object { if ($_ -is [string]) { $_ } else { $_.Id } } |
                 Where-Object { $_ })
    } catch { return @() }
}

function Get-SandboxProcesses {
    # ONLY the WindowsSandbox* names. Never vmwp/vmmem: WSL2 runs on the same
    # Hyper-V worker process, so those are present on this box with no sandbox at all
    # (verified 2026-08-27) and a probe keyed on them would always claim "booted".
    @(Get-Process -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -like 'WindowsSandbox*' } |
      Select-Object -ExpandProperty Name -Unique)
}

function Test-HostLocked {
    # LogonUI.exe runs while the lock screen or a secure desktop is up. A heuristic,
    # not an API (a UAC prompt raises it too), but enough to answer the #191 question
    # "was the screen locked while this run was working?" -- correlate it with the
    # verdict in RESULT.txt to learn whether in-VM injection survives a locked host.
    [bool](Get-Process 'LogonUI' -ErrorAction SilentlyContinue)
}

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

# Throwaway key present? Without a key the installed tool starts as the #200
# keyless shop window: it registers its hotkeys, but it has no engine, so the
# self-test lane -- the part that separates PASS from PARTIAL -- can never run.
# NEVER read or echo its contents -- presence only.
$keyFile = Join-Path $here 'temp.env'
if (-not (Test-Path -LiteralPath $keyFile)) {
    Write-Host "ERROR: no temp.env in $here. The end-to-end run needs one working key line (e.g. GROQ_API_KEY=...) so the tool starts with an engine and the self-test lane can run. Drop sandbox\temp.env (never committed) and retry."
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

$verifyArgs = "-Mode $Mode"
if ($Version) { $verifyArgs += " -Version $Version" }
# The template's LogonCommand waits for the mapped folder to mount, then runs the
# driver as `& $d SANDBOX_VERIFY_ARGS` (see verify-in-sandbox.ps1). Fill that
# placeholder with this run's -Mode/-Version.
$wsbText = $wsbText.Replace('SANDBOX_VERIFY_ARGS', $verifyArgs)

$runWsb = Join-Path $env:TEMP ('thoughtborne-sandbox-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.wsb')
Set-Content -LiteralPath $runWsb -Value $wsbText -Encoding ascii

# --- 3) Launch the sandbox (async; the ONLY external process we start) -------
# Snapshot existing out-* folders so we only accept a result produced by THIS run,
# and the sandbox ids so cleanup can never stop a sandbox we did not start.
$preExisting = @(Get-ChildItem -LiteralPath $here -Directory -Filter 'out-*' -ErrorAction SilentlyContinue |
                 Select-Object -ExpandProperty Name)
$preIds = @(Get-SandboxIds)

# Lock-state sampling (#191): once now, once per poll tick, once at the verdict.
# A single snapshot would not do -- the host cannot see WHEN the driver injects.
$lockedAtStart = Test-HostLocked
$lockSamples = 1
$lockedSamples = if ($lockedAtStart) { 1 } else { 0 }

$startedAt = Get-Date

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

$exitCode = 5
$outDirPath = $null
$verdict = 'NONE'
$detail = ''

try {
    # --- 4) Boot probe (#191) -----------------------------------------------
    # A rejected .wsb is reported only as a GUI dialog that an automated run never
    # sees, and a broken LogonCommand looks exactly like a slow install from out
    # here -- both used to cost the full ResultTimeoutSec of silence.
    #
    # Two-stage on purpose. Stage 1 is the fail-fast window: if it expires with NO
    # sign of a sandbox, nothing ever ran and the run ends right there. If a sandbox
    # IS alive, the same expiry says nothing about health -- the .wsb's mapped-folder
    # wait alone can burn ~180 s before the driver's first act -- so stage 2 keeps
    # waiting for the out-* folder rather than stopping a slow but working sandbox.
    # Its own cap is the run's nominal budget (boot + result) measured from the
    # launch: generous, finite, and derived from the two knobs the caller already
    # set. A sandbox that produced no sign of life by then is stuck, not slow.
    $bootGraceDeadline = $startedAt.AddSeconds($BootTimeoutSec + $ResultTimeoutSec)
    Write-Host "Waiting up to $BootTimeoutSec s for the sandbox to come up (a new out-* folder in $here) ..."
    $bootDeadline = (Get-Date).AddSeconds($BootTimeoutSec)
    $booted = $false
    while ((Get-Date) -lt $bootDeadline) {
        if ((Get-NewOutDirs $preExisting).Count -gt 0) { $booted = $true; break }
        Start-Sleep -Seconds 5
        $lockSamples++
        if (Test-HostLocked) { $lockedSamples++ }
    }

    if ($booted) {
        Write-Host "Sandbox is up (the driver created its out-* folder)."
    } else {
        # The process/id lists decide only whether to KEEP WAITING, never to stop:
        # a false negative here lands on the old fail-fast behaviour, a false
        # positive costs wall time and nothing else. The out-folder probe stays the
        # only thing that says "booted", so neither can kill a healthy run.
        $procs = Get-SandboxProcesses
        $ids = @(Get-SandboxIds | Where-Object { $preIds -notcontains $_ })
        Write-Host ""
        if ($procs.Count -eq 0 -and $ids.Count -eq 0) {
            Write-Host "NO BOOT: nothing is running $BootTimeoutSec s after the launch. WindowsSandbox.exe returned but no sandbox came up -- a rejected .wsb config is reported only as a GUI dialog an automated run never sees. Check $runWsb (well-formed XML? valid element values?) and try launching it by hand once."
            $exitCode = 6
        } else {
            $who = $procs -join ', '
            if ($ids.Count -gt 0) { $who += '; id ' + ($ids -join ', ') }
            $graceSec = [int]($bootGraceDeadline - (Get-Date)).TotalSeconds
            Write-Host "SLOW BOOT: a sandbox IS running ($who) but the driver has not created an out-* folder in $here within $BootTimeoutSec s. That is not a verdict -- the .wsb waits for the mapped folder to mount before it runs anything -- so this run keeps waiting up to $graceSec s more instead of stopping a sandbox that may still be working."
            while ((Get-Date) -lt $bootGraceDeadline) {
                if ((Get-NewOutDirs $preExisting).Count -gt 0) { $booted = $true; break }
                Start-Sleep -Seconds 5
                $lockSamples++
                if (Test-HostLocked) { $lockedSamples++ }
            }
            if ($booted) {
                Write-Host "Sandbox is up after all (the driver created its out-* folder late)."
            } else {
                Write-Host ""
                Write-Host "NO BOOT: a sandbox IS running ($who) but the driver never created an out-* folder in $here within the run's full budget -- so the LogonCommand or the mapped folder is the problem, not the boot. Check the <MappedFolders> path and the LogonCommand in $runWsb. Re-run with -KeepSandbox to keep the window and look inside."
                $exitCode = 6
            }
        }
    }

    if ($booted) {
        # The result window starts when the driver signalled life, late boot or not:
        # a sandbox that has just begun installing needs the whole of it, so trimming
        # it against the elapsed grace would only convert a slow start into a false
        # TIMEOUT -- the same beheading this probe was two-staged to avoid.
        Write-Host "Waiting up to $ResultTimeoutSec s for a new out-*/RESULT.txt in $here ..."

        # --- 5) Poll the mapped folder for this run's RESULT.txt -------------
        $deadline = (Get-Date).AddSeconds($ResultTimeoutSec)
        $resultFile = $null
        while ((Get-Date) -lt $deadline) {
            $resultFile = Get-NewOutDirs $preExisting |
                ForEach-Object { Join-Path $_.FullName 'RESULT.txt' } |
                Where-Object { Test-Path -LiteralPath $_ } |
                Get-Item -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if ($resultFile) { break }
            Start-Sleep -Seconds 5
            $lockSamples++
            if (Test-HostLocked) { $lockedSamples++ }
        }

        if (-not $resultFile) {
            Write-Host ""
            Write-Host "TIMEOUT: no new RESULT.txt within $ResultTimeoutSec s. Inspect the newest out-* folder in $here. A slow first run (uv sync + Python download inside the install) may need a larger -ResultTimeoutSec; re-run with -KeepSandbox to keep the sandbox window open for a look."
            $exitCode = 4
        } else {
            # --- 6) Report the verdict + artifact location -------------------
            # @(...) forces an array even for a one-line file, so $lines[0] is the first
            # LINE (not the first character of a lone string).
            $lines = @(Get-Content -LiteralPath $resultFile -ErrorAction SilentlyContinue)
            $verdict = if ($lines.Count -ge 1) { ([string]$lines[0]).Trim() } else { 'UNKNOWN' }
            $detail = if ($lines.Count -ge 2) { (($lines[1..($lines.Count - 1)]) -join ' ').Trim() } else { '' }
            $outDirPath = Split-Path -Parent $resultFile

            Write-Host ""
            Write-Host "RESULT:  $verdict"
            Write-Host "DETAIL:  $detail"
            Write-Host "ARTIFACTS (log + screenshots): $outDirPath"

            switch ($verdict) {
                'PASS'    { $exitCode = 0 }
                'FAIL'    { $exitCode = 1 }
                'PARTIAL' { $exitCode = 2 }
                'SKIP'    { $exitCode = 3 }
                default   { $exitCode = 5 }
            }

            # --- 7) Pointer for the settings-window visual check (#191) ------
            # The harness cannot grade an image itself, so the run is honest about the
            # item staying pending until a human or a vision model answers it.
            $shot = Get-ChildItem -LiteralPath $outDirPath -Filter 'screen-settings-*.png' -ErrorAction SilentlyContinue |
                Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($shot) {
                $checklist = Join-Path $here 'settings-shot-checklist.md'
                Write-Host ""
                Write-Host "VISUAL CHECK PENDING (#191)"
                Write-Host "  screenshot: $($shot.FullName)"
                Write-Host "  checklist:  $checklist"
                Write-Host "  Hand the screenshot to a vision model together with that checklist and drop its"
                Write-Host "  answer as $outDirPath\SETTINGS-SHOT.txt. This item does not gate the install verdict."
            }
        }
    }
} catch {
    # $ErrorActionPreference is Stop, so an unexpected throw would otherwise end the
    # process at 1 while the finally block below wrote the untouched initial 5 into
    # HOST.txt -- a run record contradicting the exit code its reader just saw.
    # Catch it into the same "no usable verdict" bucket (5) so both agree, and name
    # the launcher as the culprit rather than letting it read like a tool verdict.
    Write-Host ""
    Write-Host ("LAUNCHER ERROR: {0}" -f $_.Exception.Message)
    $verdict = 'LAUNCHER-ERROR'
    $exitCode = 5
} finally {
    $lockedAtVerdict = Test-HostLocked
    $lockSamples++
    if ($lockedAtVerdict) { $lockedSamples++ }
    $lockStart = if ($lockedAtStart) { 'Yes' } else { 'No' }
    # "any sample", not "throughout": one locked probe is enough for a Yes, which is
    # the honest reading of the question this sampling exists to answer. The n/m
    # counter beside it carries how much of the run that was.
    $lockAny = if ($lockedSamples -gt 0) { 'Yes' } else { 'No' }
    $lockEnd = if ($lockedAtVerdict) { 'Yes' } else { 'No' }
    $lockLine = "HOST:    locked at start=$lockStart  locked at any sample=$lockAny ($lockedSamples/$lockSamples)  locked at end=$lockEnd"
    Write-Host ""
    Write-Host $lockLine

    # A small run record beside the verdict, useful well past the lock question. Only
    # when an out-dir exists -- on a no-boot there is nowhere to put it and the line
    # above is the record. Best-effort: never let it change the exit code.
    try {
        if (-not $outDirPath) {
            $newest = Get-NewOutDirs $preExisting | Sort-Object LastWriteTime -Descending | Select-Object -First 1
            if ($newest) { $outDirPath = $newest.FullName }
        }
        if ($outDirPath -and (Test-Path -LiteralPath $outDirPath)) {
            $seenIds = @(Get-SandboxIds | Where-Object { $preIds -notcontains $_ })
            $hostLines = @(
                "mode=$Mode",
                "version=$verLabel",
                "config=$runWsb",
                "started=$($startedAt.ToString('yyyy-MM-dd HH:mm:ss'))",
                "ended=$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))",
                "verdict=$verdict",
                "exitcode=$exitCode",
                "sandbox-ids=$(if ($seenIds.Count) { $seenIds -join ', ' } else { 'none listed' })",
                "locked-at-start=$lockedAtStart",
                "locked-samples=$lockedSamples/$lockSamples",
                "locked-at-end=$lockedAtVerdict"
            )
            Set-Content -LiteralPath (Join-Path $outDirPath 'HOST.txt') -Value $hostLines -Encoding ascii
            Write-Host "Run record: $outDirPath\HOST.txt"
        }
    } catch {
        Write-Host ("HOST.txt skipped: {0}" -f $_.Exception.Message)
    }

    # Stop the sandbox THIS run started, on every post-launch path (verdict,
    # timeout, no boot) -- an unattended run must leave no orphan VM behind.
    # Cleanup is best-effort by contract: a failure here must never change the
    # verdict or the exit code the run earned.
    if ($KeepSandbox) {
        Write-Host "Sandbox left running (-KeepSandbox). Stop it with: wsb stop --id <id>"
    } else {
        try {
            $wsb = Get-Command 'wsb.exe' -ErrorAction SilentlyContinue
            $ours = @(Get-SandboxIds | Where-Object { $preIds -notcontains $_ })
            foreach ($id in $ours) {
                & $wsb.Source stop --id $id 2>$null | Out-Null
                Write-Host "Sandbox stopped (id $id)."
            }
            if (-not $wsb) {
                Write-Host "wsb.exe not available -- close the sandbox window by hand."
            } elseif ($ours.Count -eq 0) {
                Write-Host "No sandbox of this run left to stop."
            }
        } catch {
            Write-Host ("Sandbox cleanup skipped: {0}" -f $_.Exception.Message)
        }
    }
}

exit $exitCode
