@echo off
setlocal
cd /d "%~dp0"
set "APP=warrant_app_v12.py"

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD goto :nopython

rem --- first run / after an update: install any missing packages (internet needed only then)
%PYCMD% -c "import tkinter, reportlab, fitz, PIL, win32print" >nul 2>nul
if not errorlevel 1 goto :launch
echo Installing required packages, please wait...
%PYCMD% -m pip install -r requirements.txt
%PYCMD% -c "import tkinter, reportlab, fitz, PIL, win32print" >nul 2>nul
if errorlevel 1 goto :failed

:launch
rem --- start the program WITHOUT a black console window
if "%PYCMD%"=="py -3" where pyw >nul 2>nul && start "" pyw -3 "%APP%" %* && goto :done
where pythonw >nul 2>nul && start "" pythonw "%APP%" %* && goto :done
%PYCMD% "%APP%" %*
goto :done

:nopython
echo.
echo Python was not found on this computer.
echo Install Python 3 from python.org (tick "Add python.exe to PATH"), then run this file again.
echo.
pause
exit /b 1

:failed
echo.
echo ================================================================
echo   The required packages could not be installed.
echo ================================================================
echo Check the internet connection, then try:
echo     %PYCMD% -m pip install -r requirements.txt
echo.
pause
exit /b 1

:done
endlocal
exit /b 0
