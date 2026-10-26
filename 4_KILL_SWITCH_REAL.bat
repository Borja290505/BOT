@echo off
REM Doble clic: PARADA DE EMERGENCIA del modo real. Cancela ordenes, cierra la posicion
REM a mercado y deja el bot detenido hasta rearmarlo (python -m xrpbot.cli rearm --mode live).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
echo KILL SWITCH - modo REAL: cancela todas las ordenes y CIERRA la posicion a mercado.
set /p OK=Escribe KILL y pulsa Enter para confirmar: 
if /i not "%OK%"=="KILL" (
  echo Cancelado. No se ha hecho nada.
  pause
  exit /b 0
)
python -m xrpbot.cli kill --mode live
pause
