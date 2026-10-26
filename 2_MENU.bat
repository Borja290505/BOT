@echo off
REM Doble clic: menu del bot (panel, SIM, datos, backtest, investigacion, kill switch SIM...).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta 1_INSTALAR.bat
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
python -m xrpbot.launcher
pause
