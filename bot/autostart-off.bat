@echo off
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
if exist "%STARTUP%\SIT RBS Bot.bat" (
  del "%STARTUP%\SIT RBS Bot.bat"
  echo Done. The bot will no longer start with Windows.
) else (
  echo The bot was not set to start with Windows - nothing to undo.
)
pause
