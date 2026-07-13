@echo off
setlocal

set "PROJECT_ROOT=%~dp0"
set "VENV_PY=%PROJECT_ROOT%work\.venv\Scripts\python.exe"
set "APP_FILE=%PROJECT_ROOT%Gear_Builder.py"
set "REQ_FILE=%PROJECT_ROOT%requirements.txt"
set "APP_PORT=8501"
set "APP_URL=http://127.0.0.1:8501"

cd /d "%PROJECT_ROOT%"

if not exist "%VENV_PY%" (
    echo Creating local Python virtual environment...
    python -m venv "%PROJECT_ROOT%work\.venv"
    if errorlevel 1 (
        echo Failed to create the virtual environment.
        pause
        exit /b 1
    )
)

echo Installing or checking GUI dependencies...
"%VENV_PY%" -m pip install -r "%REQ_FILE%"
if errorlevel 1 (
    echo Failed to install Streamlit/Plotly dependencies.
    pause
    exit /b 1
)

echo Closing any previous CycleWash server on port %APP_PORT%...
for /f "usebackq delims=" %%P in (`powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %APP_PORT% -State Listen -ErrorAction SilentlyContinue ^| Select-Object -ExpandProperty OwningProcess -Unique"`) do (
    taskkill /PID %%P /T /F >nul 2>&1
)

echo Starting CycleWash at %APP_URL%
start "" "%APP_URL%"
"%VENV_PY%" -m streamlit run "%APP_FILE%" --server.address 127.0.0.1 --server.port %APP_PORT% --server.headless true

endlocal
