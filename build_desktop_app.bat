@echo off
setlocal
cd /d "%~dp0"

echo =========================================================
echo    AI Video Studio Pro - Desktop App Builder (PyInstaller)
echo =========================================================
echo.

where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not found in PATH!
    pause
    exit /b 1
)

echo [1/3] Checking PyInstaller...
python -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller pywebview
)

echo [2/3] Cleaning previous build artifacts...
if exist "build" rd /s /q "build"
if exist "dist\VideoStudio.exe" del /f /q "dist\VideoStudio.exe"
if exist "VideoStudio.spec" del /f /q "VideoStudio.spec"

echo [3/3] Building standalone Windows executable (VideoStudio.exe)...
pyinstaller --noconfirm --onedir --windowed --name "VideoStudio" ^
    --add-data "index.html;." ^
    --add-data "metadata.json;." ^
    --hidden-import "flask" ^
    --hidden-import "jinja2" ^
    --hidden-import "webview" ^
    --hidden-import "clr_loader" ^
    desktop_app.py

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller compilation failed!
    pause
    exit /b 1
)

echo.
echo =========================================================
echo   SUCCESS! Standalone Desktop Studio built at:
echo   %~dp0dist\VideoStudio\VideoStudio.exe
echo =========================================================
echo.
pause
