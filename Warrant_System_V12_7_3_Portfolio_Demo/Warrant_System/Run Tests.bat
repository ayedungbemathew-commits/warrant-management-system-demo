@echo off
cd /d "%~dp0"
set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD where python >nul 2>nul && set "PYCMD=python"
if not defined PYCMD ( echo Python was not found. & pause & exit /b 1 )
%PYCMD% -m unittest discover -s tests -t . -v
echo.
if errorlevel 1 ( echo *** SOME TESTS FAILED ***  ) else ( echo All tests passed. )
pause
