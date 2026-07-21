@echo off
setlocal
rem ----------------------------------------------------------------------
rem Thoughtborne settings / onboarding app launcher (#144).
rem The app is pure standard-library Python (tkinter), so it runs on any
rem real Python 3 -- no uv venv strictly required. Prefer pythonw so no
rem stray console sits behind the window; "start" detaches the launch, so
rem this window closes right away.
rem
rem Windows ships App-Execution-Alias STUBS for python.exe / python3.exe in
rem %LOCALAPPDATA%\Microsoft\WindowsApps on a machine with no real Python
rem (the README's uv-primary route). "where python" then succeeds on the
rem stub, but the stub only opens the Microsoft Store and exits without
rem running the app (README Troubleshooting: "python opens the Microsoft
rem Store"). So we filter WindowsApps out of the "where" results and never
rem run a stub; uv -- how Thoughtborne.bat runs, guaranteed present after
rem any successful tool start -- is the guaranteed last-resort fallback.
rem Messages are ASCII-only on purpose: the default cmd codepage
rem (CP850/CP437) garbles non-ASCII characters.
rem ----------------------------------------------------------------------

pushd "%~dp0"

rem 1) A real pythonw (never a WindowsApps stub): detached, no stray console.
set "PYW="
for /f "delims=" %%I in ('where pythonw 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PYW set "PYW=%%I"
if defined PYW (
    start "" "%PYW%" "thoughtborne_settings.py"
    goto done
)

rem 2) A real python (never a stub): run it directly (a brief console shows).
set "PY="
for /f "delims=" %%I in ('where python 2^>nul ^| findstr /v /i "WindowsApps"') do if not defined PY set "PY=%%I"
if defined PY (
    "%PY%" "thoughtborne_settings.py"
    goto done
)

rem 3) No real system Python (the uv-primary setup): use the uv-managed
rem interpreter, the same one Thoughtborne.bat uses. "uv run" finds the
rem project's pythonw, so there is still no stray console. Look for uv on
rem PATH first, then at the Astral per-user location
rem (%USERPROFILE%\.local\bin\uv.exe) where setup.ps1's bootstrap lands: the
rem primary one-liner lane installs uv there and may leave the machine with
rem no system Python, so without this fallback this shortcut would miss it.
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
