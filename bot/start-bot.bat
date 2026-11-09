@echo off
title SIT RBS Bot
cd /d "%~dp0"
if exist "cloud-active" (
  echo The bot is hosted in Google Cloud. Local startup is disabled to avoid duplicate bots.
  exit /b 0
)
set "BOT_PYTHON=python"
if exist "..\.venv\Scripts\python.exe" set "BOT_PYTHON=%~dp0..\.venv\Scripts\python.exe"

rem Only install packages when something is missing (saves time on every start).
"%BOT_PYTHON%" -c "import telegram, playwright, dotenv" 2>nul || (
  echo Installing dependencies...
  "%BOT_PYTHON%" -m pip install -r ..\requirements.txt -q
)
rem Makes sure the hidden browser is downloaded; does nothing if it already is.
"%BOT_PYTHON%" -m playwright install chromium >nul 2>&1

:loop
if exist "cloud-active" exit /b 0
echo Starting SIT RBS Telegram Bot...
"%BOT_PYTHON%" bot.py
echo.
echo The bot stopped. Restarting in 10 seconds - close this window to stop it for good.
timeout /t 10 /nobreak >nul
goto loop
