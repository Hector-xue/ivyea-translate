@echo off
rem One-click Windows build script for Ivyea Translate.
rem Usage:
rem   build.bat            -> folder build (fast startup)  dist\IvyeaTranslate\IvyeaTranslate.exe
setlocal

python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

python -m PyInstaller --noconfirm ivyea-translate.spec
if errorlevel 1 goto :fail
echo.
echo Done: dist\IvyeaTranslate\IvyeaTranslate.exe  ^(folder build^)
exit /b 0

:fail
echo Build failed. Check the error above.
exit /b 1
