@echo off
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
> "%STARTUP%\SIT RBS Bot.bat" echo @start "SIT RBS Bot" /min "%~dp0start-bot.bat"
echo Done! The bot will now start by itself (minimised) every time you log in to Windows.
echo To undo this, double-click autostart-off.bat.
pause
