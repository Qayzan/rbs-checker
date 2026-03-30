@echo off
echo Installing PyInstaller...
py -m pip install pyinstaller

echo.
echo Building rbs-checker.exe...
py -m PyInstaller rbs-checker.spec --clean

echo.
echo Done! Distributable is in: dist\rbs-checker\
echo Zip the entire "dist\rbs-checker" folder and share it.
pause
