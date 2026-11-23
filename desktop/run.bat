@echo off
REM AICADBridge desktop bridge. Usage: run.bat [solidworks|inventor|solidedge|catia|autocad|other]
REM Installs pywin32 once (optional: without it models are saved to ~/AICADBridge/inbox).
set HOST=%1
if "%HOST%"=="" set HOST=solidworks
py -3 -m pip install --user --quiet pywin32 || python -m pip install --user --quiet pywin32
py -3 "%~dp0aicadbridge_desktop.py" --host %HOST% || python "%~dp0aicadbridge_desktop.py" --host %HOST%
pause
