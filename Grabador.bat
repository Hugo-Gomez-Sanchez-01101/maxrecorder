@echo off
rem Launcher: ALWAYS uses the venv's Python (with all dependencies:
rem pystray, pyaudio, faster-whisper, etc.).
rem pythonw.exe = no console window. Accepts arguments (e.g. --tray).
cd /d "%~dp0"
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0grabador.py" %*
