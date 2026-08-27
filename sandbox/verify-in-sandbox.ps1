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
#   SKIP    -- no temp.env, so the installed tool has no engine: it starts as the
#              #200 keyless shop window (which does register its hotkeys) but has
#              nothing to transcribe with, so the self-test lane -- the part that
#              separates PASS from PARTIAL -- can never run.
# The detail line (line 2) always names the reached/failed stage (install /
# launch / hotkeys / self-test). It also carries two REPORTED-ONLY items that
# never gate the verdict: injection-route= (which key-injection mechanism carried
# the run, or 'none') and settings= (the #191 settings-window lane, whose
# screenshot is graded host-side against sandbox/settings-shot-checklist.md).
#
# It cannot be exercised off-Windows. What only a real sandbox pass can settle is
# called out inline: the injected-input -> RegisterHotKey path itself, and the
# exact launch/poll timing under a first-run uv sync.
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
    # swallowed. Returns the bare file name (or '' on failure) so a caller can name
    # the artifact in RESULT.txt.
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
        return (Split-Path -Leaf $shot)
    } catch {
        Write-Host ("screenshot ({0}) skipped: {1}" -f $Tag, $_.Exception.Message)
        return ''
    }
}

function Copy-Artifacts {
    # Pull the log out for the host to inspect regardless of verdict, and grab a
    # screenshot of whatever is on screen at this exit point (Cockpit, plus the
    # settings window when the #191 lane opened one). The tool does not log key
    # VALUES, so the copied log carries no secret -- confirm that on the first real
    # pass before trusting it.
    if (Test-Path -LiteralPath $LogFile) {
        Copy-Item -LiteralPath $LogFile -Destination $OutDir -Force -ErrorAction SilentlyContinue
    }
    Save-Screenshot 'exit' | Out-Null
}

function Read-LogText {
    # One guarded read of the tool's log as a single string. Python is actively
    # writing it, so a transient sharing violation just yields '' and the caller
    # retries on the next tick. Returning '' rather than $null matters for the
    # length-marking the settings lane does (a $null has no .Length to compare).
    $t = Get-Content -LiteralPath $LogFile -Raw -ErrorAction SilentlyContinue
    if ($t) { return [string]$t }
    return ''
}

# --- Key injection (#191) ---------------------------------------------------
# The tool logs one line per registered hotkey (hotkey_manager.py, file-only):
#   Registered: ctrl+alt+t -> test_transcription (id=7, mod=0x4003, vk=0x54)
# Parsing mod+vk out of it keeps every injected chord layout-independent: we
# replay exactly what RegisterHotKey was given and never map a character through
# the active layout, so the "is this sandbox US or German?" question never
# arises. test_setup.py pins this pattern against the tool's own f-string, and
# pins the polled log strings against the source that produces them -- a silent
# rename there used to cost a 60 s hang and a PARTIAL for the wrong reason.
# HOTKEY-REGEX-BEGIN
$HotkeyLineRegex = '->\s*{0}\s*\(id=\d+,\s*mod=0x([0-9A-Fa-f]{{1,4}}),\s*vk=0x([0-9A-Fa-f]{{1,2}})\)'
# HOTKEY-REGEX-END

function New-KeyInjector {
    # Build a user32!keybd_event binding WITHOUT the on-disk C# compiler (#191).
    #
    # Route A (preferred): Reflection.Emit. DefinePInvokeMethod declares the
    # binding directly in an in-memory assembly, so nothing on disk is invoked --
    # exactly the failure mode a trimmed sandbox image can produce.
    # Route B (fallback): the classic Add-Type C# block, which needs csc.exe from
    # the .NET Framework directory. The two fail under disjoint conditions, which
    # is the point of keeping both.
    # Neither survives Constrained Language Mode; ENV.txt records LanguageMode so
    # that shape is one look away instead of a guess.
    $errs = @()
    try {
        $an = New-Object System.Reflection.AssemblyName 'TbInject'
        $ab = [AppDomain]::CurrentDomain.DefineDynamicAssembly(
                  $an, [System.Reflection.Emit.AssemblyBuilderAccess]::Run)
        $dm = $ab.DefineDynamicModule('TbInjectModule')
        $tb = $dm.DefineType('TbKb', 'Public, Class')
        $mi = $tb.DefinePInvokeMethod(
                  'keybd_event', 'user32.dll',
                  [System.Reflection.MethodAttributes]'Public, Static, PinvokeImpl',
                  [System.Reflection.CallingConventions]::Standard,
                  [type]'System.Void',
                  @([byte], [byte], [uint32], [UIntPtr]),
                  [System.Runtime.InteropServices.CallingConvention]::Winapi,
                  [System.Runtime.InteropServices.CharSet]::Auto)
        $mi.SetImplementationFlags(
            $mi.GetMethodImplementationFlags() -bor
            [System.Reflection.MethodImplAttributes]::PreserveSig)
        return @{ Route = 'reflection-emit'; Type = $tb.CreateType(); Errors = @() }
    } catch {
        $errs += ('reflection-emit: ' + $_.Exception.Message)
    }

    try {
        Add-Type -ErrorAction Stop @"
using System;
using System.Runtime.InteropServices;
public static class TbKbCs {
    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
}
"@
        return @{ Route = 'add-type-csc'; Type = [TbKbCs]; Errors = $errs }
    } catch {
        $errs += ('add-type-csc: ' + $_.Exception.Message)
    }

    return @{ Route = $null; Type = $null; Errors = $errs }
}

function Get-HotkeyChord {
    # Pull the registered modifier bits + virtual key for one action out of the log.
    # $null when the line is not there (yet).
    param([string]$LogText, [string]$Action)
    if (-not $LogText) { return $null }
    $rx = [string]::Format($HotkeyLineRegex, [regex]::Escape($Action))
    if ($LogText -match $rx) {
        return @{ Mod = [Convert]::ToInt32($matches[1], 16)
                  Vk  = [Convert]::ToInt32($matches[2], 16) }
    }
    return $null
}

function Send-Chord {
    # Synthesize the exact modifier+VK combination RegisterHotKey was given.
    # RegisterHotKey fires on injected input regardless of focus, and the injecting
    # PowerShell and the tool share the same (non-elevated) integrity level in the
    # sandbox -- setup.ps1's hand-off is a plain Start-Process, no -Verb RunAs -- so
    # there is no UIPI barrier and we need not focus the Cockpit.
    param($Injector, [int]$Mod, [int]$Vk)
    $KEYUP = [uint32]2
    # Map the logged modifier bits to virtual-key codes; ignore MOD_NOREPEAT.
    $mods = @()
    if ($Mod -band 0x0002) { $mods += 0x11 }  # MOD_CONTROL -> VK_CONTROL
    if ($Mod -band 0x0001) { $mods += 0x12 }  # MOD_ALT     -> VK_MENU
    if ($Mod -band 0x0004) { $mods += 0x10 }  # MOD_SHIFT   -> VK_SHIFT
    if ($Mod -band 0x0008) { $mods += 0x5B }  # MOD_WIN     -> VK_LWIN

    $kb = $Injector.Type
    foreach ($m in $mods) { $kb::keybd_event([byte]$m, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30 }
    $kb::keybd_event([byte]$Vk, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 40
    $kb::keybd_event([byte]$Vk, 0, $KEYUP, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30
    $rev = $mods.Clone(); [array]::Reverse($rev)
    foreach ($m in $rev) { $kb::keybd_event([byte]$m, 0, $KEYUP, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30 }
}

function Save-EnvFingerprint {
    # A few facts about the image this run happened in, written next to the verdict
    # (#191). Three of them settle questions that cost two PARTIAL runs to argue
    # about: whether the image carries notepad.exe, whether it carries the C#
    # compiler, and which injection route actually bound. Diagnostics only -- never
    # read back, never part of a verdict. Read notepad= as "Get-Command found
    # something": an App Execution Alias counts here and can still fail to launch,
    # which is why the caller wraps the start rather than trusting this line.
    param($Injector)
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $lines = @(
            "psversion=$($PSVersionTable.PSVersion)",
            "languagemode=$($ExecutionContext.SessionState.LanguageMode)",
            "admin=$((New-Object Security.Principal.WindowsPrincipal $id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))",
            "notepad=$($null -ne (Get-Command notepad.exe -ErrorAction SilentlyContinue))",
            "csc=$(Test-Path -LiteralPath (Join-Path $env:SystemRoot 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'))",
            "injection-route=$(if ($Injector.Route) { $Injector.Route } else { 'none' })",
            "injection-errors=$($Injector.Errors -join ' | ')"
        )
        Set-Content -LiteralPath (Join-Path $OutDir 'ENV.txt') -Value $lines -Encoding ascii
    } catch {
        Write-Host ("ENV.txt skipped: {0}" -f $_.Exception.Message)
    }
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
    Write-Result 'SKIP' "stage=preflight: no temp.env in $Share -- the tool would start keyless (shop window, no engine), so the self-test lane cannot run. See sandbox/README.md."
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
# The exact substring the tool writes (thoughtborne.py, file-only log line).
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

# --- 6) Key-injection preflight (#191) --------------------------------------
# Build the binding ONCE, before anything needs it, so an image that supports no
# route at all is a named outcome ('none') instead of an exception string caught
# somewhere downstream. Both later lanes (self-test, settings window) depend on it.
$Injector = New-KeyInjector
$routeLabel = 'injection-route=' + $(if ($Injector.Route) { $Injector.Route } else { 'none' })
Write-Host $routeLabel
Save-EnvFingerprint $Injector

# --- 7) End-to-end self-test (gates PASS vs PARTIAL) ------------------------
# Fire the test_transcription hotkey from inside the sandbox by synthesizing the
# EXACT modifier+VK the tool logged it registered (see the Get-HotkeyChord /
# Send-Chord pair above).
#
# ONLY-REAL-BOX CAVEAT: the injected-input -> RegisterHotKey path itself is still
# reasoned from the code, not run -- the two #181 E2E attempts never got that far,
# both dying in the injection MECHANISM instead (an unguarded Start-Process
# notepad.exe, #191). If injection ever fails to trip the hotkey, this stays
# PARTIAL (not FAIL -- install is fine), the cause is named, injection-route= says
# which mechanism was in play, and a hands-on keypress is the documented backstop.
$selfTest = 'unknown'
$selfDetail = ''
if (-not $Injector.Route) {
    $selfDetail = ('no key-injection route available in this image ({0}) -- ' +
                   'the self-test could not be fired') -f ($Injector.Errors -join '; ')
} else {
    try {
        $chord = Get-HotkeyChord (Read-LogText) 'test_transcription'
        if ($chord) {
            # Best-effort insert target. The self-test really does insert its
            # transcript (handle_test_transcription queues an immediate task), so a
            # plain Notepad makes that visible in the exit screenshot. It is a
            # NICETY, never a requirement: the verdict comes from the log, and a
            # trimmed sandbox image may not carry notepad.exe at all -- an unguarded
            # Start-Process here is what made both #181 E2E runs land PARTIAL (#191).
            # The presence probe alone would not be enough, which is why the try is
            # here too: notepad.exe can exist as an App Execution Alias that
            # Get-Command finds happily and Start-Process still refuses to launch.
            # Without a target the insert lands in the Cockpit console, which is
            # harmless: the tool reads stdin only on fatal paths. The chord itself is
            # swallowed by RegisterHotKey, so Notepad only ever receives the later
            # transcription text.
            if (Get-Command notepad.exe -ErrorAction SilentlyContinue) {
                try {
                    Start-Process notepad.exe
                    Start-Sleep -Seconds 2
                } catch {
                    Write-Host ("notepad skipped: {0}" -f $_.Exception.Message)
                }
            } else {
                Write-Host 'notepad.exe not available in this image -- the insert will land in the console'
            }

            Send-Chord $Injector $chord.Mod $chord.Vk

            # Poll for the self-test outcome. Success: 'Test transcription successful'.
            # Fired-but-empty: 'Test: no transcription received' / 'Test file not
            # found'. Give the Groq call ~60 s.
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
}

# Second screenshot after the self-test so the inserted Notepad text is captured.
Save-Screenshot 'selftest' | Out-Null

# --- 8) Settings-window lane (#191, reported -- never gates the verdict) -----
# Drive Ctrl+Alt+G and photograph the desktop, so a release check can catch a stray
# console window (#227 class) or clipped text (#218/#231 class) in the real app
# before a release does. Runs whenever an injection route exists -- also when the
# self-test stayed unconfirmed, because a second chord is then the cheapest
# evidence about whether injection reaches RegisterHotKey at all.
#
# The tool refuses to open settings while an insert is still pending (#196), and
# the self-test inserts its transcript right before this -- so a single press can
# be legitimately ignored. Retry on the refusal line rather than guessing a sleep.
#
# The settings app appends its own first-paint line to the SAME log
# (thoughtborne_settings.py writes to config.LOG_FILE):
#   [SETTINGS] visible: map->expose=..s total=..s viewable=1 foreground=Y rect=WxH+X+Y mode=settings
# foreground= is a machine-checkable answer to "is it frontmost?", so the
# host-side visual check only has to judge what a picture can judge.
# SETTINGS-VISIBLE-NEEDLE-BEGIN
$visibleNeedle = '[SETTINGS] visible:'
# SETTINGS-VISIBLE-NEEDLE-END
$settingsItem = 'not-attempted'
$settingsShot = ''
if ($Injector.Route) {
    $g = Get-HotkeyChord (Read-LogText) 'open_settings'
    if (-not $g) {
        $settingsItem = 'no-chord-parsed'
    } else {
        $opened = $false
        # 'Could not open the settings app' is a HARD state (the settings script is
        # missing, or its launch threw) -- unlike the #196 insert-pending refusal, no
        # further chord can change it. Stop pressing at once instead of spending two
        # more 20 s windows proving the same thing.
        $settingsHardFail = $false
        for ($attempt = 1; $attempt -le 3 -and -not $opened -and -not $settingsHardFail; $attempt++) {
            $mark = (Read-LogText).Length
            Send-Chord $Injector $g.Mod $g.Vk
            $gDeadline = (Get-Date).AddSeconds(20)
            while ((Get-Date) -lt $gDeadline) {
                $tail = Read-LogText
                if ($tail.Length -gt $mark) {
                    $new = $tail.Substring($mark)
                    if ($new -match 'Opened the settings app') { $opened = $true; break }
                    if ($new -match 'Settings not opened') { break }          # insert pending; retry
                    if ($new -match 'Could not open the settings app') {
                        $settingsItem = 'launch-failed -- the tool logged that it could not open the settings app (script missing or launch threw), so the press was not retried'
                        $settingsHardFail = $true
                        break
                    }
                }
                Start-Sleep -Seconds 2
            }
            if (-not $opened -and -not $settingsHardFail) { Start-Sleep -Seconds 5 }
        }
        if ($opened) {
            # Read only what the log gained since the last press, exactly like the
            # open detection above: the line we want always follows it, and a stale
            # one from an earlier open must never be mistaken for this one.
            $visible = ''
            $vDeadline = (Get-Date).AddSeconds(30)
            while ((Get-Date) -lt $vDeadline) {
                $tail = Read-LogText
                if ($tail.Length -gt $mark) {
                    $new = $tail.Substring($mark)
                    if ($new -match ([regex]::Escape($visibleNeedle) + '[^\r\n]*')) { $visible = $matches[0]; break }
                }
                Start-Sleep -Seconds 2
            }
            Start-Sleep -Seconds 2                 # let the window settle before the shot
            $settingsShot = Save-Screenshot 'settings'
            if ($visible) {
                $settingsItem = 'opened; ' + $visible
            } else {
                $settingsItem = "opened; no '$visibleNeedle' line within 30s"
            }
        } elseif ($settingsItem -eq 'not-attempted') {
            $settingsItem = 'chord sent, no open/refusal line observed'
        }
        # The window stays open on purpose: the exit screenshot then carries it too,
        # and the run ends seconds later anyway.
    }
}
$settingsField = "settings=$settingsItem"
if ($settingsShot) { $settingsField += "; settings-shot=$settingsShot" }

# --- 9) Artifacts + verdict -------------------------------------------------
Copy-Artifacts
if ($selfTest -eq 'pass') {
    Write-Result 'PASS' ("stage=self-test: installed to $InstallDir; hotkeys registered; self-test transcribed; $settingsField; $routeLabel; $shortcuts; $launchNote")
} else {
    Write-Result 'PARTIAL' ("stage=self-test: installed to $InstallDir; hotkeys registered; $selfDetail; $settingsField; $routeLabel; $shortcuts; $launchNote")
}
