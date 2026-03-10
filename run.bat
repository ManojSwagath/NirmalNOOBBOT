@echo off
:: ============================================================
::  MoodBot — One-click launcher for Windows (uses uv)
::  Installs uv + dependencies (first run) and starts the app.
:: ============================================================

echo ============================================
echo   MoodBot — AI Emotion Companion
echo ============================================

:: Move to the script's own directory
cd /d "%~dp0"

:: Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from python.org
    pause
    exit /b 1
)

:: Install uv if not available
where uv >nul 2>&1
if errorlevel 1 (
    echo [SETUP] Installing uv package manager...
    pip install uv
)

:: Create venv with uv if it doesn't exist
if not exist ".venv\Scripts\activate.bat" (
    echo [SETUP] Creating virtual environment with uv...
    uv venv
)

:: Activate venv
call .venv\Scripts\activate.bat

:: Install / update dependencies
echo [SETUP] Installing dependencies with uv...
uv pip install -r requirements.txt

:: Check .env file exists
if not exist ".env" (
    echo.
    echo [ERROR] .env file not found!
    echo   Create a .env file with:  GROQ_API_KEY=your_key_here
    echo.
    pause
    exit /b 1
)

:: Run the app
echo.
echo [START] Launching MoodBot...
echo   Press Q in the video window to quit.
echo.
python main.py

pause
