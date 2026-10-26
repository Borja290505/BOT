@echo off
REM Doble clic: arranca el bot con DINERO REAL. Pide la frase de confirmacion.
REM Requiere KRAKEN_LIVE_API_KEY y KRAKEN_LIVE_API_SECRET en .env
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo Primero ejecuta 1_INSTALAR.bat
  pause
  exit /b 1
)
call .venv\Scripts\activate.bat
echo ======================================================================
echo  MODO REAL - DINERO REAL
echo  La estrategia actual NO ha superado las pruebas (ver docs\research).
echo  Usa solo dinero que puedas permitirte perder.
echo  Deja esta ventana ABIERTA mientras quieras que opere. Ctrl+C para parar.
echo  Parada de emergencia: 4_KILL_SWITCH_REAL.bat
echo ======================================================================
echo.
echo Comprobando conexion y claves con Kraken...
python -m xrpbot.cli check --mode live || goto :error
echo.
python -m xrpbot.cli run --mode live --live
echo.
echo El bot se ha detenido. Las ordenes de stop y take profit siguen en Kraken.
pause
exit /b 0
:error
echo.
echo La comprobacion fallo: revisa las claves en .env. El bot NO se ha arrancado.
pause
exit /b 1
