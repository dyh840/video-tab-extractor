@echo off
setlocal
if exist "D:\anaconda3\python.exe" (
    "D:\anaconda3\python.exe" "%~dp0app.py"
) else (
    python "%~dp0app.py"
)
if errorlevel 1 pause
