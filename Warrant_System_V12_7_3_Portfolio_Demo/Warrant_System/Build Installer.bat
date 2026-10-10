@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
rem ======================================================================
rem  Builds  dist_installer\WarrantSystem-Setup-<version>.exe  -- a normal Windows installer
rem  (Start-menu + desktop shortcut, Add/Remove Programs entry, in-place upgrades, data kept on uninstall).
rem  Run on a PC that has Python and Inno Setup 6 (free: https://jrsoftware.org/isdl.php).
rem  The AUTOMATED TESTS RUN FIRST; if any fails, no installer is built.
rem ======================================================================

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
    echo Python was not found. Install Python 3 from python.org first ^(tick "Add python.exe to PATH"^).
    pause & exit /b 1
)

for /f "usebackq delims=" %%V in (`%PYCMD% -c "import wms_version; print(wms_version.VERSION)"`) do set "VER=%%V"
if not defined VER ( echo Could not read the version from wms_version.py & pause & exit /b 1 )
echo Building Warrant System version %VER%

echo [1/4] Installing the build tools...
%PYCMD% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :failed

echo [2/4] Running the automated tests...
%PYCMD% -m unittest discover -s tests -t . 
if errorlevel 1 (
    echo.
    echo *** TESTS FAILED -- no installer was built. Fix the failures above first. ***
    pause & exit /b 1
)

echo [3/4] Building the program (a few minutes)...
%PYCMD% -m PyInstaller --noconfirm --clean --windowed --name "Warrant System" --icon "assets\wms_icon.ico" ^
  --add-data "fonts;fonts" --add-data "assets;assets" --add-data "warrant_data.db;." --add-data "payees_seed.txt;." --add-data "CHANGELOG.md;." ^
  --hidden-import win32print --hidden-import win32ui --hidden-import win32gui --hidden-import win32con ^
  --hidden-import PIL.ImageWin --hidden-import PIL.ImageTk --hidden-import fitz ^
  --hidden-import win32com.propsys.propsys --hidden-import win32com.propsys.pscon ^
  --collect-data reportlab --collect-all pymupdf ^
  warrant_app_v12.py
if errorlevel 1 goto :failed
if not exist "dist\Warrant System\Warrant System.exe" goto :failed

echo [4/4] Making the installer...
set "ISCC="
for %%P in ("%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" "%ProgramFiles%\Inno Setup 6\ISCC.exe" "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe") do if exist %%P set "ISCC=%%~P"
if not defined ISCC (
    echo.
    echo Inno Setup 6 was not found. Install it from https://jrsoftware.org/isdl.php and run this file again.
    echo ^(The program itself is already built in dist\Warrant System\ ^)
    pause & exit /b 1
)
"%ISCC%" /DMyAppVersion=%VER% "installer\WarrantSystem.iss"
if errorlevel 1 goto :failed

echo.
echo ================================================================
echo  Done:  dist_installer\WarrantSystem-Setup-%VER%.exe
echo  To install or UPGRADE on any PC: run that file. Saved warrants and settings are kept.
echo ================================================================
pause
exit /b 0

:failed
echo.
echo The build did not finish. Check the internet connection and the messages above, then run this file again.
pause
exit /b 1
