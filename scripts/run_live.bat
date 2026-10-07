@echo off
REM Arranca el bot en modo REAL y lo reinicia solo si se detiene.
REM Requisito: en .env la linea  XRPBOT_LIVE_CONFIRM=OPERAR EN REAL PF_XRPUSD
REM Para parar: Ctrl+C (y responde S) o cierra la ventana.
REM Para cerrar posiciones: en otra terminal  python -m xrpbot.cli kill --mode live

cd /d "%~dp0.."
call .venv\Scripts\activate.bat

:loop
echo [%date% %time%] Arrancando el bot en modo REAL...
python -m xrpbot.cli run --mode live --live
echo [%date% %time%] El bot se ha detenido (codigo %errorlevel%). Reinicio en 60 s. Ctrl+C para salir.
timeout /t 60 /nobreak >nul
goto loop
