@echo off
setlocal
rem ----------------------------------------------------------------------
rem Thoughtborne settings / onboarding app launcher (#144; order #171/D-005).
rem Interpreter selection runs in three ordered stages:
rem
rem 1) Project venv, health-probed. This is the SAME interpreter the tool
rem    itself runs on (Thoughtborne.bat -> uv run), so the settings app never
rem    diverges from the tool on Python version. If .venv\Scripts\pythonw.exe
rem    exists we first confirm the venv works -- .venv\Scripts\python.exe -c
rem    "import tkinter", run in this console (~0.1 s, no extra window, output
rem    silenced so a failed probe leaves no traceback) -- then detach the
rem    windowed app via the venv pythonw. A present-but-broken venv (its base
rem    interpreter removed by uv cache clean / uv python uninstall) fails
rem    the probe and falls through, instead of a detached pythonw dying
rem    invisibly with no way to report the error. The probe is the plain
rem    import; the init.tcl-not-found class that only shows at Tk() time is an
rem    accepted gap (a Tk() probe would create/destroy a window and flicker).
rem 2) System Python -- the rescue lane. Any real pythonw/python on PATH runs
rem    the app; it works because the app is pure standard library (no venv, no
rem    uv needed). Windows ships App-Execution-Alias STUBS for python.exe /
rem    python3.exe in %LOCALAPPDATA%\Microsoft\WindowsApps on a machine with no
rem    real Python (the README's uv-primary route). "where python" then
rem    succeeds on the stub, but the stub only opens the Microsoft Store and
rem    exits without running the app (README Troubleshooting: "python opens the
rem    Microsoft Store"). So we filter WindowsApps out of the "where" results
rem    and never run a stub.
rem 3) uv bootstrap. No venv and no system Python (the git-clone cold start):
rem    "uv run" creates the venv on the spot and finds the project's pythonw,
rem    so there is still no stray console. Look for uv on PATH first, then at
rem    the Astral per-user location (%USERPROFILE%\.local\bin\uv.exe) where
rem    setup.ps1's bootstrap lands.
rem
rem Messages are ASCII-only on purpose: the default cmd codepage (CP850/CP437)
rem garbles non-ASCII characters.
rem ----------------------------------------------------------------------

pushd "%~dp0"

rem 1) Project venv, health-probed (see header). Probe with the console
rem    python.exe so a broken venv fails here (silently) instead of a detached
rem    pythonw dying invisibly; only a clean probe launches the app.
if not exist ".venv\Scripts\pythonw.exe" goto sys_python
".venv\Scripts\python.exe" -c "import tkinter" >nul 2>nul
if %errorlevel% equ 0 (
    start "" ".venv\Scripts\pythonw.exe" "thoughtborne_settings.py"
    goto done
)

:sys_python
rem 2) System Python -- the rescue lane (pure-stdlib app, so any real Python 3
rem    runs it). First a real pythonw (never a WindowsApps stub): detached, no
rem    stray console.
set "PYW="
for /f "delims=" %%I in ('where pythonw 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PYW set "PYW=%%I"
if defined PYW (
    start "" "%PYW%" "thoughtborne_settings.py"
    goto done
)

rem    Then a real python (never a stub): run it directly (a brief console shows).
set "PY="
for /f "delims=" %%I in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PY set "PY=%%I"
if defined PY (
    "%PY%" "thoughtborne_settings.py"
    goto done
)

rem 3) uv bootstrap -- no venv and no system Python (the git-clone cold start).
rem    "uv run" creates the venv and finds the project's pythonw, so there is
rem    still no stray console. uv on PATH first, then the Astral per-user
rem    location (%USERPROFILE%\.local\bin\uv.exe) where setup.ps1's bootstrap
rem    lands: the primary one-liner lane installs uv there and may leave the
rem    machine with no system Python, so without this fallback this shortcut
rem    would miss it.
set "UV_CMD="
where uv >nul 2>nul
if %errorlevel% equ 0 set "UV_CMD=uv"
if not defined UV_CMD if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
if defined UV_CMD (
    start "" "%UV_CMD%" run pythonw "thoughtborne_settings.py"
    goto done
)

echo Could not find Python to run the settings app.
echo Install Python 3, or start Thoughtborne once via Thoughtborne.bat (which
echo sets up uv), then run this file again.
echo.
echo Press any key to close this window ...
pause >nul

:done
popd
endlocal
