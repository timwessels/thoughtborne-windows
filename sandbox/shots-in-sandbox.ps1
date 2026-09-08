# shots-in-sandbox.ps1 -- #288 screenshot lane inside Windows Sandbox.
#
# Sibling of verify-in-sandbox.ps1, with a different job: that one verifies the
# PUBLISHED install path, this one photographs the CURRENT working tree. The two
# differ where it matters:
#   - source: a src.zip staged into the mapped folder (git archive of the
#     checkout), not the release ZIP -- the whole point is showing code that is
#     not released yet.
#   - console: HKCU\Console defaults are set before the launch so the Cockpit
#     opens at a chosen size/font with no scrollbar (buffer == window), which a
#     stock 80x25 conhost cannot give.
#   - interaction: after the automatic phase it serves a command loop from the
#     mapped folder, so the host can click, press chords and take further shots
#     without paying another sandbox boot + uv sync per attempt.
#
# It installs to the DEFAULT location by default; -InstallDir overrides it (a
# neutral short path keeps the maintainer's profile name out of the picture).
#
# ASCII-only, like the rest of the harness. Throwaway tooling: it is not part of
# the release gate and nothing here grades anything.

param(
    [string]$Run = 'out-shots-manual',
    [string]$InstallDir = '',
    [int]$Cols = 78,
    [int]$Rows = 52,
    [int]$FontHeight = 16,
    [string]$FontFace = 'Consolas',
    [int]$IdleMinutes = 60
)

$ErrorActionPreference = 'Continue'

$Share = 'C:\thoughtborne-share'
$OutDir = Join-Path $Share $Run
$CmdDir = Join-Path $OutDir 'cmd'
New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
New-Item -ItemType Directory -Path $CmdDir -Force | Out-Null
$DriverLog = Join-Path $OutDir 'driver.log'
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\Thoughtborne' }
$LogFile = Join-Path $InstallDir 'thoughtborne.log'

function Log([string]$m) {
    $line = '{0} {1}' -f (Get-Date -Format 'HH:mm:ss'), $m
    Add-Content -LiteralPath $DriverLog -Value $line -Encoding ascii -ErrorAction SilentlyContinue
    Write-Host $line
}
function State([string]$s) {
    Set-Content -LiteralPath (Join-Path $OutDir 'STATE.txt') -Value $s -Encoding ascii -ErrorAction SilentlyContinue
    Log ('STATE ' + $s)
}

State 'boot'

# --- native bindings --------------------------------------------------------
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public struct TbRect { public int Left, Top, Right, Bottom; }
public static class Tb {
    [DllImport("user32.dll")] public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out TbRect r);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder s, int max);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr p);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr hWnd, int x, int y, int w, int h, bool repaint);
    [DllImport("dwmapi.dll")] public static extern int DwmGetWindowAttribute(IntPtr hWnd, int attr, out TbRect r, int size);
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr p);
}
"@

function Get-VisibleWindows {
    $list = New-Object System.Collections.ArrayList
    $cb = [Tb+EnumWindowsProc] {
        param($h, $l)
        if ([Tb]::IsWindowVisible($h)) {
            $sb = New-Object System.Text.StringBuilder 512
            [void][Tb]::GetWindowTextW($h, $sb, 512)
            $t = $sb.ToString()
            if ($t) {
                $r = New-Object TbRect
                [void][Tb]::GetWindowRect($h, [ref]$r)
                $d = New-Object TbRect
                [void][Tb]::DwmGetWindowAttribute($h, 9, [ref]$d, 16)
                [void]$list.Add([pscustomobject]@{
                    Handle = ('0x{0:X}' -f [int64]$h)
                    Title  = $t
                    Rect   = ('{0}x{1}+{2}+{3}' -f ($r.Right - $r.Left), ($r.Bottom - $r.Top), $r.Left, $r.Top)
                    Frame  = ('{0}x{1}+{2}+{3}' -f ($d.Right - $d.Left), ($d.Bottom - $d.Top), $d.Left, $d.Top)
                })
            }
        }
        return $true
    }
    [void][Tb]::EnumWindows($cb, [IntPtr]::Zero)
    return $list
}

function Save-Shot([string]$Tag) {
    try {
        $b = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $bmp = New-Object System.Drawing.Bitmap($b.Width, $b.Height)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($b.Location, [System.Drawing.Point]::Empty, $b.Size)
        $p = Join-Path $OutDir ('shot-' + $Tag + '.png')
        $bmp.Save($p, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        Log ('shot -> ' + (Split-Path -Leaf $p))
        return (Split-Path -Leaf $p)
    } catch {
        Log ('shot FAILED: ' + $_.Exception.Message)
        return ''
    }
}

# Same registration line the tool logs, same parse as verify-in-sandbox.ps1: the
# chord is replayed exactly as RegisterHotKey got it, so nothing depends on the
# keyboard layout of the image.
$HotkeyLineRegex = '->\s*{0}\s*\(id=\d+,\s*mod=0x([0-9A-Fa-f]{{1,4}}),\s*vk=0x([0-9A-Fa-f]{{1,2}})\)'

function Read-LogText {
    $t = Get-Content -LiteralPath $LogFile -Raw -ErrorAction SilentlyContinue
    if ($t) { return [string]$t }
    return ''
}

function Get-HotkeyChord([string]$Action) {
    $txt = Read-LogText
    if (-not $txt) { return $null }
    $rx = [string]::Format($HotkeyLineRegex, [regex]::Escape($Action))
    if ($txt -match $rx) {
        return @{ Mod = [Convert]::ToInt32($matches[1], 16); Vk = [Convert]::ToInt32($matches[2], 16) }
    }
    return $null
}

function Send-Chord([int]$Mod, [int]$Vk) {
    $KEYUP = [uint32]2
    $mods = @()
    if ($Mod -band 0x0002) { $mods += 0x11 }
    if ($Mod -band 0x0001) { $mods += 0x12 }
    if ($Mod -band 0x0004) { $mods += 0x10 }
    if ($Mod -band 0x0008) { $mods += 0x5B }
    foreach ($m in $mods) { [Tb]::keybd_event([byte]$m, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30 }
    [Tb]::keybd_event([byte]$Vk, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 40
    [Tb]::keybd_event([byte]$Vk, 0, $KEYUP, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30
    $rev = $mods.Clone(); [array]::Reverse($rev)
    foreach ($m in $rev) { [Tb]::keybd_event([byte]$m, 0, $KEYUP, [UIntPtr]::Zero); Start-Sleep -Milliseconds 30 }
}

function Send-Click([int]$X, [int]$Y) {
    [void][Tb]::SetCursorPos($X, $Y)
    Start-Sleep -Milliseconds 150
    [Tb]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)   # LEFTDOWN
    Start-Sleep -Milliseconds 60
    [Tb]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)   # LEFTUP
    Start-Sleep -Milliseconds 250
}

function Set-ConsoleDefaults([int]$cols, [int]$rows, [int]$fh, [string]$face) {
    # Buffer == window on purpose: a taller buffer paints a scrollbar into every
    # screenshot. Any per-title subkey would win over these defaults, so clear
    # them first (a fresh image has none, but a relaunch after a hand-resize can).
    $k = 'HKCU:\Console'
    New-Item -Path $k -Force | Out-Null
    Get-ChildItem -Path $k -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    Set-ItemProperty -Path $k -Name 'FaceName' -Value $face
    Set-ItemProperty -Path $k -Name 'FontFamily' -Value 54 -Type DWord
    Set-ItemProperty -Path $k -Name 'FontWeight' -Value 400 -Type DWord
    Set-ItemProperty -Path $k -Name 'FontSize' -Value ([int]($fh -shl 16)) -Type DWord
    Set-ItemProperty -Path $k -Name 'WindowSize' -Value ([int](($rows -shl 16) -bor $cols)) -Type DWord
    Set-ItemProperty -Path $k -Name 'ScreenBufferSize' -Value ([int](($rows -shl 16) -bor $cols)) -Type DWord
    Set-ItemProperty -Path $k -Name 'QuickEdit' -Value 1 -Type DWord
    Log ("console defaults: {0}x{1} {2} {3}px" -f $cols, $rows, $face, $fh)
}

function Start-Tool([string]$dir) {
    # Start the way a user does: the Start-menu shortcut setup.ps1 writes
    # (cmd /c "<bat>"), so the console title is whatever that launch really gives.
    $startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
    New-Item -ItemType Directory -Path $startMenu -Force | Out-Null
    $lnk = Join-Path $startMenu 'Thoughtborne.lnk'
    $sh = New-Object -ComObject WScript.Shell
    $sc = $sh.CreateShortcut($lnk)
    $sc.TargetPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $sc.Arguments = '/c "' + (Join-Path $dir 'Thoughtborne.bat') + '"'
    $sc.WorkingDirectory = $dir
    $sc.IconLocation = (Join-Path $dir 'assets\logo\thoughtborne.ico') + ',0'
    $sc.Description = 'Start Thoughtborne voice-to-text'
    $sc.WindowStyle = 1
    $sc.Save()
    Start-Process -FilePath $lnk
    Log ('launched via shortcut: ' + $lnk)
}

function Wait-Hotkeys([int]$sec) {
    $needle = 'All hotkeys registered successfully'
    $deadline = (Get-Date).AddSeconds($sec)
    while ((Get-Date) -lt $deadline) {
        $t = Read-LogText
        if ($t -and ($t -match [regex]::Escape($needle))) { return $true }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Dump-Windows([string]$tag) {
    $p = Join-Path $OutDir ('windows-' + $tag + '.txt')
    Get-VisibleWindows | Format-Table -AutoSize | Out-String -Width 220 |
        Set-Content -LiteralPath $p -Encoding ascii
    return (Split-Path -Leaf $p)
}

# --- 1) uv ------------------------------------------------------------------
State 'uv'
$uv = $null
$uvCmd = Get-Command uv -ErrorAction SilentlyContinue
if ($uvCmd) { $uv = $uvCmd.Source }
$uvUser = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
if ((-not $uv) -and (Test-Path -LiteralPath $uvUser)) { $uv = $uvUser }
if (-not $uv) {
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-RestMethod -Uri 'https://astral.sh/uv/install.ps1' | Invoke-Expression
    } catch {
        Log ('uv install threw: ' + $_.Exception.Message)
    }
    if (Test-Path -LiteralPath $uvUser) { $uv = $uvUser }
}
if (-not $uv) { State 'FAILED:no-uv'; return }
Log ('uv: ' + $uv)

# --- 2) stage the working-tree source --------------------------------------
State 'unpack'
$zip = Join-Path $OutDir 'src.zip'
if (-not (Test-Path -LiteralPath $zip)) { State 'FAILED:no-src-zip'; return }
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
try {
    Expand-Archive -LiteralPath $zip -DestinationPath $InstallDir -Force
} catch {
    Log ('expand threw: ' + $_.Exception.Message); State 'FAILED:expand'; return
}
if (-not (Test-Path -LiteralPath (Join-Path $InstallDir 'thoughtborne.py'))) { State 'FAILED:no-source'; return }
$keyFile = Join-Path $Share 'temp.env'
if (-not (Test-Path -LiteralPath $keyFile)) { State 'FAILED:no-temp-env'; return }
Copy-Item -LiteralPath $keyFile -Destination (Join-Path $InstallDir '.env') -Force

# --- 3) venv ----------------------------------------------------------------
State 'uv-sync'
Push-Location $InstallDir
try {
    & $uv sync 2>&1 | ForEach-Object { Log ('uv-sync: ' + $_) }
} catch {
    Log ('uv sync threw: ' + $_.Exception.Message)
}
Pop-Location

# --- 4) launch --------------------------------------------------------------
State 'launch'
Set-ConsoleDefaults $Cols $Rows $FontHeight $FontFace
Start-Tool $InstallDir
if (-not (Wait-Hotkeys 180)) { Log 'no hotkey registration observed'; State 'FAILED:no-hotkeys'; }
else { Log 'hotkeys registered' }

# The registration lines name every action a command can fire.
$reg = (Read-LogText) -split "`r?`n" | Where-Object { $_ -match 'Registered: ' }
Set-Content -LiteralPath (Join-Path $OutDir 'HOTKEYS.txt') -Value $reg -Encoding ascii

Start-Sleep -Seconds 2
Save-Shot 'startup' | Out-Null
Dump-Windows 'startup' | Out-Null
State 'ready'

# --- 5) command loop --------------------------------------------------------
# One command per file (cmd\NNN.txt, first line = command); the answer lands
# beside it as NNN.done. Keeps the sandbox alive between attempts, so a retake
# costs seconds instead of a fresh boot.
Log 'command loop up'
$idleDeadline = (Get-Date).AddMinutes($IdleMinutes)
while ((Get-Date) -lt $idleDeadline) {
    $pending = Get-ChildItem -LiteralPath $CmdDir -Filter '*.txt' -ErrorAction SilentlyContinue |
        Where-Object { -not (Test-Path -LiteralPath ($_.FullName -replace '\.txt$', '.done')) } |
        Sort-Object Name
    if (-not $pending) { Start-Sleep -Seconds 1; continue }
    foreach ($f in $pending) {
        $idleDeadline = (Get-Date).AddMinutes($IdleMinutes)
        $raw = (Get-Content -LiteralPath $f.FullName -Raw -ErrorAction SilentlyContinue)
        if ($null -eq $raw) { $raw = '' }
        $line = ($raw -split "`r?`n")[0].Trim()
        Log ('cmd ' + $f.Name + ': ' + $line)
        $out = ''
        try {
            $parts = $line -split '\s+'
            switch ($parts[0].ToLower()) {
                'shot'     { $out = Save-Shot $parts[1] }
                'windows'  { $out = (Get-VisibleWindows | Format-Table -AutoSize | Out-String -Width 220) }
                'hotkey'   {
                    $c = Get-HotkeyChord $parts[1]
                    if ($c) { Send-Chord $c.Mod $c.Vk; $out = ('sent {0} mod=0x{1:X} vk=0x{2:X}' -f $parts[1], $c.Mod, $c.Vk) }
                    else { $out = 'no chord for ' + $parts[1] }
                }
                'click'    { Send-Click ([int]$parts[1]) ([int]$parts[2]); $out = 'clicked' }
                'sleep'    { Start-Sleep -Seconds ([int]$parts[1]); $out = 'slept' }
                'relaunch' {
                    # Quit the running tool, resize the console defaults, start again.
                    $q = Get-HotkeyChord 'exit_program'
                    if ($q) { Send-Chord $q.Mod $q.Vk; Start-Sleep -Seconds 6 }
                    Get-Process -Name cmd -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle } |
                        ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
                    Start-Sleep -Seconds 2
                    $c2 = if ($parts.Count -gt 1) { [int]$parts[1] } else { $Cols }
                    $r2 = if ($parts.Count -gt 2) { [int]$parts[2] } else { $Rows }
                    $f2 = if ($parts.Count -gt 3) { [int]$parts[3] } else { $FontHeight }
                    Set-ConsoleDefaults $c2 $r2 $f2 $FontFace
                    Start-Tool $InstallDir
                    if (Wait-Hotkeys 120) { $out = 'relaunched ' + $c2 + 'x' + $r2 + ' font ' + $f2 }
                    else { $out = 'relaunched but no hotkey line' }
                }
                'log'      { $out = (Get-Content -LiteralPath $LogFile -Tail ([int]$parts[1]) -ErrorAction SilentlyContinue) -join "`n" }
                'ps'       { $out = (Invoke-Expression ($line.Substring(3)) | Out-String -Width 200) }
                'stop'     { $out = 'stopping'; Set-Content -LiteralPath ($f.FullName -replace '\.txt$', '.done') -Value $out -Encoding ascii; State 'stopped'; return }
                default    { $out = 'unknown command' }
            }
        } catch {
            $out = 'ERROR: ' + $_.Exception.Message
        }
        Set-Content -LiteralPath ($f.FullName -replace '\.txt$', '.done') -Value $out -Encoding ascii -ErrorAction SilentlyContinue
    }
}
State 'idle-timeout'
