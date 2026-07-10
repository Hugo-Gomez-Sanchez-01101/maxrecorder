@echo off
rem Lanzador del Grabador: usa SIEMPRE el Python del venv (con todas las
rem dependencias: pystray, pyaudio, faster-whisper, torch, pyannote...).
rem pythonw.exe = sin ventana de consola. Acepta argumentos (p.ej. --tray).
cd /d "%~dp0"
start "" "%~dp0venv\Scripts\pythonw.exe" "%~dp0grabador.py" %*
