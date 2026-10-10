@echo off
setlocal
cd /d "%~dp0"
rem Puts a "WMS Portfolio Demo" shortcut (with the app logo) on the Desktop and in the Start menu.
rem Run this once, from the app folder. The app folder itself must stay where it is.

set "ICON=%~dp0assets\wms_icon.ico"
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD goto :make
rem use the logo you put in assets\logo.png, if there is one (converted to an icon)
for /f "usebackq delims=" %%I in (`%PYCMD% -c "import os, wms_branding as B; d=os.path.join(os.environ.get('PROGRAMDATA','.'),'WarrantSystemPortfolioDemo'); os.makedirs(d,exist_ok=True); print(B.ensure_ico(d) or '')" 2^>nul`) do if not "%%I"=="" set "ICON=%%I"

:make
powershell -NoProfile -ExecutionPolicy Bypass -Command "$w=New-Object -ComObject WScript.Shell; foreach($dir in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){ $s=$w.CreateShortcut((Join-Path $dir 'WMS Portfolio Demo.lnk')); $s.TargetPath='%~dp0Run Warrant System V12.bat'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%ICON%,0'; $s.WindowStyle=7; $s.Description='Warrant Management System Portfolio Demo'; $s.Save() }"
if errorlevel 1 goto :failed
echo.
echo Done. "WMS Portfolio Demo" is now on your Desktop and in the Start menu.
echo.
pause
exit /b 0

:failed
echo.
echo The shortcut could not be created. Right-click "Run Warrant System V12.bat" and choose Send to - Desktop (create shortcut).
echo.
pause
exit /b 1
