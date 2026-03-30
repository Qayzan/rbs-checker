@echo off
cd /d "%~dp0"
echo Installing dependencies...
pip install -r ..\requirements.txt -q
echo.
echo Starting SIT RBS Telegram Bot...
python bot.py
pause
