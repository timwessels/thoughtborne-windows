@echo off
setlocal
rem ----------------------------------------------------------------------
rem Thoughtborne launcher.
rem Runs the tool via uv (https://docs.astral.sh/uv/): uv creates/updates
rem the local .venv from pyproject.toml + uv.lock on every start and
rem downloads a suitable Python if none is installed. After a `git pull`
rem with changed dependencies, the next start picks them up automatically.
rem Messages are ASCII-only on purpose: the default cmd codepage
rem (CP850/CP437) garbles non-ASCII characters.
rem ----------------------------------------------------------------------

pushd "%~dp0"
set "UV_CMD=uv"

where uv >nul 2>nul
if %errorlevel% equ 0 goto run

rem uv not on PATH? Try the Astral per-user install location
rem (%USERPROFILE%\.local\bin\uv.exe), where setup.ps1's uv bootstrap lands, so a
rem setup-installed uv is found without offering a second, competing uv install.
if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV_CMD=%USERPROFILE%\.local\bin\uv.exe"
if not "%UV_CMD%"=="uv" goto run

echo Thoughtborne is started with uv, a Python project manager.
echo uv was not found on this system.
echo.
choice /C YN /N /M "Install uv now using winget? [Y/N] "
if errorlevel 2 goto declined

where winget >nul 2>nul
if %errorlevel% neq 0 goto no_winget

echo.
echo Installing uv ...
winget install --id=astral-sh.uv -e
if %errorlevel% neq 0 goto install_failed

rem The running cmd session does not see the PATH update made by winget,
rem so look for the uv shim directly in the standard winget Links folders
rem (user scope first, then machine scope).
if exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe" set "UV_CMD=%LOCALAPPDATA%\Microsoft\WinGet\Links\uv.exe"
if "%UV_CMD%"=="uv" if exist "%ProgramFiles%\WinGet\Links\uv.exe" set "UV_CMD=%ProgramFiles%\WinGet\Links\uv.exe"
if "%UV_CMD%"=="uv" goto restart_needed

:run
echo Starting Thoughtborne ...
echo (on the first start, uv downloads Python and the dependencies once)
"%UV_CMD%" run thoughtborne.py
set "RC=%errorlevel%"
if "%RC%"=="0" goto done_clean
echo.
echo Thoughtborne exited with an error (code %RC%).
echo If this was the first start, check your internet connection:
echo uv needs to download Python and the dependencies once.
echo See README.md for help and for the manual pip setup.
goto done

:declined
echo.
echo Setup cancelled. To run Thoughtborne without uv, follow the classic
echo pip setup in README.md (needs Python 3.10 - 3.13).
goto done

:no_winget
echo.
echo winget is not available on this system.
echo Install uv manually: https://docs.astral.sh/uv/getting-started/installation/
echo Then run this file again. README.md also describes a pip setup.
goto done

:install_failed
echo.
echo The uv installation did not complete.
echo Install uv manually: https://docs.astral.sh/uv/getting-started/installation/
echo Then run this file again. README.md also describes a pip setup.
goto done

:restart_needed
echo.
echo uv was installed, but this window cannot use it yet.
echo Please close this window and double-click Thoughtborne.bat again.
goto done

:done
echo.
echo Press any key to close this window ...
pause >nul

rem A clean Thoughtborne exit (Ctrl+Alt+4) jumps straight to :done_clean and just
rem closes the window. The error/setup paths above fall through to :done first so
rem their message stays readable before the window closes.
:done_clean
popd
endlocal
