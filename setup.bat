@echo off
setlocal
rem ----------------------------------------------------------------------
rem Thoughtborne setup wrapper.
rem Runs the co-located setup.ps1 with a process-scoped execution-policy
rem bypass. Two roles: (1) first start from the release ZIP (double-click
rem after "Extract all"); (2) paste-free in-place update when run from
rem inside the install folder (setup.ps1 is version-agnostic and re-fetches
rem the newest release snapshot). %~dp0 co-locates the script on both lanes;
rem %* forwards any args (e.g. -DryRun).
rem
rem THOUGHTBORNE_FROM_BAT tells setup.ps1 it is on the -File lane (never the piped
rem irm|iex lane) and may end with a real  exit <code>  -- a -File run otherwise
rem reports errorlevel 0 even when the script failed. On failure we pause so the
rem double-click / ZIP user can read the error before this window closes (house
rem style: Thoughtborne.bat's :done + pause). endlocal & exit /b carries the code
rem out to any caller (the in-place update lane, the sandbox harness).
rem Messages are ASCII-only on purpose: the default cmd codepage
rem (CP850/CP437) garbles non-ASCII characters.
rem ----------------------------------------------------------------------

set "THOUGHTBORNE_FROM_BAT=1"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "TB_SETUP_EXIT=%ERRORLEVEL%"

if not "%TB_SETUP_EXIT%"=="0" (
    echo.
    echo Setup failed with exit code %TB_SETUP_EXIT% -- see the messages above.
    echo Press any key to close this window ...
    pause >nul
)

endlocal & exit /b %TB_SETUP_EXIT%
