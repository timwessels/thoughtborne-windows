# Thoughtborne installer (setup.ps1) -- issue #76.
#
# ASCII-only, saved without a BOM. This is a hard invariant, not style: the
# script ships as a GitHub *release asset*, which GitHub serves without a
# charset. Windows PowerShell 5.1's  irm ... | iex  then decodes the bytes as
# Latin-1, so any non-ASCII byte or a UTF-8 BOM would corrupt the string before
# iex ever parses it. ASCII is byte-identical in Latin-1 and UTF-8, so it passes
# through untouched. Keep every character in this file 7-bit ASCII: plain quotes
# and hyphens only, no typographic dashes or quotes, no box-drawing glyphs.
#
# No admin at any point. Idempotent. Never deletes or overwrites without a
# Thoughtborne fingerprint. Never touches user data. Never collects secrets --
# the settings app is the only config writer (respects D-002). iex-safe: on the
# piped  irm | iex  lane the body only unwinds with `return` and sets LASTEXITCODE
# -- a bare `exit` there would close the user's whole PowerShell session. The one
# `exit` is gated behind THOUGHTBORNE_FROM_BAT, which only setup.bat (the -File
# lane, never the pipe) sets, to hand a real exit code back to that wrapper.

param([switch]$DryRun)

# --- Preamble (runs before any nested fetch) -------------------------------

$ProgressPreference = 'SilentlyContinue'

# Process-scope execution-policy bypass. Redundant on the cmd/ZIP lanes (which
# already pass -ExecutionPolicy Bypass), load-bearing on the bare native-
# PowerShell one-liner on a fresh Restricted client, where the nested uv
# installer's own policy self-check must clear. The swallow matters on managed
# devices: a GPO MachinePolicy overrides Process scope and Set-ExecutionPolicy
# then emits a non-terminating error -- ignore it so the run fails later at uv's
# clear policy message, not at a noisy first line.
try { Set-ExecutionPolicy -Scope Process Bypass -Force -ErrorAction SilentlyContinue } catch { }

# User data: never copied over, never deleted. Belt-and-suspenders -- the
# curated release ZIP never carries these names anyway (they are gitignored) --
# but wired so a future stale-file clean can reuse the same list.
# DENYLIST-BEGIN
$DataDenylist = @(
    '.env',
    '.env.local',         # anticipated local override (matches the repo .gitignore)
    '.env.*.local',       # e.g. .env.dev.local (NOT .env* -- that would eat .env.example)
    'personal_settings.json',
    'history',            # dir, recursive (recordings + transcripts)
    'voice_archive',      # legacy pre-#50 archive
    'text_archive',       # legacy pre-#50 archive
    'thoughtborne.log*',  # active log + rotated .log.1/.2/.3
    '.venv'               # rebuildable by uv sync; protected to save the resync
)
# DENYLIST-END

# --- Helpers ---------------------------------------------------------------

function Optimize-SecurityProtocol {
    # Gated TLS bump (Scoop's form). .NET 4.7+ exposes a 'SystemDefault' protocol
    # that lets the OS choose the best available (TLS 1.3 on Win11); leave it
    # alone when already selected. Only when it is not -- older frameworks or a
    # pinned value -- fall back to enabling TLS 1.2/1.1/1.0 explicitly. Never a
    # blind pin to 1.2, which would exclude TLS 1.3.
    $names = [System.Enum]::GetNames([System.Net.SecurityProtocolType])
    $current = [System.Net.ServicePointManager]::SecurityProtocol.ToString()
    if (($names -contains 'SystemDefault') -and ($current -eq 'SystemDefault')) {
        return
    }
    [System.Net.ServicePointManager]::SecurityProtocol = 3072 -bor 768 -bor 192
}

function Test-DenylistMatch {
    param([string]$Name)
    foreach ($pat in $DataDenylist) {
        if ($Name -like $pat) { return $true }
    }
    return $false
}

function Copy-TreeWithDenylist {
    param([string]$Source, [string]$Destination)
    # Copy each top-level entry (files and whole subtrees) into the install dir,
    # skipping any name on the data denylist. First-segment matching is enough:
    # the denylist protects whole top-level files/folders (history/, .env, ...).
    # -ErrorAction Stop turns a mid-copy failure into a terminating error so the
    # caller's try/catch can stop cleanly instead of running on to 'uv sync' over a
    # half-copied tree ($ErrorActionPreference is 'Continue' at the top level).
    Get-ChildItem -LiteralPath $Source -Force | ForEach-Object {
        if (Test-DenylistMatch -Name $_.Name) { return }
        Copy-Item -LiteralPath $_.FullName -Destination $Destination -Recurse -Force -ErrorAction Stop
    }
}

function New-ThoughtborneShortcuts {
    param([string]$InstallDir, [switch]$DryRun)
    $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    $cmdExe = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $icon = Join-Path $InstallDir 'assets\logo\favicon.ico'

    # Two Start-menu entries. Target cmd.exe with  /c "<bat>"  rather than the bat
    # directly: only this form makes the context menu offer "Run as administrator"
    # while a plain left-click and any assigned hotkey keep working (spike #140).
    # The two-quote  /c "<path>"  rule tolerates a space in the profile path
    # (%LOCALAPPDATA% embeds the username, e.g. C:\Users\Tim Wessels\...).
    $shortcuts = @(
        @{ Name = 'Thoughtborne';          Bat = 'Thoughtborne.bat';          Desc = 'Start Thoughtborne voice-to-text' },
        @{ Name = 'Thoughtborne Settings'; Bat = 'Thoughtborne-Settings.bat'; Desc = 'Thoughtborne settings and onboarding' }
    )

    if ($DryRun) {
        foreach ($s in $shortcuts) {
            $lnk = Join-Path $startMenu ($s.Name + '.lnk')
            Write-Host ("[dry-run] would write shortcut: {0} -> cmd /c ""{1}\{2}""" -f $lnk, $InstallDir, $s.Bat)
        }
        return
    }

    $shell = New-Object -ComObject WScript.Shell
    foreach ($s in $shortcuts) {
        $lnk = Join-Path $startMenu ($s.Name + '.lnk')
        $batPath = Join-Path $InstallDir $s.Bat
        $argLine = '/c "' + $batPath + '"'
        # Rewrite only when target or arguments actually differ: preserve a user's
        # own tweaks (a hotkey, a changed icon, a RunAs flag), and avoid the
        # Explorer quirk (#140) where rewriting a .lnk can drop a live hotkey.
        if (Test-Path -LiteralPath $lnk) {
            $existing = $shell.CreateShortcut($lnk)
            if (($existing.TargetPath -eq $cmdExe) -and ($existing.Arguments -eq $argLine)) {
                continue
            }
        }
        $sc = $shell.CreateShortcut($lnk)
        $sc.TargetPath = $cmdExe
        $sc.Arguments = $argLine
        $sc.WorkingDirectory = $InstallDir
        $sc.IconLocation = $icon + ',0'
        $sc.Description = $s.Desc
        $sc.WindowStyle = 1
        $sc.Save()
        # WScript.Shell silently swallows an invalid path -- verify the file landed.
        if (-not (Test-Path -LiteralPath $lnk)) {
            Write-Host ("WARNING: shortcut could not be created: {0}" -f $lnk)
        }
    }
}

# --- Main ------------------------------------------------------------------

function Install-Thoughtborne {
    param([switch]$DryRun)

    # DryRun via the param (setup.bat -DryRun) OR the env var (the only way a
    # piped irm|iex can request it). DryRun resolves params, detects uv and
    # reports the full plan, but downloads/extracts/copies/syncs/writes nothing.
    $DryRun = $DryRun -or ($env:THOUGHTBORNE_DRYRUN -in @('1', 'true', 'yes'))

    # 1) Resolve parameters (Param/Env -> Default).
    $installDir = if ($env:THOUGHTBORNE_INSTALL_DIR) { $env:THOUGHTBORNE_INSTALL_DIR } `
                  else { Join-Path $env:LOCALAPPDATA 'Programs\Thoughtborne' }
    $version = if ($env:THOUGHTBORNE_VERSION) { $env:THOUGHTBORNE_VERSION } else { 'latest' }

    Write-Host "Thoughtborne setup"
    Write-Host ("  install dir: {0}" -f $installDir)
    Write-Host ("  version:     {0}" -f $version)
    if ($DryRun) { Write-Host "  mode:        dry-run (no changes will be made)" }
    Write-Host ""

    # 2) Fingerprint / refuse guard (before any write). A missing OR empty dir is a
    #    fresh install (an empty dir has nothing to destroy) -- proceed. A non-empty
    #    dir must show a Thoughtborne fingerprint -- a pyproject.toml naming
    #    thoughtborne, or a thoughtborne.py -- otherwise refuse. This closes the path
    #    where a mistyped install dir points at an unrelated folder that has content.
    #    No force flag: the safety default is hard. $installWasFresh (absent or empty
    #    before we touched it) also lets an aborted first copy clean up after itself
    #    in step 5, instead of leaving a partial folder that bricks every re-run.
    $installWasFresh = $true
    if (Test-Path -LiteralPath $installDir) {
        $existing = @(Get-ChildItem -LiteralPath $installDir -Force -ErrorAction SilentlyContinue)
        if ($existing.Count -gt 0) {
            $installWasFresh = $false
            $isThoughtborne = $false
            $pyproject = Join-Path $installDir 'pyproject.toml'
            if (Test-Path -LiteralPath $pyproject) {
                $text = Get-Content -LiteralPath $pyproject -Raw -ErrorAction SilentlyContinue
                if ($text -match 'name\s*=\s*["'']thoughtborne["'']') { $isThoughtborne = $true }
            }
            if (Test-Path -LiteralPath (Join-Path $installDir 'thoughtborne.py')) { $isThoughtborne = $true }
            if (-not $isThoughtborne) {
                Write-Host ("ERROR: refusing to install into '{0}'." -f $installDir)
                Write-Host "       It exists, is not empty, and is not a Thoughtborne folder (no"
                Write-Host "       pyproject.toml naming thoughtborne, and no thoughtborne.py). Pick"
                Write-Host "       an empty or existing-Thoughtborne directory, or set"
                Write-Host "       THOUGHTBORNE_INSTALL_DIR to a different path."
                $Global:LASTEXITCODE = 1
                return
            }
        }
    }

    # 3) Running-instance guard (only meaningful over an existing install; a fresh
    #    install has no log, so this passes silently). Per AGENTS.md the log
    #    heartbeat is the reliable signal: a fresh mtime with no 'Program ended'
    #    tail means the tool is running. The process-list check is deliberately
    #    NOT used -- it is unreliable under elevation.
    $logFile = Join-Path $installDir 'thoughtborne.log'
    if (Test-Path -LiteralPath $logFile) {
        $log = Get-Item -LiteralPath $logFile
        $ageMinutes = ((Get-Date) - $log.LastWriteTime).TotalMinutes
        $tail = Get-Content -LiteralPath $logFile -Tail 5 -ErrorAction SilentlyContinue
        $endedCleanly = (($tail -join "`n") -match 'Program ended')
        if (($ageMinutes -lt 3) -and (-not $endedCleanly)) {
            Write-Host "ERROR: Thoughtborne looks like it is running (fresh log, no 'Program ended')."
            Write-Host "       Close it first with Ctrl+Alt+4, then run setup again."
            $Global:LASTEXITCODE = 1
            return
        }
    }

    # 4) Detect uv on PATH and at the Astral per-user location (a just-installed
    #    uv is not on this session's PATH yet); install via Astral if missing.
    $uv = $null
    $uvCmd = Get-Command uv -ErrorAction SilentlyContinue
    if ($uvCmd) { $uv = $uvCmd.Source }
    $uvUserPath = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if ((-not $uv) -and (Test-Path -LiteralPath $uvUserPath)) { $uv = $uvUserPath }

    if (-not $uv) {
        if ($DryRun) {
            Write-Host "[dry-run] would install uv via the Astral installer (astral.sh/uv/install.ps1)"
        } else {
            Write-Host "Installing uv (Astral's Python project manager) ..."
            try {
                Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' | Invoke-Expression
            } catch {
                Write-Host ("ERROR: uv install failed: {0}" -f $_.Exception.Message)
                $Global:LASTEXITCODE = 1
                return
            }
            # Resolve uv for the rest of this run (PATH is not refreshed in-process).
            # Default location first, then uv's receipt in case UV_INSTALL_DIR/XDG_*
            # moved it. We never set UV_INSTALL_DIR ourselves (would fragment the
            # shared user-scoped uv the launcher relies on).
            if (Test-Path -LiteralPath $uvUserPath) {
                $uv = $uvUserPath
            } else {
                $receipt = Join-Path $env:LOCALAPPDATA 'uv\uv-receipt.json'
                if (Test-Path -LiteralPath $receipt) {
                    try {
                        $prefix = (Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json).install_prefix
                        if ($prefix) {
                            $cand = Join-Path $prefix 'uv.exe'
                            if (Test-Path -LiteralPath $cand) { $uv = $cand }
                        }
                    } catch { }
                }
            }
            if (-not $uv) {
                Write-Host "ERROR: uv was installed but could not be located afterwards."
                $Global:LASTEXITCODE = 1
                return
            }
        }
    } else {
        Write-Host ("uv found: {0}" -f $uv)
    }

    # 5) Fetch the code snapshot (temp-staged so an aborted/corrupt download never
    #    touches the live install) and copy it in under the data denylist. Asset
    #    names (setup.ps1 / thoughtborne.zip) to be confirmed at #145.
    if ($version -eq 'latest') {
        $zipUrl = 'https://github.com/timwessels/thoughtborne-windows/releases/latest/download/thoughtborne.zip'
    } else {
        $zipUrl = "https://github.com/timwessels/thoughtborne-windows/releases/download/$version/thoughtborne.zip"
    }

    if ($DryRun) {
        Write-Host ("[dry-run] would download: {0}" -f $zipUrl)
        Write-Host ("[dry-run] would extract, strip any single wrapper folder, and copy into: {0}" -f $installDir)
        Write-Host ("[dry-run] user-data denylist (never copied/deleted): {0}" -f ($DataDenylist -join ', '))
    } else {
        $tempDir = Join-Path $env:TEMP ('thoughtborne-setup-' + [guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
        $zipPath = Join-Path $tempDir 'thoughtborne.zip'
        Write-Host "Downloading Thoughtborne ..."
        try {
            (New-Object System.Net.WebClient).DownloadFile($zipUrl, $zipPath)
        } catch {
            Write-Host ("ERROR: download failed: {0}" -f $_.Exception.Message)
            Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
            $Global:LASTEXITCODE = 1
            return
        }
        $extractDir = Join-Path $tempDir 'extracted'
        try {
            Expand-Archive -LiteralPath $zipPath -DestinationPath $extractDir -Force
        } catch {
            Write-Host ("ERROR: extract failed: {0}" -f $_.Exception.Message)
            Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
            $Global:LASTEXITCODE = 1
            return
        }
        # Strip-if-present: a ZIP with exactly one top-level dir and no top-level
        # files is a wrapper layout -- descend into it. Otherwise the extract root
        # is the tree root. (The real layout is pinned at #145; this handles both.)
        $rootItems = @(Get-ChildItem -LiteralPath $extractDir -Force)
        $topFiles = @($rootItems | Where-Object { -not $_.PSIsContainer })
        $topDirs = @($rootItems | Where-Object { $_.PSIsContainer })
        if (($topFiles.Count -eq 0) -and ($topDirs.Count -eq 1)) {
            $treeRoot = $topDirs[0].FullName
        } else {
            $treeRoot = $extractDir
        }
        New-Item -ItemType Directory -Path $installDir -Force | Out-Null
        Write-Host "Installing files ..."
        try {
            Copy-TreeWithDenylist -Source $treeRoot -Destination $installDir
        } catch {
            Write-Host ("ERROR: copying files into '{0}' failed: {1}" -f $installDir, $_.Exception.Message)
            Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
            # A fresh install that aborted mid-copy would otherwise leave a partial,
            # non-fingerprinted folder that every re-run then refuses (a brick). It
            # holds no user data -- a fresh dir had none, and the denylist never
            # copies any -- so clearing it is safe and lets a re-run start clean. An
            # existing Thoughtborne install (not fresh) keeps its fingerprint and is
            # left untouched.
            if ($installWasFresh) {
                Remove-Item -LiteralPath $installDir -Recurse -Force -ErrorAction SilentlyContinue
            }
            $Global:LASTEXITCODE = 1
            return
        }
        Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    # 6) uv sync (rebuilds .venv from pyproject.toml + uv.lock). Capture the native
    #    exit code explicitly -- native failures do not trip $ErrorActionPreference.
    if ($DryRun) {
        Write-Host ("[dry-run] would run 'uv sync' in {0}" -f $installDir)
    } else {
        Write-Host "Setting up the Python environment (uv sync) ..."
        Write-Host "(the first run may download a ~22 MB Python -- seconds to a couple of minutes)"
        Push-Location -LiteralPath $installDir
        try {
            & $uv sync
            $syncExit = $LASTEXITCODE
        } finally {
            Pop-Location   # never leak the pushed cwd on the irm|iex lane
        }
        if ($syncExit -ne 0) {
            Write-Host ("ERROR: 'uv sync' failed (exit code {0})." -f $syncExit)
            $Global:LASTEXITCODE = 1
            return
        }
    }

    # 7) Start-menu shortcuts.
    New-ThoughtborneShortcuts -InstallDir $installDir -DryRun:$DryRun

    # 8) Hand off to the #144 settings / onboarding app (convenience: the tool
    #    itself also opens the wizard on a keyless start). setup.ps1 collects no
    #    secrets (respects D-002) -- the settings app is the only config writer.
    $settingsBat = Join-Path $installDir 'Thoughtborne-Settings.bat'
    if ($DryRun) {
        Write-Host ("[dry-run] would hand off to the settings/onboarding app: {0}" -f $settingsBat)
        Write-Host ""
        Write-Host "[dry-run] plan complete -- nothing was changed."
        $Global:LASTEXITCODE = 0   # explicit success signal (never a stale session value)
        return
    }

    Write-Host ""
    Write-Host "Setup done."
    if (Test-Path -LiteralPath $settingsBat) {
        Write-Host "The settings app is opening -- pick a provider and paste your API key."
        try {
            Start-Process -FilePath $settingsBat -WorkingDirectory $installDir | Out-Null
        } catch {
            Write-Host "(Could not auto-open it -- launch 'Thoughtborne Settings' from the Start menu.)"
        }
    } else {
        Write-Host "Open 'Thoughtborne Settings' from the Start menu to pick a provider and paste your API key."
    }
    Write-Host "Start dictation any time from the Start menu (Thoughtborne)."
    $Global:LASTEXITCODE = 0   # explicit success signal (never a stale session value)
}

Optimize-SecurityProtocol
try {
    Install-Thoughtborne -DryRun:$DryRun
} catch {
    Write-Host ("ERROR: {0}" -f $_.Exception.Message)
    $Global:LASTEXITCODE = 1
}

# The one gated exit (iex-safe). setup.bat runs this via  -File  and sets
# THOUGHTBORNE_FROM_BAT so it gets a real process exit code (a -File run reports 0
# unless the script actually calls exit) -- letting the wrapper show the error and
# pause. The piped  irm | iex  lane never sets this env var, so it falls straight
# through, leaving only $LASTEXITCODE set and the user's session open.
if ($env:THOUGHTBORNE_FROM_BAT -eq '1') { exit $Global:LASTEXITCODE }
