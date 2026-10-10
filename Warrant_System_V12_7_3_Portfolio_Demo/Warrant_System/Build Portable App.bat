@echo off
setlocal
cd /d "%~dp0"
rem ======================================================================
rem  Builds a stand-alone "Warrant System" program that runs on ANY Windows
rem  10/11 (64-bit) computer WITHOUT installing Python.
rem  Run this ONCE on a computer that has Python (needs internet). It takes a few minutes.
rem  Result:  Warrant System Portable.zip   (copy it to the other computer, extract, double-click Warrant System.exe)
rem ======================================================================

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD (
    echo Python was not found on this computer. Install Python 3 from python.org first ^(tick "Add python.exe to PATH"^).
    pause
    exit /b 1
)

echo [1/3] Installing the build tools...
%PYCMD% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :failed

echo [2/3] Building the program (a few minutes)...
%PYCMD% -m PyInstaller --noconfirm --clean --windowed --name "Warrant System" --icon "assets\wms_icon.ico" ^
  --add-data "fonts;fonts" --add-data "assets;assets" --add-data "warrant_data.db;." --add-data "payees_seed.txt;." --add-data "CHANGELOG.md;." ^
  --hidden-import win32print --hidden-import win32ui --hidden-import win32gui --hidden-import win32con ^
  --hidden-import PIL.ImageWin --hidden-import PIL.ImageTk --hidden-import fitz ^
  --hidden-import win32com.propsys.propsys --hidden-import win32com.propsys.pscon ^
  --collect-data reportlab --collect-all pymupdf ^
  warrant_app_v12.py
if errorlevel 1 goto :failed
if not exist "dist\Warrant System\Warrant System.exe" goto :failed

echo [3/3] Making the zip to carry to other computers...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Compress-Archive -Path 'dist\Warrant System' -DestinationPath 'Warrant System Portable.zip' -Force"

echo.
echo ================================================================
echo  Done.
echo    Program folder : dist\Warrant System\   (double-click "Warrant System.exe")
echo    To move it     : copy "Warrant System Portable.zip" to the other computer and extract it.
echo ================================================================
echo.
pause
exit /b 0

:failed
echo.
echo The build did not finish. Check the internet connection and the messages above, then run this file again.
echo.
pause
exit /b 1
