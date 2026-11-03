@echo off
setlocal
if not defined CANVAS_STUDY_HOME set "CANVAS_STUDY_HOME=%~dp0.local"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -m canvas_study %*
) else (
  uv run --project "%~dp0" canvas %*
)
exit /b %errorlevel%

