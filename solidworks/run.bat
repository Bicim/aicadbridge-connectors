@echo off
REM AICADBridge Link for SolidWorks: installs pywin32 once, then waits for models.
py -3 -m pip install --user --quiet pywin32 || python -m pip install --user --quiet pywin32
py -3 "%~dp0aicadbridge_sw_link.py" %* || python "%~dp0aicadbridge_sw_link.py" %*
pause
