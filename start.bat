@echo off
title Docker Accelerator Tool

echo ==================================================
echo  Docker Accelerator Tool Startup Script
echo ==================================================
echo.

REM Change to the script's directory to ensure all files are found
cd /d "%~dp0"
echo [+] Current directory: %cd%
echo.

echo [+] Checking and installing dependencies from requirements.txt...
pip install -r requirements.txt
echo.

echo [+] Starting the application (app.py)...
echo.
python app.py
