@echo off
REM Holt den neuesten Stand von GitHub und startet das Dashboard
cd /d "%~dp0"
echo Hole Updates ...
git pull
python -m pip install -q -r dashboard\requirements.txt
python -m dashboard
pause
