# Thoughtborne uninstaller (uninstall.ps1) -- issue #209.
#
# ASCII-only, saved without a BOM -- the same hard rule as setup.ps1 (keep every
# character 7-bit ASCII: plain quotes and hyphens only, no typographic dashes or
# quotes, no box-drawing glyphs). This file ships inside the git-archive release
# ZIP (D-006) and lands in the install dir like any other tracked file.
#
# PowerShell + WinForms, zero Python/venv dependency, no admin at any point: it
# removes a per-user install under %LOCALAPPDATA% and the HKCU Apps-list entry
# setup.ps1 wrote. USER DATA IS KEPT BY DEFAULT (recordings, transcripts, the
# .env key). Only an explicit, unchecked-by-default opt-in checkbox deletes it,
# and the -Silent lane can NEVER delete it -- there is no delete parameter, the
# -Silent path never builds the checkbox, and the delete branch is gated on both
# (-not $Silent) and the checkbox state (D-011).
#
# It launches itself once from the install dir, copies itself to %TEMP% and
# relaunches from there, so the whole install tree (.venv, the managed Python)
# has no open handle from us when we delete it.

param([switch]$FromTemp, [switch]$Silent, [string]$InstallDir)

$ErrorActionPreference = 'Continue'

# --- Hide our own console early (baseline anti-flash, on top of -WindowStyle
#     Hidden in the launch string). powershell.exe is a console-subsystem app, so
#     Windows hands it a console before any script line runs -- a sub-second flash
#     when Settings launches the UninstallString. conhost.exe --headless would be
#     flash-free but is undocumented and an AV-heuristic risk for an unsigned
#     tool, so the plain hide is the deliberate baseline. ---
try {
    Add-Type -Name TbWin -Namespace TB -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll")] public static extern System.IntPtr GetConsoleWindow();
[System.Runtime.InteropServices.DllImport("user32.dll")]   public static extern bool ShowWindow(System.IntPtr hWnd, int nCmdShow);
'@
    $null = [TB.TbWin]::ShowWindow([TB.TbWin]::GetConsoleWindow(), 0)   # 0 = SW_HIDE
} catch { }

$RegPath = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Thoughtborne'

# KEEPLIST-BEGIN
# User data that survives the uninstall by default -- the user-data subset of the
# setup.ps1 DENYLIST, but WITHOUT .venv: the virtualenv is rebuildable tooling of
# THIS install, not user data, so it is removed with the app. Each glob is matched
# against a top-level entry name in the install dir. (.env.example is deliberately
# NOT here -- it is a shipped template, not user data, so it goes with the app.)
$KeepList = @(
    '.env',
    '.env.local',
    '.env.*.local',
    'personal_settings.json',
    'runtime_state.json',
    'history',
    'voice_archive',
    'text_archive',
    'thoughtborne.log*'
)
# KEEPLIST-END

function Test-KeepMatch {
    param([string]$Name)
    foreach ($p in $KeepList) { if ($Name -like $p) { return $true } }
    return $false
}

function Test-IsThoughtborneDir {
    param([string]$Dir)
    # Install-dir fingerprint, mirroring setup.ps1's refuse guard: a thoughtborne.py,
    # or a pyproject.toml naming thoughtborne. Used in phase 2 BEFORE any deletion
    # (the fingerprint files are still on disk) so a stray folder is never emptied.
    if (Test-Path -LiteralPath (Join-Path $Dir 'thoughtborne.py')) { return $true }
    $pp = Join-Path $Dir 'pyproject.toml'
    if (Test-Path -LiteralPath $pp) {
        $text = Get-Content -LiteralPath $pp -Raw -ErrorAction SilentlyContinue
        if ($text -match 'name\s*=\s*["'']thoughtborne["'']') { return $true }
    }
    return $false
}

function Test-ToolRunning {
    param([string]$Dir)
    # Running-instance guard, log-heartbeat based (the AGENTS.md reliable signal,
    # mirroring setup.ps1): the tool is running iff thoughtborne.log's mtime is
    # fresh (< 3 min) AND its tail does not say 'Program ended'. Reads the log, not
    # the process list (elevation-proof), and NEVER kills the tool -- a running
    # instance is left alone and the user is asked to close it with Ctrl+Alt+4.
    $logFile = Join-Path $Dir 'thoughtborne.log'
    if (-not (Test-Path -LiteralPath $logFile)) { return $false }
    try {
        $log = Get-Item -LiteralPath $logFile
        $ageMinutes = ((Get-Date) - $log.LastWriteTime).TotalMinutes
        $tail = Get-Content -LiteralPath $logFile -Tail 5 -ErrorAction SilentlyContinue
        $endedCleanly = (($tail -join "`n") -match 'Program ended')
        return (($ageMinutes -lt 3) -and (-not $endedCleanly))
    } catch { return $false }
}

function Show-RunningNotice {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            ("Thoughtborne looks like it is still running." + [Environment]::NewLine + [Environment]::NewLine +
             "Close it first with Ctrl+Alt+4, then run the uninstall again."),
            "Thoughtborne uninstaller",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    } catch { }
}

function Show-NotThoughtborneNotice {
    param([string]$Dir)
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show(
            ("This does not look like a Thoughtborne install folder:" + [Environment]::NewLine + [Environment]::NewLine +
             $Dir + [Environment]::NewLine + [Environment]::NewLine +
             "Nothing was removed. Uninstall Thoughtborne from Settings > Installed apps."),
            "Thoughtborne uninstaller",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    } catch { }
}

function Show-ConfirmDialog {
    param([string]$Dir)
    # The confirmation dialog. Default = KEEP: the checkbox starts UNCHECKED, the
    # OK ('Remove') button is the AcceptButton and takes initial focus, so a pure
    # Enter/click-through never checks the box and user data always survives. Only
    # a deliberate toggle of the checkbox opts into deleting it. Returns Proceed
    # (OK pressed) and DeleteData (Proceed AND the checkbox was checked).
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing

    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'Uninstall Thoughtborne'
    $form.FormBorderStyle = 'FixedDialog'
    $form.StartPosition = 'CenterScreen'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.ClientSize = New-Object System.Drawing.Size(440, 212)
    $form.TopMost = $true

    $label = New-Object System.Windows.Forms.Label
    $label.Text = ("Remove Thoughtborne from this computer?" + [Environment]::NewLine + [Environment]::NewLine +
                   "Your recordings, transcripts and API key are kept by default.")
    $label.SetBounds(16, 16, 408, 52)
    $form.Controls.Add($label)

    # Name the folder that will be touched, so the user sees exactly what is
    # affected (AutoEllipsis keeps a long path inside the dialog).
    $pathLabel = New-Object System.Windows.Forms.Label
    $pathLabel.Text = ('Folder: ' + $Dir)
    $pathLabel.AutoEllipsis = $true
    $pathLabel.SetBounds(16, 72, 408, 32)
    $form.Controls.Add($pathLabel)

    $chk = New-Object System.Windows.Forms.CheckBox
    $chk.Text = 'Also delete my recordings, transcripts and API key'
    $chk.Checked = $false
    $chk.SetBounds(16, 112, 408, 24)
    $chk.TabIndex = 2
    $form.Controls.Add($chk)

    $ok = New-Object System.Windows.Forms.Button
    $ok.Text = 'Remove'
    $ok.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $ok.SetBounds(244, 164, 88, 32)
    $ok.TabIndex = 0
    $form.Controls.Add($ok)

    $cancel = New-Object System.Windows.Forms.Button
    $cancel.Text = 'Cancel'
    $cancel.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $cancel.SetBounds(340, 164, 88, 32)
    $cancel.TabIndex = 1
    $form.Controls.Add($cancel)

    $form.AcceptButton = $ok
    $form.CancelButton = $cancel
    $form.ActiveControl = $ok

    $result = $form.ShowDialog()
    $proceed = ($result -eq [System.Windows.Forms.DialogResult]::OK)
    $delete = ($proceed -and $chk.Checked)
    $form.Dispose()
    return [pscustomobject]@{ Proceed = $proceed; DeleteData = $delete }
}

function Remove-Shortcuts {
    $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    foreach ($lnkName in @('Thoughtborne.lnk', 'Thoughtborne Settings.lnk')) {
        $lnk = Join-Path $startMenu $lnkName
        if (Test-Path -LiteralPath $lnk) {
            Remove-Item -LiteralPath $lnk -Force -ErrorAction SilentlyContinue
        }
    }
}

function Remove-InstallTree {
    param([string]$Dir, [switch]$IncludeUserData)
    if (-not (Test-Path -LiteralPath $Dir)) { return }
    # Remove each top-level entry. Default: keep-listed user data is left in place,
    # so the install dir stays standing to hold it. With -IncludeUserData (the
    # opt-in checkbox), the keep-list is ignored and everything goes. uv and its
    # managed Python are never touched: they live OUTSIDE this dir (a shared
    # per-user location), so only the .venv inside this dir is removed -- it is not
    # on the keep-list, being rebuildable tooling of this install.
    Get-ChildItem -LiteralPath $Dir -Force -ErrorAction SilentlyContinue | ForEach-Object {
        if ((-not $IncludeUserData) -and (Test-KeepMatch -Name $_.Name)) { return }
        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
    }
    # If nothing is left (nothing kept, or user data was deleted too), remove the
    # now-empty install dir; otherwise leave it standing to hold the kept data.
    # Non-recursive on purpose: should the enumeration above ever misread a
    # populated dir as empty (an ACL/enum error), a bare Remove-Item cannot take the
    # children with it -- it fails on a non-empty dir and the kept data survives.
    # Same "remove only when truly empty" semantics, with the recursive weapon gone.
    if (-not (Get-ChildItem -LiteralPath $Dir -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $Dir -Force -ErrorAction SilentlyContinue
    }
}

function Show-DoneNotice {
    param([string]$Dir, [switch]$DataKept)
    try {
        Add-Type -AssemblyName System.Windows.Forms
        if ($DataKept) {
            $msg = ("Thoughtborne was removed." + [Environment]::NewLine + [Environment]::NewLine +
                    "Your recordings, transcripts and API key are kept at:" + [Environment]::NewLine +
                    $Dir + [Environment]::NewLine + [Environment]::NewLine +
                    "Delete that folder by hand if you no longer need them.")
        } else {
            $msg = "Thoughtborne and its user data were removed."
        }
        [System.Windows.Forms.MessageBox]::Show(
            $msg, "Thoughtborne uninstaller",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information) | Out-Null
    } catch { }
}

function Remove-TempCopy {
    # Best-effort cleanup of our own %TEMP% copy. PowerShell reads a -File script
    # into memory before running and holds no exclusive lock on it, but the folder
    # cannot delete itself while it is the running script's home, so a detached,
    # slightly-delayed rmdir clears it after we exit.
    if (-not $FromTemp) { return }
    try {
        $self = $PSScriptRoot
        if ($self -and ($self -like (Join-Path $env:TEMP 'Thoughtborne-uninstall-*'))) {
            $cmd = 'ping 127.0.0.1 -n 3 >nul & rmdir /s /q "' + $self + '"'
            Start-Process -FilePath 'cmd.exe' -ArgumentList '/c', $cmd -WindowStyle Hidden
        }
    } catch { }
}

# --- Phase 1: launched from the install dir by the UninstallString. Capture the
#     real install dir, refuse fast if the tool is running (before any temp copy),
#     then self-copy to %TEMP% and relaunch from there with the captured dir. ---
if (-not $InstallDir) { $InstallDir = $PSScriptRoot }

if (-not $FromTemp) {
    if (Test-ToolRunning -Dir $InstallDir) {
        if (-not $Silent) { Show-RunningNotice }
        return
    }
    try {
        $tmpDir = Join-Path $env:TEMP ('Thoughtborne-uninstall-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $tmpDir -Force | Out-Null
        $tmpScript = Join-Path $tmpDir 'uninstall.ps1'
        Copy-Item -LiteralPath $PSCommandPath -Destination $tmpScript -Force
    } catch {
        # Could not stage the temp copy -- nothing has been removed; give up quietly.
        return
    }
    # A single-string arg line with the space-bearing paths explicitly double-quoted.
    # Start-Process -ArgumentList joins an ARRAY with spaces WITHOUT quoting, which
    # would split the install path -- %LOCALAPPDATA% embeds the username, e.g.
    # C:\Users\Tim Wessels\... -- so a pre-quoted single string is the reliable form.
    $argLine = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}" -FromTemp -InstallDir "{1}"' -f $tmpScript, $InstallDir
    if ($Silent) { $argLine += ' -Silent' }
    Start-Process -FilePath 'powershell.exe' -ArgumentList $argLine -WindowStyle Hidden
    return
}

# --- Phase 2: running from %TEMP%. Use $InstallDir (threaded in from phase 1),
#     never $PSScriptRoot (now the temp dir) and never the CWD. ---

# Fingerprint guard, BEFORE any deletion (the fingerprint files are still on disk).
# Only ever remove from a dir that looks like a Thoughtborne install, mirroring
# setup.ps1's refuse guard. This closes the ad-hoc lane where the script was copied
# somewhere else and run there ($PSScriptRoot fallback), so a stray folder's
# contents are never removed. The Settings > Uninstall lane passes the real
# absolute install dir and always clears this. Silent: return quietly, no removal.
if (-not (Test-IsThoughtborneDir -Dir $InstallDir)) {
    if (-not $Silent) { Show-NotThoughtborneNotice -Dir $InstallDir }
    Remove-TempCopy
    return
}

# Re-check the running guard: time passed since phase 1, and we are about to delete.
if (Test-ToolRunning -Dir $InstallDir) {
    if (-not $Silent) { Show-RunningNotice }
    Remove-TempCopy
    return
}

$deleteUserData = $false
if (-not $Silent) {
    $answer = Show-ConfirmDialog -Dir $InstallDir
    if (-not $answer.Proceed) {
        Remove-TempCopy
        return
    }
    $deleteUserData = $answer.DeleteData
}
# The -Silent lane never reaches Show-ConfirmDialog, so it never builds the
# checkbox and $deleteUserData stays its initialized $false: the quiet/automation
# lane can never delete user data (D-011).

# 1) Start-menu shortcuts.
Remove-Shortcuts

# 2) Install files. Default keeps the keep-listed user data; the opt-in delete is
#    gated on BOTH (-not $Silent) and the checkbox -- the second wall behind the
#    structurally-silent quiet lane.
if ((-not $Silent) -and $deleteUserData) {
    Remove-InstallTree -Dir $InstallDir -IncludeUserData
} else {
    Remove-InstallTree -Dir $InstallDir
}

# 3) Registry key LAST, and only once the app files are actually gone -- the
#    property the "last" ordering promises. Removals above use SilentlyContinue, so
#    a locked file (an AV handle, an open DLL) can survive; keeping the Apps-list
#    entry then means a half-removed install still shows under Installed apps instead
#    of becoming an orphaned folder with no entry at all, and the user can delete the
#    leftover by hand. (It does not guarantee a clean re-run: if the fingerprint files
#    were removed before the lock hit, a fresh Uninstall is turned away by the phase-2
#    fingerprint guard.) "App remnants" = entries the uninstall targeted: in the
#    delete variant ANY entry, in the keep variant anything NOT on the keep-list
#    (kept user data must not hold the entry alive). This gates ONLY the key removal
#    -- it reads the dir, never touches files, never reads or removes user data. No
#    remnants -> drop the key; remnants -> keep it.
$userDataRemoved = ((-not $Silent) -and $deleteUserData)
$appRemnants = $false
if (Test-Path -LiteralPath $InstallDir) {
    foreach ($e in (Get-ChildItem -LiteralPath $InstallDir -Force -ErrorAction SilentlyContinue)) {
        if ($userDataRemoved -or (-not (Test-KeepMatch -Name $e.Name))) { $appRemnants = $true; break }
    }
}
if (-not $appRemnants) {
    try { Remove-Item -LiteralPath $RegPath -Recurse -Force -ErrorAction SilentlyContinue } catch { }
}

# 4) Closing notice (skipped on -Silent), naming where kept user data still lives.
if (-not $Silent) { Show-DoneNotice -Dir $InstallDir -DataKept:(-not $deleteUserData) }

# 5) Clean up our temp copy.
Remove-TempCopy
