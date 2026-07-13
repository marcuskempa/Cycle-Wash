@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "PROJECT_ROOT=%SCRIPT_DIR%"
set "VENV_DIR=%PROJECT_ROOT%work\.fea-venv"
set "REQUIREMENTS=%SCRIPT_DIR%requirements_fea.txt"
set "BOOTSTRAP_PYTHON=%~1"

if not defined BOOTSTRAP_PYTHON set "BOOTSTRAP_PYTHON=%CYCLEWASH_FEA_BOOTSTRAP_PYTHON%"
if not defined BOOTSTRAP_PYTHON set "BOOTSTRAP_PYTHON=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"

if not exist "%BOOTSTRAP_PYTHON%" goto :missing_python

set "PYTHON_VERSION="
set "VERSION_FILE=%TEMP%\cyclewash-fea-version-%RANDOM%-%RANDOM%.txt"
"%BOOTSTRAP_PYTHON%" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" > "%VERSION_FILE%"
if errorlevel 1 goto :version_probe_failed
set /p "PYTHON_VERSION="<"%VERSION_FILE%"
del /q "%VERSION_FILE%" >nul 2>&1
if not "%PYTHON_VERSION%"=="3.12" goto :wrong_python_version

echo [create-venv] Creating isolated FEA environment with Python %PYTHON_VERSION%.
"%BOOTSTRAP_PYTHON%" -m venv "%VENV_DIR%"
if errorlevel 1 goto :create_venv_failed

echo [install-dependencies] Installing pinned FEA dependencies.
"%VENV_DIR%\Scripts\python.exe" -m pip install -r "%REQUIREMENTS%"
if errorlevel 1 goto :install_failed

echo [verify-dependencies] Importing Gmsh, SfePy, and meshio.
"%VENV_DIR%\Scripts\python.exe" -c "import gmsh, sfepy, meshio; print(gmsh.__version__, sfepy.__version__, meshio.__version__)"
if errorlevel 1 goto :verification_failed

echo [complete] CycleWash FEA environment is ready at "%VENV_DIR%".
exit /b 0

:missing_python
echo [bootstrap-interpreter] Python executable not found: "%BOOTSTRAP_PYTHON%"
echo [bootstrap-interpreter] Pass a Python 3.12 executable as %%1 or set CYCLEWASH_FEA_BOOTSTRAP_PYTHON.
exit /b 1

:wrong_python_version
echo [bootstrap-interpreter] Expected Python 3.12 but received "%PYTHON_VERSION%" from "%BOOTSTRAP_PYTHON%".
exit /b 1

:version_probe_failed
del /q "%VERSION_FILE%" >nul 2>&1
echo [bootstrap/version-probe] Failed to execute the Python 3.12 version probe with "%BOOTSTRAP_PYTHON%".
exit /b 1

:create_venv_failed
echo [create-venv] Failed to create "%VENV_DIR%".
exit /b 1

:install_failed
echo [install-dependencies] Failed to install "%REQUIREMENTS%".
exit /b 1

:verification_failed
echo [verify-dependencies] Gmsh, SfePy, or meshio import verification failed.
exit /b 1
