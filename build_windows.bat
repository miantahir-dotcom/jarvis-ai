@echo off
setlocal EnableExtensions
title JARVIS Windows Builder
cd /d "%~dp0"

echo ==========================================
echo       JARVIS AI - WINDOWS BUILDER
echo ==========================================
echo.

if not exist "main.py" (
  echo ERROR: main.py was not found in this folder.
  echo Copy this file into your JARVIS-AI project root.
  pause
  exit /b 1
)

set "ENTRY="
if exist "desktop_app.py" set "ENTRY=desktop_app.py"
if not defined ENTRY if exist "desktop_app\desktop_app.py" set "ENTRY=desktop_app\desktop_app.py"
if not defined ENTRY (
  echo ERROR: Could not find desktop_app.py or desktop_app\desktop_app.py
  pause
  exit /b 1
)

if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
echo Desktop entry point: %ENTRY%
%PY% --version
if errorlevel 1 goto :failed

echo.
echo Installing packaging tools and project dependencies...
%PY% -m pip install --upgrade pip pyinstaller
if errorlevel 1 goto :failed
%PY% -m pip install psutil SpeechRecognition PySide6 pyautogui pywhatkit requests pywin32 edge-tts pygame
if errorlevel 1 goto :failed

echo.
echo Cleaning old build output...
if exist "build\JARVIS" rmdir /s /q "build\JARVIS"
if exist "dist\JARVIS" rmdir /s /q "dist\JARVIS"

echo.
echo Building JARVIS as a folder-based Windows application...
if exist "assets" (
  %PY% -m PyInstaller --noconfirm --clean --windowed --onedir --name JARVIS --collect-all PySide6 --hidden-import=pythoncom --hidden-import=pywintypes --hidden-import=win32timezone --hidden-import=win32com.client --hidden-import=pygame --hidden-import=edge_tts --add-data "assets;assets" "%ENTRY%"
) else (
  %PY% -m PyInstaller --noconfirm --clean --windowed --onedir --name JARVIS --collect-all PySide6 --hidden-import=pythoncom --hidden-import=pywintypes --hidden-import=win32timezone --hidden-import=win32com.client --hidden-import=pygame --hidden-import=edge_tts "%ENTRY%"
)
if errorlevel 1 goto :failed

echo.
echo BUILD COMPLETE
echo Output folder: %CD%\dist\JARVIS
echo Run: dist\JARVIS\JARVIS.exe
echo.
echo IMPORTANT: Test this folder first. Ollama and the llama3.2:1b model are external requirements for local AI chat.
echo Online voice recognition and Edge TTS need internet. This build is unsigned.
echo.
pause
exit /b 0

:failed
echo.
echo BUILD FAILED. Read the error above, fix the missing dependency, and run this file again.
pause
exit /b 1
