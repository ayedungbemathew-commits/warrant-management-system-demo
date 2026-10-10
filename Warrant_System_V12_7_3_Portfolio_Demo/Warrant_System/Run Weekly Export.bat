@echo off
setlocal
cd /d "%~dp0"
rem Runs the automatic weekly PDF export once, WITHOUT a window, and exits. Use it by hand or from Windows Task Scheduler.
rem (Settings > Weekly PDF export > "Create / update schedule" sets Task Scheduler up for you.)
set "APP=warrant_app_v12.py"

set "PYCMD="
where pyw >nul 2>nul && set "PYCMD=pyw -3"
if not defined PYCMD where pythonw >nul 2>nul && set "PYCMD=pythonw"
if not defined PYCMD where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD goto :nopython

start "" /wait %PYCMD% "%APP%" --weekly-export
endlocal
exit /b %errorlevel%

:nopython
echo Python was not found on this computer.
exit /b 1
