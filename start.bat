@echo off
rem Lance la console Voicemeeter. Premier lancement : cree le venv et installe vban-cmd.
cd /d "%~dp0"

if exist venv\Scripts\python.exe goto run
echo Premier lancement : installation de l'environnement Python...
py -3 -m venv venv 2>nul || python -m venv venv || goto nopython
venv\Scripts\python -m pip install -r requirements.txt || goto fail

:run
echo Console sur http://localhost:8090  (Ctrl+C pour arreter)
venv\Scripts\python vol_server.py
pause
exit /b

:nopython
echo.
echo Python 3.10+ introuvable.
echo Installe-le depuis https://www.python.org/downloads/ en cochant "Add python.exe to PATH".
pause
exit /b 1

:fail
echo.
echo Echec de l'installation de vban-cmd. Verifie la connexion Internet puis relance start.bat.
rmdir /s /q venv
pause
exit /b 1
