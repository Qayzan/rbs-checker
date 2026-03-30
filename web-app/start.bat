@echo off
cd /d "%~dp0"
echo Installing/updating dependencies...
pip install -r ..\requirements.txt -q
echo Installing browser...
python -m playwright install chromium
echo.
echo Starting SIT RBS Checker...
python app.py
pause
